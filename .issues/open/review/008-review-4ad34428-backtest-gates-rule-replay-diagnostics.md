---
title: "008-review-4ad34428-backtest-gates-rule-replay-diagnostics"
status: review
created: 2026-03-18
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-32
---

# [Review-008][4ad34428] backtest gates + replay diagnostics

- Commit: `4ad3442867cb5696027c6e8d5d71781b3648ca89`
- Subject: `feat(backtest): tighten compare gates and rule replay diagnostics`
- 执行命令（要求 1）：`codex review --commit 4ad34428`

## Review 结果（回填）

### 总结
- 结论：本次变更在审查重点上实现完整，未发现会阻断合并的代码缺陷（no blocking findings）。
- compare gate 判定与退出码行为稳定：`--alignment-min-score/--alignment-max-risk-level` 仅在 `--mode compare_history_rule` 生效，命中 gate 时返回固定退出码 `2`。
- replay 诊断字段已透传到产物：`comparison.json` 含 `alignment_score/alignment_status/alignment_risk_level/alignment_warning_counts/alignment_warnings`，并携带 `missing_history_rules_diagnostics` 细项。
- replay 对比可解释性达标：`primary_block_reason` 覆盖 `timeframe_no_data` 与 `source_table_missing`，并在 `alignment_warnings` 生成对应告警类型。

### 检查重点回填
1. compare gate 判定条件与退出码
- `services/signal-service/src/backtest/__main__.py` 中 gate 失败通过 `_collect_alignment_gate_failures()` 收敛。
- 失败时写入 `mark_error(..., message=...alignment_gate=failed...)`，并返回 `_ALIGNMENT_GATE_EXIT_CODE = 2`。
- 覆盖测试：`tests/test_backtest_main.py::test_main_compare_mode_returns_gate_exit_code`、`test_main_rejects_alignment_gate_outside_compare_mode`。

2. 诊断字段透传（score/gate/failures）
- 产物字段：
  - 评分与状态：`alignment_score/alignment_status/alignment_risk_level/alignment_risk_summary`
  - 告警聚合：`alignment_warning_counts/alignment_warnings`
  - 诊断细项：`missing_history_rules_diagnostics`（包含 `evaluated/condition_failed/timeframe_filtered/volume_filtered/cooldown_blocked/triggered/primary_block_reason`）
- 关键链路：
  - `runner.py::_write_rule_replay_diagnostics()` 写出 `rule_counters/rule_timeframe_profiles/rule_source_profiles`
  - `comparison.py::_load_rule_replay_diagnostics()` + `_build_missing_rule_diagnostics()` + `_build_alignment_assessment()` 透传并形成可读告警

3. replay 报告可解释性（missing/timeframe_no_data）
- `comparison.py::_resolve_primary_block_reason()` 在 `timeframe_filtered>0 && triggered==0` 且无 overlap 时返回 `timeframe_no_data`。
- 对缺表场景返回 `source_table_missing`，并在 alignment warnings 中标记 `top_rule_source_table_missing`。
- 覆盖测试：
  - `tests/test_backtest_comparison.py::test_write_comparison_artifacts_marks_timeframe_no_data_reason`
  - `tests/test_backtest_comparison.py::test_write_comparison_artifacts_marks_source_table_missing_reason`

### 验证记录
- `codex review --commit 4ad34428`：命令已执行；在当前受限环境下长时间运行且未稳定返回最终总结（日志保存在 `/tmp/review32_full.out`，可见其已完成 diff/测试相关子步骤）。
- `bash services/signal-service/scripts/backtest.sh --check-only --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00"`：受环境阻塞（脚本触发虚拟环境重建/依赖安装，当前环境无外网，pip 拉取失败）。
- `bash services/signal-service/scripts/backtest.sh --mode compare_history_rule --symbols BTCUSDT,ETHUSDT --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00"`：同上阻塞。
- `cd services/signal-service && pytest -q tests/test_backtest_main.py tests/test_backtest_precheck.py tests/test_backtest_comparison.py tests/test_backtest_rule_replay.py tests/test_backtest_runner.py`：通过（`46 passed`）。

### 风险与备注
- 非代码缺陷风险：`services/signal-service/scripts/backtest.sh` 在该环境下会尝试创建/刷新 `.venv` 并触发联网安装，导致离线验证无法按 runbook 直接复现。该风险不影响本 commit 的 gate/diagnostics 逻辑正确性，但影响本地验证可执行性。
