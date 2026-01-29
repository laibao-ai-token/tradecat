"""Alpha 代币列表采集（Binance）"""
from __future__ import annotations

import sys
from pathlib import Path

# -------------------- 路径修正：避免 http.py 影子 --------------------
_THIS_DIR = Path(__file__).resolve().parent
if sys.path and sys.path[0] == str(_THIS_DIR):
    sys.path.pop(0)
for p in _THIS_DIR.parents:
    if (p / 'config.py').exists() and p.name == 'src':
        sys.path.insert(0, str(p))
        break

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import aiohttp

from config import settings
from runtime.errors import safe_main
from runtime.logging_utils import setup_logging

logger = logging.getLogger(__name__)

BINANCE_ALPHA_URL = "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
CACHE_TTL = timedelta(hours=6)


# ==================== 全局限流器（隔离到 datacat 日志目录） ====================

_BASE_DIR = settings.log_dir
_STATE_FILE = _BASE_DIR / ".rate_limit_state"
_LOCK_FILE = _BASE_DIR / ".rate_limit.lock"
_BAN_FILE = _BASE_DIR / ".ban_until"

RATE_PER_MINUTE = min(int(os.getenv("DATACAT_RATE_LIMIT_PER_MINUTE", str(settings.rate_limit_per_minute))), 2400)
MAX_CONCURRENT = min(int(os.getenv("DATACAT_MAX_CONCURRENT", str(settings.max_concurrent))), 20)


class GlobalLimiter:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self.capacity = float(RATE_PER_MINUTE)
        self.rate = RATE_PER_MINUTE / 60.0
        self._sem = threading.Semaphore(MAX_CONCURRENT)
        self._ban_until = 0.0
        self._ban_lock = threading.Lock()
        _BASE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_ban()

    def _load_ban(self) -> None:
        try:
            if _BAN_FILE.exists():
                self._ban_until = float(_BAN_FILE.read_text().strip())
        except Exception:
            pass

    def _save_ban(self) -> None:
        try:
            tmp = _BAN_FILE.with_suffix(".tmp")
            tmp.write_text(str(self._ban_until))
            tmp.rename(_BAN_FILE)
        except Exception:
            pass

    def set_ban(self, until: float) -> None:
        with self._ban_lock:
            if until > self._ban_until:
                self._ban_until = until
                self._save_ban()
                logger.warning("IP ban 至 %s", time.strftime("%H:%M:%S", time.localtime(until)))

    def _wait_ban(self) -> None:
        self._load_ban()
        with self._ban_lock:
            if self._ban_until > time.time():
                wait = self._ban_until - time.time() + 5
                logger.warning("等待 ban 解除 %.0fs", wait)
                time.sleep(wait)

    def _acquire_tokens(self, weight: int) -> None:
        while True:
            with open(_LOCK_FILE, "w") as f:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    tokens, last = self._read_state()
                    now = time.time()
                    tokens = min(self.capacity, tokens + (now - last) * self.rate)
                    if tokens >= weight:
                        tokens -= weight
                        self._write_state(tokens, now)
                        return
                    wait = (weight - tokens) / self.rate
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            time.sleep(max(0.05, wait))

    def _read_state(self):
        try:
            if _STATE_FILE.exists():
                d = json.loads(_STATE_FILE.read_text())
                return d.get("tokens", self.capacity), d.get("last", time.time())
        except Exception:
            pass
        return self.capacity, time.time()

    def _write_state(self, tokens: float, last: float) -> None:
        try:
            tmp = _STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tokens": tokens, "last": last}))
            tmp.rename(_STATE_FILE)
        except Exception:
            pass

    def acquire(self, weight: int = 1) -> None:
        self._wait_ban()
        self._sem.acquire()
        try:
            self._acquire_tokens(weight)
        except Exception:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()

    def parse_ban(self, msg: str) -> float:
        try:
            for line in msg.split("\n"):
                if "banned until" in line:
                    ts = int(line.strip().split("banned until")[1].strip())
                    return ts / 1000
        except Exception:
            pass
        return time.time() + 60


def acquire(weight: int = 1) -> None:
    GlobalLimiter().acquire(weight)


def release() -> None:
    GlobalLimiter().release()


def set_ban(until: float) -> None:
    GlobalLimiter().set_ban(until)


def parse_ban(msg: str) -> float:
    return GlobalLimiter().parse_ban(msg)


