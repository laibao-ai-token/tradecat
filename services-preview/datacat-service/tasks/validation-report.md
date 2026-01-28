# Datacat Service 验收报告模板

## 1. 元信息

- 日期：YYYY-MM-DD
- 版本：git <short_sha>
- 运行人：<name>
- 环境：<env>
- 代理：<proxy>

## 2. 样本说明

- 符号：BTCUSDT、ETHUSDT
- 实时窗口：最近 3 小时（UTC）
- 回填窗口：最近 3 天（D-3 ~ D-1）
- 数据类型：K线 1m、Metrics 5m

## 3. 对照指标

### 3.1 字段一致性

- K线字段：
  - ✅/❌ 列名一致
  - ✅/❌ 类型一致
  - ✅/❌ 精度一致

- Metrics 字段：
  - ✅/❌ 列名一致
  - ✅/❌ 类型一致
  - ✅/❌ 精度一致

### 3.2 行数一致性

- 实时窗口：误差 <= 1%
  - 旧服务行数：
  - 新服务行数：
  - 误差：

- 回填窗口：误差 <= 0.5%
  - 旧服务行数：
  - 新服务行数：
  - 误差：

### 3.3 时间对齐

- K线：bucket_ts 对齐到 1m
- Metrics：create_time 对齐到 5m
- 最大偏移：<= 1 个周期

### 3.4 来源一致性

- ZIP：source=binance_zip
- REST：source=binance_rest/ccxt_gap
- WS：source=binance_ws

## 4. 偏差分析

- 主要偏差：
- 原因分析：
- 影响范围：

## 5. 结论

- ✅/❌ 验收通过
- 结论说明：

