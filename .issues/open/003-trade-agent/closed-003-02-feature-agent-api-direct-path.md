---
title: "003-02-feature-agent-api-direct-path"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent P0-1：TradeCat 能力桥 / 只读工具主链路

## 背景

父任务 `#003` 的主线已经进一步收敛，不再是“TradeCat 自绘 Agent 面板 + 直连 openclaw session API”，而是：

- 左侧保留原生 `TradeCat TUI`
- 右侧保留原生 `openclaw tui`
- 集成只发生在能力层：`TradeCat` 暴露稳定的只读能力给 `openclaw`

因此，这里的“主链路”也随之变化：

- 不再以 `TradeCat -> openclaw Gateway/session API` 作为当前主交付
- 改为在 `TradeCat` 仓库里提供一组稳定、只读、可脚本化的研究能力
- 再由 `openclaw` 通过 skill / tool 调用这些能力

核心原则是：**读 TradeCat 的数据和脚本，不读 TradeCat 的终端画面。**

## 目标

实现一个最小 `TradeCat` 能力桥，让 `openclaw` 能稳定调用 `TradeCat` 的研究型只读能力。

## 本期范围

1. 定义 `TradeCat` 对外暴露的最小只读能力接口
2. 第一批优先覆盖：
   - `tradecat_get_quotes`
   - `tradecat_get_signals`
   - `tradecat_get_news`
   - `tradecat_get_backtest_summary`
3. 输出统一 JSON 结构、来源标注、时间戳、错误语义
4. 桥接层直接读取现有数据源 / 服务 / 脚本，不解析终端文本
5. 提供一个可被 `openclaw` skill 调用的本地入口（脚本、命令或等价桥接方式）
6. `tradecat_get_current_focus` 仅作为增强项预留，不是本期前置

## 建议接口语义

建议在 `TradeCat` 侧先收敛为稳定命令，再交给 `openclaw` skill 使用：

- `tradecat_get_quotes(symbols, market=None)`
- `tradecat_get_signals(symbols=None, timeframe=None, limit=None)`
- `tradecat_get_news(symbols=None, query=None, limit=None, since_minutes=None)`
- `tradecat_get_backtest_summary(strategy=None, symbols=None, run_id=None)`

每个能力都应满足：

- 入参清晰
- 输出 JSON 稳定
- 明确 `source` / `ts` / `error`
- 可独立本地 smoke

这样可以减少 `openclaw` 或上游 skill 配置变化直接冲击 `TradeCat` 数据层。

## 拆分策略（适合 Sym 的部分）

`#003-02` 仍是核心实现 issue，不建议整单直接丢给 Sym。更合适的做法是：

- 主仓直做：桥接脚本、命令入口、数据读取与 JSON 输出
- 派给 Sym：接口梳理、JSON 契约、skill 提示词草案、smoke case 这类边界清晰的支撑任务

### 建议派给 Sym 的支撑子任务

- `#003-02-01 ~ #003-02-03` 现有材料继续作为 openclaw upstream / fallback 参考
- 2026-03-11 起，能力桥主线已继续拆成更适合并行推进的子单：
  - `#003-02-04`：`tradecat_get_quotes`
  - `#003-02-05`：`tradecat_get_signals`
  - `#003-02-06`：`tradecat_get_news`
  - `#003-02-07`：`tradecat_get_backtest_summary`
  - `#003-02-08`：`openclaw` skill / runbook
- 这几单都应坚持：
  - 不解析 `TradeCat` 终端文本
  - 只读现有数据源 / 服务 / 脚本
  - 输出稳定 JSON，并把 blocker 写清楚

### 不建议直接派给 Sym 的部分

- 桥接脚本真正落到仓库中的核心代码
- 对接现有数据源、DB、脚本时的工程取舍
- 最终的命令行接口拍板与错误语义收口

## 非目标

- 不在 `TradeCat` 内继续做 Agent UI
- 不修改 `repository/openclaw` 内部 TUI 代码
- 不让 `openclaw` 解析 `TradeCat` 终端文本
- 不在本期做可写操作或自动交易
- 不做完整 E2E

## 验收标准

- [x] `TradeCat` 已暴露至少 3 个核心只读能力：quotes / signals / news
- [x] 每个能力都能本地调用并返回稳定 JSON
- [x] 输出包含来源、时间戳和明确错误语义
- [x] `openclaw` skill 有可复现的调用入口
- [x] 有一份最小 smoke test 步骤可复现
- [x] `tradecat_get_backtest_summary` 至少有接口预留或最小实现

## 相关文件

- `scripts/`
- `services-preview/`
- `services/`
- `repository/openclaw/docs/`

## 相关 Issue

- Parent: `#003`
- Blocks: `#003-06`
- Related: `#003-03`, `#004`
- Reference: `#003-02-01`, `#003-02-02`, `#003-02-03`
- Children: `#003-02-04`, `#003-02-05`, `#003-02-06`, `#003-02-07`, `#003-02-08`

## 关闭边界

- 核心只读能力桥最小可用即可关闭
- 若某一项能力暂时取不到数据，也要输出 blocker、缺口清单和降级方案后收口

## 当前结论

- 主仓已落地 4 个只读桥接命令：
  - `scripts/tradecat_get_quotes.py`
  - `scripts/tradecat_get_signals.py`
  - `scripts/tradecat_get_news.py`
  - `scripts/tradecat_get_backtest_summary.py`
- 这些命令已统一输出 `ok / tool / ts / source / error / data` 结构，并保留本地数据来源信息
- `skills/tradecat-bridge/SKILL.md.template`、`scripts/install_openclaw_tradecat_skill.sh`、`docs/learn/openclaw_tradecat_skill_runbook.md` 已形成可复现的 `openclaw` skill 接入路径
- `#003-06` 已在原生 `openclaw tui` 下用 TradeCat bridge 跑通 quotes / signals / news 的最小研究型 E2E
- 本单关闭

## 进展记录

### 2026-03-09

- [x] 已对齐：不做 UI 级融合，集成发生在能力层
- [x] 已对齐：右侧保持原生 `openclaw tui`
- [x] 已拆分出适合 Sym 派单的支撑任务：`#003-02-01 ~ #003-02-03`

### 2026-03-11

- [x] 已重新定义：本单主线从 `OpenClawAdapter / Gateway` 改为 `TradeCat` 只读能力桥
- [x] 已明确：第一批能力是 quotes / signals / news / backtest summary
- [x] 已明确：`#003-02-01 ~ #003-02-03` 保留为参考资料，不再直接定义当前主实现
- [x] 已拆分并行子单：`#003-02-04 ~ #003-02-08`
- [x] 已派单到 Linear / Symphony：`TRA-14`（quotes）、`TRA-15`（signals）、`TRA-16`（news）
- [x] 已补派单到 Linear / Symphony：`TRA-17`（backtest summary）
- [x] 已确定：桥接脚本以 `scripts/tradecat_get_*.py` 形式落在主仓
- [x] 已确定：桥接命令统一输出 JSON，并收口来源、时间戳与错误语义
- [x] 已补：openclaw skill 接入说明与最小 smoke 路径

### 2026-03-12

- [x] `#003-02-04 ~ #003-02-07` 已全部落回主仓，并保留本地 CLI 入口
- [x] `#003-02-08` 已补齐 skill 模板、安装脚本与 runbook
- [x] 已验证：`openclaw skills info tradecat-bridge` 返回 `Ready`
- [x] 已验证：`#003-06` 使用 TradeCat bridge 成功完成最小研究型 E2E
