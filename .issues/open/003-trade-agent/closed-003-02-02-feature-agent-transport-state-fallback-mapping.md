---
title: "003-02-02-feature-agent-transport-state-fallback-mapping"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: medium
type: feature
---

# Trade Agent 003-02-02：传输状态 / 错误 / fallback 语义表

> 来源：Linear `TRA-8`（Symphony 产出的规格初稿）。当前已完成，待人工 review；通过后可 move 到 `.issues/closed/003-trade-agent/`。

## 背景

当前 `#003` 的 Trade Agent 架构已对齐为：

- `TradeCat`：负责 TUI / 布局 / 交互
- `openclaw`：负责 session / chat / model / tool backend
- `TradeCat` 通过 `OpenClawAdapter` 对接 `openclaw Gateway`

`#003-02` 需要的不是“能发请求”这一层能力，而是把运行时语义统一下来，避免 TUI 状态栏、日志、adapter 错误处理各说各话：

- Gateway 断开时 TUI 怎么显示
- 超时、不可达、鉴权失败怎么区分
- 什么时候允许切 CLI fallback，什么时候只报错不兜底

注：当前工作区中的 `repository/openclaw` 子模块未展开，本规格基于现有 `#003` 语义和 Trade Agent 目标定义统一约束，不要求依赖 openclaw 实现细节。

## 目标

产出一份可直接供以下场景复用的统一语义表：

1. TUI 状态栏与提示文案
2. `OpenClawAdapter` 错误分类与状态流转
3. 结构化日志字段与日志级别
4. CLI fallback 的触发、禁止与退出策略
5. 默认超时 / 重试 / 降级建议

## 范围

### In Scope

- 定义最小传输状态集合
- 定义状态优先级与展示语义
- 定义错误到状态的映射规则
- 定义 CLI fallback 的策略边界
- 给出默认超时 / 重试 / 恢复建议

### Out of Scope

- 不直接修改 `repository/openclaw`
- 不直接实现 TUI 组件
- 不直接实现重试器 / adapter 内核
- 不扩大为完整状态机代码开发

## 当前架构前提

### 1) 主链路优先级

- 默认主链路：`TradeCat -> OpenClawAdapter -> openclaw Gateway`
- CLI fallback：仅作为显式允许的降级通道，不作为默认主链路

### 2) 语义分层

- 状态栏层：只显示最小状态集合，避免过多细粒度状态污染 UI
- 日志层：保留 `reason_code`、HTTP 状态、异常类名等详细原因
- adapter 层：先归类到统一状态，再决定是否重试 / fallback / 报错

### 3) 设计原则

- 状态数量尽量少，但错误原因必须可追踪
- 非可恢复错误不允许“静默兜底”
- fallback 必须让用户感知，不能伪装成主链路成功

## 状态优先级（用于状态栏冲突裁决）

当“底层连接状态”与“当前请求瞬时状态”同时存在时，状态栏按以下优先级显示：

1. `error`
2. `cli_fallback`
3. `timeout`
4. `disconnected`
5. `degraded`
6. `connecting`
7. `ready`

补充约束：

- `timeout` 与 `error` 属于请求级瞬时覆盖状态，默认展示 `8s` 后回落到基础连接状态
- 若 `timeout` 在 `2min` 内连续出现 `>= 2` 次，应将基础连接状态提升为 `degraded`
- `cli_fallback` 为模式态，只要当前请求已切到 CLI，即优先显示，直到退出 fallback

## 最小状态集合

| 状态 | 含义 | 是否稳定态 | 说明 |
| --- | --- | --- | --- |
| `connecting` | 正在建立或恢复主链路 | 否 | 首次启动、重连、fallback 退出后的回切 |
| `ready` | 主链路健康可用 | 是 | Gateway 可达，鉴权/握手通过，可正常发请求 |
| `degraded` | 主链路仍可用，但稳定性/能力下降 | 是 | 慢、偶发失败、部分能力缺失，但未完全断开 |
| `timeout` | 当前请求超过时限 | 否 | 用于表达“这次请求超时”，不等同于永久断开 |
| `disconnected` | 主链路不可达或已断开 | 是 | 无法建连、心跳丢失、连接被对端关闭 |
| `cli_fallback` | 当前运行在 CLI 降级通道 | 是 | 必须显式展示为降级模式 |
| `error` | 非可恢复或未知错误 | 否/终态 | 配置、鉴权、协议、版本、CLI 自身异常等 |

