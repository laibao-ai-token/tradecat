---
title: "006-02-feature-backtest-p0-2-binance-cost-model"
status: closed
created: 2026-03-07
updated: 2026-03-13
closed: 2026-03-13
owner: lixh6
priority: high
type: feature
---

# 回测 P0-2：Binance 成本模型升级为交易成本三件套

## 进度条

- 总体：`██████████ 100%`
- Phase 1：`██████████ 100%`
- Phase 2：`██████████ 100%`
- Phase 3：`██████████ 100%`
- Phase 4：`██████████ 100%`（真实窗口对账）

## 背景

父任务 `#006` 已确认当前回测首版交易所口径为 **Binance USD-M Perpetual（USDT 本位永续）**。

当前成本模型仍只有：

- 单一 `fee_bps`
- 固定 `slippage_bps`

这意味着：

- 无法区分 maker / taker 成本
- 无法表达 funding 对净收益的侵蚀
- 回测报告无法回答“收益来自策略还是只是忽略了成本”

## 目标

按 **Binance USD-M Perpetual** 首版口径，把当前单费率模型升级为可审计的交易成本三件套：

1. `trading_fee`
2. `funding_fee`
3. `net_pnl`

## 本期范围

1. 为配置模型增加 maker / taker / funding 参数
2. 在执行引擎中拆分交易手续费与资金费
3. 在 `metrics.json` / `trades.csv` / `report.md` 中输出成本分解结果
4. 增加验证样例，确保净收益与成本明细一致

## 非目标（本期不做）

- 不实现 Binance VIP 阶梯费率自动匹配
- 不实现实时 funding 历史拉取与逐期精确回放
- 不实现复杂滑点分层模型（留给 P2-1）
- 不修改生产 `config/.env`
- 不变更数据库 schema

## 配置建议

- `maker_fee_bps`
- `taker_fee_bps`
- `funding_rate_bps_per_8h`
- 如首版无法按真实 funding 时间点回放，需文档明确采用的近似口径

## 实现范围

- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `services/signal-service/tests/test_backtest_runner.py`
- 如需新增更聚焦测试，可补：`services/signal-service/tests/test_backtest_reporter.py`

## 实现清单

### Phase 1：配置与模型

- [x] 用 maker / taker 参数替代单一 `fee_bps` 口径
- [x] 为 `Trade` / `Metrics` 增加 `trading_fee`、`funding_fee`、`net_pnl` 等字段
- [x] 兼容旧配置迁移或明确声明不兼容策略

### Phase 2：执行与结算

- [x] 开仓 / 平仓正确计入交易手续费
- [x] 持仓期间正确计入 funding 成本（首版允许近似实现）
- [x] LONG / SHORT 双向资金费方向清晰且可验证
- [x] 保证 `gross_pnl - trading_fee - funding_fee = net_pnl`

### Phase 3：产物与报告

- [x] `metrics.json` 输出 `gross_pnl / trading_fee / funding_fee / net_pnl`
- [x] `trades.csv` 输出单笔成本拆分
- [x] `report.md` 增加成本侵蚀解释
- [x] 报告可解释“收益来自信号”还是“被成本侵蚀”

### Phase 4：真实窗口对账（后续）

- [ ] 用真实回测窗口验证成本口径与旧结果差异

## 验收标准

- [x] `metrics.json` 输出 `gross_pnl / trading_fee / funding_fee / net_pnl`
- [x] `trades.csv` 可审计单笔交易成本拆分
- [x] 报告中能解释成本对收益的影响
- [x] Binance USD-M 首版成本口径在文档与配置中标注清楚

## 风险与注意事项

- funding 若只做固定近似，会和真实逐期 funding 有偏差，必须明示
- 若 maker/taker 首版统一按 taker 落地，也必须在配置和报告中说明
- 成本拆分会影响历史回测结果，不应与旧结果混在同一口径下比较

## 相关文件

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `docs/learn/backtest_plan.md`
- `docs/learn/backtest_prod_readiness_plan.md`
- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `services/signal-service/tests/test_backtest_runner.py`

## 相关 Issue

- Parent: `#006`
- Blocks: `#006` P0-2 完成门槛

## 真实窗口回填模板

> 本区块由 `scripts/backtest_issue_fill.py` 自动生成，时间：`2026-03-12 09:18:39.236395+00:00`
> 带“需人工复核 / 待人工补充”的项仍需人工最终确认。

### 执行信息

- 执行日期：`2026-03-12 09:18:39.236395+00:00`
- 执行命令：`./scripts/backtest_real_window_validation.sh --force`
- `run_id`：`real-window-20260312-085436-history`
- 时间窗口：`2026-01-14 00:00:00+00:00 -> 2026-02-13 00:00:00+00:00`
- symbols：`BTCUSDT,ETHUSDT`
- DB target：`localhost:5434/market_data`

### 真实窗口观察

