# Datacat Service - AGENTS

本文档面向 AI 编码/自动化 Agent，定义 datacat-service 的结构与强制规范。

---

## 1. 使命与范围

- 使命：提供“严格分层”的采集基建目录结构，并承载可执行的采集实现。
- 范围：采集/回填/拉取逻辑与必要的落库为主；编排与管道目录仅作结构预留，不承载业务策略。

---

## 2. 严格分层顺序（不可变）

```
source → market → scope → mode → direction → channel → type
```

> 粒度（interval/depth）不再作为目录层级，统一在实现文件内部处理。

---

## 3. 固定取值集合（当前可用全集）

- source: binance / third_party / internal
- market: spot / um_futures / cm_futures / options
- scope: all / symbol_group / single_symbol
- mode: realtime / backfill / sync
- direction: pull / push / sync
- channel: rest / ws / file / stream / grpc / kafka
- type: aggTrades / bookDepth / bookTicker / indexPriceKlines / klines / markPriceKlines / metrics / premiumIndexKlines / trades / alpha
- impl (文件名): http / ccxt / cryptofeed / raw_ws / official_sdk / http_zip

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
│   ├── start.sh
│   ├── validate_samples.py       # 样本验证与验收报告生成
│   └── benchmark_collectors.py   # 采集基准测试脚本
├── tasks/                    # 重构计划与任务清单
│   ├── PLAN.md
│   ├── TODO.md
│   ├── validation-report.md
│   └── task-*.md
├── data-json/                # JSONL 测试输出目录（不写生产库）
└── src/
    ├── __main__.py
    ├── config.py
    ├── adapters/              # 结构预留：协议/存储适配（暂空）
    ├── orchestration/         # 结构预留：编排/调度（暂空）
    ├── pipeline/              # 处理管道层（含 JSONL 输出）
    ├── runtime/               # 运行时支持（日志/错误处理）
    └── collectors/
        ├── README.md
        └── <source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/
            └── <impl>.py
```

---

## 5. 模块职责与边界

- collectors/: 仅负责采集/回填/拉取与必要的落库，不承担业务规则与策略计算。
- 目录中所有采集实现必须以 `<impl>.py` 命名。
    - 允许附带运行入口（CLI）以便独立执行。
- adapters/: 结构预留，未来用于协议/存储等适配层，不落业务逻辑。
- adapters/README.md: 适配层结构说明与使用边界。
- orchestration/: 结构预留，未来用于编排与调度，不直接落采集逻辑。
- orchestration/README.md: 编排层结构说明与使用边界。
- pipeline/: 处理层，承载轻量输出与规范化工具，不直接落采集逻辑。
- pipeline/json_sink.py: JSONL 测试输出，避免写入生产库。
- pipeline/README.md: 处理层结构说明与使用边界。
- runtime/: 运行时基础设施（日志与错误处理），不承载业务采集逻辑。
- runtime/logging_utils.py: 统一日志格式（plain/json）与输出规范。
- runtime/errors.py: 统一异常模型与入口守护。

---

## 6. 变更规则（强制）

- 禁止裁剪层级。
- 禁止调整层级顺序。
- 新增只允许扩展“取值集合”。
- 任意结构变更必须同步更新 `README.md` 与 `src/collectors/README.md`。
- 配置优先级固定为：DATACAT_* > 原变量 > 默认值。

---

## 7. 变更日志

- 2026-01-28: 初始化 datacat-service 严格分层目录与文档模板。
- 2026-01-28: 新增重构计划与任务清单目录。
- 2026-01-28: 回填与文件 ZIP 采集细分落位，补齐 REST/ZIP/Alpha 实现与验收模板。
- 2026-01-28: backfill pipeline 调度与 Alpha 入口补齐。
- 2026-01-28: 恢复 src 预留目录（adapters/orchestration/pipeline）。
- 2026-01-28: collectors 全深度补齐占位文件（不覆盖既有实现）。
- 2026-01-28: collectors 结构改为 type/<impl>.py（粒度内置）。
- 2026-01-29: 增加 JSONL 测试输出（data-json + pipeline/json_sink）。
- 2026-01-29: 增加 runtime 运行时支持（结构化日志与统一错误处理）。
- 2026-01-29: 增加验证与基准脚本（validate_samples / benchmark_collectors）。
