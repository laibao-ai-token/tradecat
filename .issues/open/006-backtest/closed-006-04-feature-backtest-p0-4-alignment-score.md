---
title: "006-04-feature-backtest-p0-4-alignment-score"
status: closed
created: 2026-03-07
updated: 2026-03-13
closed: 2026-03-13
owner: lixh6
priority: high
type: feature
---

# 回测 P0-4：历史信号 vs 规则重放对齐评分

## 进度条

- 总体：`██████████ 100%`
- Phase 1：`██████████ 100%`
- Phase 2：`██████████ 100%`
- Phase 3：`██████████ 100%`

## 背景

父任务 `#006` 已确认，当前回测首版口径聚焦 **Binance USD-M Perpetual（USDT 本位永续）**。

当前历史信号 vs 规则重放对比已具备：

- `rule_overlap`
- `jaccard`
- `missing_history_rules_top`
- `missing_history_rules_diagnostics`

但还缺少一个可直接复用的统一结论层：

- 固定 `0~100` 的 `alignment_score`
- 关键规则偏差超阈值时的标准告警
- 便于 TUI / 本地检查 / CI 复用的稳定字段结构

如果没有这个评分层，`compare_history_rule` 仍需要人工阅读多组统计，无法快速判断“当前回测口径是否已经偏离到不能信”。

## 目标

把“历史信号 vs 129 规则离线重放”的对比，从统计明细推进为**可机器消费的最小口径对齐评分**。

## 本期范围

1. 为 `comparison.json` 增加固定 `0~100` 的 `alignment_score`
2. 为关键偏差增加结构化阈值告警
3. 在 `comparison.md` 中输出评分、分项拆解与告警摘要
4. 补充定向单测，锁住 pass / warn / fail 语义

## 非目标（本期不做）

- 不实现真实交易所撮合级别的逐条信号机会重建
- 不在本期内默认开启对齐硬阻断；仅提供显式 CLI / 本地检查 gate
- 不在本期内直接改 CI workflow；仅先补本地 / 脚本级 gate 原语
- 不修改生产 `config/.env`
- 不变更数据库 schema

## 实现范围

- `services/signal-service/src/backtest/comparison.py`
- `services/signal-service/tests/test_backtest_comparison.py`
- `services-preview/tui-service/src/tui.py`
- `services-preview/tui-service/tests/test_quote.py`
- `services/signal-service/src/backtest/__main__.py`
- `services/signal-service/tests/test_backtest_main.py`
- 如真实窗口校准时发现诊断维度不足，再补 `services/signal-service/src/backtest/rule_replay.py`

## 实现清单

### Phase 1：评分模型与 JSON 产物

- [x] 新增 `alignment_score`
- [x] 新增 `alignment_status`
- [x] 新增 `alignment_breakdown`
- [x] 新增 `alignment_inputs / alignment_top_rules / alignment_thresholds`
- [x] 评分口径固定为 `0~100`

### Phase 2：阈值告警与 Markdown 报告

- [x] 对 rule overlap / history coverage 建立阈值告警
- [x] 对 signal count / direction mix / TopN rule delta 建立阈值告警
- [x] 对 `timeframe_no_data` 输出 error 级别告警
- [x] `comparison.md` 展示评分、分项拆解与 threshold warnings

### Phase 3：验证与校准

- [x] 补 comparison 定向测试
- [x] 覆盖对齐通过场景
- [x] 覆盖明显失配场景
- [x] 覆盖 `timeframe_no_data` 场景
- [x] 用真实回测窗口校准阈值 / 权重
- [x] TUI 已接入 `alignment_score` / `alignment_status` / `alignment_risk_level` / 告警摘要
- [x] 本地 / 脚本级 fail gate 已具备（`--alignment-min-score` / `--alignment-max-risk-level`）
- [ ] 视消费侧需要补 CI 接入

## 验收标准

- [x] `comparison.json` 输出 `alignment_score`
- [x] `comparison.json` 输出固定 `0~100` 评分结构
- [x] 关键规则偏差超阈值时产生结构化告警
- [x] `comparison.md` 可直接阅读评分结论与告警摘要
- [x] 用真实窗口完成阈值与权重校准

## 风险与注意事项

- 首版评分仍属于“最小可解释评分”，不是交易所级逐机会精确对账
- 当前 TopN 偏差主要基于历史/重放触发计数差异，不等于完整 trigger opportunity 重建
- 已完成首轮真实窗口校准；后续是否接 CI 仍按消费侧需要单独决策

## 相关文件

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `docs/learn/backtest_prod_readiness_plan.md`
- `docs/learn/backtest_freeze_20260214.md`
- `services/signal-service/src/backtest/comparison.py`
- `services/signal-service/tests/test_backtest_comparison.py`

## 相关 Issue

- Parent: `#006`
- Depends on: `#006-03` 之前的 comparison 诊断基础已具备
- Blocks: `#006` P0-4 完成门槛

## 真实窗口回填

### 执行信息

- 执行日期：`2026-03-13 01:40:42+00:00`
- 执行命令：`INDICATOR_SQLITE_PATH=artifacts/indicator_db/00604-compare-btc-eth-60d.db ./scripts/backtest.sh --config src/backtest/strategies/default.crypto.yaml --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00" --symbols BTCUSDT,ETHUSDT --min-signal-days 7 --min-signal-count 200 --min-candle-coverage-pct 95 --mode compare_history_rule --run-id real-window-00604-fixed --alignment-min-score 70 --alignment-max-risk-level medium --force`
- `run_id`：`real-window-00604-fixed`
- 请求窗口：`2026-01-14 00:00:00+00:00 -> 2026-02-13 00:00:00+00:00`
- compare 实际窗口：`2026-02-09 11:02:00+00:00 -> 2026-02-13 00:00:00+00:00`
- symbols：`BTCUSDT,ETHUSDT`
- DB target：`localhost:5434/market_data`

