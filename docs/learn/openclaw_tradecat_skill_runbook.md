# openclaw 调用 TradeCat 能力桥 runbook

适用范围：

- Local issue `#003-02-08`
- Linear `TRA-18`
- 后续最小研究型 E2E：`TRA-19`

目标不是重做 `openclaw` 或重做 `TradeCat` 能力桥，而是给出一份能直接复用的最小 runbook，明确：

- `quotes / signals / news / backtest summary` 什么时候调用
- `openclaw` 侧优先怎么接
- 成功、空结果、超时、失败分别怎么判断
- 当前主仓里哪些前置条件已经满足

## 1. 当前主仓状态

截至 `2026-03-12`，当前主仓已确认：

1. `repository/openclaw/` 已落地，不再是空 submodule。
2. 四个只读桥接命令都已存在：
   - `scripts/tradecat_get_quotes.py`
   - `scripts/tradecat_get_signals.py`
   - `scripts/tradecat_get_news.py`
   - `scripts/tradecat_get_backtest_summary.py`
3. 直接命令 smoke 依赖已具备：
   - `libs/database/services/signal-service/signal_history.db`
   - `artifacts/backtest/`

这意味着：

- `TRA-18` 不再停留在“文档 blocker”阶段，可以把 runbook 作为当前主仓资产保留
- `TRA-19` 可以基于当前主仓重新做真实 E2E，不应继续沿用过时 workspace 的 blocker 结论

## 2. openclaw 接入顺序

在不修改 `repository/openclaw` 上游 UI 的前提下，推荐顺序：

| 方式 | 是否推荐 | 适用场景 | 当前建议 |
| --- | --- | --- | --- |
| workspace skill | 推荐优先 | 只在当前 TradeCat workspace 使用，随仓库一起评审 | 第一优先级 |
| managed skill | 次选 | 多个 workspace 需要复用同一份能力定义 | 等第一版跑顺后再考虑 |
| runbook only | fallback | skill 入口还未完全定型时，先用人工 smoke 跑通流程 | 当前已提供 |

结论：

1. 先用本文档固定桥接命令和判定语义。
2. 再把本文第 4 节的草案翻译成 `openclaw` 的实际 workspace skill。
3. `TRA-19` 只验证真实调用链路，不回退到旧的 `TradeCat` 自绘 Agent 面板路线。

当前主仓已补充一个可复现安装脚本：

```bash
./scripts/install_openclaw_tradecat_skill.sh
```

默认会把实际可加载的 workspace skill 写到：

```text
~/.openclaw/workspace/skills/tradecat-bridge/SKILL.md
```

脚本会把当前仓库根目录渲染进 skill 内，避免 `openclaw` workspace 不在本仓根目录时找不到桥接命令。

## 3. 四个能力的最小用途

| 能力 | 作用 | 数据来源 | 是否只读 |
| --- | --- | --- | --- |
| `tradecat_get_quotes` | 读取最新行情快照 | `services-preview/tui-service/src/quote.py` | 是 |
| `tradecat_get_signals` | 读取最近规则信号 | `signal_history.db` | 是 |
| `tradecat_get_news` | 读取最近新闻 | `alternative.news_articles` | 是 |
| `tradecat_get_backtest_summary` | 读取已有回测摘要 | `artifacts/backtest/` | 是 |

约束：

- 只做查询，不触发交易动作
- 不让 `openclaw` 去解析左侧终端画面
- 不在 skill 内重跑回测
- 不把“空结果”误判成系统失败

## 4. 最小 skill 草案

下面不是 `openclaw` 的最终配置格式，而是等价草案。真正接到 workspace skill 时，只需要把字段名翻译成 `openclaw` 的实际 schema。

```yaml
name: tradecat-bridge
scope: workspace
description: Read-only TradeCat bridge for quotes, signals, news, and backtest summaries.

instructions: |
  Use TradeCat bridge commands only when the user asks for fresh TradeCat data:
  latest quotes, recent signals, recent news, or a saved backtest summary.
  Prefer one bridge command per turn unless the user explicitly asks for a combined view.
  If symbol, timeframe, or time window is missing and required, ask one short follow-up question.
  Never invent data when the command fails, times out, or returns empty.
  Always include tool name, source, and timestamp in the final answer.

commands:
  - id: tradecat_get_quotes
    when: latest price, latest quote, market snapshot, multi-symbol comparison
    command_template: python scripts/tradecat_get_quotes.py [symbols ...] [--symbols <csv>] [--market <market>] [--provider <provider>] [--timeout <seconds>]

  - id: tradecat_get_signals
    when: recent rule signals, latest signal for a symbol, recent 1m/5m/15m signal check
    command_template: python scripts/tradecat_get_signals.py --symbol <symbol> [--timeframe <timeframe>] [--limit <n>]

  - id: tradecat_get_news
    when: recent news by symbol, recent news by query, market-moving news in a time window
    command_template: python scripts/tradecat_get_news.py [--symbol <symbol>] [--query <query>] [--limit <n>] [--since-minutes <n>] [--timeout <seconds>]

  - id: tradecat_get_backtest_summary
    when: read an existing backtest result, not when the user asks to run a new backtest
    command_template: python scripts/tradecat_get_backtest_summary.py --run-id <run_id> [--strategy <text>] [--symbols <csv>]
```

