---
title: "003-feature-trade-agent-development"
status: closed
created: 2026-02-26
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent 开发（双 TUI 工作台 + TradeCat 能力桥 + openclaw skill）

## Source of Truth

- 主入口：本 issue `#003`
- 子 issue 目录：`.issues/open/003-trade-agent/`
- 推进方式：`#003` 负责总目标、边界、架构决策与整体状态；子 issue 负责单点收口

## 背景

TradeCat 现在已经有行情、新闻、信号等 TUI 能力，但 Agent 方向的目标已经重新收敛：

- 左侧继续是原生 `TradeCat TUI`，负责展示、研究和交易上下文
- 右侧继续是原生 `openclaw tui`，负责 Agent UI、session、model、tool runtime
- 两边**不做 UI 级融合**
- 集成只发生在**能力层**：由 `TradeCat` 暴露一组稳定的只读能力给 `openclaw`

也就是说，这一轮不再追求“TradeCat 自绘 Agent 面板 + openclaw backend”，而是做成一个**双 TUI 工作台**：

- 左：TradeCat 原生界面
- 右：openclaw 原生界面
- 中间通过只读能力桥衔接，而不是通过 curses 布局或终端文本耦合

## 架构决策

### ADR-1：工作台层只解决“并排工作”

- 用 `tmux`（或明确的降级方案）启动左 `TradeCat`、右 `openclaw tui`
- 右侧 UI 完全复用 `openclaw` 原生界面
- 这层不承担数据桥接或 session 协议适配

### ADR-2：Agent UI 与运行时主导权都在 openclaw

- session 生命周期、chat、model、tool 调度由 `openclaw` 持有
- `TradeCat` 不再承担 Agent UI，不再自绘最终形态的右侧 Agent 面板
- `/model`、`/new` 等命令以 `openclaw` 原生语义为准

### ADR-3：集成只发生在能力层

- `TradeCat` 只暴露稳定的只读能力，不让 `openclaw` 解析终端画面
- 第一批能力以研究型查询为主：
  - `tradecat_get_quotes`
  - `tradecat_get_signals`
  - `tradecat_get_news`
  - `tradecat_get_backtest_summary`
- `tradecat_get_current_focus` 仅作为后续增强项，不是当前 MVP 前置

### ADR-4：能力桥保持松耦合、只读、可替换

- 能力桥优先做成本地脚本 / 命令接口，输出稳定 JSON
- `openclaw` 通过 workspace skill / managed skill 学会何时调用这些能力
- 不让 `TradeCat` 直接接管 `openclaw` 的 session API，也不让 `openclaw` 反向控制左侧 TUI

### ADR-5：默认不改 openclaw

- `repository/openclaw` 视为上游依赖与原生 UI / runtime
- 本期优先通过 launcher、skill、bridge 接入，不主动修改 `repository/openclaw`
- 若未来发现 upstream 缺口，再单独立 issue

## 目标

交付一个可用的 Trade Agent MVP，让用户能够：

- 用一条命令打开双 TUI 工作台
- 在右侧 `openclaw tui` 中直接提问研究型任务
- 让 `openclaw` 通过 `TradeCat` 能力桥获取行情、信号、新闻、回测摘要
- 在不改右侧 UI 的前提下完成最小研究闭环

## 本轮收敛结论

`#003` 本轮只解决一件事：**把右侧原生 `openclaw tui` 接到左侧 `TradeCat` 的研究能力上。**

明确边界：

- 左侧界面继续由 `TradeCat` 负责
- 右侧界面就是 `openclaw tui`，不在 `TradeCat` 内复刻，也不修改 `openclaw` UI
- 工作台层用 `tmux` 解决“并排运行”
- 集成层只做能力桥，不做 UI 融合

因此，`#003` 的成功标准不是“做出一个新的 Agent 界面”，而是：

- 左侧能看
- 右侧能聊
- 右侧能调左侧能力
- 整个研究路径能跑通

## MVP 范围

1. `#003-01`：提供稳定的双 TUI 工作台 launcher
2. `#003-02`：提供 `TradeCat` 只读能力桥与统一 JSON 输出
3. 在 `openclaw` 侧接入 workspace skill / managed skill，让右侧知道如何调用这些能力
4. `#003-03`：验证 `/model`、`/new` 在原生 `openclaw tui` 下不受工作台 / skill 接入影响
5. `#003-06`：用 quotes / signals / news（必要时加 backtest summary）跑最小研究型 E2E

