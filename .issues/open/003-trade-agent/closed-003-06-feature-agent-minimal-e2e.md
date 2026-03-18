---
title: "003-06-feature-agent-minimal-e2e"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: medium
type: feature
---

# Trade Agent P1-1：最小研究型 E2E（通过 `openclaw` 调用 `TradeCat` 能力桥）

## 背景

父任务 `#003` 最终要证明的不是“某个后端接口能回东西”，而是：

- 用户能在右侧原生 `openclaw tui` 中发起研究问题
- `openclaw` 能调用 `TradeCat` 暴露的研究能力
- 返回结果里带有来源和时间信息
- 左侧 `TradeCat` 保持原生界面，只作为并排工作台的一部分

因此，最小 E2E 必须沿着最终产品路径去验证，也就是：

- 左侧 `TradeCat`
- 右侧 `openclaw tui`
- 中间通过 `TradeCat` 能力桥和 skill 衔接

而不是再去验证一个已被放弃的“TradeCat 内嵌 Agent 面板”。

## 目标

构建一组最小研究型冒烟样本，验证“右侧 Agent 调左侧能力”的链路已经真实可用。

## 本期范围

1. 定义最小任务样本：quotes / signals / news
2. 通过右侧原生 `openclaw tui` 跑通这些样本
3. 记录是否触发 `TradeCat` 能力、是否有来源标注、是否有时间信息
4. 视进展补一条 `backtest summary` 样本
5. 输出一份简洁 E2E 结果表

## 建议结果字段

每条样本至少记录：

- 问题
- session / model
- 调用的 `TradeCat` 能力
- 是否成功返回
- 是否触发工具
- 来源或摘要
- 时间戳
- 失败原因（如果失败）

## 非目标

- 不做自动交易执行
- 不扩展到复杂多轮交易决策
- 不做 benchmark 或压测
- 不验证 `TradeCat` 自绘 Agent 面板

## 验收标准

- [x] quotes / signals / news 三类样本均至少跑通一轮或给出 blocker
- [x] 验证路径是右侧原生 `openclaw tui` + `TradeCat` 能力桥
- [x] 每条样本都能说明调用了哪个 `TradeCat` 能力
- [x] 输出包含结果、来源、时间戳或失败原因
- [x] 形成一份可复现实验记录

## Sym 派单备注

适合直接同步到 Linear / Symphony，但要明确这是一张“最小真实链路验证单”，不是泛泛的产品说明文档。

### Sym 任务边界

- 必须沿最终产品路径验证：
  - 左侧原生 `TradeCat TUI`
  - 右侧原生 `openclaw tui`
  - 中间通过 `TradeCat` 能力桥
- 只验证第一批四个能力里的研究闭环：
  - `tradecat_get_quotes`
  - `tradecat_get_signals`
  - `tradecat_get_news`
  - `tradecat_get_backtest_summary` 可作为加分项，不是前置
- 不要退回到旧的 `TradeCat` 自绘 `Agent Shell` 路线

### 建议最小样本

- quotes：
  - 示例问题：`给我看 NVDA 最新价格`
  - 期望能力：`tradecat_get_quotes`
- signals：
  - 示例问题：`看一下 BTCUSDT 最近 1m 信号`
  - 期望能力：`tradecat_get_signals`
- news：
  - 示例问题：`看一下 BTCUSDT 最近 2 小时相关新闻`
  - 期望能力：`tradecat_get_news`
- optional：
  - 示例问题：`读取最近一次 BTC/ETH 回测摘要`
  - 期望能力：`tradecat_get_backtest_summary`

### 建议产物

- 一份 E2E 结果表
- 每条样本记录：
  - 问题
  - session / model
  - 期望调用的 TradeCat 能力
  - 实际是否触发
  - 返回是否成功
  - 来源 / 时间戳是否存在
  - 失败原因或 blocker
- 一份简短结论：哪些链路已通、哪些仍被阻塞

### 明确约束

- 优先使用 `#003-02-08` 的 runbook / skill 草案
- 不修改 `openclaw` 上游源码
- 不改 `TradeCat` 左侧 UI
- 不把“人工直接运行脚本得到结果”冒充为“右侧 Agent 已成功调用”
- 如果右侧无法真实触发能力桥，要明确写 blocker，不要伪造通过

### 完成判定

- quotes / signals / news 至少三类样本均有一次真实验证结果
- 即使失败，也要把失败位置定位到“skill 配置 / 命令发现 / 数据返回 / session 行为”中的某一层
- 形成一份人工可复查的最小实验记录，即可关闭

## 相关 Issue

- Parent: `#003`
- Depends on: `#003-01`, `#003-02`
- Related: `#003-03`, `#003-05`
- Related: `#004`

## 关闭边界

- 三类核心研究样本完成一次最小冒烟即可关闭
- 若某类样本受工具缺失或数据源限制阻塞，也必须给出 blocker 说明后收口

## 进展记录

### 2026-03-09

