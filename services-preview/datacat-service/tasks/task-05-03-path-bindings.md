# 任务 05-03：日志/数据目录绑定

## 目标

- log_dir/data_dir 绑定新服务目录。

## 执行记录（已完成）

- 已在 `src/config.py` 绑定 DATACAT_LOG_DIR（默认 datacat-service/logs）
- 已在 `src/config.py` 绑定 DATACAT_DATA_DIR（默认 libs/database/csv）

## 验收

- 新旧服务互不干扰。
