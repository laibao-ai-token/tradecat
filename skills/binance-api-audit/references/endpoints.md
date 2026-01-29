# 内部 API 端点与用法（全量覆盖）

> 说明：本清单来自代码扫描 + 手工补充 prefix。  
> 扫描范围：Datacat / Tradecat（preview + pro1）所有 FastAPI/Flask 服务。  
> 不包含与 Datacat/Tradecat 无关的 Express/其他框架服务（见“范围与排除”）。

---

## 0. 范围与排除

**纳入范围**
- Datacat API（`datacat/services/api-service/src/main.py`）
- Datacat Node（`datacat/services/api-service/src/node.py`）
- Datacat TG Node（`datacat/services/tg-node-services/src/main.py`）
- Tradecat Preview API（`tradecat/services-preview/api-service`）
- Tradecat Preview Vis（`tradecat/services-preview/vis-service`）
- Tradecat Preview Fate（`tradecat/services-preview/fate-service/services/telegram-service`）
- Tradecat Pro1 Preview API / Vis / Fate（`new/tradecat-pro1/control/preview/*`）
- External: MediaCrawler（`datacat/libs/external/MediaCrawler-main/api`）

**排除（非 Datacat/Tradecat 内部 API）**
- `new/clawdbot-main/*`（Express 服务）
- `new/tradecat-pro1/compute/preview/predict-service/*` 下 node_modules

---

## 1. Tradecat Preview API（services-preview/api-service）

**默认端口**：`API_SERVICE_PORT`（默认 8088）  
**基地址**：`http://127.0.0.1:8088`  
**数据源**：PostgreSQL/TimescaleDB + SQLite（`market_data.db` / cooldown DB）

### 1.1 端点总览（带前缀）

```
GET  /api/health
GET  /api/futures/supported-coins
GET  /api/futures/ohlc/history
GET  /api/futures/open-interest/history
GET  /api/futures/funding-rate/history
GET  /api/futures/metrics
GET  /api/futures/base-data
GET  /api/indicator/list
GET  /api/indicator/data
GET  /api/indicator/snapshot
GET  /api/signal/cooldown
```

### 1.2 详细用法（参数 + 数据源）

```
| Method | Path                          | Params (Query)                                                                 | Data Source                     | Notes |
|--------|-------------------------------|--------------------------------------------------------------------------------|----------------------------------|-------|
| GET    | /api/health                   | -                                                                              | -                                | 健康检查 |
| GET    | /api/futures/supported-coins  | -                                                                              | SQLite market_data.db            | 先读全局币种配置，兜底读库 |
| GET    | /api/futures/ohlc/history     | symbol*, exchange, interval*, limit, startTime, endTime                        | PG/Timescale: candles_*          | interval: 1m..1M |
| GET    | /api/futures/open-interest/history | symbol*, exchange, interval*, limit, startTime, endTime                    | PG/Timescale: metrics_*          | interval: 5m/15m/1h/4h/1d/1w |
| GET    | /api/futures/funding-rate/history  | symbol*, exchange, interval*, limit, startTime, endTime                    | PG/Timescale: metrics_*          | interval: 5m/15m/1h/4h/1d/1w |
| GET    | /api/futures/metrics          | symbol*, exchange, interval*, limit                                            | PG/Timescale: binance_futures_metrics_5m | interval: 1m/5m/15m/30m/1h/4h/12h/1d |
| GET    | /api/futures/base-data        | symbol*, interval*, limit, auto_resolve                                       | SQLite market_data.db            | 读取基础数据表（成交额/主动买卖比） |
| GET    | /api/indicator/list           | -                                                                              | SQLite market_data.db            | 指标表清单 |
| GET    | /api/indicator/data           | table*, symbol, interval, limit                                                 | SQLite market_data.db            | 表不存在返回错误 |
| GET    | /api/indicator/snapshot       | symbol*, panels, periods, include_base, include_pattern                         | SQLite market_data.db            | 复用 TG data_provider |
| GET    | /api/signal/cooldown          | -                                                                              | SQLite cooldown DB               | 冷却 key/时间 |
```

### 1.3 请求示例

```bash
# 支持币种
curl -sS http://127.0.0.1:8088/api/futures/supported-coins

# K线
curl -sS "http://127.0.0.1:8088/api/futures/ohlc/history?symbol=BTC&interval=1h&limit=200"

# 指标快照（完整）
curl -sS "http://127.0.0.1:8088/api/indicator/snapshot?symbol=BTC"

# 指标数据（单表）
curl -sS "http://127.0.0.1:8088/api/indicator/data?table=布林带扫描器&symbol=BTC&interval=1h&limit=200"
```

---

## 2. Tradecat Preview Vis（services-preview/vis-service）

**默认端口**：`VIS_SERVICE_PORT`（默认 8087）  
**基地址**：`http://127.0.0.1:8087`

```
| Method | Path                 | Params (Query / Body)                                             | Notes |
|--------|----------------------|--------------------------------------------------------------------|-------|
| GET    | /health              | -                                                                  | 健康检查 |
| GET    | /templates           | -                                                                  | 模板列表 |
| GET    | /kline-envelope      | symbol?, intervals?, limit?, exchange?, range_days?               | 返回 HTML 页面 |
| GET    | /kline-envelope/view | symbol*, intervals?, limit?, exchange?, startTime?, endTime?, range_days? | 返回 HTML 视图 |
| POST   | /render              | JSON: {template_id*, params?, output?} + Header: X-Vis-Token?      | 输出 png/json |
```

