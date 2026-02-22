from __future__ import annotations

import concurrent.futures
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
import json
import time
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    currency: str
    volume: float
    amount: float
    ts: str  # market local time string (YYYY-mm-dd HH:MM:SS)
    source: str


_TENCENT_PREFIX_RE = re.compile(r"^v_([a-zA-Z]+)")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9.\-]+$")
_SAFE_CN_FUND_CODE_RE = re.compile(r"^\d{6}$")


def _is_safe_token(token: str) -> bool:
    t = (token or "").strip()
    if not t:
        return False
    return bool(_SAFE_TOKEN_RE.match(t))


_SAFE_YAHOO_SYMBOL_RE = re.compile(r"^[A-Z0-9.^=\-]{1,32}$")


def _is_safe_yahoo_symbol(symbol: str) -> bool:
    s = (symbol or "").strip().upper()
    if not s:
        return False
    return bool(_SAFE_YAHOO_SYMBOL_RE.match(s))


_SAFE_METALS_SYMBOL_RE = re.compile(r"^[A-Z]{2,6}USD$")


@dataclass(frozen=True)
class _RequestPolicy:
    key: str
    rate_per_s: float = 6.0
    burst: int = 12
    attempts: int = 2
    backoff_base_s: float = 0.08
    backoff_max_s: float = 0.8


class _TokenBucket:
    def __init__(self, rate_per_s: float, burst: int) -> None:
        self._rate_per_s = max(0.1, float(rate_per_s))
        self._capacity = max(1.0, float(burst))
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout_s: float) -> bool:
        timeout = max(0.0, float(timeout_s))
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self._updated_at)
                if elapsed > 0:
                    self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_s)
                    self._updated_at = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait_s = (1.0 - self._tokens) / self._rate_per_s

            if time.monotonic() + wait_s > deadline:
                return False
            time.sleep(min(0.05, max(0.001, wait_s)))


_TOKEN_BUCKETS: dict[str, _TokenBucket] = {}
_TOKEN_BUCKETS_LOCK = threading.Lock()


def _policy_for_url(url: str) -> _RequestPolicy:
    host = (urllib.parse.urlparse(url).netloc or "").lower()
    if "qt.gtimg.cn" in host or "ifzq.gtimg.cn" in host:
        return _RequestPolicy(key="tencent", rate_per_s=12.0, burst=24, attempts=2)
    if "charting.nasdaq.com" in host:
        return _RequestPolicy(key="nasdaq", rate_per_s=3.0, burst=6, attempts=3, backoff_base_s=0.1, backoff_max_s=1.0)
    if "query1.finance.yahoo.com" in host:
        return _RequestPolicy(key="yahoo", rate_per_s=1.5, burst=3, attempts=3, backoff_base_s=0.15, backoff_max_s=1.2)
    if "api.gateio.ws" in host:
        return _RequestPolicy(key="gate", rate_per_s=4.0, burst=8, attempts=3, backoff_base_s=0.1, backoff_max_s=1.0)
    if "api.huobi.pro" in host:
        return _RequestPolicy(key="htx", rate_per_s=4.0, burst=8, attempts=3, backoff_base_s=0.1, backoff_max_s=1.0)
    if "www.okx.com" in host:
        return _RequestPolicy(key="okx", rate_per_s=4.0, burst=8, attempts=3, backoff_base_s=0.1, backoff_max_s=1.0)
    if "api.bybit.com" in host:
        return _RequestPolicy(key="bybit", rate_per_s=4.0, burst=8, attempts=3, backoff_base_s=0.1, backoff_max_s=1.0)
    if "api.kucoin.com" in host:
        return _RequestPolicy(key="kucoin", rate_per_s=4.0, burst=8, attempts=3, backoff_base_s=0.1, backoff_max_s=1.0)
    if "stooq.com" in host:
        return _RequestPolicy(key="stooq", rate_per_s=2.0, burst=4, attempts=2, backoff_base_s=0.1, backoff_max_s=0.6)
    if "hq.sinajs.cn" in host:
        return _RequestPolicy(key="sina", rate_per_s=2.0, burst=4, attempts=2, backoff_base_s=0.1, backoff_max_s=0.6)
    return _RequestPolicy(key="generic", rate_per_s=8.0, burst=16, attempts=2)


def _get_token_bucket(policy: _RequestPolicy) -> _TokenBucket:
    key = (policy.key or "generic").strip().lower() or "generic"
    with _TOKEN_BUCKETS_LOCK:
        bucket = _TOKEN_BUCKETS.get(key)
        if bucket is None:
            bucket = _TokenBucket(policy.rate_per_s, policy.burst)
            _TOKEN_BUCKETS[key] = bucket
        return bucket


def _is_retryable_request_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return int(getattr(exc, "code", 0) or 0) in {408, 409, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError)):
        return True
    if isinstance(exc, OSError):
        return True
    return False


def _request_with_policy(
    fetcher: Callable[[], str],
    *,
    url: str,
    timeout_s: float,
    policy: _RequestPolicy | None = None,
) -> str:
    req_policy = policy or _policy_for_url(url)
    attempts = max(1, int(req_policy.attempts))
    wait_budget = max(0.05, min(1.5, float(timeout_s)))
    last_exc: Exception | None = None

    for attempt in range(attempts):
        bucket = _get_token_bucket(req_policy)
        if not bucket.acquire(wait_budget):
            last_exc = TimeoutError(f"rate limited: {req_policy.key}")
            if attempt >= attempts - 1:
                break
            time.sleep(min(0.05, req_policy.backoff_base_s))
            continue

        try:
            return fetcher()
        except Exception as exc:  # noqa: PERF203 - single place for retry control
            last_exc = exc
            if attempt >= attempts - 1 or not _is_retryable_request_error(exc):
                raise
            delay = min(
                float(req_policy.backoff_max_s),
                float(req_policy.backoff_base_s) * float(2**attempt),
            )
            time.sleep(max(0.01, delay))

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("request failed with unknown error")


def _normalize_metals_symbol(symbol: str) -> str:
    # Canonical internal format: XAUUSD, XAGUSD (no suffix)
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    s = s.replace("/", "").replace("-", "")
    if s.endswith("=X"):
        s = s[:-2]
    return s


def _is_safe_metals_symbol(symbol: str) -> bool:
    s = _normalize_metals_symbol(symbol)
    if not s:
        return False
    return bool(_SAFE_METALS_SYMBOL_RE.match(s))


def _metals_name(sym: str) -> str:
    s = _normalize_metals_symbol(sym)
    if s.startswith("XAU"):
        return "Gold"
    if s.startswith("XAG"):
        return "Silver"
    return s or "--"


def _parse_ts(ts: str) -> str:
    """
    Tencent returns multiple timestamp formats depending on market.
    Normalize to "YYYY-mm-dd HH:MM:SS" for display.
    """
    raw = (ts or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def _normalize_market_ts(ts: str, market_kind: str) -> str:
    """
    Normalize provider timestamp into local comparable wall time.

    Tencent US quote timestamp is market-local (New York) without timezone suffix.
    Convert it to local timezone so UI freshness and close/live judgement are correct.
    """
    normalized = _parse_ts(ts)
    if not normalized:
        return ""

    kind = (market_kind or "").strip().lower()
    if kind != "us":
        return normalized

    try:
        from zoneinfo import ZoneInfo

        ny_tz = ZoneInfo("America/New_York")
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            return normalized
        ny_dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ny_tz)
        local_dt = ny_dt.astimezone(local_tz)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return normalized


def _split_amount_field(raw: str) -> float:
    """
    A-share responses often include an 'amount' embedded like:
      idx35: "price/volume/amount"
    Return parsed amount if possible.
    """
    s = (raw or "").strip()
    if not s or "/" not in s:
        return 0.0
    parts = s.split("/")
    if len(parts) < 3:
        return 0.0
    return _to_float(parts[2])


