# 回测真实窗口校准 Runbook

更新时间：2026-03-08

## 目标

在 TimescaleDB 恢复后，用一套固定命令把回测“工程可跑”推进到“真实窗口可解释、可校准、可对账”。

覆盖范围：

1. `P0-1` 强平/维持保证金口径复核
2. `P0-2` 成本三件套（trading / funding / net）复核
3. `P0-3` 输入质量与覆盖率复核
4. `P0-4` history vs rule 对齐 gate 复核
5. `P1/P2` Walk-Forward / stability / 多基准 / 滑点 / 执行约束的真实窗口解释力复核

## 前置条件

- `config/.env` 已配置可用的 `DATABASE_URL`
- TimescaleDB 已恢复，`5434/5433` 中至少一套可达
- `services/signal-service/.venv` 可用
- 指标 SQLite 已准备好（默认沿用现有回测脚本解析逻辑）

## 推荐命令

先只看将执行哪些命令：

```bash
./scripts/backtest_real_window_validation.sh --dry-run
```

确认无误后正式执行：

```bash
./scripts/backtest_real_window_validation.sh
```

校准结束后，可自动生成 issue 回填草稿：

```bash
python3 scripts/backtest_issue_fill.py --run-prefix <run_prefix> --print
```

如果确认要直接回写本地 issue 文件：

```bash
python3 scripts/backtest_issue_fill.py --run-prefix <run_prefix> --apply-issues
```

如需指定窗口：

```bash
./scripts/backtest_real_window_validation.sh \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --start "2026-02-01 00:00:00" \
  --end "2026-03-01 00:00:00"
```

## 脚本执行的四步

### 1. 覆盖率预检查

检查点：

- `signal_days >= 7`
- `signal_count >= 200`
- `candle_coverage_pct >= 95`

若失败：

- 先缩小窗口 / 缩小 symbols
- 或切换到 `offline_replay / offline_rule_replay` 做定位
- 非必要不要先用 `--force`

### 2. 对齐 gate

检查 `comparison.json`：

- `alignment_score >= 70`
- `alignment_risk_level <= medium`
- `missing_reasons` 是否集中在 `timeframe_no_data` / coverage 类问题

若失败：

- 先看 `comparison.md` 与 `rule_replay_diagnostics.json`
- 记录是“数据覆盖问题”还是“规则口径漂移”
- 需要把结论回填到 `#006-04`

### 3. 单次 history_signal 回测

重点检查 `metrics.json` / `report.md`：

- `gross_pnl / trading_fee / funding_fee / net_pnl`
- `cost_drag_pct_of_initial`
- `slippage_cost / impact_cost`
- `partial_fill_trade_count`
- `buy_hold_return_pct / risk_parity_return_pct / momentum_return_pct`
- `best_baseline_name`

重点检查 `trades.csv`：

- 是否出现 `LIQUIDATION` / 强平类退出
- `constraint_flags` 是否异常集中
- `fill_ratio` 是否过低
- `entry_slippage_bps / exit_slippage_bps` 是否明显失真

### 4. Walk-Forward

重点检查 `walk_forward_summary.json`：

- `fold_count`
- `avg_return_pct / avg_max_drawdown_pct / avg_excess_return_pct`
- `avg_buy_hold_return_pct / avg_risk_parity_return_pct / avg_momentum_return_pct`
- `best_baseline_name / best_baseline_return_pct`
- 各折 `selected_params`

重点检查：

- 是否依赖单一 fold 的偶然收益
- `aggressive / conservative / base` 是否有稳定偏好
- 多基准下是否仍保持可解释超额收益

## 推荐人工复核结论模板

### `#006-01` 强平 / 保证金

- 强平是否只发生在极端 K 线下
- 强平价格口径是否可解释
- 强平成本是否过大/过小

### `#006-02` 成本模型

- funding 是否在长短方向上表现合理
- trading fee 是否与 Binance USD-M 预期档位一致
- gross → net 保留率是否符合经验

### `#006-03` 输入质量

- 缺失 bar 是否集中在特定 symbol/window
- 无 `next_open` 可成交次数是否过高
- 是否需要提高默认 coverage gate

### `#006-04` 对齐评分

- score/risk 阈值是否仍合理
- 是否需要针对 BTC/ETH 与长尾币分层阈值
- 是否可以进入本地 CI gate

## 建议回填位置

执行完真实窗口校准后，把结论回填到：

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `.issues/open/006-backtest/006-01-feature-backtest-p0-1-binance-liquidation-model.md`（使用其中的“真实窗口回填模板”）
- `.issues/open/006-backtest/006-02-feature-backtest-p0-2-binance-cost-model.md`（使用其中的“真实窗口回填模板”）
- `.issues/open/006-backtest/006-03-feature-backtest-p0-3-input-quality-artifacts.md`（使用其中的“真实窗口回填模板”）
- `.issues/open/006-backtest/006-04-feature-backtest-p0-4-alignment-score.md`（使用其中的“真实窗口回填模板”）

## 当前阻塞

截至 2026-03-08，本 runbook 仍受以下条件阻塞：

- TimescaleDB 尚未恢复
- 因此只能先完成脚本、文档与 issue 准备，无法给出真实窗口阈值结论
