from __future__ import annotations

import concurrent.futures
import csv
import curses
import hashlib
import html
import json
import locale
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from common.scheduler import wait_seconds

from .db import SignalRow, fetch_recent, parse_ts, probe
from .etf_profiles import (
    get_all_domain_keys,
    get_domain_label,
    get_etf_domain_profile,
    load_dynamic_auto_driving_symbols,
)
from .etf_selector import select_etf_candidates
from .micro import Candle, MicroConfig, MicroEngine, MicroSnapshot
from .news_db import (
    StoredNewsArticle,
    fetch_recent_news_articles,
    resolve_news_database_schema,
    resolve_news_database_url,
)
from .news_defaults import (
    CORE_GROUP,
    PRIMARY_TIER,
    SUPPLEMENTAL_TIER,
    UNKNOWN_GROUP,
    UNKNOWN_TIER,
    default_tui_news_rss_feeds_value,
    extract_source_meta_tags,
    news_source_code,
    news_source_group,
    news_source_tier,
)
from .news_events import NewsEvent, cluster_news_items
from .news_health import (
    NewsHealthSnapshot,
    build_live_news_health,
    load_news_collector_health,
    resolve_news_health_log_path,
)
from .quote import Quote, fetch_daily_curve_1d, fetch_intraday_curve_1m, fetch_quote, fetch_quotes
from .watchlists import (
    Watchlists,
    normalize_cn_fund_symbols,
    normalize_cn_symbols,
    normalize_crypto_symbols,
    normalize_hk_symbols,
    normalize_metals_symbols,
    normalize_us_symbols,
    save_watchlists,
)


@dataclass
class Filters:
    sources: set[str] = field(default_factory=lambda: {"pg", "sqlite"})
    directions: set[str] = field(default_factory=lambda: {"BUY", "SELL", "ALERT"})
    paused: bool = False

    def toggle_source(self, src: str) -> None:
        if src in self.sources:
            self.sources.remove(src)
        else:
            self.sources.add(src)

    def toggle_direction(self, d: str) -> None:
        if d in self.directions:
            self.directions.remove(d)
        else:
            self.directions.add(d)


@dataclass
class QuoteConfig:
    enabled: bool = True
    provider: str = "tencent"
    market: str = "us_stock"
    symbols: list[str] = field(default_factory=lambda: ["NVDA"])
    refresh_s: float = 1.0
    timeout_s: float = 2.0


@dataclass
class QuoteConfigs:
    us: QuoteConfig = field(default_factory=lambda: QuoteConfig(market="us_stock", symbols=["NVDA", "META", "ORCL"]))
    hk: QuoteConfig = field(default_factory=lambda: QuoteConfig(market="hk_stock", symbols=["00700", "01810", "03690"]))
    cn: QuoteConfig = field(default_factory=lambda: QuoteConfig(market="cn_stock", symbols=["SH600519", "SZ000001", "SH688256"]))
    fund_cn: QuoteConfig = field(
        default_factory=lambda: QuoteConfig(market="cn_fund", symbols=["SH510300", "SZ159915", "SH512100"])
    )
    crypto: QuoteConfig = field(
        default_factory=lambda: QuoteConfig(
            provider="auto",
            market="crypto_spot",
            symbols=["BTC_USDT", "ETH_USDT"],
            timeout_s=10.0,  # Gate API can be slower than tencent; keep a larger default.
        )
    )
    metals: QuoteConfig = field(
        default_factory=lambda: QuoteConfig(
            provider="auto",
            market="metals",
            symbols=["XAUUSD", "XAGUSD"],
            timeout_s=6.0,
        )
    )


@dataclass
class QuoteEntryState:
    quote: Quote | None = None
    last_error: str = ""
    # Timestamp of the last successful quote fetch (used for the "age" column).
    last_fetch_at: float = 0.0


@dataclass
class QuoteBookState:
    entries: dict[str, QuoteEntryState] = field(default_factory=dict)


@dataclass
class MasterPaneState:
    selected: int = 0
    left_scroll: int = 0
    right_scroll: int = 0
    focus: str = "left"


@dataclass
class FundDomainRuntimeState:
    keys: list[str] = field(default_factory=get_all_domain_keys)
    selected_idx: int = 0
    selected_key: str = ""

    def __post_init__(self) -> None:
        if not self.selected_key:
            self.selected_key = self.keys[0] if self.keys else "auto_driving_cn"
        if self.keys:
            try:
                self.selected_idx = self.keys.index(self.selected_key)
            except ValueError:
                self.selected_idx = 0
                self.selected_key = self.keys[0]
        else:
            self.selected_idx = 0

    def cycle(self, step: int) -> str | None:
        if not self.keys:
            return None
        self.selected_idx = (self.selected_idx + int(step)) % len(self.keys)
        self.selected_key = self.keys[self.selected_idx]
        return self.selected_key


@dataclass
class RuntimeState:
    fund_domain: FundDomainRuntimeState = field(default_factory=FundDomainRuntimeState)


@dataclass(frozen=True)
class NewsItem:
    id: str
    published_at: float
    source: str
    category: str
    severity: str
    symbols: tuple[str, ...]
    source_group: str = ""
    source_tier: str = ""
    title: str = ""
    summary: str = ""
    url: str = ""
    direction: str = "Neutral"
    confidence: float = 0.50
    impact_assets: tuple[str, ...] = ()
    suggestion: str = ""


@dataclass
class NewsPageState:
    focus: str = "middle"  # middle / right
    watch_selected: int = 0
    news_selected: int = 0
    news_scroll: int = 0
    category_idx: int = 0
    source_idx: int = 0
    window_idx: int = 2
    search_query: str = ""
    watch_filter_locked: bool = False


@dataclass(frozen=True)
class NewsFeedSnapshot:
    mode: str  # DB / RSS / LIVE / MIX
    items: tuple[NewsItem, ...] = ()
    feeds: tuple[str, ...] = ()
    last_ok_at: float = 0.0
    latest_item_at: float = 0.0
    refresh_s: float = 0.0
    last_error: str = ""
    health: NewsHealthSnapshot = field(default_factory=NewsHealthSnapshot)


@dataclass(frozen=True)
class ServiceStatus:
    data_running: int = 0
    data_total: int = 4
    signal_up: bool = False
    trading_up: bool = False
    signal_data_fresh: bool = False
    trading_data_fresh: bool = False
    signal_data_age_s: int | None = None
    trading_data_age_s: int | None = None
    checked_at: float = 0.0


@dataclass
class DirtyFlags:
    db: bool = False
    quotes: bool = False
    micro: bool = False
    ui: bool = False
    services: bool = False
    layout: bool = False
    forced: bool = False

    def any(self) -> bool:
        return self.db or self.quotes or self.micro or self.ui or self.services or self.layout or self.forced


@dataclass
class RenderState:
    db_sig: tuple | None = None
    quote_sig: tuple | None = None
    micro_sig: tuple | None = None
    ui_sig: tuple | None = None
    service_sig: tuple | None = None
    layout_sig: tuple[int, int] | None = None
    last_draw_at: float = 0.0


@dataclass(frozen=True)
class BacktestSymbolContribution:
    symbol: str
    pnl_net: float | None = None
    trade_count: int | None = None
    win_rate_pct: float | None = None
    avg_holding_minutes: float | None = None


@dataclass(frozen=True)
class BacktestCompareDelta:
    key: str
    history_count: int
    rule_count: int
    delta: int


@dataclass
class BacktestCompareSnapshot:
    available: bool = False
    run_id: str = "--"
    history_run_id: str = "--"
    rule_run_id: str = "--"
    delta_return_pct: float | None = None
    delta_max_drawdown_pct: float | None = None
    delta_trade_count: int | None = None
    delta_excess_return_pct: float | None = None
    delta_signal_count: int | None = None
    history_buy_ratio_pct: float | None = None
    rule_buy_ratio_pct: float | None = None
    delta_buy_ratio_pct: float | None = None
    rule_history_types: int | None = None
    rule_rule_types: int | None = None
    rule_shared_types: int | None = None
    rule_jaccard_pct: float | None = None
    alignment_score: float | None = None
    alignment_status: str = "--"
    alignment_risk_level: str = "--"
    alignment_risk_summary: str = ""
    alignment_warning_count: int = 0
    alignment_warning_summary: str = ""
    signal_type_delta_top: list[BacktestCompareDelta] = field(default_factory=list)
    missing_rule_reason: str = ""


@dataclass
class BacktestSnapshot:
    available: bool = False
    status: str = "no backtest artifacts"
    mode: str = "--"
    run_id: str = "--"
    date_range: str = "--"
    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    sharpe: float | None = None
    win_rate_pct: float | None = None
    trade_count: int | None = None
    avg_holding_minutes: float | None = None
    buy_hold_return_pct: float | None = None
    excess_return_pct: float | None = None
    quality_score: float | None = None
    quality_status: str = "--"
    quality_summary: str = ""
    stability_status: str = "--"
    stability_summary: str = ""
    stability_comparable_run_count: int = 0
    strategy_label: str = "--"
    strategy_summary: str = ""
    equity_points: list[float] = field(default_factory=list)
    symbol_contributions: list[BacktestSymbolContribution] = field(default_factory=list)
    recent_trades: list[str] = field(default_factory=list)
    is_walk_forward: bool = False
    wf_fold_count: int | None = None
    wf_positive_fold_rate_pct: float | None = None
    wf_history_fold_count: int | None = None
    wf_replay_fold_count: int | None = None
    wf_fallback_fold_count: int | None = None


@dataclass(frozen=True)
class BacktestRunStateSnapshot:
    status: str = "idle"
    stage: str = "idle"
    run_id: str = "--"
    mode: str = "--"
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    latest_run_id: str = "--"
    message: str = ""
    error: str = ""


@dataclass
class DebounceSwitch:
    version: int = 0
    ready_at: float = 0.0

    def bump(self, now_ts: float, delay_s: float) -> None:
        self.version += 1
        self.ready_at = float(now_ts) + max(0.0, float(delay_s))


def _read_env_ratio(name: str, default: float, min_value: float = 0.25, max_value: float = 0.60) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    if not math.isfinite(value):
        return float(default)
    return max(float(min_value), min(float(max_value), float(value)))


def _adaptive_left_min_width(total_width: int, *, base_min: int, floor_min: int, min_ratio: float) -> int:
    width = max(0, int(total_width))
    if width <= 0:
        return max(0, int(floor_min))
    ratio_bound = int(width * max(0.0, float(min_ratio)))
    return max(int(floor_min), min(int(base_min), ratio_bound))


_CLOSED_CURVE_STALE_SECONDS = 180
_CLOSED_CURVE_MIN_POINTS = 20
_CLOSED_CURVE_HISTORY_LIMIT = 60
_CLOSED_CURVE_RETRY_SECONDS = 120
_CLOSED_CURVE_TARGET_SPAN_SECONDS = 45 * 60
_FUND_CN_CURVE_DAYS = max(5, int(os.environ.get("TUI_FUND_CN_CURVE_DAYS", "15")))
_FUND_CN_CURVE_REFRESH_SECONDS = max(30.0, float(os.environ.get("TUI_FUND_CN_CURVE_REFRESH_SECONDS", "300")))
_MARKET_MICRO_LEFT_RATIO = _read_env_ratio("TUI_MARKET_MICRO_LEFT_RATIO", 0.36)
_MARKET_MICRO_LEFT_BASE_MIN_WIDTH = 34
_MARKET_MICRO_LEFT_FLOOR_MIN_WIDTH = 24
_MARKET_MICRO_LEFT_MIN_RATIO = 0.24
_MARKET_MICRO_RIGHT_MIN_WIDTH = 28
_RENDER_IDLE_REDRAW_S = 2.0
_RENDER_FRAME_INTERVAL_S = 1.0 / 30.0
_SWITCH_DEBOUNCE_S = 0.15
_PRIMARY_MARKET_VIEWS = ("market_us", "market_cn", "market_hk", "market_fund_cn", "market_micro", "market_news")
_BACKTEST_VIEW = "market_backtest"
_NEWS_CATEGORIES = ("全部", "宏观", "公司", "加密", "政策")
_NEWS_SOURCE_FILTER_ALL = "全部"
_NEWS_SOURCE_FILTER_PRIMARY = "主链"
_NEWS_SOURCE_FILTER_SUPPLEMENTAL = "补充"
_NEWS_WINDOWS_H = (1, 6, 24)


def _default_tui_news_rss_feeds_value() -> str:
    preset = (os.getenv("TUI_NEWS_RSS_PRESET", "") or os.getenv("NEWS_RSS_PRESET", "")).strip()
    return default_tui_news_rss_feeds_value(preset)


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(8):
        if (cur / "services").exists() and (cur / "services-preview").exists() and (cur / "config").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)