## 5. 问题到能力的映射

| 用户问题 | 应触发能力 | 最小调用模板 | 说明 |
| --- | --- | --- | --- |
| `给我看 NVDA 最新价格` | `tradecat_get_quotes` | `python scripts/tradecat_get_quotes.py NVDA --market us_stock` | 已在主仓直接 smoke 成功 |
| `给我看 BTCUSDT 和 ETHUSDT 最新价格` | `tradecat_get_quotes` | `python scripts/tradecat_get_quotes.py BTCUSDT ETHUSDT --market crypto_spot` | 多 symbol 直接走 positional args |
| `看一下 BTCUSDT 最近 1m 信号` | `tradecat_get_signals` | `python scripts/tradecat_get_signals.py --symbol BTCUSDT --timeframe 1m --limit 5` | 已在主仓直接 smoke 成功 |
| `看一下 NVDA 最近信号` | `tradecat_get_signals` | `python scripts/tradecat_get_signals.py --symbol NVDA --limit 5` | 未指定 timeframe 时可依脚本默认或追问一次 |
| `看一下 BTCUSDT 最近 2 小时相关新闻` | `tradecat_get_news` | `python scripts/tradecat_get_news.py --symbol BTCUSDT --since-minutes 120 --limit 5` | 当前主仓该查询返回 `ok=true` + `data=[]`，这是空结果不是失败 |
| `搜一下英伟达最近新闻` | `tradecat_get_news` | `python scripts/tradecat_get_news.py --query NVDA --limit 5 --since-minutes 240` | 适合非标准 symbol 口径 |
| `读取最近一次 BTC/ETH 回测摘要` | `tradecat_get_backtest_summary` | `python scripts/tradecat_get_backtest_summary.py --run-id fixdb-risk-L1.5-P025-151147 --symbols BTCUSDT,ETHUSDT` | `--run-id latest` 不保证可用，建议给明确 run_id |
| `帮我解释为什么 BTC 最近有信号` | 先 `tradecat_get_signals`，必要时再 `tradecat_get_quotes` 或 `tradecat_get_news` | 先单工具，用户明确要综合解释时再串联第二个工具 | 避免默认多工具并发 |

触发准则：

1. 问题要求“最新”“最近”“刚刚”“过去 N 分钟/小时”的数据时，优先调用 TradeCat 能力桥。
2. 问题是通用知识、规则解释、策略概念说明时，不默认调用。
3. 用户已经给出 symbol/timeframe/window 时，直接调工具，不重复确认。
4. 用户只说“看看最近信号/新闻”但没有对象时，先追问一次范围。

## 6. 成功、空结果、超时、失败判定

四个桥接命令都按统一 JSON 顶层契约返回：

- `ok`
- `tool`
- `ts`
- `source`
- `request`
- `data`
- `error`

`openclaw` 侧先按顶层契约判定，不依赖每个能力内部私有字段。

| 结果类型 | 判定方式 | openclaw 应如何表述 |
| --- | --- | --- |
| 成功 | 退出码 `0`，JSON 可解析，`ok=true`，且 `data` 非空 | 正常总结结果，并保留 `tool / source / ts` |
| 空结果 | 退出码 `0`，JSON 可解析，`ok=true`，但 `data=[]` 或空对象 | 明确说“TradeCat 没有返回匹配数据”，不要写成系统失败 |
| 超时 | 命令在推荐超时时间内未返回 | 明确说“TradeCat bridge 超时”，可建议缩小时间窗或标的后重试 |
| 失败 | 非零退出码，无法解析 JSON，或 JSON `ok=false` | 明确说“TradeCat bridge 失败”，带上 `error` 摘要 |
| bridge 未接好 | 命令不存在、不可执行，或 skill 无法发现命令 | 明确说“TradeCat bridge 尚未接入此 workspace” |

## 7. 推荐超时与重试

