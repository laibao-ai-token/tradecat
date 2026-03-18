---
title: "006-feature-backtest-prod-readiness"
status: closed
created: 2026-03-07
updated: 2026-03-13
closed: 2026-03-13
owner: lixh6
priority: high
type: feature
---

# 回测优化与实盘前可用性推进

## 背景

当前 TradeCat 的回测链路已经具备可运行的工程闭环，支持：

- `history_signal`
- `offline_replay`
- `offline_rule_replay`
- `compare_history_rule`
- Walk-Forward 摘要
- TUI 回测展示

但当前状态仍属于“工程链路可用（M1）”，尚未达到“实盘前可用（P0 完成）”。

从 2026-03-07 起，回测后续推进不再以 `docs/learn/backtest_plan.md` 作为唯一管理入口，而是以本 issue 作为**推进主入口 / 状态主入口 / 任务拆分主入口**。

## Source of Truth

- **后续推进入口**：本 issue `#006`
- **子 issue 目录**：`.issues/open/006-backtest/`
- **方案归档文档**：`docs/learn/backtest_plan.md`
- **实盘前改造清单参考**：`docs/learn/backtest_prod_readiness_plan.md`
- **真实窗口校准 Runbook**：`docs/learn/backtest_real_window_validation.md`
- **冻结结论参考**：`docs/learn/backtest_freeze_20260214.md`

说明：

- `docs/learn/backtest_plan.md` 保留为方案背景与设计归档
- 具体推进状态、优先级、验收与下一步动作，统一在本 issue 维护

## 交易所口径（已确认）

- **目标交易所**：Binance
- **首版执行口径**：Binance USD-M Perpetual（USDT 本位永续）
- **适用范围**：当前仅覆盖 Crypto 回测链路，不扩展到 A 股 / 美股 / ETF 回测
- **执行建模原则**：先以 Binance 合约规则做首版可解释实现，再视需要抽象为 exchange profile
- **非目标**：本期不建设“跨交易所统一强平模型”

## 目标

将当前“可回测可对比”的回测框架，推进为“可用于实盘前决策评估”的可靠回测系统。

### 本期主目标

1. 补齐执行层关键缺口，避免收益/回撤结论明显失真
2. 建立输入质量与线上/离线口径对齐能力
3. 让回测产物具备可解释、可追踪、可复算能力
4. 为后续“小仓位真实执行验证”提供可信的回测基础

## 非目标（本期不做）

- 不在本 issue 中直接建设自动下单 `order-service`
- 不修改生产 `config/.env`
- 不变更数据库 schema（除非后续单独立项）
- 不将 TUI 变成回测执行器（TUI 仍以只读展示为主）

## 当前状态（已确认）

- [x] 回测主流程已打通
- [x] 多模式运行链路已可用
- [x] 已有基础测试覆盖
- [x] 已有产物目录、latest、run_state、TUI 回测页
- [x] P0/P1 主链路已达到“实盘前可用（首版）”

### 当前主要缺口

1. `#006-01 ~ #006-04` 已完成首版收口；当前剩余 `#006-05 ~ #006-07` 属于后置增强，不再阻塞 P0/P1 闭环
2. 若后续引入 mark price / 分档保证金 / VIP maker-taker 费率 / 更细粒度执行约束，建议以新 issue 单独推进

## 进度看板

- 总体：`██████████ 100%`
- P0-1 `#006-01`：`██████████ 100%`
- P0-2 `#006-02`：`██████████ 100%`
- P0-3 `#006-03`：`██████████ 100%`
- P0-4 `#006-04`：`██████████ 100%`
- P1-1：`██████████ 100%`
- P2-1 `#006-05`：`████████░░ 80%`
- P2-2 `#006-06`：`████████░░ 80%`
- P2-3 `#006-07`：`██████░░░░ 60%`
- 阻塞项：P0/P1 已无阻塞；`#006-05 ~ #006-07` 为后置增强项

## 复工判断（2026-03-12）

- 已解除：`#006-04`
  - `2026-03-13` 真实窗口复验（`run_id=real-window-00604-fixed`）已达 `alignment_score=80.92 / alignment_risk_level=medium`
- 已解除：`#006-01`
  - `2026-03-13` 真实历史 stress window（`run_id=stress-liq-lev50`）已补齐 LONG / SHORT 强平样本
