---
title: "003-03-feature-agent-model-new-regression"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent P0-2：原生 `openclaw` `/model` 与 `/new` 工作台回归验证

## 背景

在新的架构下，`/model` 与 `/new` 仍然重要，但它们已经不再是 `TradeCat` 面板命令，而是右侧原生 `openclaw tui` 的命令语义：

- `/model` 需要证明右侧显示切换成功时，真实 session 模型也已切换
- `/new` 需要证明新会话 / reset 后历史上下文不会串台
- 在接入 `TradeCat` skill / 能力桥之后，这些原生命令不能被工作台包装层破坏

如果不单独做回归验证，就很容易出现“右侧能打开，但 session 语义已经漂移”的假成功。

## 目标

通过右侧原生 `openclaw tui` 路径，对 `/model` 与 `/new` 做最小但可信的回归验证。

## 本期范围

1. 设计最小回归用例
2. 验证 model 切换后右侧显示与 session 状态一致
3. 验证 new/reset 后历史上下文不再串台
4. 验证接入 `TradeCat` skill 后，`/model`、`/new` 仍保持原生语义
5. 记录成功样例、失败样例与 blocker
6. 形成一份简洁回归结论

## 非目标

- 不承担能力桥主链路实现
- 不验证 `TradeCat` 自绘 Agent 面板
- 不建设完整工具事件闭环
- 不做 quotes / signals / news E2E

## 验收标准

- [x] `/model` 切换步骤清晰可复现
- [x] `/new` 或 reset 语义验证步骤清晰可复现
- [x] 验证路径是原生 `openclaw tui`，不是 `TradeCat` 面板
- [x] 报告里能明确说明右侧显示与后端 session 状态是否一致
- [x] 能说明 `TradeCat` skill 接入是否影响这两个原生命令
- [x] 有通过样例和失败样例或 blocker 说明

## 相关 Issue

- Parent: `#003`
- Depends on: `#003-01`, `#003-02`

## 关闭边界

- 有回归结果和明确结论即可关闭
- 若仍被工作台、skill 或 upstream 能力阻塞，也要给出 blocker 和下一步建议后收口

## 最小回归矩阵

### Case M-01：原生 `openclaw tui` 下验证 `/model`

前置条件：

- 可启动原生 `openclaw tui`
- 本机存在可用 provider / model 配置
- 可读取 session 状态文件与 jsonl 日志

步骤：

1. 启动 `openclaw tui --session tc-00303-regression`
2. 记录默认模型
3. 执行 `/model xminimaxm25`
4. 发送探针：`Reply with ONLY the active provider/model key.`
5. 对照 UI、session 索引、jsonl 日志

通过口径：

- UI 状态栏显示切换后的模型
- `sessions.json` 中 session 的 `providerOverride` / `modelOverride` 与 UI 一致
- jsonl 中存在 `model_change` 与 assistant `provider` / `model` 证据

### Case N-01：原生 `openclaw tui` 下验证 `/new`

前置条件：

- Case M-01 可通过

步骤：

1. 发送：`Remember this exact token for later recall: TRA00303-RESET-A`
2. 发送：`What is the exact token I asked you to remember?`
3. 执行 `/new`
4. 记录新的 session id 与 reset 归档文件
5. 发送：`What token did I ask you to remember before reset? Reply with ONLY the token or UNKNOWN.`

通过口径：

- reset 前 assistant 能回忆出 `TRA00303-RESET-A`
- `/new` 后 session id 变化，旧 jsonl 被切分归档
- reset 后 assistant 返回 `UNKNOWN` 或等价失忆结果

### Case M-02 / N-02：接入 `TradeCat` skill 后复测

前置条件：

- `TradeCat` workspace skill 已按 `#003-02-08` 实际安装到 `openclaw`

通过口径：

- skill 只增加工具可见性，不改变 `/model`、`/new` 的原生命令语义
- 至少复测一次 M-01 / N-01，并保留与 skill 接入后的证据

## 当前主仓实测结果（2026-03-12）

### M-01 通过

执行环境：

- 会话名：`tc-00303-regression`
- 首次切换后的 session id：`bc6b3389-dace-4fb2-b0c9-d7286d8e3f83`

通过样例：

- `/model xminimaxm25` 成功，右侧 TUI 状态栏显示 `xop/xminimaxm25`
- 探针提示词返回 `xop/xminimaxm25`
- `/root/.openclaw/agents/main/sessions/sessions.json` 同步记录：
  - `providerOverride = xop`
  - `modelOverride = xminimaxm25`
  - `model = xminimaxm25`
- `/root/.openclaw/agents/main/sessions/bc6b3389-dace-4fb2-b0c9-d7286d8e3f83.jsonl` 记录了：
  - `model_change -> xop/xminimaxm25`
  - assistant `provider = xop`
  - assistant `model = xminimaxm25`

失败样例：

- `/model openai/glm-4.6` 返回 `model set failed: Error: model not allowed: xop/glm-4.6`
- `/model list` 与 `/model 2` 也被解释为非法 alias

结论：

- 当前环境下，`/model` 的可靠路径是“切到允许的 alias / model”，不是跨 provider 任意字符串
- 这不影响 `/model` 原生命令语义已可验证

### N-01 通过

reset 前证据：

