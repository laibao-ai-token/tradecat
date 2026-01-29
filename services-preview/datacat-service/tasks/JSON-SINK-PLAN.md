# JSON 写入替代方案（测试用）— 任务规划

> 目标：不写入生产库，所有采集结果追加写入 JSON（JSONL）文件，作为测试输出。

---

## 1. 约束与原则

- 只改 `services-preview/datacat-service`  
- 不改旧服务  
- 输出为 **JSON Lines**（追加写入）  
- 采集逻辑不阉割，仅替换“落库动作”  
- 目录层级不改变  

---

## 2. 输出路径定义

```
services-preview/datacat-service/data-json/
├── candles_1m.jsonl
├── metrics_5m.jsonl
└── ...（按 type/interval 扩展）
```

---

## 3. 配置改动

1) `src/config.py` 新增配置：
   - `DATACAT_OUTPUT_MODE`（db/json）
   - `DATACAT_JSON_DIR`
2) 默认保持 `db`，测试时切 `json`

---

## 4. 代码改动清单

### 4.1 新增公共 JSON Sink

路径：`src/pipeline/json_sink.py`

功能：
- 追加写入 JSONL  
- 目录不存在自动创建  
- 统一调用入口（append_jsonl）

### 4.2 采集器落库替换（仅写入层）

替换目标（写库函数）：

- `.../realtime/push/ws/klines/cryptofeed.py`  
  - `TimescaleAdapter.upsert_candles`  
  - `TimescaleAdapter.upsert_metrics`

- `.../realtime/pull/rest/metrics/http.py`  
  - `TimescaleAdapter.upsert_metrics`

- `.../backfill/pull/file/klines/http_zip.py`  
  - `TimescaleAdapter.upsert_candles`

- `.../backfill/pull/file/metrics/http_zip.py`  
  - `TimescaleAdapter.upsert_metrics`

- `.../backfill/pull/rest/klines/ccxt.py`  
  - `TimescaleAdapter.upsert_candles`

- `.../backfill/pull/rest/metrics/http.py`  
  - `TimescaleAdapter.upsert_metrics`

替换规则：
```
if settings.output_mode == "json":
    return append_jsonl(json_path("<kind>"), rows)
```

---

## 5. 文档同步

1) `README.md`：增加 JSON 输出说明  
2) `src/collectors/README.md`：补充测试输出约定  
3) `AGENTS.md`：新增 pipeline/json_sink 的职责说明  

---

## 6. 验证步骤

1) `DATACAT_OUTPUT_MODE=json`  
2) 运行任意采集器  
3) 验证 `data-json/*.jsonl` 有追加输出  
4) 确认未写入数据库（不调用 upsert SQL）  

