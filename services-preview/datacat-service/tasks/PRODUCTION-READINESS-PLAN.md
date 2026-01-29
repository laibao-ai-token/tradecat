# Datacat Service 生产可用性补齐计划

日期：2026-01-29

## 1. 目标与范围

- 目标：让 `services-preview/datacat-service` 在功能、依赖、运行、监控与验证层面达到可生产上线标准。
- 范围：与 `services/data-service` 功能对齐（WS K线 / REST 指标 / ZIP+REST 回填 / Alpha / 限流），并补齐生产级脚本与验证链路。
- 前提：不改动旧服务，仅在新结构内补齐。

## 2. 现存阻塞点（必须解决）

1) Python 路径缺失：新服务依赖 `tradecat/libs/common`，但 `start.sh` 未将其加入 PYTHONPATH。
2) 依赖锁文件不一致：`requirements.txt` 与 `requirements.lock.txt` 不匹配，且 lock 不包含运行依赖。
3) 验证链路仅基于 JSONL：尚未完成“真实 TimescaleDB”写入验证。

## 3. 交付物清单

- 运行路径修复（PYTHONPATH 或 common 模块内聚）
- 更新后的 `requirements.lock.txt`
- 生产启动脚本（含环境注入）
- 数据库端到端验证报告
- 运行健康检查与监控规范文档
- 回填管线与错误处理机制的生产验证记录

## 4. 执行计划（细粒度）

### A. 依赖与路径

- A1：修复 `common.symbols` 的导入路径
  - 方案 1：启动脚本追加 `PYTHONPATH=<tradecat>/libs:<tradecat>/services-preview/datacat-service/src`
  - 方案 2：把 `tradecat/libs/common` 迁入 `datacat-service/src/common`（慎重，需同步更新引用）
- A2：新增/更新 `DATACAT_LIB_PATH` 环境变量（若采用方案 1）
- A3：冒烟启动验证（WS + Metrics 启动成功且不报 ImportError）

### B. 依赖锁定

- B1：以 `requirements.txt` 为基准重建 lock
  - 执行：`pip install -r requirements.txt` → `pip freeze > requirements.lock.txt`
- B2：记录 python 版本 / pip 版本
- B3：在 CI 或本地 clean venv 验证可安装

### C. 生产启动脚本

- C1：`scripts/start.sh` 增加环境变量注入（DATACAT_*）
- C2：支持 `start/stop/status/restart` 的一致日志输出
- C3：默认记录 PID 与日志路径（保持现有规范）

### D. 数据库端到端验证

- D1：准备测试库（非生产）
- D2：跑 24h WS + Metrics 真实落库
- D3：跑 backfill (ZIP → REST) 完整流程
- D4：执行一致性对照（与旧服务表）
- D5：输出验收报告（新增 `tasks/validation-report-db.md`）

### E. 运行稳态验证

- E1：WS 断线重连测试（强制断网 5 分钟）
- E2：REST 限流/ban 恢复测试（429/418）
- E3：CPU/内存/吞吐 监控基线（输出 `tasks/runtime-baseline.md`）

### F. 监控与日志规范落地

- F1：明确 JSON/Plain 输出策略（默认 plain，生产可切 JSON）
- F2：日志字段规范：ts / level / component / msg / error_code
- F3：健康检查脚本（最小可行：DB ping + HTTP ping）

## 5. 验收标准（必须同时满足）

1) 新服务功能覆盖旧服务全部功能点。
2) 依赖锁文件可复现（clean venv 一次安装成功）。
3) 生产启动脚本可启动并保持运行 24h。
4) DB 写入量与旧服务一致（阈值内）。
5) 监控/日志可用于排障（结构化字段齐全）。

## 6. 风险与缓释

- 风险：代理/网络不稳定导致 WS 断线或 REST 波动。
  - 缓释：限流退避 + 重连 + 监控报警。
- 风险：路径/依赖变化影响旧流程。
  - 缓释：保持旧服务不变，仅新服务内聚处理。

## 7. 预计工期（按顺序）

- A/B/C：0.5 天
- D/E：1-2 天（含 24h 长跑）
- F：0.5 天