## 非目标（本期不做）

- 不在 `TradeCat` 里继续深挖自绘 Agent 面板
- 不把 `openclaw` TUI 嵌进 `TradeCat`
- 不让 `openclaw` 解析 `TradeCat` 终端文本
- 不做双向联动控制流
- 不做自动交易或可写操作
- 不默认修改 `repository/openclaw`

## 开发里程碑

### Phase 1：工作台层（P0）

- [x] `#003-01` 已落最小 launcher：`scripts/launch_trade_workbench.sh`
- [x] 工作台使用细节（焦点切换、失败提示、runbook 收口）已补到 `AGENTS.md` / `README*.md`

### Phase 2：能力桥层（P0）

- [x] `#003-02` 已落一组稳定的只读桥接命令
- [x] quotes / signals / news / backtest summary 具备最小可用输出
- [x] 返回结构、超时、错误语义统一

### Phase 3：skill 与原生命令烟测（P0）

- [x] skill 配置路径可复现，右侧 `openclaw` 能发现 `TradeCat` 能力
- [x] `#003-03` 验证 `/model` 与 `/new` 在原生 `openclaw tui` 下语义稳定

### Phase 4：支撑性观测与事件资产（Support）

- [x] `#003-04` 已沉淀最小事件 DTO / 字段语义与本地落点
- [x] `#003-05` 已沉淀最小观测链路与 upstream 证据
- [x] 已明确这些资产在新架构下属于“支撑 / fallback”，而非最终 UI 主链路

### Phase 5：最小研究型 E2E（P1）

- [x] `#003-06` 通过右侧 `openclaw tui` 跑通最小研究任务
- [x] 已形成一份可复现实验记录

## 验收标准

- [x] 有一条命令能稳定启动双 TUI 工作台
- [x] 左侧是原生 `TradeCat`，右侧是原生 `openclaw tui`
- [x] 两边不做 UI 级融合
- [x] `openclaw` 可通过能力桥读取 `TradeCat` 的 quotes / signals / news
- [x] skill 配置方式、调用方式和失败提示可复现
- [x] `/model` 与 `/new` 在原生右侧 TUI 下可验证
- [x] 至少一条研究型问题可通过右侧 Agent 调到 `TradeCat` 能力并成功回答

## 相关 Issue

- Related: `#004`（新闻能力可直接作为 Agent 研究输入）

## 执行约束

- 默认只修改 `TradeCat` 主仓库，不主动修改 `repository/openclaw`
- 不做 UI 融合，不解析终端文本，不依赖 curses 结构
- 第一阶段只做只读研究能力，不做写操作和自动执行
- 任何 commit / push 需先经 owner 明确同意

## 建议任务拆分（当前版本）

1. [x] issue 结构已拆分为父子 issue，便于并行推进与单点收口
2. [x] 架构方向已重定向为“双 TUI 工作台 + TradeCat 能力桥 + openclaw skill”
3. [x] `#003-01` 已完成 launcher 与工作台编排
4. [x] `#003-02` 已完成只读能力桥与 JSON 输出契约
5. [x] `#003-02-01 ~ #003-02-03` 仍可作为 openclaw upstream / fallback 参考资料
6. [x] `#003-02-04 ~ #003-02-08` 已拆出，供能力桥按命令维度并行推进
7. [x] `#003-03` 补原生 `/model`、`/new` 的工作台 smoke
8. [x] `#003-04` 已完成最小事件 DTO 资产
9. [x] `#003-05` 已完成最小观测资产
10. [x] `#003-06` 已完成最小研究型 E2E 并输出结果记录

## 进展记录

### 2026-03-09

- [x] 已将 `#003` 重整为父 issue + 子 issue 结构
- [x] 已确认 `openclaw` 保持上游运行时角色
- [x] 已拆分 `#003-02` 的支撑子任务：`#003-02-01 ~ #003-02-03`

### 2026-03-10

