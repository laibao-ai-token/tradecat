# Datacat Service (Preview) — 严格分层基建

本服务用于承载“严格分层”的数据采集目录结构，作为长期基建的统一规范。

---

## 1. 分层顺序（不可变）

```
source → market → scope → mode → direction → channel → type → granularity → impl
```

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
│   └── task-*.md
└── src/
    ├── __main__.py
    ├── config.py
    └── collectors/
        ├── README.md
        └── <source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/<granularity>/<impl>/
            └── collector.py
```

---

## 3. 采集层路径模板（唯一标准）

```
src/collectors/<source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/<granularity>/<impl>/collector.py
```

---

## 4. 标准取值集合（当前可用全集）

- source: binance / third_party / internal
- market: spot / um_futures / cm_futures / options
- scope: all / symbol_group / single_symbol
- mode: realtime / backfill / sync
- direction: pull / push / sync
- channel: rest / ws / file / stream / grpc / kafka
- type: aggTrades / bookDepth / bookTicker / indexPriceKlines / klines / markPriceKlines / metrics / premiumIndexKlines / trades
- granularity: interval_1m / interval_5m / depth_20 / depth_1000
- impl: http / ccxt / cryptofeed / raw_ws / official_sdk / http_zip

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
- 采集逻辑只落在 collector.py。

---

## 6. 运行

```bash
cd services-preview/datacat-service
make install
make run
```

---

## 7. 变更日志

- 2026-01-28: 建立严格分层基建模板与文档规范。
