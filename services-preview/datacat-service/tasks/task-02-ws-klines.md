# 任务 02：WS K线采集迁移（细化版）

## 目标

- 迁移 WS K线采集逻辑到严格路径：
  `binance/um_futures/all/realtime/push/ws/klines/interval_1m/cryptofeed/collector.py`

## 子任务拆分

- 02-01：WS wrapper 复用旧逻辑
- 02-02：WS 逻辑内聚化（去旧路径 import）
- 02-03：WS 回填依赖落位与调用链确认

## 关键依赖

- backfill: GapScanner / RestBackfiller / ZipBackfiller
- adapters: cryptofeed / timescale / metrics
- config: ws_gap_interval / ws_source / db_exchange / ccxt_exchange

## 执行步骤（更细）

1) 先用 wrapper 方式直接调用旧 `WSCollector`，确保能运行。
2) 将 `WSCollector` 复制进新 collector.py。
3) 将 cryptofeed/timecale/metrics 依赖内聚进同一文件。
4) 明确 `_smart_backfill` 依赖路径与回填逻辑落位。
5) 保持批量写入与缺口巡检策略一致。

## 验收

- 采集逻辑等价。
- 缓冲写入/缺口巡检/补齐逻辑不丢失。
- 入口参数兼容原服务。
