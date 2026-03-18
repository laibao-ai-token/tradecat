---
title: "003-02-03-feature-agent-gateway-cli-smoke-matrix"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: medium
type: feature
---

# Trade Agent 003-02-03：Gateway / CLI fallback smoke 验证矩阵

## 背景

`#003-02` 最后一定要有可复现的 smoke test，但这部分可以提前拆出来，作为更适合 Sym 的验证型任务。

它的产物不是核心代码，而是一份能反复复用的验证矩阵：

- Gateway 正常时验证什么
- Gateway 异常时验证什么
- CLI fallback 开启后验证什么
- 什么情况应该直接失败而不是“假成功”

## 目标

整理一份最小但可信的 `Gateway / CLI fallback` 验证矩阵，供后续联调与回归反复复用。

## 本期范围

1. 覆盖以下最小场景：
   - session 列表加载
   - history 加载
   - send message
   - models list
   - model patch
   - session reset
2. 增加异常场景：
   - Gateway 不可达
   - 请求超时
   - 返回异常
   - CLI fallback 开 / 关 两种情况
3. 为每个场景给出：
   - 前置条件
   - 操作步骤
   - 预期结果
   - 证据位置（TUI / 日志 / session）
4. 输出一份可复用的 pass/fail 记录模板

## 为什么适合 Sym

- 这是典型的验证矩阵 / 冒烟方案任务
- 产物是步骤表和结论，不强依赖主仓大改
- 后续即使实现迭代，矩阵也能复用做回归

## 非目标

- 不要求本 issue 内完成 adapter 实现
- 不做完整 E2E
- 不修改 `repository/openclaw`

## 验收标准

- [x] 有覆盖主链路和 fallback 的 smoke 场景表
- [x] 每个场景都有前置条件、步骤、预期结果、证据位置
- [x] 有统一的 pass/fail 模板
- [x] 可直接作为 `#003-02` 联调和 `#003-03` 回归的输入

## 相关 Issue

- Parent: `#003`
- Supports: `#003-02`
- Related: `#003-03`

## 关闭边界

- smoke 矩阵和记录模板齐备即可关闭

## 交付物

- 主文档：`docs/analysis/gateway_cli_fallback_smoke_matrix.md`
- 归档（Sym workspace 快照）：`artifacts/symphony-archive/TRA-9/20260310T130458Z/`

## Blocker（需要在 #003-02 联调时补齐）

- `B1`：`repository/openclaw` 未初始化（gitlink 空工作树），导致无法确认真实命令/日志/session 存储结构。
- `B2`：缺少可复用的 Gateway stub / timeout / malformed response 注入脚本，异常场景无法直接执行。
- `B3`：缺少可对照的真实运行证据样例（因此文档中不伪造 pass/fail）。

## 进展记录

### 2026-03-09

- [ ] 待开始：列出 happy path 与 fallback path 最小场景
- [ ] 待开始：输出 smoke 记录模板

### 2026-03-10

- [x] 已产出 smoke matrix + pass/fail 模板：`docs/analysis/gateway_cli_fallback_smoke_matrix.md`
- [x] 已明确当前 blocker，避免在 `#003-02` 联调阶段“假通过”

### 2026-03-12

- [x] 已人工确认该参考验证矩阵可以正式收口，保留在 `closed-*` 文件名下作为历史材料
