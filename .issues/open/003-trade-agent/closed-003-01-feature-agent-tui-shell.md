---
title: "003-01-feature-agent-tui-shell"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent P0-0：双 TUI 工作台骨架（TradeCat + openclaw 原生 TUI）

## 背景

父任务 `#003` 的最初方向，是在 `TradeCat` 内部自绘一个右侧 Agent 面板，让用户在同一套 TUI 里同时看到行情和 Agent。

但结合当前仓库现状、`openclaw` 已有能力和最新讨论，这条路线的收益已经明显下降：

- `openclaw` 已经有自己的原生 TUI、真实 session/model/tool 语义和 Gateway 主链路
- `TradeCat` 当前右侧 `Agent Shell` 只是本地 demo 骨架，并不是真正的 `openclaw` 运行时
- 若继续在 `TradeCat` 里复刻一套“像 openclaw 的 UI”，后续会持续承受语义漂移和维护成本

因此，本 issue 从 2026-03-11 起调整目标：

- 不再以“TradeCat 内嵌自绘 Agent 面板”为主目标
- 改为在一个终端工作区中，**左侧运行原生 `TradeCat TUI`，右侧运行原生 `openclaw tui`**
- `TradeCat` 负责交易/行情/信号能力，`openclaw` 负责 Agent UI 与运行时
- 通过启动器把两者编排到同一个 terminal workspace 中，而不是把一个 TUI 伪装成另一个 TUI

本 issue 的参考形态，仍以当前讨论中的截图 `[Image #1]` 为准，但语义改为：

- 左栏：`TradeCat` 原生 TUI
- 右栏：`openclaw` 原生 TUI
- 两者是并排协作，不是嵌套复刻

## 目标

交付一个稳定的“双 TUI 工作台”骨架，让用户能用一条命令启动：

默认目标形态：

- 左侧：`TradeCat` 当前行情/图表/信号区域
- 右侧：`openclaw tui` 的原生会话界面
- 两边在同一个 terminal workspace 内并排展示
- 用户不需要自己手工分别启动和排版

## 本期范围

1. 确定工作台编排方式：
   - 优先 `tmux`
   - 如有必要可支持 `zellij`
   - 不把 curses/TUI 嵌进另一个 curses/TUI 进程
2. 提供一个最小 launcher：
   - 例如 `scripts/launch_trade_workbench.sh`
   - 或顶层 `scripts/start.sh run-workbench`
3. 明确左右 pane 的职责：
   - 左：`TradeCat TUI`
   - 右：`openclaw tui`
4. 明确最小启动参数与环境传递：
   - `TradeCat` view / symbols / refresh
   - `openclaw` gateway url / token / session
5. 补一份最小 runbook：
   - 如何启动
   - 如何切 pane
   - 如何退出
   - 哪边负责什么
6. 把当前 `TradeCat` 自绘 `Agent Shell` 重新定义为：
   - 过渡期 demo / fallback
   - 不是最终交付形态

## 推荐实现方向

建议按“一个终端 workspace，两个原生 TUI 进程”的方式推进：

- 整体参考：当前讨论截图 `[Image #1]`
- 左栏：沿用现有 `TradeCat` TUI 主内容（自选列表 / 图表 / 信号等）
- 右栏：直接运行 `openclaw tui`
- 二者通过 terminal multiplexer 编排，不共享 curses 渲染树

第一版建议：

- 新建 launcher 脚本负责：
  - 检查 `tmux` / `openclaw` / `TradeCat` 是否可执行
  - 创建 session / panes
  - 左 pane 启动 `TradeCat`
  - 右 pane 启动 `openclaw tui`
- pane 标题、启动参数、退出行为可先简单，不需要一开始就做复杂联动

这一版不再要求 `TradeCat` 右栏自己承载：

- session / model / tool 状态栏
- slash 命令输入框
- 工具卡片 / streaming 消息流

## 非目标

- 不要求本 issue 内把 `TradeCat` 能力已经接成 `openclaw` tool
- 不要求本 issue 内完成 `Gateway` 联调
- 不要求把 `TradeCat` 和 `openclaw` 合并成单进程
- 不做 curses 级别的 TUI 嵌套
- 不修改 `repository/openclaw`
- 不重画 `TradeCat` 左侧主行情 TUI
- 不把当前 `TradeCat` 自绘 `Agent Shell` 当成最终 UI 继续深挖

## 验收标准

- [x] 有一条命令可以启动双 TUI 工作台
- [x] 左侧运行的是原生 `TradeCat TUI`
- [x] 右侧运行的是原生 `openclaw tui`
- [x] 两边能在同一个 terminal workspace 中稳定并排显示
- [x] 启动、切 pane、退出的最小 runbook 明确
- [x] 当前 `TradeCat` 自绘 `Agent Shell` 已被明确标注为过渡实现，而非最终形态
- [x] 若 `tmux` / `openclaw` / 环境参数存在 blocker，issue 中有明确降级方案或失败提示

## 参考截图

- 参考：当前讨论中的 `[Image #1]`
- 解释：左侧为 `TradeCat` 现有行情 TUI，右侧为 `openclaw` 原生 TUI
- 约束：追求的是“同一个 terminal workspace 里的双工作区”，不是“一个程序把另一个程序的 UI 重画出来”

## 相关文件

- `scripts/`
- `services-preview/tui-service/scripts/start.sh`
- `services-preview/tui-service/src/tui.py`
- `repository/openclaw/docs/web/tui.md`
- `repository/openclaw/docs/cli/tui.md`

## 相关 Issue

- Parent: `#003`
- Next: `#003-02`

## 关闭边界

- 双 TUI launcher 可启动、可说明、可演示即可关闭
- 如发现 `tmux` / `zellij` / `openclaw tui` 在当前环境下存在结构性 blocker，也必须输出 blocker 与替代方案后再收口

## 进展记录

### 2026-03-09

- [x] 已对齐：左侧 `TradeCat` 基本不动，Agent 工作区需要与交易区并排出现
- [x] 已补充：布局参考当前讨论截图 `[Image #1]`
- [x] 已先落过一版 `TradeCat` 内部自绘 `Agent Shell` demo，用于验证右侧空间和交互槽位

### 2026-03-11

- [x] 已重新评估：继续在 `TradeCat` 内复刻 `openclaw` TUI，维护成本过高，且真实语义仍归 `openclaw`
- [x] 已对齐：最终目标改为“左 `TradeCat` + 右 `openclaw tui`”的双 TUI 工作台
- [x] 已明确：当前 `services-preview/tui-service/src/tui.py` 中的右侧 `Agent Shell` 仅视为本地占位 / fallback，不再作为最终交付形态
- [x] 已派单到 Linear / Symphony：`TRA-12`（`[003-01] 双 TUI 工作台骨架（TradeCat + openclaw 原生 TUI）`）
- [x] 已落地 launcher：`scripts/launch_trade_workbench.sh`
- [x] 已验证：可通过 `tmux` 启动左右双 pane
- [x] 已补最小降级说明：未安装 `tmux` 时输出 manual fallback
- [x] 已收口：pane 焦点切换、退出方式和 fallback runbook 已同步到 `README.md` / `README_EN.md` / `AGENTS.md`

### 2026-03-12

- [x] 已人工确认双 TUI 工作台 launcher、fallback 和文档说明均已到位，本单正式关闭
