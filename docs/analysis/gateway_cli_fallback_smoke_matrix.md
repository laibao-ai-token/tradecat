# Gateway / CLI fallback smoke 验证矩阵

## 目标

为 `#003-02` 联调和 `#003-03` 回归提供一份最小但可信、可反复复用的 smoke matrix，覆盖：

- session 列表加载
- history 加载
- send message
- models list
- model patch
- session reset
- Gateway 不可达 / 请求超时 / 返回异常
- CLI fallback 开 / 关

本文档的定位是“验证矩阵 + 执行模板 + blocker 说明”，不是 adapter 实现，也不是完整 E2E。

## 当前事实与 blocker（2026-03-10 UTC）

- `B1`：`repository/openclaw` 当前是未初始化的 gitlink，工作树为空，无法在本仓库内确认真实命令面、日志路径、session 存储结构。
- `B2`：当前仓库内没有可直接复用的 Gateway stub / timeout 注入 / malformed response 注入脚本，异常场景暂不能在本地直接执行。
- `B3`：当前仓库内没有现成的 `Gateway / CLI fallback` 运行产物，无法填充真实证据样例；因此本单只交付矩阵、模板与 blocker，不伪造 pass。

结论：下表所有场景当前统一标记为 `Blocked` 或 `Not Run`；待 `repository/openclaw` 工作树可用后，按本文档直接补跑即可。

## 共享前置条件

- `P1`：`repository/openclaw` 已初始化，且能定位到 TUI / CLI 入口命令。
- `P2`：已知 Gateway 地址、认证方式、默认模型、CLI fallback 开关名。
- `P3`：已知日志文件路径或 stderr 重定向路径。
- `P4`：已知 session 存储位置（如 `jsonl`、sqlite、目录结构）。
- `P5`：已有至少 1 个可读取的测试会话；若没有，则先执行一次最小 send message 建立会话。
- `P6`：建议统一归档证据到 `artifacts/analysis/gateway_cli_fallback/<run-id>/`，其中 `<run-id>` 推荐使用 `YYYYMMDD-HHMMSS`。

## 证据契约

每个场景都至少采集三类证据：

- `TUI`：界面状态变化、错误提示、模型标识、消息内容或会话列表截图/录屏。
- `LOG`：Gateway 调用、fallback 决策、错误栈、重试记录、最终成功/失败日志。
- `SESSION`：会话元数据、message 记录、model 变更记录、reset 后的上下文状态。

建议每个场景的归档目录统一为：

`artifacts/analysis/gateway_cli_fallback/<run-id>/<scenario-id>/`

目录内最少包含：

- `notes.md`：执行人、时间、结果、备注
- `tui.png` 或 `tui.txt`
- `app.log`
- `session.txt` 或 `session.jsonl`

## 执行顺序建议

为减少依赖，建议按以下顺序首次执行：

1. `S01` session 列表加载
2. `S03` send message（若需要建立测试会话）
3. `S02` history 加载
4. `S04` models list
5. `S05` model patch
6. `S06` session reset
7. `F01` ~ `F06` 异常 / fallback 场景

## 主链路 smoke matrix

