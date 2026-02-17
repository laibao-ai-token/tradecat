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

可选：
- 仅看行情，不自动启动 data/signal：`TUI_AUTO_START_DATA=0 TUI_AUTO_START_SIGNAL=0 ./scripts/start.sh run`
- 退出 TUI 立即停止 data：`TUI_DATA_STOP_DELAY_SECONDS=0 ./scripts/start.sh run`
- 退出 TUI 立即停止 signal：`TUI_SIGNAL_STOP_DELAY_SECONDS=0 ./scripts/start.sh run`
- 退出 TUI 后保留自动启动的 data：`TUI_KEEP_DATA_ON_EXIT=1 ./scripts/start.sh run`
- 退出 TUI 后保留自动启动的 signal：`TUI_KEEP_SIGNAL_ON_EXIT=1 ./scripts/start.sh run`

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
- `t` / `Tab`: US -> CN -> MICRO -> BACKTEST -> US
- `1` US, `2` CN, `3` MICRO, `4` BACKTEST, `0` home(MICRO)
- `+` add symbols, `-` remove symbols (per current page)
  - `Enter` confirm, `Esc` cancel

Override watchlists:

```bash
./scripts/start.sh run --view quotes_hk --hk-symbols 00700,01810,03690 --quote-refresh 1
./scripts/start.sh run --view quotes_cn --cn-symbols SH600519,SZ000001,SH688256 --quote-refresh 1
./scripts/start.sh run --view quotes_crypto --crypto-symbols BTC_USDT,ETH_USDT --quote-refresh 1
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

Foreground (debug):

```bash
cd services-preview/tui-service
python -m src --refresh 1
```

## Key Bindings

- `q`: quit
- Arrow keys: scroll
- `t` / `Tab`: cycle pages (US/CN/MICRO/BACKTEST)
- `1`/`2`/`3`/`4`: jump to US/CN/MICRO/BACKTEST
- `0`: jump to home (MICRO)
- `p`: toggle PG source
- `s`: toggle SQLite source
- `b`: toggle BUY
- `e`: toggle SELL
- `a`: toggle ALERT
- `space`: pause/resume auto refresh
