# TradeCat 回测实施方案（Crypto，分两步）

## 1. 目标与原则

本方案用于将 TradeCat 从“实时信号展示”推进到“可用于实盘前验证的回测系统”。

核心原则：

- 结果可信优先（避免未来函数 > 速度 > 展示）
- 与现有服务边界保持一致（不破坏 data/trading/signal/tui 职责）
- 先做可落地闭环，再做完整规则重放
- 不修改生产 `config/.env`、不改数据库 schema、不写敏感历史库

---

## 2. 已确认范围（冻结）

### 2.1 首版范围（Phase A）

- 市场：Crypto
- 标的：main4（BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT）
- 主周期：1m
- 默认区间：最近90天
- 执行口径：信号在 bar 收盘确认，下一根 bar 开盘成交
- 交易模型：固定杠杆 + 维持保证金 + 强平
- 成本模型：交易所默认手续费档位（YAML 可覆盖）+ 滑点
- 聚合方式：按 `strength` 直接打分累计（BUY/SELL 净分）
- 验证方式：Walk-Forward（滚动窗口）
- 入口形态：先做 TUI backtest「TR切片（只读占位）」；再接 CLI + 报告产出
- 结果保留：latest + 最近30次运行

### 2.2 二阶段范围（Phase B）

- 在不影响 Phase A 的基础上，补“129条规则离线重放”
- 离线信号流接入同一执行层进行回测
- 输出“历史信号回测 vs 离线规则回测”对比

---

## 3. 分阶段方案

## Phase A：执行层回测（先做）

### 3.1 输入数据

- `libs/database/services/signal-service/signal_history.db`（只读）
- TimescaleDB `market_data.candles_1m`（只读）

### 3.2 回测流程

1. 读取历史信号（按 symbol/timeframe/time range 过滤）
2. 对齐行情（按 symbol + timestamp 对齐到 bar）
3. 信号聚合（strength 累加）：
   - `buy_score = Σ BUY.strength`
   - `sell_score = Σ SELL.strength`
   - `net_score = buy_score - sell_score`
4. 动作判定：
   - `net_score >= long_open_threshold` => 开多/加多
   - `net_score <= -short_open_threshold` => 开空/加空
   - 落在中间区间 => 持有/减仓（按配置）
5. 成交规则：
   - 使用下一根 K 线 `open` 成交
   - 成交价叠加滑点
   - 扣手续费
6. 风控：
   - 固定杠杆
   - 维持保证金检查
   - 触发强平时按保守价格处理
7. 输出结果：
   - 交易明细、权益曲线、指标统计、Markdown报告

### 3.3 输出产物

- `artifacts/backtest/<run_id>/trades.csv`
- `artifacts/backtest/<run_id>/equity_curve.csv`
- `artifacts/backtest/<run_id>/metrics.json`
- `artifacts/backtest/<run_id>/report.md`
- `artifacts/backtest/latest` 指向最新 run
- 自动清理旧结果，仅保留最近 30 次 + latest

---

## Phase B：129规则离线重放（后做）

### 3.4 输入数据

- PG `market_data.candles_1m`（只读）
- SQLite 指标库（只读，用于规则表字段）

### 3.5 离线重放流程

1. 复用 `services/signal-service/src/rules/` 规则定义
2. 按 `(symbol, timeframe, 数据时间)` 顺序构造 `prev/curr`
3. 调用 `rule.check_condition(prev, curr)` 生成离线信号
4. 将离线信号写到独立产物（CSV/Parquet），不写线上 `signal_history.db`
5. 接入 Phase A 执行层，复用同一成交/风控/统计逻辑

### 3.6 对比输出

- online_history 回测结果
- offline_129 回测结果
- 差异报告（收益、回撤、交易数、胜率、分symbol贡献）

---

## 4. 模块与目录设计

建议新增：

- `services/signal-service/src/backtest/`
  - `models.py`：BacktestConfig / SignalEvent / Bar / Trade / Position / Metrics
  - `config_loader.py`：YAML加载与校验
  - `data_loader.py`：读取signal_history与candles
  - `aggregator.py`：方向打分聚合
  - `execution_engine.py`：成交与仓位/杠杆/强平
  - `walkforward.py`：滚动窗口评估
  - `reporter.py`：CSV/JSON/Markdown输出
  - `retention.py`：结果保留（最近30次）
  - `rule_replay.py`（Phase B）
  - `runner.py`：总编排
- `services/signal-service/src/backtest/__main__.py`
- `services/signal-service/scripts/backtest.sh`
- 根目录可选脚本：`scripts/backtest.sh`（转发）

---

## 5. 配置与接口

### 5.1 CLI（首版）

```bash
cd services/signal-service
python -m src.backtest --config src/backtest/strategies/default.crypto.yaml
# 或在仓库根目录：
./scripts/backtest.sh
```

可选参数：

