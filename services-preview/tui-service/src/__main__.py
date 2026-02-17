from __future__ import annotations

import argparse
from pathlib import Path

from .micro import MicroConfig
from .tui import QuoteConfig, QuoteConfigs, run
from .watchlists import (
    Watchlists,
    load_watchlists,
    normalize_cn_symbols,
    normalize_crypto_symbols,
    normalize_hk_symbols,
    normalize_metals_symbols,
    normalize_us_symbols,
)


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(10):
        if (cur / "services").exists() and (cur / "config").exists() and (cur / "AGENTS.md").exists():
            return cur
        cur = cur.parent
    return start.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="TradeCat TUI (preview): view pg/sqlite signals from signal_history.db")
    parser.add_argument("--db", default="", help="Path to signal_history.db (default: repo libs/database/.../signal_history.db)")
    parser.add_argument("--refresh", type=float, default=1.0, help="Refresh interval seconds (default: 1.0)")
    parser.add_argument("--limit", type=int, default=500, help="Max rows to load each refresh (default: 500)")
    parser.add_argument("--no-quote", action="store_true", help="Disable quote line in header (default: enabled)")
    parser.add_argument("--quote-provider", default="tencent", help="Quote provider for stock markets (default: tencent)")
    parser.add_argument(
        "--crypto-provider",
        default="auto",
        help="Crypto quote provider: auto/htx/gate/okx/bybit/kucoin (default: auto)",
    )
    parser.add_argument("--crypto-timeout", type=float, default=10.0, help="Crypto request timeout seconds (default: 10.0)")
    parser.add_argument("--metals-provider", default="auto", help="Metals quote provider: auto/sina/stooq/yahoo (default: auto)")
    parser.add_argument("--metals-timeout", type=float, default=6.0, help="Metals request timeout seconds (default: 6.0)")
    parser.add_argument("--quote-refresh", type=float, default=1.0, help="Quote refresh interval seconds (default: 1.0)")
    parser.add_argument("--us-symbols", default="NVDA,META,ORCL", help="US watchlist. Example: NVDA,META,ORCL")
    parser.add_argument("--hk-symbols", default="00700,01810,03690", help="HK watchlist. Example: 00700,01810,03690")
    parser.add_argument("--cn-symbols", default="SH600519,SZ000001,SH688256", help="A-share watchlist. Example: SH600519,SZ000001")
    parser.add_argument("--crypto-symbols", default="BTC_USDT,ETH_USDT", help="Crypto spot watchlist. Example: BTC_USDT,ETH_USDT")
    parser.add_argument("--metals-symbols", default="XAUUSD,XAGUSD", help="Metals watchlist. Example: XAUUSD,XAGUSD")
    # Backward compatible flags (if you used earlier versions).
    parser.add_argument("--quote-market", default="", help="(compat) Quote market: us_stock/hk_stock/cn_stock/crypto_spot/metals")
    parser.add_argument("--quote-symbols", default="", help="(compat) Comma-separated quote symbols for quote-market")
    parser.add_argument(
        "--view",
        default="market_micro",
        choices=[
            "signals",
            "quotes",
            "quotes_us",
            "market_us",
            "quotes_hk",
            "quotes_cn",
            "quotes_crypto",
            "quotes_metals",
            "market_cn",
            "market_crypto",
            "market_micro",
            "market_backtest",
        ],
        help="Start view",
    )
    parser.add_argument("--micro-symbol", default="BTC_USDT", help="Micro view symbol. Example: BTC_USDT")
    parser.add_argument(
        "--micro-interval",
        type=int,
        default=5,
        help="Micro view candle interval seconds (5/10/15/30/60; default: 5)",
    )
    parser.add_argument("--micro-window", type=int, default=60, help="Micro view max candles in chart (default: 60)")
    parser.add_argument("--micro-flow-rows", type=int, default=30, help="Micro view trade-flow rows (default: 30)")
    parser.add_argument("--micro-refresh", type=float, default=0.5, help="Micro view refresh seconds (default: 0.5)")
    parser.add_argument("--hot-reload", action="store_true", help="Dev mode: auto-restart TUI when src/*.py changes")
    parser.add_argument(
        "--hot-reload-poll",
        type=float,
        default=1.0,
        help="Hot reload polling interval seconds (default: 1.0)",
    )
    args = parser.parse_args()

    if args.db:
        db_path = Path(args.db).expanduser().resolve()
    else:
        repo_root = _find_repo_root(Path(__file__).resolve())
        db_path = repo_root / "libs" / "database" / "services" / "signal-service" / "signal_history.db"

    service_root = Path(__file__).resolve().parents[1]
    watchlists_path = service_root / "watchlists.json"
    file_wl: Watchlists = load_watchlists(str(watchlists_path))

    us_symbols = normalize_us_symbols(str(args.us_symbols))
    hk_symbols = normalize_hk_symbols(str(args.hk_symbols))
    cn_symbols = normalize_cn_symbols(str(args.cn_symbols))
    crypto_symbols = normalize_crypto_symbols(str(args.crypto_symbols))
    metals_symbols = normalize_metals_symbols(str(args.metals_symbols))

    # If watchlists.json exists, use it as defaults unless flags are explicitly provided.
    if file_wl.us and str(args.us_symbols) == "NVDA,META,ORCL":
        us_symbols = list(file_wl.us)
    if file_wl.hk and str(args.hk_symbols) == "00700,01810,03690":
        hk_symbols = list(file_wl.hk)
    if file_wl.cn and str(args.cn_symbols) == "SH600519,SZ000001,SH688256":
        cn_symbols = list(file_wl.cn)
    if file_wl.crypto and str(args.crypto_symbols) == "BTC_USDT,ETH_USDT":
        crypto_symbols = list(file_wl.crypto)
    if file_wl.metals and str(args.metals_symbols) == "XAUUSD,XAGUSD":
        metals_symbols = list(file_wl.metals)

    # compat: --quote-market + --quote-symbols overrides the corresponding list
    qmarket = str(args.quote_market).strip().lower()
    qsymbols = [s.strip() for s in str(args.quote_symbols).split(",") if s.strip()]
    if qmarket and qsymbols:
        if qmarket == "us_stock":
            us_symbols = normalize_us_symbols(",".join(qsymbols))
        elif qmarket == "hk_stock":
            hk_symbols = normalize_hk_symbols(",".join(qsymbols))
        elif qmarket == "cn_stock":
            cn_symbols = normalize_cn_symbols(",".join(qsymbols))
        elif qmarket == "crypto_spot":
            crypto_symbols = normalize_crypto_symbols(",".join(qsymbols))
        elif qmarket in {"metals", "metals_spot"}:
            metals_symbols = normalize_metals_symbols(",".join(qsymbols))

    quotes = QuoteConfigs(
        us=QuoteConfig(
            enabled=not bool(args.no_quote),
            provider=str(args.quote_provider),
            market="us_stock",
            symbols=us_symbols,
            refresh_s=max(0.2, float(args.quote_refresh)),
        ),
        hk=QuoteConfig(
            enabled=not bool(args.no_quote),
            provider=str(args.quote_provider),
            market="hk_stock",
            symbols=hk_symbols,
            refresh_s=max(0.2, float(args.quote_refresh)),
        ),
        cn=QuoteConfig(
            enabled=not bool(args.no_quote),
            provider=str(args.quote_provider),
            market="cn_stock",
            symbols=cn_symbols,
            refresh_s=max(0.2, float(args.quote_refresh)),
        ),
        crypto=QuoteConfig(
            enabled=not bool(args.no_quote),
            provider=str(args.crypto_provider),
            market="crypto_spot",
            symbols=crypto_symbols,
            refresh_s=max(0.2, float(args.quote_refresh)),
            timeout_s=max(1.0, float(args.crypto_timeout)),
        ),
        metals=QuoteConfig(
            enabled=not bool(args.no_quote),
            provider=str(args.metals_provider),
            market="metals",
            symbols=metals_symbols,
            refresh_s=max(0.2, float(args.quote_refresh)),
            timeout_s=max(1.0, float(args.metals_timeout)),
        ),
    )

    micro_candidates = normalize_crypto_symbols(str(args.micro_symbol))
    micro_symbol = micro_candidates[0] if micro_candidates else "BTC_USDT"
    micro_interval = int(args.micro_interval)
    if micro_interval not in {5, 10, 15, 30, 60}:
        micro_interval = 5
    micro_cfg = MicroConfig(
        symbol=micro_symbol,
        interval_s=micro_interval,
        window=max(10, int(args.micro_window)),
        flow_rows=max(10, int(args.micro_flow_rows)),
        refresh_s=max(0.2, float(args.micro_refresh)),
    )

    run(
        str(db_path),
        refresh_s=max(0.1, float(args.refresh)),
        limit=max(10, int(args.limit)),
        quotes=quotes,
        micro_cfg=micro_cfg,
        start_view=str(args.view),
        watchlists_path=str(watchlists_path),
        hot_reload=bool(args.hot_reload),
        hot_reload_poll_s=max(0.2, float(args.hot_reload_poll)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
