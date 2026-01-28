# Bugfix-01：回填解耦与入口修复

## 目标

- REST 与 ZIP 回填职责彻底拆分。
- pipeline 按“ZIP → REST”顺序执行。

## 变更点

- 裁剪 REST K线回填，只保留 REST 逻辑。
- 保留 ZIP K线/ZIP Metrics 各自独立采集器。
- __main__ 引入回填 pipeline 顺序执行。

## 验收

- REST/ZIP 逻辑不再重复。
- backfill 执行顺序固定：ZIP → REST。