_DATA_PID_FILES = [
    _REPO_ROOT / "services" / "data-service" / "pids" / "daemon.pid",
    _REPO_ROOT / "services" / "data-service" / "pids" / "backfill.pid",
    _REPO_ROOT / "services" / "data-service" / "pids" / "metrics.pid",
    _REPO_ROOT / "services" / "data-service" / "pids" / "ws.pid",
]
_SIGNAL_PID_FILE = _REPO_ROOT / "services" / "signal-service" / "logs" / "signal-service.pid"
_TRADING_PID_FILE = _REPO_ROOT / "services" / "trading-service" / "pids" / "service.pid"
_SIGNAL_HISTORY_DB_FILE = _REPO_ROOT / "libs" / "database" / "services" / "signal-service" / "signal_history.db"
_TRADING_SQLITE_DB_FILE = _REPO_ROOT / "libs" / "database" / "services" / "telegram-service" / "market_data.db"
_SIGNAL_FRESH_MAX_AGE_S = max(300, int(float(os.environ.get("TUI_SIGNAL_FRESH_MAX_AGE_SECONDS", "43200"))))
_TRADING_FRESH_MAX_AGE_S = max(60, int(float(os.environ.get("TUI_TRADING_FRESH_MAX_AGE_SECONDS", "900"))))
_BACKTEST_LATEST_DIR = _REPO_ROOT / "artifacts" / "backtest" / "latest"
_BACKTEST_RUN_STATE_PATH = _REPO_ROOT / "artifacts" / "backtest" / "run_state.json"
_BACKTEST_MAX_EQUITY_POINTS = 600
_BACKTEST_RECENT_TRADES = 8
_BACKTEST_SHOW_COMPARE = str(os.environ.get("TUI_BACKTEST_SHOW_COMPARE", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _backtest_mode_text(mode: str) -> str:
    """Human-friendly backtest mode label for the TUI."""

    key = str(mode or "").strip().lower()
    mapping = {
        "offline_rule_replay": "RULE(129规则离线重放)",
        "history_signal": "HISTORY(signal_history回放)",
        "offline_replay": "OFFLINE(PG K线伪信号)",
        "compare_history_rule": "COMPARE(history vs rule)",
    }
    return mapping.get(key, str(mode or "--").strip() or "--")


def _is_pid_file_running(pid_file: Path) -> bool:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip().splitlines()[0]
    except Exception:
        return False
    if not raw:
        return False
    try:
        pid = int(raw)
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _file_age_seconds(path: Path, now_ts: float) -> int | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return max(0, int(float(now_ts) - float(stat.st_mtime)))


def _collect_service_status(now_ts: float | None = None) -> ServiceStatus:
    checked_at = float(now_ts if now_ts is not None else time.time())
    signal_age_s = _file_age_seconds(_SIGNAL_HISTORY_DB_FILE, checked_at)
    trading_age_s = _file_age_seconds(_TRADING_SQLITE_DB_FILE, checked_at)
    signal_up = _is_pid_file_running(_SIGNAL_PID_FILE)
    trading_up = _is_pid_file_running(_TRADING_PID_FILE)
    return ServiceStatus(
        data_running=sum(1 for path in _DATA_PID_FILES if _is_pid_file_running(path)),
        data_total=len(_DATA_PID_FILES),
        signal_up=signal_up,
        trading_up=trading_up,
        signal_data_fresh=signal_age_s is not None and signal_age_s <= _SIGNAL_FRESH_MAX_AGE_S,
        trading_data_fresh=trading_age_s is not None and trading_age_s <= _TRADING_FRESH_MAX_AGE_S,
        signal_data_age_s=signal_age_s,
        trading_data_age_s=trading_age_s,
        checked_at=checked_at,
    )


def _format_service_status_bar(status: ServiceStatus) -> str:
    if status.data_running <= 0:
        data_txt = "数据离线"
    elif status.data_running >= max(1, status.data_total):
        data_txt = "数据在线"
    else:
        data_txt = f"数据{status.data_running}/{status.data_total}"
    if not status.signal_up:
        sig_txt = "信号离线"
    elif status.signal_data_fresh:
        sig_txt = "信号在线"
    else:
        sig_txt = "信号陈旧"
    if not status.trading_up:
        trd_txt = "交易离线"
    elif status.trading_data_fresh:
        trd_txt = "交易在线"
    else:
        trd_txt = "交易陈旧"
    return f"服务 {data_txt} | {sig_txt} | {trd_txt}"


def _view_display_name(view: str) -> str:
    mapping = {
        "market_us": "行情-美股",
        "market_cn": "行情-A股",
        "market_hk": "行情-港股",
        "market_fund_cn": "行情-基金",
        "market_micro": "行情-加密",
        "market_news": "资讯",
        "market_backtest": "回测",
        "quotes_us": "报价-美股",
        "quotes_cn": "报价-A股",
        "quotes_hk": "报价-港股",
        "quotes_crypto": "报价-加密",
        "quotes_metals": "报价-金属",
        "signals": "信号",
    }
    return mapping.get((view or "").strip().lower(), view)


def _market_display_name(market: str, fallback: str = "") -> str:
    mapping = {
        "us_stock": "美股",
        "hk_stock": "港股",
        "cn_stock": "A股",
        "cn_fund": "基金",
        "crypto_spot": "加密",
        "metals": "金属",
        "metals_spot": "金属",
    }
    return mapping.get((market or "").strip().lower(), fallback or market)


class _HotReloadRequested(RuntimeError):
    """Internal marker exception used to restart curses wrapper in dev mode."""


class _HotReloadWatcher:
    def __init__(self, roots: list[Path], poll_s: float = 1.0) -> None:
        self._roots = [r.resolve() for r in roots]
        self._poll_s = max(0.2, float(poll_s))
        self._last_check = 0.0
        self._fingerprint = self._snapshot()

    def _iter_files(self) -> Iterable[Path]:
        for root in self._roots:
            if root.is_file():
                yield root
                continue
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.py")):
                if path.is_file():
                    yield path

    def _snapshot(self) -> tuple[tuple[str, int, int], ...]:
        files: list[tuple[str, int, int]] = []
        for path in self._iter_files():
            try:
                st = path.stat()
            except OSError:
                continue
            files.append((str(path), int(st.st_mtime_ns), int(st.st_size)))
        return tuple(files)

    def should_reload(self, now_ts: float | None = None) -> bool:
        now = float(time.time() if now_ts is None else now_ts)
        if (now - self._last_check) < self._poll_s:
            return False
        self._last_check = now
        current = self._snapshot()
        if current == self._fingerprint:
            return False
        self._fingerprint = current
        return True


def _build_hot_reload_watcher(enabled: bool, poll_s: float = 1.0) -> _HotReloadWatcher | None:
    if not enabled:
        return None
    src_root = Path(__file__).resolve().parent
    # Monitor only source files to avoid restart loops from runtime state writes.
    return _HotReloadWatcher([src_root], poll_s=poll_s)


class QuotePoller:
    def __init__(self, cfg: QuoteConfig) -> None:
        self._cfg = cfg
        self._cfg_lock = threading.Lock()
        self._lock = threading.Lock()
        self._state = QuoteBookState()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._t = threading.Thread(target=self._run, name="quote-poller", daemon=True)

    def start(self) -> None:
        if not self._cfg.enabled:
            return
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            self._t.join(timeout=1.0)
        except Exception:
            pass

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
            self._wake.set()

    def snapshot(self) -> QuoteBookState:
        with self._lock:
            return QuoteBookState(entries=dict(self._state.entries))

    def set_symbols(self, symbols: list[str]) -> None:
        with self._cfg_lock:
            self._cfg.symbols = list(symbols)
        self._wake.set()

    def request_refresh(self) -> None:
        self._wake.set()

    def _set_one(self, symbol: str, quote: Quote | None, err: str) -> None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return
        with self._lock:
            prev = self._state.entries.get(sym)
            now = time.time()
            if quote is None and prev and prev.quote is not None:
                # Keep last known quote on transient failures; preserve last_fetch_at to reflect staleness.
                self._state.entries[sym] = QuoteEntryState(quote=prev.quote, last_error=err, last_fetch_at=prev.last_fetch_at)
            else:
                # Success (or first-ever failure with no previous quote).
                last_ok = now if quote is not None else (prev.last_fetch_at if prev else 0.0)
                self._state.entries[sym] = QuoteEntryState(quote=quote, last_error=err, last_fetch_at=last_ok)

    def _run(self) -> None:
        # Poll in a background thread so the UI never blocks on network IO.
        while not self._stop.is_set():
            if self._paused.is_set():
                self._wake.clear()
                self._stop.wait(timeout=0.2)
                continue

            started = time.time()
            try:
                with self._cfg_lock:
                    cur_syms = list(self._cfg.symbols or [])
                syms = [s.strip().upper() for s in cur_syms if (s or "").strip()]
                if not syms:
                    self._stop.wait(timeout=0.5)
                    continue

                if str(self._cfg.market).strip().lower() == "crypto_spot":
                    # Per-symbol fetch so one flaky endpoint won't break the whole page,
                    # and we can attach per-symbol error messages. Run in parallel so a slow symbol/provider
                    # doesn't stall the whole watchlist refresh.
                    max_workers = min(8, max(1, len(syms)))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                        futs: dict[concurrent.futures.Future[Quote | None], str] = {}
                        for sym in syms:
                            futs[
                                ex.submit(
                                    fetch_quote,
                                    provider=self._cfg.provider,
                                    market=self._cfg.market,
                                    symbol=sym,
                                    timeout_s=self._cfg.timeout_s,
                                )
                            ] = sym
                        for fut in concurrent.futures.as_completed(futs):
                            sym = futs[fut]
                            try:
                                q = fut.result()
                                if q is None:
                                    self._set_one(sym, None, "no data")
                                else:
                                    self._set_one(sym, q, "")
                            except Exception as e:
                                self._set_one(sym, None, f"{type(e).__name__}: {e}")
                else:
                    res = fetch_quotes(
                        provider=self._cfg.provider,
                        market=self._cfg.market,
                        symbols=syms,
                        timeout_s=self._cfg.timeout_s,
                    )
                    for sym in syms:
                        q = res.get(sym)
                        if q is None:
                            self._set_one(sym, None, "no data")
                        else:
                            self._set_one(sym, q, "")
            except Exception as e:
                for sym in (self._cfg.symbols or []):
                    self._set_one(sym, None, f"{type(e).__name__}: {e}")

            # Sleep remaining interval (if any).
            elapsed = time.time() - started
            sleep_s = max(0.1, float(self._cfg.refresh_s) - elapsed)
            if sleep_s <= 0:
                continue
            deadline = time.time() + sleep_s
            while not self._stop.is_set():
                remain = deadline - time.time()
                if remain <= 0:
                    break
                if self._wake.wait(timeout=min(0.2, remain)):
                    self._wake.clear()
                    break


_RSS_TAG_RE = re.compile(r"<[^>]+>")


def _rss_local(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _rss_text(el) -> str:
    if el is None:
        return ""
    return (getattr(el, "text", "") or "").strip()


def _rss_strip_html(raw: str) -> str:
    if not raw:
        return ""
    raw = html.unescape(raw)
    raw = _RSS_TAG_RE.sub(" ", raw)
    return " ".join(raw.split())


def _rss_parse_dt(raw: str) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return float(dt.timestamp())
    except Exception:
        pass
    # Atom often uses RFC3339/ISO8601 (e.g. 2026-03-05T12:34:56Z)
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return None


def _rss_find_first(el, names: tuple[str, ...]):
    for child in list(el):
        if _rss_local(getattr(child, "tag", "")) in names:
            return child
    return None


def _rss_all_children(el, name: str):
    out = []
    for child in list(el):
        if _rss_local(getattr(child, "tag", "")) == name:
            out.append(child)
    return out


def _rss_atom_link(entry) -> str:
    for link in _rss_all_children(entry, "link"):
        rel = (getattr(link, "attrib", {}) or {}).get("rel", "")
        href = (getattr(link, "attrib", {}) or {}).get("href", "")
        rel = (rel or "").strip().lower()
        href = (href or "").strip()
        if not href:
            continue
        if rel in ("", "alternate"):
            return href
    for link in _rss_all_children(entry, "link"):
        href = (getattr(link, "attrib", {}) or {}).get("href", "")
        href = (href or "").strip()
        if href:
            return href
    return ""


def _parse_rss_feed(xml_text: str) -> tuple[str, list[dict[str, object]]]:
    """
    Parse RSS/Atom XML text (best-effort, stdlib only).
    Returns: (feed_title, entries) where entry has: title/url/published_at/summary/categories.
    """
    xml_text = (xml_text or "").strip()
    if not xml_text:
        return "", []

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
    except Exception:
        return "", []

    root_name = _rss_local(getattr(root, "tag", "")).lower()

    if root_name == "feed":  # Atom
        feed_title = _rss_strip_html(_rss_text(_rss_find_first(root, ("title",))))
        entries: list[dict[str, object]] = []
        for entry in _rss_all_children(root, "entry"):
            title = _rss_strip_html(_rss_text(_rss_find_first(entry, ("title",))))
            url = _rss_atom_link(entry) or _rss_strip_html(_rss_text(_rss_find_first(entry, ("id",))))
            summary = _rss_strip_html(_rss_text(_rss_find_first(entry, ("summary",))))
            content = _rss_strip_html(_rss_text(_rss_find_first(entry, ("content",))))
            published = _rss_text(_rss_find_first(entry, ("published", "updated")))
            published_at = _rss_parse_dt(published)
            categories: list[str] = []
            for cat in _rss_all_children(entry, "category"):
                term = (getattr(cat, "attrib", {}) or {}).get("term", "")
                term = (term or "").strip()
                if term:
                    categories.append(term)
                else:
                    categories.append(_rss_strip_html(_rss_text(cat)))
            entries.append(
                {
                    "title": title,
                    "url": url,
                    "published_at": published_at,
                    "summary": summary or content,
                    "categories": [c for c in categories if c],
                }
            )
        return feed_title, entries

    # RSS 2.0: <rss><channel>...<item>...</item></channel></rss>
    channel = _rss_find_first(root, ("channel",)) or root
    feed_title = _rss_strip_html(_rss_text(_rss_find_first(channel, ("title",))))
    entries = []
    for item in channel.iter():
        if _rss_local(getattr(item, "tag", "")) != "item":
            continue
        title = _rss_strip_html(_rss_text(_rss_find_first(item, ("title",))))
        url = _rss_strip_html(_rss_text(_rss_find_first(item, ("link",))))
        if not url:
            url = _rss_strip_html(_rss_text(_rss_find_first(item, ("guid",))))
        pub = _rss_text(_rss_find_first(item, ("pubDate", "date", "published", "updated")))
        published_at = _rss_parse_dt(pub)
        summary = _rss_strip_html(_rss_text(_rss_find_first(item, ("description", "summary"))))
        categories = [_rss_strip_html(_rss_text(c)) for c in _rss_all_children(item, "category")]
        entries.append(
            {
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": summary,
                "categories": [c for c in categories if c],
            }
        )
    return feed_title, entries


_DIRECT_NEWS_PREFIX = "direct://"


def _parse_rss_feeds_value(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    for piece in raw.replace("\n", ",").split(","):
        piece = piece.strip()
        if piece:
            parts.append(piece)
    out: list[str] = []
    for u in parts:
        normalized = u.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered.startswith(_DIRECT_NEWS_PREFIX):
            out.append(lowered)
            continue
        if normalized.startswith("file://"):
            out.append(normalized)
            continue
        if normalized.startswith("http://") or normalized.startswith("https://"):
            out.append(normalized)
            continue
        if normalized.startswith("//"):
            out.append(f"https:{normalized}")
            continue
        # Friendly fallback: allow "rsshub.app/jin10" (no scheme) in env/config.
        if re.match(r"^[A-Za-z0-9.-]+/.+", normalized) and "." in normalized.split("/", 1)[0]:
            out.append(f"https://{normalized}")
            continue
        local_path = Path(normalized).expanduser()
        if not local_path.is_absolute():
            local_path = (_REPO_ROOT / local_path).resolve()
        if local_path.exists():
            out.append(local_path.as_uri())
    return out


def _split_direct_news_source(source: str) -> str:
    value = (source or "").strip().lower()
    if value.startswith(_DIRECT_NEWS_PREFIX):
        return value[len(_DIRECT_NEWS_PREFIX) :].strip().strip("/")
    return value


def _is_direct_news_source(source: str) -> bool:
    return (source or "").strip().lower().startswith(_DIRECT_NEWS_PREFIX)


def _news_source_mode(feeds: tuple[str, ...]) -> str:
    count = len(feeds)
    has_direct = any(_is_direct_news_source(feed) for feed in feeds)
    has_rss = any(not _is_direct_news_source(feed) for feed in feeds)
    if has_direct and has_rss:
        return f"MIX({count})"
    if has_direct:
        return f"LIVE({count})"
    return "RSS"


def _news_source_code(feed_url: str, feed_title: str) -> str:
    if "demo.tradecat.local" in (feed_url or "").strip().lower() or (feed_url or "").startswith("file://"):
        return "DEMO"
    code = news_source_code(feed_url, feed_title)
    if code:
        return code
    title = (feed_title or "").strip()
    if title:
        return _truncate(title, 4)
    return "RSS"


def _news_normalize_text(value: object) -> str:
    return _rss_strip_html(str(value or "").strip())


def _news_parse_timestamp(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        return ts if ts > 0 else None
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        try:
            return _news_parse_timestamp(float(raw))
        except Exception:
            return None
    return _rss_parse_dt(raw)


def _news_with_default_tz(value: object, tz_suffix: str = "+08:00") -> object:
    raw = str(value or "").strip()
    if not raw:
        return value
    if re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", raw):
        return raw
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$", raw):
        return f"{raw}{tz_suffix}"
    return value


def _news_symbol_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        candidate = ""
        if isinstance(item, dict):
            for key in ("symbol", "code", "stockCode", "stock_code", "secuCode", "secu_code", "ticker", "name"):
                candidate = str(item.get(key) or "").strip()
                if candidate:
                    break
        else:
            candidate = str(item or "").strip()
        if not candidate:
            continue
        candidate = candidate.upper()
        if candidate not in out:
            out.append(candidate)
    return tuple(out[:8])


def _news_http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, object] | None = None,
    timeout_s: float = 10.0,
    allow_jsonp: bool = False,
) -> object:
    if params:
        query = urllib.parse.urlencode([(str(k), str(v)) for k, v in params.items()], doseq=True)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    req_headers = {"User-Agent": "TradeCatTUI/news"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    raw = body.decode("utf-8", errors="ignore").lstrip("\ufeff").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if not allow_jsonp:
            raise
        start = raw.find("(")
        end = raw.rfind(")")
        if start >= 0 and end > start:
            return json.loads(raw[start + 1 : end].strip())
        raise


def _news_http_get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, object] | None = None,
    timeout_s: float = 10.0,
) -> str:
    if params:
        query = urllib.parse.urlencode([(str(k), str(v)) for k, v in params.items()], doseq=True)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{query}"
    req_headers = {"User-Agent": "TradeCatTUI/news"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    return body.decode("utf-8", errors="ignore")


def _direct_news_entry(
    *,
    title: object,
    summary: object,
    url: object,
    published_at: object,
    categories: list[str] | None = None,
    severity: str = "",
    symbols: tuple[str, ...] = (),
) -> dict[str, object] | None:
    title_text = _news_normalize_text(title)
    summary_text = _news_normalize_text(summary)
    if not title_text:
        title_text = summary_text
    if not title_text:
        return None
    ts = _news_parse_timestamp(published_at)
    if ts is None:
        return None
    return {
        "title": title_text,
        "summary": summary_text or title_text,
        "url": str(url or "").strip(),
        "published_at": ts,
        "categories": [str(cat).strip() for cat in (categories or []) if str(cat).strip()],
        "severity": str(severity or "").strip().upper(),
        "symbols": symbols,
    }


def _fetch_direct_news_entries(source: str, timeout_s: float) -> list[dict[str, object]]:
    target = _split_direct_news_source(source)
    if target == "jin10":
        payload = _news_http_get_json(
            "https://flash-api.jin10.com/get_flash_list",
            headers={"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"},
            params={"channel": "-8200", "vip": "1"},
            timeout_s=timeout_s,
        )
        rows = payload.get("data") if isinstance(payload, dict) else []
        entries: list[dict[str, object]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or int(row.get("type") or 0) == 1:
                continue
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            content = _news_normalize_text(data.get("content") if isinstance(data, dict) else "")
            title = _news_normalize_text(data.get("title") if isinstance(data, dict) else "")
            if not title and content:
                matched = re.match(r"^【([^】]+)】\s*(.*)$", content)
                if matched:
                    title = matched.group(1).strip()
                    content = matched.group(2).strip() or content
                else:
                    title = content
            entry = _direct_news_entry(
                title=title,
                summary=content,
                url=(data.get("source_link") if isinstance(data, dict) else "") or "https://www.jin10.com/",
                published_at=_news_with_default_tz(row.get("time") if isinstance(row, dict) else None),
                categories=[
                    _news_normalize_text(tag.get("name"))
                    for tag in (row.get("tags") if isinstance(row.get("tags"), list) else [])
                    if isinstance(tag, dict)
                ],
                severity="HIGH" if int(row.get("important") or 0) > 0 else "",
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "gelonghui/live":
        payload = _news_http_get_json(
            "https://www.gelonghui.com/api/live-channels/all/lives/v4",
            timeout_s=timeout_s,
        )
        rows = payload.get("result") if isinstance(payload, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            source_info = row.get("source") if isinstance(row.get("source"), dict) else {}
            categories = [
                _news_normalize_text(source_info.get("name") if isinstance(source_info, dict) else ""),
                _news_normalize_text(row.get("contentPrefix")),
            ]
            entry = _direct_news_entry(
                title=row.get("title") or row.get("content"),
                summary=row.get("content") or row.get("title"),
                url=row.get("route") or "https://www.gelonghui.com/live",
                published_at=row.get("createTimestamp"),
                categories=categories,
                severity="HIGH" if int(row.get("level") or 0) >= 2 else "",
                symbols=_news_symbol_list(row.get("relatedStocks")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "10jqka/realtimenews":
        payload = _news_http_get_json(
            "https://news.10jqka.com.cn/tapp/news/push/stock",
            params={"page": "1", "tag": ""},
            timeout_s=timeout_s,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        rows = data.get("list") if isinstance(data, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories = []
            for tag in (row.get("tags") if isinstance(row.get("tags"), list) else []):
                if isinstance(tag, dict):
                    categories.append(_news_normalize_text(tag.get("name")))
            for tag in (row.get("tagInfo") if isinstance(row.get("tagInfo"), list) else []):
                if isinstance(tag, dict):
                    categories.append(_news_normalize_text(tag.get("name")))
            entry = _direct_news_entry(
                title=row.get("title") or row.get("digest"),
                summary=row.get("short") or row.get("digest") or row.get("title"),
                url=row.get("url") or row.get("shareUrl") or "https://news.10jqka.com.cn/realtimenews.html",
                published_at=row.get("ctime") or row.get("rtime"),
                categories=categories,
                severity="HIGH" if str(row.get("color") or "") == "2" else "",
                symbols=_news_symbol_list(row.get("stock")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "sina/7x24":
        payload = _news_http_get_json(
            "https://zhibo.sina.com.cn/api/zhibo/feed",
            params={"zhibo_id": "152", "page": "1", "pagesize": "50", "tag_id": "0", "dire": "f"},
            timeout_s=timeout_s,
        )
        data = payload.get("result") if isinstance(payload, dict) else {}
        feed = data.get("data") if isinstance(data, dict) else {}
        feed_info = feed.get("feed") if isinstance(feed, dict) else {}
        rows = feed_info.get("list") if isinstance(feed_info, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            ext_raw = row.get("ext")
            ext: dict[str, object] = {}
            if isinstance(ext_raw, str) and ext_raw.strip():
                try:
                    parsed_ext = json.loads(ext_raw)
                    if isinstance(parsed_ext, dict):
                        ext = parsed_ext
                except Exception:
                    ext = {}
            categories = [
                _news_normalize_text(tag.get("name"))
                for tag in (row.get("tag") if isinstance(row.get("tag"), list) else [])
                if isinstance(tag, dict)
            ]
            entry = _direct_news_entry(
                title=row.get("rich_text"),
                summary=row.get("rich_text"),
                url=row.get("docurl") or ext.get("docurl") or "https://finance.sina.com.cn/7x24/notification.shtml",
                published_at=_news_with_default_tz(row.get("create_time")),
                categories=categories,
                severity="HIGH" if int(row.get("is_focus") or 0) > 0 else "",
                symbols=_news_symbol_list(ext.get("stocks")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "eastmoney/kuaixun":
        payload = _news_http_get_json(
            "http://newsapi.eastmoney.com/kuaixun/v2/api/list",
            params={"column": "102", "p": "1", "limit": "50", "callback": "cb"},
            timeout_s=timeout_s,
            allow_jsonp=True,
        )
        rows = payload.get("news") if isinstance(payload, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories = [_news_normalize_text(row.get("Art_Media_Name"))]
            entry = _direct_news_entry(
                title=row.get("title") or row.get("digest"),
                summary=row.get("digest") or row.get("title"),
                url=row.get("url_m") or row.get("url_w") or "https://kuaixun.eastmoney.com/7_24.html",
                published_at=_news_with_default_tz(row.get("showtime") or row.get("ordertime")),
                categories=categories,
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "cls/telegraph":
        raw = _news_http_get_text("https://www.cls.cn/telegraph", timeout_s=timeout_s)
        matched = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw, re.S)
        if not matched:
            return []
        payload = json.loads(matched.group(1))
        props = payload.get("props") if isinstance(payload, dict) else {}
        state = {}
        if isinstance(props, dict):
            state = props.get("initialState") if isinstance(props.get("initialState"), dict) else {}
            if not state:
                page_props = props.get("pageProps") if isinstance(props.get("pageProps"), dict) else {}
                state = page_props.get("initialState") if isinstance(page_props.get("initialState"), dict) else {}
        telegraph = state.get("telegraph") if isinstance(state, dict) else {}
        rows = telegraph.get("telegraphList") if isinstance(telegraph, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories = []
            for subject in (row.get("subjects") if isinstance(row.get("subjects"), list) else []):
                if isinstance(subject, dict):
                    categories.append(_news_normalize_text(subject.get("subject_name")))
            entry = _direct_news_entry(
                title=row.get("title") or row.get("content"),
                summary=row.get("brief") or row.get("content") or row.get("title"),
                url=row.get("shareurl") or "https://www.cls.cn/telegraph",
                published_at=row.get("ctime") or row.get("modified_time"),
                categories=categories,
                severity="HIGH" if str(row.get("level") or "").upper() in {"A", "B"} else "",
                symbols=_news_symbol_list(row.get("stock_list")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "wallstreetcn/live":
        payload = _news_http_get_json(
            "https://api-one.wallstcn.com/apiv1/content/lives",
            params={"channel": "global-channel", "limit": "100"},
            timeout_s=timeout_s,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        rows = data.get("items") if isinstance(data, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories = [_news_normalize_text(row.get("global_channel_name"))]
            categories.extend(
                _news_normalize_text(tag)
                for tag in (row.get("channels") if isinstance(row.get("channels"), list) else [])
                if isinstance(tag, str)
            )
            entry = _direct_news_entry(
                title=row.get("title") or row.get("content_text"),
                summary=row.get("content_text") or row.get("title"),
                url=row.get("uri") or "https://wallstreetcn.com/live",
                published_at=row.get("display_time"),
                categories=categories,
                severity="HIGH" if int(row.get("score") or 0) >= 2 else "",
                symbols=_news_symbol_list(row.get("symbols")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "eeo/kuaixun":
        payload = _news_http_get_json(
            "https://app.eeo.com.cn/",
            params={
                "app": "article",
                "controller": "index",
                "action": "getMoreArticle",
                "catid": "3690",
                "uuid": "b048c7211db949eeb7443cd5b9b3bfe3",
                "page": "1",
                "pageSize": "50",
            },
            timeout_s=timeout_s,
        )
        rows = payload.get("data") if isinstance(payload, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            entry = _direct_news_entry(
                title=row.get("title") or row.get("description"),
                summary=row.get("description") or row.get("content") or row.get("title"),
                url=row.get("url") or "https://www.eeo.com.cn/kuaixun/",
                published_at=_news_with_default_tz(row.get("published")),
                categories=[_news_normalize_text(row.get("catname"))],
            )
            if entry is not None:
                entries.append(entry)
        return entries

    raise ValueError(f"unsupported direct news source: {source}")


def _infer_news_category(categories: list[str], title: str) -> str:
    cat_blob = " ".join([c for c in categories if c]).lower()
    t = (title or "").lower()
    blob = f"{cat_blob} {t}"

    crypto_keys = ("crypto", "btc", "eth", "比特币", "以太坊", "数字货币", "加密")
    if any(k in blob for k in crypto_keys):
        return "加密"
    policy_keys = ("fed", "ecb", "bis", "央行", "美联储", "加息", "降息", "利率", "政策", "监管")
    if any(k in blob for k in policy_keys):
        return "政策"
    company_keys = ("earnings", "guidance", "sec", "filing", "财报", "公司", "公告")
    if any(k in blob for k in company_keys):
        return "公司"
    return "宏观"


def _infer_news_severity(title: str, summary: str) -> str:
    blob = f"{title} {summary}".lower()
    high_keys = ("突发", "breaking", "emergency", "紧急", "爆炸", "崩盘")
    if any(k in blob for k in high_keys):
        return "HIGH"
    return "MID"


def _news_dedup_id(source: str, url: str, title: str, published_at: float) -> str:
    key = (url or "").strip()
    if not key:
        key = f"{source}|{int(published_at)}|{title}".strip()
    digest = hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()
    return f"rss-{digest[:16]}"


def _news_item_from_stored_article(article: StoredNewsArticle) -> NewsItem:
    source = _news_source_code(article.url or article.source, article.source)
    summary = article.summary.strip() or article.title.strip()
    clean_categories = tuple(
        str(item).strip() for item in article.categories if str(item).strip() and not str(item).strip().lower().startswith("tc_source_")
    )
    source_group, source_tier = extract_source_meta_tags(article.categories)
    if source_group == UNKNOWN_GROUP:
        source_group = news_source_group(article.url or article.source, source)
    if source_tier == UNKNOWN_TIER:
        source_tier = news_source_tier(article.url or article.source, source)
    category = _infer_news_category(list(clean_categories), article.title)
    severity = _infer_news_severity(article.title, summary)
    symbols = tuple(article.symbols)
    item_id = article.dedup_hash.strip() or _news_dedup_id(source, article.url, article.title, article.published_at)
    return NewsItem(
        id=item_id,
        published_at=float(article.published_at),
        source=source,
        category=category,
        severity=severity,
        symbols=symbols,
        source_group=source_group,
        source_tier=source_tier,
        title=article.title.strip(),
        summary=summary,
        url=article.url.strip() or article.source.strip(),
        direction="Neutral",
        confidence=0.50,
        impact_assets=symbols,
        suggestion="",
    )


class RssNewsPoller:
    """Poll the unified news chain for the TUI.

    Preferred path: read the configured `<schema>.news_articles` table from the database.
    Fallback path: fetch the configured direct/RSS feeds locally when the DB is
    unavailable or still empty.
    """

    def __init__(
        self,
        feeds: list[str],
        *,
        refresh_s: float = 2.0,
        timeout_s: float = 5.0,
        max_items: int = 300,
        database_url: str = "",
        database_schema: str = "alternative",
        database_window_h: int = 72,
        database_timeout_s: float = 5.0,
        database_stale_after_s: float = 60.0,
    ) -> None:
        self._feeds = tuple(_parse_rss_feeds_value(",".join(feeds)))
        self._mode = _news_source_mode(self._feeds)
        self._refresh_s = max(2.0, float(refresh_s))
        self._timeout_s = max(1.0, float(timeout_s))
        self._max_items = max(50, int(max_items))
        self._database_url = str(database_url or "").strip()
        self._database_schema = str(database_schema or "alternative").strip() or "alternative"
        self._database_window_h = max(1, int(database_window_h))
        self._database_timeout_s = max(1.0, float(database_timeout_s))
        self._database_stale_after_s = max(15.0, float(database_stale_after_s))
        self._collector_health_log = resolve_news_health_log_path(_REPO_ROOT)
        self._lock = threading.Lock()
        self._items: list[NewsItem] = []
        self._last_error = ""
        self._last_ok_at = 0.0
        self._latest_item_at = 0.0
        self._health = NewsHealthSnapshot()
        self._backend = "DB" if self._database_url else (self._mode or "RSS")
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._wake = threading.Event()
        self._t = threading.Thread(target=self._run, name="news-poller", daemon=True)

    def start(self) -> None:
        if not self._database_url and not self._feeds:
            return
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            self._t.join(timeout=1.0)
        except Exception:
            pass

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
            self._wake.set()

    def request_refresh(self) -> None:
        self._wake.set()

    def snapshot(self) -> NewsFeedSnapshot:
        with self._lock:
            mode = "DB" if self._backend == "DB" else self._mode
            return NewsFeedSnapshot(
                mode=mode,
                items=tuple(self._items),
                feeds=tuple(self._feeds),
                last_ok_at=float(self._last_ok_at),
                latest_item_at=float(self._latest_item_at),
                refresh_s=float(self._refresh_s),
                last_error=str(self._last_error or ""),
                health=self._health,
            )

    def _fetch_one_feed(self, feed_url: str, headers: dict[str, str], started: float) -> list[NewsItem]:
        if _is_direct_news_source(feed_url):
            feed_title = ""
            entries = _fetch_direct_news_entries(feed_url, timeout_s=self._timeout_s)
        else:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                body = resp.read()
            text = body.decode("utf-8", errors="ignore")
            feed_title, entries = _parse_rss_feed(text)

        source = _news_source_code(feed_url, feed_title)
        source_group = news_source_group(feed_url, source)
        source_tier = news_source_tier(feed_url, source)
        is_demo_feed = feed_url.startswith("file://")
        items: list[NewsItem] = []
        for idx, entry in enumerate(entries):
            title = _news_normalize_text(entry.get("title"))
            if not title:
                continue
            url = str(entry.get("url") or "").strip()
            published_at = entry.get("published_at")
            ts = float(published_at) if isinstance(published_at, (int, float)) and float(published_at) > 0 else started
            if is_demo_feed and (ts > started or started - ts > 24 * 3600):
                ts = started - idx * 900
            summary = _news_normalize_text(entry.get("summary"))
            cats = entry.get("categories") or []
            categories = [str(c).strip() for c in cats] if isinstance(cats, list) else []
            category = _infer_news_category(categories, title)
            severity = str(entry.get("severity") or "").strip().upper() or _infer_news_severity(title, summary)
            symbols = _news_symbol_list(entry.get("symbols"))
            items.append(
                NewsItem(
                    id=_news_dedup_id(source, url, title, ts),
                    published_at=ts,
                    source=source,
                    category=category,
                    severity=severity,
                    symbols=symbols,
                    source_group=source_group,
                    source_tier=source_tier,
                    title=title,
                    summary=summary,
                    url=url or feed_url,
                    direction="Neutral",
                    confidence=0.50,
                    impact_assets=symbols,
                    suggestion="",
                )
            )
        return items

    def _fetch_live_items(self, started: float, headers: dict[str, str]) -> tuple[list[NewsItem], list[str]]:
        if not self._feeds:
            return [], []

        new_items: list[NewsItem] = []
        errors: list[str] = []
        max_workers = min(max(1, len(self._feeds)), 8)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="news-feed") as executor:
            futures = {
                executor.submit(self._fetch_one_feed, feed_url, headers, started): feed_url
                for feed_url in self._feeds
            }
            for future in concurrent.futures.as_completed(futures):
                feed_url = futures[future]
                try:
                    new_items.extend(future.result())
                except Exception as exc:
                    errors.append(f"{_news_source_code(feed_url, '')}: {type(exc).__name__}")

        new_items.sort(key=lambda item: float(item.published_at), reverse=True)
        if len(new_items) > self._max_items:
            new_items = new_items[: self._max_items]
        return new_items, errors

    def _fetch_database_items(self) -> list[NewsItem]:
        rows = fetch_recent_news_articles(
            self._database_url,
            limit=self._max_items,
            window_hours=self._database_window_h,
            timeout_s=self._database_timeout_s,
            schema=self._database_schema,
        )
        items = [_news_item_from_stored_article(row) for row in rows]
        items.sort(key=lambda item: float(item.published_at), reverse=True)
        return items[: self._max_items]

    def _latest_item_ts(self, items: list[NewsItem]) -> float:
        return max((float(item.published_at) for item in items), default=0.0)

    def _db_stale_reason(self, latest_item_at: float, health: NewsHealthSnapshot, now_ts: float) -> str:
        if health.available and float(health.checked_at) > 0:
            health_age_s = max(0.0, float(now_ts) - float(health.checked_at))
            if health_age_s > self._database_stale_after_s:
                return f"collector stale {_news_age_text(now_ts, float(health.checked_at))}"

        if latest_item_at <= 0:
            return ""

        item_age_s = max(0.0, float(now_ts) - float(latest_item_at))
        fallback_threshold_s = max(300.0, self._database_stale_after_s * 5.0)
        if item_age_s > fallback_threshold_s:
            return f"db stale {_news_age_text(now_ts, latest_item_at)}"
        return ""

    def _refresh_once(self, *, started: float | None = None, headers: dict[str, str] | None = None) -> None:
        request_headers = dict(headers or {"User-Agent": "TradeCatTUI/rss"})
        started_at = float(started) if started is not None else time.time()
        new_items: list[NewsItem] = []
        errors: list[str] = []
        live_errors: list[str] = []
        backend = self._mode or "RSS"
        db_attempted = False
        db_success = False
        db_items: list[NewsItem] = []
        db_health = NewsHealthSnapshot()
        db_stale_reason = ""
        live_attempted = False

        if self._database_url:
            db_attempted = True
            try:
                db_items = self._fetch_database_items()
                db_success = True
                db_health = load_news_collector_health(self._collector_health_log)
                if db_items:
                    latest_db_item_at = self._latest_item_ts(db_items)
                    db_stale_reason = self._db_stale_reason(latest_db_item_at, db_health, started_at)
                    new_items = db_items
                    if not db_stale_reason:
                        backend = "DB"
                    else:
                        errors.append(db_stale_reason)
            except Exception as exc:
                errors.append(f"DB: {type(exc).__name__}")

        should_try_live = bool(self._feeds) and ((not new_items) or bool(db_stale_reason))
        if should_try_live:
            live_attempted = True
            live_items, live_errors = self._fetch_live_items(started_at, request_headers)
            if live_items:
                new_items = live_items
                backend = self._mode or "RSS"
                errors = list(live_errors)
            else:
                errors.extend(live_errors)
                if db_stale_reason and db_items:
                    new_items = db_items
                    backend = "DB"

        finished_at = time.time()
        latest_item_at = self._latest_item_ts(new_items)
        health = NewsHealthSnapshot()
        if live_attempted:
            health = build_live_news_health(len(self._feeds), live_errors, checked_at=finished_at)
        elif db_attempted:
            health = db_health
        elif self._feeds:
            health = build_live_news_health(len(self._feeds), errors, checked_at=finished_at)

        with self._lock:
            self._backend = backend if new_items else ("DB" if (db_attempted and db_success) else (self._backend or backend))
            if new_items:
                self._items = new_items
                self._last_ok_at = finished_at
                self._latest_item_at = latest_item_at
                if db_stale_reason and backend == "DB":
                    self._last_error = "; ".join(errors) if errors else db_stale_reason
                else:
                    self._last_error = ""
                if health.available:
                    self._health = health
            else:
                if health.available:
                    self._health = health
                if db_attempted and db_success:
                    self._last_ok_at = finished_at
                    if self._items:
                        self._latest_item_at = max((float(item.published_at) for item in self._items), default=0.0)
                        self._last_error = db_stale_reason or ""
                    else:
                        self._latest_item_at = 0.0
                        self._last_error = db_stale_reason or "waiting for collector"
                else:
                    self._last_error = "; ".join(errors) if errors else (self._last_error or "no data")

    def _run(self) -> None:
        headers = {"User-Agent": "TradeCatTUI/rss"}

        while not self._stop.is_set():
            if self._paused.is_set():
                self._wake.clear()
                self._stop.wait(timeout=0.2)
                continue

            started = time.time()
            self._refresh_once(started=started, headers=headers)
            finished_at = time.time()
            elapsed = finished_at - started
            sleep_s = max(0.2, self._refresh_s - elapsed)
            self._wake.clear()
            if sleep_s > 0:
                self._wake.wait(timeout=sleep_s)


def _init_colors() -> dict[str, int]:
    if not curses.has_colors():
        return {}
    curses.start_color()
    try:
        curses.use_default_colors()
    except Exception:
        pass

    # Pair IDs must be 1..; keep small and stable.
    curses.init_pair(1, curses.COLOR_RED, -1)     # BUY / Up
    curses.init_pair(2, curses.COLOR_GREEN, -1)   # SELL / Down
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # ALERT / header accent
    curses.init_pair(4, curses.COLOR_CYAN, -1)    # source
    return {"BUY": 1, "SELL": 2, "ALERT": 3, "SRC": 4}


def _fmt_time(ts: str) -> str:
    dt = parse_ts(ts)
    if dt == datetime.min:
        return "--:--:--"
    return dt.strftime("%H:%M:%S")


def _fmt_date(ts: str) -> str:
    dt = parse_ts(ts)
    if dt == datetime.min:
        return "--:--:--"[:8]
    return dt.strftime("%y-%m-%d")


def _fmt_quote_ts(ts: str) -> str:
    """Compact quote timestamp for narrow TUI tables (YYYY -> YY)."""
    raw = (ts or "").strip()
    if not raw:
        return "--"

    dt = parse_ts(raw)
    if dt != datetime.min:
        return dt.strftime("%y-%m-%d %H:%M:%S")

    # Best effort fallback when provider returns non-ISO timestamps.
    if len(raw) >= 4 and raw[:4].isdigit():
        return raw[2:]
    return raw


def _fmt_quote_ts_date8(ts: str) -> str:
    """Date-only compact timestamp used by master pane (YY-MM-DD)."""
    s = _fmt_quote_ts(ts)
    if s == "--":
        return s
    if " " in s:
        return s.split(" ", 1)[0][:8]
    return s[:8]


def _crypto_signal_symbol_to_pair(symbol: str) -> str:
    """
    Convert signal symbol format (e.g. BTCUSDT) to quote pair format (e.g. BTC_USDT).
    This enables "signals <-> quotes" alignment in the TUI.
    """
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    s = s.replace("/", "").replace("-", "").replace("_", "")
    # Common quotes
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if s.endswith(quote) and len(s) > len(quote):
            base = s[: -len(quote)]
            return f"{base}_{quote}"
    return s


def _build_latest_signal_map(rows: list[SignalRow]) -> dict[str, SignalRow]:
    """
    Build a map: crypto_pair -> latest signal row (newest wins).
    Uses best-effort symbol normalization.
    """
    out: dict[str, SignalRow] = {}
    for r in rows:
        pair = _crypto_signal_symbol_to_pair(r.symbol)
        if not pair or "_" not in pair:
            continue
        if pair not in out:
            out[pair] = r
    return out


def _normalize_cn_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    s = s.replace("/", "").replace("-", "").replace("_", "")
    if s.endswith(".SH") and len(s) >= 9:
        s = "SH" + s[:-3]
    elif s.endswith(".SZ") and len(s) >= 9:
        s = "SZ" + s[:-3]
    if s.startswith("SH") or s.startswith("SZ"):
        digits = "".join(ch for ch in s[2:] if ch.isdigit())
        if len(digits) == 6:
            return s[:2] + digits
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 6:
        if digits[0] in {"5", "6", "9"}:
            return "SH" + digits
        return "SZ" + digits
    return s


def _normalize_cn_fund_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    s = s.replace("/", "").replace("-", "").replace("_", "")
    if s.endswith(".SH"):
        s = "SH" + s[:-3]
    elif s.endswith(".SZ"):
        s = "SZ" + s[:-3]
    if s.startswith(("SH", "SZ")):
        digits = "".join(ch for ch in s[2:] if ch.isdigit())
        if len(digits) == 6:
            return s[:2] + digits
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 6:
        return digits
    return ""


def _normalize_hk_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return s
    return digits.zfill(5)


def _match_signal_to_symbol(signal_symbol: str, quote_symbol: str, market: str) -> bool:
    m = (market or "").strip().lower()
    qsym = (quote_symbol or "").strip().upper()
    if not qsym:
        return False
    if m == "crypto_spot":
        return _crypto_signal_symbol_to_pair(signal_symbol) == qsym
    if m == "us_stock":
        return (signal_symbol or "").strip().upper() == qsym
    if m == "hk_stock":
        return _normalize_hk_symbol(signal_symbol) == _normalize_hk_symbol(qsym)
    if m == "cn_stock":
        return _normalize_cn_symbol(signal_symbol) == _normalize_cn_symbol(qsym)
    if m == "cn_fund":
        qfund = _normalize_cn_fund_symbol(qsym)
        sfund = _normalize_cn_fund_symbol(signal_symbol)
        if qfund.startswith(("SH", "SZ")):
            return _normalize_cn_symbol(sfund) == _normalize_cn_symbol(qfund)
        # 支持场外基金(6位代码)匹配，避免基金页“有信号但显示0”。
        qdigits = "".join(ch for ch in qfund if ch.isdigit())
        sdigits = "".join(ch for ch in sfund if ch.isdigit())
        if len(qdigits) == 6:
            return sdigits == qdigits
        return False
    return False


def _signals_for_symbol(rows: list[SignalRow], quote_symbol: str, market: str) -> list[SignalRow]:
    out: list[SignalRow] = []
    for row in rows:
        if _match_signal_to_symbol(row.symbol, quote_symbol, market):
            out.append(row)
    return out


def _build_signal_radar_rows(
    rows: list[SignalRow],
    symbol: str,
    market: str,
    now_dt: datetime,
    *,
    window_minutes: int = 15,
    min_strength: int = 65,
) -> list[tuple[SignalRow, int]]:
    focus = (symbol or "").strip().upper()
    if not focus:
        return []

    out: list[tuple[SignalRow, int]] = []
    max_age_s = max(1, int(window_minutes)) * 60
    min_str = int(min_strength)
    for row in rows:
        if not _match_signal_to_symbol(row.symbol, focus, market):
            continue
        ts_dt = parse_ts(row.timestamp)
        if ts_dt == datetime.min:
            continue
        age_s = max(0, int((now_dt - ts_dt).total_seconds()))
        if age_s > max_age_s:
            continue
        try:
            strength = int(row.strength)
        except (TypeError, ValueError):
            continue
        if strength < min_str:
            continue
        out.append((row, age_s))
    return out


def _fmt_freshness(ts: str, now_dt: datetime) -> str:
    dt = parse_ts(ts)
    if dt == datetime.min:
        return "--"
    return f"{max(0, int((now_dt - dt).total_seconds()))}s"


def _fmt_duration_compact(seconds: int | None) -> str:
    if seconds is None:
        return "--"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h:02d}h"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def _window_signal_stats(
    rows: list[SignalRow],
    now_dt: datetime,
    *,
    windows_minutes: tuple[int, ...] = (60, 24 * 60),
) -> tuple[dict[int, dict[str, int]], int | None, int | None]:
    stats = {
        int(minutes): {"rows": 0, "buy": 0, "sell": 0, "alert": 0, "net": 0}
        for minutes in windows_minutes
    }
    parsed_ages: list[int] = []

    for row in rows:
        ts_dt = parse_ts(row.timestamp)
        if ts_dt == datetime.min:
            continue
        age_s = max(0, int((now_dt - ts_dt).total_seconds()))
        parsed_ages.append(age_s)

        direction = (row.direction or "").upper()
        strength = _safe_int(row.strength, 0)

        for minutes in windows_minutes:
            if age_s > int(minutes) * 60:
                continue
            bucket = stats[int(minutes)]
            bucket["rows"] += 1
            if direction == "BUY":
                bucket["buy"] += 1
                bucket["net"] += strength
            elif direction == "SELL":
                bucket["sell"] += 1
                bucket["net"] -= strength
            elif direction in {"ALERT", "ALER"}:
                bucket["alert"] += 1

    if not parsed_ages:
        return stats, None, None
    return stats, min(parsed_ages), max(parsed_ages)


def _signal_row_age_seconds(row: SignalRow, now_dt: datetime) -> int | None:
    ts_dt = parse_ts(row.timestamp)
    if ts_dt == datetime.min:
        return None
    return max(0, int((now_dt - ts_dt).total_seconds()))


def _count_recent_signal_rows(rows: list[SignalRow], now_dt: datetime, *, max_age_s: int) -> int:
    limit_s = max(0, int(max_age_s))
    count = 0
    for row in rows:
        age_s = _signal_row_age_seconds(row, now_dt)
        if age_s is not None and age_s <= limit_s:
            count += 1
    return count


def _split_signal_rows_by_age(
    rows: list[SignalRow],
    now_dt: datetime,
) -> tuple[list[tuple[SignalRow, int]], list[tuple[SignalRow, int]], list[tuple[SignalRow, int]]]:
    realtime_rows: list[tuple[SignalRow, int]] = []
    h1_rows: list[tuple[SignalRow, int]] = []
    h12_rows: list[tuple[SignalRow, int]] = []

    for row in rows:
        age_s = _signal_row_age_seconds(row, now_dt)
        if age_s is None:
            continue
        if age_s <= 5 * 60:
            realtime_rows.append((row, age_s))
        elif age_s <= 60 * 60:
            h1_rows.append((row, age_s))
        elif age_s <= 12 * 60 * 60:
            h12_rows.append((row, age_s))

    return realtime_rows, h1_rows, h12_rows


def _latest_signal_row(rows: list[SignalRow], now_dt: datetime) -> tuple[SignalRow, int] | None:
    latest: tuple[SignalRow, int] | None = None
    for row in rows:
        age_s = _signal_row_age_seconds(row, now_dt)
        if age_s is None:
            continue
        if latest is None or age_s < latest[1]:
            latest = (row, age_s)
    return latest


def _build_recent_signal_panel_title(rows: list[SignalRow], now_dt: datetime) -> str:
    base = "规则信号列表（5min/1h/12h）"
    latest = _latest_signal_row(rows, now_dt)
    if latest is None:
        return f"{base} | 暂无历史信号"

    latest_row, latest_age_s = latest
    if latest_age_s <= 12 * 60 * 60:
        return base

    return (
        f"{base} | 近12h无信号 | 最新={_fmt_date(latest_row.timestamp)} "
        f"{_fmt_time(latest_row.timestamp)} ({_fmt_duration_compact(latest_age_s)}前)"
    )


def _fmt_vol(v: float) -> str:
    if v <= 0:
        return "--"
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.2f}K"
    return f"{v:.0f}"


def _display_symbol(sym: str, market: str) -> str:
    symbol = (sym or "").strip().upper()
    if market == "hk_stock":
        return f"{symbol}.HK"
    if market == "cn_stock" and len(symbol) > 2 and symbol[:2] in {"SH", "SZ"}:
        return f"{symbol[2:]}.{symbol[:2]}"
    if market == "cn_fund":
        if len(symbol) > 2 and symbol[:2] in {"SH", "SZ"}:
            return f"{symbol[2:]}.{symbol[:2]}"
        if symbol.isdigit() and len(symbol) == 6:
            return symbol
    if market == "crypto_spot" and "_" in symbol:
        return symbol.replace("_", "/")
    if market in {"metals", "metals_spot"} and symbol.endswith("USD") and len(symbol) >= 6:
        return f"{symbol[:3]}/USD"
    return symbol


def _display_name(sym: str, q: Quote | None, market: str) -> str:
    if q is not None:
        name = (q.name or "").strip().replace("\n", " ")
        if name:
            return name
    return _display_symbol(sym, market)


def _line_chars() -> tuple[str, str, str, str, str, str]:
    # Avoid ncurses ACS fallback glyphs (q/x/l/m/...) on some terminals.
    encoding = (locale.getpreferredencoding(False) or "").lower()
    if "utf" in encoding:
        return ("│", "─", "┌", "┐", "└", "┘")
    return ("|", "-", "+", "+", "+", "+")


def _safe_vline(win, y: int, x: int, height: int, attr: int = 0) -> None:
    if height <= 0:
        return
    vline, _, _, _, _, _ = _line_chars()
    for i in range(height):
        _safe_addstr(win, y + i, x, vline, attr)


def _safe_hline(win, y: int, x: int, width: int, attr: int = 0) -> None:
    if width <= 0:
        return
    _, hline, _, _, _, _ = _line_chars()
    _safe_addstr(win, y, x, hline * width, attr)


def _draw_box(win, x: int, y: int, width: int, height: int, attr: int = 0) -> None:
    if width < 2 or height < 2:
        return
    left = x
    top = y
    right = x + width - 1
    bottom = y + height - 1

    _safe_hline(win, top, left + 1, max(0, width - 2), attr)
    _safe_hline(win, bottom, left + 1, max(0, width - 2), attr)
    _safe_vline(win, top + 1, left, max(0, height - 2), attr)
    _safe_vline(win, top + 1, right, max(0, height - 2), attr)

    _, _, tl, tr, bl, br = _line_chars()
    _safe_addstr(win, top, left, tl, attr)
    _safe_addstr(win, top, right, tr, attr)
    _safe_addstr(win, bottom, left, bl, attr)
    _safe_addstr(win, bottom, right, br, attr)


def _safe_addstr(win, y: int, x: int, s: str, attr: int = 0) -> None:
    try:
        win.addstr(y, x, s, attr)
    except Exception:
        # Avoid crashing on edge cases (small terminal, wide chars, etc.)
        pass


def _signal_rows_signature(rows: list[SignalRow]) -> tuple[int, int, int, str, str]:
    if not rows:
        return (0, 0, 0, "", "")
    newest = rows[0]
    oldest = rows[-1]
    return (len(rows), int(newest.id), int(oldest.id), str(newest.timestamp), str(oldest.timestamp))


def _quote_book_signature(state: QuoteBookState) -> tuple:
    out: list[tuple] = []
    for sym, entry in sorted(state.entries.items()):
        q = entry.quote
        if q is None:
            out.append((sym, 0.0, "", "", round(float(entry.last_fetch_at), 3), (entry.last_error or "").strip()))
            continue
        out.append(
            (
                sym,
                round(float(q.price), 6),
                round(float(q.volume), 3),
                round(float(q.amount), 3),
                (q.ts or "").strip(),
                (q.source or "").strip(),
                round(float(entry.last_fetch_at), 3),
                (entry.last_error or "").strip(),
            )
        )
    return tuple(out)


def _curve_map_signature(curve_map: dict[str, list[Candle]]) -> tuple:
    out: list[tuple] = []
    for sym in sorted(curve_map):
        curve = curve_map[sym]
        if not curve:
            out.append((sym, 0, 0, 0.0, 0.0))
            continue
        last = curve[-1]
        out.append(
            (
                sym,
                len(curve),
                int(last.ts_open),
                round(float(last.close), 6),
                round(float(last.volume_est), 3),
            )
        )
    return tuple(out)


def _micro_snapshot_signature(snapshot: MicroSnapshot) -> tuple:
    candles = snapshot.candles
    last_candle = candles[-1] if candles else None
    flow = snapshot.flow
    last_flow = flow[-1] if flow else None
    return (
        (snapshot.symbol or "").strip().upper(),
        int(snapshot.interval_s),
        round(float(snapshot.last_price), 6),
        (snapshot.last_source or "").strip(),
        (snapshot.last_quote_ts or "").strip(),
        (snapshot.error or "").strip(),
        len(candles),
        0 if last_candle is None else int(last_candle.ts_open),
        0.0 if last_candle is None else round(float(last_candle.close), 6),
        len(flow),
        0.0 if last_flow is None else round(float(last_flow.ts), 3),
        "" if last_flow is None else (last_flow.side or "").strip(),
        round(float(snapshot.signals.score), 4),
        (snapshot.signals.bias or "").strip(),
    )


def _service_status_signature(status: ServiceStatus) -> tuple[int, int, bool, bool, bool, bool]:
    return (
        int(status.data_running),
        int(status.data_total),
        bool(status.signal_up),
        bool(status.trading_up),
        bool(status.signal_data_fresh),
        bool(status.trading_data_fresh),
    )


def _char_display_width(ch: str) -> int:
    if not ch:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in {"F", "W"}:
        return 2
    return 1


def _text_display_width(text: str) -> int:
    return sum(_char_display_width(ch) for ch in (text or ""))


def _truncate(s: str, width: int) -> str:
    if width <= 0:
        return ""

    text = str(s or "")
    if _text_display_width(text) <= width:
        return text

    if width == 1:
        return ">"

    budget = width - 1
    used = 0
    out: list[str] = []
    for ch in text:
        w = _char_display_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w

    # Keep ASCII-only suffix to avoid locale-specific truncation glyph issues.
    return "".join(out) + ">"


def _fit_cell(text: str, width: int, *, align: str = "left") -> str:
    """
    Fit text into a fixed display-width cell (handles CJK full-width chars).
    """
    if width <= 0:
        return ""
    clipped = _truncate(text, width)
    pad = max(0, width - _text_display_width(clipped))
    if align == "right":
        return (" " * pad) + clipped
    return clipped + (" " * pad)


@dataclass(frozen=True)
class NewsWatchItem:
    symbol: str
    market: str
    name: str
    price: float | None
    pct: float | None


def _news_time_text(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def _news_age_text(now_ts: float, ts: float) -> str:
    age_s = max(0, int(now_ts - float(ts)))
    if age_s < 60:
        return f"{age_s}s"
    minutes, _ = divmod(age_s, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _collect_news_watch_items(
    quote_cfgs: QuoteConfigs,
    quote_state_us: QuoteBookState,
    quote_state_hk: QuoteBookState,
    quote_state_cn: QuoteBookState,
    quote_state_crypto: QuoteBookState,
) -> list[NewsWatchItem]:
    picks: list[tuple[str, str]] = []
    picks.extend([("crypto_spot", s) for s in list(quote_cfgs.crypto.symbols)[:3]])
    picks.extend([("us_stock", s) for s in list(quote_cfgs.us.symbols)[:3]])
    picks.extend([("hk_stock", s) for s in list(quote_cfgs.hk.symbols)[:2]])
    picks.extend([("cn_stock", s) for s in list(quote_cfgs.cn.symbols)[:2]])

    out: list[NewsWatchItem] = []
    seen: set[str] = set()
    for market, sym_raw in picks:
        sym = (sym_raw or "").strip().upper()
        if not sym or f"{market}:{sym}" in seen:
            continue
        seen.add(f"{market}:{sym}")
        if market == "crypto_spot":
            state = quote_state_crypto.entries.get(sym)
        elif market == "us_stock":
            state = quote_state_us.entries.get(sym)
        elif market == "hk_stock":
            state = quote_state_hk.entries.get(sym)
        else:
            state = quote_state_cn.entries.get(sym)
        q = state.quote if state else None
        name = _display_name(sym, q, market)
        price = float(q.price) if q is not None else None
        pct: float | None = None
        if q is not None and q.prev_close:
            pct = (float(q.price) - float(q.prev_close)) / float(q.prev_close) * 100.0
        out.append(NewsWatchItem(symbol=sym, market=market, name=name, price=price, pct=pct))
    return out


def _news_source_group_label(group: str, tier: str) -> str:
    normalized_group = (group or "").strip().lower()
    normalized_tier = (tier or "").strip().lower()
    if normalized_tier == PRIMARY_TIER or normalized_group == CORE_GROUP:
        return _NEWS_SOURCE_FILTER_PRIMARY
    if normalized_tier == SUPPLEMENTAL_TIER:
        return _NEWS_SOURCE_FILTER_SUPPLEMENTAL
    return "--"


def _news_source_filter_options(items: list[NewsItem]) -> tuple[str, ...]:
    options: list[str] = [_NEWS_SOURCE_FILTER_ALL]
    seen_codes: set[str] = set()
    has_primary = False
    has_supplemental = False
    codes: list[str] = []

    for item in items:
        if (item.source_tier or "").strip().lower() == PRIMARY_TIER:
            has_primary = True
        elif (item.source_tier or "").strip().lower() == SUPPLEMENTAL_TIER:
            has_supplemental = True

        code = (item.source or "").strip().upper()
        if code and code not in seen_codes:
            seen_codes.add(code)
            codes.append(code)

    if has_primary:
        options.append(_NEWS_SOURCE_FILTER_PRIMARY)
    if has_supplemental:
        options.append(_NEWS_SOURCE_FILTER_SUPPLEMENTAL)
    options.extend(sorted(codes))
    return tuple(options)


def _matches_news_source_filter(item: NewsItem, source_filter: str) -> bool:
    current = (source_filter or "").strip()
    if not current or current == _NEWS_SOURCE_FILTER_ALL:
        return True
    if current == _NEWS_SOURCE_FILTER_PRIMARY:
        return (item.source_tier or "").strip().lower() == PRIMARY_TIER
    if current == _NEWS_SOURCE_FILTER_SUPPLEMENTAL:
        return (item.source_tier or "").strip().lower() == SUPPLEMENTAL_TIER
    return (item.source or "").strip().upper() == current.upper()


def _filter_news_items(
    items: list[NewsItem],
    *,
    now_ts: float,
    category: str,
    window_h: int,
    search_query: str,
    source_filter: str = _NEWS_SOURCE_FILTER_ALL,
    watch_symbol: str = "",
) -> list[NewsItem]:
    max_age = max(1, int(window_h)) * 3600
    query = (search_query or "").strip().lower()
    ws = (watch_symbol or "").strip().upper()
    filtered: list[NewsItem] = []
    for item in items:
        age_s = now_ts - item.published_at
        if age_s < 0 or age_s > max_age:
            continue
        if category != "全部" and item.category != category:
            continue
        if not _matches_news_source_filter(item, source_filter):
            continue
        if ws and ws not in item.symbols and ws not in item.impact_assets:
            # RSS feeds may not provide structured symbol tags yet; fall back to fuzzy matching.
            hay = f"{item.title} {item.summary} {item.url}".upper()
            keywords = {ws}
            if "_" in ws:
                keywords.update({p for p in ws.split("_") if p})
            # A/H-share common prefixes.
            if (ws.startswith("SH") or ws.startswith("SZ")) and len(ws) > 2:
                keywords.add(ws[2:])
            if not any(k and k in hay for k in keywords):
                continue
        if query:
            haystack = " ".join(
                [
                    item.title,
                    item.summary,
                    item.source,
                    item.category,
                    " ".join(item.symbols),
                    " ".join(item.impact_assets),
                ]
            ).lower()
            if query not in haystack:
                continue
        filtered.append(item)
    return filtered


def _build_news_events(items: list[NewsItem]) -> list[NewsEvent]:
    return cluster_news_items(items)


def _coerce_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    fv = _coerce_float(value)
    if fv is None:
        return None
    return int(round(fv))


def _coerce_pct(value: object) -> float | None:
    fv = _coerce_float(value)
    if fv is None:
        return None
    if abs(fv) <= 1.0:
        return fv * 100.0
    return fv


def _extract_metric(payload: dict, keys: tuple[str, ...]) -> object | None:
    if not isinstance(payload, dict):
        return None

    for key in keys:
        if key in payload:
            return payload.get(key)

    for container_key in ("summary", "metrics", "result", "stats", "performance"):
        nested = payload.get(container_key)
        if not isinstance(nested, dict):
            continue
        for key in keys:
            if key in nested:
                return nested.get(key)

    return None


def _resample_series(values: list[float], max_points: int) -> list[float]:
    if max_points <= 0 or not values:
        return []
    if len(values) <= max_points:
        return list(values)
    if max_points == 1:
        return [values[-1]]

    out: list[float] = []
    step = (len(values) - 1) / float(max_points - 1)
    for i in range(max_points):
        idx = int(round(i * step))
        idx = max(0, min(len(values) - 1, idx))
        out.append(float(values[idx]))
    return out


def _load_equity_curve(path: Path, max_points: int = _BACKTEST_MAX_EQUITY_POINTS) -> list[float]:
    if not path.exists():
        return []

    values: list[float] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            has_header = True
            try:
                has_header = csv.Sniffer().has_header(sample)
            except Exception:
                has_header = True

            if has_header:
                reader = csv.DictReader(fh)
                for row in reader:
                    if not row:
                        continue
                    val: float | None = None
                    for key in (
                        "equity",
                        "equity_value",
                        "balance",
                        "net_value",
                        "value",
                        "asset",
                        "capital",
                        "close",
                    ):
                        val = _coerce_float(row.get(key))
                        if val is not None:
                            break
                    if val is None:
                        for raw in row.values():
                            val = _coerce_float(raw)
                            if val is not None:
                                break
                    if val is not None:
                        values.append(float(val))
            else:
                reader2 = csv.reader(fh)
                for row in reader2:
                    if not row:
                        continue
                    val: float | None = None
                    for raw in reversed(row):
                        val = _coerce_float(raw)
                        if val is not None:
                            break
                    if val is not None:
                        values.append(float(val))
    except Exception:
        return []

    return _resample_series(values, max_points=max_points)


def _load_recent_trades(path: Path, max_rows: int = _BACKTEST_RECENT_TRADES) -> list[str]:
    if not path.exists():
        return []

    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                parsed: list[str] = []
                for row in reader:
                    ts = (
                        (row.get("exit_ts") or row.get("timestamp") or row.get("time") or row.get("entry_ts") or "--")
                        .strip()
                        .replace("T", " ")
                    )
                    ts = ts[:19] if ts else "--"
                    sym = (row.get("symbol") or row.get("asset") or "--").strip().upper()[:10]
                    side_raw = (row.get("side") or row.get("direction") or "--").strip()
                    side = _display_side_cn(side_raw)[:5]

                    pnl: float | None = None
                    for key in ("realized_pnl", "pnl", "profit", "net_pnl"):
                        pnl = _coerce_float(row.get(key))
                        if pnl is not None:
                            break
                    pnl_txt = f"pnl={pnl:+.2f}" if pnl is not None else "pnl=--"
                    parsed.append(f"{ts:<19} {sym:<10} {side:<5} {pnl_txt}")
                lines = parsed[-max_rows:]
            else:
                fh.seek(0)
                reader2 = csv.reader(fh)
                raw_lines = [" ".join(col.strip() for col in row if col.strip()) for row in reader2 if row]
                lines = [_truncate(line, 96) for line in raw_lines[-max_rows:]]
    except Exception:
        return []

    return lines


def _display_side_cn(side: str) -> str:
    """Human display name for trade sides (keep internal CSV as LONG/SHORT)."""
    s = (side or "").strip().upper()
    if s == "LONG":
        return "做多"
    if s == "SHORT":
        return "做空"
    return (side or "--").strip()


def _load_symbol_contributions(payload: dict, max_rows: int = 4) -> list[BacktestSymbolContribution]:
    raw = payload.get("symbol_contributions")
    if not isinstance(raw, list):
        return []

    rows: list[BacktestSymbolContribution] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "--").strip().upper()[:10]
        rows.append(
            BacktestSymbolContribution(
                symbol=symbol,
                pnl_net=_coerce_float(item.get("pnl_net")),
                trade_count=_coerce_int(item.get("trade_count")),
                win_rate_pct=_coerce_pct(item.get("win_rate_pct")),
                avg_holding_minutes=_coerce_float(item.get("avg_holding_minutes")),
            )
        )

    return rows[: max(0, max_rows)]


def _normalize_compare_base_run_id(run_id: str) -> str:
    rid = str(run_id or "").strip()
    if not rid:
        return ""
    for suffix in ("-history", "-rules", "-compare"):
        if rid.endswith(suffix):
            return rid[: -len(suffix)]
    return rid


def _parse_compare_delta_rows(raw: object, max_rows: int = 3) -> list[BacktestCompareDelta]:
    if not isinstance(raw, list):
        return []

    rows: list[BacktestCompareDelta] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "--").strip()
        if not key:
            key = "--"
        rows.append(
            BacktestCompareDelta(
                key=key,
                history_count=_coerce_int(item.get("history_count")) or 0,
                rule_count=_coerce_int(item.get("rule_count")) or 0,
                delta=_coerce_int(item.get("delta")) or 0,
            )
        )

    return rows[: max(0, max_rows)]


def _resolve_backtest_root(base_dir: Path) -> Path:
    base = Path(base_dir)
    if (base / "latest").exists():
        return base
    if base.name == "latest":
        return base.parent
    if base.parent.name == "backtest":
        return base.parent
    return base


def _find_compare_json(root: Path, base_run: str) -> Path | None:
    direct = root / f"{base_run}-compare" / "comparison.json"
    if direct.exists():
        return direct

    # New layout: artifacts/backtest/<timestamp>/<base_run>-compare/comparison.json
    try:
        nested = [
            p
            for p in root.glob(f"*/{base_run}-compare/comparison.json")
            if p.is_file()
        ]
    except Exception:
        nested = []

    if not nested:
        return None
    nested.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return nested[0]


def _load_backtest_compare_snapshot(
    base_dir: Path = _BACKTEST_LATEST_DIR,
    *,
    run_state: BacktestRunStateSnapshot | None = None,
    current_run_id: str = "",
) -> BacktestCompareSnapshot:
    root = _resolve_backtest_root(Path(base_dir))
    candidate_ids: list[str] = []

    if run_state and run_state.mode == "compare_history_rule" and run_state.run_id != "--":
        candidate_ids.append(run_state.run_id)
    if current_run_id and current_run_id != "--":
        candidate_ids.append(current_run_id)
    if run_state and run_state.latest_run_id != "--":
        candidate_ids.append(run_state.latest_run_id)

    compare_json: Path | None = None
    for rid in candidate_ids:
        base_run = _normalize_compare_base_run_id(rid)
        if not base_run:
            continue
        candidate = _find_compare_json(root, base_run)
        if candidate is None:
            continue
        if candidate.exists():
            compare_json = candidate
            break

    if compare_json is None:
        return BacktestCompareSnapshot()

    try:
        payload = json.loads(compare_json.read_text(encoding="utf-8"))
    except Exception:
        return BacktestCompareSnapshot()

    if not isinstance(payload, dict):
        return BacktestCompareSnapshot()

    history_mix = payload.get("history_direction_mix") if isinstance(payload.get("history_direction_mix"), dict) else {}
    rule_mix = payload.get("rule_direction_mix") if isinstance(payload.get("rule_direction_mix"), dict) else {}
    rule_overlap = payload.get("rule_overlap") if isinstance(payload.get("rule_overlap"), dict) else {}

    missing_rule_reason = ""
    missing_diag = payload.get("missing_history_rules_diagnostics")
    if isinstance(missing_diag, list) and missing_diag:
        top = missing_diag[0]
        if isinstance(top, dict):
            key = str(top.get("key") or "--").strip() or "--"
            reason = str(top.get("primary_block_reason") or "unknown").strip() or "unknown"
            missing_rule_reason = f"{key}: {reason}"

    alignment_status = str(payload.get("alignment_status") or "--").strip().lower() or "--"
    alignment_risk_level = str(payload.get("alignment_risk_level") or "--").strip().lower() or "--"
    alignment_risk_summary = str(payload.get("alignment_risk_summary") or "").strip()
    alignment_warning_summary = ""
    alignment_warnings = payload.get("alignment_warnings") if isinstance(payload.get("alignment_warnings"), list) else []
    if alignment_warnings:
        top_warning = alignment_warnings[0]
        if isinstance(top_warning, dict):
            kind = str(top_warning.get("kind") or "warn").strip() or "warn"
            subject = str(top_warning.get("subject") or "--").strip() or "--"
            alignment_warning_summary = f"{kind}: {subject}"

    return BacktestCompareSnapshot(
        available=True,
        run_id=str(payload.get("run_id") or "--").strip() or "--",
        history_run_id=str(payload.get("history_run_id") or "--").strip() or "--",
        rule_run_id=str(payload.get("rule_run_id") or "--").strip() or "--",
        delta_return_pct=_coerce_float(payload.get("delta_return_pct")),
        delta_max_drawdown_pct=_coerce_float(payload.get("delta_max_drawdown_pct")),
        delta_trade_count=_coerce_int(payload.get("delta_trade_count")),
        delta_excess_return_pct=_coerce_float(payload.get("delta_excess_return_pct")),
        delta_signal_count=_coerce_int(payload.get("delta_signal_count")),
        history_buy_ratio_pct=_coerce_float(history_mix.get("buy_ratio_pct")),
        rule_buy_ratio_pct=_coerce_float(rule_mix.get("buy_ratio_pct")),
        delta_buy_ratio_pct=_coerce_float(payload.get("delta_buy_ratio_pct")),
        rule_history_types=_coerce_int(rule_overlap.get("history_rule_types")),
        rule_rule_types=_coerce_int(rule_overlap.get("rule_rule_types")),
        rule_shared_types=_coerce_int(rule_overlap.get("shared_rule_types")),
        rule_jaccard_pct=_coerce_float(rule_overlap.get("jaccard_pct")),
        alignment_score=_coerce_float(payload.get("alignment_score")),
        alignment_status=alignment_status,
        alignment_risk_level=alignment_risk_level,
        alignment_risk_summary=alignment_risk_summary,
        alignment_warning_count=len(alignment_warnings),
        alignment_warning_summary=alignment_warning_summary,
        signal_type_delta_top=_parse_compare_delta_rows(payload.get("signal_type_delta_top"), max_rows=3),
        missing_rule_reason=missing_rule_reason,
    )


def _extract_walk_forward_payload(base: Path, metrics_payload: dict) -> dict:
    summary_path = base / "walk_forward_summary.json"
    if summary_path.exists():
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}

    nested = metrics_payload.get("walk_forward_summary")
    if isinstance(nested, dict):
        return nested
    return {}


def _apply_walk_forward_snapshot(
    snap: BacktestSnapshot,
    wf_payload: dict,
    *,
    initial_equity: float | None = None,
) -> None:
    if not isinstance(wf_payload, dict):
        return

    fold_count = _coerce_int(wf_payload.get("fold_count"))
    if fold_count is None or fold_count <= 0:
        return

    snap.is_walk_forward = True
    snap.wf_fold_count = fold_count
    snap.wf_positive_fold_rate_pct = _coerce_float(wf_payload.get("positive_fold_rate_pct"))
    snap.wf_history_fold_count = _coerce_int(wf_payload.get("history_fold_count"))
    snap.wf_replay_fold_count = _coerce_int(wf_payload.get("replay_fold_count"))
    snap.wf_fallback_fold_count = _coerce_int(wf_payload.get("fallback_fold_count"))

    avg_ret = _coerce_float(wf_payload.get("avg_return_pct"))
    avg_dd = _coerce_float(wf_payload.get("avg_max_drawdown_pct"))
    avg_excess = _coerce_float(wf_payload.get("avg_excess_return_pct"))
    if avg_ret is not None:
        snap.total_return_pct = avg_ret
    if avg_dd is not None:
        snap.max_drawdown_pct = avg_dd
    if avg_excess is not None:
        snap.excess_return_pct = avg_excess
    if snap.total_return_pct is not None and snap.excess_return_pct is not None:
        snap.buy_hold_return_pct = snap.total_return_pct - snap.excess_return_pct

    folds_raw = wf_payload.get("folds")
    folds = [item for item in folds_raw if isinstance(item, dict)] if isinstance(folds_raw, list) else []

    if folds:
        first_start = folds[0].get("test_start")
        last_end = folds[-1].get("test_end")
        if (snap.date_range == "--") and (first_start or last_end):
            snap.date_range = f"{_compact_backtest_date(first_start)} -> {_compact_backtest_date(last_end)}"

        if not snap.recent_trades:
            lines: list[str] = []
            for item in folds[-_BACKTEST_RECENT_TRADES:]:
                fold_idx = _coerce_int(item.get("fold"))
                fold_txt = "--" if fold_idx is None else f"F{fold_idx:02d}"
                mode_txt = str(item.get("mode") or "--").strip()[:7]
                ret_val = _coerce_float(item.get("total_return_pct"))
                dd_val = _coerce_float(item.get("max_drawdown_pct"))
                trade_val = _coerce_int(item.get("trade_count"))
                ret_txt = "--" if ret_val is None else f"{ret_val:+.2f}%"
                dd_txt = "--" if dd_val is None else f"{dd_val:.2f}%"
                trade_txt = "--" if trade_val is None else str(trade_val)
                ts_txt = _compact_backtest_date(item.get("test_end") or item.get("test_start"))
                lines.append(f"{ts_txt} {fold_txt:<4} {mode_txt:<7} ret={ret_txt} dd={dd_txt} n={trade_txt}")
            snap.recent_trades = lines

        if not snap.equity_points:
            initial = _coerce_float(initial_equity)
            if initial is None or initial <= 0:
                initial = 10_000.0

            curve: list[float] = [float(initial)]
            equity = float(initial)
            for item in folds:
                ret_val = _coerce_float(item.get("total_return_pct"))
                if ret_val is None:
                    continue
                equity *= 1.0 + ret_val / 100.0
                curve.append(float(equity))

            if len(curve) >= 2:
                snap.equity_points = _resample_series(curve, max_points=_BACKTEST_MAX_EQUITY_POINTS)


def _format_symbol_contrib_lines(
    rows: list[BacktestSymbolContribution],
    width: int,
) -> list[tuple[str, int]]:
    if not rows:
        return [("--", 0)]

    max_abs_pnl = max((abs(row.pnl_net) for row in rows if row.pnl_net is not None), default=0.0)
    bar_limit = max(1, min(10, max(1, width // 6)))
    utf = "utf" in (locale.getpreferredencoding(False) or "").lower()
    pos_char = "█" if utf else "+"
    neg_char = "█" if utf else "-"

    compact = width < 36
    medium = 36 <= width < 50

    out: list[tuple[str, int]] = []
    for row in rows:
        pnl_txt = "--" if row.pnl_net is None else f"{row.pnl_net:+.2f}"
        pnl_short = "--" if row.pnl_net is None else f"{row.pnl_net:+.0f}"
        trade_txt = "--" if row.trade_count is None else str(row.trade_count)
        win_txt = "--" if row.win_rate_pct is None else f"{row.win_rate_pct:.1f}%"
        hold_txt = "--" if row.avg_holding_minutes is None else f"{row.avg_holding_minutes:.1f}m"

        sign = 0
        bar = ""
        if row.pnl_net is not None and max_abs_pnl > 1e-9:
            sign = 1 if row.pnl_net >= 0 else -1
            bar_len = max(1, int(round(abs(row.pnl_net) / max_abs_pnl * bar_limit)))
            bar = (pos_char if sign > 0 else neg_char) * bar_len

        if compact:
            base = f"{row.symbol[:8]:<8} {pnl_short:>7}"
        elif medium:
            base = f"{row.symbol[:8]:<8} {pnl_txt:>9} n={trade_txt:>4}"
        else:
            base = f"{row.symbol:<8} {pnl_txt:>9} n={trade_txt:>4} w={win_txt:>6} h={hold_txt:>6}"

        if bar:
            space_left = max(0, width - len(base) - 1)
            if space_left > 0:
                bar = bar[:space_left]
                base = f"{base} {bar}"

        out.append((base, sign))

    return out


def _format_backtest_curve_summary(values: list[float]) -> str:
    if not values:
        return "净值: -- | 最高: -- | 最低: -- | 变化: -- | 均值: --"

    first = float(values[0])
    last = float(values[-1])
    highest = max(values)
    lowest = min(values)
    avg = sum(values) / max(1, len(values))

    if abs(first) <= 1e-9:
        delta_pct = 0.0
    else:
        delta_pct = (last - first) / abs(first) * 100.0

    arrow = "↗" if delta_pct >= 0 else "↘"
    return (
        f"净值: {last:.2f} | 最高: {highest:.2f} | 最低: {lowest:.2f} | "
        f"变化: {arrow} {delta_pct:+.2f}% | 均值: {avg:.2f}"
    )


def _format_backtest_trade_line(raw: str, width: int) -> str:
    line = (raw or "").strip()
    if not line or line == "--":
        return "--"

    parts = line.split()
    if len(parts) >= 4 and len(parts[0]) >= 10:
        day = parts[0][5:10]
        hhmm = parts[1][:5] if len(parts) >= 2 else "--:--"
        symbol = parts[2][:10] if len(parts) >= 3 else "--"
        side_raw = parts[3] if len(parts) >= 4 else "--"
        side = _display_side_cn(side_raw)[:5]

        pnl = "--"
        for item in parts[4:]:
            if item.startswith("pnl="):
                pnl = item.split("=", 1)[1]
                break

        compact = f"{day} {hhmm} {symbol:<10} {side:<5} pnl {pnl}"
        return _truncate(compact, max(0, width))

    return _truncate(line, max(0, width))


def _compute_drawdown_series(values: list[float]) -> list[float]:
    if not values:
        return []

    series: list[float] = []
    peak = float(values[0]) if abs(float(values[0])) > 1e-9 else 1.0
    for value in values:
        fv = float(value)
        if fv > peak:
            peak = fv
        if abs(peak) <= 1e-9:
            series.append(0.0)
        else:
            series.append((fv - peak) / abs(peak) * 100.0)
    return series


def _format_backtest_drawdown_summary(values: list[float]) -> str:
    series = _compute_drawdown_series(values)
    if not series:
        return "回撤: --"

    cur_dd = series[-1]
    max_dd = min(series)
    return f"回撤: 当前 {cur_dd:+.2f}% | 最大 {max_dd:+.2f}%"


def _draw_backtest_drawdown_strip(
    stdscr,
    values: list[float],
    colors: dict[str, int],
    x0: int,
    y: int,
    width: int,
) -> None:
    if width <= 14:
        return

    label = "回撤带"
    label_w = len(label) + 2
    spark_w = max(6, width - label_w - 12)
    if spark_w <= 4:
        return

    dd_series = _compute_drawdown_series(values)
    if not dd_series:
        _safe_addstr(stdscr, y, x0, _truncate(f"{label}: --", width), curses.color_pair(colors.get("SRC", 0)))
        return

    samples = _resample_series(dd_series, max_points=spark_w)
    max_abs = max((abs(v) for v in samples), default=0.0)
    max_abs = max(max_abs, 1e-9)

    utf = "utf" in (locale.getpreferredencoding(False) or "").lower()
    levels = "▁▂▃▄▅▆▇█" if utf else ".-:=+*#@"

    src_attr = curses.color_pair(colors.get("SRC", 0))
    dd_attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD

    _safe_addstr(stdscr, y, x0, f"{label}:", src_attr)

    for idx, value in enumerate(samples):
        if value >= -1e-9:
            char = levels[0]
            attr = src_attr
        else:
            ratio = min(1.0, abs(value) / max_abs)
            level_idx = max(1, int(round(ratio * (len(levels) - 1))))
            char = levels[level_idx]
            attr = dd_attr
        _safe_addstr(stdscr, y, x0 + label_w + idx, char, attr)

    tail = f" {samples[-1]:+.2f}%"
    _safe_addstr(stdscr, y, x0 + label_w + spark_w, _truncate(tail, max(0, width - label_w - spark_w)), src_attr)


def _compact_backtest_date(raw: object) -> str:
    text = str(raw or "").strip().replace("T", " ").replace("Z", "")
    if not text:
        return "--"
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text[:19]


def _parse_backtest_date(raw: object) -> datetime | None:
    text = str(raw or "").strip().replace("T", " ").replace("Z", "")
    if not text:
        return None
    candidates = [text]
    if len(text) >= 19:
        candidates.append(text[:19])
    if len(text) >= 10:
        candidates.append(text[:10])
    for item in dict.fromkeys(candidates):
        try:
            return datetime.fromisoformat(item)
        except Exception:
            continue
    return None


def _short_backtest_date(raw: object) -> str:
    compact = _compact_backtest_date(raw)
    if len(compact) >= 10 and compact[4] == "-" and compact[7] == "-":
        return compact[2:10]
    return compact


def _format_backtest_time_axis(date_range: str, width: int) -> str:
    w = max(0, int(width))
    if w <= 0:
        return ""

    start_raw = "--"
    end_raw = "--"
    if "->" in (date_range or ""):
        left, right = date_range.split("->", 1)
        start_raw = left.strip() or "--"
        end_raw = right.strip() or "--"
    elif date_range:
        start_raw = str(date_range).strip()
        end_raw = str(date_range).strip()

    start_label = _short_backtest_date(start_raw)
    end_label = _short_backtest_date(end_raw)

    mid_label = "--"
    start_dt = _parse_backtest_date(start_raw)
    end_dt = _parse_backtest_date(end_raw)
    if start_dt and end_dt and end_dt >= start_dt:
        mid_label = _short_backtest_date(start_dt + (end_dt - start_dt) / 2)

    if w < 24:
        base = f"{start_label} -> {end_label}"
        return _truncate(base, w)

    utf = "utf" in (locale.getpreferredencoding(False) or "").lower()
    tick = "┬" if utf else "|"

    chars = [" "] * w

    def _put(center_x: int, label: str) -> None:
        if not label:
            return
        x = max(0, min(w - len(label), int(center_x)))
        for i, ch in enumerate(label):
            idx = x + i
            if 0 <= idx < w:
                chars[idx] = ch

    tick_pos = [0, w // 2, w - 1]
    for pos in tick_pos:
        if 0 <= pos < w:
            chars[pos] = tick

    _put(0, start_label)
    _put(max(0, w // 2 - len(mid_label) // 2), mid_label)
    _put(max(0, w - len(end_label)), end_label)
    return "".join(chars)


def _backtest_state_status_text(status: str) -> str:
    mapping = {
        "idle": "空闲",
        "running": "运行中",
        "done": "已完成",
        "error": "异常",
        "unknown": "未知",
    }
    return mapping.get((status or "").strip().lower(), "未知")


def _backtest_state_stage_text(stage: str) -> str:
    mapping = {
        "idle": "空闲",
        "loading_signals": "读取信号",
        "loading_candles": "读取K线",
        "loading_indicator_tables": "读取规则表",
        "replaying_signals": "离线回放",
        "executing": "回测执行",
        "walk_forward": "滚动验证",
        "compare_modes": "模式对比",
        "writing": "写入产物",
        "retention": "更新latest",
        "done": "完成",
        "error": "异常",
    }
    return mapping.get((stage or "").strip().lower(), "未知")


def _load_backtest_run_state(path: Path = _BACKTEST_RUN_STATE_PATH) -> BacktestRunStateSnapshot:
    state_path = Path(path)
    if not state_path.exists():
        return BacktestRunStateSnapshot()

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return BacktestRunStateSnapshot(status="unknown", stage="unknown", message="run_state parse failed")

    if not isinstance(payload, dict):
        return BacktestRunStateSnapshot(status="unknown", stage="unknown", message="run_state invalid payload")

    status = str(payload.get("status") or "idle").strip().lower()
    if status not in {"idle", "running", "done", "error"}:
        status = "unknown"

    stage = str(payload.get("stage") or "idle").strip().lower() or "unknown"

    run_id = str(payload.get("run_id") or "--").strip() or "--"
    mode = str(payload.get("mode") or "--").strip() or "--"
    started_at = str(payload.get("started_at") or "").strip()
    updated_at = str(payload.get("updated_at") or "").strip()
    finished_at = str(payload.get("finished_at") or "").strip()
    latest_run_id = str(payload.get("latest_run_id") or "--").strip() or "--"
    message = str(payload.get("message") or "").strip()
    error = str(payload.get("error") or "").strip()

    return BacktestRunStateSnapshot(
        status=status,
        stage=stage,
        run_id=run_id,
        mode=mode,
        started_at=started_at,
        updated_at=updated_at,
        finished_at=finished_at,
        latest_run_id=latest_run_id,
        message=message,
        error=error,
    )


def _format_backtest_state_line(state: BacktestRunStateSnapshot, width: int) -> str:
    status_txt = _backtest_state_status_text(state.status)
    stage_txt = _backtest_state_stage_text(state.stage)

    run_txt = state.run_id if state.run_id != "--" else state.latest_run_id
    if not run_txt:
        run_txt = "--"

    updated_txt = _fmt_quote_ts(state.updated_at) if state.updated_at else "--"
    line = f"状态: {status_txt} | 阶段: {stage_txt} | run={run_txt} | 更新: {updated_txt}"

    if state.status == "error" and state.error:
        line = f"{line} | err={state.error}"
    elif state.message:
        # Keep the backtest page focused on RULE by default; compare-mode message
        # contains both history + rule returns and can confuse users.
        if (not _BACKTEST_SHOW_COMPARE) and state.mode == "compare_history_rule":
            line = f"{line} | compare done"
        else:
            line = f"{line} | {state.message}"

    return _truncate(line, width)



def _load_backtest_snapshot(base_dir: Path = _BACKTEST_LATEST_DIR) -> BacktestSnapshot:
    base = Path(base_dir)
    metrics_path = base / "metrics.json"
    equity_path = base / "equity_curve.csv"
    trades_path = base / "trades.csv"
    input_quality_path = base / "input_quality.json"
    stability_path = base / "stability_report.json"
    wf_summary_path = base / "walk_forward_summary.json"

    snap = BacktestSnapshot()
    snap.equity_points = _load_equity_curve(equity_path, max_points=_BACKTEST_MAX_EQUITY_POINTS)
    snap.recent_trades = _load_recent_trades(trades_path, max_rows=_BACKTEST_RECENT_TRADES)

    if not metrics_path.exists() and not snap.equity_points and not snap.recent_trades and not wf_summary_path.exists():
        snap.status = "no backtest artifacts yet"
        return snap

    payload: dict = {}
    if metrics_path.exists():
        try:
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
            snap.status = "metrics.json parse failed"

    run_id = _extract_metric(payload, ("run_id", "runId", "id"))
    if run_id is not None and str(run_id).strip():
        snap.run_id = str(run_id).strip()

    mode = _extract_metric(payload, ("mode", "run_mode"))
    if mode is not None and str(mode).strip():
        snap.mode = str(mode).strip()

    start = _extract_metric(payload, ("start", "date_start", "start_ts", "from"))
    end = _extract_metric(payload, ("end", "date_end", "end_ts", "to"))
    if start or end:
        snap.date_range = f"{_compact_backtest_date(start)} -> {_compact_backtest_date(end)}"

    # New artifacts use *_pct fields directly. Legacy fields such as "total_return"
    # and "max_drawdown" may be fractional ratios (0.123 -> 12.3%).
    snap.total_return_pct = _coerce_float(_extract_metric(payload, ("total_return_pct", "return_pct")))
    if snap.total_return_pct is None:
        snap.total_return_pct = _coerce_pct(_extract_metric(payload, ("total_return", "pnl_pct", "roi")))

    snap.max_drawdown_pct = _coerce_float(_extract_metric(payload, ("max_drawdown_pct", "mdd_pct")))
    if snap.max_drawdown_pct is None:
        snap.max_drawdown_pct = _coerce_pct(_extract_metric(payload, ("max_drawdown", "mdd")))

    snap.sharpe = _coerce_float(_extract_metric(payload, ("sharpe", "sharpe_ratio")))
    snap.win_rate_pct = _coerce_float(_extract_metric(payload, ("win_rate_pct",)))
    if snap.win_rate_pct is None:
        snap.win_rate_pct = _coerce_pct(_extract_metric(payload, ("win_rate", "hit_rate")))
    snap.trade_count = _coerce_int(_extract_metric(payload, ("trade_count", "total_trades", "trades", "n_trades")))
    snap.avg_holding_minutes = _coerce_float(
        _extract_metric(payload, ("avg_holding_minutes", "avg_hold_minutes", "avg_holding_mins"))
    )
    snap.buy_hold_return_pct = _coerce_float(
        _extract_metric(payload, ("buy_hold_return_pct", "buyhold_return_pct", "benchmark_return_pct"))
    )
    snap.excess_return_pct = _coerce_float(
        _extract_metric(payload, ("excess_return_pct", "alpha_return_pct", "strategy_minus_buy_hold_pct"))
    )
    strategy_label = _extract_metric(payload, ("strategy_label", "strategy", "profile"))
    if strategy_label is not None and str(strategy_label).strip():
        snap.strategy_label = str(strategy_label).strip()
    strategy_summary = _extract_metric(payload, ("strategy_summary",))
    if strategy_summary is not None and str(strategy_summary).strip():
        snap.strategy_summary = str(strategy_summary).strip()
    snap.symbol_contributions = _load_symbol_contributions(payload)

    if input_quality_path.exists():
        try:
            quality_payload = json.loads(input_quality_path.read_text(encoding="utf-8"))
        except Exception:
            quality_payload = {}
        if isinstance(quality_payload, dict):
            snap.quality_score = _coerce_float(quality_payload.get("quality_score"))
            snap.quality_status = str(quality_payload.get("quality_status") or "--").strip().lower() or "--"
            coverage = _coerce_float(quality_payload.get("candle_coverage_pct"))
            gaps = _coerce_int(quality_payload.get("gap_count"))
            no_next_open = _coerce_int(quality_payload.get("no_next_open_bucket_count"))
            dropped = _coerce_int(quality_payload.get("dropped_signal_count"))
            parts = []
            if coverage is not None:
                parts.append(f"coverage={coverage:.1f}%")
            if gaps is not None:
                parts.append(f"gaps={gaps}")
            if no_next_open is not None:
                parts.append(f"no_next_open={no_next_open}")
            if dropped is not None:
                parts.append(f"dropped={dropped}")
            snap.quality_summary = " | ".join(parts)

    if stability_path.exists():
        try:
            stability_payload = json.loads(stability_path.read_text(encoding="utf-8"))
        except Exception:
            stability_payload = {}
        if isinstance(stability_payload, dict):
            snap.stability_status = str(stability_payload.get("stability_status") or "--").strip().lower() or "--"
            snap.stability_summary = str(stability_payload.get("stability_summary") or "").strip()
            snap.stability_comparable_run_count = int(stability_payload.get("comparable_run_count") or 0)

    wf_payload = _extract_walk_forward_payload(base, payload)
    _apply_walk_forward_snapshot(
        snap,
        wf_payload,
        initial_equity=_extract_metric(payload, ("initial_equity",)),
    )

    if snap.trade_count is None and snap.recent_trades:
        snap.trade_count = len(snap.recent_trades)

    snap.available = bool(metrics_path.exists() or snap.equity_points or snap.recent_trades or bool(wf_payload))
    if snap.available and snap.status == "no backtest artifacts":
        snap.status = "ok"
    elif snap.available and snap.status == "no backtest artifacts yet":
        snap.status = "partial"

    return snap


def _build_header_line(now: str, view: str, status: str, svc: str, width: int) -> str:
    view_txt = _view_display_name(view)
    left = f"TradeCat TUI  |  {now}  |  页面={view_txt}  |  {status}"
    left_compact = f"TUI {now} 页={view_txt} {status}"
    svc_txt = (svc or "").strip()
    if not svc_txt:
        return _truncate(left, width)

    sep = "  |  "
    full = f"{left}{sep}{svc_txt}"
    if len(full) <= width:
        return full

    # Keep service status visible on narrow terminals; shrink left context first.
    left_budget = width - len(sep) - len(svc_txt)
    if left_budget >= 12:
        if len(left_compact) <= left_budget:
            return f"{left_compact}{sep}{svc_txt}"
        return f"{_truncate(left_compact, left_budget)}{sep}{svc_txt}"

    # Very narrow terminals: show compact service text only.
    return _truncate(svc_txt, width)


def run(
    db_path: str,
    refresh_s: float = 1.0,
    limit: int = 500,
    quotes: QuoteConfigs | None = None,
    micro_cfg: MicroConfig | None = None,
    start_view: str = "market_micro",
    watchlists_path: str = "",
    hot_reload: bool = False,
    hot_reload_poll_s: float = 1.0,
) -> None:
    sv = (start_view or "market_micro").strip().lower()
    # Back-compat: "quotes" means US quotes.
    if sv == "quotes":
        sv = "market_us"
    if sv in {"quotes_us"}:
        sv = "market_us"
    if sv in {"quotes_cn"}:
        sv = "market_cn"
    if sv in {"quotes_hk"}:
        sv = "market_hk"
    if sv in {"market_fund", "market_fund_cn", "quotes_fund_cn", "quotes_fund"}:
        sv = "market_fund_cn"
    if sv in {"quotes_metals", "market_crypto", "quotes_crypto"}:
        # Back-compat: old quote pages are folded into market_micro.
        sv = "market_micro"
    if sv in {"news", "market_news"}:
        sv = "market_news"
    if sv in {"backtest", "market_bt"}:
        sv = "market_backtest"
    if sv not in {
        "signals",
        "quotes_us",
        "quotes_hk",
        "quotes_cn",
        "quotes_metals",
        "market_us",
        "market_cn",
        "market_hk",
        "market_fund_cn",
        "market_micro",
        "market_news",
        "market_backtest",
    }:
        sv = "market_micro"
    micro = micro_cfg or MicroConfig()
    watcher = _build_hot_reload_watcher(bool(hot_reload), poll_s=hot_reload_poll_s)

    while True:
        try:
            curses.wrapper(_main, db_path, refresh_s, limit, quotes or QuoteConfigs(), micro, sv, watchlists_path, watcher)
            return
        except _HotReloadRequested:
            if watcher is None:
                return
            # Full process restart is required for Python source updates to take effect.
            # Re-entering curses.wrapper alone would keep old modules/functions in memory.
            restart_argv = [sys.executable, "-m", "src", *sys.argv[1:]]
            try:
                os.execv(sys.executable, restart_argv)
            except Exception:
                continue


def _main(
    stdscr,
    db_path: str,
    refresh_s: float,
    limit: int,
    quote_cfgs: QuoteConfigs,
    micro_cfg: MicroConfig,
    start_view: str,
    watchlists_path: str,
    hot_reload_watcher: _HotReloadWatcher | None = None,
) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)

    colors = _init_colors()
    filt = Filters()

    poll_us = QuotePoller(quote_cfgs.us)
    poll_hk = QuotePoller(quote_cfgs.hk)
    poll_cn = QuotePoller(quote_cfgs.cn)
    poll_fund_cn = QuotePoller(quote_cfgs.fund_cn)
    poll_crypto = QuotePoller(quote_cfgs.crypto)
    poll_metals = QuotePoller(quote_cfgs.metals)
    poll_us.start()
    poll_hk.start()
    poll_cn.start()
    poll_fund_cn.start()
    poll_crypto.start()
    poll_metals.start()

    news_poller: RssNewsPoller | None = None
    raw_news_feeds = (os.getenv("TUI_NEWS_RSS_FEEDS", "") or os.getenv("NEWS_RSS_FEEDS", "") or "").strip()
    if not raw_news_feeds:
        raw_news_feeds = _default_tui_news_rss_feeds_value()
    news_feeds = _parse_rss_feeds_value(raw_news_feeds)
    news_database_url = resolve_news_database_url(_REPO_ROOT)
    news_database_schema = resolve_news_database_schema(_REPO_ROOT)
    if news_feeds or news_database_url:
        try:
            news_refresh_s = float(os.getenv("TUI_NEWS_RSS_REFRESH_S", "2").strip() or "2")
        except Exception:
            news_refresh_s = 2.0
        try:
            news_timeout_s = float(os.getenv("TUI_NEWS_RSS_TIMEOUT_S", "5").strip() or "5")
        except Exception:
            news_timeout_s = 5.0
        try:
            news_max_items = int(os.getenv("TUI_NEWS_MAX_ITEMS", "300").strip() or "300")
        except Exception:
            news_max_items = 300
        try:
            news_db_window_h = int(os.getenv("TUI_NEWS_DB_WINDOW_HOURS", "72").strip() or "72")
        except Exception:
            news_db_window_h = 72
        try:
            news_db_timeout_s = float(os.getenv("TUI_NEWS_DB_TIMEOUT_S", "5").strip() or "5")
        except Exception:
            news_db_timeout_s = 5.0
        news_poller = RssNewsPoller(
            news_feeds,
            refresh_s=news_refresh_s,
            timeout_s=news_timeout_s,
            max_items=news_max_items,
            database_url=news_database_url,
            database_schema=news_database_schema,
            database_window_h=news_db_window_h,
            database_timeout_s=news_db_timeout_s,
        )
        news_poller.start()

    last_id = 0
    rows: list[SignalRow] = []
    rows_all: list[SignalRow] = []
    scroll = 0
    last_refresh = 0.0
    view = start_view  # "signals" or "quotes"
    qscroll: dict[str, int] = {
        "quotes_us": 0,
        "quotes_hk": 0,
        "quotes_cn": 0,
        "quotes_crypto": 0,
        "quotes_metals": 0,
    }
    master_panes: dict[str, MasterPaneState] = {
        "market_us": MasterPaneState(),
        "market_cn": MasterPaneState(),
        "market_hk": MasterPaneState(),
        "market_fund_cn": MasterPaneState(),
    }
    news_state = NewsPageState()
    seed = normalize_crypto_symbols(micro_cfg.symbol or "")
    micro_symbol_current = seed[0] if seed else "BTC_USDT"
    micro_engines: dict[str, MicroEngine] = {}
    micro_last_refresh: dict[str, float] = {}
    micro_errors: dict[str, str] = {}
    us_quote_curves: dict[str, deque[Candle]] = {}
    hk_quote_curves: dict[str, deque[Candle]] = {}
    cn_quote_curves: dict[str, deque[Candle]] = {}
    fund_cn_quote_curves: dict[str, deque[Candle]] = {}
    fund_cn_daily_curves: dict[str, deque[Candle]] = {}
    crypto_quote_curves: dict[str, deque[Candle]] = {}
    us_curve_seed_attempts: dict[str, float] = {}
    hk_curve_seed_attempts: dict[str, float] = {}
    cn_curve_seed_attempts: dict[str, float] = {}
    fund_cn_curve_seed_attempts: dict[str, float] = {}
    us_micro_engines: dict[str, MicroEngine] = {}
    hk_micro_engines: dict[str, MicroEngine] = {}
    cn_micro_engines: dict[str, MicroEngine] = {}
    fund_cn_micro_engines: dict[str, MicroEngine] = {}
    us_micro_errors: dict[str, str] = {}
    hk_micro_errors: dict[str, str] = {}
    cn_micro_errors: dict[str, str] = {}
    fund_cn_micro_errors: dict[str, str] = {}
    us_last_ingested_fetch: dict[str, float] = {}
    hk_last_ingested_fetch: dict[str, float] = {}
    cn_last_ingested_fetch: dict[str, float] = {}
    fund_cn_last_ingested_fetch: dict[str, float] = {}
    crypto_last_ingested_fetch: dict[str, float] = {}
    service_status = _collect_service_status()
    service_status_refresh_s = 1.0
    render_state = RenderState()
    runtime_state = RuntimeState()
    next_frame_at = time.time()
    pending_force_redraw = True

    micro_switch = DebounceSwitch()
    micro_switch_applied = 0
    master_switches: dict[str, DebounceSwitch] = {
        "market_us": DebounceSwitch(),
        "market_cn": DebounceSwitch(),
        "market_hk": DebounceSwitch(),
        "market_fund_cn": DebounceSwitch(),
    }
    master_switch_applied: dict[str, int] = {"market_us": 0, "market_cn": 0, "market_hk": 0, "market_fund_cn": 0}
    view_aliases = {
        "quotes_us": "market_us",
        "quotes_cn": "market_cn",
        "quotes_hk": "market_hk",
        "quotes_fund_cn": "market_fund_cn",
        "quotes_metals": "market_micro",
        "quotes_crypto": "market_micro",
        "market_crypto": "market_micro",
        "news": "market_news",
        "backtest": _BACKTEST_VIEW,
        "market_bt": _BACKTEST_VIEW,
    }

    def _canonical_view(v: str) -> str:
        return view_aliases.get(v, v)

    last_primary_view = _canonical_view(view)
    if last_primary_view not in _PRIMARY_MARKET_VIEWS:
        last_primary_view = "market_micro"
    backtest_parent_view = last_primary_view

    def _remember_primary_view(v: str) -> None:
        nonlocal last_primary_view
        canonical = _canonical_view(v)
        if canonical in _PRIMARY_MARKET_VIEWS:
            last_primary_view = canonical

    def _is_master_view(v: str) -> bool:
        return v in master_panes

    def _master_has_signal_panel(v: str) -> bool:
        # Market pages are unified as quad layout; tab keeps cycling pages.
        return False

    def _next_view(v: str) -> str:
        cur = _canonical_view(v)
        if cur not in _PRIMARY_MARKET_VIEWS:
            cur = backtest_parent_view if backtest_parent_view in _PRIMARY_MARKET_VIEWS else last_primary_view
        try:
            idx = _PRIMARY_MARKET_VIEWS.index(cur)
        except ValueError:
            return "market_micro"
        return _PRIMARY_MARKET_VIEWS[(idx + 1) % len(_PRIMARY_MARKET_VIEWS)]

    def _master_symbol_count(v: str) -> int:
        if v == "market_us":
            return len(quote_cfgs.us.symbols)
        if v == "market_cn":
            return len(quote_cfgs.cn.symbols)
        if v == "market_hk":
            return len(quote_cfgs.hk.symbols)
        if v == "market_fund_cn":
            return len(quote_cfgs.fund_cn.symbols)
        return 0

    def _cycle_master_symbol(v: str, delta: int) -> bool:
        pane = master_panes.get(v)
        count = _master_symbol_count(v)
        if pane is None or count <= 0:
            return False
        old_selected = pane.selected
        pane.selected = (pane.selected + int(delta)) % count
        pane.right_scroll = 0
        changed = pane.selected != old_selected
        if changed and v in master_switches:
            master_switches[v].bump(time.time(), _SWITCH_DEBOUNCE_S)
        return changed

    def _news_source_options() -> tuple[str, ...]:
        all_items: list[NewsItem] = []
        if news_poller is not None:
            all_items = list(news_poller.snapshot().items)
        return _news_source_filter_options(all_items)

    def _news_counts(now_ts: float | None = None) -> tuple[int, int]:
        ts = float(time.time() if now_ts is None else now_ts)
        category = _NEWS_CATEGORIES[min(max(0, news_state.category_idx), len(_NEWS_CATEGORIES) - 1)]
        window_h = _NEWS_WINDOWS_H[min(max(0, news_state.window_idx), len(_NEWS_WINDOWS_H) - 1)]
        source_options = _news_source_options()
        source_filter = source_options[min(max(0, news_state.source_idx), len(source_options) - 1)] if source_options else _NEWS_SOURCE_FILTER_ALL
        all_items: list[NewsItem] = []
        if news_poller is not None:
            all_items = list(news_poller.snapshot().items)
        items = _filter_news_items(
            all_items,
            now_ts=ts,
            category=category,
            window_h=window_h,
            search_query=news_state.search_query,
            source_filter=source_filter,
        )
        events = _build_news_events(items)
        return 0, len(events)

    def _ensure_micro_engine(symbol: str) -> MicroEngine:
        sym = (symbol or "").strip().upper() or "BTC_USDT"
        engine = micro_engines.get(sym)
        if engine is None:
            engine = MicroEngine(
                MicroConfig(
                    symbol=sym,
                    interval_s=micro_cfg.interval_s,
                    window=micro_cfg.window,
                    flow_rows=micro_cfg.flow_rows,
                    refresh_s=micro_cfg.refresh_s,
                )
            )
            micro_engines[sym] = engine
        return engine

    def _ensure_us_micro_engine(symbol: str) -> MicroEngine:
        sym = (symbol or "").strip().upper()
        if not sym:
            sym = "NVDA"
        engine = us_micro_engines.get(sym)
        if engine is None:
            engine = MicroEngine(
                MicroConfig(
                    symbol=sym,
                    interval_s=5,
                    window=micro_cfg.window,
                    flow_rows=micro_cfg.flow_rows,
                    refresh_s=quote_cfgs.us.refresh_s,
                )
            )
            us_micro_engines[sym] = engine
        return engine

    def _ensure_cn_micro_engine(symbol: str) -> MicroEngine:
        sym = _normalize_cn_symbol(symbol)
        if not sym:
            sym = "SH600519"
        engine = cn_micro_engines.get(sym)
        if engine is None:
            engine = MicroEngine(
                MicroConfig(
                    symbol=sym,
                    interval_s=5,
                    window=micro_cfg.window,
                    flow_rows=micro_cfg.flow_rows,
                    refresh_s=quote_cfgs.cn.refresh_s,
                )
            )
            cn_micro_engines[sym] = engine
        return engine

    def _ensure_hk_micro_engine(symbol: str) -> MicroEngine:
        sym = _normalize_hk_symbol(symbol)
        if not sym:
            sym = "00700"
        engine = hk_micro_engines.get(sym)
        if engine is None:
            engine = MicroEngine(
                MicroConfig(
                    symbol=sym,
                    interval_s=5,
                    window=micro_cfg.window,
                    flow_rows=micro_cfg.flow_rows,
                    refresh_s=quote_cfgs.hk.refresh_s,
                )
            )
            hk_micro_engines[sym] = engine
        return engine

    def _ensure_fund_cn_micro_engine(symbol: str) -> MicroEngine:
        sym = _normalize_cn_fund_symbol(symbol)
        if not sym:
            sym = "SH510300"
        engine = fund_cn_micro_engines.get(sym)
        if engine is None:
            engine = MicroEngine(
                MicroConfig(
                    symbol=sym,
                    interval_s=5,
                    window=micro_cfg.window,
                    flow_rows=micro_cfg.flow_rows,
                    refresh_s=quote_cfgs.fund_cn.refresh_s,
                )
            )
            fund_cn_micro_engines[sym] = engine
        return engine

    def _micro_watch_symbols() -> list[str]:
        syms = [s.strip().upper() for s in (quote_cfgs.crypto.symbols or []) if (s or "").strip()]
        cur = (micro_symbol_current or "").strip().upper()
        if cur and cur not in syms:
            syms.insert(0, cur)
        return syms

    def _switch_micro_symbol(delta: int) -> bool:
        nonlocal micro_symbol_current
        syms = _micro_watch_symbols()
        if not syms:
            return False

        cur = (micro_symbol_current or "").strip().upper()
        try:
            idx = syms.index(cur)
        except ValueError:
            idx = 0

        target_idx = max(0, min(len(syms) - 1, idx + int(delta)))
        target = syms[target_idx]
        if not target:
            return False

        changed = target != cur
        micro_symbol_current = target
        _ensure_micro_engine(micro_symbol_current)
        if changed:
            micro_switch.bump(time.time(), _SWITCH_DEBOUNCE_S)
        return changed

    _ensure_micro_engine(micro_symbol_current)

    def _persist_watchlists() -> None:
        if not watchlists_path:
            return
        wl = Watchlists(
            us=normalize_us_symbols(",".join(quote_cfgs.us.symbols)),
            hk=normalize_hk_symbols(",".join(quote_cfgs.hk.symbols)),
            cn=normalize_cn_symbols(",".join(quote_cfgs.cn.symbols)),
            fund_cn=normalize_cn_fund_symbols(",".join(quote_cfgs.fund_cn.symbols)),
            crypto=normalize_crypto_symbols(",".join(quote_cfgs.crypto.symbols)),
            metals=normalize_metals_symbols(",".join(quote_cfgs.metals.symbols)),
        )
        try:
            save_watchlists(watchlists_path, wl)
        except Exception:
            # Persistence should never crash the UI.
            pass

    def _reload_dynamic_fund_universe(top_n: int = 35) -> bool:
        dynamic = load_dynamic_auto_driving_symbols(_REPO_ROOT, top_n=top_n)
        if not dynamic:
            return False
        normalized = normalize_cn_fund_symbols(",".join(dynamic))
        if not normalized:
            return False
        if normalized == [s.strip().upper() for s in (quote_cfgs.fund_cn.symbols or []) if (s or "").strip()]:
            return False

        quote_cfgs.fund_cn.symbols = list(normalized)
        poll_fund_cn.set_symbols(quote_cfgs.fund_cn.symbols)
        pane = master_panes.get("market_fund_cn")
        if pane is not None:
            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.fund_cn.symbols) - 1))
            pane.left_scroll = min(max(0, pane.left_scroll), max(0, len(quote_cfgs.fund_cn.symbols) - 1))
            pane.right_scroll = 0
        master_switches["market_fund_cn"].bump(time.time(), _SWITCH_DEBOUNCE_S)
        return True

    def _refresh_all_data() -> None:
        nonlocal last_refresh
        last_refresh = 0.0
        poll_us.request_refresh()
        poll_hk.request_refresh()
        poll_cn.request_refresh()
        poll_fund_cn.request_refresh()
        poll_crypto.request_refresh()
        poll_metals.request_refresh()
        us_curve_seed_attempts.clear()
        hk_curve_seed_attempts.clear()
        cn_curve_seed_attempts.clear()
        fund_cn_curve_seed_attempts.clear()

    def _prompt(prompt: str, max_len: int = 64) -> str:
        """
        Prompt on the last line.

        Keys:
        - Enter: confirm
        - ESC: cancel (returns empty string)
        - Backspace: edit
        """
        h, w = stdscr.getmaxyx()
        y = h - 1
        x0 = 0
        # Keep the hint short; it will be truncated if terminal is narrow.
        hint = f"{prompt} (Enter=OK, Esc=Cancel) "
        buf: list[str] = []

        stdscr.nodelay(False)
        try:
            while True:
                stdscr.move(y, x0)
                stdscr.clrtoeol()
                _safe_addstr(stdscr, y, x0, _truncate(hint + "".join(buf), max(0, w - 1)))
                stdscr.refresh()

                try:
                    ch = stdscr.getch()
                except KeyboardInterrupt:
                    return ""

                if ch in (27,):  # ESC
                    return ""
                if ch in (10, 13):  # Enter
                    return "".join(buf).strip()
                if ch in (curses.KEY_BACKSPACE, 127, 8):
                    if buf:
                        buf.pop()
                    continue
                if ch == -1:
                    continue

                # Only accept a conservative ASCII subset to avoid weird control chars in watchlists.
                if 32 <= ch <= 126:
                    c = chr(ch)
                    if len(buf) < max_len:
                        buf.append(c)
        finally:
            stdscr.nodelay(True)

    def _pane_signature(pane: MasterPaneState) -> tuple[int, int, int, str]:
        return (
            int(pane.selected),
            int(pane.left_scroll),
            int(pane.right_scroll),
            (pane.focus or "left").strip(),
        )

    def _ui_signature() -> tuple:
        return (
            view,
            int(scroll),
            int(qscroll.get(view, 0)),
            bool(filt.paused),
            tuple(sorted(filt.sources)),
            tuple(sorted(filt.directions)),
            tuple((name, _pane_signature(pane)) for name, pane in sorted(master_panes.items())),
            tuple(s.strip().upper() for s in (quote_cfgs.us.symbols or [])),
            tuple(s.strip().upper() for s in (quote_cfgs.hk.symbols or [])),
            tuple(s.strip().upper() for s in (quote_cfgs.cn.symbols or [])),
            tuple(s.strip().upper() for s in (quote_cfgs.fund_cn.symbols or [])),
            tuple(s.strip().upper() for s in (quote_cfgs.crypto.symbols or [])),
            tuple(s.strip().upper() for s in (quote_cfgs.metals.symbols or [])),
            (micro_symbol_current or "").strip().upper(),
            runtime_state.fund_domain.selected_key,
            int(runtime_state.fund_domain.selected_idx),
            (news_state.focus or "middle").strip(),
            int(news_state.watch_selected),
            int(news_state.news_selected),
            int(news_state.news_scroll),
            int(news_state.category_idx),
            int(news_state.source_idx),
            int(news_state.window_idx),
            (news_state.search_query or "").strip().lower(),
            bool(news_state.watch_filter_locked),
        )

    def _maybe_apply_switch_debounce(now_ts: float) -> bool:
        nonlocal micro_switch_applied, pending_force_redraw

        changed = False

        if micro_switch.version != micro_switch_applied and now_ts >= micro_switch.ready_at:
            micro_switch_applied = micro_switch.version
            current = (micro_symbol_current or "").strip().upper()
            if current:
                micro_last_refresh[current] = 0.0
            changed = True

        for mv in ("market_us", "market_cn", "market_hk", "market_fund_cn"):
            sw = master_switches[mv]
            if sw.version == master_switch_applied[mv] or now_ts < sw.ready_at:
                continue

            master_switch_applied[mv] = sw.version
            pane = master_panes.get(mv)
            if pane is None:
                continue

            if mv == "market_us":
                syms = [s.strip().upper() for s in (quote_cfgs.us.symbols or []) if (s or "").strip()]
                if not syms:
                    continue
                pane.selected = min(max(0, pane.selected), len(syms) - 1)
                us_curve_seed_attempts.pop(syms[pane.selected], None)
                changed = True
                continue

            if mv == "market_hk":
                syms_hk = [_normalize_hk_symbol(s) for s in (quote_cfgs.hk.symbols or []) if (s or "").strip()]
                syms_hk = [s for s in syms_hk if s]
                if not syms_hk:
                    continue
                pane.selected = min(max(0, pane.selected), len(syms_hk) - 1)
                hk_curve_seed_attempts.pop(syms_hk[pane.selected], None)
                changed = True
                continue

            source_symbols = quote_cfgs.cn.symbols if mv == "market_cn" else quote_cfgs.fund_cn.symbols
            norm_fn = _normalize_cn_symbol if mv == "market_cn" else _normalize_cn_fund_symbol
            syms_cn = [norm_fn(s) for s in (source_symbols or []) if (s or "").strip()]
            syms_cn = [s for s in syms_cn if s]
            if not syms_cn:
                continue
            pane.selected = min(max(0, pane.selected), len(syms_cn) - 1)
            if mv == "market_cn":
                cn_curve_seed_attempts.pop(syms_cn[pane.selected], None)
            else:
                fund_cn_curve_seed_attempts.pop(syms_cn[pane.selected], None)
            changed = True

        if changed:
            pending_force_redraw = True
        return changed

    try:
        while True:
            now = time.time()
            dirty = DirtyFlags(forced=pending_force_redraw)
            pending_force_redraw = False
            if hot_reload_watcher is not None and hot_reload_watcher.should_reload(now):
                raise _HotReloadRequested()
            if (now - service_status.checked_at) >= service_status_refresh_s:
                new_service_status = _collect_service_status(now)
                if _service_status_signature(new_service_status) != _service_status_signature(service_status):
                    dirty.services = True
                service_status = new_service_status

            if _maybe_apply_switch_debounce(now):
                dirty.ui = True

            if not filt.paused and (now - last_refresh) >= refresh_s:
                ok, _ = probe(db_path)
                if ok:
                    new_rows = fetch_recent(
                        db_path,
                        limit=limit,
                        min_id=None,
                        sources=sorted(filt.sources),
                        directions=sorted(filt.directions),
                    )
                    # Also keep an unfiltered view for cross-page correlation (quotes <-> signals),
                    # so the quotes pages can still show "latest signal" even if the user hides a direction/source.
                    rows_all = fetch_recent(
                        db_path,
                        limit=max(200, int(limit)),
                        min_id=None,
                        sources=["pg", "sqlite"],
                        directions=["BUY", "SELL", "ALERT"],
                    )
                    # fetch_recent returns DESC order (newest first)
                    rows = new_rows
                    if rows:
                        last_id = max(last_id, rows[0].id)
                else:
                    rows = []
                    rows_all = []
                last_refresh = now

            quote_state_us = poll_us.snapshot()
            quote_state_hk = poll_hk.snapshot()
            quote_state_cn = poll_cn.snapshot()
            quote_state_fund_cn = poll_fund_cn.snapshot()
            quote_state_crypto = poll_crypto.snapshot()
            quote_state_metals = poll_metals.snapshot()

            active_us_symbols = {s.strip().upper() for s in (quote_cfgs.us.symbols or []) if (s or "").strip()}
            for stale_symbol in list(us_quote_curves.keys()):
                if stale_symbol not in active_us_symbols:
                    us_quote_curves.pop(stale_symbol, None)
                    us_curve_seed_attempts.pop(stale_symbol, None)
            for stale_symbol in list(us_micro_engines.keys()):
                if stale_symbol not in active_us_symbols:
                    us_micro_engines.pop(stale_symbol, None)
                    us_micro_errors.pop(stale_symbol, None)
                    us_last_ingested_fetch.pop(stale_symbol, None)

            for sym in active_us_symbols:
                st = quote_state_us.entries.get(sym)
                if st and st.quote is not None:
                    curve_ts = _curve_update_ts(st.quote, st.last_fetch_at, now)
                    _update_quote_curve(us_quote_curves, sym, st.quote, curve_ts, interval_s=5, max_points=240)
                    us_engine = _ensure_us_micro_engine(sym)
                    last_seen = us_last_ingested_fetch.get(sym, 0.0)
                    if st.last_fetch_at > last_seen:
                        us_engine.ingest_quote(st.quote, fetched_at=st.last_fetch_at)
                        us_last_ingested_fetch[sym] = st.last_fetch_at
                    us_micro_errors[sym] = (st.last_error or "").strip()
                else:
                    us_micro_errors[sym] = "no data"

            active_hk_symbols = {_normalize_hk_symbol(s) for s in (quote_cfgs.hk.symbols or []) if (s or "").strip()}
            active_hk_symbols.discard("")
            for stale_symbol in list(hk_quote_curves.keys()):
                if stale_symbol not in active_hk_symbols:
                    hk_quote_curves.pop(stale_symbol, None)
                    hk_curve_seed_attempts.pop(stale_symbol, None)
            for stale_symbol in list(hk_micro_engines.keys()):
                if stale_symbol not in active_hk_symbols:
                    hk_micro_engines.pop(stale_symbol, None)
                    hk_micro_errors.pop(stale_symbol, None)
                    hk_last_ingested_fetch.pop(stale_symbol, None)

            for sym in active_hk_symbols:
                st = quote_state_hk.entries.get(sym)
                if st and st.quote is not None:
                    curve_ts = _curve_update_ts(st.quote, st.last_fetch_at, now)
                    _update_quote_curve(hk_quote_curves, sym, st.quote, curve_ts, interval_s=5, max_points=240)
                    hk_engine = _ensure_hk_micro_engine(sym)
                    last_seen = hk_last_ingested_fetch.get(sym, 0.0)
                    if st.last_fetch_at > last_seen:
                        hk_engine.ingest_quote(st.quote, fetched_at=st.last_fetch_at)
                        hk_last_ingested_fetch[sym] = st.last_fetch_at
                    hk_micro_errors[sym] = (st.last_error or "").strip()
                else:
                    hk_micro_errors[sym] = "no data"

            active_cn_symbols = {_normalize_cn_symbol(s) for s in (quote_cfgs.cn.symbols or []) if (s or "").strip()}
            active_cn_symbols.discard("")
            for stale_symbol in list(cn_quote_curves.keys()):
                if stale_symbol not in active_cn_symbols:
                    cn_quote_curves.pop(stale_symbol, None)
                    cn_curve_seed_attempts.pop(stale_symbol, None)
            for stale_symbol in list(cn_micro_engines.keys()):
                if stale_symbol not in active_cn_symbols:
                    cn_micro_engines.pop(stale_symbol, None)
                    cn_micro_errors.pop(stale_symbol, None)
                    cn_last_ingested_fetch.pop(stale_symbol, None)

            for sym in active_cn_symbols:
                st = quote_state_cn.entries.get(sym)
                if st and st.quote is not None:
                    curve_ts = _curve_update_ts(st.quote, st.last_fetch_at, now)
                    _update_quote_curve(cn_quote_curves, sym, st.quote, curve_ts, interval_s=5, max_points=240)
                    cn_engine = _ensure_cn_micro_engine(sym)
                    last_seen = cn_last_ingested_fetch.get(sym, 0.0)
                    if st.last_fetch_at > last_seen:
                        cn_engine.ingest_quote(st.quote, fetched_at=st.last_fetch_at)
                        cn_last_ingested_fetch[sym] = st.last_fetch_at
                    cn_micro_errors[sym] = (st.last_error or "").strip()
                else:
                    cn_micro_errors[sym] = "no data"

            active_fund_cn_symbols = {
                _normalize_cn_fund_symbol(s) for s in (quote_cfgs.fund_cn.symbols or []) if (s or "").strip()
            }
            active_fund_cn_symbols.discard("")
            for stale_symbol in list(fund_cn_quote_curves.keys()):
                if stale_symbol not in active_fund_cn_symbols:
                    fund_cn_quote_curves.pop(stale_symbol, None)
            for stale_symbol in list(fund_cn_daily_curves.keys()):
                if stale_symbol not in active_fund_cn_symbols:
                    fund_cn_daily_curves.pop(stale_symbol, None)
                    fund_cn_curve_seed_attempts.pop(stale_symbol, None)
            for stale_symbol in list(fund_cn_curve_seed_attempts.keys()):
                if stale_symbol not in active_fund_cn_symbols:
                    fund_cn_curve_seed_attempts.pop(stale_symbol, None)
            for stale_symbol in list(fund_cn_micro_engines.keys()):
                if stale_symbol not in active_fund_cn_symbols:
                    fund_cn_micro_engines.pop(stale_symbol, None)
                    fund_cn_micro_errors.pop(stale_symbol, None)
                    fund_cn_last_ingested_fetch.pop(stale_symbol, None)

            for sym in active_fund_cn_symbols:
                st = quote_state_fund_cn.entries.get(sym)
                if st and st.quote is not None:
                    curve_ts = _curve_update_ts(st.quote, st.last_fetch_at, now)
                    _update_quote_curve(fund_cn_quote_curves, sym, st.quote, curve_ts, interval_s=5, max_points=240)
                    cn_engine = _ensure_fund_cn_micro_engine(sym)
                    last_seen = fund_cn_last_ingested_fetch.get(sym, 0.0)
                    if st.last_fetch_at > last_seen:
                        quote_for_engine = st.quote
                        if (quote_for_engine.symbol or "").strip().upper() != sym:
                            # Keep engine key stable for mixed fund symbols (exchange/off-market).
                            quote_for_engine = Quote(
                                symbol=sym,
                                name=quote_for_engine.name,
                                price=quote_for_engine.price,
                                prev_close=quote_for_engine.prev_close,
                                open=quote_for_engine.open,
                                high=quote_for_engine.high,
                                low=quote_for_engine.low,
                                currency=quote_for_engine.currency,
                                volume=quote_for_engine.volume,
                                amount=quote_for_engine.amount,
                                ts=quote_for_engine.ts,
                                source=quote_for_engine.source,
                            )
                        cn_engine.ingest_quote(quote_for_engine, fetched_at=st.last_fetch_at)
                        fund_cn_last_ingested_fetch[sym] = st.last_fetch_at
                    fund_cn_micro_errors[sym] = (st.last_error or "").strip()
                else:
                    fund_cn_micro_errors[sym] = "no data"

            _maybe_seed_closed_curve_from_history(
                curves=us_quote_curves,
                quote_state=quote_state_us,
                symbols=active_us_symbols,
                market=quote_cfgs.us.market,
                provider=quote_cfgs.us.provider,
                attempts=us_curve_seed_attempts,
                now_ts=now,
                max_points=240,
            )
            _maybe_seed_closed_curve_from_history(
                curves=cn_quote_curves,
                quote_state=quote_state_cn,
                symbols=active_cn_symbols,
                market=quote_cfgs.cn.market,
                provider=quote_cfgs.cn.provider,
                attempts=cn_curve_seed_attempts,
                now_ts=now,
                max_points=240,
            )
            _maybe_seed_closed_curve_from_history(
                curves=hk_quote_curves,
                quote_state=quote_state_hk,
                symbols=active_hk_symbols,
                market=quote_cfgs.hk.market,
                provider=quote_cfgs.hk.provider,
                attempts=hk_curve_seed_attempts,
                now_ts=now,
                max_points=240,
            )
            fund_symbols_order = [_normalize_cn_fund_symbol(s) for s in (quote_cfgs.fund_cn.symbols or []) if (s or "").strip()]
            fund_symbols_order = [s for s in fund_symbols_order if s]
            fund_pane = master_panes.get("market_fund_cn")
            selected_fund_symbol = ""
            if fund_symbols_order and fund_pane is not None:
                fund_pane.selected = min(max(0, fund_pane.selected), len(fund_symbols_order) - 1)
                selected_fund_symbol = fund_symbols_order[fund_pane.selected]
            if selected_fund_symbol and selected_fund_symbol.startswith(("SH", "SZ")):
                _maybe_seed_fund_curve_from_daily_history(
                    curves=fund_cn_daily_curves,
                    symbols={selected_fund_symbol},
                    market=quote_cfgs.fund_cn.market,
                    provider=quote_cfgs.fund_cn.provider,
                    attempts=fund_cn_curve_seed_attempts,
                    now_ts=now,
                    lookback_days=_FUND_CN_CURVE_DAYS,
                )

            micro_symbols = _micro_watch_symbols()
            active_crypto_symbols = set(micro_symbols)
            for stale_symbol in list(crypto_quote_curves.keys()):
                if stale_symbol not in active_crypto_symbols:
                    crypto_quote_curves.pop(stale_symbol, None)
            for stale_symbol in list(micro_engines.keys()):
                if stale_symbol not in active_crypto_symbols:
                    micro_engines.pop(stale_symbol, None)
                    micro_errors.pop(stale_symbol, None)
                    micro_last_refresh.pop(stale_symbol, None)
                    crypto_last_ingested_fetch.pop(stale_symbol, None)

            if not filt.paused:
                for sym in sorted(active_crypto_symbols):
                    entry = quote_state_crypto.entries.get(sym)
                    if entry and entry.quote is not None:
                        _update_quote_curve(crypto_quote_curves, sym, entry.quote, entry.last_fetch_at, interval_s=5, max_points=240)
                        micro_engine = _ensure_micro_engine(sym)
                        last_seen = crypto_last_ingested_fetch.get(sym, 0.0)
                        if entry.last_fetch_at > last_seen:
                            micro_engine.ingest_quote(entry.quote, fetched_at=entry.last_fetch_at)
                            crypto_last_ingested_fetch[sym] = entry.last_fetch_at
                        micro_errors[sym] = (entry.last_error or "").strip()
                        micro_last_refresh[sym] = now
                        continue

                    current_micro_symbol = (micro_symbol_current or "").strip().upper()
                    if sym != current_micro_symbol:
                        if entry is None:
                            micro_errors[sym] = "no data"
                        else:
                            micro_errors[sym] = (entry.last_error or "").strip() or "no data"
                        continue

                    if micro_switch.version != micro_switch_applied and now < micro_switch.ready_at:
                        continue

                    last_micro_refresh = micro_last_refresh.get(sym, 0.0)
                    if (now - last_micro_refresh) < micro_cfg.refresh_s:
                        continue

                    switch_version = micro_switch.version
                    quote = fetch_quote(
                        quote_cfgs.crypto.provider,
                        quote_cfgs.crypto.market,
                        sym,
                        timeout_s=quote_cfgs.crypto.timeout_s,
                    )
                    if switch_version != micro_switch.version or sym != (micro_symbol_current or "").strip().upper():
                        continue
                    if quote is None:
                        micro_errors[sym] = "no quote"
                    else:
                        ts_now = time.time()
                        _update_quote_curve(crypto_quote_curves, sym, quote, ts_now, interval_s=5, max_points=240)
                        micro_engine = _ensure_micro_engine(sym)
                        micro_engine.ingest_quote(quote, fetched_at=ts_now)
                        crypto_last_ingested_fetch[sym] = ts_now
                        micro_errors[sym] = ""
                    micro_last_refresh[sym] = now

            cur_micro_symbol = micro_symbol_current
            cur_micro_engine = _ensure_micro_engine(cur_micro_symbol)

            latest_sig_map_crypto = _build_latest_signal_map(rows_all)
            us_curve_map = _snapshot_quote_curves(us_quote_curves)
            hk_curve_map = _snapshot_quote_curves(hk_quote_curves)
            cn_curve_map = _snapshot_quote_curves(cn_quote_curves)
            fund_cn_curve_map = _snapshot_quote_curves(fund_cn_quote_curves)
            fund_cn_daily_curve_map = _snapshot_quote_curves(fund_cn_daily_curves)
            crypto_curve_map = _snapshot_quote_curves(crypto_quote_curves)
            us_micro_snapshots = {
                sym: engine.snapshot(error=us_micro_errors.get(sym, "")) for sym, engine in us_micro_engines.items()
            }
            hk_micro_snapshots = {
                sym: engine.snapshot(error=hk_micro_errors.get(sym, "")) for sym, engine in hk_micro_engines.items()
            }
            cn_micro_snapshots = {
                sym: engine.snapshot(error=cn_micro_errors.get(sym, "")) for sym, engine in cn_micro_engines.items()
            }
            fund_cn_micro_snapshots = {
                sym: engine.snapshot(error=fund_cn_micro_errors.get(sym, ""))
                for sym, engine in fund_cn_micro_engines.items()
            }
            micro_snapshot = cur_micro_engine.snapshot(error=micro_errors.get(cur_micro_symbol, ""))

            db_sig = (
                _signal_rows_signature(rows),
                _signal_rows_signature(rows_all),
                int(last_id),
            )
            if db_sig != render_state.db_sig:
                dirty.db = True
                render_state.db_sig = db_sig

            quote_sig = (
                _quote_book_signature(quote_state_us),
                _quote_book_signature(quote_state_hk),
                _quote_book_signature(quote_state_cn),
                _quote_book_signature(quote_state_fund_cn),
                _quote_book_signature(quote_state_crypto),
                _quote_book_signature(quote_state_metals),
            )
            if quote_sig != render_state.quote_sig:
                dirty.quotes = True
                render_state.quote_sig = quote_sig

            micro_sig = (
                _curve_map_signature(us_curve_map),
                _curve_map_signature(hk_curve_map),
                _curve_map_signature(cn_curve_map),
                _curve_map_signature(fund_cn_curve_map),
                _curve_map_signature(fund_cn_daily_curve_map),
                _curve_map_signature(crypto_curve_map),
                tuple((sym, _micro_snapshot_signature(ss)) for sym, ss in sorted(us_micro_snapshots.items())),
                tuple((sym, _micro_snapshot_signature(ss)) for sym, ss in sorted(hk_micro_snapshots.items())),
                tuple((sym, _micro_snapshot_signature(ss)) for sym, ss in sorted(cn_micro_snapshots.items())),
                tuple((sym, _micro_snapshot_signature(ss)) for sym, ss in sorted(fund_cn_micro_snapshots.items())),
                _micro_snapshot_signature(micro_snapshot),
                tuple(micro_symbols),
            )
            if micro_sig != render_state.micro_sig:
                dirty.micro = True
                render_state.micro_sig = micro_sig

            ui_sig = _ui_signature()
            if ui_sig != render_state.ui_sig:
                dirty.ui = True
                render_state.ui_sig = ui_sig

            service_sig = _service_status_signature(service_status)
            if service_sig != render_state.service_sig:
                dirty.services = True
                render_state.service_sig = service_sig

            h, w = stdscr.getmaxyx()
            layout_sig = (int(h), int(w))
            if layout_sig != render_state.layout_sig:
                dirty.layout = True
                render_state.layout_sig = layout_sig

            idle_due = render_state.last_draw_at <= 0.0 or (now - render_state.last_draw_at) >= _RENDER_IDLE_REDRAW_S
            frame_due = now >= next_frame_at
            header_only_due = (
                dirty.services
                and not (dirty.db or dirty.quotes or dirty.micro or dirty.ui or dirty.layout or dirty.forced)
            )

            if frame_due and (dirty.any() or idle_due):
                if header_only_due and not idle_due:
                    _, w = stdscr.getmaxyx()
                    _draw_header(stdscr, colors, filt, refresh_s, view, service_status, w)
                    stdscr.noutrefresh()
                    curses.doupdate()
                else:
                    news_snapshot: NewsFeedSnapshot | None = None
                    if view == "market_news":
                        if news_poller is not None:
                            news_snapshot = news_poller.snapshot()
                        else:
                            news_snapshot = NewsFeedSnapshot(
                                mode="RSS",
                                items=(),
                                feeds=(),
                                last_ok_at=0.0,
                                latest_item_at=0.0,
                                refresh_s=0.0,
                                last_error="news feeds not configured",
                            )
                    _draw(
                        stdscr,
                        db_path,
                        rows,
                        rows_all,
                        filt,
                        scroll,
                        colors,
                        refresh_s,
                        last_id,
                        quote_cfgs,
                        quote_state_us,
                        quote_state_hk,
                        quote_state_cn,
                        quote_state_fund_cn,
                        quote_state_crypto,
                        quote_state_metals,
                        latest_sig_map_crypto,
                        us_curve_map,
                        hk_curve_map,
                        cn_curve_map,
                        fund_cn_curve_map,
                        fund_cn_daily_curve_map,
                        crypto_curve_map,
                        us_micro_snapshots,
                        hk_micro_snapshots,
                        cn_micro_snapshots,
                        fund_cn_micro_snapshots,
                        micro_snapshot,
                        micro_symbols,
                        service_status,
                        view,
                        qscroll.get(view, 0),
                        master_panes.get(view),
                        runtime_state,
                        news_state,
                        news_snapshot,
                    )
                render_state.last_draw_at = now
                next_frame_at = now + _RENDER_FRAME_INTERVAL_S
            key = stdscr.getch()
            if key == -1:
                sleep_for = min(0.03, max(0.0, next_frame_at - time.time()))
                if sleep_for > 0:
                    wait_seconds(sleep_for)
                continue

            pending_force_redraw = True

            if key in (ord("q"), 27):  # q or ESC
                return
            if key in (ord(" "),):
                filt.paused = not filt.paused
                poll_us.set_paused(filt.paused)
                poll_hk.set_paused(filt.paused)
                poll_cn.set_paused(filt.paused)
                poll_fund_cn.set_paused(filt.paused)
                poll_crypto.set_paused(filt.paused)
                poll_metals.set_paused(filt.paused)
                if news_poller is not None:
                    news_poller.set_paused(filt.paused)
            elif key == ord("\t"):
                if view == "market_news":
                    focus_order = ("middle", "right")
                    try:
                        idx = focus_order.index(news_state.focus)
                    except ValueError:
                        idx = 0
                    news_state.focus = focus_order[(idx + 1) % len(focus_order)]
                elif _is_master_view(view) and _master_has_signal_panel(view):
                    pane = master_panes[view]
                    pane.focus = "right" if pane.focus == "left" else "left"
                else:
                    view = _next_view(view)
                    _remember_primary_view(view)
            elif key == ord("t"):
                view = _next_view(view)
                _remember_primary_view(view)
            elif key == ord("1"):
                view = "market_us"
                _remember_primary_view(view)
            elif key == ord("2"):
                view = "market_cn"
                _remember_primary_view(view)
            elif key == ord("3"):
                view = "market_micro"
                _remember_primary_view(view)
            elif key == ord("4"):
                if view == _BACKTEST_VIEW:
                    view = backtest_parent_view if backtest_parent_view in _PRIMARY_MARKET_VIEWS else last_primary_view
                    if view not in _PRIMARY_MARKET_VIEWS:
                        view = "market_micro"
                    _remember_primary_view(view)
                elif view == "market_micro":
                    canonical = _canonical_view(view)
                    if canonical in _PRIMARY_MARKET_VIEWS:
                        backtest_parent_view = canonical
                    else:
                        backtest_parent_view = last_primary_view
                    view = _BACKTEST_VIEW
            elif key == ord("5"):
                view = "market_fund_cn"
                _remember_primary_view(view)
            elif key == ord("6"):
                view = "market_hk"
                _remember_primary_view(view)
            elif key == ord("7"):
                view = "market_news"
                _remember_primary_view(view)
            elif key == ord("["):
                if view == "market_micro":
                    _switch_micro_symbol(-1)
                elif view in {"market_us", "market_cn", "market_hk", "market_fund_cn"}:
                    _cycle_master_symbol(view, -1)
            elif key == ord("]"):
                if view == "market_micro":
                    _switch_micro_symbol(1)
                elif view in {"market_us", "market_cn", "market_hk", "market_fund_cn"}:
                    _cycle_master_symbol(view, 1)
            elif key == ord(","):  # 切换领域（上一个）
                if view == "market_fund_cn":
                    selected_key = runtime_state.fund_domain.cycle(-1)
                    if selected_key:
                        # 更新候选池 symbols
                        domain_profile = get_etf_domain_profile(selected_key)
                        new_symbols = [s.upper() for s in domain_profile.symbols]
                        quote_cfgs.fund_cn.symbols = new_symbols
                        poll_fund_cn.set_symbols(new_symbols)
                        # 重置候选池选中状态
                        pane = master_panes.get(view)
                        if pane:
                            pane.selected = 0
                            pane.left_scroll = 0
                        master_switches["market_fund_cn"].bump(now, _SWITCH_DEBOUNCE_S)
            elif key == ord("."):  # 切换领域（下一个）
                if view == "market_fund_cn":
                    selected_key = runtime_state.fund_domain.cycle(1)
                    if selected_key:
                        # 更新候选池 symbols
                        domain_profile = get_etf_domain_profile(selected_key)
                        new_symbols = [s.upper() for s in domain_profile.symbols]
                        quote_cfgs.fund_cn.symbols = new_symbols
                        poll_fund_cn.set_symbols(new_symbols)
                        # 重置候选池选中状态
                        pane = master_panes.get(view)
                        if pane:
                            pane.selected = 0
                            pane.left_scroll = 0
                        master_switches["market_fund_cn"].bump(now, _SWITCH_DEBOUNCE_S)
            elif key == ord("0"):
                # Keep a stable home key, now pointing to micro page by default.
                view = "market_micro"
                _remember_primary_view(view)
            elif key == curses.KEY_UP:
                if view == "market_news":
                    _, item_count = _news_counts(now)
                    news_state.news_selected = max(0, news_state.news_selected - 1)
                    news_state.news_selected = min(news_state.news_selected, max(0, item_count - 1))
                elif _is_master_view(view):
                    pane = master_panes[view]
                    if _master_has_signal_panel(view) and pane.focus == "right":
                        pane.right_scroll = max(0, pane.right_scroll - 1)
                    else:
                        old_selected = pane.selected
                        pane.selected = max(0, pane.selected - 1)
                        pane.right_scroll = 0
                        if pane.selected != old_selected and view in master_switches:
                            master_switches[view].bump(now, _SWITCH_DEBOUNCE_S)
                elif view.startswith("quotes_"):
                    qscroll[view] = max(0, qscroll.get(view, 0) - 1)
                elif view == "market_micro":
                    # Avoid wheel/arrow accidental symbol switches on micro page.
                    pass
                else:
                    scroll = max(0, scroll - 1)
            elif key == curses.KEY_DOWN:
                if view == "market_news":
                    _, item_count = _news_counts(now)
                    news_state.news_selected = min(max(0, item_count - 1), news_state.news_selected + 1)
                elif _is_master_view(view):
                    pane = master_panes[view]
                    if _master_has_signal_panel(view) and pane.focus == "right":
                        pane.right_scroll = max(0, pane.right_scroll + 1)
                    else:
                        old_selected = pane.selected
                        pane.selected = min(max(0, _master_symbol_count(view) - 1), pane.selected + 1)
                        pane.right_scroll = 0
                        if pane.selected != old_selected and view in master_switches:
                            master_switches[view].bump(now, _SWITCH_DEBOUNCE_S)
                elif view.startswith("quotes_"):
                    qscroll[view] = max(0, qscroll.get(view, 0) + 1)
                elif view == "market_micro":
                    # Avoid wheel/arrow accidental symbol switches on micro page.
                    pass
                else:
                    scroll = min(max(0, len(rows) - 1), scroll + 1)
            elif key == curses.KEY_PPAGE:  # PageUp
                if view == "market_news":
                    _, item_count = _news_counts(now)
                    news_state.news_selected = max(0, news_state.news_selected - 10)
                    news_state.news_selected = min(news_state.news_selected, max(0, item_count - 1))
                elif _is_master_view(view):
                    pane = master_panes[view]
                    if _master_has_signal_panel(view) and pane.focus == "right":
                        pane.right_scroll = max(0, pane.right_scroll - 10)
                    else:
                        old_selected = pane.selected
                        pane.selected = max(0, pane.selected - 10)
                        pane.right_scroll = 0
                        if pane.selected != old_selected and view in master_switches:
                            master_switches[view].bump(now, _SWITCH_DEBOUNCE_S)
                elif view.startswith("quotes_"):
                    qscroll[view] = max(0, qscroll.get(view, 0) - 10)
                else:
                    scroll = max(0, scroll - 10)
            elif key == curses.KEY_NPAGE:  # PageDown
                if view == "market_news":
                    _, item_count = _news_counts(now)
                    news_state.news_selected = min(max(0, item_count - 1), news_state.news_selected + 10)
                elif _is_master_view(view):
                    pane = master_panes[view]
                    if _master_has_signal_panel(view) and pane.focus == "right":
                        pane.right_scroll = max(0, pane.right_scroll + 10)
                    else:
                        old_selected = pane.selected
                        pane.selected = min(max(0, _master_symbol_count(view) - 1), pane.selected + 10)
                        pane.right_scroll = 0
                        if pane.selected != old_selected and view in master_switches:
                            master_switches[view].bump(now, _SWITCH_DEBOUNCE_S)
                elif view.startswith("quotes_"):
                    qscroll[view] = max(0, qscroll.get(view, 0) + 10)
                else:
                    scroll = min(max(0, len(rows) - 1), scroll + 10)
            elif key == ord("g"):
                if view == "market_news":
                    news_state.news_selected = 0
                    news_state.news_scroll = 0
                elif _is_master_view(view):
                    pane = master_panes[view]
                    if _master_has_signal_panel(view) and pane.focus == "right":
                        pane.right_scroll = 0
                    else:
                        old_selected = pane.selected
                        pane.selected = 0
                        pane.left_scroll = 0
                        pane.right_scroll = 0
                        if pane.selected != old_selected and view in master_switches:
                            master_switches[view].bump(now, _SWITCH_DEBOUNCE_S)
                elif view.startswith("quotes_"):
                    qscroll[view] = 0
                else:
                    scroll = 0
            elif key == ord("G"):
                if view == "market_news":
                    _, item_count = _news_counts(now)
                    news_state.news_selected = max(0, item_count - 1)
                elif _is_master_view(view):
                    pane = master_panes[view]
                    if _master_has_signal_panel(view) and pane.focus == "right":
                        pane.right_scroll = 10**9
                    else:
                        old_selected = pane.selected
                        pane.selected = max(0, _master_symbol_count(view) - 1)
                        pane.right_scroll = 0
                        if pane.selected != old_selected and view in master_switches:
                            master_switches[view].bump(now, _SWITCH_DEBOUNCE_S)
                elif view.startswith("quotes_"):
                    qscroll[view] = 10**9
                else:
                    scroll = max(0, len(rows) - 1)
            elif key in (10, 13, curses.KEY_ENTER):
                pass
            elif key == ord("/"):
                if view == "market_news":
                    raw = _prompt("News search keyword: ", max_len=80)
                    news_state.search_query = (raw or "").strip()
                    news_state.news_selected = 0
                    news_state.news_scroll = 0
            elif key in (ord("f"), ord("F")):
                if view == "market_news":
                    news_state.category_idx = (news_state.category_idx + 1) % len(_NEWS_CATEGORIES)
                    news_state.news_selected = 0
                    news_state.news_scroll = 0
            elif key in (ord("w"), ord("W")):
                if view == "market_news":
                    news_state.window_idx = (news_state.window_idx + 1) % len(_NEWS_WINDOWS_H)
                    news_state.news_selected = 0
                    news_state.news_scroll = 0
            elif key in (ord("c"), ord("C")):
                if view == "market_news":
                    news_state.search_query = ""
                    news_state.source_idx = 0
                    news_state.news_selected = 0
                    news_state.news_scroll = 0
            elif key == ord("p"):
                filt.toggle_source("pg")
                scroll = 0
                last_refresh = 0.0
            elif key in (ord("s"), ord("S")):
                if view == "market_news":
                    source_options = _news_source_options()
                    if source_options:
                        news_state.source_idx = (news_state.source_idx + 1) % len(source_options)
                        news_state.news_selected = 0
                        news_state.news_scroll = 0
                else:
                    filt.toggle_source("sqlite")
                    scroll = 0
                    last_refresh = 0.0
            elif key == ord("b"):
                filt.toggle_direction("BUY")
                scroll = 0
                last_refresh = 0.0
            elif key == ord("e"):
                filt.toggle_direction("SELL")
                scroll = 0
                last_refresh = 0.0
            elif key == ord("a"):
                filt.toggle_direction("ALERT")
                scroll = 0
                last_refresh = 0.0
            elif key in (ord("r"), ord("R")):
                _refresh_all_data()
                if view == "market_fund_cn":
                    _reload_dynamic_fund_universe(top_n=35)
            elif key in (ord("u"), ord("U")) and view == "market_fund_cn":
                if _reload_dynamic_fund_universe(top_n=35):
                    _refresh_all_data()
            elif key in (ord("+"), ord("=")) and (view.startswith("quotes_") or _is_master_view(view) or view == "market_micro"):
                raw = _prompt("Add symbols (comma-separated): ")
                if raw:
                    if view in {"quotes_us", "market_us"}:
                        added = normalize_us_symbols(raw)
                        quote_cfgs.us.symbols = normalize_us_symbols(",".join(quote_cfgs.us.symbols + added))
                        poll_us.set_symbols(quote_cfgs.us.symbols)
                        pane = master_panes.get("market_us")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.us.symbols) - 1))
                        master_switches["market_us"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view in {"quotes_hk", "market_hk"}:
                        added = normalize_hk_symbols(raw)
                        quote_cfgs.hk.symbols = normalize_hk_symbols(",".join(quote_cfgs.hk.symbols + added))
                        poll_hk.set_symbols(quote_cfgs.hk.symbols)
                        pane = master_panes.get("market_hk")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.hk.symbols) - 1))
                        master_switches["market_hk"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view in {"quotes_cn", "market_cn"}:
                        added = normalize_cn_symbols(raw)
                        quote_cfgs.cn.symbols = normalize_cn_symbols(",".join(quote_cfgs.cn.symbols + added))
                        poll_cn.set_symbols(quote_cfgs.cn.symbols)
                        pane = master_panes.get("market_cn")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.cn.symbols) - 1))
                        master_switches["market_cn"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view == "market_fund_cn":
                        added = normalize_cn_fund_symbols(raw)
                        quote_cfgs.fund_cn.symbols = normalize_cn_fund_symbols(",".join(quote_cfgs.fund_cn.symbols + added))
                        poll_fund_cn.set_symbols(quote_cfgs.fund_cn.symbols)
                        pane = master_panes.get("market_fund_cn")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.fund_cn.symbols) - 1))
                        master_switches["market_fund_cn"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view in {"quotes_crypto", "market_crypto", "market_micro"}:
                        added = normalize_crypto_symbols(raw)
                        quote_cfgs.crypto.symbols = normalize_crypto_symbols(",".join(quote_cfgs.crypto.symbols + added))
                        poll_crypto.set_symbols(quote_cfgs.crypto.symbols)
                        micro_switch.bump(now, _SWITCH_DEBOUNCE_S)
                    elif view == "quotes_metals":
                        added = normalize_metals_symbols(raw)
                        quote_cfgs.metals.symbols = normalize_metals_symbols(",".join(quote_cfgs.metals.symbols + added))
                        poll_metals.set_symbols(quote_cfgs.metals.symbols)
                    _persist_watchlists()
            elif key in (ord("-"), ord("_")) and (view.startswith("quotes_") or _is_master_view(view) or view == "market_micro"):
                raw = _prompt("Remove symbols (comma-separated): ")
                if raw:
                    if view in {"quotes_us", "market_us"}:
                        rm = set(normalize_us_symbols(raw))
                        quote_cfgs.us.symbols = [s for s in quote_cfgs.us.symbols if s.upper() not in rm]
                        poll_us.set_symbols(quote_cfgs.us.symbols)
                        pane = master_panes.get("market_us")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.us.symbols) - 1))
                        master_switches["market_us"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view in {"quotes_hk", "market_hk"}:
                        rm = set(normalize_hk_symbols(raw))
                        quote_cfgs.hk.symbols = [s for s in quote_cfgs.hk.symbols if s.zfill(5) not in rm]
                        poll_hk.set_symbols(quote_cfgs.hk.symbols)
                        pane = master_panes.get("market_hk")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.hk.symbols) - 1))
                        master_switches["market_hk"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view in {"quotes_cn", "market_cn"}:
                        rm = set(normalize_cn_symbols(raw))
                        quote_cfgs.cn.symbols = [s for s in quote_cfgs.cn.symbols if s.upper() not in rm]
                        poll_cn.set_symbols(quote_cfgs.cn.symbols)
                        pane = master_panes.get("market_cn")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.cn.symbols) - 1))
                        master_switches["market_cn"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view == "market_fund_cn":
                        rm = set(normalize_cn_fund_symbols(raw))
                        quote_cfgs.fund_cn.symbols = [s for s in quote_cfgs.fund_cn.symbols if s.upper() not in rm]
                        poll_fund_cn.set_symbols(quote_cfgs.fund_cn.symbols)
                        pane = master_panes.get("market_fund_cn")
                        if pane is not None:
                            pane.selected = min(max(0, pane.selected), max(0, len(quote_cfgs.fund_cn.symbols) - 1))
                        master_switches["market_fund_cn"].bump(now, _SWITCH_DEBOUNCE_S)
                    elif view in {"quotes_crypto", "market_crypto", "market_micro"}:
                        rm = set(normalize_crypto_symbols(raw))
                        quote_cfgs.crypto.symbols = [s for s in quote_cfgs.crypto.symbols if s.upper() not in rm]
                        poll_crypto.set_symbols(quote_cfgs.crypto.symbols)
                        _switch_micro_symbol(0)
                        micro_switch.bump(now, _SWITCH_DEBOUNCE_S)
                    elif view == "quotes_metals":
                        rm = set(normalize_metals_symbols(raw))
                        quote_cfgs.metals.symbols = [s for s in quote_cfgs.metals.symbols if s.upper() not in rm]
                        poll_metals.set_symbols(quote_cfgs.metals.symbols)
                    _persist_watchlists()
    finally:
        poll_us.stop()
        poll_hk.stop()
        poll_cn.stop()
        poll_fund_cn.stop()
        poll_crypto.stop()
        poll_metals.stop()
        if news_poller is not None:
            news_poller.stop()


def _draw(
    stdscr,
    db_path: str,
    rows: list[SignalRow],
    rows_all: list[SignalRow],
    filt: Filters,
    scroll: int,
    colors: dict[str, int],
    refresh_s: float,
    last_id: int,
    quote_cfgs: QuoteConfigs,
    quote_state_us: QuoteBookState,
    quote_state_hk: QuoteBookState,
    quote_state_cn: QuoteBookState,
    quote_state_fund_cn: QuoteBookState,
    quote_state_crypto: QuoteBookState,
    quote_state_metals: QuoteBookState,
    latest_sig_map_crypto: dict[str, SignalRow],
    us_curve_map: dict[str, list[Candle]],
    hk_curve_map: dict[str, list[Candle]],
    cn_curve_map: dict[str, list[Candle]],
    fund_cn_curve_map: dict[str, list[Candle]],
    fund_cn_daily_curve_map: dict[str, list[Candle]],
    crypto_curve_map: dict[str, list[Candle]],
    us_micro_snapshots: dict[str, MicroSnapshot],
    hk_micro_snapshots: dict[str, MicroSnapshot],
    cn_micro_snapshots: dict[str, MicroSnapshot],
    fund_cn_micro_snapshots: dict[str, MicroSnapshot],
    micro_snapshot: MicroSnapshot,
    micro_symbols: list[str],
    service_status: ServiceStatus,
    view: str,
    qscroll: int,
    master_pane: MasterPaneState | None,
    runtime_state: RuntimeState,
    news_state: NewsPageState | None = None,
    news_snapshot: NewsFeedSnapshot | None = None,
) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _draw_header(stdscr, colors, filt, refresh_s, view, service_status, w)
    if view == "quotes_us":
        _draw_quotes(stdscr, "US", quote_cfgs.us, quote_state_us, w, h, qscroll)
    elif view == "market_us":
        _draw_market_quad(
            stdscr,
            label="US",
            quote_cfg=quote_cfgs.us,
            quote_state=quote_state_us,
            rows=rows_all,
            pane=master_pane or MasterPaneState(),
            colors=colors,
            curve_map=us_curve_map,
            micro_snapshots=us_micro_snapshots,
            w=w,
            h=h,
            refresh_s=refresh_s,
        )
    elif view == "market_hk":
        _draw_market_quad(
            stdscr,
            label="HK",
            quote_cfg=quote_cfgs.hk,
            quote_state=quote_state_hk,
            rows=rows_all,
            pane=master_pane or MasterPaneState(),
            colors=colors,
            curve_map=hk_curve_map,
            micro_snapshots=hk_micro_snapshots,
            w=w,
            h=h,
            refresh_s=refresh_s,
        )
    elif view == "quotes_hk":
        _draw_quotes(stdscr, "HK", quote_cfgs.hk, quote_state_hk, w, h, qscroll)
    elif view == "quotes_cn":
        _draw_quotes(stdscr, "CN", quote_cfgs.cn, quote_state_cn, w, h, qscroll)
    elif view == "market_cn":
        _draw_market_quad(
            stdscr,
            label="CN",
            quote_cfg=quote_cfgs.cn,
            quote_state=quote_state_cn,
            rows=rows_all,
            pane=master_pane or MasterPaneState(),
            colors=colors,
            curve_map=cn_curve_map,
            micro_snapshots=cn_micro_snapshots,
            w=w,
            h=h,
            refresh_s=refresh_s,
        )
    elif view == "market_fund_cn":
        _draw_market_fund_two_panel(
            stdscr,
            quote_cfg=quote_cfgs.fund_cn,
            quote_state=quote_state_fund_cn,
            rows=rows_all,
            pane=master_pane or MasterPaneState(),
            colors=colors,
            curve_map=fund_cn_curve_map,
            daily_curve_map=fund_cn_daily_curve_map,
            micro_snapshots=fund_cn_micro_snapshots,
            w=w,
            h=h,
            refresh_s=refresh_s,
            runtime_state=runtime_state,
        )
    elif view in {"quotes_crypto", "market_crypto"}:
        # Back-compat: old crypto quote pages are folded into market_micro.
        _draw_market_micro(stdscr, micro_snapshot, micro_symbols, rows_all, quote_state_crypto, crypto_curve_map, colors, w, h)
    elif view == "market_micro":
        _draw_market_micro(stdscr, micro_snapshot, micro_symbols, rows_all, quote_state_crypto, crypto_curve_map, colors, w, h)
    elif view == "market_backtest":
        _draw_market_backtest(stdscr, colors, w, h)
    elif view == "market_news":
        _draw_market_news(
            stdscr,
            news_state or NewsPageState(),
            news_snapshot,
            quote_cfgs,
            quote_state_us,
            quote_state_hk,
            quote_state_cn,
            quote_state_crypto,
            colors,
            w,
            h,
        )
    elif view == "quotes_metals":
        _draw_quotes(stdscr, "METALS", quote_cfgs.metals, quote_state_metals, w, h, qscroll)
    else:
        _draw_signals(stdscr, db_path, rows, filt, scroll, colors, refresh_s, last_id, w, h, quote_state_crypto)

    stdscr.noutrefresh()
    curses.doupdate()


def _draw_header(
    stdscr,
    colors: dict[str, int],
    filt: Filters,
    refresh_s: float,
    view: str,
    service_status: ServiceStatus,
    width: int,
) -> None:
    now = datetime.now().strftime("%y-%m-%d %H:%M:%S")
    status = "已暂停" if filt.paused else f"刷新={refresh_s:.1f}s"
    svc = _format_service_status_bar(service_status)
    header = _build_header_line(now, view, status, svc, width)
    stdscr.move(0, 0)
    stdscr.clrtoeol()
    _safe_addstr(stdscr, 0, 0, header, curses.color_pair(colors.get("ALERT", 0)) | curses.A_BOLD)


def _draw_market_master(
    stdscr,
    label: str,
    quote_cfg: QuoteConfig,
    quote_state: QuoteBookState,
    rows: list[SignalRow],
    pane: MasterPaneState,
    w: int,
    h: int,
    refresh_s: float,
    show_signals: bool = True,
) -> None:
    key_hint = (
        "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯 | tab切焦点 | +/-加减自选 | ↑↓滚动"
        if show_signals
        else "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯 | +/-加减自选 | ↑↓滚动"
    )

    symbols = [s.strip().upper() for s in (quote_cfg.symbols or []) if (s or "").strip()]
    if not (quote_cfg.enabled and symbols):
        _safe_addstr(stdscr, 1, 0, _truncate("行情页：未启用或无标的", w))
        _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))
        return

    pane.selected = min(max(0, pane.selected), max(0, len(symbols) - 1))
    selected_symbol = symbols[pane.selected]
    selected_rows = _signals_for_symbol(rows, selected_symbol, quote_cfg.market)

    market_name = _market_display_name(quote_cfg.market, label)
    line1 = f"行情[{market_name}]"
    _safe_addstr(stdscr, 1, 0, _truncate(line1, w))
    _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))

    if not show_signals:
        pane.focus = "left"

    if show_signals:
        split_x = max(42, int(w * 0.72))
        if split_x >= w - 24:
            split_x = max(28, w - 24)
        left_w = max(24, split_x)
        right_x = min(w - 1, split_x + 1)
        right_w = max(10, w - right_x)
    else:
        left_w = max(24, w)
        right_x = w
        right_w = 0

    panel_top = 2
    panel_h = max(0, h - panel_top - 1)
    if panel_h < 4:
        return

    _draw_box(stdscr, 0, panel_top, left_w, panel_h)
    if show_signals:
        _draw_box(stdscr, right_x, panel_top, right_w, panel_h)

    left_focus = "*" if (pane.focus == "left" or not show_signals) else " "
    sel_state = quote_state.entries.get(selected_symbol)
    sel_quote = sel_state.quote if sel_state else None
    _safe_addstr(stdscr, panel_top, 1, _truncate(f"[{left_focus}] 行情", left_w - 2), curses.A_UNDERLINE)
    if show_signals:
        right_focus = "*" if pane.focus == "right" else " "
        _safe_addstr(
            stdscr,
            panel_top,
            right_x + 1,
            _truncate(f"[{right_focus}] 信号: {_display_name(selected_symbol, sel_quote, quote_cfg.market)}", right_w - 2),
            curses.A_UNDERLINE,
        )

    left_col = "名称         最新     涨跌     幅度     开盘     最高     最低      量      时间      延迟 状态"
    _safe_addstr(stdscr, panel_top + 1, 1, _truncate(left_col, left_w - 2), curses.A_UNDERLINE)
    if show_signals:
        right_col = "时间    方向 强度 周期 类型"
        _safe_addstr(stdscr, panel_top + 1, right_x + 1, _truncate(right_col, right_w - 2), curses.A_UNDERLINE)

    left_body_top = panel_top + 2
    right_body_top = panel_top + 2
    left_body_h = max(0, panel_h - 3)
    right_body_h = max(0, panel_h - 3) if show_signals else 0

    pane.left_scroll = min(max(0, pane.left_scroll), max(0, len(symbols) - 1))
    if pane.selected < pane.left_scroll:
        pane.left_scroll = pane.selected
    if pane.selected >= pane.left_scroll + max(1, left_body_h):
        pane.left_scroll = max(0, pane.selected - max(1, left_body_h) + 1)

    now_dt = datetime.now()
    left_visible = symbols[pane.left_scroll : pane.left_scroll + max(1, left_body_h)]
    now_dt = datetime.now()
    for i, sym in enumerate(left_visible):
        y = left_body_top + i
        st = quote_state.entries.get(sym) or QuoteEntryState(quote=None, last_error="pending", last_fetch_at=0.0)
        q = st.quote
        age_s = int(max(0.0, time.time() - (st.last_fetch_at or 0.0))) if st.last_fetch_at else 0
        prefix = ">" if (pane.left_scroll + i) == pane.selected else " "
        name = _display_name(sym, q, quote_cfg.market)
        if q is None:
            status = (st.last_error or "no-data").strip()[:2]
            line = f"{prefix} {name:<10} {'--':>7} {'--':>8} {'--':>7} {'--':>7} {'--':>7} {'--':>7} {'--':>8} {'--':<8} {age_s:>3}s {status:<2}"
            _safe_addstr(stdscr, y, 1, _truncate(line, left_w - 2))
            continue
        chg = q.price - q.prev_close
        pct = (chg / q.prev_close * 100.0) if q.prev_close else 0.0
        status = "ok" if not (st.last_error or "").strip() else "er"
        line = (
            f"{prefix} {name:<10} {q.price:>7.2f} {chg:>+8.2f} {pct:>+6.2f}% {q.open:>7.2f} {q.high:>7.2f} {q.low:>7.2f} "
            f"{_fmt_vol(q.volume):>8} {_fmt_quote_ts_date8(q.ts):<8} {age_s:>3}s {status:<2}"
        )
        _safe_addstr(stdscr, y, 1, _truncate(line, left_w - 2))

    if show_signals:
        if pane.right_scroll >= 10**8:
            right_start = max(0, len(selected_rows) - max(1, right_body_h))
        else:
            right_start = min(max(0, pane.right_scroll), max(0, len(selected_rows) - 1))
        right_visible = selected_rows[right_start : right_start + max(1, right_body_h)]

        for i, row in enumerate(right_visible):
            y = right_body_top + i
            t = _fmt_time(row.timestamp)
            direction = (row.direction or "").upper()[:4]
            strength = str(row.strength)[:3]
            tf = (row.timeframe or "")[:3]
            stype = (row.signal_type or "")[:10]
            line = f"{t:<8}{direction:<5}{strength:>3} {tf:<3} {stype:<10}"
            _safe_addstr(stdscr, y, right_x + 1, _truncate(line, right_w - 2))

def _draw_quotes(
    stdscr,
    label: str,
    quote_cfg: QuoteConfig,
    quote_state: QuoteBookState,
    w: int,
    h: int,
    qscroll: int,
    sig_map: dict[str, SignalRow] | None = None,
) -> None:
    key_hint = "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯 | +/-加减自选 | ↑↓滚动 | r刷新"

    symbols = [s.strip().upper() for s in (quote_cfg.symbols or []) if (s or "").strip()]
    if not (quote_cfg.enabled and symbols):
        _safe_addstr(stdscr, 1, 0, _truncate("报价页：未启用或无标的", w))
        _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))
        return

    # Summary lines
    market_name = _market_display_name(quote_cfg.market, label)
    line1 = f"报价[{market_name}]"
    _safe_addstr(stdscr, 1, 0, _truncate(line1, w))
    _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))

    # Table header
    col = "代码        名称         最新     涨跌     幅度     开盘     最高     最低      量      币种  时间               延迟 状态"
    if quote_cfg.market == "crypto_spot":
        col += "  信号向 强度 周期 信号旧度 信号类型"
    _safe_addstr(stdscr, 3, 0, _truncate(col, w), curses.A_UNDERLINE)

    body_top = 4
    body_h = max(0, h - body_top - 1)
    if body_h <= 0:
        return

    # Stable symbol order.
    entries: list[tuple[str, QuoteEntryState]] = []
    for sym in symbols:
        st = quote_state.entries.get(sym) or QuoteEntryState(quote=None, last_error="pending", last_fetch_at=0.0)
        entries.append((sym, st))

    if qscroll >= 10**8:
        start = max(0, len(entries) - body_h)
    else:
        start = min(max(0, qscroll), max(0, len(entries) - 1))
    visible = entries[start : start + body_h]

    for i, (sym, st) in enumerate(visible):
        y = body_top + i
        q = st.quote
        age_s = max(0.0, time.time() - (st.last_fetch_at or 0.0)) if st.last_fetch_at else 0.0
        disp_sym = sym
        if quote_cfg.market == "hk_stock":
            disp_sym = f"{sym}.HK"
        elif quote_cfg.market == "cn_stock" and len(sym) > 2 and sym[:2] in {"SH", "SZ"}:
            disp_sym = f"{sym[2:]}.{sym[:2]}"
        elif quote_cfg.market == "crypto_spot" and "_" in sym:
            disp_sym = sym.replace("_", "/")
        elif quote_cfg.market in {"metals", "metals_spot"} and sym.endswith("USD") and len(sym) >= 6:
            # Common UX for metals FX-style tickers (XAUUSD -> XAU/USD).
            disp_sym = f"{sym[:3]}/USD"
        if q is None:
            status = (st.last_error or "unavailable").strip()
            line = (
                f"{disp_sym:<10}  {'--':<10}  {'--':>7}  {'--':>7}  {'--':>7}  {'--':>7}  {'--':>7}  {'--':>7}  "
                f"{'--':>7}  {'--':<4}  {'--':<17}  {age_s:>3.0f}s  {status}"
            )
            _safe_addstr(stdscr, y, 0, _truncate(line, w))
            continue

        chg = q.price - q.prev_close
        pct = (chg / q.prev_close * 100.0) if q.prev_close else 0.0
        vol = _fmt_vol(q.volume)
        cur_raw = (q.currency or "--").strip()
        cur = cur_raw[:4] if quote_cfg.market == "crypto_spot" else cur_raw[:3]
        # Name can be non-ASCII; keep it short to reduce width issues.
        name = (q.name or "").strip().replace("\n", " ")
        if not name:
            name = "--"
        name = _truncate(name, 10)
        status = "ok" if not (st.last_error or "").strip() else (st.last_error or "").strip()
        if status == "ok" and (q.source or "").strip():
            status = f"ok({q.source})"
        line = (
            f"{disp_sym:<10}  {name:<10}  {q.price:>7.2f}  {chg:>+7.2f}  {pct:>+6.2f}%  {q.open:>7.2f}  {q.high:>7.2f}  {q.low:>7.2f}  "
            f"{vol:>7}  {cur:<4}  {_fmt_quote_ts(q.ts):<17}  {age_s:>3.0f}s  {status}"
        )
        if quote_cfg.market == "crypto_spot" and sig_map is not None:
            sig = sig_map.get(sym)
            if sig is None:
                line += "  --      --  --      --s  --"
            else:
                sig_dir = (sig.direction or "").upper()[:5] or "--"
                sig_str = f"{sig.strength:>3}" if sig.strength is not None else "--"
                sig_tf = (sig.timeframe or "")[:3] or "--"
                dt = parse_ts(sig.timestamp)
                sig_age = int(max(0.0, time.time() - dt.timestamp())) if dt != datetime.min else 0
                sig_type = (sig.signal_type or "")[:10] or "--"
                line += f"  {sig_dir:<5}  {sig_str:>3} {sig_tf:<3}  {sig_age:>6}s  {sig_type:<10}"
        _safe_addstr(stdscr, y, 0, _truncate(line, w))