- 已解除：`#006-02`
  - `2026-03-13` 真实历史 stress cost window（`run_id=stress-cost-funding20`）已补齐 LONG / SHORT funding 方向样本，并在报告中明确 taker 费率假设
- 已解除：`#006-03`
  - `2026-03-13` 同窗复验：`input_quality.json` 已变为 `quality_status=fail / gate_status=fail / signal_days=4`
  - 已确认不再出现“`quality_status=pass` 但 precheck fail”冲突
- 后置增强：`#006-05 ~ #006-07`
  - 当前不阻塞 P0 收口，但在 P0 未收敛前不建议继续扩大并行面

## 推进清单

### P0（必须完成，才可称“实盘前可用”）

- [x] **P0-1 执行层补齐维持保证金与强平模型**
  - 范围：`services/signal-service/src/backtest/models.py`
  - 范围：`services/signal-service/src/backtest/execution_engine.py`
  - 范围：`services/signal-service/src/backtest/strategies/default.crypto.yaml`
  - 口径：按 Binance USD-M Perpetual 首版实现
  - 配置建议：`maintenance_margin_ratio`
  - 配置建议：`liquidation_fee_bps`
  - 配置建议：`liquidation_buffer_bps`
  - 验收：支持正常平仓 / 反手 / 中性平仓 / 强平四类退出
  - 验收：`trades.csv` 可解释强平原因、强平成本、触发价格口径
  - 验收：极端行情下权益不出现不合理跳变（含测试样例）

- [x] **P0-2 成本模型升级为交易成本三件套**
  - 范围：`services/signal-service/src/backtest/models.py`
  - 范围：`services/signal-service/src/backtest/execution_engine.py`
  - 范围：`services/signal-service/src/backtest/reporter.py`
  - 配置建议：`maker_fee_bps`
  - 配置建议：`taker_fee_bps`
  - 配置建议：`funding_rate_bps_per_8h`
  - 验收：输出 `gross_pnl / trading_fee / funding_fee / net_pnl`
  - 验收：报告中可解释“收益来自信号”还是“被成本侵蚀”

- [x] **P0-3 数据完整性审计入产物**
  - 范围：`services/signal-service/src/backtest/precheck.py`
  - 范围：`services/signal-service/src/backtest/runner.py`
  - 范围：`services/signal-service/src/backtest/reporter.py`
  - 验收：新增 `input_quality.json`
  - 验收：记录每 symbol 缺口统计、无 `next_open` 可成交次数、被丢弃信号计数
  - 验收：报告中显示数据质量评分 / 覆盖度
  - 验收：覆盖不足时默认阻断（除非显式 `--force`）

- [x] **P0-4 历史信号 vs 离线重放最小口径对齐评分**
  - 范围：`services/signal-service/src/backtest/rule_replay.py`
  - 范围：`services/signal-service/src/backtest/comparison.py`
  - 范围：`services/signal-service/tests/test_backtest_rule_replay.py`
  - 目标：建立 TopN 规则触发差异阈值基准（方向偏差 / 触发率偏差）
  - 验收：`comparison.json` 输出 `alignment_score`
  - 验收：关键规则偏差超阈值时告警
  - 验收：评分口径固定为 0~100，便于 TUI / 本地检查 / CI 复用

### P1（应完成，提升稳定性和可解释性）

- [x] **P1-1 Walk-Forward 加入训练窗参数选择**
  - 范围：`services/signal-service/src/backtest/walkforward.py`
  - 验收：每个 fold 记录 `selected_params`
  - 验收：`walk_forward_summary.json` 可追踪每折参数与收益关系
  - 验收：默认在训练窗对 `base/aggressive/conservative` 候选做首版轻量选参
- [x] **P1-2 指标稳定性报告（跨 run 对比）**
  - 范围：`services/signal-service/src/backtest/reporter.py`
  - 范围：`services/signal-service/src/backtest/retention.py`
  - 验收：产物新增 `stability_report.json/.md`
  - 验收：可识别参数过拟合导致的性能塌陷
- [x] **P1-3 TUI 展示输入质量与口径对齐分**
  - 范围：`services-preview/tui-service/src/tui.py`
  - 验收：回测页可直接展示 `input_quality` 与 `alignment_score`
  - 验收：异常状态给出可执行修复提示（命令级）

