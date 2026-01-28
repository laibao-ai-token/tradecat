# 任务 03-01：Metrics wrapper 复用旧逻辑

## 目标

- wrapper 调用 MetricsCollector。

## 执行记录（已完成）

- 已在目标路径创建 wrapper：
  `services-preview/datacat-service/src/collectors/binance/um_futures/all/realtime/pull/rest/metrics/interval_5m/http/collector.py`
- 通过 `_legacy_src()` 定位旧服务并导入 `collectors.metrics.MetricsCollector`

## 验收

- 可运行。