def _draw_signals(
    stdscr,
    db_path: str,
    rows: list[SignalRow],
    filt: Filters,
    scroll: int,
    colors: dict[str, int],
    refresh_s: float,
    last_id: int,
    w: int,
    h: int,
    quote_state_crypto: QuoteBookState,
) -> None:
    # Quote hint (this view focuses on signals)
    status = "已暂停" if filt.paused else f"刷新={refresh_s:.1f}s"
    header2 = f"信号页: 行数={len(rows)} last_id={last_id}  |  {status}  |  按键: q退出, t切页"
    _safe_addstr(stdscr, 1, 0, _truncate(header2, w))

    src = ",".join(sorted(filt.sources)) or "无"
    dirs = ",".join(sorted(filt.directions)) or "无"
    line3 = f"数据库: {db_path}  |  来源: {src}  |  方向: {dirs}  |  空格暂停, 方向键滚动"
    _safe_addstr(stdscr, 2, 0, _truncate(line3, w))

    # Column header
    col = "时间     源    向   强度  标的          周期  价格         类型                消息"
    _safe_addstr(stdscr, 3, 0, _truncate(col, w), curses.A_UNDERLINE)

    body_top = 4
    body_h = max(0, h - body_top - 1)
    if body_h <= 0:
        return

    start = min(scroll, max(0, len(rows) - 1))
    visible = rows[start : start + body_h]

    for i, r in enumerate(visible):
        y = body_top + i
        t = _fmt_time(r.timestamp)
        src = (r.source or "").upper()
        direction = (r.direction or "").upper()
        strength = str(r.strength)
        symbol = r.symbol
        tf = r.timeframe or ""
        price = "" if r.price is None else f"{r.price:.4f}"
        stype = r.signal_type
        msg = (r.message or "").replace("\n", " ")
        # For crypto signals, append current quote info (if available) so the signals page aligns with quotes_crypto.
        pair = _crypto_signal_symbol_to_pair(r.symbol)
        if "_" in pair:
            st = quote_state_crypto.entries.get(pair)
            if st and st.quote is not None:
                q = st.quote
                q_age_s = int(max(0.0, time.time() - (st.last_fetch_at or 0.0))) if st.last_fetch_at else 0
                msg = f"{msg} | q={q.price:.2f} {q_age_s}s {q.source}"

        dir_attr = curses.color_pair(colors.get(direction, 0)) | curses.A_BOLD
        src_attr = curses.color_pair(colors.get("SRC", 0))

        x = 0
        _safe_addstr(stdscr, y, x, f"{t:<8}")
        x += 8
        _safe_addstr(stdscr, y, x, f"{src:<5}"[:5], src_attr)
        x += 6
        _safe_addstr(stdscr, y, x, f"{direction:<4}"[:4], dir_attr)
        x += 5
        _safe_addstr(stdscr, y, x, f"{strength:>3}"[:3])
        x += 5
        _safe_addstr(stdscr, y, x, _truncate(f"{symbol:<12}", 12))
        x += 13
        _safe_addstr(stdscr, y, x, _truncate(f"{tf:<4}", 4))
        x += 5
        _safe_addstr(stdscr, y, x, _truncate(f"{price:<11}", 11))
        x += 12
        _safe_addstr(stdscr, y, x, _truncate(f"{stype:<18}", 18))
        x += 19
        _safe_addstr(stdscr, y, x, _truncate(msg, max(0, w - x)))

    # Footer
    footer = (
        "筛选: p PG | s SQLITE | b BUY | e SELL | a ALERT | r刷新 | "
        "t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯"
    )
    _safe_addstr(stdscr, h - 1, 0, _truncate(footer, w))


