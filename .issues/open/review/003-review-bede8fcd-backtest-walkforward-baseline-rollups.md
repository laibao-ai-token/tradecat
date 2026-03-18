---
title: "003-review-bede8fcd-backtest-walkforward-baseline-rollups"
status: review
created: 2026-03-17
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-27
---

# [Review-003][bede8fcd] walk-forward baseline rollups

## Review 输入

- Commit: `bede8fcd1784de8aa5aa545a17fa705401552e56`
- 标题: `feat(backtest): add walk-forward baseline rollups`
- 指令: `codex review --commit bede8fcd`

## Review 结果（回填）

### Findings

1. [Low] Walk-Forward 文档未明确 `best_baseline_return_pct` 的计算口径（avg vs compounded），存在解读歧义。
   - 代码中 `best_baseline_name/best_baseline_return_pct` 基于 compounded baseline 回报计算，而不是 avg baseline 回报。
   - 证据：`services/signal-service/src/backtest/walkforward.py:120-137`、`services/signal-service/src/backtest/walkforward.py:667-679`、`services/signal-service/src/backtest/walkforward.py:793-805`。
   - 文档当前只列出 `avg_*` 与 `best_baseline_*` 字段，未说明二者口径差异。
   - 证据：`docs/learn/backtest_real_window_validation.md:112-115`。
   - 建议：在 runbook/README 的 Walk-Forward 字段说明中补充 `best_baseline_*` 采用 compounded 口径，避免按均值解读。

### 检查重点结论

- rollup 指标定义与窗口：代码已输出 `avg_*` 与 `*_compounded_*` 两套聚合，并在 summary/metrics 保持一致；未发现窗口边界错误。
- 每折可追溯性：每折产物包含 `selected_params`，训练窗选参记录 `candidate_scores/train_score/candidate_name/train_eval_mode`，可追溯参数、得分与选择依据。
  - 证据：`services/signal-service/src/backtest/walkforward.py:422-507`、`services/signal-service/src/backtest/walkforward.py:683-707`、`services/signal-service/src/backtest/walkforward.py:807-818`。
- retention 与目录结构：保留策略仍以 `artifacts/backtest/<run_id>` 顶层目录为单位清理，`latest` 更新逻辑保持不变；未发现破坏既有目录结构的回归。
  - 证据：`services/signal-service/src/backtest/retention.py:119-125`。

### 验证记录

- 已执行：`codex review --commit bede8fcd`（当前环境中命令持续输出执行日志，未在超时窗口内返回最终 findings 段；本回填基于该日志与人工复核完成）。
- 已执行：

```bash
cd services/signal-service
pytest -q tests/test_backtest_walkforward.py tests/test_backtest_reporter.py
```

- 结果：`11 passed in 0.55s`

## 后续处理（2026-03-17）

- 已按本单 Low finding 完成文档修复：`docs/learn/backtest_real_window_validation.md` 已补充 `best_baseline_return_pct` 为 compounded 口径说明。