| ID | 场景 | 前置条件 | 操作步骤 | 预期结果 | 证据位置 | 当前状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `S01` | session 列表加载 | `P1` `P2` `P3` `P4`；Gateway 可达；CLI fallback=`off` | 1. 启动 TUI 或 CLI。<br>2. 进入 session 列表视图或执行 session list。<br>3. 等待列表返回。 | 1. 列表成功返回。<br>2. TUI 列表与 Gateway 返回一致。<br>3. 日志中没有 fallback 标记。 | `TUI`：session 列表区。<br>`LOG`：list 请求/响应日志。<br>`SESSION`：若有审计事件则记录 list 行为；不应修改会话内容。 | `Blocked` (`B1`,`B3`) |
| `S02` | history 加载 | `P1` `P2` `P3` `P4` `P5`；Gateway 可达；CLI fallback=`off` | 1. 打开已有测试会话。<br>2. 触发 history 加载。<br>3. 比对首尾消息和消息数。 | 1. 历史消息顺序正确。<br>2. 无重复、无截断。<br>3. 当前会话 ID 与 history 来源一致。 | `TUI`：消息列表/滚动区域。<br>`LOG`：history 请求/响应日志。<br>`SESSION`：历史消息文件或会话元数据。 | `Blocked` (`B1`,`B3`) |
| `S03` | send message | `P1` `P2` `P3` `P4`；Gateway 可达；CLI fallback=`off` | 1. 在新会话或测试会话发送固定 smoke prompt。<br>2. 等待 assistant 返回。<br>3. 检查消息是否持久化。 | 1. 消息发送成功。<br>2. 返回内容非空，且与当前模型一致。<br>3. session 中新增 user/assistant 记录。 | `TUI`：输入框与消息气泡。<br>`LOG`：send 请求/响应、耗时。<br>`SESSION`：新消息记录。 | `Blocked` (`B1`,`B3`) |
| `S04` | models list | `P1` `P2` `P3` `P4`；Gateway 可达；CLI fallback=`off` | 1. 打开模型列表入口或执行 models list。<br>2. 记录默认模型和可选模型列表。 | 1. 模型列表成功返回。<br>2. 默认模型可识别。<br>3. 日志中无 fallback。 | `TUI`：模型列表/下拉面板。<br>`LOG`：models list 请求/响应。<br>`SESSION`：可选；若有模型缓存元数据则留存。 | `Blocked` (`B1`,`B3`) |
| `S05` | model patch | `P1` `P2` `P3` `P4`；Gateway 可达；CLI fallback=`off`；至少有两个可切换模型 | 1. 先执行 `S04` 获取候选模型。<br>2. 切换到目标模型。<br>3. 立即发送一条固定 prompt 验证实际生效模型。 | 1. UI 当前模型标识更新。<br>2. 后续请求实际使用新模型。<br>3. session 或日志能看到模型变更记录。 | `TUI`：状态栏/模型 badge。<br>`LOG`：model patch 请求、后续 send 使用的模型。<br>`SESSION`：模型元数据变更记录。 | `Blocked` (`B1`,`B3`) |
| `S06` | session reset | `P1` `P2` `P3` `P4`；Gateway 可达；CLI fallback=`off`；测试会话已有上下文 | 1. 在已有上下文的会话中执行 reset。<br>2. 再发送一条依赖上下文的问题。<br>3. 观察是否正确丢失旧上下文。 | 1. reset 操作成功。<br>2. 后续回答不再引用旧上下文。<br>3. session 状态体现上下文已清空或新会话已创建。 | `TUI`：reset 提示与后续对话。<br>`LOG`：reset 请求/响应。<br>`SESSION`：上下文清空、新会话 ID 或消息截断证据。 | `Blocked` (`B1`,`B3`) |

## 异常 / fallback smoke matrix

说明：异常矩阵采用最小覆盖原则，优先选择最能体现 fallback 决策的 3 类操作：

- `session list`：验证只读列表请求的 fallback 行为
- `send message`：验证长链路主交互的 fallback 行为
- `models list`：验证元数据请求在异常响应下的行为

