# 任务 02-02：WS 内聚化

## 目标

- 将 WSCollector 及依赖内聚到新 <impl>.py。

## 执行记录（已完成）

- 已将 WSCollector、cryptofeed、timescale、rate_limiter、ccxt、backfill 相关逻辑内聚到目标 <impl>.py。
- 已移除对旧 data-service 路径的 import。

## 验收

- 无旧路径 import。