- [x] `#003-02-01`：已产出 Gateway 合约清单与 DTO 映射文档
- [x] `#003-02-02`：已产出传输状态 / 错误 / CLI fallback 语义表
- [x] `#003-02-03`：已产出 Gateway / CLI fallback smoke matrix（作为参考资料保留）
- [x] `#003-04`：已完成最小事件 DTO 规格、代码壳、单测，以及本地落点
- [x] `#003-05`：已完成最小观测契约与 upstream 证据确认

### 2026-03-11

- [x] 已重新对齐：最终形态不再是 `TradeCat` 自绘 Agent 面板
- [x] 已重新对齐：左侧 `TradeCat`、右侧 `openclaw tui`，两边不做 UI 级融合
- [x] 已重新对齐：主链路改为 `TradeCat` 暴露只读能力，供 `openclaw` 通过 skill 调用
- [x] 已收敛：右侧界面本身不再作为 `#003` 的开发对象，默认复用 `openclaw tui`
- [x] 已继续拆分 `#003-02`：新增 `#003-02-04 ~ #003-02-08`，按 quotes / signals / news / backtest / skill-runbook 分单
- [x] 已派单到 Linear / Symphony：`TRA-14` / `TRA-15` / `TRA-16`
- [x] 已补派单到 Linear / Symphony：`TRA-17`
- [x] 已落地：`scripts/launch_trade_workbench.sh`
- [x] 已按主仓实现：`TradeCat` 能力桥脚本 / 命令入口
- [x] 已按主仓实现：`tradecat` skill 与最小 E2E

### 2026-03-12

- [x] `tradecat_get_quotes` / `tradecat_get_signals` / `tradecat_get_news` / `tradecat_get_backtest_summary` 已在主仓落地
- [x] `#003-02-08` runbook 已落回主仓：`docs/learn/openclaw_tradecat_skill_runbook.md`
- [x] 已确认当前主仓满足 `#003-06` 的预检条件：`repository/openclaw/`、桥接脚本、`signal_history.db`、`artifacts/backtest/` 都存在
- [x] 已确认旧的 `TRA-19` review 结论基于过时 workspace，不能继续作为关闭依据
- [x] 已决定拒绝旧一轮 `TRA-19` 产物，不合入主仓
- [x] `#003-03` 已在当前主仓完成原生 smoke：`/model xminimaxm25` 与 `/new` 都已通过 UI + session 文件验证
- [x] 已确认旧的 `TRA-13` blocker 结论同样基于过时 workspace，不能继续作为当前主仓状态判断依据
- [x] `#003-02-08` 已补齐：workspace skill 模板 `skills/tradecat-bridge/SKILL.md.template` 与安装脚本 `scripts/install_openclaw_tradecat_skill.sh`
- [x] 已验证：`openclaw skills` 可发现 workspace skill `tradecat-bridge`
- [x] `#003-02-08` 已收口：实际使用验证并入 `#003-06` 最小 E2E
- [x] `#003-06` 已在当前主仓完成真实 smoke，并收口为 `closed-003-06-feature-agent-minimal-e2e.md`
- [x] `#003-03` 已补完 skill 接入后的 M-02 / N-02 复测，并收口为 `closed-003-03-feature-agent-model-new-regression.md`
- [x] `#003-02` 已按主仓现状完成父单回填并收口
- [x] `#003` 的 MVP 验收标准已满足，父单关闭

## 当前结论（2026-03-12）

### 已完成

- 双 TUI launcher、TradeCat 能力桥、workspace skill、原生命令回归、最小研究型 E2E 都已在主仓验证
- 左侧保持原生 `TradeCat TUI`，右侧保持原生 `openclaw tui`，集成只发生在能力层
- `#003-01` / `#003-02` / `#003-03` / `#003-06` 均已收口，`#003-04` / `#003-05` 作为支撑资产保留

### 风险备注

- `xop` provider 在实际请求时仍可能报 `AppIdNoAuthError`；这属于环境 / provider 鉴权问题，不阻塞 `#003` 收口
- 双 TUI launcher 依赖本机安装 `tmux`；未安装时会自动降级为手工双终端 runbook

### 后续非阻塞迭代建议

- 若后续继续扩展，可另开新单推进 `tradecat_get_current_focus`、更多只读 bridge 命令，或更强的研究工作流
- 自动交易、写操作、实时交易闭环不属于 `#003` 范围，应另立交易系统 issue 推进