- `--start`, `--end`
- `--symbols`
- `--run-id`
- `--mode` (`history_signal` / `offline_replay` / `offline_rule_replay` / `compare_history_rule`)
- `--fee-bps`, `--slippage-bps`
- `--initial-equity`, `--leverage`, `--position-size-pct`
- `--long-threshold`, `--short-threshold`, `--close-threshold`
- `--walk-forward`, `--walk-forward-max-folds`
- `--wf-train-days`, `--wf-test-days`, `--wf-step-days`
- `--walk-forward-auto-fallback` / `--no-walk-forward-auto-fallback`

### 5.2 YAML（默认模板）

`services/signal-service/src/backtest/strategies/default.crypto.yaml`

推荐低频模板：`src/backtest/strategies/default.crypto.btc_eth.safe.yaml`（BTC/ETH, 200/200 阈值）

建议字段：

- `market: crypto`
- `symbols: [BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT]`
- `timeframe: 1m`
- `date_range: { start, end }`
- `execution: { entry: next_open, slippage_bps, fee_bps }`
- `risk: { leverage, maintenance_margin_ratio, liquidation_buffer }`
- `aggregation: { long_open_threshold, short_open_threshold }`
- `walk_forward: { train_days, test_days, step_days }`
- `retention: { keep_runs: 30 }`

---

## 6. 验收标准

### 6.1 可信性（必须）

- 无未来函数：任何下单决策不得读取未来 bar
- 成交可追溯：每笔交易有可复算的入场/出场/费用
- 强平可解释：触发条件与价格记录完整
- 缺失数据可解释：丢弃信号数、缺bar数有统计

### 6.2 结果指标（最小集）

- 总收益率
- 年化收益率（可选）
- 最大回撤
- Sharpe（简化版）
- 交易次数 / 胜率 / 盈亏比
- 平均持仓时长
- 每个symbol贡献
- 基准对比（等权 Buy&Hold 收益、策略超额收益）

### 6.3 稳定性

- 同输入重复运行结果一致
- 最近90天 main4 可在可接受时间内跑完
- 输出目录结构稳定，便于TUI读取

---

## 7. TUI 接入（首版只读）

按两段推进：

- M0：先做 `backtest` 视图壳（TR切片），可读取 latest，没数据时给出清晰占位提示
- M3：回测服务产出稳定后，完善指标区/交易明细区/曲线展示

在 `tui-service` 新增 `backtest` 视图：

- 读取 `artifacts/backtest/latest/metrics.json`
- 读取 `equity_curve.csv` 展示简版曲线
- 展示：
  - 收益 / 回撤 / Sharpe
  - 胜率 / 交易数
  - 最近N笔交易
  - 当前run_id和回测区间

说明：

- TUI 不负责执行回测，仅负责展示最近结果
- 回测执行仍由 CLI 触发，避免耦合在线服务

### 7.1 TUI 布局冻结（可视化优先 v3）

- 布局比例：采用 **55/45**（左图右表），借鉴 FreqUI/QuantConnect 的“图表+指标卡”阅读路径。
- 左侧：`权益曲线` 使用连续折线（非面积块），保留价格刻度与参考横线，避免“整屏色块”误读。
- 左侧底部：新增 `回撤带`（深度越高表示回撤越深）+ `净值/最高/最低/变化/均值` 摘要，先给人类一眼结论。
- 右侧上段：`核心指标`（状态、收益率、最大回撤、夏普、胜率、交易数、平均持仓）。
- 右侧中段：`风险解读`（当前回撤/最大回撤 + 一句话解释），帮助非量化用户快速判断。
- 右侧下段：`币种贡献`（红盈绿亏）与 `最近平仓`（紧凑格式）分区展示，降低信息混杂。

> 说明：本阶段仍以“可视性与可解释性”为第一目标；回测精度（成交细节、滑点模型、规则重放）后续再优化。

### 7.2 回测状态接入（M1.1）

- 新增状态文件：`artifacts/backtest/run_state.json`（阶段级状态）。
- 状态字段：`status/stage/run_id/mode/started_at/updated_at/finished_at/latest_run_id/message/error`。
- runner 在关键阶段写状态：
  - `history_signal`: `loading_signals -> loading_candles -> executing -> writing -> retention -> done`
  - `offline_replay`: `loading_candles -> replaying_signals -> executing -> writing -> retention -> done`
  - `offline_rule_replay`: `loading_indicator_tables -> loading_candles -> executing -> writing -> retention -> done`
  - `compare_history_rule`: `compare_modes -> (history + rule replay) -> done`
- 异常时写 `status=error` 与 `error` 摘要，便于 TUI 与 CLI 快速定位问题。
- TUI 回测页读取状态并展示“运行中/完成/异常”，但仍不直接执行回测。

### 7.3 回测前覆盖率检查（check-only）