def _fmt_signed(v: float) -> str:
    return f"{v:+.3f}"


def _sample_candles_minmax(candles: list[Candle], target_points: int) -> list[Candle]:
    """Downsample dense candles while preserving local extremes."""
    if target_points <= 0:
        return []
    if len(candles) <= target_points:
        return candles[-target_points:]
    if len(candles) <= 4 or target_points < 4:
        return candles[-target_points:]

    head = candles[0]
    tail = candles[-1]
    body = candles[1:-1]
    bucket_count = max(1, (target_points - 2) // 2)
    bucket_size = max(1, len(body) // bucket_count)

    sampled: list[Candle] = [head]
    for bucket_idx in range(bucket_count):
        start = bucket_idx * bucket_size
        end = len(body) if bucket_idx == bucket_count - 1 else min(len(body), (bucket_idx + 1) * bucket_size)
        chunk = body[start:end]
        if not chunk:
            continue
        low = min(chunk, key=lambda c: float(c.low))
        high = max(chunk, key=lambda c: float(c.high))
        pair = (low, high) if low.ts_open <= high.ts_open else (high, low)
        for item in pair:
            if sampled and sampled[-1].ts_open == item.ts_open:
                continue
            sampled.append(item)

    if sampled[-1].ts_open != tail.ts_open:
        sampled.append(tail)

    if len(sampled) <= target_points:
        return sampled

    pick: list[Candle] = []
    seen_ts: set[int] = set()
    denom = max(1, target_points - 1)
    src_last = len(sampled) - 1
    for i in range(target_points):
        idx = int(round(i * src_last / denom))
        item = sampled[idx]
        if item.ts_open in seen_ts:
            continue
        seen_ts.add(item.ts_open)
        pick.append(item)
    return pick if pick else sampled[-target_points:]


def _draw_price_curve(
    stdscr,
    candles: list[Candle],
    colors: dict[str, int],
    x0: int,
    y0: int,
    width: int,
    height: int,
    marker_rows: list[SignalRow] | None = None,
) -> None:
    if width <= 12 or height <= 5 or not candles:
        return

    clean_candles = [
        c
        for c in candles
        if _is_finite_number(c.open)
        and _is_finite_number(c.high)
        and _is_finite_number(c.low)
        and _is_finite_number(c.close)
        and _is_finite_number(c.volume_est)
        and min(float(c.open), float(c.high), float(c.low), float(c.close)) > 0.0
    ]
    if not clean_candles:
        return

    # Reserve one row at the bottom for x-axis time labels.
    if height <= 6:
        return
    axis_h = 1
    chart_height = height - axis_h
    if chart_height <= 4:
        return

    # Reserve room for price labels so the chart body can stay compact and readable.
    label_w = 9
    if width <= label_w + 8:
        return

    chart_x0 = x0 + label_w
    chart_w = max(4, width - label_w - 1)

    raw_count = len(clean_candles)
    max_points = max(8, chart_w * 2)
    draw_candles = _sample_candles_minmax(clean_candles, target_points=max_points)
    highs = [c.high for c in draw_candles]
    lows = [c.low for c in draw_candles]
    top = max(highs)
    bottom = min(lows)

    span0 = abs(top - bottom)
    pad = max(1e-3, span0 * 0.03, abs(top) * 0.001)
    top += pad
    bottom -= pad
    span = max(1e-9, top - bottom)

    volume_h = 0
    price_h = chart_height
    volume_sep_y: int | None = None
    volume_y0: int | None = None
    if chart_height >= 10:
        volume_h = min(5, max(2, chart_height // 4))
        candidate_price_h = chart_height - volume_h - 1
        if candidate_price_h >= 4:
            price_h = candidate_price_h
            volume_sep_y = y0 + price_h
            volume_y0 = volume_sep_y + 1
        else:
            volume_h = 0

    def _to_y(value: float) -> int:
        ratio = (top - value) / span
        y = y0 + int(round(ratio * max(1, price_h - 1)))
        return max(y0, min(y0 + price_h - 1, y))

    utf = "utf" in (locale.getpreferredencoding(False) or "").lower()
    wick_char = "│" if utf else "|"
    body_char = "█" if utf else "#"

    buy_attr = curses.color_pair(colors.get("BUY", 0)) | curses.A_BOLD
    sell_attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD
    neutral_attr = curses.color_pair(colors.get("SRC", 0))

    tick_rows = sorted({
        y0,
        y0 + (price_h - 1) // 3,
        y0 + (price_h - 1) * 2 // 3,
        y0 + price_h - 1,
    })
    for gy in tick_rows:
        rel = (gy - y0) / max(1, price_h - 1)
        val = top - (span * rel)
        _safe_addstr(stdscr, gy, x0, _truncate(f"{val:>8.2f}", label_w), neutral_attr)

    # Merge multiple source candles that map to the same terminal column.
    # When points are sparse, keep candles contiguous (right-aligned) to avoid a "scatter" look.
    merged: dict[int, list[float | int]] = {}
    if len(draw_candles) <= chart_w:
        start_x = chart_x0 + (chart_w - len(draw_candles))
        for idx, candle in enumerate(draw_candles):
            x = start_x + idx
            merged[x] = [
                idx,
                float(candle.open),
                float(candle.high),
                float(candle.low),
                float(candle.close),
                float(candle.volume_est),
            ]
    else:
        denom = max(1, len(draw_candles) - 1)
        for idx, candle in enumerate(draw_candles):
            x = chart_x0 + int(round(idx * (chart_w - 1) / denom))
            data = merged.get(x)
            if data is None:
                merged[x] = [
                    idx,
                    float(candle.open),
                    float(candle.high),
                    float(candle.low),
                    float(candle.close),
                    float(candle.volume_est),
                ]
                continue
            data[2] = max(float(data[2]), float(candle.high))
            data[3] = min(float(data[3]), float(candle.low))
            data[5] = float(data[5]) + float(candle.volume_est)
            if idx >= int(data[0]):
                data[0] = idx
                data[4] = float(candle.close)

    columns: list[tuple[int, float, float, float, float, float]] = []
    for x in sorted(merged):
        _, o, h, l, c, v = merged[x]
        columns.append((x, float(o), float(h), float(l), float(c), float(v)))

    if not columns:
        return

    # Keep dot/line mode only for ultra-dense windows; most cases stay in candle mode.
    # This avoids "all dots" when n is moderate (e.g. 120 on a normal terminal width).
    line_mode = raw_count > int(chart_w * 3.0)
    line_char = "●" if utf else "*"
    line_seg_char = "─" if utf else "-"
    close_y_by_x: dict[int, int] = {}

    if line_mode:
        close_points: list[tuple[int, int, float, float]] = []
        for x, c_open, _c_high, _c_low, c_close, _c_vol in columns:
            close_points.append((x, _to_y(c_close), c_open, c_close))

        if close_points:
            px, py, _po, pc = close_points[0]
            close_y_by_x[px] = py
            _safe_addstr(stdscr, py, px, line_char, neutral_attr)
            for x, y, c_open, c_close in close_points[1:]:
                attr = buy_attr if c_close >= pc else sell_attr
                dx = max(1, x - px)
                for step in range(dx + 1):
                    cx = px + step
                    cy = int(round(py + (y - py) * (step / dx)))
                    glyph = line_char if step in {0, dx} else line_seg_char
                    _safe_addstr(stdscr, cy, cx, glyph, attr)
                    close_y_by_x[cx] = cy
                px, py, pc = x, y, c_close
    else:
        for x, c_open, c_high, c_low, c_close, _c_vol in columns:
            y_high = _to_y(c_high)
            y_low = _to_y(c_low)
            y_open = _to_y(c_open)
            y_close = _to_y(c_close)
            close_y_by_x[x] = y_close

            attr = buy_attr if c_close >= c_open else sell_attr

            for y in range(min(y_high, y_low), max(y_high, y_low) + 1):
                _safe_addstr(stdscr, y, x, wick_char, neutral_attr)

            y_top = min(y_open, y_close)
            y_bottom = max(y_open, y_close)
            for y in range(y_top, y_bottom + 1):
                _safe_addstr(stdscr, y, x, body_char, attr)

    last_x, last_open, _last_high, _last_low, last_close, _last_vol = columns[-1]
    last_y = _to_y(last_close)
    ref_char = "┈" if utf else "."
    for rx in range(chart_x0, chart_x0 + chart_w):
        _safe_addstr(stdscr, last_y, rx, ref_char, neutral_attr)
    _safe_addstr(stdscr, last_y, last_x, line_char if line_mode else body_char, buy_attr if last_close >= last_open else sell_attr)
    _safe_addstr(
        stdscr,
        last_y,
        x0,
        _truncate(f"{last_close:>8.2f}", label_w),
        buy_attr if last_close >= last_open else sell_attr,
    )

    if volume_h > 0 and volume_y0 is not None and volume_sep_y is not None:
        _safe_hline(stdscr, volume_sep_y, chart_x0, chart_w, neutral_attr)
        _safe_addstr(stdscr, volume_y0, x0, _truncate(f"{'VOL':>8}", label_w), neutral_attr)
        vol_values = [max(float(v), abs(float(c) - float(o))) for _x, o, _h, _l, c, v in columns]
        vol_max = max(vol_values) if vol_values else 0.0
        if vol_max > 0:
            vol_bottom = volume_y0 + volume_h - 1
            vol_char = "▇" if utf else "="
            for (x, c_open, _c_high, _c_low, c_close, _c_vol), v_metric in zip(columns, vol_values):
                ratio = max(0.0, min(1.0, float(v_metric) / float(vol_max)))
                bar_h = max(1, int(round(ratio * volume_h)))
                attr = buy_attr if c_close >= c_open else sell_attr
                for yy in range(vol_bottom - bar_h + 1, vol_bottom + 1):
                    _safe_addstr(stdscr, yy, x, vol_char, attr)

    if marker_rows and close_y_by_x:
        marker_candidates: list[tuple[int, SignalRow]] = []
        first_ts = int(draw_candles[0].ts_open)
        last_ts = int(draw_candles[-1].ts_open)
        span_ts = max(1, last_ts - first_ts)

        for row in marker_rows:
            direction = (row.direction or "").strip().upper()
            if direction not in {"BUY", "SELL", "ALER", "ALERT"}:
                continue
            ts_dt = parse_ts(row.timestamp)
            if ts_dt == datetime.min:
                continue
            ts_s = int(ts_dt.timestamp())
            if ts_s < first_ts or ts_s > last_ts:
                continue
            marker_candidates.append((ts_s, row))

        marker_candidates.sort(key=lambda item: item[0])
        marker_candidates = marker_candidates[-12:]

        marker_seen_x: set[int] = set()
        for ts_s, row in marker_candidates:
            direction = (row.direction or "").strip().upper()
            ratio = (ts_s - first_ts) / span_ts
            x = chart_x0 + int(round(ratio * (chart_w - 1)))
            x = max(chart_x0, min(chart_x0 + chart_w - 1, x))
            if x in marker_seen_x:
                continue
            marker_seen_x.add(x)

            if x in close_y_by_x:
                base_y = close_y_by_x[x]
            else:
                nearest_x = min(close_y_by_x, key=lambda px: abs(px - x))
                base_y = close_y_by_x[nearest_x]

            if direction == "BUY":
                glyph = "▲" if utf else "^"
                attr = buy_attr
                y = max(y0, base_y - 1)
            elif direction == "SELL":
                glyph = "▼" if utf else "v"
                attr = sell_attr
                y = min(y0 + price_h - 1, base_y + 1)
            else:
                glyph = "◆" if utf else "*"
                attr = curses.color_pair(colors.get("ALERT", 0)) | curses.A_BOLD
                y = base_y

            _safe_addstr(stdscr, y, x, glyph, attr)

    axis_y = y0 + chart_height
    axis_attr = curses.color_pair(colors.get("SRC", 0))
    _safe_hline(stdscr, axis_y, chart_x0, chart_w, axis_attr)

    first = draw_candles[0]
    mid = draw_candles[len(draw_candles) // 2]
    last = draw_candles[-1]
    span_ts = max(0, int(last.ts_open) - int(first.ts_open))
    if span_ts >= 2 * 24 * 3600:
        axis_fmt = "%m-%d"
    else:
        axis_fmt = "%H:%M"

    def _fmt_axis_ts(ts_open: int) -> str:
        try:
            return datetime.fromtimestamp(int(ts_open)).strftime(axis_fmt)
        except Exception:
            return "--"

    axis_labels = [
        (chart_x0, _fmt_axis_ts(first.ts_open), "left"),
        (chart_x0 + chart_w // 2, _fmt_axis_ts(mid.ts_open), "center"),
        (chart_x0 + chart_w - 1, _fmt_axis_ts(last.ts_open), "right"),
    ]

    for pos, label, align in axis_labels:
        if not label:
            continue
        if align == "left":
            lx = pos
        elif align == "right":
            lx = pos - len(label) + 1
        else:
            lx = pos - len(label) // 2
        lx = max(chart_x0, min(chart_x0 + chart_w - len(label), lx))
        _safe_addstr(stdscr, axis_y, lx, _truncate(label, max(0, chart_x0 + chart_w - lx)), axis_attr)


def _update_quote_curve(
    curves: dict[str, deque[Candle]],
    symbol: str,
    quote: Quote | None,
    fetched_at: float,
    interval_s: int = 5,
    max_points: int = 240,
) -> None:
    if quote is None:
        return

    sym = (symbol or "").strip().upper()
    if not sym:
        return

    price = float(quote.price)
    if price <= 0 or not _is_finite_number(price):
        return

    ts = float(fetched_at or 0.0)
    if ts <= 0:
        parsed = parse_ts(quote.ts)
        ts = parsed.timestamp() if parsed != datetime.min else time.time()

    step = max(1, int(interval_s))
    bucket = int(ts // step) * step

    buf = curves.get(sym)
    if buf is None:
        buf = deque(maxlen=max(20, int(max_points)))
        curves[sym] = buf

    if not buf or buf[-1].ts_open != bucket:
        buf.append(Candle(ts_open=bucket, open=price, high=price, low=price, close=price, volume_est=0.0, notional_est=0.0))
        return

    prev = buf[-1]
    buf[-1] = Candle(
        ts_open=prev.ts_open,
        open=prev.open,
        high=max(prev.high, price),
        low=min(prev.low, price),
        close=price,
        volume_est=prev.volume_est,
        notional_est=prev.notional_est,
    )


def _snapshot_quote_curves(curves: dict[str, deque[Candle]]) -> dict[str, list[Candle]]:
    return {sym: list(buf) for sym, buf in curves.items()}


def _curve_update_ts(quote: Quote, fetched_at: float, now_ts: float) -> float:
    """Use market timestamp for stale quotes so closed markets do not paint fake live bars."""
    quote_dt = parse_ts(quote.ts)
    if quote_dt == datetime.min:
        return float(fetched_at or now_ts)

    quote_ts = quote_dt.timestamp()
    if (now_ts - quote_ts) >= _CLOSED_CURVE_STALE_SECONDS:
        return quote_ts
    return float(fetched_at or quote_ts)


def _seed_curve_from_intraday_series(
    curves: dict[str, deque[Candle]],
    symbol: str,
    series: list[tuple[int, float, float]],
    *,
    interval_s: int = 60,
    max_points: int = 240,
) -> bool:
    """Replace/seed curve buffer from minute intraday history."""
    sym = (symbol or "").strip().upper()
    if not sym or not series:
        return False

    clean: list[tuple[int, float, float]] = []
    for ts_open, price, volume in series:
        try:
            ts_i = int(ts_open)
            px = float(price)
            vol = float(volume)
        except Exception:
            continue
        if ts_i <= 0 or px <= 0 or not (_is_finite_number(px) and _is_finite_number(vol)):
            continue
        clean.append((ts_i, px, max(0.0, vol)))

    if not clean:
        return False

    clean.sort(key=lambda x: x[0])
    bucket_s = max(1, int(interval_s))
    buffer = deque(maxlen=max(20, int(max_points)))

    prev_close = clean[0][1]
    prev_cum_vol = clean[0][2]
    for ts_open, close_px, cum_vol in clean:
        bucket = int(ts_open // bucket_s) * bucket_s
        open_px = prev_close
        high_px = max(open_px, close_px)
        low_px = min(open_px, close_px)
        vol_delta = max(0.0, cum_vol - prev_cum_vol)
        notional = vol_delta * close_px if vol_delta > 0 else 0.0

        buffer.append(
            Candle(
                ts_open=bucket,
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume_est=vol_delta,
                notional_est=notional,
            )
        )
        prev_close = close_px
        prev_cum_vol = cum_vol

    if not buffer:
        return False

    curves[sym] = buffer
    return True


def _seed_curve_from_daily_series(
    curves: dict[str, deque[Candle]],
    symbol: str,
    series: list[tuple[int, float, float, float, float, float]],
    *,
    max_points: int = 90,
) -> bool:
    """Replace/seed curve buffer from daily OHLCV history."""
    sym = (symbol or "").strip().upper()
    if not sym or not series:
        return False

    clean: list[tuple[int, float, float, float, float, float]] = []
    for ts_open, open_px, high_px, low_px, close_px, volume in series:
        try:
            ts_i = int(ts_open)
            o = float(open_px)
            h = float(high_px)
            l = float(low_px)
            c = float(close_px)
            v = float(volume)
        except Exception:
            continue
        if ts_i <= 0 or o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        if not (_is_finite_number(o) and _is_finite_number(h) and _is_finite_number(l) and _is_finite_number(c) and _is_finite_number(v)):
            continue
        clean.append((ts_i, o, h, l, c, max(0.0, v)))

    if not clean:
        return False

    clean.sort(key=lambda item: item[0])
    buffer = deque(maxlen=max(20, int(max_points)))
    for ts_i, o, h, l, c, v in clean:
        buffer.append(
            Candle(
                ts_open=ts_i,
                open=o,
                high=max(h, o, c),
                low=min(l, o, c),
                close=c,
                volume_est=v,
                notional_est=v * c if v > 0 else 0.0,
            )
        )

    if not buffer:
        return False
    curves[sym] = buffer
    return True


def _maybe_seed_fund_curve_from_daily_history(
    *,
    curves: dict[str, deque[Candle]],
    symbols: set[str],
    market: str,
    provider: str,
    attempts: dict[str, float],
    now_ts: float,
    lookback_days: int = 15,
) -> None:
    """
    Seed fund page curves with daily history.

    We fetch at a coarse interval to avoid high-frequency network polling while still keeping
    the chart window in a multi-day context (default 15D).
    """
    days = max(5, int(lookback_days))
    target_span_s = max(24 * 3600, (days - 1) * 24 * 3600)

    for symbol in sorted(symbols):
        existing = curves.get(symbol)
        last_try = float(attempts.get(symbol, 0.0) or 0.0)
        if existing is not None and len(existing) >= max(5, days // 2):
            span_s = max(0.0, float(existing[-1].ts_open - existing[0].ts_open))
            if span_s >= target_span_s and (now_ts - last_try) < _FUND_CN_CURVE_REFRESH_SECONDS:
                continue

        if (now_ts - last_try) < _FUND_CN_CURVE_REFRESH_SECONDS:
            continue
        attempts[symbol] = now_ts

        series = fetch_daily_curve_1d(
            provider=provider,
            market=market,
            symbol=symbol,
            timeout_s=6.0,
            limit=days,
        )
        if not series:
            continue
        _seed_curve_from_daily_series(
            curves,
            symbol,
            series,
            max_points=max(20, days * 3),
        )


def _maybe_seed_closed_curve_from_history(
    *,
    curves: dict[str, deque[Candle]],
    quote_state: QuoteBookState,
    symbols: set[str],
    market: str,
    provider: str,
    attempts: dict[str, float],
    now_ts: float,
    max_points: int = 240,
) -> None:
    """Seed a 1h replay curve for stale (closed-market) symbols."""
    for symbol in sorted(symbols):
        st = quote_state.entries.get(symbol)
        quote = st.quote if st else None
        if quote is None:
            continue

        quote_dt = parse_ts(quote.ts)
        if quote_dt == datetime.min:
            continue

        market_age_s = max(0.0, now_ts - quote_dt.timestamp())
        if market_age_s < _CLOSED_CURVE_STALE_SECONDS:
            attempts.pop(symbol, None)
            continue

        existing = curves.get(symbol)
        if existing is not None and len(existing) >= 2:
            span_s = max(0.0, float(existing[-1].ts_open - existing[0].ts_open))
            if span_s >= _CLOSED_CURVE_TARGET_SPAN_SECONDS:
                continue

        last_try = float(attempts.get(symbol, 0.0) or 0.0)
        if (now_ts - last_try) < _CLOSED_CURVE_RETRY_SECONDS:
            continue
        attempts[symbol] = now_ts

        series = fetch_intraday_curve_1m(
            provider=provider,
            market=market,
            symbol=symbol,
            timeout_s=6.0,
            limit=_CLOSED_CURVE_HISTORY_LIMIT,
        )
        if not series:
            continue

        _seed_curve_from_intraday_series(
            curves,
            symbol,
            series,
            interval_s=60,
            max_points=max_points,
        )


def _draw_market_quad(
    stdscr,
    label: str,
    quote_cfg: QuoteConfig,
    quote_state: QuoteBookState,
    rows: list[SignalRow],
    pane: MasterPaneState,
    colors: dict[str, int],
    curve_map: dict[str, list[Candle]],
    micro_snapshots: dict[str, MicroSnapshot],
    w: int,
    h: int,
    refresh_s: float,
) -> None:
    key_hint = "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯 | [/]切换 | +/-加减自选 | r刷新"

    symbols = [s.strip().upper() for s in (quote_cfg.symbols or []) if (s or "").strip()]
    if not (quote_cfg.enabled and symbols):
        _safe_addstr(stdscr, 1, 0, _truncate("行情页：未启用或无标的", w))
        _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))
        return

    pane.selected = min(max(0, pane.selected), max(0, len(symbols) - 1))
    selected_symbol = symbols[pane.selected]
    selected_rows = _signals_for_symbol(rows, selected_symbol, quote_cfg.market)
    selected_state = quote_state.entries.get(selected_symbol)
    selected_quote = selected_state.quote if selected_state else None
    selected_curve = curve_map.get(selected_symbol, [])
    _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))

    panel_top = 1
    panel_h = max(0, h - panel_top - 1)
    if panel_h < 10:
        return

    split_x = max(30, int(w * 0.38))
    if split_x >= w - 28:
        split_x = max(24, w - 28)
    left_w = max(24, split_x)
    right_x = min(w - 1, split_x + 1)
    right_w = max(18, w - right_x)

    right_top_h = int(round(panel_h * 0.62))
    right_top_h = max(8, min(right_top_h, panel_h - 6))
    right_bottom_y = panel_top + right_top_h
    right_bottom_h = panel_h - right_top_h
    if right_bottom_h < 5:
        return

    box_attr = curses.color_pair(colors.get("SRC", 0))
    _draw_box(stdscr, 0, panel_top, left_w, panel_h, box_attr)
    _draw_box(stdscr, right_x, panel_top, right_w, right_top_h, box_attr)
    _draw_box(stdscr, right_x, right_bottom_y, right_w, right_bottom_h, box_attr)

    left_inner_w = max(0, left_w - 2)
    _safe_addstr(stdscr, panel_top, 2, _truncate(f"候选池({len(symbols)})", max(0, left_w - 4)), curses.A_UNDERLINE)

    if left_inner_w >= 56:
        table_cols: list[tuple[str, str, int, str]] = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 10, "left"),
            ("name", "名称", 1, "left"),
            ("last", "最新", 7, "right"),
            ("pct", "涨跌", 7, "right"),
            ("sig", "12h", 4, "right"),
        ]
    elif left_inner_w >= 44:
        table_cols = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 10, "left"),
            ("name", "名称", 1, "left"),
            ("last", "最新", 7, "right"),
            ("pct", "涨跌", 7, "right"),
        ]
    elif left_inner_w >= 34:
        table_cols = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 8, "left"),
            ("name", "名称", 1, "left"),
            ("pct", "涨跌", 7, "right"),
        ]
    else:
        table_cols = [
            ("idx", "序", 3, "right"),
            ("name", "名称", 1, "left"),
            ("pct", "涨跌", 6, "right"),
        ]

    fixed_w = sum(width for key, _, width, _ in table_cols if key != "name")
    field_count = len(table_cols)
    overhead_w = 2 + max(0, field_count - 1)
    resolved_name_w = max(1, left_inner_w - fixed_w - overhead_w)
    resolved_cols: list[tuple[str, str, int, str]] = []
    for key, header, width, align in table_cols:
        if key == "name":
            resolved_cols.append((key, header, resolved_name_w, align))
        else:
            resolved_cols.append((key, header, width, align))

    def _render_left_row(prefix: str, values: dict[str, str]) -> str:
        cells = [_fit_cell(values.get(key, ""), width, align=align) for key, _, width, align in resolved_cols]
        body = " ".join(cells)
        return _fit_cell(f"{prefix} {body}", left_inner_w)

    header_values = {key: header for key, header, _, _ in resolved_cols}
    _safe_addstr(stdscr, panel_top + 1, 1, _render_left_row(" ", header_values), curses.A_UNDERLINE)

    left_body_top = panel_top + 2
    left_body_h = max(0, panel_h - 3)
    pane.left_scroll = min(max(0, pane.left_scroll), max(0, len(symbols) - 1))
    if pane.selected < pane.left_scroll:
        pane.left_scroll = pane.selected
    if pane.selected >= pane.left_scroll + max(1, left_body_h):
        pane.left_scroll = max(0, pane.selected - max(1, left_body_h) + 1)

    now_dt = datetime.now()
    left_visible = symbols[pane.left_scroll : pane.left_scroll + max(1, left_body_h)]
    for i, sym in enumerate(left_visible):
        y = left_body_top + i
        global_idx = pane.left_scroll + i
        st = quote_state.entries.get(sym) or QuoteEntryState(quote=None, last_error="pending", last_fetch_at=0.0)
        q = st.quote
        name = _display_name(sym, q, quote_cfg.market)
        code = _display_symbol(sym, quote_cfg.market)
        prefix = ">" if global_idx == pane.selected else " "

        last_txt = "--"
        pct_txt = "--"
        if q is not None:
            chg = q.price - q.prev_close
            pct = (chg / q.prev_close * 100.0) if q.prev_close else 0.0
            last_txt = f"{q.price:.2f}"
            pct_txt = f"{pct:+.2f}%"

        symbol_rows = _signals_for_symbol(rows, sym, quote_cfg.market)
        row_values = {
            "idx": str(global_idx + 1),
            "code": code,
            "name": name,
            "last": last_txt,
            "pct": pct_txt,
            "sig": str(_count_recent_signal_rows(symbol_rows, now_dt, max_age_s=12 * 60 * 60)),
        }
        _safe_addstr(stdscr, y, 1, _render_left_row(prefix, row_values))

    selected_label = _display_name(selected_symbol, selected_quote, quote_cfg.market)
    selected_disp_symbol = _display_symbol(selected_symbol, quote_cfg.market)
    _safe_addstr(
        stdscr,
        panel_top,
        right_x + 2,
        _truncate(f"标的详情: {selected_label} ({selected_disp_symbol})", max(0, right_w - 4)),
        curses.A_UNDERLINE,
    )

    if selected_quote is None:
        stats_line = "价格=--  涨跌=--  幅度=--  成交量=--  延迟=--  模式=--"
    else:
        selected_chg = selected_quote.price - selected_quote.prev_close
        selected_pct = (selected_chg / selected_quote.prev_close * 100.0) if selected_quote.prev_close else 0.0
        selected_age_s = int(max(0.0, time.time() - (selected_state.last_fetch_at or 0.0))) if selected_state else 0

        curve_mode = "LIVE"
        quote_ts_dt = parse_ts(selected_quote.ts)
        if quote_ts_dt != datetime.min:
            quote_age_s = max(0, int(time.time() - quote_ts_dt.timestamp()))
            if quote_age_s >= _CLOSED_CURVE_STALE_SECONDS:
                curve_span_s = 0.0
                if len(selected_curve) >= 2:
                    curve_span_s = max(0.0, float(selected_curve[-1].ts_open - selected_curve[0].ts_open))
                curve_mode = "CLOSE-1H" if curve_span_s >= _CLOSED_CURVE_TARGET_SPAN_SECONDS else "CLOSE"

        stats_line = (
            f"价格={selected_quote.price:.2f}  涨跌={selected_chg:+.2f} ({selected_pct:+.2f}%)  "
            f"成交量={_fmt_vol(selected_quote.volume)}  延迟={selected_age_s}s  模式={curve_mode}"
        )
    _safe_addstr(stdscr, panel_top + 1, right_x + 1, _truncate(stats_line, max(0, right_w - 2)))

    chart_y = panel_top + 2
    chart_h = max(1, right_top_h - 3)
    _draw_price_curve(
        stdscr,
        selected_curve,
        colors,
        right_x + 1,
        chart_y,
        right_w - 2,
        chart_h,
        marker_rows=selected_rows,
    )

    signal_panel_title = _build_recent_signal_panel_title(selected_rows, now_dt)
    _safe_addstr(stdscr, right_bottom_y, right_x + 2, _truncate(signal_panel_title, max(0, right_w - 4)), curses.A_UNDERLINE)

    right_inner_x = right_x + 1
    right_inner_y = right_bottom_y + 1
    right_inner_w = max(0, right_w - 2)
    right_inner_h = max(0, right_bottom_h - 2)

    # Keep content readable on very narrow terminals.
    if right_inner_w < 30 or right_inner_h < 3:
        _safe_addstr(
            stdscr,
            right_inner_y,
            right_inner_x,
            _truncate("信号区过窄：请放大终端查看三列（5min/1h/12h）", right_inner_w),
            curses.color_pair(colors.get("SRC", 0)),
        )
        right_body_h = max(0, right_inner_h - 1)
        if selected_rows and right_body_h > 0:
            right_visible = selected_rows[: max(1, right_body_h)]
            for i, row in enumerate(right_visible):
                y = right_inner_y + 1 + i
                ts_dt = parse_ts(row.timestamp)
                age_s = max(0, int((now_dt - ts_dt).total_seconds())) if ts_dt != datetime.min else 0
                direction = (row.direction or "--").upper()[:4]
                tf = (row.timeframe or "--")[:3]
                strength = _safe_int(row.strength, 0)
                line = f"{_fmt_time(row.timestamp):<8} {age_s:>3}s {direction:<4}{strength:>3} {tf:<3}"
                _safe_addstr(stdscr, y, right_inner_x, _truncate(line, right_inner_w))
    else:
        col_count = 3
        sep_count = col_count - 1
        usable_w = max(3, right_inner_w - sep_count)
        col_ws = [usable_w // col_count] * col_count
        for i in range(usable_w % col_count):
            col_ws[i] += 1
        col_xs = [right_inner_x]
        for i in range(1, col_count):
            col_xs.append(col_xs[-1] + col_ws[i - 1] + 1)
        sep_xs = [col_xs[1] - 1, col_xs[2] - 1]

        for sep_x in sep_xs:
            _safe_vline(stdscr, right_inner_y, sep_x, right_inner_h, curses.color_pair(colors.get("SRC", 0)))

        realtime_rows, h1_rows, h12_rows = _split_signal_rows_by_age(selected_rows, now_dt)

        buckets: list[tuple[str, list[tuple[SignalRow, int]]]] = [
            ("实时", realtime_rows),
            ("1h", h1_rows),
            ("12h", h12_rows),
        ]

        for i, (title, bucket_rows) in enumerate(buckets):
            header = f"{title}({len(bucket_rows)})"
            _safe_addstr(
                stdscr,
                right_inner_y,
                col_xs[i],
                _fit_cell(header, col_ws[i], align="left"),
                curses.A_UNDERLINE,
            )

        body_h = max(0, right_inner_h - 1)
        for i, (_title, bucket_rows) in enumerate(buckets):
            col_x = col_xs[i]
            col_w = col_ws[i]
            if body_h <= 0:
                continue
            if not bucket_rows:
                _safe_addstr(
                    stdscr,
                    right_inner_y + 1,
                    col_x,
                    _fit_cell("暂无", col_w, align="left"),
                    curses.color_pair(colors.get("SRC", 0)),
                )
                continue

            for row_idx, (row, _age_s) in enumerate(bucket_rows[:body_h]):
                y = right_inner_y + 1 + row_idx
                direction = (row.direction or "--").upper()
                tf = (row.timeframe or "--")[:3]
                strength = _safe_int(row.strength, 0)
                if col_w >= 20:
                    line = f"{_fmt_time(row.timestamp):<8} {direction[:4]:<4}{strength:>3} {tf:<3}"
                elif col_w >= 14:
                    line = f"{_fmt_time(row.timestamp)[3:]:<5} {direction[:1]}{strength:>2} {tf:<3}"
                else:
                    line = f"{direction[:1]}{strength:>2} {_fmt_time(row.timestamp)[3:]}"

                attr = 0
                if direction.startswith("BUY"):
                    attr = curses.color_pair(colors.get("BUY", 0))
                elif direction.startswith("SELL"):
                    attr = curses.color_pair(colors.get("SELL", 0))
                elif direction.startswith("ALER"):
                    attr = curses.color_pair(colors.get("ALERT", 0))
                _safe_addstr(stdscr, y, col_x, _fit_cell(line, col_w, align="left"), attr)

    # Keep footer row for key hints.


def _draw_market_fund_two_panel(
    stdscr,
    quote_cfg: QuoteConfig,
    quote_state: QuoteBookState,
    rows: list[SignalRow],
    pane: MasterPaneState,
    colors: dict[str, int],
    curve_map: dict[str, list[Candle]],
    daily_curve_map: dict[str, list[Candle]],
    micro_snapshots: dict[str, MicroSnapshot],
    w: int,
    h: int,
    refresh_s: float,
    runtime_state: RuntimeState,
) -> None:
    key_hint = "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯 | [/]切换标的 | ,.切换领域 | +/-加减自选 | r刷新"
    fund_domain = runtime_state.fund_domain

    # 获取当前选中领域
    domain_profile = get_etf_domain_profile(fund_domain.selected_key)
    domain_label = domain_profile.label or fund_domain.selected_key

    symbols = [s.strip().upper() for s in (quote_cfg.symbols or []) if (s or "").strip()]
    if not (quote_cfg.enabled and symbols):
        _safe_addstr(stdscr, 1, 0, _truncate("行情页：未启用或无标的", w))
        _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))
        return

    pane.selected = min(max(0, pane.selected), max(0, len(symbols) - 1))
    selected_symbol = symbols[pane.selected]
    selected_state = quote_state.entries.get(selected_symbol)
    selected_quote = selected_state.quote if selected_state else None
    selected_rows = _signals_for_symbol(rows, selected_symbol, quote_cfg.market)
    selected_live_curve = curve_map.get(selected_symbol, [])
    selected_curve = daily_curve_map.get(selected_symbol) or selected_live_curve

    top_n_limit = max(1, int(domain_profile.top_n))
    ranking_profile = replace(domain_profile, top_n=max(top_n_limit, len(symbols)))
    ranking_snapshot = select_etf_candidates(
        profile=ranking_profile,
        symbols=symbols,
        quote_entries=quote_state.entries,
        curve_map=curve_map,
        micro_snapshots=micro_snapshots,
        now_ts=time.time(),
        stale_seconds=120,
    )
    model_items = tuple(ranking_snapshot.items)
    top_items = tuple(model_items[:top_n_limit])
    model_rank_map = {item.symbol: idx + 1 for idx, item in enumerate(model_items)}
    model_item_map = {item.symbol: item for item in model_items}
    domain_top_symbols = tuple(symbols[:top_n_limit])
    candidate_rank_map = {sym: idx + 1 for idx, sym in enumerate(symbols)}
    selected_rank = model_rank_map.get(selected_symbol)
    selected_item = model_item_map.get(selected_symbol)
    risk_map = {"LOW": "低", "MED": "中", "HIGH": "高"}

    line2 = (
        f"策略={ranking_snapshot.strategy_label} {ranking_snapshot.strategy_version} | 领域={domain_label} | 覆盖={ranking_snapshot.valid_candidates}/"
        f"{ranking_snapshot.total_candidates} 过期={ranking_snapshot.skipped_stale} | "
        f"口径: cnd=领域相关序 MRank=模型排名 sRank=模型总分 | 更新时间={ranking_snapshot.as_of}"
    )
    _safe_addstr(stdscr, 1, 0, _truncate(line2, w), curses.color_pair(colors.get("SRC", 0)))
    _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))

    panel_top = 2
    panel_h = max(0, h - panel_top - 1)
    if panel_h < 10:
        return

    # 领域列宽度
    domain_col_w = 10
    domain_x = 0

    split_x = max(30, int(w * 0.38))
    if split_x >= w - 28 - domain_col_w:
        split_x = max(24, w - 28 - domain_col_w)
    left_w = max(24, split_x - domain_col_w)
    right_x = min(w - 1, split_x + domain_col_w + 1)
    right_w = max(18, w - right_x)

    right_top_h = int(round(panel_h * 0.58))
    right_top_h = max(8, min(right_top_h, panel_h - 6))
    right_bottom_y = panel_top + right_top_h
    right_bottom_h = panel_h - right_top_h
    if right_bottom_h < 5:
        return

    box_attr = curses.color_pair(colors.get("SRC", 0))
    # 领域列盒子
    _draw_box(stdscr, domain_x, panel_top, domain_col_w, panel_h, box_attr)
    # 候选池盒子（右移）
    _draw_box(stdscr, domain_col_w, panel_top, left_w, panel_h, box_attr)
    _draw_box(stdscr, right_x, panel_top, right_w, right_top_h, box_attr)
    _draw_box(stdscr, right_x, right_bottom_y, right_w, right_bottom_h, box_attr)

    # 绘制领域列标题
    _safe_addstr(stdscr, panel_top, domain_x + 1, _truncate("领域", domain_col_w - 2), curses.A_UNDERLINE)

    # 绘制领域选项
    for i, dkey in enumerate(fund_domain.keys):
        y = panel_top + 1 + i
        if y >= panel_top + panel_h - 1:
            break
        dlabel = get_domain_label(dkey)
        is_selected = dkey == fund_domain.selected_key
        display = _truncate(dlabel, domain_col_w - 2)
        if is_selected:
            _safe_addstr(stdscr, y, domain_x + 1, display, curses.A_REVERSE | curses.color_pair(colors.get("HIGHLIGHT", 0)))
        else:
            _safe_addstr(stdscr, y, domain_x + 1, display)

    left_inner_w = max(0, left_w - 2)
    _safe_addstr(stdscr, panel_top, domain_col_w + 2, _truncate(f"候选池({len(symbols)})", max(0, left_w - 4)), curses.A_UNDERLINE)

    if left_inner_w >= 55:
        table_cols: list[tuple[str, str, int, str]] = [
            ("cand", "候序", 4, "right"),
            ("code", "代码", 10, "left"),
            ("name", "名称", 1, "left"),  # width is resolved dynamically
            ("last", "最新", 7, "right"),
            ("pct", "涨跌", 7, "right"),
            ("rank", "MRank", 5, "right"),
            ("score", "sRank", 6, "right"),
        ]
    elif left_inner_w >= 38:
        table_cols = [
            ("cand", "候序", 4, "right"),
            ("code", "代码", 10, "left"),
            ("name", "名称", 1, "left"),
            ("rank", "MRank", 5, "right"),
            ("score", "sRank", 6, "right"),
        ]
    elif left_inner_w >= 30:
        table_cols = [
            ("cand", "候", 3, "right"),
            ("code", "代码", 8, "left"),
            ("name", "名称", 1, "left"),
            ("rank", "MRk", 4, "right"),
            ("score", "sRk", 5, "right"),
        ]
    else:
        table_cols = [
            ("cand", "候", 3, "right"),
            ("name", "名称", 1, "left"),
            ("rank", "MRk", 4, "right"),
            ("score", "sRk", 5, "right"),
        ]

    fixed_w = sum(width for key, _, width, _ in table_cols if key != "name")
    field_count = len(table_cols)
    # Row layout = prefix(1) + leading blank(1) + fields + blanks between fields.
    overhead_w = 2 + max(0, field_count - 1)
    resolved_name_w = max(1, left_inner_w - fixed_w - overhead_w)
    resolved_cols: list[tuple[str, str, int, str]] = []
    for key, header, width, align in table_cols:
        if key == "name":
            resolved_cols.append((key, header, resolved_name_w, align))
        else:
            resolved_cols.append((key, header, width, align))

    def _render_left_row(prefix: str, values: dict[str, str]) -> str:
        cells = [_fit_cell(values.get(key, ""), width, align=align) for key, _, width, align in resolved_cols]
        body = " ".join(cells)
        return _fit_cell(f"{prefix} {body}", left_inner_w)

    header_values = {key: header for key, header, _, _ in resolved_cols}
    _safe_addstr(stdscr, panel_top + 1, domain_col_w + 1, _render_left_row(" ", header_values), curses.A_UNDERLINE)

    left_body_top = panel_top + 2
    left_body_h = max(0, panel_h - 3)
    pane.left_scroll = min(max(0, pane.left_scroll), max(0, len(symbols) - 1))
    if pane.selected < pane.left_scroll:
        pane.left_scroll = pane.selected
    if pane.selected >= pane.left_scroll + max(1, left_body_h):
        pane.left_scroll = max(0, pane.selected - max(1, left_body_h) + 1)

    left_visible = symbols[pane.left_scroll : pane.left_scroll + max(1, left_body_h)]
    for i, sym in enumerate(left_visible):
        y = left_body_top + i
        candidate_rank = candidate_rank_map.get(sym)
        st = quote_state.entries.get(sym) or QuoteEntryState(quote=None, last_error="pending", last_fetch_at=0.0)
        q = st.quote
        name = _display_name(sym, q, quote_cfg.market)
        code = _display_symbol(sym, quote_cfg.market)
        prefix = ">" if (pane.left_scroll + i) == pane.selected else " "
        rank = model_rank_map.get(sym)
        item = model_item_map.get(sym)
        rank_txt = f"#{rank}" if rank is not None else "--"
        score_txt = f"{item.total_score:.1f}" if item is not None else "--"
        cand_txt = str(candidate_rank) if candidate_rank is not None else "--"
        last_txt = "--"
        pct_txt = "--"
        if q is not None:
            chg = q.price - q.prev_close
            pct = (chg / q.prev_close * 100.0) if q.prev_close else 0.0
            last_txt = f"{q.price:.2f}"
            pct_txt = f"{pct:+.2f}%"

        row_values = {
            "cand": cand_txt,
            "code": code,
            "name": name,
            "last": last_txt,
            "pct": pct_txt,
            "rank": rank_txt,
            "score": score_txt,
        }
        _safe_addstr(stdscr, y, domain_col_w + 1, _render_left_row(prefix, row_values))

    selected_label = _display_name(selected_symbol, selected_quote, quote_cfg.market)
    selected_disp_symbol = _display_symbol(selected_symbol, quote_cfg.market)
    _safe_addstr(
        stdscr,
        panel_top,
        right_x + 2,
        _truncate(f"票详情: {selected_label} ({selected_disp_symbol})", max(0, right_w - 4)),
        curses.A_UNDERLINE,
    )
    if selected_quote is None:
        stats_line = "价格=--  涨跌=--  幅度=--  成交量=--  延迟=--  模式=--"
    else:
        selected_chg = selected_quote.price - selected_quote.prev_close
        selected_pct = (selected_chg / selected_quote.prev_close * 100.0) if selected_quote.prev_close else 0.0
        selected_age_s = int(max(0.0, time.time() - (selected_state.last_fetch_at or 0.0))) if selected_state else 0

        curve_mode = "LIVE"
        quote_ts_dt = parse_ts(selected_quote.ts)
        if quote_ts_dt != datetime.min:
            quote_age_s = max(0, int(time.time() - quote_ts_dt.timestamp()))
            if quote_age_s >= _CLOSED_CURVE_STALE_SECONDS:
                curve_span_s = 0.0
                if len(selected_curve) >= 2:
                    curve_span_s = max(0.0, float(selected_curve[-1].ts_open - selected_curve[0].ts_open))
                target_days_span_s = max(24 * 3600, (_FUND_CN_CURVE_DAYS - 1) * 24 * 3600)
                if curve_span_s >= target_days_span_s:
                    curve_mode = f"CLOSE-{_FUND_CN_CURVE_DAYS}D"
                elif curve_span_s >= _CLOSED_CURVE_TARGET_SPAN_SECONDS:
                    curve_mode = "CLOSE-1H"
                else:
                    curve_mode = "CLOSE"

        stats_line = (
            f"价格={selected_quote.price:.2f}  涨跌={selected_chg:+.2f} ({selected_pct:+.2f}%)  "
            f"成交量={_fmt_vol(selected_quote.volume)}  延迟={selected_age_s}s  模式={curve_mode}  周期={_FUND_CN_CURVE_DAYS}D"
        )
    _safe_addstr(stdscr, panel_top + 1, right_x + 1, _truncate(stats_line, max(0, right_w - 2)))

    chart_y = panel_top + 2
    chart_h = max(1, right_top_h - 3)
    _draw_price_curve(
        stdscr,
        selected_curve,
        colors,
        right_x + 1,
        chart_y,
        right_w - 2,
        chart_h,
        marker_rows=selected_rows,
    )

    _safe_addstr(stdscr, right_bottom_y, right_x + 2, _truncate("选票信息 / TopN(领域优先+模型)", max(0, right_w - 4)), curses.A_UNDERLINE)
    details: list[str] = []
    details.append("口径说明: cnd=领域相关序 | MRank=模型排名 | sRank=模型总分")
    details.append("结论清单(编号+代码+名称+角色):")
    role_names = ("主选", "备选", "观察")
    conclusion_role_map: dict[str, str] = {}
    for idx, sym in enumerate(domain_top_symbols[: len(role_names)], start=1):
        role_name = role_names[idx - 1]
        conclusion_role_map[sym] = role_name
        rank_state = quote_state.entries.get(sym)
        code = _display_symbol(sym, quote_cfg.market)
        name = _display_name(sym, rank_state.quote if rank_state else None, quote_cfg.market)
        details.append(f"{idx}. {code} {name} | 角色={role_name}")
    if not conclusion_role_map:
        details.append("当前领域暂无可用结论清单")
    role = conclusion_role_map.get(selected_symbol)
    if role is not None:
        details.append(f"当前票定位: {role}（{domain_label}结论清单）")
    else:
        details.append(f"当前票定位: 非结论清单（{domain_label}候选池）")

    if selected_item is None:
        details.append("当前状态: 暂无模型评分（数据缺失/过期）")
        details.append("提示: 可按 r 刷新，或等待行情更新")
    else:
        risk_txt = risk_map.get(selected_item.risk_level, selected_item.risk_level)
        cand_rank_txt = candidate_rank_map.get(selected_symbol)
        cand_rank_disp = str(cand_rank_txt) if cand_rank_txt is not None else "--"
        model_rank_disp = f"#{selected_rank}/{ranking_snapshot.valid_candidates}" if selected_rank is not None else "--"
        details.append(
            f"当前票: cnd={cand_rank_disp}  MRank={model_rank_disp}  sRank={selected_item.total_score:.1f}  风险={risk_txt}"
        )
        if cand_rank_txt is not None and selected_rank is not None and cand_rank_txt != selected_rank:
            details.append("提示: cnd 与 MRank 不必一致（相关度 vs 交易评分）")
        if selected_rank is not None and selected_rank > top_n_limit:
            details.append(f"前{top_n_limit}: 未入选（当前模型排名偏后）")
        details.append(
            f"因子: 趋势{selected_item.trend_score:.1f} 动量{selected_item.momentum_score:.1f} "
            f"流动{selected_item.liquidity_score:.1f} 风险{selected_item.risk_adjusted_score:.1f}"
        )
        details.append(f"标签: {' / '.join(selected_item.reason_tags[:3])}")

    if selected_rows:
        latest = selected_rows[0]
        latest_dir = (latest.direction or "--").upper()[:4]
        details.append(f"近期信号: {len(selected_rows)} | 最新={_fmt_time(latest.timestamp)} {latest_dir}")
    else:
        details.append("近期信号: 0（基金页以选票为主，信号仅作参考）")

    details.append(f"Top{top_n_limit}({domain_label}相关序):")
    for idx, sym in enumerate(domain_top_symbols, start=1):
        rank_state = quote_state.entries.get(sym)
        rank_name = _display_name(sym, rank_state.quote if rank_state else None, quote_cfg.market)
        rank_symbol = _display_symbol(sym, quote_cfg.market)
        item = model_item_map.get(sym)
        rank = model_rank_map.get(sym)
        rank_txt = f"#{rank}" if rank is not None else "--"
        score_txt = f"{item.total_score:.1f}" if item is not None else "--"
        details.append(f"{idx}. {rank_symbol:<10} {rank_name:<12} MRank={rank_txt} sRank={score_txt}")

    details.append(f"Top{top_n_limit}(模型评分,全池):")
    for idx, item in enumerate(top_items, start=1):
        rank_symbol = _display_symbol(item.symbol, quote_cfg.market)
        rank_state = quote_state.entries.get(item.symbol)
        rank_name = _display_name(item.symbol, rank_state.quote if rank_state else None, quote_cfg.market)
        risk_txt = risk_map.get(item.risk_level, item.risk_level)
        details.append(f"{idx}. {rank_symbol:<10} {rank_name:<12} sRank={item.total_score:>5.1f} 风险={risk_txt}")

    right_body_h = max(0, right_bottom_h - 2)
    for i, line in enumerate(details[: max(1, right_body_h)]):
        _safe_addstr(stdscr, right_bottom_y + 1 + i, right_x + 1, _truncate(line, max(0, right_w - 2)))

    # Keep footer row for key hints.


