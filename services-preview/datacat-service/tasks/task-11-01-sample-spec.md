# 任务 11-01：对照样本窗口与符号定义

## 目标

- 明确样本范围。

## 样本定义（固定，不随运行变化）

- 交易所：Binance U 本位永续
- 符号：BTCUSDT、ETHUSDT
- 数据类型：
  - K线：1m
  - 指标：5m（openInterest/LSR 等）
- 实时窗口：最近 3 小时（按 UTC 向下取整到 5m）
- 回填窗口：最近 3 天（D-3 ~ D-1，按 UTC 自然日）

## 边界与过滤

- 仅统计 `exchange = binance_futures_um` 的数据。
- K线按 `bucket_ts` 过滤；指标按 `create_time` 过滤。
- 缺口判定阈值：95% 完整度。

## 样本输出字段（用于一致性对照）

- K线：symbol、bucket_ts、open、high、low、close、volume、source、is_closed
- Metrics：symbol、create_time、sum_open_interest、sum_open_interest_value、count_toptrader_long_short_ratio、sum_toptrader_long_short_ratio、count_long_short_ratio、sum_taker_long_short_vol_ratio、source、is_closed

## 验收

- 样本清单完整。