- CLI 新增 `--check-only`：只做输入数据覆盖率检查，不执行回测。
- 推荐命令：`./scripts/backtest.sh --check-only --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00"`
- 输出内容：
  - 回测窗口（start/end/timeframe/symbols）
  - signal_history 覆盖（行数、天数、时间范围、分symbol）
  - candles_1m 覆盖（行数、预期行数、覆盖率、时间范围、分symbol）

### 7.4 覆盖补齐路径（offline_replay）

- 当 `history_signal` 的信号历史覆盖不足时，可直接使用 `--mode offline_replay`。
- `offline_replay` 仅依赖 PG 的 `candles_1m`，按 K 线生成可复现的离线信号流（不写入线上 `signal_history.db`）。
- 推荐命令：
  - `./scripts/backtest.sh --mode offline_replay --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00"`

### 7.5 SQLite 129规则离线重放（offline_rule_replay）

- `offline_rule_replay` 会读取 `market_data.db` 指标表历史行，并按 `src/rules` 全量规则回放生成信号流。
- 当回测配置指定 `timeframe=1m` 时，会对“默认规则周期(1h/4h/1d)”自动做 1m 对齐，减少与历史信号口径偏差。
- 该模式不依赖 `signal_history` 覆盖，适合做 Phase B 的规则口径验证。
- 推荐命令：
  - `./scripts/backtest.sh --mode offline_rule_replay --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00"`

### 7.6 历史信号 vs 规则重放对比（compare_history_rule）

- 对比模式会依次运行：
  - `history_signal`
  - `offline_rule_replay`
- 输出对比产物：`artifacts/backtest/<run_id>-compare/comparison.json` 与 `comparison.md`。
- 对比产物新增信号剖面：`signal_type_delta_top`、`direction_delta`、`timeframe_delta_top`，用于定位两种模式命中差异。
- 对比产物新增规则对齐指标：`rule_overlap`、`missing_history_rules_top`、`new_rule_types_top`、`history_timeframe_profile/rule_timeframe_profile`。
- 规则重放会额外产出 `rule_replay_diagnostics.json`，对比报告可附带 `missing_history_rules_diagnostics`（规则未命中原因）。
- 诊断中新增 `rule_timeframe_profiles`（规则配置周期/数据可见周期/重叠周期），当无周期重叠时会标记 `primary_block_reason=timeframe_no_data`。
- 推荐命令：
  - `./scripts/backtest.sh --mode compare_history_rule --symbols BTCUSDT,ETHUSDT --start "2026-01-14 00:00:00" --end "2026-02-13 00:00:00"`
- 该模式默认不受 `--min-signal-days/--min-signal-count` 门槛约束，避免对比流程被历史覆盖不足阻断。
- TUI 回测页会在右侧新增“模式对比（history vs rule）”卡片，显示收益差/信号差与命中差异 Top。

### 7.7 覆盖率门槛防呆（硬门槛）

- 回测前默认执行门槛校验（可配置）：
  - `--min-signal-days`（默认 7，仅 `history_signal` 生效）
  - `--min-signal-count`（默认 200，仅 `history_signal` 生效）
  - `--min-candle-coverage-pct`（默认 95）
- 门槛不达标默认直接失败，避免“数据缺口导致的伪结果”。
- 需要强制运行时可显式加 `--force`（会打印 warning）。

---

## 8. 风险与规避

- 风险：`signal_history` 历史覆盖不足
  - 规避：输出覆盖率统计；Phase B 补离线重放
- 风险：1m/90天数据量导致运行时间较长
  - 规避：先单symbol压测，优化后扩main4
- 风险：强平模型过于理想化
  - 规避：采用保守成交假设与安全buffer
- 风险：规则口径线上线下不一致
  - 规避：Phase B 直接复用 `rules` 目录定义

---

## 9. 实施里程碑

### M0（TUI TR切片，先做）
- `tui-service` 新增 `backtest` 只读页壳
- 读取 `artifacts/backtest/latest/*`；无数据时显示占位与下一步提示

### M1（Phase A骨架）
- 回测runner + 配置加载 + 结果落盘

### M2（Phase A完成）
- 成交/费用/杠杆/强平 + Walk-Forward + 报告
- 已完成：Walk-Forward CLI 骨架与摘要产物（`walk_forward_summary.json` / `walk_forward_folds.csv`）
- 已完成：history 模式分折覆盖不足时自动 fallback 到 `offline_replay`（可关闭）
- 已完成：Walk-Forward 产物记录 `signal_count/signal_days/fallback_reason`，便于解释“为何切换回放”

### M3（展示完善）
- TUI backtest 页对齐真实产物（指标区/曲线/最近交易）

### M4（Phase B）
- 已推进：`offline_rule_replay`（SQLite 129规则回放）与 `compare_history_rule`（对比报告）
- 待完善：规则重放与线上 PG 引擎口径逐条对齐（精度优化）

---

## 10. 当前结论

我们已完成“可实施级”方案对齐。  
当前进入 M4 精度优化阶段：先保证可解释与可对比，再逐步收敛到线上口径一致。