- [x] 已对齐：E2E 的验收对象是“真实产品路径”，不是单独的后端接口

### 2026-03-11

- [x] 已重新对齐：最终路径改为“原生 `openclaw tui` 调用 `TradeCat` 能力桥”
- [x] 已明确：左侧 `TradeCat` 不承担 Agent UI，只提供并排工作与研究上下文
- [ ] 待开始：确定 quotes / signals / news 三类最小样本问题
- [ ] 待开始：定义 E2E 结果表模板与成功判定标准

### 2026-03-12

- [x] 已补充：可直接用于 Linear / Symphony 派单的执行备注
- [x] 已明确：本单是最小真实链路验证，不接受“只跑本地脚本”式伪闭环
- [x] 已确认：`#003-02-08` runbook 已落回主仓：`docs/learn/openclaw_tradecat_skill_runbook.md`
- [x] 已确认：当前主仓具备 E2E 预检条件：
  - `repository/openclaw/` 已落地
  - 四个 `tradecat_get_*` 桥接脚本都存在
  - `signal_history.db` 与 `artifacts/backtest/` 都存在
- [x] 已确认：旧的 Symphony review 结论基于过时 workspace，不能作为本单关闭依据
- [x] 已决定：拒绝旧一轮 `TRA-19` 产物，不合入主仓
- [x] 拒绝原因已明确：
  - 基于过时 workspace，错误判断 `openclaw` / runbook / 桥接脚本 / 数据前置均缺失
  - 更新了旧 issue 路径，不是当前 `.issues/open/003-trade-agent/` 主路径
  - 附带的 `minimal_e2e_audit.py` 搜索范围与 backtest 判定逻辑不足，当前会稳定误报
- [x] 已在当前主仓通过右侧原生 `openclaw tui --session tc-00306-e2e` 完成最小 E2E
- [x] 已确认当前 session 已加载 workspace skill `tradecat-bridge`
- [x] 已完成三条核心样本：
  - quotes：`NVDA 最新价格`
  - signals：`BTCUSDT 最近 1m 信号`
  - news：`BTCUSDT 最近 2 小时相关新闻`

## 2026-03-12 实测结果表

执行环境：

- 左侧：原生 `TradeCat TUI`
- 右侧：原生 `openclaw tui`
- session：`tc-00306-e2e`
- model：`openai/qwen3-coder-plus`
- skill：`tradecat-bridge`（`openclaw skills info tradecat-bridge` = `Ready`）

| 样本 | 问题 | 期望能力 | 实际触发 | 是否成功 | 来源 | 时间戳 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| quotes | `给我看 NVDA 最新价格` | `tradecat_get_quotes` | `read ~/.openclaw/workspace/skills/tradecat-bridge/SKILL.md` -> `exec scripts/tradecat_get_quotes.py NVDA --market us_stock` | 成功 | Tencent / `services-preview/tui-service/src/quote.py` | `2026-03-12T07:46:09Z` | 通过 |
| signals | `看一下 BTCUSDT 最近 1m 信号` | `tradecat_get_signals` | `exec scripts/tradecat_get_signals.py --symbol BTCUSDT --timeframe 1m --limit 5` | 成功 | SQLite `signal_history.db` | `2026-03-12T07:47:21.786495Z` | 通过 |
| news | `看一下 BTCUSDT 最近 2 小时相关新闻` | `tradecat_get_news` | `exec scripts/tradecat_get_news.py --symbol BTCUSDT --since-minutes 120 --limit 5` | 成功（空结果） | PostgreSQL `alternative.news_articles` | `2026-03-12T07:48:41.646082Z` | 通过；空结果被正确表述为“无匹配新闻”，未误报失败 |

人工复查证据：

- TUI pane：`tmux capture-pane -pt tradecat-workbench:main.0`
- session store：`/root/.openclaw/agents/main/sessions/sessions.json`
- session jsonl：`/root/.openclaw/agents/main/sessions/cf062f41-5782-4c56-ad95-97a29cf44049.jsonl`

关键证据摘要：

- quotes 样本在 jsonl 中记录了：
  - `toolCall read -> ~/.openclaw/workspace/skills/tradecat-bridge/SKILL.md`
  - `toolCall exec -> scripts/tradecat_get_quotes.py`
  - tool result 返回稳定 JSON，assistant 最终回复包含 `tool / source / timestamp`
- signals 样本在 jsonl 中记录了：
  - `toolCall exec -> scripts/tradecat_get_signals.py`
  - tool result 返回 SQLite `signal_history.db` 数据
- news 样本在 jsonl 中记录了：
  - `toolCall exec -> scripts/tradecat_get_news.py`
  - tool result 为 `ok=true` + `data=[]`
  - assistant 正确表述为空结果，不是系统错误

## 当前结论

本单已经完成最小真实链路验证：右侧原生 `openclaw tui` 能通过 workspace skill 调用 `TradeCat` 能力桥，并在 quotes / signals / news 三类样本上返回可复查结果。因此本单关闭。