def _http_get(url: str, timeout_s: float = 3.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            # Some endpoints reject empty UA.
            "User-Agent": "TradeCat/tui-service",
            "Accept": "*/*",
            "Connection": "close",
        },
        method="GET",
    )

    def _read(force_ipv4: bool) -> bytes:
        # Some container/host networks have broken IPv6 routes; urllib may pick IPv6 first and fail with
        # "Network is unreachable". Retry once with IPv4-only resolution.
        if not force_ipv4:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()

        orig_getaddrinfo = socket.getaddrinfo

        def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

        socket.getaddrinfo = _ipv4_only_getaddrinfo  # type: ignore[assignment]
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        finally:
            socket.getaddrinfo = orig_getaddrinfo  # type: ignore[assignment]

    def _read_once() -> str:
        try:
            data = _read(force_ipv4=False)
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            errno = getattr(reason, "errno", None)
            if errno in {101, 113, 99}:  # network unreachable / no route / address family issues
                data = _read(force_ipv4=True)
            else:
                raise
        # Tencent often uses GBK/GB18030 for Chinese fields (company name).
        # Prefer UTF-8 if it's valid, otherwise fall back to GB18030.
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("gb18030", errors="replace")

    return _request_with_policy(_read_once, url=url, timeout_s=timeout_s)


def _http_get_with_headers(url: str, headers: dict[str, str], timeout_s: float = 3.0) -> str:
    """
    Some public quote endpoints require a browser-like UA/Referer to avoid 403.
    Keep this separate from _http_get() so we don't unintentionally change other providers.
    """
    req = urllib.request.Request(url, headers=headers, method="GET")

    def _read(force_ipv4: bool) -> bytes:
        if not force_ipv4:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()

        orig_getaddrinfo = socket.getaddrinfo

        def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

        socket.getaddrinfo = _ipv4_only_getaddrinfo  # type: ignore[assignment]
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        finally:
            socket.getaddrinfo = orig_getaddrinfo  # type: ignore[assignment]

    def _read_once() -> str:
        try:
            data = _read(force_ipv4=False)
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            errno = getattr(reason, "errno", None)
            if errno in {101, 113, 99}:
                data = _read(force_ipv4=True)
            else:
                raise

        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("gb18030", errors="replace")

    return _request_with_policy(_read_once, url=url, timeout_s=timeout_s)


def _to_float(raw: str) -> float:
    try:
        return float(raw)
    except Exception:
        return 0.0


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


def _cn_fund_exchange_candidates(symbol: str) -> list[str]:
    sym = _normalize_cn_fund_symbol(symbol)
    if not sym:
        return []
    if sym.startswith(("SH", "SZ")):
        return [sym]
    # Try both exchanges for 6-digit raw code; ETF/LOF codes are exchange-scoped.
    code = sym
    out: list[str] = []
    if code[0] in {"5", "6", "9"}:
        out.append("SH" + code)
    if code.startswith(("15", "16", "18")):
        out.append("SZ" + code)
    return out


