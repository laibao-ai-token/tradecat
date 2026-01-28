# 任务 02-03：WS 回填依赖落位

## 目标

- 确保 _smart_backfill 调用链完整。

## 执行记录（已完成）

- GapScanner/RestBackfiller/ZipBackfiller 已内聚到 WS collector。
- _smart_backfill 直接调用本文件内类，调用链完整。

## 验收

- 回填触发逻辑可执行。
