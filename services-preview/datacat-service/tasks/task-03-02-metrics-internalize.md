# 任务 03-02：Metrics 内聚化

## 目标

- 内聚 MetricsCollector 逻辑到新 <impl>.py。

## 执行记录（已完成）

- 已将 MetricsCollector、rate_limiter、timescale、metrics、ccxt 符号加载逻辑内聚到目标 <impl>.py。
- 已移除对旧 data-service 路径的 import。

## 验收

- 无旧路径 import。
