# tui-service (preview)

Terminal UI (TUI) consumer for TradeCat signals.

- Reads signal history from: `libs/database/services/signal-service/signal_history.db`
- Shows both sources: `pg` and `sqlite` (as recorded by `signal-service`)
- No Telegram / no API key required

## Quick Start

```bash
cd services-preview/tui-service
./scripts/start.sh run
```

`run`/`start` 会自动尝试启动 `data-service` 和 `signal-service`（若未运行）。

默认：
- 退出 TUI 后 1 小时自动停止“由 TUI 启动”的 data-service。
- 退出 TUI 后 1 小时自动停止“由 TUI 启动”的 signal-service。
- 内置右侧 `Agent Shell` 占位已默认关闭，避免与真实 `openclaw tui` 语义混淆。

可选：
- 仅看行情，不自动启动 data/signal：`TUI_AUTO_START_DATA=0 TUI_AUTO_START_SIGNAL=0 ./scripts/start.sh run`
- 退出 TUI 立即停止 data：`TUI_DATA_STOP_DELAY_SECONDS=0 ./scripts/start.sh run`
- 退出 TUI 立即停止 signal：`TUI_SIGNAL_STOP_DELAY_SECONDS=0 ./scripts/start.sh run`
- 退出 TUI 后保留自动启动的 data：`TUI_KEEP_DATA_ON_EXIT=1 ./scripts/start.sh run`
- 退出 TUI 后保留自动启动的 signal：`TUI_KEEP_SIGNAL_ON_EXIT=1 ./scripts/start.sh run`
- 如需临时打开旧的内置 Agent 占位壳层做对照，可显式设置：`TUI_ENABLE_AGENT_PLACEHOLDER=1 ./scripts/start.sh run`

Show real-time quotes (no API key, Tencent):

```bash
cd services-preview/tui-service
./scripts/start.sh run --view quotes_us --quote-refresh 1
```

Run micro market page (default start view):

```bash
cd services-preview/tui-service
./scripts/start.sh run --view market_micro --micro-symbol BTC_USDT --micro-interval 5
```

Run backtest TUI slice (read-only placeholder first):

```bash
cd services-preview/tui-service
./scripts/start.sh run --view market_backtest
```

Optional:
- Show compare panel (history vs rule) if comparison artifacts exist: `TUI_BACKTEST_SHOW_COMPARE=1 ./scripts/start.sh run --view market_backtest`

Micro page options:
- `--micro-symbol` (default `BTC_USDT`)
- `--micro-interval` (default `5`, allowed: `5/10/15/30/60`)
- `--micro-window` (default `60` candles)
- `--micro-flow-rows` (default `30`)
- `--micro-refresh` (default `0.5` seconds)

Notes:
- Default start view is `market_micro` (can still override with `--view ...`).
- `market_micro` is included in in-app page cycling.

Switch pages inside TUI:
- `t` / `Tab`: US -> CN -> HK -> FUND-CN -> MICRO -> US（主行情页循环，不含回测）
- `1` US, `2` CN, `3` MICRO, `5` FUND-CN, `6` HK, `0` home(MICRO)
- `4`: 仅在 MICRO(加密) 页可打开回测；在回测页按 `4` 返回 MICRO
- `+` add symbols, `-` remove symbols (per current page)
  - `Enter` confirm, `Esc` cancel

Override watchlists:

```bash
./scripts/start.sh run --view quotes_hk --hk-symbols 00700,01810,03690 --quote-refresh 1
./scripts/start.sh run --view quotes_cn --cn-symbols SH600519,SZ000001,SH688256 --quote-refresh 1
./scripts/start.sh run --view market_fund_cn --fund-cn-symbols SH510300,SZ159915,SH512100 --quote-refresh 1
./scripts/start.sh run --view quotes_crypto --crypto-symbols BTC_USDT,ETH_USDT --quote-refresh 1
```

