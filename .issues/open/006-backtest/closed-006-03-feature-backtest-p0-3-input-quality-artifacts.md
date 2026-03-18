---
title: "006-03-feature-backtest-p0-3-input-quality-artifacts"
status: closed
created: 2026-03-07
updated: 2026-03-13
closed: 2026-03-13
owner: lixh6
priority: high
type: feature
---

# 回测 P0-3：输入质量审计入产物

## 进度条

- 总体：`██████████ 100%`
- Phase 1：`██████████ 100%`
- Phase 2：`██████████ 100%`
- Phase 3：`██████████ 100%`

## 背景

父任务 `#006` 已确认，回测在进入“实盘前可用”前，必须把输入质量审计沉淀为标准产物，而不仅是 CLI 预检日志。

当前已具备 precheck guard，但仍缺少：

- `input_quality.json` 标准产物
- 每 symbol 缺口统计
- 无 `next_open` 可成交次数
- 被丢弃信号计数
- 回测报告中的质量评分 / 覆盖度展示

## 目标

把当前 precheck 从“命令行检查”推进为“每次 run 可复算、可追踪、可解释的输入质量产物链路”。

## 实现范围

- `services/signal-service/src/backtest/precheck.py`
- `services/signal-service/src/backtest/runner.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/tests/test_backtest_precheck.py`
- `services/signal-service/tests/test_backtest_reporter.py`
- `services/signal-service/tests/test_backtest_runner.py`

## 实现清单

### Phase 1：质量模型与 JSON 产物

- [x] 新增 run 级 `input_quality.json` 产物
- [x] 新增 per-symbol 质量诊断结构
- [x] 记录覆盖率、gap、`no_next_open`、`dropped_signal` 等字段

### Phase 2：回测主链路接入

- [x] 在 runner 中生成并落盘输入质量产物
- [x] 在 `report.md` 中展示输入质量摘要
- [x] `quality_score` 已增加 `status + breakdown`，具备静态可解释校准基础
- [x] 修复 `input_quality` 与 precheck guard 的 signal-day / gate 口径表达

### Phase 3：验证

- [x] 补充 precheck/input-quality 定向测试
- [x] 补充 reporter/input-quality 产物测试
- [ ] 用真实数据窗口复跑，确认修复后的 `signal_days / gate_status / quality_status` 与 real-window 结果一致

## 验收标准

- [x] 新增 `input_quality.json`
- [x] 记录每 symbol 缺口统计、无 `next_open` 可成交次数、被丢弃信号计数
- [x] `report.md` 显示数据质量评分 / 覆盖度
- [x] 覆盖不足时默认阻断（除非显式 `--force`）

## 相关文件

- `.issues/open/006-backtest/006-feature-backtest-prod-readiness.md`
- `services/signal-service/src/backtest/precheck.py`
- `services/signal-service/src/backtest/runner.py`
- `services/signal-service/src/backtest/reporter.py`
- `services/signal-service/tests/test_backtest_precheck.py`
- `services/signal-service/tests/test_backtest_reporter.py`
- `services/signal-service/tests/test_backtest_runner.py`

## 相关 Issue

- Parent: `#006`
- Blocks: `#006` P0-3 完成门槛

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

- `quality_score`：`100.00`
- `quality_status`：`pass`
- `signal_days`：`4（precheck guard） / 1063（input_quality aggregated_signal_bucket_count）`
- `signal_count`：`1534`
- `candle_coverage_pct`：`100.00%`
- 缺失 bar 是否集中在少数 symbol：`否；BTCUSDT / ETHUSDT 的 candle_coverage_pct 均为 100%`
- 无 `next_open` 可成交次数是否异常：`0`

### 重点产物摘录

