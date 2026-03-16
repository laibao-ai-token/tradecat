#!/usr/bin/env python3
"""Minimal read-only quotes command for TradeCat."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional, Sequence


TOOL_NAME = "tradecat_get_quotes"
SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[1]
QUOTE_MODULE_PATH = REPO_ROOT / "services-preview" / "tui-service" / "src" / "quote.py"
WATCHLISTS_MODULE_PATH = REPO_ROOT / "services-preview" / "tui-service" / "src" / "watchlists.py"
REPO_ENV_LOADER_PATH = REPO_ROOT / "scripts" / "lib" / "repo_env_loader.py"

MARKET_ALIASES = {
    "us": "us_stock",
    "us_stock": "us_stock",
    "hk": "hk_stock",
    "hk_stock": "hk_stock",
    "cn": "cn_stock",
    "cn_stock": "cn_stock",
    "fund": "cn_fund",
    "fund_cn": "cn_fund",
    "cn_fund": "cn_fund",
    "crypto": "crypto_spot",
    "crypto_spot": "crypto_spot",
    "metals": "metals",
    "metals_spot": "metals",
}

DEFAULT_PROVIDER_BY_MARKET = {
    "us_stock": "tencent",
    "hk_stock": "tencent",
    "cn_stock": "tencent",
    "cn_fund": "tencent",
    "crypto_spot": "auto",
    "metals": "auto",
}

PROVIDERS_BY_MARKET = {
    "us_stock": {"tencent"},
    "hk_stock": {"tencent"},
    "cn_stock": {"tencent"},
    "cn_fund": {"tencent"},
    "crypto_spot": {"auto", "gate", "htx", "okx", "bybit", "kucoin"},
    "metals": {"auto", "stooq", "sina", "yahoo"},
}

NORMALIZER_BY_MARKET = {
    "us_stock": "normalize_us_symbols",
    "hk_stock": "normalize_hk_symbols",
    "cn_stock": "normalize_cn_symbols",
    "cn_fund": "normalize_cn_fund_symbols",
    "crypto_spot": "normalize_crypto_symbols",
    "metals": "normalize_metals_symbols",
}

DEFAULT_TIMEOUT_BY_MARKET = {
    "us_stock": 3.0,
    "hk_stock": 3.0,
    "cn_stock": 3.0,
    "cn_fund": 3.0,
    "crypto_spot": 10.0,
    "metals": 6.0,
}


@dataclass
class QuoteRuntime:
    """Resolved runtime modules reused by the command."""

    quote_module: ModuleType
    watchlists_module: ModuleType


class CliError(RuntimeError):
    """Structured command error."""

    def __init__(self, code: str, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ParserExit(RuntimeError):
    """Raised when argparse wants to terminate."""

    def __init__(self, status: int, message: Optional[str] = None):
        super().__init__(message or "")
        self.status = int(status)
        self.message = message or ""


class JsonArgumentParser(argparse.ArgumentParser):
    """Argument parser that lets the caller emit JSON errors."""

    def error(self, message: str) -> None:
        raise CliError("invalid_arguments", message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if status == 0:
            raise ParserExit(status=status, message=message)
        raise CliError("invalid_arguments", (message or "").strip() or f"argument parsing failed (status={status})")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _error_payload(code: str, message: str, *, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "code": str(code),
        "message": str(message),
        "details": details or None,
    }


def _make_base_response() -> dict[str, Any]:
    return {
        "ok": False,
        "tool": TOOL_NAME,
        "ts": _utc_now_iso(),
        "source": {
            "mode": "direct_module",
            "script": "scripts/tradecat_get_quotes.py",
            "reader": "services-preview/tui-service/src/quote.py",
            "writes": False,
        },
        "request": {},
        "summary": {
            "requested": 0,
            "succeeded": 0,
            "failed": 0,
        },
        "data": [],
        "error": None,
    }


def _load_module(module_name: str, module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise CliError(
            "import_failed",
            f"unable to create import spec for {module_path}",
            details={"path": str(module_path)},
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - turned into structured CLI error
        raise CliError(
            "import_failed",
            f"failed to import {module_path.name}: {exc}",
            details={"path": str(module_path), "type": exc.__class__.__name__},
        ) from exc
    return module


def _load_runtime(repo_root: Path) -> QuoteRuntime:
    libs_path = repo_root / "libs"
    libs_path_str = str(libs_path)
    if libs_path_str not in sys.path:
        sys.path.insert(0, libs_path_str)

    if REPO_ENV_LOADER_PATH.exists():
        loader_module = _load_module("tradecat_repo_env_loader", REPO_ENV_LOADER_PATH)
        load_repo_env = getattr(loader_module, "load_repo_env_compat", None)
        if callable(load_repo_env):
            try:
                load_repo_env(repo_root, set_os_env=True, override=False)
            except Exception:
                # Quote fetching can still work without config/.env, so keep this best-effort.
                pass

    quote_module = _load_module("tradecat_tui_quote", QUOTE_MODULE_PATH)
    watchlists_module = _load_module("tradecat_tui_watchlists", WATCHLISTS_MODULE_PATH)
    return QuoteRuntime(quote_module=quote_module, watchlists_module=watchlists_module)


def _split_symbols(chunks: Sequence[str]) -> list[str]:
    out: list[str] = []
    for chunk in chunks:
        for part in str(chunk).split(","):
            symbol = part.strip()
            if symbol:
                out.append(symbol)
    return out


def _normalize_market(raw_market: str) -> str:
    market = MARKET_ALIASES.get(str(raw_market).strip().lower(), "")
    if not market:
        supported = ", ".join(sorted(MARKET_ALIASES))
        raise CliError(
            "unsupported_market",
            f"unsupported market: {raw_market}",
            details={"supported": supported},
        )
    return market


def _normalize_provider(raw_provider: str, market: str) -> str:
    provider = str(raw_provider or DEFAULT_PROVIDER_BY_MARKET[market]).strip().lower()
    allowed = PROVIDERS_BY_MARKET[market]
    if provider not in allowed:
        raise CliError(
            "invalid_provider",
            f"provider {provider!r} is not supported for market {market!r}",
            details={"market": market, "supported": sorted(allowed)},
        )
    return provider


def _infer_symbol_market(raw_symbol: str) -> str:
    token = str(raw_symbol or "").strip().upper()
    if not token:
        return ""

    compact = token.replace(" ", "")
    digits_only = compact.isdigit()

    canonical_metals = compact.replace("/", "").replace("-", "")
    if canonical_metals.endswith("=X"):
        canonical_metals = canonical_metals[:-2]
    if canonical_metals in {"XAUUSD", "XAGUSD"}:
        return "metals"

    if "_" in compact or "/" in compact or "-" in compact or (compact.endswith("USDT") and len(compact) > 4):
        return "crypto_spot"

    if compact.startswith(("SH", "SZ")) or compact.endswith((".SH", ".SZ")):
        normalized = compact
        if normalized.endswith(".SH"):
            exchange = "SH"
            code = normalized[:-3]
        elif normalized.endswith(".SZ"):
            exchange = "SZ"
            code = normalized[:-3]
        else:
            exchange = normalized[:2]
            code = normalized[2:]
        digits = "".join(ch for ch in code if ch.isdigit())
        if len(digits) != 6:
            return ""
        if (exchange == "SH" and digits.startswith("5")) or (exchange == "SZ" and digits.startswith("1")):
            return "cn_fund"
        return "cn_stock"

    if digits_only and len(compact) == 5:
        return "hk_stock"

    if digits_only and len(compact) == 6:
        if compact.startswith(("1", "5")):
            return "cn_fund"
        if compact.startswith(("2", "3", "6", "9")):
            return "cn_stock"
        return ""

    if compact and all(ch.isalnum() or ch in {".", "-"} for ch in compact):
        return "us_stock"

    return ""


def _infer_market(symbols: Sequence[str]) -> str:
    inferred: set[str] = set()
    unknown_symbols: list[str] = []
    for raw_symbol in symbols:
        market = _infer_symbol_market(raw_symbol)
        if not market:
            unknown_symbols.append(raw_symbol)
            continue
        inferred.add(market)

    if unknown_symbols:
        raise CliError(
            "ambiguous_market",
            "unable to infer market for one or more symbols; pass --market explicitly",
            details={"symbols": unknown_symbols},
        )

    if not inferred:
        raise CliError("ambiguous_market", "unable to infer market; pass --market explicitly")

    if len(inferred) > 1:
        raise CliError(
            "mixed_markets_not_supported",
            "all symbols must belong to the same market for a single command invocation",
            details={"markets": sorted(inferred)},
        )

    return next(iter(inferred))


def _normalize_symbol(runtime: QuoteRuntime, market: str, raw_symbol: str) -> Optional[str]:
    normalizer_name = NORMALIZER_BY_MARKET[market]
    normalizer = getattr(runtime.watchlists_module, normalizer_name)
    normalized = normalizer(raw_symbol)
    if not normalized:
        return None
    return str(normalized[0]).strip()


def _serialize_quote(
    *,
    raw_symbol: str,
    normalized_symbol: str,
    market: str,
    quote: Any,
) -> dict[str, Any]:
    return {
        "ok": True,
        "request_symbol": raw_symbol,
        "symbol": str(getattr(quote, "symbol", "") or normalized_symbol),
        "market": market,
        "name": str(getattr(quote, "name", "") or "") or None,
        "price": getattr(quote, "price", None),
        "quote_ts": str(getattr(quote, "ts", "") or "") or None,
        "provider": str(getattr(quote, "source", "") or "") or None,
        "currency": str(getattr(quote, "currency", "") or "") or None,
        "prev_close": getattr(quote, "prev_close", None),
        "open": getattr(quote, "open", None),
        "high": getattr(quote, "high", None),
        "low": getattr(quote, "low", None),
        "volume": getattr(quote, "volume", None),
        "amount": getattr(quote, "amount", None),
        "error": None,
    }


def _serialize_symbol_error(
    *,
    raw_symbol: str,
    normalized_symbol: Optional[str],
    market: str,
    error_code: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "request_symbol": raw_symbol,
        "symbol": normalized_symbol,
        "market": market,
        "name": None,
        "price": None,
        "quote_ts": None,
        "provider": None,
        "currency": None,
        "prev_close": None,
        "open": None,
        "high": None,
        "low": None,
        "volume": None,
        "amount": None,
        "error": _error_payload(error_code, message, details=details),
    }


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description="TradeCat minimal read-only quotes command")
    parser.add_argument("symbols", nargs="*", help="Symbols to query. Supports repeated args or comma-separated values.")
    parser.add_argument("--symbols", dest="symbols_csv", default="", help="Comma-separated symbols.")
    parser.add_argument(
        "--market",
        default="",
        help="Market: us_stock/hk_stock/cn_stock/cn_fund/crypto_spot/metals. Optional when symbols are unambiguous.",
    )
    parser.add_argument("--provider", default="", help="Optional provider override. Defaults depend on market.")
    parser.add_argument("--timeout", type=float, default=None, help="Request timeout seconds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    return parser


def execute(
    argv: Optional[Sequence[str]] = None,
    *,
    runtime: Optional[QuoteRuntime] = None,
) -> tuple[int, dict[str, Any]]:
    response = _make_base_response()
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    raw_symbols = _split_symbols(list(args.symbols) + ([args.symbols_csv] if args.symbols_csv else []))
    if not raw_symbols:
        raise CliError("invalid_arguments", "at least one symbol is required")

    market = _normalize_market(args.market) if args.market else _infer_market(raw_symbols)
    provider = _normalize_provider(args.provider, market)
    timeout_s = float(args.timeout) if args.timeout is not None else float(DEFAULT_TIMEOUT_BY_MARKET[market])
    if timeout_s <= 0:
        raise CliError("invalid_arguments", "--timeout must be greater than 0")

    active_runtime = runtime or _load_runtime(REPO_ROOT)

    entries: list[dict[str, Any]] = []
    normalized_symbols: list[str] = []
    seen_symbols: set[str] = set()
    for raw_symbol in raw_symbols:
        normalized_symbol = _normalize_symbol(active_runtime, market, raw_symbol)
        if not normalized_symbol:
            entries.append(
                {
                    "raw_symbol": raw_symbol,
                    "normalized_symbol": None,
                    "valid": False,
                }
            )
            continue

        entries.append(
            {
                "raw_symbol": raw_symbol,
                "normalized_symbol": normalized_symbol,
                "valid": True,
            }
        )
        if normalized_symbol not in seen_symbols:
            seen_symbols.add(normalized_symbol)
            normalized_symbols.append(normalized_symbol)

    response["request"] = {
        "symbols": raw_symbols,
        "normalized_symbols": normalized_symbols,
        "market": market,
        "provider": provider,
        "timeout_s": timeout_s,
    }
    response["summary"]["requested"] = len(entries)

    quotes_by_symbol: dict[str, Any] = {}
    batch_error: Optional[dict[str, Any]] = None
    if normalized_symbols:
        try:
            quotes_by_symbol = dict(
                active_runtime.quote_module.fetch_quotes(
                    provider,
                    market,
                    normalized_symbols,
                    timeout_s=timeout_s,
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001 - degraded into per-symbol structured errors
            batch_error = _error_payload(
                "batch_fetch_failed",
                f"batch quote fetch failed: {exc}",
                details={"type": exc.__class__.__name__},
            )
            for symbol in normalized_symbols:
                try:
                    quotes_by_symbol[symbol] = active_runtime.quote_module.fetch_quote(
                        provider,
                        market,
                        symbol,
                        timeout_s=timeout_s,
                    )
                except Exception as item_exc:  # noqa: BLE001 - converted to structured symbol error
                    quotes_by_symbol[symbol] = item_exc

    data: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    for entry in entries:
        raw_symbol = str(entry["raw_symbol"])
        normalized_symbol = entry.get("normalized_symbol")
        if not entry["valid"]:
            failed_count += 1
            data.append(
                _serialize_symbol_error(
                    raw_symbol=raw_symbol,
                    normalized_symbol=None,
                    market=market,
                    error_code="invalid_symbol",
                    message=f"symbol is invalid for market {market!r}",
                )
            )
            continue

        symbol_result = quotes_by_symbol.get(str(normalized_symbol))
        if isinstance(symbol_result, Exception):
            failed_count += 1
            data.append(
                _serialize_symbol_error(
                    raw_symbol=raw_symbol,
                    normalized_symbol=str(normalized_symbol),
                    market=market,
                    error_code="quote_fetch_failed",
                    message=f"quote fetch failed: {symbol_result}",
                    details={"type": symbol_result.__class__.__name__},
                )
            )
            continue

        if symbol_result is None:
            failed_count += 1
            details = {"provider": provider}
            if batch_error is not None:
                details["batch_error"] = batch_error
            data.append(
                _serialize_symbol_error(
                    raw_symbol=raw_symbol,
                    normalized_symbol=str(normalized_symbol),
                    market=market,
                    error_code="quote_not_found",
                    message="quote data unavailable",
                    details=details,
                )
            )
            continue

        success_count += 1
        data.append(
            _serialize_quote(
                raw_symbol=raw_symbol,
                normalized_symbol=str(normalized_symbol),
                market=market,
                quote=symbol_result,
            )
        )

    response["data"] = data
    response["summary"]["succeeded"] = success_count
    response["summary"]["failed"] = failed_count
    response["source"]["provider"] = provider
    response["source"]["market"] = market
    response["source"]["batch_fallback"] = bool(batch_error)

    if success_count > 0:
        response["ok"] = True
        response["error"] = None
        return 0, response

    response["ok"] = False
    if data:
        first_error = data[0].get("error")
        response["error"] = first_error or batch_error or _error_payload("quote_not_found", "quote data unavailable")
    else:
        response["error"] = batch_error or _error_payload("quote_not_found", "quote data unavailable")
    return 1, response


def main(argv: Optional[Sequence[str]] = None) -> int:
    pretty = "--pretty" in set(argv or sys.argv[1:])
    try:
        exit_code, payload = execute(argv)
    except ParserExit as exc:
        if exc.message:
            sys.stdout.write(exc.message)
        return exc.status
    except CliError as exc:
        payload = _make_base_response()
        payload["request"] = {"argv": list(argv) if argv is not None else sys.argv[1:]}
        payload["error"] = _error_payload(exc.code, str(exc), details=exc.details)
        payload["ok"] = False
        exit_code = 1
    except Exception as exc:  # noqa: BLE001 - final guard for stable JSON output
        payload = _make_base_response()
        payload["request"] = {"argv": list(argv) if argv is not None else sys.argv[1:]}
        payload["error"] = _error_payload(
            "unexpected_error",
            f"unexpected error: {exc}",
            details={"type": exc.__class__.__name__},
        )
        payload["ok"] = False
        exit_code = 1

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if pretty else None, sort_keys=False)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