`market_fund_cn` supports mixed monitoring:
- Exchange-traded ETF/LOF (`SH/SZ` + 6 digits, realtime)
- Off-market public funds (`6-digit fund code`, valuation/NAV based)

Example (on-market + off-market):

```bash
./scripts/start.sh run --view market_fund_cn --fund-cn-symbols SH516110,SZ159889,024389,021490 --quote-refresh 1
```

Crypto symbol input tips:
- You can type `DOGE` and it will be treated as `DOGE_USDT`
- You can also type `DOGEUSDT` / `DOGE-USDT` / `DOGE_USDT`

If you see intermittent `URLError` on crypto page, increase timeout (or switch provider):

```bash
./scripts/start.sh run --view quotes_crypto --crypto-timeout 20 --quote-refresh 1
./scripts/start.sh run --view quotes_crypto --crypto-provider htx --quote-refresh 1
./scripts/start.sh run --view quotes_crypto --crypto-provider gate --quote-refresh 1
```

Watchlists are persisted to: `services-preview/tui-service/watchlists.json` (auto-created on first change).

Collect + view (starts markets-service `equity-poll` in background, then runs TUI; data-service/signal-service will also auto-start if needed):

```bash
export MARKETS_SERVICE_DATABASE_URL="postgresql://postgres:postgres@localhost:5434/market_data"
cd services-preview/tui-service
./scripts/start.sh run-equity us_stock nasdaq NVDA 60 5
```

News page note:
- TUI news is now DB-first: it reads `alternative.news_articles` first, and only falls back to local direct/RSS fetching when the unified news DB is unavailable or still empty. The news header now separates `同步=...前`, `最新=...前`, and `健=H/F/C` (healthy/failing/cooldown).
- Database URL resolution order is `TUI_NEWS_DATABASE_URL` -> `MARKETS_SERVICE_DATABASE_URL` -> `DATABASE_URL` -> `config/.env` -> built-in default `postgresql://postgres:postgres@localhost:5434/market_data`. For local URLs, the reader will also retry common local ports `5434/5433/5432` automatically.
- By default, the fallback live-source set uses mixed fast/news coverage: direct fast-news connectors (`direct://jin10`, `direct://10jqka/realtimenews`, `direct://sina/7x24`, `direct://eastmoney/kuaixun`, `direct://cls/telegraph`, `direct://gelonghui/live`, `direct://wallstreetcn/live`, `direct://eeo/kuaixun`) plus public RSS feeds such as GlobeNewswire / SEC press releases / FXStreet / Cointelegraph, and the curated `worldmonitor_trading` RSS subset (known unstable feeds are excluded from the default preset).
- To override the fallback live sources, set `TUI_NEWS_RSS_FEEDS` before launch; it accepts both RSS URLs and direct specs such as `direct://jin10`. `NEWS_RSS_FEEDS` still works for plain RSS URLs. TUI fetches these feeds in parallel and the default per-source timeout is 5 seconds (`TUI_NEWS_RSS_TIMEOUT_S`).
- To keep only the original fast-news layer, set `TUI_NEWS_RSS_PRESET="core"` (or `NEWS_RSS_PRESET="core"` if you want the same preset shared with `markets-service`).
- To keep a 7x24 collector running in the background while viewing the TUI, use `./scripts/start.sh run-news 2`.

Foreground (debug):

```bash
cd services-preview/tui-service
python -m src --refresh 1
```

## Key Bindings

- `q`: quit
- Arrow keys: scroll
- `t` / `Tab`: cycle main market pages (US/CN/HK/FUND-CN/MICRO)
- `1`/`2`/`3`/`5`/`6`: jump to US/CN/MICRO/FUND-CN/HK
- `4`: toggle backtest only from MICRO page; press again in backtest to return MICRO
- `0`: jump to home (MICRO)
- `p`: toggle PG source
- `s`: toggle SQLite source
- `b`: toggle BUY
- `e`: toggle SELL
- `a`: toggle ALERT
- `space`: pause/resume auto refresh
