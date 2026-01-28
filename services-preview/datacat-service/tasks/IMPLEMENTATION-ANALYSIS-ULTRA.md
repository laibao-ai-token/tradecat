# 实现分析（极致细粒度）— 旧 data-service → 新 datacat-service

> 约束：只读 `/services/data-service`，所有改动仅发生在 `/services-preview/datacat-service`。

---

## 1. 旧服务能力清单（只读源）

### 1.1 collectors
- `collectors/ws.py`
  - WebSocket 1m K线采集
  - 缓冲批量写入
  - 缺口巡检 + 回填触发
- `collectors/metrics.py`
  - REST 期货指标采集（5m）
  - 并发采集 + 限流处理
- `collectors/backfill.py`
  - GapScanner（缺口精确扫描）
  - REST 回填（Klines）
  - ZIP 回填（Klines）
  - Metrics REST 回填
  - ZIP 文件解析/写库
- `collectors/downloader.py`
  - 通用 ZIP 文件下载器（代理/重试）
- `collectors/alpha.py`
  - Alpha 代币列表拉取与缓存

### 1.2 adapters
- `adapters/ccxt.py`：交易所 Kline 拉取
- `adapters/cryptofeed.py`：WebSocket 订阅
- `adapters/metrics.py`：采集统计
- `adapters/rate_limiter.py`：限流与 ban 处理
- `adapters/timescale.py`：Timescale 写入

---

## 2. 新结构目标（必须落位）

```
collectors/binance/um_futures/all/
├── realtime/push/ws/klines/cryptofeed.py
├── realtime/pull/rest/metrics/http.py
├── backfill/pull/rest/klines/ccxt.py
├── backfill/pull/rest/metrics/http.py
├── backfill/pull/file/klines/http_zip.py
├── backfill/pull/file/metrics/http_zip.py
└── sync/pull/rest/alpha/http.py
```

说明：
- 粒度（1m/5m/depth）仅在 `impl.py` 内处理  
- ZIP 与 REST 必须彻底分离  
- WS 只保留实时采集，回填逻辑外移  

---

## 3. 旧 → 新 映射表（ASCII）

```
+------------------------------+---------------------------------------------------------------+------------------------------+
| 旧文件                       | 新目标路径                                                    | 保留/拆分要点                |
|------------------------------+---------------------------------------------------------------+------------------------------|
| collectors/ws.py             | .../realtime/push/ws/klines/cryptofeed.py                     | 保留 WS/缓冲/写库；回填外移 |
| collectors/metrics.py        | .../realtime/pull/rest/metrics/http.py                        | 保留并发拉取/限流           |
| collectors/backfill.py       | .../backfill/pull/rest/klines/ccxt.py                          | 仅 REST Klines 分页回填     |
| collectors/backfill.py       | .../backfill/pull/rest/metrics/http.py                         | 仅 REST Metrics 回填        |
| collectors/backfill.py       | .../backfill/pull/file/klines/http_zip.py                      | 仅 ZIP Klines 回填          |
| collectors/backfill.py       | .../backfill/pull/file/metrics/http_zip.py                     | 仅 ZIP Metrics 回填         |
| collectors/downloader.py     | 合并入 http_zip.py                                             | 下载器内嵌                  |
| collectors/alpha.py          | .../sync/pull/rest/alpha/http.py                               | 原样迁移                    |
| adapters/*                   | 可复制到 new adapters/ 或内嵌到 impl 文件                      | 视实现策略选择              |
| __main__.py                  | 调度新路径（WS/REST/ZIP/ALPHA）                                | 入口覆盖                     |
+------------------------------+---------------------------------------------------------------+------------------------------+
```

---

## 4. 每个目标文件的迁移步骤（逐动作）

### 4.1 WS Klines（cryptofeed.py）
1) 读取旧 `collectors/ws.py`  
2) 提取：WebSocket 订阅回调、缓冲队列、批量写入  
3) 保留：batch flush / max buffer / flush window  
4) 移除：GapScanner / RestBackfiller / ZipBackfiller 逻辑  
5) 将回填触发改为“调度器可调用入口”  
6) 修正 config 引用为 datacat-service 的 `config.settings`  
7) 确认依赖：
   - 若使用 adapters → 复制 adapters  
   - 若内嵌 → 将 adapters 逻辑合并到文件  

### 4.2 REST Metrics（http.py）
1) 读取旧 `collectors/metrics.py`  
2) 提取：`_get`、`_collect_one`、`collect`、`save`、`run_once`  
3) 保留：全局限流处理（429/418）  
4) 保留：并发采集逻辑（ThreadPoolExecutor）  
5) 修正配置读取（proxy、rate limit、db）  
6) 添加 `main()` 入口保持可运行  

### 4.3 REST Backfill Klines（ccxt.py）
1) 读取旧 `collectors/backfill.py`  
2) 提取：`RestBackfiller.fill_kline_gap` + `fill_gaps`  
3) 保留：分页逻辑 + INTERVAL_TO_MS  
4) 移除：ZIP 与 Metrics 相关逻辑  
5) 依赖：
   - `fetch_ohlcv / to_rows`（来自 adapters/ccxt）  
6) 添加 `main()` 或 `run_once` 入口  

### 4.4 REST Backfill Metrics（http.py）
1) 读取旧 `collectors/backfill.py`  
2) 提取：`MetricsRestBackfiller`  
3) 保留：5个 API 合并逻辑  
4) 保留：限流与 ban 处理  
5) 修正配置读取  

### 4.5 ZIP Backfill Klines（http_zip.py）
1) 读取旧 `collectors/backfill.py` + `collectors/downloader.py`  
2) 提取：ZIP 下载 / 解压 / CSV 解析 / 写库  
3) 合并 downloader 逻辑到文件内部  
4) 保留：文件缓存与失败重试策略  
5) 移除 REST 相关逻辑  

### 4.6 ZIP Backfill Metrics（http_zip.py）
1) 同上，抽离 Metrics ZIP 逻辑  
2) 保留 CSV 解析、字段映射、写库  
3) 与 klines ZIP 逻辑保持解耦  

### 4.7 Alpha（http.py）
1) 读取旧 `collectors/alpha.py`  
2) 迁移：缓存策略、解析逻辑  
3) 保留：代理 + ban 处理  
4) 入口保持 async 可运行  

---

## 5. 配置与环境变量映射

```
旧变量/字段                  -> 新 settings
HTTP_PROXY                  -> settings.http_proxy
DATABASE_URL                -> settings.database_url
RATE_LIMIT_PER_MINUTE       -> settings.rate_limit_per_minute
MAX_CONCURRENT              -> settings.max_concurrent
BINANCE_WS_GAP_INTERVAL      -> settings.ws_gap_interval
BINANCE_WS_SOURCE            -> settings.ws_source
```

注意：
1) 若仅设置 DATACAT_HTTP_PROXY，则必须生效  
2) 旧服务默认值需完整保留  

---

## 6. 不得丢失的行为清单（验收点）

1) WS 批量写入缓冲与时间窗口策略  
2) REST Metrics 并发与限流  
3) GapScanner 精确缺口检测  
4) REST 回填分页策略  
5) ZIP 回填全流程（下载 → 解压 → 解析 → 写库）  
6) Alpha 缓存与 6h TTL  
7) 入口可运行（WS/REST/ZIP/ALPHA）  