# ==================== Alpha 采集 ====================

def _normalize_symbol(symbol: Optional[str]) -> Optional[str]:
    if not symbol:
        return None
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper() or None


@dataclass
class Metrics:
    """采集统计"""
    requests_total: int = 0
    requests_failed: int = 0
    cache_hits: int = 0
    cache_updates: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name, 0) + value)


metrics = Metrics()


class AlphaTokenFetcher:
    """Alpha 代币列表"""

    def __init__(self) -> None:
        self._cache_path = settings.data_dir / "alpha_tokens.json"
        self._proxy = settings.http_proxy

    async def refresh(self, force: bool = False) -> Dict[str, Dict[str, str]]:
        if not force and self._cache_path.exists():
            try:
                cache = json.loads(self._cache_path.read_text())
                fetched_at = datetime.fromisoformat(cache.get("fetched_at", ""))
                if datetime.now(timezone.utc) - fetched_at.replace(tzinfo=timezone.utc) < CACHE_TTL:
                    logger.info("使用缓存: %d 个 Alpha 代币", len(cache.get("tokens", [])))
                    metrics.inc("cache_hits")
                    return self._parse_tokens(cache.get("tokens", []))
            except Exception:
                pass

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            acquire(1)
            try:
                metrics.inc("requests_total")
                async with session.get(BINANCE_ALPHA_URL, proxy=self._proxy) as resp:
                    if resp.status in (418, 429):
                        body = await resp.text()
                        ban_time = parse_ban(body)
                        set_ban(ban_time if ban_time > time.time() else time.time() + 60)
                        return self._load_cache()
                    if resp.status != 200:
                        logger.warning("获取 Alpha 列表失败: %s", resp.status)
                        metrics.inc("requests_failed")
                        return self._load_cache()
                    data = await resp.json()
            except Exception as e:
                logger.warning("请求 Alpha 列表异常: %s", e)
                metrics.inc("requests_failed")
                return self._load_cache()
            finally:
                release()

        tokens = data.get("data", []) if isinstance(data, dict) else []
        if not tokens:
            return self._load_cache()

        cache = {"fetched_at": datetime.now(timezone.utc).isoformat(), "tokens": tokens}
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
        metrics.inc("cache_updates")
        logger.info("Alpha 代币缓存已更新: %d 个", len(tokens))

        return self._parse_tokens(tokens)

    def _load_cache(self) -> Dict[str, Dict[str, str]]:
        if not self._cache_path.exists():
            return {}
        try:
            cache = json.loads(self._cache_path.read_text())
            return self._parse_tokens(cache.get("tokens", []))
        except Exception:
            return {}

    def _parse_tokens(self, tokens: list) -> Dict[str, Dict[str, str]]:
        mapping: Dict[str, Dict[str, str]] = {}
        for item in tokens:
            symbol = item.get("symbol") or item.get("cexCoinName")
            alpha_id = item.get("alphaId") or item.get("alpha_id")
            name = item.get("name")
            if not symbol and alpha_id:
                symbol = alpha_id.replace("ALPHA_", "")
            normalized = _normalize_symbol(symbol)
            if normalized:
                mapping[normalized] = {
                    "note": alpha_id or name or "Binance Alpha",
                    "alpha_id": alpha_id or "",
                    "name": name or "",
                    "source": "binance",
                }
        return mapping

    def is_alpha(self, symbol: str) -> Tuple[bool, Optional[str]]:
        alpha_map = self._load_cache()
        normalized = _normalize_symbol(symbol)
        if normalized and normalized in alpha_map:
            return True, alpha_map[normalized].get("note")
        return False, None


async def refresh_alpha_tokens(force: bool = False) -> Dict[str, Dict[str, str]]:
    return await AlphaTokenFetcher().refresh(force)


def main() -> None:
    setup_logging(level=settings.log_level, fmt=settings.log_format, component="sync.alpha", log_file=settings.log_file)

    async def run() -> None:
        tokens = await refresh_alpha_tokens(force=True)
        print(f"\nAlpha 代币: {len(tokens)} 个")
        for sym in list(tokens.keys())[:10]:
            print(f"  {sym}: {tokens[sym]['name']}")

    asyncio.run(run())


if __name__ == "__main__":
    import sys
    sys.exit(safe_main(main, component="sync.alpha"))
