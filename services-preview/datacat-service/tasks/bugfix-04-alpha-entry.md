# Bugfix-04：Alpha 调度入口补齐

## 目标

- Alpha 采集器可由入口调度执行。

## 变更点

- __main__ 增加 --alpha 选项。
- 文档说明同步。

## 验收

- `python src/__main__.py --alpha` 可执行。