## 状态语义表

| 状态 | 触发条件 | TUI / 用户提示 | 日志级别 | 建议动作 |
| --- | --- | --- | --- | --- |
| `connecting` | 首次启动；手动重连；`disconnected`/`cli_fallback` 后尝试恢复 Gateway | `Agent 连接中...` | `info` | 显示转圈/等待；暂停新请求排队或提示稍候 |
| `ready` | Gateway 健康检查通过；握手成功；最近心跳/探测在新鲜窗口内 | `Agent 已连接` | 状态切换时 `info`；稳态可不重复 | 正常发送请求 |
| `degraded` | Gateway 可达但出现高延迟、单次重试后才成功、部分事件流缺失、可选能力暂不可用 | `Agent 连接不稳定` | `warning` | 允许继续查询；后台重试探测；必要时提示“结果可能较慢或缺少实时事件” |
| `timeout` | 首包超时；流式响应长时间无新 token / 事件；总请求超出 deadline | `Agent 响应超时` | 首次 `warning`；达到 hard timeout 可记 `error` | 对幂等读请求允许一次自动重试；满足条件时可评估 CLI fallback |
| `disconnected` | 连接拒绝、DNS 失败、网络不可达、socket 关闭、心跳连续丢失 | `Agent 已断开` | 状态切换 `error`；后续重试失败记 `warning` | 停止把请求直接发往 Gateway；进入重连退避；策略允许则尝试 CLI fallback |
| `cli_fallback` | 主链路不健康且满足 fallback 门槛；CLI 通道健康检查通过 | `Agent 已切换 CLI fallback` | 进入 `warning`；退出 `info` | 显式标记“降级运行”；限制到安全/只读请求；持续探测 Gateway 恢复 |
| `error` | 鉴权失败、配置错误、协议不兼容、CLI 缺失/异常、未知不可分类异常 | `Agent 配置或协议错误` / `Agent 运行错误` | `error` | 不自动 fallback；明确暴露错误与修复建议；等待人工处理 |

## 错误到状态映射

| 原始错误 / 场景 | 归类状态 | `reason_code` 建议 | 是否自动重试 | 是否允许 CLI fallback |
| --- | --- | --- | --- | --- |
| DNS 解析失败 / `ECONNREFUSED` / 网络不可达 | `disconnected` | `network.unreachable` | 是 | 是（需满足 fallback 前提） |
| TCP 已连接但请求首包超时 / 流中断超时 | `timeout` | `network.timeout` | 是（限 1 次） | 是（连续超时后） |
| HTTP `401/403` / API key 无效 / 网关鉴权失败 | `error` | `auth.denied` | 否 | 否 |
| HTTP `404/426/501` / 协议版本不兼容 / 路由不存在 | `error` | `protocol.unsupported` | 否 | 否 |
| HTTP `429` / `502` / `503` / 上游过载但服务仍可达 | `degraded`（超出 deadline 则叠加 `timeout`） | `gateway.overloaded` | 是（带退避） | 持续过载后可启用 |
| 心跳连续丢失 / 连接被对端关闭 | `disconnected` | `gateway.disconnected` | 是 | 是 |
| 工具事件解析失败，但回答正文仍可用 | `degraded` | `protocol.partial_event` | 否 | 否 |
| CLI 二进制不存在 / CLI 返回非零退出 / CLI 版本不匹配 | `error` | `fallback.unavailable` | 否 | 否（并退出 fallback） |
| 未知异常且无法确定可恢复性 | `error` | `unknown` | 否（除非白名单） | 否 |

## CLI fallback 策略

### 触发条件（全部满足才允许进入）

1. 已显式开启 fallback 开关（例如 `agent.cli_fallback_enabled=true`）
2. 主链路当前为 `disconnected`，或在短窗口内连续 `timeout`
3. 当前请求是只读 / 幂等 / 可容忍降级的任务
4. CLI 通道本身健康：可执行文件存在、基础配置完整、健康检查通过
5. 当前请求未声明 `strict_gateway_only` / `no_fallback`

### 默认触发门槛（建议值）

