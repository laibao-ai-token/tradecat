# Datacat Service 验收报告

## 1. 元信息

- 日期：2026-01-29
- 版本：git a95ada16
- 运行人：auto
- 环境：local
- 代理：http://127.0.0.1:7890

## 2. 样本说明

- 符号：BTCUSDT, ETHUSDT
- 实时窗口：2026-01-29 04:30 ~ 2026-01-29 07:30 UTC
- 回填窗口：2026-01-26 ~ 2026-01-28 (UTC 自然日)
- 数据类型：K线 1m、Metrics 5m

## 3. 对照指标

### 3.1 字段一致性

- K线字段：✅
- Metrics 字段：✅

### 3.2 行数一致性

- 实时窗口：误差 <= 1%
  - K线期望行数：360
  - K线实际行数：360
  - K线误差：0.00%
  - Metrics期望行数：72
  - Metrics实际行数：72
  - Metrics误差：0.00%

- 回填窗口：误差 <= 0.5%
  - K线期望行数：8640
  - K线实际行数：8640
  - K线误差：0.00%
  - Metrics期望行数：1728
  - Metrics实际行数：1728
  - Metrics误差：0.00%

### 3.3 时间对齐

- K线：bucket_ts 对齐到 1m
- Metrics：create_time 对齐到 5m
- 最大偏移：K线 0s / Metrics 0s

### 3.4 来源一致性

- K线来源：binance_ws, ccxt_gap, binance_zip
- Metrics来源：binance_api, binance_rest, binance_zip

## 4. 偏差分析

- 主要偏差：未发现
- 原因分析：数据齐全且时间对齐
- 影响范围：无

## 5. 结论

- ✅ 验收通过
- 结论说明：满足当前阈值
