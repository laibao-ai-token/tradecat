# 任务 01-03：原路径 → 新路径映射表

## 目标

- 为每个原模块给出严格分层路径。

## 映射表（核心）

| 原路径 | 新路径 |
|:---|:---|
| collectors/ws.py | binance/um_futures/all/realtime/push/ws/klines/interval_1m/cryptofeed/collector.py |
| collectors/metrics.py | binance/um_futures/all/realtime/pull/rest/metrics/interval_5m/http/collector.py |
| collectors/backfill.py (RestBackfiller) | binance/um_futures/all/backfill/pull/rest/klines/interval_1m/ccxt/collector.py |
| collectors/backfill.py (MetricsRestBackfiller) | binance/um_futures/all/backfill/pull/rest/metrics/interval_5m/http/collector.py |
| collectors/backfill.py (ZipBackfiller Klines) | binance/um_futures/all/backfill/pull/file/klines/interval_1m/http_zip/collector.py |
| collectors/backfill.py (ZipBackfiller Metrics) | binance/um_futures/all/backfill/pull/file/metrics/interval_5m/http_zip/collector.py |
| collectors/backfill.py (GapScanner) | 内聚至对应回填 collector 内部 |
| collectors/backfill.py (DataBackfiller) | 内聚至回填统一入口逻辑 |
| collectors/downloader.py | 融入 file/.../http_zip/collector.py |
| collectors/alpha.py | 内聚至 symbol_group 相关 collector |
| adapters/ccxt.py | 融入 REST K线补齐 collector（ccxt impl） |
| adapters/cryptofeed.py | 融入 WS K线 collector（cryptofeed impl） |
| adapters/timescale.py | 融入各 collector 内部 |
| adapters/rate_limiter.py | 融入 REST/FILE collectors |
| adapters/metrics.py | 融入 collectors 计数/计时 |
| config.py | datacat-service/src/config.py |
| __main__.py | datacat-service/src/__main__.py |

## 验收

- 覆盖率 100%。
