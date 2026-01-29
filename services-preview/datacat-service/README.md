# Datacat Service (Preview) — 严格分层基建

本服务用于承载“严格分层”的数据采集目录结构，并内置可运行的采集/回填实现。

---

## 1. 分层顺序（不可变）

```
source → market → scope → mode → direction → channel → type
```

> 粒度（interval/depth）不再作为目录层级，统一在实现文件内部处理。

---

## 2. 目录结构（固定模板）

```
datacat-service/
├── AGENTS.md
├── README.md
├── Makefile
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── requirements.lock.txt
├── scripts/
│   └── start.sh
├── tasks/
│   ├── PLAN.md
│   ├── TODO.md
│   ├── validation-report.md
│   └── task-*.md
└── src/
    ├── __main__.py
    ├── config.py
    ├── adapters/          # 预留：适配层
    ├── orchestration/     # 预留：编排层
    ├── pipeline/          # 预留：处理层
    └── collectors/
        ├── README.md
        └── <source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/
            └── <impl>.py
```

---

## 3. 采集层路径模板（唯一标准）

```
src/collectors/<source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/<impl>.py
```

---

## 4. 标准取值集合（当前可用全集）

- source: binance / third_party / internal
- market: spot / um_futures / cm_futures / options
- scope: all / symbol_group / single_symbol
- mode: realtime / backfill / sync
- direction: pull / push / sync
- channel: rest / ws / file / stream / grpc / kafka
- type: aggTrades / bookDepth / bookTicker / indexPriceKlines / klines / markPriceKlines / metrics / premiumIndexKlines / trades / alpha
- impl (文件名): http / ccxt / cryptofeed / raw_ws / official_sdk / http_zip

---

## 5. 配置优先级（DATACAT_* 优先）

```
DATACAT_* > 原服务环境变量 > 默认值
```

示例：
- `DATACAT_DATABASE_URL` 覆盖 `DATABASE_URL`
- `DATACAT_LOG_DIR` 覆盖 `DATA_SERVICE_LOG_DIR`
- `DATACAT_DATA_DIR` 覆盖 `DATA_SERVICE_DATA_DIR`

---

## 6. 规范要求（硬性）

- 层级顺序不可改动。
- 不允许裁剪任何层级。
- 扩展必须按层级新增取值。
- 采集逻辑只落在 `<impl>.py`（允许包含独立运行入口）。

---

## 7. 运行

```bash
cd services-preview/datacat-service
make install
make run
```

单独执行：

```bash
python src/__main__.py --backfill   # 按 ZIP → REST 顺序回填
python src/__main__.py --alpha      # Alpha 列表采集
python src/__main__.py --all        # WS + Metrics + Backfill + Alpha
```

测试输出（JSONL，不写生产库）：

```bash
export DATACAT_OUTPUT_MODE=json
export DATACAT_JSON_DIR=services-preview/datacat-service/data-json
python src/__main__.py --metrics
```

结构化日志（可选）：

```bash
export DATACAT_LOG_FORMAT=json
export DATACAT_LOG_LEVEL=INFO
```

验证与基准：

```bash
export DATACAT_OUTPUT_MODE=json
export SYMBOLS_GROUPS=sample
export SYMBOLS_GROUP_SAMPLE=BTCUSDT,ETHUSDT
python scripts/validate_samples.py
python scripts/benchmark_collectors.py
```

可选环境变量：
- `DATACAT_VALIDATION_SYMBOLS=BTCUSDT,ETHUSDT`
- `DATACAT_BENCH_SYMBOLS=BTCUSDT,ETHUSDT`

---

## 8. 变更日志

- 2026-01-28: 建立严格分层基建模板与文档规范。
- 2026-01-28: 回填与 Alpha 采集结构落位，补充验收模板。
- 2026-01-28: backfill pipeline 调度与 Alpha 入口补齐。
- 2026-01-28: 恢复 src 预留目录（adapters/orchestration/pipeline）。
- 2026-01-28: collectors 全深度补齐占位文件（不覆盖既有实现）。
- 2026-01-28: collectors 结构改为 type/<impl>.py（粒度内置）。
- 2026-01-29: 新增 JSONL 测试输出（DATACAT_OUTPUT_MODE=json）。