- 同一旧 session `bc6b3389-dace-4fb2-b0c9-d7286d8e3f83` 中，assistant 成功返回 `TRA00303-RESET-A`

`/new` 后证据：

- `sessions.json` 中当前 session id 变为 `0451b2e6-67ad-4135-9192-63b3edff3c2b`
- 旧会话被归档为：
  - `/root/.openclaw/agents/main/sessions/bc6b3389-dace-4fb2-b0c9-d7286d8e3f83.jsonl.reset.2026-03-12T01-56-47.830Z`
- 新会话文件为：
  - `/root/.openclaw/agents/main/sessions/0451b2e6-67ad-4135-9192-63b3edff3c2b.jsonl`
- 新会话首条问答中 assistant 返回 `UNKNOWN`
- `/new` 后 `providerOverride` / `modelOverride` 被清空，模型回到默认 `xop/xopglm5`

结论：

- `/new` 不只是清空聊天窗口，而是实际新建了 session 并切断旧 history
- reset 后上下文未串台

### M-02 / N-02 通过

执行环境：

- workspace skill 状态：`openclaw skills info tradecat-bridge -> Ready`
- `/model` 复测会话：`tc-00303-skill-regression`
- `/new` / skill 复测会话：`tc-00303-skill-reset`

通过样例：

- skill 已安装后，`tc-00303-skill-regression` 的 `sessions.json` 记录：
  - `sessionId = 3a4076fd-0f1c-408e-ac4c-4c76b21286d3`
  - `providerOverride = xop`
  - `modelOverride = xminimaxm25`
  - `model = xminimaxm25`
- `/root/.openclaw/agents/main/sessions/3a4076fd-0f1c-408e-ac4c-4c76b21286d3.jsonl` 中存在：
  - `model_change -> xop/xminimaxm25`
  - 随后用户在同一 skill 环境下发出 `请使用 TradeCat bridge` 的提示
- 同一会话里的后续请求因 `AppIdNoAuthError` 失败，说明阻塞点是 `xop` provider 鉴权，不是 `/model` 语义漂移
- `tc-00303-skill-reset` 中，reset 前 session id 为 `35db4ab4-edce-464b-8772-68d69fa2ff49`
- 执行 `/new` 后，当前 session id 变为 `a5ff433c-5cf2-4b7b-9f21-f5ad01bab14f`
- 旧会话被归档为：
  - `/root/.openclaw/agents/main/sessions/35db4ab4-edce-464b-8772-68d69fa2ff49.jsonl.reset.2026-03-12T08-01-13.320Z`
- 新会话首问 `What token did I ask you to remember before reset?` 返回 `UNKNOWN`
- 新会话继续执行 `给我看 NVDA 最新价格。请使用 TradeCat bridge，并只回答 tool 和 price。`
- `/root/.openclaw/agents/main/sessions/a5ff433c-5cf2-4b7b-9f21-f5ad01bab14f.jsonl` 记录了：
  - `read ~/.openclaw/workspace/skills/tradecat-bridge/SKILL.md`
  - `exec python3 "/public/home/lixh6/laibao/proj/tx_test_0106/tradecat-origin/scripts/tradecat_get_quotes.py" NVDA --market us_stock`
  - assistant 最终返回 `tool: tradecat_get_quotes`

结论：

- `TradeCat` skill 只增加了工具可见性，没有改变 `/model` 的模型切换语义
- `/new` 仍然会切断旧 history；reset 后 skill 仍可被发现并继续调用
- M-02 / N-02 已满足关闭边界

## 当前结论

- 旧的 `TRA-13` Symphony 产出不能直接作为关闭依据；其中“主仓无法验证”的结论已过时
- 在当前主仓、当前本机环境下，原生 `openclaw tui` 的 `/model` 与 `/new` 都已完成功能级 smoke
- `TradeCat` skill 接入后的 M-02 / N-02 也已补完，且未破坏原生命令语义
- 本单关闭

## 进展记录

### 2026-03-09

- [x] 已对齐：`/model`、`/new` 是 P0，而不是“后面再看”的细节

### 2026-03-11

- [x] 已重新对齐：验证路径改为右侧原生 `openclaw tui`
- [x] 已明确：本单验证的是原生命令语义，不是 `TradeCat` UI 表现
- [x] 已派单到 Linear / Symphony：`TRA-13`（`[003-03] 原生 openclaw \`/model\` 与 \`/new\` 工作台回归验证`）
- [x] 已整理：最小回归矩阵与成功判定标准
- [x] 已纳入后续实测：skill 接入后 `/new` 对工具可见性的影响

### 2026-03-12

- [x] 已在当前主仓启动原生 `openclaw tui --session tc-00303-regression`
- [x] 已完成 `/model xminimaxm25` 实测，并用 UI + `sessions.json` + jsonl 三重证据确认切换成功
- [x] 已完成 `/new` 实测，并确认 session id 切换、旧 jsonl 归档、新会话失忆
- [x] 已确认旧 `TRA-13` 的 blocker 结论过时，不能继续作为当前主仓状态判断依据
- [x] 已确认 workspace skill `tradecat-bridge` 为 Ready，并补完 skill 接入后的 M-02 复测
- [x] 已确认 `/new` 后新 session 仍可读取 `tradecat-bridge` skill 并调用 `tradecat_get_quotes`