### P2（增强项，追求更贴近真实交易环境）

- [ ] **P2-1 分层滑点模型（按波动 / 成交量 / 时段）**（首版代码 + 测试已落地，待真实窗口校准）
  - 目标：滑点不再固定 bps，而是随市场状态变化
  - 范围：`services/signal-service/src/backtest/execution_engine.py`
  - 验收：支持 `fixed | layered` 双口径并保留向后兼容
  - 验收：`trades.csv / metrics.json / report.md` 可解释滑点成本
- [ ] **P2-2 执行约束（部分成交 / 最小成交量 / 冲击）**（首版代码 + 测试已落地，待真实窗口校准）
  - 目标：降低大仓位下“可成交性”高估
  - 范围：`services/signal-service/src/backtest/execution_engine.py`
  - 验收：支持部分成交 / 拆分平仓 / 最小成交额门槛
  - 验收：`trades.csv / metrics.json / report.md` 可解释 `impact_cost / fill_ratio / constraint_flags`
- [ ] **P2-3 多基准比较（B&H / 风险平价 / 简单动量）**（首版代码 + 测试已落地，待真实窗口校准）
  - 目标：避免只和单一基准比较导致误判
  - 范围：`services/signal-service/src/backtest/models.py`
  - 范围：`services/signal-service/src/backtest/reporter.py`
  - 范围：`services/signal-service/src/backtest/walkforward.py`
  - 验收：`metrics.json / report.md / walk_forward_summary.json` 输出多基准收益字段
  - 验收：输出 `excess_return_vs_risk_parity_pct / excess_return_vs_momentum_pct`
  - 验收：输出 `best_baseline_name / best_baseline_return_pct`

## 推荐实施顺序

### 第 1 周

1. P0-1 强平模型
2. P0-2 成本模型
3. P0-3 输入质量审计

### 第 2 周

1. P0-4 对齐评分
2. P1-1 Walk-Forward 训练窗选参
3. P1-3 TUI 风险可视化

### 第 3 周

1. P1-2 稳定性报告
2. P2 项按资源并行推进

## 里程碑验收门槛

### P0 完成门槛

- [x] 回测产物包含：`metrics.json`、`report.md`、`input_quality.json`
- [x] 执行层支持强平并可解释
- [x] 成本拆分可审计
- [x] 对齐评分可输出且具备阈值告警
- [x] 首版结果明确标注为 Binance USD-M 口径

### P1 完成门槛

- [x] Walk-Forward 每折具备参数选择记录
- [x] 稳定性报告可显示跨 run 漂移
- [x] TUI 可直接展示质量与对齐风险

## 相关文件

- `docs/learn/backtest_plan.md`（归档 / 方案背景）
- `docs/learn/backtest_prod_readiness_plan.md`（改造清单参考）
- `docs/learn/backtest_real_window_validation.md`（真实窗口校准 Runbook）
- `docs/learn/backtest_freeze_20260214.md`（冻结结论）
- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/precheck.py`
- `services/signal-service/src/backtest/rule_replay.py`
- `services/signal-service/src/backtest/comparison.py`
- `services/signal-service/src/backtest/walkforward.py`
- `services/signal-service/src/backtest/retention.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `scripts/backtest_real_window_validation.sh`
- `services-preview/tui-service/src/tui.py`

## 进展记录

### 2026-03-12

- [x] 已确认 `localhost:5434/market_data` 可达；旧的“5434/5433 均 refused”阻塞已过时
- [x] 已执行真实窗口：`./scripts/backtest_real_window_validation.sh --force`
- [x] precheck 实测：`signal_days=4 < 7`，当前默认 gate 会阻断 history_signal 主链路
- [x] compare gate 实测：`alignment_score=4.55 / alignment_risk_level=critical / exit_code=2`
- [x] history_signal 实测：`run_id=real-window-20260312-085436-history`，`return=-3.70%`
- [x] walk-forward 实测：`fold_count=6 / replay_fold_count=6 / fallback_fold_count=6 / avg_return=-13.36%`
- [ ] 判定结果：`#006-03` 与 `#006-04` 需要复工，`#006-01` 与 `#006-02` 需继续补真实窗口验证，`#006` 不能关闭

### 2026-03-13