### 真实窗口观察

- `alignment_score`：`80.92`
- `alignment_status`：`warn`
- `alignment_risk_level`：`medium`
- `alignment_warning_counts`：`{"error": 0, "warn": 1, "info": 0}`
- `alignment_inputs`：`signal_count_delta_pct=4.89 / total_signal_count_delta_pct=146.22 / comparable_rule_signal_count=1609`
- 剩余主告警：`rule_type_jaccard_below_threshold (41.86 < 60.00)`
- 是否主要受 coverage / timeframe / rule drift 影响：`当前主问题已收敛为“4天 history 与 replay 的规则集合仍有扩张差异”；不再是 compare 主链路失效`

### 重点产物摘录

- `comparison.json`：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-174033/real-window-00604-fixed-compare/comparison.json`
- `comparison.md`：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-174033/real-window-00604-fixed-compare/comparison.md`
- `rule_replay_diagnostics.json`：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-174033/real-window-00604-fixed-rules/rule_replay_diagnostics.json`

### 阈值与结论

- 当前 `--alignment-min-score 70` 是否合理：`合理；修正 compare 口径后实测 80.92，已能稳定通过`
- 当前 `--alignment-max-risk-level medium` 是否合理：`合理；当前风险等级为 medium，说明可用但仍需保留告警阅读`
- 是否需要区分 BTC/ETH 与长尾币阈值：`暂不需要；先以主流币 overlap window 收口`
- 是否可以接入本地 CI gate：`本地 gate 已可用；是否接 CI 取决于消费侧是否接受 overlap-window 口径`
- 最终结论：`P0-4 主阻塞已解除，当前 local issue 可关闭；剩余仅是规则集合扩张告警，不再阻塞 compare gate`

## 进展记录

### 2026-03-12

- [x] 已执行真实窗口：`./scripts/backtest_real_window_validation.sh --force`
- [x] compare gate 实测结果：`alignment_score=4.55 / alignment_risk_level=critical / exit_code=2`
- [x] 已确认 `shared_rule_types=0`，Top missing rules 集中在 `KDJ / MACD` 主规则族
- [ ] 判定结果：当前阻塞已从“数据库不可达”切换为“规则重放主链路未收敛”，`#006-04` 需要复工
- [ ] 下一步先排查 `compare_history_rule / rule_replay` 的真实窗口语义，再讨论阈值和 CI gate

### 2026-03-13

- [x] 已确认旧版 `TRA-21` review blocker 描述不再适用于当前主仓，不作为收口依据
- [x] 已修复 compare-mode 运行口径：自动收缩到 `history signal overlap window`
- [x] 已修复 backtest 环境语义：默认不再隐式继承 `config/.env` 中的 `SIGNAL_RULE_TIMEFRAMES`
- [x] 已调整对齐计数口径：`signal_count_score` 改为基于 shared rules 计算，同时保留 `total_signal_count_delta_pct` 供人工审阅
- [x] 定向测试已通过：`services/signal-service/tests/test_backtest_rule_replay.py services/signal-service/tests/test_backtest_comparison.py services/signal-service/tests/test_backtest_main.py` → `15 passed`
- [x] 真实窗口复验：`alignment_score=80.92 / alignment_risk_level=medium / gate=pass`
- [x] 判定结果：`#006-04` 已达到关闭条件
- [x] 已按当前复验结论关闭 `#006-04`

### 2026-03-08

- [x] compare CLI 已补 `--alignment-min-score` / `--alignment-max-risk-level`，未达标时返回退出码 `2`
- [x] 新增 `test_backtest_main.py`，锁定 compare gate 成功/失败与模式约束语义
- [x] README / README_EN / AGENTS 已补对齐 gate 命令示例，便于本地检查 / CI 复用
- [x] `comparison.json` 已补 `alignment_risk_level / alignment_risk_summary / alignment_warning_counts`
- [x] `comparison.md` 已补风险等级与告警分级摘要，便于人工快速判读
- [x] TUI compare 视图已读取并展示 `alignment_risk_level`，同步保留风险摘要
- [x] 已补 comparison / TUI 定向测试，并回归通过 signal-service 34 项回测测试集 + TUI compare 2 项测试
- [x] 2026-03-12 已确认该阻塞过时：`5434` 已恢复可用，当前阻塞改为真实窗口 score 严重失配
- [x] 已补“真实窗口回填模板”，PG 恢复后可直接记录 score/risk/gate 结论

### 2026-03-07

- [x] 从父任务 `#006` 拆分出 P0-4 独立 issue
- [x] 首版 `alignment_score / alignment_status / alignment_breakdown` 已落地
- [x] 已补齐结构化 threshold warnings，并落盘到 `comparison.json/.md`
- [x] 已补 comparison 定向测试，并通过相关回测测试集
- [x] TUI 已可读取并展示 `alignment_score` / `alignment_status` / 告警计数与主告警
- [x] 2026-03-12 已完成首轮真实窗口校准
- [x] 已完成真实窗口口径修复；是否补 CI fail gate 后置到消费侧决策

## 备注

首版优先保证“可以快速判断是否偏离过大”，后续再继续往真实窗口校准、TUI 展示与命令级 fail gate 推进。