| ID | 场景 | 前置条件 | 操作步骤 | 预期结果 | 证据位置 | 当前状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `F01` | Gateway 不可达 + fallback=`off`（session 列表） | `P1` `P2` `P3` `P4`；存在“不可达 Gateway”配置 | 1. 将 Gateway 指向不可达地址。<br>2. 显式关闭 CLI fallback。<br>3. 执行 session list。 | 1. 请求快速失败。<br>2. UI 明确显示错误。<br>3. 不发生静默 CLI fallback。 | `TUI`：错误提示。<br>`LOG`：连接失败/拒绝连接。<br>`SESSION`：不应产生会话修改。 | `Blocked` (`B1`,`B2`,`B3`) |
| `F02` | Gateway 不可达 + fallback=`on`（session 列表） | `P1` `P2` `P3` `P4`；CLI 路径可用；存在“不可达 Gateway”配置 | 1. 将 Gateway 指向不可达地址。<br>2. 开启 CLI fallback。<br>3. 执行 session list。 | 1. 首次 Gateway 请求失败。<br>2. 明确记录已切换到 CLI fallback。<br>3. 若 CLI 正常，则 session list 成功返回。 | `TUI`：错误后恢复或 fallback 标识。<br>`LOG`：Gateway fail -> CLI fallback -> success。<br>`SESSION`：可选；若有审计事件应记录 fallback。 | `Blocked` (`B1`,`B2`,`B3`) |
| `F03` | 请求超时 + fallback=`off`（send message） | `P1` `P2` `P3` `P4`；存在超时注入方式 | 1. 配置一个会触发超时的 Gateway。<br>2. 关闭 CLI fallback。<br>3. 执行固定 smoke prompt。 | 1. 请求按超时阈值失败。<br>2. UI 给出超时错误。<br>3. 不发生 CLI fallback。<br>4. session 中不应出现“伪成功” assistant 消息。 | `TUI`：超时提示。<br>`LOG`：timeout 与耗时。<br>`SESSION`：仅保留 user 消息或错误事件，无成功回复。 | `Blocked` (`B1`,`B2`,`B3`) |
| `F04` | 请求超时 + fallback=`on`（send message） | `P1` `P2` `P3` `P4`；CLI 路径可用；存在超时注入方式 | 1. 配置超时 Gateway。<br>2. 开启 CLI fallback。<br>3. 发送固定 smoke prompt。 | 1. Gateway 超时后触发 CLI fallback。<br>2. UI 或日志明确标记 fallback 来源。<br>3. 若 CLI 正常，则最终返回可用回复，并持久化到 session。 | `TUI`：loading 后恢复、fallback 标识、最终回复。<br>`LOG`：timeout -> fallback -> success。<br>`SESSION`：最终 assistant 回复与来源标记。 | `Blocked` (`B1`,`B2`,`B3`) |
| `F05` | 返回异常 + fallback=`off`（models list） | `P1` `P2` `P3` `P4`；存在 malformed response 注入方式 | 1. 让 models list 返回非预期结构或非法 JSON。<br>2. 关闭 CLI fallback。<br>3. 执行 models list。 | 1. UI 明确显示解析失败或异常响应。<br>2. 不发生 CLI fallback。<br>3. 不缓存脏数据。 | `TUI`：错误提示或空列表态。<br>`LOG`：parse error / schema mismatch。<br>`SESSION`：不应写入错误模型元数据。 | `Blocked` (`B1`,`B2`,`B3`) |
| `F06` | 返回异常 + fallback=`on`（models list） | `P1` `P2` `P3` `P4`；CLI 路径可用；存在 malformed response 注入方式 | 1. 让 models list 返回异常。<br>2. 开启 CLI fallback。<br>3. 执行 models list。 | 1. Gateway 异常被正确识别。<br>2. 明确触发 CLI fallback。<br>3. 若 CLI 正常，则模型列表可用，且来源可区分。 | `TUI`：fallback 后模型列表恢复。<br>`LOG`：response error -> fallback -> success。<br>`SESSION`：可选；若有模型缓存来源，应记录来源切换。 | `Blocked` (`B1`,`B2`,`B3`) |

## Pass / Fail 记录模板

### 执行汇总表模板

| Run ID | Scenario ID | Gateway 状态 | CLI fallback | Result | 证据目录 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| `20260310-000000` | `S01` | healthy / unreachable / timeout / malformed | on / off | Pass / Fail / Blocked / Not Run | `artifacts/analysis/gateway_cli_fallback/<run-id>/<scenario-id>/` |  |

### 单场景记录模板

```md
# Gateway / CLI fallback smoke record

- Scenario ID:
- Run ID:
- 执行日期:
- 执行人:
- Git SHA:
- 环境:
- Gateway 配置:
- CLI fallback: on / off
- 结果: Pass / Fail / Blocked / Not Run

## 前置条件检查

- [ ] `repository/openclaw` 已初始化
- [ ] 已确认 TUI / CLI 入口命令
- [ ] 已确认日志路径
- [ ] 已确认 session 存储路径
- [ ] 已准备测试会话 / 测试模型 / 异常注入配置

## 执行步骤

1.
2.
3.

## 预期结果

1.
2.
3.

## 实际结果

-

## 证据

- TUI:
- LOG:
- SESSION:

## Blocker / 偏差

-

## 结论

- [ ] Pass
- [ ] Fail
- [ ] Blocked
- [ ] 需要后续补跑
```

## 交付给 `#003-02` / `#003-03` 的使用方式

- `#003-02` 联调阶段：先按 `S01` ~ `S06` 补齐 happy path，确认主链路能用。
- `#003-03` 回归阶段：复用同一套场景 ID，再补跑 `F01` ~ `F06`，验证 fallback 不回退、不静默失败。
- 若后续补齐 `OpenClawAdapter` 或 Gateway mock，可在本文件继续追加 `F07+`，但不要改动现有场景 ID，避免回归数据失去可比性。