def _parse_fundgz_jsonp(payload: str) -> Quote | None:
    """
    Parse Eastmoney fund valuation payload:
      jsonpgz({...});
    """
    text = (payload or "").strip()
    if not text:
        return None
    m = re.search(r"\((\{.*\})\)\s*;?\s*$", text, flags=re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    code = str(obj.get("fundcode") or "").strip()
    if not _SAFE_CN_FUND_CODE_RE.match(code):
        return None
    name = str(obj.get("name") or code).strip() or code
    est_nav = _to_float(str(obj.get("gsz") or ""))
    nav = _to_float(str(obj.get("dwjz") or ""))
    price = est_nav if est_nav > 0 else nav
    if price <= 0:
        return None
    prev_close = nav if nav > 0 else price
    change_pct = _to_float(str(obj.get("gszzl") or ""))
    open_price = prev_close
    if prev_close > 0:
        open_price = prev_close
    elif abs(change_pct) > 1e-9:
        open_price = price / max(1e-9, (1 + change_pct / 100.0))
    ts = str(obj.get("gztime") or "").strip()
    if not ts:
        d = str(obj.get("jzrq") or "").strip()
        ts = f"{d} 15:00:00" if d else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return Quote(
        symbol=code,
        name=name,
        price=price,
        prev_close=prev_close,
        open=open_price,
        high=price,
        low=price,
        currency="CNY",
        volume=0.0,
        amount=0.0,
        ts=ts,
        source="fundgz",
    )


def fetch_cn_offmarket_fund_quote(symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    code = _normalize_cn_fund_symbol(symbol)
    if not _SAFE_CN_FUND_CODE_RE.match(code):
        return None
    url = f"https://fundgz.1234567.com.cn/js/{code}.js"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://fund.eastmoney.com/{code}.html",
        "Accept": "application/javascript,text/javascript,*/*;q=0.1",
    }
    try:
        payload = _http_get_with_headers(url, headers=headers, timeout_s=timeout_s)
    except Exception:
        return None
    return _parse_fundgz_jsonp(payload)


def _parse_tencent_quote_line(line: str) -> Optional[Quote]:
    """
    Parse a single Tencent quote line:
      v_usNVDA="...~...~";
    """
    line = (line or "").strip()
    if not line:
        return None
    if "=" not in line or '"' not in line:
        return None
    m = _TENCENT_PREFIX_RE.search(line)
    if not m:
        return None
    # Extract quoted content
    payload = line.split('"', 2)[1]
    parts = payload.split("~")
    if len(parts) < 31:
        return None

    # Symbol is encoded in the prefix: v_usNVDA / v_hk00700 / v_sh600519 / v_sz000001
    prefix = line.split("=", 1)[0].strip()
    raw = prefix.replace("v_", "").strip()
    kind = raw[:2].lower()
    code = raw[2:]
    if kind == "us":
        sym = code.strip().upper()
    elif kind == "hk":
        sym = code.strip().zfill(5)
    elif kind in {"sh", "sz"}:
        # Keep exchange to avoid ambiguity across A-shares.
        sym = f"{kind.upper()}{code.strip()}"
    else:
        # Unknown prefix; best-effort.
        sym = raw.strip().upper()

    name = parts[1] or sym
    price = _to_float(parts[3])
    prev_close = _to_float(parts[4])
    open_p = _to_float(parts[5])
    chigh = _to_float(parts[33]) if len(parts) > 33 else 0.0
    clow = _to_float(parts[34]) if len(parts) > 34 else 0.0
    volume = _to_float(parts[36]) if len(parts) > 36 else 0.0
    amount = _to_float(parts[37]) if len(parts) > 37 else 0.0

    # Currency index differs per market.
    if kind == "hk":
        currency = (parts[75] if len(parts) > 75 else "") or ""
    elif kind in {"sh", "sz"}:
        currency = (parts[82] if len(parts) > 82 else "") or ""
        # Prefer exact amount (CNY) from idx35, fallback to idx37 * 10000 (common unit).
        amt = _split_amount_field(parts[35] if len(parts) > 35 else "")
        if amt > 0:
            amount = amt
        elif amount > 0:
            amount = amount * 10000.0
    else:
        currency = (parts[35] if len(parts) > 35 else "") or ""

    ts = _normalize_market_ts(parts[30] if len(parts) > 30 else "", kind)
    return Quote(
        symbol=sym,
        name=name,
        price=price,
        prev_close=prev_close,
        open=open_p,
        high=chigh,
        low=clow,
        currency=currency,
        volume=volume,
        amount=amount,
        ts=ts,
        source="tencent",
    )


def fetch_tencent_us_quote(symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Fetch a US equity quote from Tencent.

    Endpoint example:
      https://qt.gtimg.cn/q=usNVDA

    Response example:
      v_usNVDA="200~<name>~NVDA.OQ~179.90~185.61~186.24~...~2026-02-03 10:36:40~...";
    Field mapping (common):
      idx3: last price
      idx4: prev close
      idx5: open
      idx30: timestamp
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    url = f"https://qt.gtimg.cn/q=us{sym}"
    text = _http_get(url, timeout_s=timeout_s)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    return _parse_tencent_quote_line(lines[0])


def fetch_tencent_us_quotes(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Batch fetch US equity quotes from Tencent in a single request:
      https://qt.gtimg.cn/q=usNVDA,usAAPL
    """
    cleaned: list[str] = []
    for s in symbols:
        sym = (s or "").strip().upper()
        if sym and _is_safe_token(sym):
            cleaned.append(sym)
    if not cleaned:
        return {}

    q = ",".join([f"us{sym}" for sym in cleaned])
    url = f"https://qt.gtimg.cn/q={q}"
    text = _http_get(url, timeout_s=timeout_s)

    out: dict[str, Optional[Quote]] = {sym: None for sym in cleaned}
    for line in text.splitlines():
        qv = _parse_tencent_quote_line(line)
        if qv is None:
            continue
        if qv.symbol in out:
            out[qv.symbol] = qv
    return out


def fetch_tencent_hk_quotes(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Batch fetch HK equity quotes from Tencent:
      https://qt.gtimg.cn/q=hk00700,hk01810
    """
    cleaned: list[str] = []
    for s in symbols:
        raw = (s or "").strip()
        if not raw:
            continue
        # Accept 700 / 0700 / 00700 / 00700.HK / 700.HK etc.
        digits = "".join([c for c in raw if c.isdigit()])
        if not digits:
            continue
        cleaned.append(digits.zfill(5))
    if not cleaned:
        return {}

    q = ",".join([f"hk{sym}" for sym in cleaned])
    url = f"https://qt.gtimg.cn/q={q}"
    text = _http_get(url, timeout_s=timeout_s)

    out: dict[str, Optional[Quote]] = {sym: None for sym in cleaned}
    for line in text.splitlines():
        qv = _parse_tencent_quote_line(line)
        if qv is None:
            continue
        if qv.symbol in out:
            out[qv.symbol] = qv
    return out


def fetch_tencent_cn_quotes(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Batch fetch A-share quotes from Tencent:
      https://qt.gtimg.cn/q=sh600519,sz000001

    Symbols accepted:
      - SH600519 / SZ000001
      - sh600519 / sz000001
      - 600519.SH / 000001.SZ
    """
    cleaned: list[str] = []
    for s in symbols:
        raw = (s or "").strip()
        if not raw:
            continue
        upper = raw.upper()
        if upper.endswith(".SH"):
            upper = "SH" + upper[:-3]
        elif upper.endswith(".SZ"):
            upper = "SZ" + upper[:-3]
        if upper.startswith("SH") or upper.startswith("SZ"):
            ex = upper[:2]
            digits = "".join([c for c in upper[2:] if c.isdigit()])
            if len(digits) == 6:
                cleaned.append(ex + digits)
    if not cleaned:
        return {}

    q = ",".join([f"{sym[:2].lower()}{sym[2:]}" for sym in cleaned])
    url = f"https://qt.gtimg.cn/q={q}"
    text = _http_get(url, timeout_s=timeout_s)

    out: dict[str, Optional[Quote]] = {sym: None for sym in cleaned}
    for line in text.splitlines():
        qv = _parse_tencent_quote_line(line)
        if qv is None:
            continue
        if qv.symbol in out:
            out[qv.symbol] = qv
    return out


def _normalize_tencent_equity_code(symbol: str, market: str) -> tuple[str, str]:
    """Normalize UI symbol to Tencent minute-query code."""
    m = (market or "").strip().lower()
    raw = (symbol or "").strip()
    if not raw:
        return "", ""

    if m == "us_stock":
        sym = raw.upper()
        if sym.endswith(".US") and len(sym) > 3:
            sym = sym[:-3]
        if not _is_safe_token(sym):
            return "", ""
        return f"us{sym}", sym

    if m in {"cn_stock", "cn_fund"}:
        sym = raw.upper()
        if sym.endswith(".SH") and len(sym) > 3:
            sym = "SH" + sym[:-3]
        elif sym.endswith(".SZ") and len(sym) > 3:
            sym = "SZ" + sym[:-3]
        if sym.isdigit() and len(sym) == 6:
            if m == "cn_fund":
                if sym[0] in {"5", "6", "9"}:
                    sym = "SH" + sym
                elif sym.startswith(("15", "16", "18")):
                    sym = "SZ" + sym
                else:
                    # Off-market fund codes (e.g. 024389) don't have minute bars.
                    return "", sym
            else:
                sym = ("SH" if sym[0] in {"5", "6", "9"} else "SZ") + sym
        if not (sym.startswith("SH") or sym.startswith("SZ")):
            return "", ""
        digits = "".join([c for c in sym[2:] if c.isdigit()])
        if len(digits) != 6:
            return "", ""
        ex = sym[:2]
        normalized = ex + digits
        return f"{ex.lower()}{digits}", normalized

    if m == "hk_stock":
        digits = "".join([c for c in raw if c.isdigit()])
        if not digits:
            return "", ""
        normalized = digits.zfill(5)
        return f"hk{normalized}", normalized

    return "", ""


def _parse_market_trade_date(raw_date: str, fallback_ts: str, market: str) -> date | None:
    """Parse trade date from Tencent minute payload."""
    d = (raw_date or "").strip()
    if len(d) == 8 and d.isdigit():
        try:
            return datetime.strptime(d, "%Y%m%d").date()
        except ValueError:
            pass

    fallback = (fallback_ts or "").strip()
    if not fallback:
        return None
    fmts = []
    m = (market or "").strip().lower()
    if m == "cn_stock":
        fmts = ["%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]
    elif m == "hk_stock":
        fmts = ["%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]
    else:
        fmts = ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]

    for fmt in fmts:
        try:
            return datetime.strptime(fallback, fmt).date()
        except ValueError:
            continue
    return None


def _parse_tencent_minute_payload(payload: str, code: str, market: str, limit: int = 60) -> list[tuple[int, float, float]]:
    """Parse Tencent minute-query payload into (epoch_s, price, volume_cum)."""
    try:
        obj = json.loads(payload)
    except Exception:
        return []

    block = obj.get("data", {}).get(code, {}) if isinstance(obj, dict) else {}
    if not isinstance(block, dict):
        return []
    data = block.get("data") if isinstance(block.get("data"), dict) else {}
    rows = data.get("data") if isinstance(data.get("data"), list) else []

    qt_ts = ""
    qt = block.get("qt")
    if isinstance(qt, dict):
        qt_row = qt.get(code)
        if isinstance(qt_row, list) and len(qt_row) > 30:
            qt_ts = str(qt_row[30] or "")

    trade_date = _parse_market_trade_date(str(data.get("date") or ""), qt_ts, market)
    if trade_date is None or not rows:
        return []

    tz_name = {
        "us_stock": "America/New_York",
        "cn_stock": "Asia/Shanghai",
        "hk_stock": "Asia/Hong_Kong",
    }.get((market or "").strip().lower())
    if not tz_name:
        return []

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        return []

    out: list[tuple[int, float, float]] = []
    for raw in rows:
        row = str(raw or "").strip()
        if not row:
            continue
        cols = row.split()
        if len(cols) < 2:
            continue

        hhmm = cols[0]
        if len(hhmm) != 4 or not hhmm.isdigit():
            continue
        hh = int(hhmm[:2])
        mm = int(hhmm[2:])
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            continue

        price = _to_float(cols[1])
        if price <= 0:
            continue
        volume = _to_float(cols[2]) if len(cols) > 2 else 0.0

        ts = datetime(
            trade_date.year,
            trade_date.month,
            trade_date.day,
            hh,
            mm,
            tzinfo=tz,
        )
        out.append((int(ts.timestamp()), price, max(0.0, volume)))

    if not out:
        return []
    out.sort(key=lambda x: x[0])
    safe_limit = max(5, int(limit))
    return out[-safe_limit:]


def fetch_tencent_equity_minute_series(
    symbol: str,
    market: str,
    timeout_s: float = 5.0,
    limit: int = 60,
) -> list[tuple[int, float, float]]:
    """Fetch minute close series from Tencent minute-query endpoint."""
    code, _ = _normalize_tencent_equity_code(symbol, market)
    if not code:
        return []

    url = f"https://ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    try:
        payload = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return []
    return _parse_tencent_minute_payload(payload, code=code, market=market, limit=limit)


_NASDAQ_INTRADAY_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://charting.nasdaq.com/dynamic/chart.html",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}

_EASTMONEY_KLINE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": "https://quote.eastmoney.com/",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
}


def _normalize_eastmoney_cn_secid(symbol: str, market: str) -> tuple[str, str]:
    """
    Normalize CN stock/fund symbol to Eastmoney secid.

    Returns:
      - (secid, normalized_symbol)
      - secid is empty for unsupported symbols (e.g. off-market fund codes like 024389).
    """
    m = (market or "").strip().lower()
    raw = (symbol or "").strip()
    if m not in {"cn_stock", "cn_fund"} or not raw:
        return "", ""

    sym = raw.upper()
    if sym.endswith(".SH") and len(sym) > 3:
        sym = "SH" + sym[:-3]
    elif sym.endswith(".SZ") and len(sym) > 3:
        sym = "SZ" + sym[:-3]

    if sym.startswith(("SH", "SZ")):
        ex = sym[:2]
        digits = "".join(ch for ch in sym[2:] if ch.isdigit())
        if len(digits) != 6:
            return "", ""
        secid = ("1." if ex == "SH" else "0.") + digits
        return secid, ex + digits

    digits = "".join(ch for ch in sym if ch.isdigit())
    if len(digits) != 6:
        return "", ""

    if m == "cn_fund":
        if digits[0] in {"5", "6", "9"}:
            return "1." + digits, "SH" + digits
        if digits.startswith(("15", "16", "18")):
            return "0." + digits, "SZ" + digits
        # Off-market fund codes don't have Eastmoney exchange secid.
        return "", digits

    if digits[0] in {"5", "6", "9"}:
        return "1." + digits, "SH" + digits
    return "0." + digits, "SZ" + digits


def _parse_eastmoney_daily_kline_payload(payload: str, limit: int = 15) -> list[tuple[int, float, float, float, float, float]]:
    """
    Parse Eastmoney daily kline payload into:
      (epoch_s, open, high, low, close, volume)
    """
    try:
        obj = json.loads(payload)
    except Exception:
        return []

    data = obj.get("data") if isinstance(obj, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(klines, list):
        return []

    out: list[tuple[int, float, float, float, float, float]] = []
    for raw in klines:
        row = str(raw or "").strip()
        if not row:
            continue
        parts = row.split(",")
        if len(parts) < 6:
            continue
        try:
            dt = datetime.strptime(parts[0].strip(), "%Y-%m-%d")
            ts = int(dt.replace(hour=15, minute=0, second=0).timestamp())
            open_px = _to_float(parts[1])
            close_px = _to_float(parts[2])
            high_px = _to_float(parts[3])
            low_px = _to_float(parts[4])
            vol = max(0.0, _to_float(parts[5]))
        except Exception:
            continue
        if open_px <= 0 or close_px <= 0 or high_px <= 0 or low_px <= 0:
            continue
        out.append((ts, open_px, high_px, low_px, close_px, vol))

    if not out:
        return []
    out.sort(key=lambda item: item[0])
    safe_limit = max(5, min(120, int(limit)))
    return out[-safe_limit:]


def fetch_eastmoney_cn_daily_series(
    symbol: str,
    market: str,
    timeout_s: float = 6.0,
    limit: int = 15,
) -> list[tuple[int, float, float, float, float, float]]:
    """Fetch CN stock/fund daily kline series from Eastmoney (no API key)."""
    secid, _ = _normalize_eastmoney_cn_secid(symbol, market)
    if not secid:
        return []

    params = urllib.parse.urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "klt": "101",  # daily
            "fqt": "1",  # forward-adjusted
            "lmt": str(max(5, min(120, int(limit)))),
            "end": "20500101",
        }
    )
    url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{params}"
    try:
        payload = _http_get_with_headers(url, headers=_EASTMONEY_KLINE_HEADERS, timeout_s=timeout_s)
    except Exception:
        return []
    return _parse_eastmoney_daily_kline_payload(payload, limit=limit)


def fetch_daily_curve_1d(
    provider: str,
    market: str,
    symbol: str,
    timeout_s: float = 6.0,
    limit: int = 15,
) -> list[tuple[int, float, float, float, float, float]]:
    """Fetch daily curve points for chart windows longer than intraday."""
    p = (provider or "").strip().lower()
    m = (market or "").strip().lower()
    safe_limit = max(5, min(120, int(limit)))

    if m in {"cn_stock", "cn_fund"} and p in {"", "auto", "tencent", "eastmoney"}:
        return fetch_eastmoney_cn_daily_series(symbol, market=m, timeout_s=timeout_s, limit=safe_limit)
    return []


def _parse_nasdaq_intraday_payload(payload: str, symbol: str, limit: int = 60) -> list[tuple[int, float, float]]:
    """Parse Nasdaq intraday payload into (epoch_s, price, volume_cum)."""
    try:
        obj = json.loads(payload)
    except Exception:
        return []

    rows = obj.get("marketData") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        return []

    try:
        from zoneinfo import ZoneInfo

        ny_tz = ZoneInfo("America/New_York")
    except Exception:
        return []

    out: list[tuple[int, float, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts_raw = str(row.get("Date") or "").strip()
        price = _to_float(str(row.get("Value") or ""))
        if not ts_raw or price <= 0:
            continue
        try:
            dt = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ny_tz)
        except ValueError:
            continue
        volume = _to_float(str(row.get("Volume") or ""))
        out.append((int(dt.timestamp()), price, max(0.0, volume)))

    if not out:
        return []
    out.sort(key=lambda x: x[0])
    safe_limit = max(5, int(limit))
    return out[-safe_limit:]


def fetch_nasdaq_us_minute_series(symbol: str, timeout_s: float = 6.0, limit: int = 60) -> list[tuple[int, float, float]]:
    """Fetch US 1m close series from Nasdaq charting endpoint (no API key)."""
    sym = (symbol or "").strip().upper()
    if sym.endswith(".US") and len(sym) > 3:
        sym = sym[:-3]
    if not sym or not _is_safe_token(sym):
        return []

    url = (
        "https://charting.nasdaq.com/data/charting/intraday"
        f"?symbol={sym}&mostRecent=1&includeLatestIntradayData=1"
    )
    try:
        payload = _http_get_with_headers(url, headers=_NASDAQ_INTRADAY_HEADERS, timeout_s=timeout_s)
    except Exception:
        return []

    return _parse_nasdaq_intraday_payload(payload, symbol=sym, limit=limit)


def fetch_intraday_curve_1m(
    provider: str,
    market: str,
    symbol: str,
    timeout_s: float = 6.0,
    limit: int = 60,
) -> list[tuple[int, float, float]]:
    """Fetch intraday minute curve points for TUI closed-market replay."""
    p = (provider or "").strip().lower()
    m = (market or "").strip().lower()
    safe_limit = max(5, min(int(limit), 390))

    if m == "us_stock":
        # Nasdaq returns full session minute points and is better for closed-market replay.
        series = fetch_nasdaq_us_minute_series(symbol, timeout_s=timeout_s, limit=safe_limit)
        if series:
            return series
        if p in {"", "auto", "tencent"}:
            return fetch_tencent_equity_minute_series(symbol, market=m, timeout_s=timeout_s, limit=safe_limit)
        return []

    if m in {"cn_stock", "hk_stock", "cn_fund"}:
        if p in {"", "auto", "tencent"}:
            return fetch_tencent_equity_minute_series(symbol, market=m, timeout_s=timeout_s, limit=safe_limit)
        return []

    return []



_SAFE_CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]{2,12}_[A-Z0-9]{2,12}$")


def _is_safe_crypto_pair(pair: str) -> bool:
    p = (pair or "").strip().upper()
    return bool(_SAFE_CRYPTO_PAIR_RE.match(p))


def _split_crypto_pair(pair: str) -> tuple[str, str] | tuple[None, None]:
    p = (pair or "").strip().upper().replace("-", "_")
    if not _is_safe_crypto_pair(p) or "_" not in p:
        return (None, None)
    base, quote = p.split("_", 1)
    if not base or not quote:
        return (None, None)
    return (base, quote)


def fetch_gate_spot_quote(pair: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Fetch a crypto spot ticker from Gate (no API key).

    Endpoint example:
      https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT

    Note:
      Gate returns 24h stats, not an exchange "prev_close" field.
      We derive prev_close from last and change_percentage:
        prev_close ~= last / (1 + pct/100)
    """
    p = (pair or "").strip().upper().replace("-", "_")
    if not _is_safe_crypto_pair(p):
        return None
    url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={p}"
    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, list) or not obj:
        return None
    row = obj[0] if isinstance(obj[0], dict) else None
    if not row:
        return None
    last = _to_float(str(row.get("last", "")))
    pct = _to_float(str(row.get("change_percentage", "")))
    prev_close = 0.0
    if last and pct > -99.0:
        prev_close = last / (1.0 + pct / 100.0) if (1.0 + pct / 100.0) != 0 else 0.0
    high_24h = _to_float(str(row.get("high_24h", "")))
    low_24h = _to_float(str(row.get("low_24h", "")))
    base_vol = _to_float(str(row.get("base_volume", "")))
    quote_vol = _to_float(str(row.get("quote_volume", "")))
    quote_ccy = p.split("_", 1)[1] if "_" in p else ""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return Quote(
        symbol=p,
        name=p.replace("_", "/"),
        price=last,
        prev_close=prev_close,
        open=prev_close,
        high=high_24h,
        low=low_24h,
        currency=quote_ccy,
        volume=base_vol,
        amount=quote_vol,
        ts=ts,
        source="gate",
    )


def fetch_gate_spot_quotes(pairs: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Batch fetch for Gate spot tickers.

    Gate's `spot/tickers` filter only supports a single `currency_pair` per request,
    so we do sequential requests (kept small by design: watchlist size).
    """
    out: dict[str, Optional[Quote]] = {}
    for raw in pairs:
        p = (raw or "").strip().upper().replace("-", "_")
        if not _is_safe_crypto_pair(p):
            continue
        try:
            out[p] = fetch_gate_spot_quote(p, timeout_s=timeout_s)
        except Exception:
            out[p] = None
    return out


def fetch_htx_spot_quote(pair: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Fetch a crypto spot ticker from HTX/Huobi (no API key).

    Endpoint example:
      https://api.huobi.pro/market/detail/merged?symbol=btcusdt
    """
    p = (pair or "").strip().upper().replace("-", "_")
    if not _is_safe_crypto_pair(p):
        return None

    base, quote = p.split("_", 1)
    symbol = f"{base}{quote}".lower()
    url = f"https://api.huobi.pro/market/detail/merged?symbol={symbol}"

    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return None

    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("status") != "ok":
        return None

    tick = obj.get("tick")
    if not isinstance(tick, dict):
        return None

    last = _to_float(str(tick.get("close", "")))
    open_24h = _to_float(str(tick.get("open", "")))
    high_24h = _to_float(str(tick.get("high", "")))
    low_24h = _to_float(str(tick.get("low", "")))
    base_vol = _to_float(str(tick.get("amount", "")))
    quote_vol = _to_float(str(tick.get("vol", "")))

    ts_ms = 0.0
    try:
        ts_ms = float(obj.get("ts") or 0.0)
    except Exception:
        ts_ms = 0.0
    ts = datetime.utcfromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S") if ts_ms else datetime.utcnow().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # HTX doesn't provide "prev close"; use 24h open as baseline for chg/pct display.
    prev_close = open_24h
    return Quote(
        symbol=p,
        name=p.replace("_", "/"),
        price=last,
        prev_close=prev_close,
        open=prev_close,
        high=high_24h,
        low=low_24h,
        currency=quote,
        volume=base_vol,
        amount=quote_vol,
        ts=ts,
        source="htx",
    )


def fetch_htx_spot_quotes(pairs: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    out: dict[str, Optional[Quote]] = {}
    for raw in pairs:
        p = (raw or "").strip().upper().replace("-", "_")
        if not _is_safe_crypto_pair(p):
            continue
        try:
            out[p] = fetch_htx_spot_quote(p, timeout_s=timeout_s)
        except Exception:
            out[p] = None
    return out


def _parse_yahoo_quote_row(row: dict) -> Optional[Quote]:
    sym = str(row.get("symbol") or "").strip().upper()
    if not sym or not _is_safe_yahoo_symbol(sym):
        return None

    name = str(row.get("shortName") or row.get("longName") or row.get("displayName") or sym).strip() or sym
    price = _to_float(str(row.get("regularMarketPrice") or ""))
    prev_close = _to_float(str(row.get("regularMarketPreviousClose") or ""))
    open_p = _to_float(str(row.get("regularMarketOpen") or ""))
    high = _to_float(str(row.get("regularMarketDayHigh") or ""))
    low = _to_float(str(row.get("regularMarketDayLow") or ""))
    volume = _to_float(str(row.get("regularMarketVolume") or ""))
    currency = str(row.get("currency") or "").strip() or ""

    ts_epoch = 0
    try:
        ts_epoch = int(row.get("regularMarketTime") or 0)
    except Exception:
        ts_epoch = 0
    ts = (
        datetime.utcfromtimestamp(ts_epoch).strftime("%Y-%m-%d %H:%M:%S")
        if ts_epoch
        else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    )

    # Yahoo doesn't always provide "open" / "prev close" consistently for all instruments.
    # Prefer prev_close if present; otherwise keep it 0.0 so UI pct logic won't crash.
    if not open_p and prev_close:
        open_p = prev_close

    return Quote(
        symbol=sym,
        name=name,
        price=price,
        prev_close=prev_close,
        open=open_p,
        high=high,
        low=low,
        currency=currency,
        volume=volume,
        amount=0.0,
        ts=ts,
        source="yahoo",
    )


def fetch_yahoo_quotes(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Fetch quotes from Yahoo Finance (no API key).

    Endpoint:
      https://query1.finance.yahoo.com/v7/finance/quote?symbols=...

    Metals examples (Yahoo format):
      - XAUUSD=X  (Gold spot)
      - XAGUSD=X  (Silver spot)
    """
    cleaned: list[str] = []
    for s in symbols:
        sym = _normalize_metals_symbol(s)
        if not sym:
            continue
        yahoo_sym = f"{sym}=X"
        if not _is_safe_yahoo_symbol(yahoo_sym):
            continue
        cleaned.append(yahoo_sym)
    if not cleaned:
        return {}

    # Encode each symbol (e.g., "=" -> "%3D") but keep commas as separators.
    encoded = ",".join([urllib.parse.quote(sym, safe="") for sym in cleaned])
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded}"

    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return {sym: None for sym in cleaned}

    try:
        obj = json.loads(text)
    except Exception:
        return {sym: None for sym in cleaned}

    res: dict[str, Optional[Quote]] = {sym: None for sym in cleaned}
    qr = obj.get("quoteResponse") if isinstance(obj, dict) else None
    rows = (qr.get("result") if isinstance(qr, dict) else None) if qr is not None else None
    if not isinstance(rows, list):
        return res

    for row in rows:
        if not isinstance(row, dict):
            continue
        q = _parse_yahoo_quote_row(row)
        if q is None:
            continue
        if q.symbol in res:
            res[q.symbol] = q
    return res


def fetch_yahoo_quote(symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    sym = _normalize_metals_symbol(symbol)
    if not sym:
        return None
    yahoo_sym = f"{sym}=X"
    return fetch_yahoo_quotes([sym], timeout_s=timeout_s).get(yahoo_sym)


def _parse_stooq_csv_line(line: str) -> Optional[Quote]:
    # CSV example:
    # Symbol,Date,Time,Open,High,Low,Close,Volume
    # XAUUSD,2026-02-04,16:59:12,4946.66,5091.82,4888.07,4921.14,
    s = (line or "").strip()
    if not s or s.lower().startswith("symbol,"):
        return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 8:
        return None
    sym = (parts[0] or "").strip().upper()
    if not sym or not _is_safe_metals_symbol(sym):
        return None

    date_s = (parts[1] or "").strip()
    time_s = (parts[2] or "").strip()
    ts = f"{date_s} {time_s}".strip()
    open_p = _to_float(parts[3])
    high = _to_float(parts[4])
    low = _to_float(parts[5])
    last = _to_float(parts[6])
    vol = _to_float(parts[7])

    prev_close = open_p
    return Quote(
        symbol=sym,
        name=_metals_name(sym),
        price=last,
        prev_close=prev_close,
        open=open_p,
        high=high,
        low=low,
        currency="USD",
        volume=vol,
        amount=0.0,
        ts=ts,
        source="stooq",
    )


def fetch_stooq_metals_quote(symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    sym = _normalize_metals_symbol(symbol)
    if not _is_safe_metals_symbol(sym):
        return None
    url = f"https://stooq.com/q/l/?s={sym.lower()}&f=sd2t2ohlcv&h&e=csv"
    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    return _parse_stooq_csv_line(lines[1])


def fetch_stooq_metals_quotes(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    out: dict[str, Optional[Quote]] = {}
    for s in symbols:
        sym = _normalize_metals_symbol(s)
        if not _is_safe_metals_symbol(sym):
            continue
        try:
            out[sym] = fetch_stooq_metals_quote(sym, timeout_s=timeout_s)
        except Exception:
            out[sym] = None
    return out


_SINA_HF_LINE_RE = re.compile(r'^var\s+hq_str_hf_([A-Za-z0-9]+)="(.*)";?$')


def _parse_sina_hf_line(line: str) -> tuple[str, list[str]] | tuple[None, None]:
    s = (line or "").strip()
    if not s:
        return (None, None)
    m = _SINA_HF_LINE_RE.match(s)
    if not m:
        return (None, None)
    sym = (m.group(1) or "").strip().upper()
    payload = m.group(2) or ""
    parts = payload.split(",")
    return (sym, parts)


def fetch_sina_metals_quotes(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Fetch metals quotes from Sina (no API key, but requires browser-like headers).

    Underlying instruments:
      - Gold:   hf_GC (COMEX Gold, mapped to XAUUSD)
      - Silver: hf_SI (COMEX Silver, mapped to XAGUSD)
    """
    normalized: list[str] = []
    wanted: set[str] = set()
    for s in symbols:
        sym = _normalize_metals_symbol(s)
        if not _is_safe_metals_symbol(sym):
            continue
        normalized.append(sym)
        if sym.startswith("XAU"):
            wanted.add("GC")
        elif sym.startswith("XAG"):
            wanted.add("SI")
    if not normalized or not wanted:
        return {}

    q = ",".join([f"hf_{x}" for x in sorted(wanted)])
    url = f"https://hq.sinajs.cn/list={q}"
    headers = {
        "User-Agent": "Mozilla/5.0 (TradeCat/tui-service)",
        "Referer": "https://finance.sina.com.cn/",
        "Accept": "*/*",
        "Connection": "close",
    }

    try:
        text = _http_get_with_headers(url, headers=headers, timeout_s=timeout_s)
    except Exception:
        return {sym: None for sym in normalized}

    # Map GC/SI -> parsed quote
    hf: dict[str, Quote] = {}
    for ln in text.splitlines():
        key, parts = _parse_sina_hf_line(ln)
        if not key or not parts:
            continue
        # Sina hf payload is a CSV list; the following indices are observed stable in practice:
        # 0:last, 4:high, 5:low, 6:time, 7:open, 8:prev_close, 12:date, 13:name
        last = _to_float(parts[0] if len(parts) > 0 else "")
        high = _to_float(parts[4] if len(parts) > 4 else "")
        low = _to_float(parts[5] if len(parts) > 5 else "")
        t = (parts[6] if len(parts) > 6 else "").strip()
        open_p = _to_float(parts[7] if len(parts) > 7 else "")
        prev_close = _to_float(parts[8] if len(parts) > 8 else "")
        d = (parts[12] if len(parts) > 12 else "").strip()
        ts = f"{d} {t}".strip()
        name = (parts[13] if len(parts) > 13 else "").strip() or key
        hf[key] = Quote(
            symbol=key,
            name=name,
            price=last,
            prev_close=prev_close,
            open=open_p or prev_close,
            high=high,
            low=low,
            currency="USD",
            volume=0.0,
            amount=0.0,
            ts=ts,
            source="sina",
        )

    out: dict[str, Optional[Quote]] = {}
    for sym in normalized:
        base = hf.get("GC") if sym.startswith("XAU") else (hf.get("SI") if sym.startswith("XAG") else None)
        if base is None:
            out[sym] = None
            continue
        out[sym] = Quote(
            symbol=sym,
            name=_metals_name(sym),
            price=base.price,
            prev_close=base.prev_close,
            open=base.open,
            high=base.high,
            low=base.low,
            currency=base.currency,
            volume=base.volume,
            amount=base.amount,
            ts=base.ts,
            source=base.source,
        )
    return out


def fetch_sina_metals_quote(symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    sym = _normalize_metals_symbol(symbol)
    if not _is_safe_metals_symbol(sym):
        return None
    return fetch_sina_metals_quotes([sym], timeout_s=timeout_s).get(sym)


def fetch_okx_spot_quote(pair: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Fetch a crypto spot ticker from OKX (no API key).

    Endpoint example:
      https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT
    """
    base, quote = _split_crypto_pair(pair)
    if not base or not quote:
        return None
    p = f"{base}_{quote}"
    inst = f"{base}-{quote}"
    url = f"https://www.okx.com/api/v5/market/ticker?instId={inst}"

    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return None

    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict) or str(obj.get("code")) != "0":
        return None
    data = obj.get("data")
    if not isinstance(data, list) or not data:
        return None
    row = data[0] if isinstance(data[0], dict) else None
    if not row:
        return None

    last = _to_float(str(row.get("last", "")))
    open_24h = _to_float(str(row.get("open24h", "")))
    high_24h = _to_float(str(row.get("high24h", "")))
    low_24h = _to_float(str(row.get("low24h", "")))
    base_vol = _to_float(str(row.get("vol24h", "")))
    quote_vol = _to_float(str(row.get("volCcy24h", "")))

    ts_ms = _to_float(str(row.get("ts", "")))
    ts = (
        datetime.utcfromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
        if ts_ms
        else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    )
    prev_close = open_24h
    return Quote(
        symbol=p,
        name=p.replace("_", "/"),
        price=last,
        prev_close=prev_close,
        open=prev_close,
        high=high_24h,
        low=low_24h,
        currency=quote,
        volume=base_vol,
        amount=quote_vol,
        ts=ts,
        source="okx",
    )


def fetch_okx_spot_quotes(pairs: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    out: dict[str, Optional[Quote]] = {}
    for raw in pairs:
        p = (raw or "").strip().upper().replace("-", "_")
        if not _is_safe_crypto_pair(p):
            continue
        try:
            out[p] = fetch_okx_spot_quote(p, timeout_s=timeout_s)
        except Exception:
            out[p] = None
    return out


def fetch_bybit_spot_quote(pair: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Fetch a crypto spot ticker from Bybit (no API key).

    Endpoint example:
      https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT
    """
    base, quote = _split_crypto_pair(pair)
    if not base or not quote:
        return None
    p = f"{base}_{quote}"
    symbol = f"{base}{quote}"
    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"

    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return None

    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict) or int(obj.get("retCode") or -1) != 0:
        return None

    result = obj.get("result")
    if not isinstance(result, dict):
        return None
    lst = result.get("list")
    if not isinstance(lst, list) or not lst:
        return None
    row = lst[0] if isinstance(lst[0], dict) else None
    if not row:
        return None

    last = _to_float(str(row.get("lastPrice", "")))
    prev_close = _to_float(str(row.get("prevPrice24h", "")))
    high_24h = _to_float(str(row.get("highPrice24h", "")))
    low_24h = _to_float(str(row.get("lowPrice24h", "")))
    base_vol = _to_float(str(row.get("volume24h", "")))
    quote_vol = _to_float(str(row.get("turnover24h", "")))

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return Quote(
        symbol=p,
        name=p.replace("_", "/"),
        price=last,
        prev_close=prev_close,
        open=prev_close,
        high=high_24h,
        low=low_24h,
        currency=quote,
        volume=base_vol,
        amount=quote_vol,
        ts=ts,
        source="bybit",
    )


def fetch_bybit_spot_quotes(pairs: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    out: dict[str, Optional[Quote]] = {}
    for raw in pairs:
        p = (raw or "").strip().upper().replace("-", "_")
        if not _is_safe_crypto_pair(p):
            continue
        try:
            out[p] = fetch_bybit_spot_quote(p, timeout_s=timeout_s)
        except Exception:
            out[p] = None
    return out


def fetch_kucoin_spot_quote(pair: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Fetch a crypto spot ticker from KuCoin (no API key).

    Endpoint example:
      https://api.kucoin.com/api/v1/market/stats?symbol=BTC-USDT
    """
    base, quote = _split_crypto_pair(pair)
    if not base or not quote:
        return None
    p = f"{base}_{quote}"
    symbol = f"{base}-{quote}"
    url = f"https://api.kucoin.com/api/v1/market/stats?symbol={symbol}"

    try:
        text = _http_get(url, timeout_s=timeout_s)
    except Exception:
        return None

    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict) or str(obj.get("code")) != "200000":
        return None
    data = obj.get("data")
    if not isinstance(data, dict):
        return None

    last = _to_float(str(data.get("last", "")))
    high_24h = _to_float(str(data.get("high", "")))
    low_24h = _to_float(str(data.get("low", "")))
    base_vol = _to_float(str(data.get("vol", "")))
    quote_vol = _to_float(str(data.get("volValue", "")))

    # changeRate is a fraction string like "0.0234" (i.e., 2.34%).
    change_rate = _to_float(str(data.get("changeRate", "")))
    prev_close = 0.0
    if last and change_rate > -0.99:
        prev_close = last / (1.0 + change_rate) if (1.0 + change_rate) != 0 else 0.0

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return Quote(
        symbol=p,
        name=p.replace("_", "/"),
        price=last,
        prev_close=prev_close,
        open=prev_close,
        high=high_24h,
        low=low_24h,
        currency=quote,
        volume=base_vol,
        amount=quote_vol,
        ts=ts,
        source="kucoin",
    )


def fetch_kucoin_spot_quotes(pairs: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    out: dict[str, Optional[Quote]] = {}
    for raw in pairs:
        p = (raw or "").strip().upper().replace("-", "_")
        if not _is_safe_crypto_pair(p):
            continue
        try:
            out[p] = fetch_kucoin_spot_quote(p, timeout_s=timeout_s)
        except Exception:
            out[p] = None
    return out


CryptoSpotFetcher = Callable[[str, float], Optional[Quote]]


_CRYPTO_SPOT_FETCHERS: dict[str, CryptoSpotFetcher] = {
    "htx": fetch_htx_spot_quote,
    "gate": fetch_gate_spot_quote,
    "okx": fetch_okx_spot_quote,
    "bybit": fetch_bybit_spot_quote,
    "kucoin": fetch_kucoin_spot_quote,
}

# Remember the last known good provider per pair to avoid always hitting multiple endpoints.
_CRYPTO_PREFERRED_PROVIDER: dict[str, str] = {}

# Default fallback chain when provider=auto.
_CRYPTO_AUTO_CHAIN: tuple[str, ...] = ("htx", "gate", "okx", "bybit", "kucoin")


def _race_crypto_spot_providers(pair: str, providers: list[str], timeout_s: float) -> Optional[Quote]:
    provs = [p for p in providers if p in _CRYPTO_SPOT_FETCHERS]
    if not provs:
        return None
    # Use a small pool: watchlists are small and we want fast UI refresh.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(provs))) as ex:
        futs: dict[concurrent.futures.Future[Optional[Quote]], str] = {}
        for p in provs:
            fetcher = _CRYPTO_SPOT_FETCHERS[p]
            futs[ex.submit(fetcher, pair, timeout_s)] = p  # type: ignore[arg-type]
        try:
            for fut in concurrent.futures.as_completed(futs, timeout=timeout_s):
                try:
                    q = fut.result()
                except Exception:
                    continue
                if q is not None and q.price > 0:
                    _CRYPTO_PREFERRED_PROVIDER[pair] = q.source
                    return q
        except concurrent.futures.TimeoutError:
            return None
    return None


def fetch_crypto_spot_quote_auto(pair: str, timeout_s: float = 3.0) -> Optional[Quote]:
    p = (pair or "").strip().upper().replace("-", "_")
    if not _is_safe_crypto_pair(p):
        return None

    pref = _CRYPTO_PREFERRED_PROVIDER.get(p)
    if pref and pref in _CRYPTO_SPOT_FETCHERS:
        # Fast path: try the last known good provider first.
        try:
            q = _CRYPTO_SPOT_FETCHERS[pref](p, timeout_s=timeout_s)  # type: ignore[arg-type]
        except Exception:
            q = None
        if q is not None and q.price > 0:
            return q

    # Slow path: race a subset quickly, then fall back sequentially.
    chain = [x for x in _CRYPTO_AUTO_CHAIN if x in _CRYPTO_SPOT_FETCHERS and x != pref]
    # "auto" exists to reduce missing rows; be a bit more patient than the stock quote timeout.
    # The TUI polls crypto symbols in parallel, so a slightly longer per-symbol timeout is OK.
    race_timeout = max(1.0, min(float(timeout_s), 6.0))
    q = _race_crypto_spot_providers(p, chain[:3], timeout_s=race_timeout)
    if q is not None:
        return q
    # Last resort: try the remaining providers one by one (still capped).
    for prov in chain[3:]:
        try:
            q = _CRYPTO_SPOT_FETCHERS[prov](p, timeout_s=race_timeout)  # type: ignore[arg-type]
        except Exception:
            q = None
        if q is not None and q.price > 0:
            _CRYPTO_PREFERRED_PROVIDER[p] = q.source
            return q
    return None


MetalsFetcher = Callable[[str, float], Optional[Quote]]

_METALS_FETCHERS: dict[str, MetalsFetcher] = {
    "stooq": fetch_stooq_metals_quote,
    "sina": fetch_sina_metals_quote,
    "yahoo": fetch_yahoo_quote,
}

_METALS_PREFERRED_PROVIDER: dict[str, str] = {}
_METALS_AUTO_CHAIN: tuple[str, ...] = ("sina", "stooq", "yahoo")


def fetch_metals_quote_auto(symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    sym = _normalize_metals_symbol(symbol)
    if not _is_safe_metals_symbol(sym):
        return None

    pref = _METALS_PREFERRED_PROVIDER.get(sym)
    if pref and pref in _METALS_FETCHERS:
        try:
            q = _METALS_FETCHERS[pref](sym, timeout_s=timeout_s)  # type: ignore[arg-type]
        except Exception:
            q = None
        if q is not None and q.price > 0:
            return q

    for prov in _METALS_AUTO_CHAIN:
        if prov == pref or prov not in _METALS_FETCHERS:
            continue
        try:
            q = _METALS_FETCHERS[prov](sym, timeout_s=timeout_s)  # type: ignore[arg-type]
        except Exception:
            q = None
        if q is not None and q.price > 0:
            _METALS_PREFERRED_PROVIDER[sym] = q.source
            return q
    return None


def fetch_metals_quotes_auto(symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Batch auto fetch for metals.

    Important: some free endpoints have strict rate limits ("daily hits" / 429). For the TUI,
    prefer providers that support batching to reduce request count.
    """
    syms: list[str] = []
    for s in symbols:
        sym = _normalize_metals_symbol(s)
        if not _is_safe_metals_symbol(sym):
            continue
        syms.append(sym)
    if not syms:
        return {}

    # De-dup but keep order.
    seen: set[str] = set()
    ordered: list[str] = []
    for s in syms:
        if s in seen:
            continue
        seen.add(s)
        ordered.append(s)

    out: dict[str, Optional[Quote]] = {s: None for s in ordered}

    # 1) Try Sina in a single request (best-effort).
    try:
        res = fetch_sina_metals_quotes(ordered, timeout_s=timeout_s)
    except Exception:
        res = {}
    for s, q in (res or {}).items():
        if q is not None and q.price > 0:
            out[s] = q
            _METALS_PREFERRED_PROVIDER[s] = q.source

    missing = [s for s in ordered if out.get(s) is None]
    if not missing:
        return out

    # 2) Fallback to stooq (may hit daily limit).
    try:
        res2 = fetch_stooq_metals_quotes(missing, timeout_s=timeout_s)
    except Exception:
        res2 = {}
    for s, q in (res2 or {}).items():
        if out.get(s) is None and q is not None and q.price > 0:
            out[s] = q
            _METALS_PREFERRED_PROVIDER[s] = q.source

    missing = [s for s in ordered if out.get(s) is None]
    if not missing:
        return out

    # 3) Last resort: Yahoo (often 429).
    try:
        raw = fetch_yahoo_quotes(missing, timeout_s=timeout_s)
    except Exception:
        raw = {}
    for s in missing:
        qy = raw.get(f"{s}=X") if s else None
        if qy is not None and qy.price > 0:
            # Normalize symbol back to canonical.
            out[s] = Quote(
                symbol=s,
                name=_metals_name(s),
                price=qy.price,
                prev_close=qy.prev_close,
                open=qy.open,
                high=qy.high,
                low=qy.low,
                currency=qy.currency,
                volume=qy.volume,
                amount=qy.amount,
                ts=qy.ts,
                source=qy.source,
            )
            _METALS_PREFERRED_PROVIDER[s] = "yahoo"

    return out


def fetch_quote(provider: str, market: str, symbol: str, timeout_s: float = 3.0) -> Optional[Quote]:
    """
    Unified quote fetch.

    Currently supported (no API key):
      - provider=tencent, market=us_stock
    """
    p = (provider or "").strip().lower()
    m = (market or "").strip().lower()
    if p == "tencent" and m == "us_stock":
        return fetch_tencent_us_quote(symbol, timeout_s=timeout_s)
    if p == "tencent" and m == "hk_stock":
        res = fetch_tencent_hk_quotes([symbol], timeout_s=timeout_s)
        # Normalize lookup key to 5-digit.
        digits = "".join([c for c in (symbol or "") if c.isdigit()]).zfill(5)
        return res.get(digits)
    if p == "tencent" and m == "cn_stock":
        res = fetch_tencent_cn_quotes([symbol], timeout_s=timeout_s)
        upper = (symbol or "").strip().upper()
        if upper.endswith(".SH"):
            upper = "SH" + upper[:-3]
        elif upper.endswith(".SZ"):
            upper = "SZ" + upper[:-3]
        return res.get(upper)
    if p == "tencent" and m == "cn_fund":
        # 1) Try exchange-traded route first (ETF/LOF).
        candidates = _cn_fund_exchange_candidates(symbol)
        if candidates:
            res = fetch_tencent_cn_quotes(candidates, timeout_s=timeout_s)
            for key in candidates:
                q = res.get(key)
                if q is not None and q.price > 0:
                    return q
        # 2) Fallback to off-market valuation quote.
        return fetch_cn_offmarket_fund_quote(symbol, timeout_s=timeout_s)
    if p == "stooq" and m in {"metals", "metals_spot"}:
        return fetch_stooq_metals_quote(symbol, timeout_s=timeout_s)
    if p == "sina" and m in {"metals", "metals_spot"}:
        return fetch_sina_metals_quote(symbol, timeout_s=timeout_s)
    if p == "yahoo" and m in {"metals", "metals_spot"}:
        return fetch_yahoo_quote(symbol, timeout_s=timeout_s)
    if p == "auto" and m in {"metals", "metals_spot"}:
        return fetch_metals_quote_auto(symbol, timeout_s=timeout_s)
    if p == "gate" and m == "crypto_spot":
        return fetch_gate_spot_quote(symbol, timeout_s=timeout_s)
    if p == "htx" and m == "crypto_spot":
        return fetch_htx_spot_quote(symbol, timeout_s=timeout_s)
    if p == "okx" and m == "crypto_spot":
        return fetch_okx_spot_quote(symbol, timeout_s=timeout_s)
    if p == "bybit" and m == "crypto_spot":
        return fetch_bybit_spot_quote(symbol, timeout_s=timeout_s)
    if p == "kucoin" and m == "crypto_spot":
        return fetch_kucoin_spot_quote(symbol, timeout_s=timeout_s)
    if p == "auto" and m == "crypto_spot":
        return fetch_crypto_spot_quote_auto(symbol, timeout_s=timeout_s)
    return None


def fetch_quotes(provider: str, market: str, symbols: Iterable[str], timeout_s: float = 3.0) -> dict[str, Optional[Quote]]:
    """
    Unified batch quote fetch.

    Currently supported (no API key):
      - provider=tencent, market=us_stock  (batched)
    """
    p = (provider or "").strip().lower()
    m = (market or "").strip().lower()
    if p == "tencent" and m == "us_stock":
        return fetch_tencent_us_quotes(symbols, timeout_s=timeout_s)
    if p == "tencent" and m == "hk_stock":
        return fetch_tencent_hk_quotes(symbols, timeout_s=timeout_s)
    if p == "tencent" and m == "cn_stock":
        return fetch_tencent_cn_quotes(symbols, timeout_s=timeout_s)
    if p == "tencent" and m == "cn_fund":
        # Mixed list: exchange-traded funds + off-market funds.
        out: dict[str, Optional[Quote]] = {}
        normalized_inputs: list[str] = []
        exchange_queries: list[str] = []
        exchange_map: dict[str, list[str]] = {}
        offmarket_codes: list[str] = []

        for raw in symbols:
            norm = _normalize_cn_fund_symbol(raw)
            if not norm:
                continue
            normalized_inputs.append(norm)
            if _SAFE_CN_FUND_CODE_RE.match(norm):
                offmarket_codes.append(norm)
            cands = _cn_fund_exchange_candidates(norm)
            if cands:
                exchange_map[norm] = cands
                exchange_queries.extend(cands)

        exchange_quotes = fetch_tencent_cn_quotes(exchange_queries, timeout_s=timeout_s) if exchange_queries else {}
        for sym in normalized_inputs:
            q: Optional[Quote] = None
            for key in exchange_map.get(sym, []):
                item = exchange_quotes.get(key)
                if item is not None and item.price > 0:
                    q = item
                    break
            if q is None and _SAFE_CN_FUND_CODE_RE.match(sym):
                q = fetch_cn_offmarket_fund_quote(sym, timeout_s=timeout_s)
            out[sym] = q
        return out
    if p == "stooq" and m in {"metals", "metals_spot"}:
        return fetch_stooq_metals_quotes(symbols, timeout_s=timeout_s)
    if p == "sina" and m in {"metals", "metals_spot"}:
        return fetch_sina_metals_quotes(symbols, timeout_s=timeout_s)
    if p == "yahoo" and m in {"metals", "metals_spot"}:
        # Yahoo returns keys in Yahoo format (e.g., XAUUSD=X). Convert to canonical keys for the UI.
        raw = fetch_yahoo_quotes(symbols, timeout_s=timeout_s)
        out: dict[str, Optional[Quote]] = {}
        for s in symbols:
            sym = _normalize_metals_symbol(s)
            out[sym] = raw.get(f"{sym}=X") if sym else None
        return out
    if p == "auto" and m in {"metals", "metals_spot"}:
        return fetch_metals_quotes_auto(symbols, timeout_s=timeout_s)
    if p == "gate" and m == "crypto_spot":
        return fetch_gate_spot_quotes(symbols, timeout_s=timeout_s)
    if p == "htx" and m == "crypto_spot":
        return fetch_htx_spot_quotes(symbols, timeout_s=timeout_s)
    if p == "okx" and m == "crypto_spot":
        return fetch_okx_spot_quotes(symbols, timeout_s=timeout_s)
    if p == "bybit" and m == "crypto_spot":
        return fetch_bybit_spot_quotes(symbols, timeout_s=timeout_s)
    if p == "kucoin" and m == "crypto_spot":
        return fetch_kucoin_spot_quotes(symbols, timeout_s=timeout_s)
    if p == "auto" and m == "crypto_spot":
        # Per-symbol fallback.
        out: dict[str, Optional[Quote]] = {}
        for s in symbols:
            sym = (s or "").strip().upper().replace("-", "_")
            if not _is_safe_crypto_pair(sym):
                continue
            q = fetch_crypto_spot_quote_auto(sym, timeout_s=timeout_s)
            out[sym] = q
        return out

    # Fallback: sequential single fetch.
    out: dict[str, Optional[Quote]] = {}
    for s in symbols:
        sym = (s or "").strip()
        if not sym:
            continue
        out[sym.upper()] = fetch_quote(provider, market, sym, timeout_s=timeout_s)
    return out
