---
title: "006-05-feature-backtest-p2-1-tiered-slippage-model"
status: open
created: 2026-03-08
updated: 2026-03-13
owner: lixh6
priority: medium
type: feature
---

# 回测 P2-1：分层滑点模型（按波动 / 成交量 / 时段）

## 进度条

- 总体：`██████░░░░ 60%`
- Phase 1：`██████████ 100%`
- Phase 2：`████████░░ 80%`
- Phase 3：`████░░░░░░ 40%`

## 背景

当前回测滑点仍以固定 `slippage_bps` 为主，虽然工程链路稳定，但会带来两个偏差：

- 高波动 / 低成交量 / 冷门时段下的成交成本被低估
- 不同市场状态下，回测收益对“可成交性”的敏感度不可解释

父任务 `#006` 已把 `P2-1` 定义为增强项：让滑点不再固定，而是随市场状态变化。

## 目标

在保持当前回测主链路稳定的前提下，落地首版**分层滑点模型**：

1. 继续保留固定滑点口径（向后兼容）
2. 新增 `layered` 模式，按波动 / 成交量 / 时段动态抬升滑点
3. 把单笔滑点 bps 与滑点成本写入产物，便于解释收益侵蚀来源

## 本期范围

1. 为 `execution` 增加 `slippage_model` 与 layered 参数
2. 在执行引擎中按 fill 上下文动态计算 entry / exit slippage
3. 在 `trades.csv` / `metrics.json` / `report.md` 中输出滑点解释字段
4. 增加定向单测，锁定动态滑点、cap 与产物字段语义

## 非目标（本期不做）

- 不接入真实订单簿 / tick 级回放
- 不做冲击成本模型（留给 `P2-2`）
- 不做真实窗口参数校准自动化（待 TimescaleDB 恢复后继续）
- 不修改生产 `config/.env`
- 不变更数据库 schema

## 配置建议

- `slippage_model`: `fixed | layered`
- `slippage_max_bps`
- `slippage_volatility_weight`
- `slippage_volume_weight`
- `slippage_session_weight`
- `slippage_volume_window`

## 实现范围

- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/config_loader.py`
- `services/signal-service/src/backtest/runner.py`
- `services/signal-service/src/backtest/walkforward.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `services/signal-service/tests/test_backtest_runner.py`

## 实现清单

### Phase 1：配置与模型

- [x] 新增 `slippage_model` 与 layered 参数
- [x] 保持 `fixed` 口径向后兼容
- [x] 增加 trade / metrics 的滑点解释字段

### Phase 2：执行与产物

- [x] 按波动 / 成交量 / 时段动态计算滑点
- [x] 增加滑点 cap，避免极端场景无限放大
- [x] `trades.csv` 输出 entry / exit slippage bps 与 cost
- [x] `metrics.json` / `report.md` 输出 `slippage_cost`
- [ ] 真实窗口上复核 layered 参数的保守性

### Phase 3：验证与校准

- [x] 增加定向单测，锁定薄量高波动场景下 layered > fixed
- [x] 回归通过 signal-service 回测相关测试
- [ ] 用真实窗口校准时段权重、volume window 与 cap

## 验收标准

- [x] 仍支持固定滑点口径
- [x] `layered` 模式会随市场状态动态调节滑点
- [x] `trades.csv` / `metrics.json` / `report.md` 可解释滑点成本
- [ ] 真实窗口下 layered 参数不过度夸大或低估成交成本

## 相关文件

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/config_loader.py`
- `services/signal-service/src/backtest/runner.py`
- `services/signal-service/src/backtest/walkforward.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `services/signal-service/tests/test_backtest_runner.py`

## 相关 Issue

- Parent: `#006`
- Related: `#006-02`

## 进展记录

### 2026-03-13

- [x] `#006` 的 P0/P1 主链路已关闭，`#006-05` 继续作为 P2 增强项推进
- [x] 已创建 Linear / Symphony 派单：`TRA-22` `[006-05] 校准 layered slippage 真实窗口参数与保守性结论`
- [ ] 下一步由 Symphony 在真实窗口下复核 `fixed vs layered`，必要时收敛默认参数与 cap

### 2026-03-08

- [x] 从父任务 `#006` 拆分出 `P2-1` 独立 issue
- [x] 首版 `slippage_model=fixed|layered` 已落地
- [x] layered 口径已按波动 / 成交量 / 时段三层因子抬升滑点，并增加 cap
- [x] `trades.csv` 已输出 `entry_slippage_bps / exit_slippage_bps / entry_slippage_cost / exit_slippage_cost`
- [x] `metrics.json` / `report.md` 已输出 `slippage_cost`
- [x] 已补并通过定向测试 + signal-service 回测相关 46 项测试
- [ ] 下一步用真实窗口校准 layered 权重与 cap

## 备注

首版 layered 模型仍属于 OHLCV 级启发式近似，目标是“比固定 bps 更保守、更可解释”，不是 tick/盘口级精确成交回放。
