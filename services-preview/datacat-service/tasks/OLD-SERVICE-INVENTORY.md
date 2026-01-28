# 旧服务清点（只读）

## 1. 旧服务路径与约束

- 旧服务路径：`/home/lenovo/.projects/tradecat/services/data-service`
- 约束：只读，不做任何修改

---

## 2. 旧 collectors 清单

```
collectors/
├── ws.py
├── metrics.py
├── backfill.py
├── alpha.py
└── downloader.py
```

职责摘要：
- `ws.py`：WS 1m K线 + 缓冲批量写入 + 缺口触发
- `metrics.py`：REST 5m 指标采集
- `backfill.py`：Gap 扫描 + REST/ZIP 回填
- `alpha.py`：Alpha 代币列表
- `downloader.py`：ZIP 下载器

---

## 3. 旧 adapters 清单

```
adapters/
├── ccxt.py
├── cryptofeed.py
├── metrics.py
├── rate_limiter.py
└── timescale.py
```

---

## 4. 旧入口与运行方式（来自 README）

```
PYTHONPATH=src python3 -m collectors.ws
PYTHONPATH=src python3 -m collectors.metrics
PYTHONPATH=src python3 -m collectors.backfill --all
```

---

## 5. 旧配置项与环境变量（来自 README）

```
+--------------------------+-------------------------------------------+
| 变量                     | 说明                                      |
|--------------------------+-------------------------------------------|
| DATABASE_URL             | TimescaleDB 连接串                        |
| HTTP_PROXY               | 代理地址                                  |
| RATE_LIMIT_PER_MINUTE    | API 限流                                  |
| MAX_CONCURRENT           | 最大并发                                  |
| BINANCE_WS_GAP_INTERVAL  | 缺口巡检间隔（秒）                         |
| BINANCE_WS_SOURCE        | 数据来源标识                              |
| BINANCE_WS_GAP_LOOKBACK  | 缺口回溯范围（分钟，若旧版存在）           |
| KLINE_DB_SCHEMA          | K线 schema（若旧版存在）                   |
| BINANCE_WS_DB_EXCHANGE   | 交易所标识（若旧版存在）                   |
| BINANCE_WS_CCXT_EXCHANGE | CCXT 交易所名（若旧版存在）                |
| BACKFILL_MODE            | 回填模式（若旧版存在）                     |
| BACKFILL_DAYS            | 回填天数（若旧版存在）                     |
| BACKFILL_START_DATE      | 起始日期（若旧版存在）                     |
| BACKFILL_ON_START        | 启动回填开关（若旧版存在）                 |
| DATA_SERVICE_LOG_DIR     | 日志目录（若旧版存在）                     |
| DATA_SERVICE_DATA_DIR    | 数据目录（若旧版存在）                     |
| DATACAT_*                | 新服务优先级更高（迁移后使用）             |
+--------------------------+-------------------------------------------+
```

