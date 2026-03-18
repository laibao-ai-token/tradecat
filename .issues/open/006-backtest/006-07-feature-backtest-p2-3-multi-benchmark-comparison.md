---
title: "006-07-feature-backtest-p2-3-multi-benchmark-comparison"
status: open
created: 2026-03-08
updated: 2026-03-13
owner: lixh6
priority: medium
type: feature
---

# 回测 P2-3：多基准比较（B&H / 风险平价 / 简单动量）

## 进度条

- 总体：`██████░░░░ 60%`
- Phase 1：`██████████ 100%`
- Phase 2：`███████░░░ 70%`
- Phase 3：`███░░░░░░░ 30%`

## 背景

当前回测已经支持 `buy_hold_return_pct` 与 `excess_return_pct`，但仍主要基于单一买入持有基准判断策略优劣，容易带来两类误判：

- 多币种组合下，单一等权 B&H 无法反映“波动差异”对基准收益的影响
- 趋势阶段里，策略即使跑赢 B&H，也不一定跑赢更合理的简单动量基准

父任务 `#006` 已将 `P2-3` 定义为增强项：把回测结果从“单基准对比”升级为“多基准对比”。

## 目标

补齐首版多基准比较能力，让回测报告更接近组合评估口径：

1. 保留现有等权 `buy_hold` 基准
2. 新增静态逆波动加权的 `risk_parity` 基准
3. 新增基于 bar close 的简单时序动量 `momentum` 基准
4. 在 `metrics.json` / `report.md` / walk-forward 摘要中输出“相对不同基准的超额收益”与“最强基准”

## 本期范围

1. 扩展 `Metrics` 支持多基准字段
2. 在 reporter 中计算 `buy_hold / risk_parity / momentum`
3. 在 walk-forward fold / summary 产物中透出多基准收益字段
4. 补定向测试并回归现有回测链路

## 非目标（本期不做）

- 不引入真实指数或外部市场 benchmark
- 不接入动态再平衡组合优化器
- 不做真实窗口下 benchmark 参数自动校准（待 TimescaleDB 恢复后继续）
- 不修改生产 `config/.env`
- 不变更数据库 schema

## 实现范围

- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/walkforward.py`
- `services/signal-service/tests/test_backtest_reporter.py`
- `services/signal-service/tests/test_backtest_walkforward.py`

## 实现清单

### Phase 1：模型与指标

- [x] `Metrics` 增加 `risk_parity / momentum / best_baseline` 相关字段
- [x] 保持原有 `buy_hold` / `excess_return_pct` 向后兼容
- [x] walk-forward fold 行增加多基准收益字段

### Phase 2：产物与报告

- [x] `metrics.json` 输出多基准收益与相对收益字段
- [x] `report.md` 输出多基准摘要与 strongest baseline
- [x] walk-forward summary 输出多基准均值字段
- [ ] 真实窗口上复核多基准对比是否符合预期

### Phase 3：验证与校准

- [x] 增加 reporter / walk-forward 定向测试
- [x] 回归通过 signal-service 回测相关测试
- [ ] 用真实窗口校准 benchmark 口径与阈值解释

## 验收标准

- [x] 单次回测可同时输出 `buy_hold / risk_parity / momentum`
- [x] `metrics.json` / `report.md` 可解释相对不同基准的超额收益
- [x] walk-forward 摘要可聚合多基准收益
- [ ] 真实窗口下多基准比较不过度乐观或悲观

## 相关文件

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/walkforward.py`
- `services/signal-service/tests/test_backtest_reporter.py`
- `services/signal-service/tests/test_backtest_walkforward.py`

## 相关 Issue

- Parent: `#006`
- Related: `#006-05`
- Related: `#006-06`

## 进展记录

### 2026-03-13

- [x] `#006` 的 P0/P1 主链路已关闭，`#006-07` 继续作为 P2 增强项推进
- [x] 已创建 Linear / Symphony 派单：`TRA-24` `[006-07] 复核多基准比较：risk parity / momentum 的真实窗口解释力`
- [ ] 下一步由 Symphony 在真实窗口下复核多基准解释力，必要时调整默认报告口径

### 2026-03-08

- [x] 从父任务 `#006` 拆分出 `P2-3` 独立 issue
- [x] 已补 `buy_hold / risk_parity / momentum` 三类 baseline
- [x] `metrics.json` / `report.md` / walk-forward 摘要已输出多基准收益字段
- [x] 已补并通过 reporter / walk-forward 定向测试 + signal-service 回测相关 49 项测试
- [ ] 下一步用真实窗口复核 benchmark 解释力与阈值口径

## 备注

首版多基准比较仍基于 OHLCV bar close 的启发式组合近似，目标是“比单一 B&H 更可解释”，不是资产配置级严谨回测框架。
