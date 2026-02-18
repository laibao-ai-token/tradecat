# Backtest 实盘可用性改造清单（P0 / P1 / P2）

> 版本日期：2026-02-14  
> 目标：将当前“可回测可对比”提升为“可用于实盘前决策评估”的可靠回测框架。

---

## 1. 当前基线（现状）

- 已具备完整模式链路：`history_signal` / `offline_replay` / `offline_rule_replay` / `compare_history_rule`
- 已具备预检门槛、Walk-Forward 摘要、TUI 回测展示与运行状态
- 已有测试覆盖骨架，当前回测相关测试通过
- 主要短板：执行层风控模型较简化（维持保证金/强平/资金费口径尚未完整建模），以及线上信号引擎与离线重放口径尚未逐条收敛

---

## 2. 目标分层定义

### 2.1 P0（必须完成，才可称“实盘前可用”）

**目标**：回测收益/回撤结论不因关键执行假设缺失而明显失真。

### 2.2 P1（应完成，提升稳定性和可解释性）

**目标**：结果可诊断、可复算、可追责，便于持续调参与版本对比。

### 2.3 P2（可选增强，追求贴近真实交易环境）

**目标**：降低“纸面收益”与真实交易之间的偏差。

---

## 3. P0 清单（建议 1.5~2.5 周）

### P0-1 执行层补齐维持保证金与强平模型

- **改造点**：
  - `services/signal-service/src/backtest/models.py`
  - `services/signal-service/src/backtest/execution_engine.py`
  - `services/signal-service/src/backtest/strategies/default.crypto.yaml`
- **新增配置建议**：
  - `maintenance_margin_ratio`
  - `liquidation_fee_bps`
  - `liquidation_buffer_bps`
- **验收标准**：
  - 可复现“正常平仓 / 反手 / 中性平仓 / 强平”四类退出
  - `trades.csv` 增加强平原因和强平成本
  - 极端行情下权益不出现不合理跳变（有测试样例）
- **预估工时**：2~3 人天

### P0-2 成本模型从“单费率”升级为“交易成本三件套”

- **改造点**：
  - `services/signal-service/src/backtest/models.py`
  - `services/signal-service/src/backtest/execution_engine.py`
  - `services/signal-service/src/backtest/reporter.py`
- **新增配置建议**：
  - `maker_fee_bps`
  - `taker_fee_bps`
  - `funding_rate_bps_per_8h`（可选启用）
- **验收标准**：
  - `metrics.json` 能分解 `gross_pnl / trading_fee / funding_fee / net_pnl`
  - 报告中可解释“收益由信号贡献还是成本侵蚀”
- **预估工时**：1.5~2.5 人天

### P0-3 数据完整性审计入产物（避免“伪结果”）

- **改造点**：
  - `services/signal-service/src/backtest/precheck.py`
  - `services/signal-service/src/backtest/runner.py`
  - `services/signal-service/src/backtest/reporter.py`
- **新增产物建议**：
  - `input_quality.json`（每 symbol 缺口统计、无 next_open 可成交次数、被丢弃信号计数）
- **验收标准**：
  - 回测报告显示“数据质量评分/覆盖度”
  - 覆盖不足可阻断运行（除非显式 `--force`）
- **预估工时**：1.5~2 人天

### P0-4 离线重放与历史信号最小口径对齐基准

- **改造点**：
  - `services/signal-service/src/backtest/rule_replay.py`
  - `services/signal-service/src/backtest/comparison.py`
  - `services/signal-service/tests/test_backtest_rule_replay.py`
- **目标**：
  - 建立 TopN 规则的“触发差异阈值”基准（例如方向偏差、触发率偏差）
- **验收标准**：
  - `comparison.json` 输出对齐评分（0~100）
  - 关键规则偏差超阈值时，CI 或本地检查直接告警
- **预估工时**：1.5~2 人天

---

## 4. P1 清单（建议 1~1.5 周）

### P1-1 Walk-Forward 加入“训练窗参数选择”

- **改造点**：`services/signal-service/src/backtest/walkforward.py`
- **目标**：不只滚动测试；训练窗用于阈值候选选择（小网格搜索即可）
- **验收标准**：
  - 每个 fold 记录 `selected_params`
  - `walk_forward_summary.json` 可追踪每折参数与收益关系
- **预估工时**：2~3 人天

### P1-2 指标稳定性报告（跨 run 对比）

- **改造点**：
  - `services/signal-service/src/backtest/reporter.py`
  - `services/signal-service/src/backtest/retention.py`
- **目标**：输出最近 N 次 run 的收益、回撤、胜率漂移
- **验收标准**：
  - 产物新增 `stability_report.json/.md`
  - 可快速识别“参数过拟合导致的性能塌陷”
- **预估工时**：1.5~2 人天

### P1-3 TUI 增强：显示输入质量与口径对齐分

- **改造点**：`services-preview/tui-service/src/tui.py`
- **目标**：回测页直接展示质量风险（不是只看收益率）
- **验收标准**：
  - TUI 回测视图可读到 `input_quality` 与 `alignment_score`
  - 异常状态给出可执行修复提示（命令级）
- **预估工时**：1~1.5 人天

---

## 5. P2 清单（建议 1~2 周，可并行）

### P2-1 分层滑点模型（按波动/成交量/时段）

- **目标**：滑点不再固定 bps，而是随市场状态变化
- **预估工时**：2~3 人天

### P2-2 订单执行约束（部分成交/最小成交量/冲击）

- **目标**：降低大仓位下“可成交性”高估
- **预估工时**：2~4 人天

### P2-3 多基准比较（B&H、风险平价、简单动量）

- **目标**：避免只和单一基准比较导致误判
- **预估工时**：1~2 人天

---

## 6. 推荐实施顺序（可直接执行）

### 第 1 周（P0 优先）

1. P0-1 强平模型
2. P0-2 成本模型
3. P0-3 输入质量审计

### 第 2 周（P0 收口 + P1 起步）

1. P0-4 规则对齐评分
2. P1-1 Walk-Forward 训练窗选参
3. P1-3 TUI 风险可视化

### 第 3 周（稳定性提升）

1. P1-2 稳定性报告
2. P2 项按资源并行

---

## 7. 里程碑验收门槛（建议）

### P0 完成门槛

- 回测产物包含：`metrics.json`、`report.md`、`input_quality.json`
- 执行层支持强平并可解释
- 成本拆分可审计
- 对齐评分可输出且有阈值告警

### P1 完成门槛

- Walk-Forward 每折具备参数选择记录
- 稳定性报告可显示跨 run 漂移
- TUI 可直接显示质量与对齐风险

---

## 8. 任务拆分建议（Issue 模板）

每个任务建议统一包含：

- **背景**（为什么做）
- **范围**（改哪些文件）
- **非范围**（明确不做的点）
- **验收**（产物字段 + 测试）
- **回滚方案**（配置开关或兼容分支）

这样可以避免“功能做了但不可落地/不可复算”。
