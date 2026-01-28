# 任务 05-01：配置字段对齐与命名策略

## 目标

- 复刻原 Settings 字段，并加入 DATACAT_* 覆盖规则。

## 执行记录（已完成）

- 已更新 `src/config.py`，包含 database_url/http_proxy/log_dir/data_dir/ws_gap/ccxt/db_schema 等字段，且支持 DATACAT_* 覆盖。

## 验收

- 字段齐全。
