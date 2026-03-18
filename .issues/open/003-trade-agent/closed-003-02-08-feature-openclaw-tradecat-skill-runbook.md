---
title: "003-02-08-feature-openclaw-tradecat-skill-runbook"
status: closed
created: 2026-03-11
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: medium
type: feature
---

# Trade Agent 003-02-08：`openclaw` 调用 `TradeCat` 能力桥的 skill / runbook

## 背景

`#003-02-04 ~ #003-02-07` 负责把 TradeCat 能力收敛成可调用命令，但右侧最终是否能用，还取决于：

- `openclaw` 侧怎么发现这些命令
- 什么时候调用
- 命令失败时如何降级

因此需要一份最小可复现的 skill / runbook，把能力桥真正接到右侧工作流里。

## 目标

提供一份最小 `openclaw` skill 接入说明和 runbook，明确：

- quotes / signals / news / backtest summary 分别怎么调用
- 什么问题触发什么能力
- 成功 / 失败时如何判断

## 本期范围

1. 明确 `openclaw` 侧的接入位置（workspace skill / managed skill / runbook）
2. 给出四个能力的最小调用示例
3. 给出最小 prompt / tool 使用准则
4. 说明失败提示、超时、空结果的处理方式
5. 输出一份可复现 smoke 步骤

## 建议交付物

- 一份 skill 配置草案或等价 runbook
- 一份最小调用样例表
- 一份 blocker / fallback 说明

## 建议写入范围

- `repository/openclaw` 相关文档引用
- `docs/analysis/` 或 `docs/` 下 runbook
- 如确需本仓接 skill 模板，可新增到仓库内文档或脚本目录

## 依赖

- `#003-02-04`
- `#003-02-05`
- `#003-02-06`
- `#003-02-07`

## 非目标

- 不改 `openclaw` 内部 TUI 代码
- 不做自动交易
- 不做多轮复杂策略代理

## 验收标准

- [x] skill / runbook 路径清晰可复现
- [x] quotes / signals / news 至少三类能力有最小调用示例
- [x] 说明成功、失败、空结果和超时的判定方式
- [x] 形成一份能直接支撑 `#003-06` 的操作说明

## 为什么适合 Sym

- 以文档、样例、运行手册为主
- 依赖清晰
- 适合在能力命令落地后单独收口

## Sym 派单备注

适合直接同步到 Linear / Symphony，按“文档 + runbook + 最小 skill 草案”交付，不要求在本单里改 `openclaw` 内部 TUI 代码。

### Sym 任务边界

- 只围绕这四个已落地命令展开：
  - `scripts/tradecat_get_quotes.py`
  - `scripts/tradecat_get_signals.py`
  - `scripts/tradecat_get_news.py`
  - `scripts/tradecat_get_backtest_summary.py`
- 目标是让右侧原生 `openclaw tui` 知道“什么时候调用哪个命令”
- 不要把任务扩成“重新设计 Agent 架构”或“重做 openclaw skill 系统”

### 建议交付物

- 一份可直接复现的 runbook 文档
- 一份最小 skill / prompt 草案
- 一张“问题 -> 调用哪个 TradeCat 命令”的映射表
- 一张“成功 / 空结果 / 超时 / 失败”的处理说明表
- 一组最小 smoke 步骤，能直接支撑 `#003-06`

### 建议写入范围

- 优先写到 `docs/analysis/` 或 `docs/`
- 如确实需要，可补一个仓库内可复用的 skill 模板草案
- 不要修改 `repository/openclaw` 上游源码

### 明确约束

- 右侧 UI 保持原生 `openclaw tui`
- 左侧 UI 保持原生 `TradeCat TUI`
- 集成只发生在能力层，不做 UI 级融合
- 不新增自动交易动作
- 不修改这四个命令的核心 JSON 契约，除非发现明确 blocker，并把原因写清楚

### 完成判定

- 文档里能明确 `openclaw` 侧配置位置或等价接入位置
- 四个命令至少有三类能力给出最小调用示例
- 成功 / 空结果 / 超时 / 失败的判定方式清楚
- 文档能让人工继续执行 `#003-06`，即视为本单完成

### 阻塞处理

- 若当前环境下 `openclaw` skill 放置路径、配置入口或调用方式无法确认，不要空转
- 直接输出 blocker、候选路径和建议 fallback（例如先 runbook，后 skill）
- 本单允许以“阻塞但可执行的 runbook 草案”方式收口

## 相关 Issue

- Parent: `#003`
- Parent: `#003-02`
- Depends on: `#003-02-04`, `#003-02-05`, `#003-02-06`, `#003-02-07`
- Feeds: `#003-06`

## 关闭边界

- skill / runbook 可复现、能支撑最小 E2E 即可关闭
- 若被 `openclaw` 配置路径或命令接口阻塞，也要明确 blocker 后收口

## 进展记录

### 2026-03-11

- [x] 已从 `#003-02` 拆分为独立子单，便于在命令能力落地后单独收口
- [ ] 待开始：确定 `openclaw` 侧的 skill 放置方式
- [ ] 待开始：整理最小调用样例与 fallback 说明

### 2026-03-12

- [x] 已补充：可直接用于 Linear / Symphony 派单的执行备注
- [x] 已明确：本单只围绕四个已落地命令做 runbook / skill 草案，不扩到 `openclaw` 上游改造
- [x] 已将 `TRA-18` 的有效产物回收并落到主仓：`docs/learn/openclaw_tradecat_skill_runbook.md`
- [x] 已按当前主仓真实状态回填 runbook，而不是沿用过时 Symphony workspace 的 blocker 结论
- [x] 已用当前主仓直接 smoke 四个桥接命令：
  - `tradecat_get_quotes`: 成功
  - `tradecat_get_signals`: 成功
  - `tradecat_get_news`: 成功但空结果
  - `tradecat_get_backtest_summary`: 使用明确 `run_id` 成功
- [x] 已把本文第 4 节草案翻译成可安装的 workspace skill 模板：`skills/tradecat-bridge/SKILL.md.template`
- [x] 已补充一键安装脚本：`scripts/install_openclaw_tradecat_skill.sh`
- [x] 已验证：`openclaw skills list` / `openclaw skills info tradecat-bridge` 可发现 workspace skill
- [x] 已决定：`tradecat-bridge` 的“实际使用验证”并入 `#003-06` 最小 E2E 收口，不再在本单重复拆验

## 当前结论

本单已经完成“runbook + 可安装 workspace skill + 发现验证”的交付。最终的真实调用验证并入 `#003-06` 最小 E2E，因此本单按“能力桥接入说明已就绪”关闭。