- [x] `#006-03 / TRA-20` 已完成 review，可用部分已合入主仓工作树，并通过定向测试 `37 passed`
- [x] `#006-01` 已通过真实历史 stress window（`run_id=stress-liq-lev50`）补齐 LONG / SHORT 强平样本，可作为首版收口依据
- [x] `#006-02` 已通过真实历史 stress cost window（`run_id=stress-cost-funding20`）补齐 LONG / SHORT funding 样本，并在 `report.md` 明确 taker 费率假设；定向测试 `30 passed`
- [x] `#006-03` 已完成同窗 real-window 复验：`artifacts/backtest/20260312-162023/input_quality.json` 显示 `quality_status=fail / gate_status=fail / signal_days=4`，口径冲突已消失
- [x] `#006-04` 已完成主仓修复：compare mode 自动收缩到 history overlap window，且 backtest 默认不再隐式继承 `SIGNAL_RULE_TIMEFRAMES`
- [x] `#006-04` 真实窗口复验：`artifacts/backtest/20260312-174033/real-window-00604-fixed-compare/comparison.json` 显示 `alignment_score=80.92 / alignment_status=warn / alignment_risk_level=medium`
- [x] `#006` 的 P0/P1 主阻塞已全部解除，父 issue 以“实盘前可用首版”口径收口关闭
- [x] `#006-05 ~ #006-07` 保持 open，作为不阻塞当前回测主链路的后置增强项继续排期

### 2026-03-08

- [x] `P2-2` 首版执行约束已落地：支持容量上限、最小成交额、部分成交与 bar 参与率冲击成本
- [x] `P2-2` 已把 `partial_fill / fill_ratio / impact_cost / constraint_flags` 写入回测产物
- [x] 已回归通过 signal-service 回测相关 49 项测试
- [ ] 真实窗口下的参与率上限 / 冲击参数校准仍待 TimescaleDB 恢复后继续

- [x] `P2-3` 首版多基准比较已落地：支持 `buy_hold / risk_parity / momentum` 三类 baseline
- [x] `P2-3` 已把 `excess_return_vs_risk_parity_pct / excess_return_vs_momentum_pct / best_baseline_name` 写入回测产物
- [x] 已回归通过 signal-service 回测相关 49 项测试（含 reporter / walk-forward 多基准定向样例）
- [ ] 真实窗口下的 benchmark 解释力与阈值口径仍待 TimescaleDB 恢复后继续

- [x] 已补 `scripts/backtest_real_window_validation.sh`，用于 PG 恢复后串行执行 `check-only / compare gate / history_signal / walk-forward` 真实窗口校准闭环
- [x] 校准脚本支持 `--dry-run / --skip-db-check / --force`，便于数据库恢复前先核命令、恢复后直接执行
- [x] 已为 `#006-01/#006-02/#006-03/#006-04` 补“真实窗口回填模板”，便于 PG 恢复后按 issue 逐项沉淀校准结论
- [x] 已补 `scripts/backtest_issue_fill.py`，可按 `run_prefix` 自动提取 `#006-01/#006-02/#006-03/#006-04` 的 issue 回填草稿，并支持 `--apply-issues` 直接写回本地 issue 文件
- [x] 2026-03-12 已执行真实窗口校准脚本，并完成首轮回填

- [x] `P2-1` 首版分层滑点模型已落地：支持 `fixed | layered` 双口径，layered 会按波动 / 成交量 / 时段动态抬升滑点
- [x] `P2-1` 已把 `entry_slippage_bps / exit_slippage_bps / slippage_cost` 写入回测产物，便于解释执行侵蚀
- [x] 已回归通过 signal-service 回测相关 46 项测试
- [ ] 真实窗口下的 layered 权重 / cap 校准仍待 TimescaleDB 恢复后继续

- [x] `P1-1` Walk-Forward 已加入训练窗轻量选参：默认评估 `base/aggressive/conservative` 三组候选，并把入选参数写入每折 `selected_params`
- [x] `P1-1` 训练窗评估改用 `run_backtest(..., ephemeral=True)`，不会污染 `latest` / retention / `run_state.json`
- [x] 已回归通过 signal-service 回测相关 44 项测试（含新增 walk-forward / runner 防回归样例）
- [ ] 真实窗口下的候选网格扩展与阈值校准仍待 TimescaleDB 恢复后继续

