# Backtest 状态冻结记录（2026-02-14）

## 1) 冻结结论

- 当前回测链路已打通，可稳定运行：
  - `history_signal`
  - `offline_replay`
  - `offline_rule_replay`
  - `compare_history_rule`
- 当前阶段定义为：**工程链路可用（M1）**，未达到“实盘前可用（P0完成）”。

## 2) 冻结时未关闭问题（后续排期）

1. P0-1：执行层缺少维持保证金/强平模型（仅有 normal close/reverse/eod close）。
2. P0-2：成本模型仍为单费率（`fee_bps`），未拆分 maker/taker/funding。
3. P0-3：未产出 `input_quality.json`，输入质量审计未落盘。
4. P0-4：对齐报告有 overlap/jaccard，但尚未输出 `alignment_score`。

## 3) 本次冻结范围说明

- 冻结对象：回测主流程、TUI回测展示、三模式运行路径与产物格式。
- 暂不推进：强平模型、成本拆分、输入质量审计、对齐评分增强。
- 后续恢复开发时，按 P0 顺序推进：P0-1 -> P0-2 -> P0-3 -> P0-4。

## 4) 解冻触发条件（建议）

- 有明确时间窗口处理 P0 项；
- 或出现“回测结论无法支持参数决策”的业务阻塞。
