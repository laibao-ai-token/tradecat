---
title: "006-01-feature-backtest-p0-1-binance-liquidation-model"
status: closed
created: 2026-03-07
updated: 2026-03-13
closed: 2026-03-13
owner: lixh6
priority: high
type: feature
---

# 回测 P0-1：Binance 强平与维持保证金模型补齐

## 进度条

- 总体：`██████████ 100%`
- Phase 1：`██████████ 100%`
- Phase 2：`██████████ 100%`
- Phase 3：`██████████ 100%`

## 背景

父任务 `#006` 已确认当前回测首版交易所口径为 **Binance USD-M Perpetual（USDT 本位永续）**。

当前执行层仍属于“工程链路可用（M1）”，主要问题是：

- 只有正常平仓 / 反手 / 中性平仓 / eod close
- 缺少维持保证金判定
- 缺少强平触发与强平成本
- `trades.csv` 无法解释强平原因与价格口径

这会导致极端行情下的收益、回撤与风险暴露判断偏乐观，无法作为实盘前决策依据。

## 目标

按 **Binance USD-M Perpetual** 首版口径，为 backtest 执行层补齐可解释的维持保证金与强平模型。

## 本期范围

1. 在回测配置中增加强平/维持保证金相关参数
2. 在执行引擎中加入维持保证金校验与强平分支
3. 为 `Trade` / `trades.csv` 增加强平原因、强平成本、价格口径信息
4. 增加极端行情测试样例，避免权益曲线异常跳变

## 非目标（本期不做）

- 不实现跨交易所统一强平模型
- 不实现 Binance 全量分档维持保证金表自动同步
- 不实现 ADL / 部分强平等更高复杂度机制
- 不修改生产 `config/.env`
- 不变更数据库 schema

## 配置建议

- `maintenance_margin_ratio`
- `liquidation_fee_bps`
- `liquidation_buffer_bps`
- 若后续需要，可预留 `exchange_profile=binance_usdm`

## 实现范围

- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `services/signal-service/tests/test_backtest_runner.py`
- 如需新增更聚焦测试，可补：`services/signal-service/tests/test_backtest_execution_engine.py`

## 实现清单

### Phase 1：模型字段与产物字段

- [x] 为执行/风险模型增加维持保证金与强平相关字段
- [x] 为 `Trade` 增加强平成本、强平价格口径、退出类型字段
- [x] 确认 `trades.csv` 可输出新增字段

### Phase 2：执行逻辑

- [x] 建立 Binance USD-M 首版强平判定逻辑
- [x] 在持仓存续期间持续检查是否触发强平
- [x] 支持四类退出：正常平仓 / 反手 / 中性平仓 / 强平
- [x] 强平后权益、费用、仓位状态保持一致且可解释

### Phase 3：验证

- [x] 增加极端行情测试样例
- [x] 覆盖 LONG / SHORT 双向强平场景
- [x] 覆盖“有缓冲 / 无缓冲”边界场景
- [x] 验证权益曲线不出现不合理跳变

## 验收标准

- [x] `trades.csv` 可解释强平原因、强平成本、触发价格口径
- [x] 执行层支持正常平仓 / 反手 / 中性平仓 / 强平四类退出
- [x] 极端行情样例下结果稳定、可复算（单测样例）
- [x] 文档和默认策略配置与 Binance USD-M 口径一致

## 风险与注意事项

- Binance 真实规则较复杂，首版允许做“保守简化实现”，但必须在文档中明确说明
- 若当前仅使用 K 线 OHLC 数据，强平价格判定需要明确采用的近似口径
- 若后续引入 mark price，需要保证与当前首版结果可区分、可追踪

## 相关文件

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `docs/learn/backtest_plan.md`
- `docs/learn/backtest_prod_readiness_plan.md`
- `services/signal-service/src/backtest/models.py`
- `services/signal-service/src/backtest/execution_engine.py`
- `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- `services/signal-service/tests/test_backtest_runner.py`

## 相关 Issue

- Parent: `#006`
- Blocks: `#006` P0-1 完成门槛

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

- 强平触发笔数：`0`
- LONG 强平是否可解释：`窗口内未发现 LONG 强平`
- SHORT 强平是否可解释：`窗口内未发现 SHORT 强平`
- 强平触发价格口径是否合理：`需人工复核`
- 强平成本是否偏大/偏小：`需人工复核`
- 是否出现异常权益跳变：`需人工复核 equity_curve.csv`