- [x] `P1-3` 回测页已直接展示 `input_quality` / `stability_status`，compare 区继续展示 `alignment_score` / `alignment_risk_level`
- [x] `P1-3` 已补命令级修复提示：质量异常给 `--check-only`，稳定性漂移给 `--walk-forward`，对齐风险高给 compare gate 示例
- [x] 已回归通过 signal-service 41 项测试 + TUI backtest/compare 5 项测试

- [x] `P1-2` 已落地 `stability_report.json/.md`，对最近可比 run 的收益/回撤/胜率漂移做跨 run 摘要
- [x] `P1-2` 已补 `report.md` 稳定性摘要区块，并回归通过 signal-service 回测相关 41 项测试
- [ ] 当前稳定性报告仍基于“相同窗口 / 相同 symbols / 相同 mode”的历史 run 基线；更复杂的参数簇/策略簇聚合仍未实现

- [x] `#006-04` compare CLI 已补本地 / 脚本级 fail gate（`--alignment-min-score` / `--alignment-max-risk-level`，失败返回退出码 2）
- [x] README / README_EN / AGENTS 已补 gate 命令示例，方便后续接到本地检查 / CI
- [x] 已补 `test_backtest_main.py` 并回归通过 signal-service 回测相关 37 项测试
- [ ] 真实窗口验证已完成首轮；当前剩余工作改为校准结论回填与主链路修复

- [x] `#006-04` 已补 `alignment_risk_level / alignment_risk_summary / alignment_warning_counts` 产物字段
- [x] `#006-04` 的 TUI compare 视图已补风险等级展示，消费侧从“分数 + 状态”扩展为“分数 + 状态 + 风险”
- [x] 已回归通过 signal-service 回测相关 34 项测试 + TUI compare 2 项测试
- [ ] 真实窗口验证已完成首轮；当前剩余工作改为对齐失败原因定位与阈值收敛

### 2026-03-07

- [x] 确认后续以 issue 管理回测推进工作
- [x] 建立本 issue `#006` 作为回测推进主入口
- [x] 将 `docs/learn/backtest_plan.md` 降级为归档/背景文档
- [x] 明确首版交易所口径为 Binance USD-M Perpetual
- [x] 补齐 P0 / P1 / P2 的实施范围与验收细节
- [x] 已拆分 P0-1 子任务：`#006-01`
- [x] 已拆分 P0-2 子任务：`#006-02`
- [x] `#006-01` 首版强平模型已落地（代码 + 测试）
- [x] `#006-02` 首版成本模型已落地（代码 + 测试）
- [x] 已拆分 P0-3 子任务：`#006-03`
- [x] `#006-03` 输入质量产物链路已落地（代码 + 测试）
- [x] 已拆分 P0-4 子任务：`#006-04`
- [x] `#006-04` 首版对齐评分与阈值告警已落地（代码 + 测试）
- [x] `#006-04` 对齐评分已接入 TUI compare 视图读取链路（展示分数 / 状态 / 告警摘要）
- [x] `#006-01` 已补 `liquidation_buffer_bps` 边界样例，并修正强平边界浮点漏触发
- [x] `#006-01` 已补极端 gap 强平样例，按破产价上限封顶并验证强平后曲线钉平
- [x] `#006-03` 已为输入质量评分增加 `status + breakdown`，报告可解释扣分来源
- [x] `#006-02` 已增强成本归因解释字段，报告可区分 signal-driven / cost-heavy / funding-tailwind
- [x] 2026-03-12 已完成首轮真实窗口验证
- [ ] 下一步优先复工 `#006-03/#006-04`，随后再决定 `#006-01/#006-02` 是否可关闭

## 相关 Issue

- Child: `#006-01` - Binance 强平与维持保证金模型
- Child: `#006-02` - Binance 成本模型三件套
- Child: `#006-03` - 输入质量审计入产物
- Child: `#006-04` - 历史信号 vs 规则重放对齐评分

## 相关文档

- Related: `docs/learn/backtest_plan.md`
- Related: `docs/learn/backtest_prod_readiness_plan.md`
- Related: `docs/learn/backtest_freeze_20260214.md`

## 备注

后续若需要进一步细拆，建议把 P0-1 ~ P0-4 分拆成独立 issue，并以本 issue 作为父任务总控。