示例：
```bash
curl -sS http://127.0.0.1:8087/health
curl -sS "http://127.0.0.1:8087/kline-envelope?symbol=BTCUSDT"
```

---

## 3. Tradecat Preview Fate（services-preview/fate-service）

**默认端口**：`FATE_SERVICE_PORT`（默认 8001）  
**基地址**：`http://127.0.0.1:8001`

```
| Method | Path                          | Params (Body / Query)                         | Notes |
|--------|-------------------------------|----------------------------------------------|-------|
| GET    | /health                       | -                                            | 健康检查 |
| POST   | /api/v1/bazi/simple            | JSON: BaziRequest*                           | 简化八字 |
| POST   | /api/v1/bazi/calculate         | JSON: BaziRequest*, Query: user_id?          | 完整排盘 |
| POST   | /api/v1/liuyao/factor          | JSON: LiuyaoFactorRequest*                   | 六爻因子 |
| GET    | /api/v1/records/{record_id}    | -                                            | 取记录 |
| GET    | /api/v1/user/{user_id}/records | Query: biz_type?, limit?                     | 用户记录 |
| DELETE | /api/v1/records/{record_id}    | -                                            | 删记录 |
```

---

## 4. Tradecat Pro1 Preview（new/tradecat-pro1/control/preview/*）

**说明**：端点与 preview 版本基本一致，但 **缺少** `/api/indicator/snapshot`。  
端口默认与 preview 相同（API 8088 / VIS 8087 / FATE 8001）。

### 4.1 Pro1 API

```
GET /api/health
GET /api/futures/supported-coins
GET /api/futures/ohlc/history
GET /api/futures/open-interest/history
GET /api/futures/funding-rate/history
GET /api/futures/metrics
GET /api/indicator/list
GET /api/indicator/data
GET /api/signal/cooldown
```

### 4.2 Pro1 Vis / Fate

与 preview 端点一致（见第 2/3 节）。

---

## 5. Datacat API（services/api-service）

**默认端口**：`PORT`（默认 8000）  
**基地址**：`http://127.0.0.1:8000`  
**数据源**：SQLite `unified.db`

```
| Method | Path                | Params (Query / Body)                                      | Notes |
|--------|---------------------|-----------------------------------------------------------|-------|
| GET    | /health             | -                                                         | DB 连接检查 |
| GET    | /v1/events           | tag?, limit?, offset?, hours?, since_id?                  | 时序事件 |
| GET    | /v1/events/latest    | -                                                         | 最新事件 ID |
| GET    | /v1/calendar         | -                                                         | 经济日历快照 |
| GET    | /v1/positions        | segment?                                                  | Hyperliquid 持仓 |
| GET    | /v1/positions/summary| -                                                         | 持仓摘要 |
| GET    | /v1/tags             | -                                                         | 24h 标签统计 |
| GET    | /v1/stats            | -                                                         | 系统统计 |
| WS     | /v1/ws               | subscribe: {channels?, tags?}, ping                       | 事件/快照推送 |
```

示例：
```bash
curl -sS "http://127.0.0.1:8000/v1/events?limit=50"
curl -sS "http://127.0.0.1:8000/v1/events/latest"
```

WebSocket 订阅示例（消息体）：
```
{"action":"subscribe","channels":["events","calendar","positions"],"tags":["信号"]}
```

---

## 6. Datacat Node（services/api-service/src/node.py）

**默认端口**：`PORT`（默认 8000）  
**基地址**：`http://127.0.0.1:8000`  
**数据源**：内存缓存 + Telegram 实时流

```
| Method | Path             | Params (Query)                 | Notes |
|--------|------------------|--------------------------------|-------|
| GET    | /health          | -                              | 运行状态 |
| GET    | /v1/events       | tag?, limit?, since_id?        | 事件列表 |
| GET    | /v1/events/latest| -                              | 最新 ID |
| GET    | /v1/stats        | -                              | 缓存统计 |
| WS     | /v1/ws           | subscribe: {tags?}, ping       | 事件推送 |
```

---

## 7. Datacat TG Node（services/tg-node-services）

**默认端口**：`PORT`（默认 8000）  
**基地址**：`http://127.0.0.1:8000`  
**数据源**：内存缓存 + Telegram 实时流

```
| Method | Path             | Params (Query)                 | Notes |
|--------|------------------|--------------------------------|-------|
| GET    | /health          | -                              | 运行状态 |
| GET    | /v1/events       | tag?, limit?, since_id?        | 事件列表 |
| GET    | /v1/events/latest| -                              | 最新 ID |
| GET    | /v1/tags         | -                              | 标签统计 |
| GET    | /v1/stats        | -                              | 缓存统计 |
| WS     | /v1/ws           | subscribe: {tags?}, ping       | 事件推送 |
```

---

## 8. External: MediaCrawler（datacat/libs/external/MediaCrawler-main/api）

**默认端口**：`8080`  
**基地址**：`http://127.0.0.1:8080`

```
GET  /api/health
GET  /api/env/check
GET  /api/config/platforms
GET  /api/config/options
GET  /files
GET  /files/{file_path:path}
GET  /download/{file_path:path}
GET  /logs
GET  /status
GET  /stats
POST /start
POST /stop
WS   /ws/logs
WS   /ws/status
GET  /
```

> 备注：外部仓库/示例服务，非 Datacat/Tradecat 核心 API，仅作可选依赖记录。
