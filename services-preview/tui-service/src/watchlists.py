from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Watchlists:
    us: list[str] = field(default_factory=list)
    hk: list[str] = field(default_factory=list)  # 5-digit strings, e.g. "00700"
    cn: list[str] = field(default_factory=list)  # "SH600519"/"SZ000001"
    crypto: list[str] = field(default_factory=list)  # Gate spot pairs, e.g. "BTC_USDT"
    metals: list[str] = field(default_factory=list)  # Yahoo symbols, e.g. "XAUUSD=X"


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


_US_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,12}$")


def normalize_us_symbols(raw: str) -> list[str]:
    syms: list[str] = []
    for token in (raw or "").replace(" ", "").split(","):
        t = token.strip().upper()
        if not t:
            continue
        # Reject if it contains anything outside the safe set. This avoids
        # accidentally turning control keys like "^C" into fake tickers like "C".
        if any((not (c.isalnum() or c in {".", "-"})) for c in t):
            continue
        if not _US_TICKER_RE.match(t):
            continue
        syms.append(t)
    return _dedup_keep_order(syms)


def normalize_hk_symbols(raw: str) -> list[str]:
    syms: list[str] = []
    for token in (raw or "").replace(" ", "").split(","):
        t = token.strip()
        if not t:
            continue
        digits = "".join([c for c in t if c.isdigit()])
        if not digits:
            continue
        syms.append(digits.zfill(5))
    return _dedup_keep_order(syms)


def normalize_cn_symbols(raw: str) -> list[str]:
    syms: list[str] = []
    for token in (raw or "").replace(" ", "").split(","):
        t = token.strip().upper()
        if not t:
            continue
        # Allow pure 6-digit codes and infer exchange:
        # - 6xxxxx -> SH
        # - 0xxxxx/3xxxxx -> SZ
        # (BJ/others are not handled here)
        if t.isdigit() and len(t) == 6:
            if t.startswith("6"):
                syms.append("SH" + t)
            elif t.startswith(("0", "3")):
                syms.append("SZ" + t)
            continue
        if t.endswith(".SH"):
            t = "SH" + t[:-3]
        elif t.endswith(".SZ"):
            t = "SZ" + t[:-3]
        if t.startswith("SH") or t.startswith("SZ"):
            ex = t[:2]
            digits = "".join([c for c in t[2:] if c.isdigit()])
            if len(digits) != 6:
                continue
            syms.append(ex + digits)
    return _dedup_keep_order(syms)


_CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]{2,12}_[A-Z0-9]{2,12}$")


def normalize_crypto_symbols(raw: str) -> list[str]:
    """
    Normalize crypto pairs for the default spot provider (Gate).

    Accept examples:
      - BTC_USDT
      - BTCUSDT  -> BTC_USDT (only if quote is USDT)
      - BTC-USDT -> BTC_USDT
      - eth_usdt -> ETH_USDT
    """
    out: list[str] = []
    for token in (raw or "").replace(" ", "").split(","):
        t = token.strip().upper()
        if not t:
            continue
        t = t.replace("/", "_")
        t = t.replace("-", "_")
        if "_" not in t:
            # Best-effort: infer BTCUSDT -> BTC_USDT
            if t.endswith("USDT") and len(t) > 4:
                t = t[:-4] + "_USDT"
            else:
                # Common UX: user types "DOGE" meaning "DOGE_USDT".
                # Default to USDT to match the built-in watchlist.
                if t.isalnum() and 2 <= len(t) <= 12:
                    t = t + "_USDT"
        if not _CRYPTO_PAIR_RE.match(t):
            continue
        out.append(t)
    return _dedup_keep_order(out)


_METALS_SYMBOL_RE = re.compile(r"^[A-Z0-9.^=\-/]{1,32}$")


def normalize_metals_symbols(raw: str) -> list[str]:
    """
    Normalize precious metals symbols for the default provider (Yahoo).

    Accept examples:
      - XAUUSD    (gold spot, canonical)
      - XAGUSD    (silver spot, canonical)
      - XAUUSD=X  (legacy Yahoo format; will be normalized to XAUUSD)
      - XAU/USD   (best-effort; will be normalized to XAUUSD)
    """
    out: list[str] = []
    for token in (raw or "").replace(" ", "").split(","):
        t = token.strip().upper()
        if not t:
            continue
        if not _METALS_SYMBOL_RE.match(t):
            continue
        # legacy: Yahoo style "XAUUSD=X"
        if t.endswith("=X") and len(t) >= 3:
            t = t[:-2]
        # best-effort: "XAU/USD" -> "XAUUSD"
        t = t.replace("/", "")
        t = t.replace("-", "")
        out.append(t)
    return _dedup_keep_order(out)


def load_watchlists(path: str) -> Watchlists:
    p = Path(path)
    if not p.exists():
        return Watchlists()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return Watchlists()
    return Watchlists(
        us=normalize_us_symbols(",".join(data.get("us", []) if isinstance(data.get("us"), list) else [])),
        hk=normalize_hk_symbols(",".join(data.get("hk", []) if isinstance(data.get("hk"), list) else [])),
        cn=normalize_cn_symbols(",".join(data.get("cn", []) if isinstance(data.get("cn"), list) else [])),
        crypto=normalize_crypto_symbols(",".join(data.get("crypto", []) if isinstance(data.get("crypto"), list) else [])),
        metals=normalize_metals_symbols(",".join(data.get("metals", []) if isinstance(data.get("metals"), list) else [])),
    )


def save_watchlists(path: str, wl: Watchlists) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "us": _dedup_keep_order([s.strip().upper() for s in (wl.us or []) if s.strip()]),
        "hk": _dedup_keep_order([s.strip().zfill(5) for s in (wl.hk or []) if s.strip()]),
        "cn": _dedup_keep_order([s.strip().upper() for s in (wl.cn or []) if s.strip()]),
        "crypto": _dedup_keep_order([s.strip().upper() for s in (wl.crypto or []) if s.strip()]),
        "metals": _dedup_keep_order([s.strip().upper() for s in (wl.metals or []) if s.strip()]),
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