| 能力 | 推荐超时 | 重试建议 | 原因 |
| --- | --- | --- | --- |
| quotes | `5s` | 最多重试 1 次 | 快速行情查询，不应长时间阻塞 |
| signals | `8s` | 默认不重试 | 本地只读数据库，失败通常不是抖动问题 |
| news | `8s` | 最多重试 1 次 | 可能受数据库响应或筛选窗口影响 |
| backtest summary | `5s` | 默认不重试 | 只读本地产物，失败通常是 run_id 不匹配 |

统一规则：

1. 只对“超时”做最多一次重试。
2. 空结果不重试。
3. `ok=false` 或非零退出码不重试，直接报告失败。
4. 命令不存在时直接报告安装/接入阻塞。

## 8. 最小回答模板

成功：

```text
已调用 TradeCat 能力：tradecat_get_signals
来源：sqlite signal_history
时间：2026-03-11T17:20:49Z
结果：BTCUSDT 最近 1m 返回 3 条信号，最新一条方向为 BUY。
```

空结果：

```text
已调用 TradeCat 能力：tradecat_get_news
来源：alternative.news_articles
时间：2026-03-11T17:20:49Z
结果：TradeCat 没有返回 BTCUSDT 最近 120 分钟内的相关新闻。
```

失败：

```text
TradeCat bridge 调用失败：tradecat_get_backtest_summary
失败层级：artifact_not_found
说明：当前 run_id 没有匹配到已有回测产物，请改用明确 run_id 后重试。
```

## 9. 最小 smoke 步骤

### 9.1 预检

在仓库根目录执行：

```bash
test -n "$(find repository/openclaw -mindepth 1 -maxdepth 1 -print -quit)" && echo "openclaw:ok" || echo "openclaw:missing"
ls scripts/tradecat_get_quotes.py scripts/tradecat_get_signals.py scripts/tradecat_get_news.py scripts/tradecat_get_backtest_summary.py
test -f libs/database/services/signal-service/signal_history.db && echo "signals-db:ok" || echo "signals-db:missing"
test -d artifacts/backtest && echo "backtest-artifacts:ok" || echo "backtest-artifacts:missing"
```

当前主仓基线结果：

- `openclaw:ok`
- 四个脚本都存在
- `signals-db:ok`
- `backtest-artifacts:ok`

### 9.2 直接命令 smoke

建议先跑命令层，再跑右侧 `openclaw tui`：

```bash
python scripts/tradecat_get_quotes.py NVDA --market us_stock
python scripts/tradecat_get_signals.py --symbol BTCUSDT --timeframe 1m --limit 3
python scripts/tradecat_get_news.py --symbol BTCUSDT --since-minutes 120 --limit 3
python scripts/tradecat_get_backtest_summary.py --run-id fixdb-risk-L1.5-P025-151147
```

当前主仓已验证的最小结果：

- `quotes`: 成功，`ok=true`
- `signals`: 成功，`ok=true`
- `news`: 成功但空结果，`ok=true` + `data=[]`
- `backtest_summary`: 使用明确 `run_id` 成功；`--run-id latest` 不保证命中

### 9.3 openclaw 真实 E2E smoke

右侧 `openclaw tui` 至少验证以下三条问题：

1. `给我看 NVDA 最新价格`
2. `看一下 BTCUSDT 最近 1m 信号`
3. `看一下 BTCUSDT 最近 2 小时相关新闻`

可选第四条：

4. `读取 fixdb-risk-L1.5-P025-151147 这次回测摘要`

每条问题记录：

- 问题
- session / model
- 期望调用能力
- 实际是否触发
- 返回是否成功
- 是否带 `source / ts`
- 失败或 blocker 位置

### 9.4 最小 pass/fail 表

| 样本 | 期望能力 | 实际触发 | 结果 | `source` | `ts` | blocker |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA 最新价格 | `tradecat_get_quotes` |  |  |  |  |  |
| BTCUSDT 最近 1m 信号 | `tradecat_get_signals` |  |  |  |  |  |
| BTCUSDT 最近 2 小时新闻 | `tradecat_get_news` |  |  |  |  |  |
| fixdb-risk-L1.5-P025-151147 回测摘要 | `tradecat_get_backtest_summary` |  |  |  |  |  |

## 10. 当前结论

- `TRA-18` 的 runbook 产物现在已具备主仓落点，可以作为 `#003-02-08` 的交付物保留。
- `TRA-19` 之前的 blocker 结论基于过时 workspace，不能继续作为关闭依据。
- 下一步应该基于当前主仓，重新执行右侧原生 `openclaw tui` 的真实 E2E。
