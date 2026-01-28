# 任务 01-01：逐文件扫描函数/类清单

## 目标

- 为 collectors/adapters/config/__main__ 建立函数/类清单。

## 输出

- 清单表（文件 → 类/函数）

## 步骤

1) 逐文件读取并记录类/函数名。
2) 标注职责与依赖。

## 执行记录（已完成）

- __main__.py
  - classes: Scheduler
  - functions: main
- adapters/ccxt.py
  - classes: _CompatLimiter
  - functions: _parse_list, get_client, load_symbols, fetch_ohlcv, to_rows, normalize_symbol, _check_and_wait_ban, async_acquire, async_check_and_wait_ban
- adapters/cryptofeed.py
  - classes: CandleEvent, BinanceWSAdapter
  - functions: preload_symbols
- adapters/metrics.py
  - classes: Metrics, Timer
- adapters/rate_limiter.py
  - classes: GlobalLimiter
  - functions: acquire, release, set_ban, parse_ban, get_limiter
- adapters/timescale.py
  - classes: TimescaleAdapter
- collectors/alpha.py
  - classes: AlphaTokenFetcher
  - functions: _normalize_symbol, refresh_alpha_tokens, main
- collectors/backfill.py
  - classes: GapInfo, GapScanner, RestBackfiller, MetricsRestBackfiller, ZipBackfiller, DataBackfiller, GapFiller
  - functions: get_backfill_config, compute_lookback, main
- collectors/downloader.py
  - classes: RateLimiterProtocol, Downloader
- collectors/metrics.py
  - classes: MetricsCollector
  - functions: _to_decimal, main
- collectors/ws.py
  - classes: WSCollector
  - functions: main
- config.py
  - classes: Settings, GapTask
  - functions: _int_env, normalize_interval

## 验收

- 覆盖所有 .py 文件。
