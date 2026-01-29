# 配置矩阵（DATACAT 优先）

```
+--------------------------+--------------------------------------+-------------------------------------------+
| 配置项                   | 默认值                               | 回退变量                                  |
|--------------------------+--------------------------------------+-------------------------------------------|
| DATACAT_DATABASE_URL     | postgresql://postgres:...:5433/...   | DATABASE_URL                              |
| DATACAT_HTTP_PROXY       | None                                 | HTTP_PROXY / DATACAT_HTTPS_PROXY/HTTPS_PROXY |
| DATACAT_LOG_DIR          | services-preview/datacat-service/logs| DATA_SERVICE_LOG_DIR                      |
| DATACAT_LOG_LEVEL        | INFO                                 | DATA_SERVICE_LOG_LEVEL                    |
| DATACAT_LOG_FORMAT       | plain                                | DATA_SERVICE_LOG_FORMAT                   |
| DATACAT_LOG_FILE         | None                                 | DATA_SERVICE_LOG_FILE                     |
| DATACAT_DATA_DIR         | libs/database/csv                    | DATA_SERVICE_DATA_DIR                     |
| DATACAT_WS_GAP_INTERVAL  | 600                                  | BINANCE_WS_GAP_INTERVAL                   |
| DATACAT_WS_GAP_LOOKBACK  | 10080                                | BINANCE_WS_GAP_LOOKBACK                   |
| DATACAT_WS_SOURCE        | binance_ws                           | BINANCE_WS_SOURCE                         |
| DATACAT_DB_SCHEMA        | market_data                          | KLINE_DB_SCHEMA                           |
| DATACAT_DB_EXCHANGE      | binance_futures_um                   | BINANCE_WS_DB_EXCHANGE                    |
| DATACAT_CCXT_EXCHANGE    | binance                              | BINANCE_WS_CCXT_EXCHANGE                  |
| DATACAT_RATE_LIMIT_PER_MINUTE | 1800                            | RATE_LIMIT_PER_MINUTE                     |
| DATACAT_MAX_CONCURRENT   | 5                                    | MAX_CONCURRENT                            |
| DATACAT_BACKFILL_MODE    | days                                 | BACKFILL_MODE                             |
| DATACAT_BACKFILL_DAYS    | 30                                   | BACKFILL_DAYS                             |
| DATACAT_BACKFILL_START_DATE | None                              | BACKFILL_START_DATE                       |
| DATACAT_BACKFILL_ON_START | false                               | BACKFILL_ON_START                         |
| DATACAT_OUTPUT_MODE      | db                                   | DATA_SERVICE_OUTPUT_MODE                  |
| DATACAT_JSON_DIR         | services-preview/datacat-service/data-json | DATA_SERVICE_JSON_DIR               |
+--------------------------+--------------------------------------+-------------------------------------------+
```

说明：
- 读取逻辑集中在 `src/config.py`  
- DATACAT_* 优先级最高  