- `disconnected` 连续出现 `>= 2` 次，且总时长超过 `5s`
- 或 `timeout` 在 `90s` 内连续出现 `>= 2` 次
- 或 Gateway 明确返回 `502/503/504` 且重试后仍失败

### 禁止条件（任一满足即禁止进入）

1. 鉴权、配置、协议、版本类错误（`auth.denied` / `protocol.unsupported` / `fallback.unavailable`）
2. 用户正在执行 `/model`、`/new`、需要精确 session 连续性的操作
3. 当前请求要求结构化工具事件完整落盘（`tool_start / tool_update / tool_end / tool_error`）
4. 当前请求属于写操作、外部副作用操作，或未来的交易执行类动作
5. CLI 与 Gateway 的模型/工具权限/来源标注能力不一致且无法显式告知用户

### 退出条件

1. Gateway 健康探测连续成功 `>= 2` 次，且间隔不少于 `10s`
2. 下一次新请求优先回到 Gateway，并显示 `connecting -> ready`
3. 用户显式关闭 fallback 或选择“仅重试主链路”
4. CLI fallback 自身进入 `error`（例如 CLI 不可用）
5. 用户执行 `/model`、`/new` 等要求回到主链路重新建会话的操作

### 用户可见约束

- 进入 fallback 时必须提示：当前为降级模式，结果可能缺少实时事件或完整工具轨迹
- 在回答来源标注中，应额外加入 `transport=cli_fallback`
- 不允许把 fallback 结果伪装为主链路成功

## 默认超时 / 重试 / 降级建议

| 项目 | 建议值 | 说明 |
| --- | --- | --- |
| 健康检查连接超时 | `1.5s` | 用于状态栏与后台探测，不宜过长 |
| 首次建连超时 | `3s` | 首次启动或重连握手 |
| 首包超时（交互请求） | `10s` | 用户已发起请求但迟迟无响应 |
| 流式空闲超时 | `15s` | 流式 token / tool event 长时间无更新 |
| 单次请求总超时 | `45s` | 默认聊天/查询类请求 hard deadline |
| 自动重试次数 | `1` 次 | 仅限只读 / 幂等请求 |
| 快速重试退避 | `250ms -> 1s` | 用于一次自动重试 |
| 后台重连退避 | `2s -> 5s -> 10s -> 20s -> 30s` | 上限 `30s`，避免刷屏 |
| 进入 `degraded` 门槛 | `2min` 内 `>= 2` 次超时或最近 `5` 次探测中 `>= 2` 次失败 | 用于表达“能用但不稳” |
| fallback 恢复门槛 | Gateway 探测连续成功 `2` 次 | 避免频繁抖动 |

## 日志与结构化字段建议

为让 TUI、日志与 session history 可交叉验证，建议每个传输事件至少落以下字段：

- `transport_state`
- `reason_code`
- `request_id`
- `session_id`
- `model`
- `attempt`
- `latency_ms`
- `retry_in_ms`
- `fallback_allowed`
- `fallback_decision`

## 为什么适合 Sym

- 这是规格定义题，不依赖大量代码改造
- 输出就是状态表、策略表、异常分类表，验收边界清晰
- 先做完可以直接减少主仓实现时的拍脑袋决策

## 非目标

- 不实现真实重试器
- 不落具体 TUI 组件代码
- 不修改 `repository/openclaw`

## 验收标准

- [x] 有一份最小状态枚举和解释
- [x] 每种状态都有触发条件、UI 提示、日志语义
- [x] CLI fallback 的触发与禁止条件明确
- [x] 有超时 / 重试 / 降级建议，可直接供 `#003-02` 与 `#003-05` 使用

## 相关 Issue

- Parent: `#003`
- Supports: `#003-02`
- Related: `#003-05`

## 关闭边界

- 状态表、fallback 策略和默认建议齐备即可关闭

## 进展记录

### 2026-03-09

- [ ] 待开始：整理传输状态枚举草案
- [ ] 待开始：输出 fallback 触发矩阵

### 2026-03-10

- [x] 已产出最小状态集合 + 状态优先级 + 状态语义表
- [x] 已产出错误分类到状态映射 + `reason_code` 建议
- [x] 已产出 CLI fallback 触发/禁止/退出策略 + 默认超时/重试建议

### 2026-03-12

- [x] 已人工确认该参考规格单可以正式收口，保留在 `closed-*` 文件名下作为历史材料