### 重点产物摘录

- `trades.csv` 强平样例行：`未找到`
- `report.md` 强平摘要：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-085524/report.md`
- `metrics.json` 相关字段：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-085524/metrics.json`

### 参数与结论

- 当前 `maintenance_margin_ratio` 是否合理：`默认窗口未触发强平；在 leverage=50 的真实历史 stress window 下，LONG/SHORT 均按阈值口径触发，首版实现可解释`
- 当前 `liquidation_fee_bps` 是否合理：`当前默认值为 0，stress window 中 liquidation_fee=0 与配置一致`
- 当前 `liquidation_buffer_bps` 是否合理：`当前默认值为 0，stress window 中触发价与公式口径一致`
- 是否需要继续调参：`P0 首版先不调参；后续若引入 mark price / 分档维持保证金，再单独校准`
- 最终结论：`代码、单测与真实历史 stress window 证据已齐，可作为 #006-01 首版收口依据`

### 回填完成检查

- [x] 已粘贴真实窗口执行命令与 `run_id`
- [x] 已给出至少 1 笔 LONG/SHORT 强平样例或说明窗口内未出现强平
- [x] 已判断强平价格口径与成本是否可接受
- [x] 已给出是否调参的明确结论

## 进展记录

### 2026-03-13

- [x] 已补真实历史 stress window：`./scripts/backtest.sh --config src/backtest/strategies/default.crypto.yaml --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00" --symbols BTCUSDT,ETHUSDT --min-signal-days 7 --min-signal-count 200 --min-candle-coverage-pct 95 --initial-equity 3000 --leverage 50 --position-size-pct 0.2 --mode history_signal --run-id stress-liq-lev50 --force`
- [x] 产物：`artifacts/backtest/20260312-164545/trades.csv` / `artifacts/backtest/20260312-164545/metrics.json` / `artifacts/backtest/20260312-164545/equity_curve.csv`
- [x] stress window 共触发 `13` 笔强平：`LONG=11 / SHORT=2`，全部使用 `exit_price_source=binance_usdm_liquidation_threshold`
- [x] LONG 样例：`ETHUSDT 2026-02-09 23:44:00+00:00 -> 2026-02-10 03:19:00+00:00`，`entry=2111.18442974 / exit=2079.51666330 / liquidation_price=2079.51666330`
- [x] SHORT 样例：`ETHUSDT 2026-02-11 13:46:00+00:00 -> 2026-02-11 14:07:00+00:00`，`entry=1955.40415904 / exit=1984.73522143 / liquidation_price=1984.73522143`
- [x] 已检查 `equity_curve.csv`：最小权益 `732.77`，未出现负权益或异常反弹；当前首版强平链路可视为通过
- [x] 判定结果：`#006-01` 已满足关闭条件并关闭

### 2026-03-12

- [x] 已执行真实窗口：`./scripts/backtest_real_window_validation.sh --force`
- [x] 当前窗口 `real-window-20260312-085436-history` 未触发任何强平，`trades.csv` 中无强平样例
- [ ] 由于缺少真实窗口强平事件，尚不能确认 `maintenance_margin_ratio / liquidation_fee_bps / liquidation_buffer_bps` 是否收敛
- [x] 后续已通过真实历史 stress window 补齐 LONG / SHORT 强平样本

### 2026-03-08

- [x] 已补“真实窗口回填模板”，PG 恢复后可直接按 issue 回填真实窗口校准结论
- [ ] 待执行 `./scripts/backtest_real_window_validation.sh` 后补实测结果

### 2026-03-07

- [x] 从父任务 `#006` 拆分出 P0-1 独立 issue
- [x] 明确首版交易所口径为 Binance USD-M Perpetual
- [x] 首版维持保证金 / 强平分支已落地
- [x] `trades.csv` 已输出强平原因、价格口径与强平成本字段
- [x] 已补充并通过 LONG / SHORT 定向单测
- [x] 已补 `liquidation_buffer_bps` 的 LONG / SHORT 边界样例
- [x] 已修正强平边界值受浮点误差影响导致的漏触发问题
- [x] 已补 gap 强平样例，入场后极端跳空时按破产价上限封顶，避免权益曲线异常反弹/穿透
- [x] 下一步进入真实窗口验证与 Binance 实盘窗口复核

## 备注

首版优先保证“结果不明显失真 + 产物可解释”，后续再考虑分档保证金、部分强平、ADL 等增强项。
