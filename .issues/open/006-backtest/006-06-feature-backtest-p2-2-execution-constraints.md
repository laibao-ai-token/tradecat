---
title: "006-06-feature-backtest-p2-2-execution-constraints"
status: open
created: 2026-03-08
updated: 2026-03-13
owner: lixh6
priority: medium
type: feature
---

# 回测 P2-2：执行约束（部分成交 / 最小成交量 / 冲击）

## 进度条

- 总体：`█████░░░░░ 55%`
- Phase 1：`██████████ 100%`
- Phase 2：`███████░░░ 70%`
- Phase 3：`███░░░░░░░ 30%`

## 背景

当前回测在 `P2-1` 已经补上分层滑点，但仍默认假设：

- 信号触发后可在目标 bar 一次性吃满仓位
- 不会因为成交量不足导致部分成交
- 不会因为仓位参与率过高而产生额外冲击

这会继续高估大仓位场景下的“可成交性”。

## 目标

补齐首版执行约束模型，让回测在 OHLCV 级别上更保守、更可解释：

1. 支持按 bar 成交量上限做容量约束
2. 支持最小成交额门槛
3. 支持部分成交与多笔拆分平仓
4. 支持按 bar 参与率追加冲击成本

## 本期范围

1. `execution` 增加容量 / 最小成交额 / 冲击参数
2. 开仓 / 平仓按 fill bar 可用容量裁剪成交量
3. 产物输出部分成交与冲击成本解释字段
4. 补定向测试并回归现有回测链路

## 非目标（本期不做）

- 不接入 tick / orderbook 回放
- 不实现 IOC/FOK 等订单类型语义
- 不实现撮合队列与盘口档位滑移
- 不修改生产 `config/.env`
- 不变更数据库 schema

## 配置建议

- `max_bar_participation_rate`
- `min_order_notional`
- `impact_bps_per_bar_participation`

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

### Phase 1：模型与配置

- [x] 新增 `max_bar_participation_rate / min_order_notional / impact_bps_per_bar_participation`
- [x] 保持默认配置向后兼容（未启用时不改变旧行为）
- [x] 为 Trade / Metrics 增加执行约束解释字段

### Phase 2：执行与产物

- [x] 开仓按 fill bar 容量约束裁剪成交量
- [x] 平仓支持部分成交与拆分 trade
- [x] 冲击成本按 bar 参与率追加到成交价
- [x] `trades.csv` 输出 `partial_fill / fill_ratio / impact_cost / constraint_flags`
- [x] `metrics.json` / `report.md` 输出 `impact_cost / partial_fill_trade_count`
- [ ] 真实窗口上复核 `max_bar_participation_rate` 与 impact 参数

### Phase 3：验证与校准

- [x] 增加部分开仓 / 部分平仓 / 回测产物定向测试
- [x] 回归通过 signal-service 回测相关测试
- [ ] 用真实窗口校准容量约束与冲击参数

## 验收标准

- [x] 默认配置保持旧行为
- [x] 启用执行约束后可出现部分成交 / 拆分平仓
- [x] 产物可解释 fill ratio / impact / constraint_flags
- [ ] 真实窗口下容量约束不过度悲观或乐观

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
- Related: `#006-05`

## 进展记录

### 2026-03-13

- [x] `#006` 的 P0/P1 主链路已关闭，`#006-06` 继续作为 P2 增强项推进
- [x] 已创建 Linear / Symphony 派单：`TRA-23` `[006-06] 校准执行约束参数：容量上限 / 部分成交 / 冲击成本`
- [ ] 下一步由 Symphony 在真实窗口下复核容量约束、部分成交与冲击成本口径，必要时做最小修正

### 2026-03-08

- [x] 从父任务 `#006` 拆分出 `P2-2` 独立 issue
- [x] 首版执行约束模型已落地：容量上限 / 最小成交额 / 冲击成本
- [x] 支持部分开仓、部分平仓与拆分 trade
- [x] `trades.csv` 已输出 `partial_fill / constraint_flags / entry_fill_ratio / exit_fill_ratio / impact_cost`
- [x] `metrics.json` / `report.md` 已输出 `impact_cost / partial_fill_trade_count`
- [x] 已补并通过定向测试 + signal-service 回测相关 49 项测试
- [ ] 下一步用真实窗口校准参与率上限与冲击参数

## 备注

首版执行约束仍基于 OHLCV 聚合成交量做近似，目标是“降低可成交性高估”，不是订单簿级精确撮合回放。
