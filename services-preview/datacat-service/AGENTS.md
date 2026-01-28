# Datacat Service - AGENTS

本文档面向 AI 编码/自动化 Agent，定义 datacat-service 的结构与强制规范。

---

## 1. 使命与范围

- 使命：提供“严格分层”的采集基建目录结构。
- 范围：仅定义采集结构与模板，不包含业务逻辑实现。

---

## 2. 严格分层顺序（不可变）

```
source → market → scope → mode → direction → channel → type → granularity → impl
```

---

## 3. 固定取值集合（当前可用全集）

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

## 4. 目录结构树（固定模板）

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
└── src/
    ├── __main__.py
    ├── config.py
    └── collectors/
        ├── README.md
        └── <source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/<granularity>/<impl>/
            └── collector.py
```

---

## 5. 模块职责与边界

- collectors/: 仅负责采集，不做规范化、校验、落库、编排。
- 目录中所有采集实现必须以 `collector.py` 命名。

---

## 6. 变更规则（强制）

- 禁止裁剪层级。
- 禁止调整层级顺序。
- 新增只允许扩展“取值集合”。
- 任意结构变更必须同步更新 `README.md` 与 `src/collectors/README.md`。

---

## 7. 变更日志

- 2026-01-28: 初始化 datacat-service 严格分层目录与文档模板。
