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

---

## 8. 变更日志

- 2026-01-28: 建立严格分层基建模板与文档规范。
- 2026-01-28: 回填与 Alpha 采集结构落位，补充验收模板。
- 2026-01-28: backfill pipeline 调度与 Alpha 入口补齐。
- 2026-01-28: 恢复 src 预留目录（adapters/orchestration/pipeline）。
- 2026-01-28: collectors 全深度补齐占位文件（不覆盖既有实现）。
- 2026-01-28: collectors 结构改为 type/<impl>.py（粒度内置）。