def _draw_backtest_curve(
    stdscr,
    values: list[float],
    colors: dict[str, int],
    x0: int,
    y0: int,
    width: int,
    height: int,
) -> None:
    if width <= 14 or height <= 6 or len(values) < 2:
        return

    label_w = 10
    if width <= label_w + 6:
        label_w = max(7, width - 6)
    if width <= label_w + 3:
        return

    chart_x0 = x0 + label_w
    chart_w = max(3, width - label_w)

    samples = _resample_series(values, max_points=chart_w)
    if len(samples) < 2:
        return

    low = min(samples)
    high = max(samples)
    span0 = high - low

    # Avoid over-amplifying tiny returns: keep the y-range at least +/-1% around the initial equity.
    # Otherwise a -0.5% move can look like a "crash" because we stretch min/max to the full chart height.
    baseline = float(values[0]) if values else float(samples[0])
    min_span = max(1e-9, abs(baseline) * 0.02)  # >=2% span around baseline
    if span0 < min_span:
        low = min(low, baseline - min_span / 2.0)
        high = max(high, baseline + min_span / 2.0)
        span0 = high - low

    pad = max(1e-9, abs(high) * 0.001, span0 * 0.04)
    low -= pad
    high += pad
    span = max(1e-9, high - low)

    def _to_y(value: float) -> int:
        ratio = (high - value) / span
        y = y0 + int(round(ratio * max(1, height - 1)))
        return max(y0, min(y0 + height - 1, y))

    utf = "utf" in (locale.getpreferredencoding(False) or "").lower()
    guide_char = "┈" if utf else "."
    up_char = "╱" if utf else "/"
    down_char = "╲" if utf else "\\"
    flat_char = "─" if utf else "-"
    point_char = "●" if utf else "*"
    join_char = "│" if utf else "|"

    buy_attr = curses.color_pair(colors.get("BUY", 0)) | curses.A_BOLD
    sell_attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD
    neutral_attr = curses.color_pair(colors.get("SRC", 0))

    tick_rows = sorted({y0, y0 + (height - 1) // 3, y0 + ((height - 1) * 2) // 3, y0 + height - 1})
    for gy in tick_rows:
        rel = (gy - y0) / max(1, height - 1)
        val = high - span * rel
        _safe_addstr(stdscr, gy, x0, _truncate(f"{val:>9.2f}", label_w), neutral_attr)
        _safe_addstr(stdscr, gy, chart_x0, guide_char * chart_w, neutral_attr)

    prev_y: int | None = None
    for i, value in enumerate(samples):
        x = chart_x0 + i
        y = _to_y(value)
        if prev_y is not None:
            prev_value = samples[i - 1]
            attr = buy_attr if value >= prev_value else sell_attr
            if y == prev_y:
                _safe_addstr(stdscr, y, x - 1, flat_char, attr)
            else:
                _safe_addstr(stdscr, prev_y, x - 1, up_char if y < prev_y else down_char, attr)
                step = 1 if y > prev_y else -1
                for yy in range(prev_y + step, y, step):
                    _safe_addstr(stdscr, yy, x - 1, join_char, attr)
            _safe_addstr(stdscr, y, x, point_char, attr)
        else:
            _safe_addstr(stdscr, y, x, point_char, neutral_attr)
        prev_y = y


def _draw_market_backtest(stdscr, colors: dict[str, int], w: int, h: int) -> None:
    snap = _load_backtest_snapshot()
    run_state = _load_backtest_run_state()
    compare_snap = BacktestCompareSnapshot()
    if _BACKTEST_SHOW_COMPARE:
        compare_snap = _load_backtest_compare_snapshot(
            _BACKTEST_LATEST_DIR,
            run_state=run_state,
            current_run_id=snap.run_id,
        )

    _safe_addstr(stdscr, 1, 0, _truncate("回测看板[只读]: latest目录产物展示", w))
    resolved_latest = _BACKTEST_LATEST_DIR
    try:
        resolved_latest = _BACKTEST_LATEST_DIR.resolve()
    except Exception:
        resolved_latest = _BACKTEST_LATEST_DIR
    if resolved_latest != _BACKTEST_LATEST_DIR:
        path_line = f"路径: {_BACKTEST_LATEST_DIR} -> {resolved_latest}"
    else:
        path_line = f"路径: {_BACKTEST_LATEST_DIR}"
    _safe_addstr(stdscr, 2, 0, _truncate(path_line, w), curses.color_pair(colors.get("SRC", 0)))

    state_attr = curses.color_pair(colors.get("SRC", 0))
    if run_state.status == "done":
        state_attr = curses.color_pair(colors.get("BUY", 0)) | curses.A_BOLD
    elif run_state.status == "error":
        state_attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD
    _safe_addstr(stdscr, 3, 0, _format_backtest_state_line(run_state, w), state_attr)

    panel_top = 4
    panel_h = max(0, h - panel_top - 1)
    if panel_h < 12:
        _safe_addstr(stdscr, h - 1, 0, _truncate("回测页: 终端高度不足（建议>=26行）", w))
        return

    # Readability-first split: keep right metrics area larger than before.
    split_x = max(40, int(w * 0.55))
    if split_x >= w - 34:
        split_x = max(24, w - 34)

    left_w = max(24, split_x)
    right_x = min(w - 1, split_x + 1)
    right_w = max(16, w - right_x)

    box_attr = curses.color_pair(colors.get("SRC", 0))
    _draw_box(stdscr, 0, panel_top, left_w, panel_h, box_attr)
    _draw_box(stdscr, right_x, panel_top, right_w, panel_h, box_attr)

    left_inner_w = max(0, left_w - 2)
    right_inner_w = max(0, right_w - 2)

    run_title = f"权益曲线 | run={snap.run_id}"
    if snap.is_walk_forward:
        run_title = f"Walk-Forward | run={snap.run_id}"
    _safe_addstr(stdscr, panel_top, 2, _truncate(run_title, max(0, left_w - 4)), curses.A_UNDERLINE)

    left_body_top = panel_top + 1
    left_bottom = panel_top + panel_h - 2

    _safe_addstr(
        stdscr,
        left_body_top,
        1,
        _truncate(f"区间: {snap.date_range}", left_inner_w),
        curses.color_pair(colors.get("SRC", 0)),
    )
    chart_desc = "主图: 权益折线（下方回撤带用于判断风险阶段）"
    if snap.is_walk_forward:
        chart_desc = "主图: Walk-Forward 累计折线（每点代表一折）"
    _safe_addstr(
        stdscr,
        left_body_top + 1,
        1,
        _truncate(chart_desc, left_inner_w),
        curses.color_pair(colors.get("SRC", 0)),
    )

    summary_y = left_bottom
    drawdown_y: int | None = left_bottom - 1
    divider_y = left_bottom - 2
    chart_y = left_body_top + 2
    chart_h = divider_y - chart_y + 1

    if chart_h < 6:
        drawdown_y = None
        divider_y = left_bottom - 1
        chart_h = divider_y - chart_y + 1

    if chart_h < 3:
        chart_y = left_body_top + 1
        divider_y = None
        drawdown_y = None
        chart_h = max(1, left_bottom - chart_y)

    if not snap.available:
        if run_state.status == "running":
            missing_lines = [
                "回测正在运行，等待产物输出...",
                f"阶段: {_backtest_state_stage_text(run_state.stage)}",
                f"run: {run_state.run_id}",
                "可稍后按 r 刷新",
            ]
        elif run_state.status == "error":
            missing_lines = [
                "回测失败，暂无可读产物",
                f"阶段: {_backtest_state_stage_text(run_state.stage)}",
                f"错误: {run_state.error or '--'}",
                "修复后重试: ./scripts/backtest.sh",
            ]
        else:
            missing_lines = [
                "暂无回测结果",
                "先运行 signal-service 回测脚本",
                "需要文件: metrics.json/equity_curve.csv/trades.csv",
                "执行: ./scripts/backtest.sh",
            ]

        max_rows = max(1, chart_h)
        for i, line in enumerate(missing_lines[:max_rows]):
            _safe_addstr(
                stdscr,
                chart_y + i,
                1,
                _truncate(line, left_inner_w),
                curses.color_pair(colors.get("SRC", 0)),
            )
    else:
        _draw_backtest_curve(stdscr, snap.equity_points, colors, 1, chart_y, left_inner_w, chart_h)

    if divider_y is not None and divider_y >= chart_y:
        axis = _format_backtest_time_axis(snap.date_range, left_inner_w)
        _safe_addstr(stdscr, divider_y, 1, _truncate(axis, left_inner_w), curses.color_pair(colors.get("SRC", 0)))
    if drawdown_y is not None and drawdown_y < summary_y:
        _draw_backtest_drawdown_strip(stdscr, snap.equity_points, colors, 1, drawdown_y, left_inner_w)

    summary_line = _format_backtest_curve_summary(snap.equity_points)
    _safe_addstr(stdscr, summary_y, 1, _truncate(summary_line, left_inner_w), curses.color_pair(colors.get("SRC", 0)))

    def _fmt_pct(value: float | None, *, signed: bool = False) -> str:
        if value is None:
            return "--"
        return f"{value:+.2f}%" if signed else f"{value:.2f}%"

    def _fmt_num(value: float | None) -> str:
        return "--" if value is None else f"{value:.2f}"

    def _fmt_int(value: int | None) -> str:
        return "--" if value is None else str(value)

    def _fmt_delta_int(value: int | None) -> str:
        return "--" if value is None else f"{value:+d}"

    def _interpret_text() -> str:
        if snap.is_walk_forward:
            if (snap.wf_fold_count or 0) <= 0:
                return "解读: Walk-Forward 折数不足，先检查窗口配置"
            if snap.total_return_pct is None or snap.max_drawdown_pct is None:
                return "解读: 折均指标不足，先补齐完整窗口"
            if (snap.excess_return_pct or 0.0) >= 0 and snap.max_drawdown_pct <= 5:
                return "解读: 折均超额为正且回撤可控，可进入参数稳健性验证"
            if snap.total_return_pct < 0 and snap.max_drawdown_pct >= 15:
                return "解读: 折均收益偏弱且回撤偏大，建议提高阈值并降频"
            return "解读: 当前是折均结果，建议结合下方各折明细再决策"

        if snap.total_return_pct is None or snap.max_drawdown_pct is None:
            return "解读: 数据不足，先补齐一轮完整回测"
        if snap.total_return_pct < 0 and snap.max_drawdown_pct >= 20:
            return "解读: 当前处于亏损+深回撤阶段，建议先降频再调参"
        if snap.total_return_pct >= 0 and snap.max_drawdown_pct <= 15:
            return "解读: 收益/回撤均可接受，可继续做稳健性验证"
        return "解读: 收益与风险不匹配，重点看币种贡献与交易明细"

    status_map = {
        "ok": "正常",
        "partial": "部分可用",
        "metrics.json parse failed": "metrics.json 解析失败",
        "no backtest artifacts": "暂无回测产物",
        "no backtest artifacts yet": "暂无回测产物",
    }
    status_text = status_map.get(snap.status, snap.status)

    run_status_txt = _backtest_state_status_text(run_state.status)
    run_stage_txt = _backtest_state_stage_text(run_state.stage)
    run_status_attr = curses.color_pair(colors.get("SRC", 0))
    if run_state.status == "done":
        run_status_attr = curses.color_pair(colors.get("BUY", 0)) | curses.A_BOLD
    elif run_state.status == "error":
        run_status_attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD

    row = panel_top
    max_row = panel_top + panel_h - 1

    def _section(title: str) -> bool:
        nonlocal row
        if row >= max_row:
            return False
        _safe_addstr(stdscr, row, right_x + 2, _truncate(title, max(0, right_w - 4)), curses.A_UNDERLINE)
        row += 1
        return row < max_row

    def _line(text: str, attr: int = 0) -> bool:
        nonlocal row
        if row >= max_row:
            return False
        _safe_addstr(stdscr, row, right_x + 1, _truncate(text, right_inner_w), attr)
        row += 1
        return row < max_row

    _section("核心指标")
    _line(f"运行状态: {run_status_txt} | 阶段: {run_stage_txt}", run_status_attr)
    if run_state.run_id != "--":
        _line(f"当前run: {run_state.run_id}")
    if snap.mode != "--":
        _line(f"产物模式: {_backtest_mode_text(snap.mode)}")
    if snap.strategy_label != "--":
        _line(f"策略: {snap.strategy_label}")
    if snap.strategy_summary:
        _line(f"策略参数: {snap.strategy_summary}")
    if _BACKTEST_SHOW_COMPARE and run_state.mode != "--" and run_state.mode != snap.mode:
        _line(f"命令模式: {_backtest_mode_text(run_state.mode)}")
    _line(f"产物状态: {status_text}")
    _line(f"收益率: {_fmt_pct(snap.total_return_pct, signed=True)} | 最大回撤: {_fmt_pct(snap.max_drawdown_pct)}")
    _line(f"夏普: {_fmt_num(snap.sharpe)} | 胜率: {_fmt_pct(snap.win_rate_pct)}")
    trade_count_txt = "--" if snap.trade_count is None else str(snap.trade_count)
    avg_hold_txt = "--" if snap.avg_holding_minutes is None else f"{snap.avg_holding_minutes:.2f}m"
    _line(f"交易数: {trade_count_txt} | 平均持仓: {avg_hold_txt}")
    _line(
        f"基准(BH): {_fmt_pct(snap.buy_hold_return_pct, signed=True)} | 超额: {_fmt_pct(snap.excess_return_pct, signed=True)}"
    )
    if snap.quality_score is not None or snap.quality_status != "--":
        _line(
            f"质量分: {_fmt_num(snap.quality_score)} | 质量状态: {(snap.quality_status or '--').upper()}"
        )
        if snap.quality_summary:
            _line(f"质量摘要: {snap.quality_summary}")
    if snap.stability_status != "--":
        _line(
            f"稳定性: {(snap.stability_status or '--').upper()} | 可比run: {snap.stability_comparable_run_count}"
        )
        if snap.stability_summary:
            _line(f"稳定性摘要: {snap.stability_summary}")
    if snap.is_walk_forward:
        fold_txt = "--" if snap.wf_fold_count is None else str(snap.wf_fold_count)
        pos_txt = _fmt_pct(snap.wf_positive_fold_rate_pct)
        hist_txt = "--" if snap.wf_history_fold_count is None else str(snap.wf_history_fold_count)
        replay_txt = "--" if snap.wf_replay_fold_count is None else str(snap.wf_replay_fold_count)
        fallback_txt = "--" if snap.wf_fallback_fold_count is None else str(snap.wf_fallback_fold_count)
        _line(f"WF折数: {fold_txt} | 正收益折比: {pos_txt}")
        _line(f"WF来源: history={hist_txt} replay={replay_txt} fallback={fallback_txt}")

    if run_state.status == "error" and run_state.error and row < max_row:
        _line(f"错误: {run_state.error}", curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD)
    elif run_state.message and row < max_row:
        msg = run_state.message
        if (not _BACKTEST_SHOW_COMPARE) and run_state.mode == "compare_history_rule":
            msg = "compare done"
        _line(f"消息: {msg}", curses.color_pair(colors.get("SRC", 0)))

    if row < max_row:
        _safe_hline(stdscr, row, right_x + 1, right_inner_w, box_attr)
        row += 1

    _section("风险解读")
    _line(_format_backtest_drawdown_summary(snap.equity_points))
    _line(_interpret_text(), curses.color_pair(colors.get("SRC", 0)))
    if snap.quality_status in {"warn", "fail"} and row < max_row:
        _line(
            "检查建议: ./scripts/backtest.sh --check-only",
            curses.color_pair(colors.get("ALERT", 0)),
        )
    if snap.stability_status in {"warn", "critical"} and row < max_row:
        _line(
            "稳定性建议: ./scripts/backtest.sh --walk-forward --walk-forward-max-folds 6",
            curses.color_pair(colors.get("ALERT", 0)),
        )
    if compare_snap.available and compare_snap.alignment_risk_level in {"high", "critical"} and row < max_row:
        _line(
            "对齐建议: ./scripts/backtest.sh --mode compare_history_rule --alignment-min-score 70 --alignment-max-risk-level medium",
            curses.color_pair(colors.get("ALERT", 0)),
        )

    if row < max_row:
        _safe_hline(stdscr, row, right_x + 1, right_inner_w, box_attr)
        row += 1

    if compare_snap.available and (not snap.is_walk_forward):
        _section("模式对比（history vs rule）")
        _line(f"对比run: {compare_snap.run_id}")
        status_text = (compare_snap.alignment_status or "--").upper()
        status_attr = curses.color_pair(colors.get("SRC", 0))
        if status_text == "PASS":
            status_attr = curses.color_pair(colors.get("BUY", 0))
        elif status_text == "FAIL":
            status_attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD
        elif status_text == "WARN":
            status_attr = curses.color_pair(colors.get("ALERT", 0))
        risk_text = (compare_snap.alignment_risk_level or "--").upper()
        _line(
            f"对齐分: {_fmt_num(compare_snap.alignment_score)} / 100 | 状态: {status_text} | "
            f"风险: {risk_text} | 告警: {compare_snap.alignment_warning_count}",
            status_attr,
        )
        if compare_snap.alignment_risk_summary:
            _line(f"风险说明: {compare_snap.alignment_risk_summary}")
        _line(
            f"规则重合: {_fmt_int(compare_snap.rule_shared_types)}/"
            f"{_fmt_int(compare_snap.rule_history_types)}/"
            f"{_fmt_int(compare_snap.rule_rule_types)} | "
            f"Jaccard: {_fmt_pct(compare_snap.rule_jaccard_pct)}"
        )
        _line(
            f"收益差: {_fmt_pct(compare_snap.delta_return_pct, signed=True)} | "
            f"信号差: {_fmt_delta_int(compare_snap.delta_signal_count)}"
        )
        _line(
            f"交易差: {_fmt_delta_int(compare_snap.delta_trade_count)} | "
            f"超额差: {_fmt_pct(compare_snap.delta_excess_return_pct, signed=True)}"
        )
        _line(f"买入占比差: {_fmt_pct(compare_snap.delta_buy_ratio_pct, signed=True)}")
        if compare_snap.alignment_warning_summary and row < max_row:
            _line(f"主告警: {compare_snap.alignment_warning_summary}")
        if compare_snap.missing_rule_reason and row < max_row:
            _line(f"缺失主因: {compare_snap.missing_rule_reason}")
        if compare_snap.signal_type_delta_top and row < max_row:
            _line("命中差异前列:")
            for item in compare_snap.signal_type_delta_top:
                if row >= max_row:
                    break
                _line(
                    f"- {item.key} {item.history_count}->{item.rule_count} "
                    f"({item.delta:+d})"
                )

        if row < max_row:
            _safe_hline(stdscr, row, right_x + 1, right_inner_w, box_attr)
            row += 1

    contrib_title = "币种贡献（红盈绿亏）" if not snap.is_walk_forward else "Walk-Forward统计"
    _section(contrib_title)
    if snap.is_walk_forward:
        hist_txt = "--" if snap.wf_history_fold_count is None else str(snap.wf_history_fold_count)
        replay_txt = "--" if snap.wf_replay_fold_count is None else str(snap.wf_replay_fold_count)
        fallback_txt = "--" if snap.wf_fallback_fold_count is None else str(snap.wf_fallback_fold_count)
        _line(f"history折: {hist_txt} | replay折: {replay_txt}")
        _line(f"fallback折: {fallback_txt}")
    else:
        contrib_lines = _format_symbol_contrib_lines(snap.symbol_contributions, right_inner_w)
        for line, sign in contrib_lines:
            if row >= max_row:
                break
            attr = 0
            if sign > 0:
                attr = curses.color_pair(colors.get("BUY", 0)) | curses.A_BOLD
            elif sign < 0:
                attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD
            _line(line, attr)

    if row < max_row:
        _safe_hline(stdscr, row, right_x + 1, right_inner_w, box_attr)
        row += 1

    trade_section_title = "最近平仓" if not snap.is_walk_forward else "最近折结果"
    _section(trade_section_title)
    trade_lines = snap.recent_trades or ["--"]
    for raw in trade_lines:
        if row >= max_row:
            break
        if snap.is_walk_forward:
            _line(_truncate(raw, right_inner_w))
        else:
            _line(_format_backtest_trade_line(raw, right_inner_w))

    if row < max_row:
        hint = "提示: 读图顺序=收益/回撤 -> 币种贡献 -> 最近交易"
        if compare_snap.available and (not snap.is_walk_forward):
            hint = "提示: 读图顺序=收益/回撤 -> 模式对比 -> 币种贡献"
        if snap.is_walk_forward:
            hint = "提示: 读图顺序=折均指标 -> 折来源 -> 最近折结果"
        _line(hint, curses.color_pair(colors.get("SRC", 0)))

    footer = "回测页: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 4返回主页面 | 5基金 | 6港股 | 7资讯 | r刷新"
    _safe_addstr(stdscr, h - 1, 0, _truncate(footer, w))


def _draw_market_micro(
    stdscr,
    snapshot: MicroSnapshot,
    micro_symbols: list[str],
    rows: list[SignalRow],
    quote_state: QuoteBookState,
    curve_map: dict[str, list[Candle]],
    colors: dict[str, int],
    w: int,
    h: int,
) -> None:
    key_hint = "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 4回测切换 | 5基金 | 6港股 | 7资讯 | [/]切换标的 | r刷新"
    _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))

    symbols = [s.strip().upper() for s in (micro_symbols or []) if (s or "").strip()]
    focus_symbol = (snapshot.symbol or "").strip().upper()
    if focus_symbol and focus_symbol not in symbols:
        symbols.insert(0, focus_symbol)

    if not symbols:
        _safe_addstr(stdscr, 1, 0, _truncate("加密行情：无可用标的（可用 + 添加）", w))
        return

    selected_symbol = focus_symbol if focus_symbol in symbols else symbols[0]
    selected_idx = symbols.index(selected_symbol)
    selected_rows = _signals_for_symbol(rows, selected_symbol, "crypto_spot")
    selected_state = quote_state.entries.get(selected_symbol)
    selected_quote = selected_state.quote if selected_state else None
    selected_curve = curve_map.get(selected_symbol, [])

    panel_top = 1
    panel_h = max(0, h - panel_top - 1)
    if panel_h < 10:
        return

    left_min_w = _adaptive_left_min_width(
        w,
        base_min=_MARKET_MICRO_LEFT_BASE_MIN_WIDTH,
        floor_min=_MARKET_MICRO_LEFT_FLOOR_MIN_WIDTH,
        min_ratio=_MARKET_MICRO_LEFT_MIN_RATIO,
    )
    split_x = max(left_min_w, int(w * _MARKET_MICRO_LEFT_RATIO))
    if split_x >= w - _MARKET_MICRO_RIGHT_MIN_WIDTH:
        split_x = max(left_min_w, w - _MARKET_MICRO_RIGHT_MIN_WIDTH)
    left_w = max(left_min_w, split_x)
    right_x = min(w - 1, split_x + 1)
    right_w = max(18, w - right_x)

    right_top_h = int(round(panel_h * 0.62))
    right_top_h = max(8, min(right_top_h, panel_h - 6))
    right_bottom_y = panel_top + right_top_h
    right_bottom_h = panel_h - right_top_h
    if right_bottom_h < 5:
        return

    box_attr = curses.color_pair(colors.get("SRC", 0))
    _draw_box(stdscr, 0, panel_top, left_w, panel_h, box_attr)
    _draw_box(stdscr, right_x, panel_top, right_w, right_top_h, box_attr)
    _draw_box(stdscr, right_x, right_bottom_y, right_w, right_bottom_h, box_attr)

    left_inner_w = max(0, left_w - 2)
    _safe_addstr(stdscr, panel_top, 2, _truncate(f"候选池({len(symbols)})", max(0, left_w - 4)), curses.A_UNDERLINE)

    if left_inner_w >= 56:
        table_cols: list[tuple[str, str, int, str]] = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 11, "left"),
            ("name", "名称", 1, "left"),
            ("last", "最新", 7, "right"),
            ("pct", "涨跌", 7, "right"),
            ("sig", "12h", 4, "right"),
        ]
    elif left_inner_w >= 46:
        table_cols = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 11, "left"),
            ("name", "名称", 1, "left"),
            ("last", "最新", 7, "right"),
            ("pct", "涨跌", 7, "right"),
        ]
    elif left_inner_w >= 36:
        table_cols = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 9, "left"),
            ("name", "名称", 1, "left"),
            ("last", "最新", 7, "right"),
            ("pct", "涨跌", 7, "right"),
        ]
    else:
        table_cols = [
            ("idx", "序", 3, "right"),
            ("code", "代码", 9, "left"),
            ("last", "最新", 7, "right"),
        ]
        if left_inner_w >= 30:
            table_cols.append(("pct", "涨跌", 7, "right"))
    
    fixed_w = sum(width for key, _, width, _ in table_cols if key != "name")
    field_count = len(table_cols)
    overhead_w = 2 + max(0, field_count - 1)
    resolved_name_w = max(1, left_inner_w - fixed_w - overhead_w)
    resolved_cols: list[tuple[str, str, int, str]] = []
    for key, header, width, align in table_cols:
        if key == "name":
            resolved_cols.append((key, header, resolved_name_w, align))
        else:
            resolved_cols.append((key, header, width, align))

    def _render_left_row(prefix: str, values: dict[str, str]) -> str:
        cells = [_fit_cell(values.get(key, ""), width, align=align) for key, _, width, align in resolved_cols]
        body = " ".join(cells)
        return _fit_cell(f"{prefix} {body}", left_inner_w)

    header_values = {key: header for key, header, _, _ in resolved_cols}
    _safe_addstr(stdscr, panel_top + 1, 1, _render_left_row(" ", header_values), curses.A_UNDERLINE)

    left_body_top = panel_top + 2
    left_body_h = max(0, panel_h - 3)
    left_scroll = 0
    now_dt = datetime.now()
    if selected_idx >= max(1, left_body_h):
        left_scroll = selected_idx - max(1, left_body_h) + 1
    left_visible = symbols[left_scroll : left_scroll + max(1, left_body_h)]

    for i, sym in enumerate(left_visible):
        y = left_body_top + i
        global_idx = left_scroll + i
        st = quote_state.entries.get(sym) or QuoteEntryState(quote=None, last_error="pending", last_fetch_at=0.0)
        q = st.quote
        name = _display_name(sym, q, "crypto_spot")
        code = _display_symbol(sym, "crypto_spot")
        prefix = ">" if global_idx == selected_idx else " "

        last_txt = "--"
        pct_txt = "--"
        if q is not None:
            chg = q.price - q.prev_close
            pct = (chg / q.prev_close * 100.0) if q.prev_close else 0.0
            last_txt = f"{q.price:.2f}"
            pct_txt = f"{pct:+.2f}%"

        symbol_rows = _signals_for_symbol(rows, sym, "crypto_spot")
        row_values = {
            "idx": str(global_idx + 1),
            "code": code,
            "name": name,
            "last": last_txt,
            "pct": pct_txt,
            "sig": str(_count_recent_signal_rows(symbol_rows, now_dt, max_age_s=12 * 60 * 60)),
        }
        _safe_addstr(stdscr, y, 1, _render_left_row(prefix, row_values))

    selected_label = _display_name(selected_symbol, selected_quote, "crypto_spot")
    selected_disp_symbol = _display_symbol(selected_symbol, "crypto_spot")
    _safe_addstr(
        stdscr,
        panel_top,
        right_x + 2,
        _truncate(f"标的详情: {selected_label} ({selected_disp_symbol})", max(0, right_w - 4)),
        curses.A_UNDERLINE,
    )

    if selected_quote is None:
        stats_line = "价格=--  涨跌=--  幅度=--  成交量=--  延迟=--  源=--  模式=--"
    else:
        selected_chg = selected_quote.price - selected_quote.prev_close
        selected_pct = (selected_chg / selected_quote.prev_close * 100.0) if selected_quote.prev_close else 0.0
        selected_age_s = int(max(0.0, time.time() - (selected_state.last_fetch_at or 0.0))) if selected_state else 0
        src = (selected_quote.source or "--").upper()[:8]
        mode = "LIVE" if selected_age_s <= 15 else ("LIVE-SLOW" if selected_age_s <= 120 else "STALE")
        stats_line = (
            f"价格={selected_quote.price:.2f}  涨跌={selected_chg:+.2f} ({selected_pct:+.2f}%)  "
            f"成交量={_fmt_vol(selected_quote.volume)}  延迟={selected_age_s}s  源={src}  模式={mode}"
        )
        if selected_symbol == focus_symbol:
            bias = (snapshot.signals.bias or "NEUTRAL").upper()
            score = float(snapshot.signals.score)
            stats_line += f"  偏向={bias}  评分={score:+.2f}"
    _safe_addstr(stdscr, panel_top + 1, right_x + 1, _truncate(stats_line, max(0, right_w - 2)))

    chart_y = panel_top + 2
    chart_h = max(1, right_top_h - 3)
    _draw_price_curve(
        stdscr,
        selected_curve,
        colors,
        right_x + 1,
        chart_y,
        right_w - 2,
        chart_h,
        marker_rows=selected_rows,
    )

    signal_panel_title = _build_recent_signal_panel_title(selected_rows, now_dt)
    _safe_addstr(stdscr, right_bottom_y, right_x + 2, _truncate(signal_panel_title, max(0, right_w - 4)), curses.A_UNDERLINE)

    right_inner_x = right_x + 1
    right_inner_y = right_bottom_y + 1
    right_inner_w = max(0, right_w - 2)
    right_inner_h = max(0, right_bottom_h - 2)

    # Fallback to legacy single-column rendering on very narrow terminals.
    if right_inner_w < 30 or right_inner_h < 3:
        _safe_addstr(
            stdscr,
            right_inner_y,
            right_inner_x,
            _truncate("信号区过窄：请放大终端查看三列（5min/1h/12h）", right_inner_w),
            curses.color_pair(colors.get("SRC", 0)),
        )
        right_body_h = max(0, right_inner_h - 1)
        if selected_rows and right_body_h > 0:
            right_visible = selected_rows[: max(1, right_body_h)]
            for i, row in enumerate(right_visible):
                y = right_inner_y + 1 + i
                ts_dt = parse_ts(row.timestamp)
                age_s = max(0, int((now_dt - ts_dt).total_seconds())) if ts_dt != datetime.min else 0
                direction = (row.direction or "--").upper()[:4]
                tf = (row.timeframe or "--")[:3]
                strength = _safe_int(row.strength, 0)
                line = f"{_fmt_time(row.timestamp):<8} {age_s:>3}s {direction:<4}{strength:>3} {tf:<3}"
                _safe_addstr(stdscr, y, right_inner_x, _truncate(line, right_inner_w))
    else:
        col_count = 3
        sep_count = col_count - 1
        usable_w = max(3, right_inner_w - sep_count)
        col_ws = [usable_w // col_count] * col_count
        for i in range(usable_w % col_count):
            col_ws[i] += 1
        col_xs = [right_inner_x]
        for i in range(1, col_count):
            col_xs.append(col_xs[-1] + col_ws[i - 1] + 1)
        sep_xs = [col_xs[1] - 1, col_xs[2] - 1]

        for sep_x in sep_xs:
            _safe_vline(stdscr, right_inner_y, sep_x, right_inner_h, curses.color_pair(colors.get("SRC", 0)))

        realtime_rows, h1_rows, h12_rows = _split_signal_rows_by_age(selected_rows, now_dt)

        buckets: list[tuple[str, list[tuple[SignalRow, int]]]] = [
            ("实时", realtime_rows),
            ("1h", h1_rows),
            ("12h", h12_rows),
        ]

        for i, (title, bucket_rows) in enumerate(buckets):
            header = f"{title}({len(bucket_rows)})"
            _safe_addstr(
                stdscr,
                right_inner_y,
                col_xs[i],
                _fit_cell(header, col_ws[i], align="left"),
                curses.A_UNDERLINE,
            )

        body_h = max(0, right_inner_h - 1)
        for i, (_title, bucket_rows) in enumerate(buckets):
            col_x = col_xs[i]
            col_w = col_ws[i]
            if body_h <= 0:
                continue
            if not bucket_rows:
                _safe_addstr(
                    stdscr,
                    right_inner_y + 1,
                    col_x,
                    _fit_cell("暂无", col_w, align="left"),
                    curses.color_pair(colors.get("SRC", 0)),
                )
                continue

            for row_idx, (row, _age_s) in enumerate(bucket_rows[:body_h]):
                y = right_inner_y + 1 + row_idx
                direction = (row.direction or "--").upper()
                tf = (row.timeframe or "--")[:3]
                strength = _safe_int(row.strength, 0)
                if col_w >= 20:
                    line = f"{_fmt_time(row.timestamp):<8} {direction[:4]:<4}{strength:>3} {tf:<3}"
                elif col_w >= 14:
                    line = f"{_fmt_time(row.timestamp)[3:]:<5} {direction[:1]}{strength:>2} {tf:<3}"
                else:
                    line = f"{direction[:1]}{strength:>2} {_fmt_time(row.timestamp)[3:]}"

                attr = 0
                if direction.startswith("BUY"):
                    attr = curses.color_pair(colors.get("BUY", 0))
                elif direction.startswith("SELL"):
                    attr = curses.color_pair(colors.get("SELL", 0))
                elif direction.startswith("ALER"):
                    attr = curses.color_pair(colors.get("ALERT", 0))
                _safe_addstr(stdscr, y, col_x, _fit_cell(line, col_w, align="left"), attr)


def _draw_market_news(
    stdscr,
    state: NewsPageState,
    news_snapshot: NewsFeedSnapshot | None,
    quote_cfgs: QuoteConfigs,
    quote_state_us: QuoteBookState,
    quote_state_hk: QuoteBookState,
    quote_state_cn: QuoteBookState,
    quote_state_crypto: QuoteBookState,
    colors: dict[str, int],
    w: int,
    h: int,
) -> None:
    key_hint = (
        "按键: q退出 | t主页面切换 | 1美股 | 2A股 | 3加密 | 5基金 | 6港股 | 7资讯 | Tab切焦点 | "
        "/搜索 | f分类 | s来源 | w时间窗 | c清空"
    )
    _safe_addstr(stdscr, h - 1, 0, _truncate(key_hint, w))

    category = _NEWS_CATEGORIES[min(max(0, state.category_idx), len(_NEWS_CATEGORIES) - 1)]
    window_h = _NEWS_WINDOWS_H[min(max(0, state.window_idx), len(_NEWS_WINDOWS_H) - 1)]
    all_items = [] if news_snapshot is None else list(news_snapshot.items)
    source_options = _news_source_filter_options(all_items)
    state.source_idx = min(max(0, state.source_idx), max(0, len(source_options) - 1))
    source_filter = source_options[state.source_idx] if source_options else _NEWS_SOURCE_FILTER_ALL
    now_ts = time.time()
    feed_hint = "RSS(0)"
    sync_age = ""
    latest_age = ""
    health_hint = ""
    feed_err = ""
    if news_snapshot is not None:
        mode = (news_snapshot.mode or "").strip().upper() or "RSS"
        if mode == "RSS":
            feed_hint = f"RSS({len(news_snapshot.feeds)})"
        else:
            feed_hint = mode
        if news_snapshot.last_ok_at > 0:
            sync_age = _news_age_text(now_ts, news_snapshot.last_ok_at)
        if news_snapshot.latest_item_at > 0:
            latest_age = _news_age_text(now_ts, news_snapshot.latest_item_at)
        health = news_snapshot.health
        if health.available:
            health_hint = f" 健=H{int(health.healthy)}/F{int(health.failing)}/C{int(health.cooldown)}"
        feed_err = (news_snapshot.last_error or "").strip()
        if not feed_err and health.sample and (int(health.failing) > 0 or int(health.cooldown) > 0):
            feed_err = health.sample
    else:
        feed_err = "新闻源未配置"
    if feed_err:
        feed_err = _truncate(feed_err, 18)
    feed_status = f"源={feed_hint}"
    if sync_age:
        feed_status += f" 同步={sync_age}前"
    if latest_age:
        feed_status += f" 最新={latest_age}前"
    if health_hint:
        feed_status += health_hint
    if feed_err:
        feed_status += f" ERR={feed_err}"

    cmd_line = f"{feed_status}  搜索=/{state.search_query or ''}  分类=[{category}]  时间窗=[{window_h}h]"
    _safe_addstr(stdscr, 1, 0, _truncate(cmd_line, w))

    panel_top = 2
    panel_h = max(0, h - panel_top - 1)
    if panel_h < 10:
        _safe_addstr(stdscr, panel_top, 0, _truncate("资讯页: 终端高度不足（建议>=22行）", w))
        return

    top_h = int(round(panel_h * 0.66))
    top_h = max(8, min(top_h, panel_h - 5))
    bottom_y = panel_top + top_h
    bottom_h = panel_h - top_h
    if bottom_h < 4:
        return

    right_w = max(26, int(round(w * 0.28)))
    right_w = min(right_w, max(26, w - 44))
    mid_w = w - right_w
    mid_x = 0
    right_x = mid_w
    if mid_w < 44 or right_w < 26:
        return

    box_attr = curses.color_pair(colors.get("SRC", 0))
    _draw_box(stdscr, mid_x, panel_top, mid_w, top_h, box_attr)
    _draw_box(stdscr, right_x, panel_top, right_w, top_h, box_attr)
    _draw_box(stdscr, 0, bottom_y, w, bottom_h, box_attr)

    filtered_items = _filter_news_items(
        all_items,
        now_ts=now_ts,
        category=category,
        window_h=window_h,
        search_query=state.search_query,
        source_filter=source_filter,
    )
    events = _build_news_events(filtered_items)
    state.news_selected = min(max(0, state.news_selected), max(0, len(events) - 1))
    if state.news_selected < state.news_scroll:
        state.news_scroll = state.news_selected

    middle_focus = "*" if state.focus == "middle" else " "
    right_focus = "*" if state.focus == "right" else " "

    _safe_addstr(stdscr, panel_top, mid_x + 1, _truncate(f"[{middle_focus}] 新闻事件({len(events)})", mid_w - 2), curses.A_UNDERLINE)
    _safe_addstr(stdscr, panel_top, right_x + 1, _truncate(f"[{right_focus}] 事件影响", right_w - 2), curses.A_UNDERLINE)
    _safe_addstr(stdscr, bottom_y, 1, _truncate("详情", w - 2), curses.A_UNDERLINE)

    mid_inner_w = max(0, mid_w - 2)
    mid_body_top = panel_top + 1
    mid_body_h = max(0, top_h - 2)
    if mid_body_h <= 0:
        return

    mid_header = "时间     源   类别 强度 聚合    标题"
    _safe_addstr(stdscr, mid_body_top, mid_x + 1, _truncate(mid_header, mid_inner_w), curses.A_UNDERLINE)
    list_h = max(1, mid_body_h - 1)
    state.news_scroll = min(max(0, state.news_scroll), max(0, len(events) - 1))
    if state.news_selected >= state.news_scroll + list_h:
        state.news_scroll = state.news_selected - list_h + 1
    visible_events = events[state.news_scroll : state.news_scroll + list_h]

    if not visible_events:
        msg = "暂无匹配事件（可按 c 清空搜索）"
        if news_snapshot is not None:
            mode_label = (news_snapshot.mode or "").strip().upper() or "NEWS"
            if (news_snapshot.last_error or "").strip():
                msg = f"{mode_label} 暂无数据/拉取失败: {_truncate(news_snapshot.last_error, 26)}（可按 c 清空搜索）"
            elif news_snapshot.items:
                msg = "当前筛选条件下暂无匹配事件（可按 c 清空搜索）"
            else:
                msg = f"{mode_label} 暂无数据（等待刷新）"
        _safe_addstr(
            stdscr,
            mid_body_top + 1,
            mid_x + 1,
            _truncate(msg, mid_inner_w),
            curses.color_pair(colors.get("SRC", 0)),
        )
    for i, event in enumerate(visible_events):
        y = mid_body_top + 1 + i
        global_idx = state.news_scroll + i
        prefix = ">" if global_idx == state.news_selected else " "
        merge_hint = f"{event.article_count}条/{event.source_count}源"
        title = _truncate(event.primary_title, max(8, mid_inner_w - 34))
        line = (
            f"{prefix}{_news_time_text(event.last_updated_at):<8} "
            f"{event.primary_source[:4]:<4} {event.category[:2]:<2} {event.severity:<4} {merge_hint:<7} {title}"
        )
        attr = 0
        if event.severity == "HIGH":
            attr = curses.color_pair(colors.get("SELL", 0)) | curses.A_BOLD
        elif event.severity == "MID":
            attr = curses.color_pair(colors.get("ALERT", 0))
        else:
            attr = curses.color_pair(colors.get("SRC", 0))
        if global_idx == state.news_selected and state.focus == "middle":
            attr |= curses.A_REVERSE
        _safe_addstr(stdscr, y, mid_x + 1, _truncate(line, mid_inner_w), attr)

    selected_event = events[state.news_selected] if events else None
    right_inner_w = max(0, right_w - 2)
    right_row = panel_top + 1
    if selected_event is None:
        _safe_addstr(stdscr, right_row, right_x + 1, _truncate("暂无选中事件", right_inner_w), curses.color_pair(colors.get("SRC", 0)))
    else:
        lines = [
            f"事件等级: {selected_event.severity}",
            f"方向偏向: {selected_event.direction}",
            (
                f"代表源: {selected_event.primary_source}  "
                f"分组: {_news_source_group_label(selected_event.source_group, selected_event.source_tier)}  分类: {selected_event.category}"
            ),
            f"首次出现: {_news_age_text(now_ts, selected_event.first_seen_at)}前",
            f"最近更新: {_news_age_text(now_ts, selected_event.last_updated_at)}前",
            f"聚合规模: {selected_event.article_count}条 / {selected_event.source_count}源",
            f"Top来源: {', '.join(selected_event.top_sources[:4]) or '--'}",
            f"相关标的: {', '.join(selected_event.symbols[:4]) or '--'}",
            f"影响资产: {', '.join(selected_event.impact_assets[:4]) or '--'}",
            f"置信度: {selected_event.confidence:.2f}",
            f"建议动作: {selected_event.suggestion or '--'}",
        ]
        for line in lines:
            if right_row >= panel_top + top_h - 1:
                break
            _safe_addstr(stdscr, right_row, right_x + 1, _truncate(line, right_inner_w))
            right_row += 1
    detail_inner_w = max(0, w - 2)
    detail_top = bottom_y + 1
    detail_h = max(0, bottom_h - 2)
    if detail_h <= 0:
        return
    if selected_event is None:
        _safe_addstr(stdscr, detail_top, 1, _truncate("无事件详情", detail_inner_w), curses.color_pair(colors.get("SRC", 0)))
        return

    detail_lines = [
        f"标题: {selected_event.primary_title}",
        f"摘要: {selected_event.primary_summary or '--'}",
        (
            f"来源: {selected_event.primary_source}   首次: {_news_time_text(selected_event.first_seen_at)}   "
            f"更新: {_news_time_text(selected_event.last_updated_at)}"
        ),
        f"聚类: {selected_event.article_count}条 / {selected_event.source_count}源   Top={', '.join(selected_event.top_sources[:5]) or '--'}",
        f"URL: {selected_event.primary_url or '--'}",
        (
            f"标签: [{selected_event.category}][{selected_event.severity}] "
            f"symbols={','.join(selected_event.symbols[:5]) or '--'} "
            f"impact={','.join(selected_event.impact_assets[:5]) or '--'}"
        ),
    ]
    for i, line in enumerate(detail_lines[:detail_h]):
        _safe_addstr(stdscr, detail_top + i, 1, _truncate(line, detail_inner_w))