- `input_quality.json` 摘要：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-085524/input_quality.json`
- `report.md` 质量摘要：`/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/artifacts/backtest/20260312-085524/report.md`
- `quality_breakdown` / penalty 来源：`{"coverage_score": 100.0, "missing_candle_penalty": 0.0, "gap_penalty": 0.0, "no_next_open_penalty": 0.0, "dropped_signal_penalty": 0.0, "missing_candle_ratio_pct": 0.0, "no_next_open_ratio_pct": 0.0, "dropped_signal_ratio_pct": 0.0, "gap_count": 0, "largest_gap_minutes": 0, "quality_score": 100.0}`

### 参数与结论

- 默认 `--min-signal-days 7` 是否合理：`合理；当前问题不是门槛过严，而是 input_quality 未输出与 precheck 同口径的 signal-day 覆盖`
- 默认 `--min-signal-count 200` 是否合理：`合理；当前窗口 signal_count=1534，不构成阻塞`
- 默认 `--min-candle-coverage-pct 95` 是否合理：`合理；当前窗口 candle_coverage_pct=100%`
- 是否需要上调/下调门槛：`暂不调整门槛，先统一 input_quality 与 precheck guard 的统计口径`
- 最终结论：`首轮结论是必须复工；2026-03-13 同窗复验已确认当前 input_quality 与 precheck gate 口径一致，原“pass vs fail”冲突已消失`

### 回填完成检查

- [x] 已回填 `quality_score / quality_status / coverage` 关键结果
- [x] 已说明缺失 bar / 无 next_open 是否集中于个别 symbol
- [x] 已判断默认 coverage gate 是否需要调整
- [x] 已给出是否继续阻断/放宽的明确结论

## 进展记录

### 2026-03-12

- [x] 已执行真实窗口：`./scripts/backtest_real_window_validation.sh --force`
- [x] 已确认当前窗口 `candle_coverage_pct=100% / no_next_open=0 / dropped_signal=0`
- [x] 已确认 `input_quality.json` 给出 `quality_score=100 / pass`，但 precheck guard 同时报告 `signal_days=4<7`
- [ ] 判定结果：`#006-03` 需要复工，先把 input-quality 与 precheck guard 的 signal-day 口径统一，再讨论是否调门槛

### 2026-03-13

- [x] 已完成 `TRA-20` patch review，确认 `#006-03` 主修复方向可合
- [x] 已将 `TRA-20` 的可用部分合入主仓工作树：`precheck/__main__/runner/reporter/backtest_issue_fill + 定向测试 + README`
- [x] 已保留主仓现有 `strategy_context` / stability 对比链路，未接受会回退现有能力的改动
- [x] 已验证：`PYTHONPATH=libs python -m pytest services/signal-service/tests/test_backtest_precheck.py services/signal-service/tests/test_backtest_reporter.py services/signal-service/tests/test_backtest_runner.py -q` → `37 passed`
- [x] 已用同一真实窗口重跑：`./scripts/backtest.sh --config src/backtest/strategies/default.crypto.yaml --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00" --symbols BTCUSDT,ETHUSDT --min-signal-days 7 --min-signal-count 200 --min-candle-coverage-pct 95 --initial-equity 3000 --leverage 2 --position-size-pct 0.2 --mode history_signal --run-id real-window-20260313-0021-history --force`
- [x] 同窗复验结果：`artifacts/backtest/20260312-162023/input_quality.json` 显示 `quality_score=100 / quality_status=fail / gate_status=fail / signal_days=4 / aggregated_signal_bucket_count=1063`
- [x] 已确认不再出现“`quality_status=pass` 但 precheck `signal_days=4<7`”的口径冲突，`#006-03` 技术阻塞解除
- [x] 判定结果：`#006-03` 已满足关闭条件并关闭

### 2026-03-08

- [x] 已补“真实窗口回填模板”，PG 恢复后可直接按 issue 回填真实窗口校准结论
- [x] 已执行 `./scripts/backtest_real_window_validation.sh --force` 并补入首轮实测结果

### 2026-03-07

- [x] 从父任务 `#006` 拆分出 P0-3 独立 issue
- [x] `input_quality.json` 已接入回测产物链路
- [x] `report.md` 已展示输入质量摘要
- [x] 已补充并通过定向测试
- [x] 已为 `quality_score` 增加 penalty breakdown，并在 `report.md` 展示质量状态与扣分来源
- [x] 已补定向测试锁定 `quality_score / quality_status / quality_breakdown`
- [ ] 下一步在真实回测窗口上校准质量评分与缺口统计