- `gross_pnl`：`-52.37`
- `trading_fee`：`58.64`
- `funding_fee`：`0.00`
- `net_pnl`：`-111.01`
- `gross_to_net_retention_pct`：`211.97%`
- funding 方向解释是否合理：`需人工复核`
- 成本是否明显侵蚀策略收益：`Strategy loses before cost, and costs worsen the final result.`

### 重点产物摘录

- `metrics.json` 成本字段：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-085524/metrics.json`
- `report.md` 成本摘要：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-085524/report.md`
- `trades.csv` 单笔成本样例：`BTCUSDT | 2026-02-09 11:12:00+00:00 -> 2026-02-09 11:33:00+00:00 | qty=0.01746918 | reason=reverse_to_long`

### 参数与结论

- 当前 `maker_fee_bps` 是否合理：`当前 next_open 执行全部按 taker 口径计价；报告已明确该假设，maker_fee_bps 预留给未来被动成交路径`
- 当前 `taker_fee_bps` 是否合理：`真实窗口下交易费用持续落入产物，口径稳定可审计`
- 当前 `funding_rate_bps_per_8h` 是否合理：`在真实历史 stress cost 配置（20bps/8h）下，已验证 LONG 支付 funding、SHORT 获得 funding credit`
- 是否需要按 Binance VIP / maker-taker 场景细分：`P0 首版暂不细分；后续若引入被动成交或 VIP 费率，再单独立项`
- 最终结论：`成本链路、报告披露与真实历史 stress window 对账已齐，可作为 #006-02 首版收口依据`

### 回填完成检查

- [x] 已回填 `gross_pnl / trading_fee / funding_fee / net_pnl`
- [x] 已说明 funding 在 LONG/SHORT 方向上的解释是否合理
- [x] 已判断成本侵蚀是否符合 Binance USD-M 首版预期
- [x] 已给出是否调参/分层的明确结论

## 进展记录

### 2026-03-13

- [x] 已补真实历史 stress cost window：基于默认策略派生 `artifacts/backtest/20260312-164746/config-stress-cost-funding20.json`，设置 `funding_rate_bps_per_8h=20`
- [x] 执行命令：`./scripts/backtest.sh --config /tmp/tradecat_backtest_cost_funding20.json --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00" --symbols BTCUSDT,ETHUSDT --min-signal-days 7 --min-signal-count 200 --min-candle-coverage-pct 95 --initial-equity 3000 --leverage 2 --position-size-pct 0.2 --mode history_signal --run-id stress-cost-funding20 --force`
- [x] 产物：`artifacts/backtest/20260312-164746/metrics.json` / `artifacts/backtest/20260312-164746/report.md` / `artifacts/backtest/20260312-164746/trades.csv`
- [x] stress window 结果：`gross_pnl=-52.1613 / trading_fee=58.4134 / funding_fee=22.5846 / net_pnl=-133.1593`
- [x] LONG funding 样例：`BTCUSDT 2026-02-09 23:38:00+00:00 -> 2026-02-10 11:50:00+00:00`，`funding_fee=+3.68792315`，符合 LONG 支付 funding 的预期
- [x] SHORT funding 样例：`BTCUSDT 2026-02-10 18:28:00+00:00 -> 2026-02-11 01:35:00+00:00`，`funding_fee=-2.09940344`，符合 SHORT 获得 funding credit 的预期
- [x] 已补报告口径说明：`report.md` 现在会明确声明 `next_open` 执行按 taker 费率计价；定向测试 `services/signal-service/tests/test_backtest_runner.py services/signal-service/tests/test_backtest_reporter.py` → `30 passed`
- [x] 判定结果：`#006-02` 已满足关闭条件并关闭

### 2026-03-12

- [x] 已执行真实窗口：`./scripts/backtest_real_window_validation.sh --force`
- [x] 历史窗口产物显示：`gross_pnl=-52.37 / trading_fee=58.64 / funding_fee=0.00 / net_pnl=-111.01`
- [x] 已确认当前窗口中“策略先亏损、成本进一步放大亏损”这一结论成立
- [x] funding 方向与 taker 费率假设已通过后续真实历史 stress window 补齐

### 2026-03-08

- [x] 已补“真实窗口回填模板”，PG 恢复后可直接按 issue 回填真实窗口校准结论
- [ ] 待执行 `./scripts/backtest_real_window_validation.sh` 后补实测结果

### 2026-03-07

- [x] 从父任务 `#006` 拆分出 P0-2 独立 issue
- [x] 明确首版交易所口径为 Binance USD-M Perpetual
- [x] 首版 maker / taker / funding 成本链路已落地
- [x] `metrics.json` / `trades.csv` / `report.md` 已输出成本拆分字段
- [x] 已补充并通过定向单测
- [x] 已为 `metrics.json/report.md` 增加 `cost_status / cost_summary / gross_to_net_retention_pct / cost_erosion_pct_of_gross` 等解释字段
- [x] 下一步用真实回测窗口验证成本口径与旧结果差异

## 备注

首版优先保证“成本口径透明 + 结果可审计”，后续再继续推进 funding 精细回放、VIP 阶梯费率与分层滑点。
