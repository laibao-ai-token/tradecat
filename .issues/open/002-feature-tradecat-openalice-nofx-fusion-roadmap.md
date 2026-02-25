# [Refactor] 主分支迁移跟踪（main -> tradeagent）

**Issue ID**: #002  
**Status**: Open（迁移完成，跟踪中）  
**Priority**: High  
**Created**: 2026-02-22  
**Updated**: 2026-02-23  
**Assignee**: Unassigned  
**Labels**: refactor, branch, migration, tradeagent

---

## 目标

本 Issue 专门跟踪一次分支迁移：将原先以 `main` 为主的工作基线，切换到 `tradeagent` 分支持续推进。

> 注：本 Issue 不再承接“融合路线设计”内容，只跟踪分支迁移事实与后续收尾。

## 当前结论（已确认）

1. 当前工作分支已切换到 `tradeagent`。  
2. `origin/tradeagent` 已存在并可作为后续迭代基线。  
3. `origin/main` 作为历史基线保留，不作为当前主开发线。  
4. 历史口误 `translation` 已统一为 `tradeagent`。

## 迁移快照（本地核验）

- 当前分支：`tradeagent`（HEAD）  
- 跟踪分支：`origin/tradeagent`  
- 与 `origin/main` 差异：`behind=0 / ahead=2`  
- 共同基点（merge-base）：`966ec11`

## In Scope（本 Issue）

- [x] 明确本次迁移对象：`main -> tradeagent`
- [x] 确认本地/远端分支关系可用
- [x] 固定后续工作语义：默认基线为 `tradeagent`
- [x] 修正文档语义歧义（translation -> tradeagent）

## Out of Scope（本 Issue）

- [x] 不在本 Issue 内实现业务功能开发
- [x] 不在本 Issue 内重做融合方案
- [x] 不在本 Issue 内做实盘/回测策略评估

## 待收尾项（轻量）

- [ ] 在后续新 issue 中标注“基于 tradeagent 开发”
- [ ] 若团队需要，再决定是否将远端默认分支从 `main` 调整为 `tradeagent`（仓库设置项）

## 验收标准（DoD）

- [x] 分支迁移事实清晰可追踪（目标、路径、结果）
- [x] 本地与远端关系清晰（tradeagent 可持续开发）
- [x] 语义统一（不再使用 translation 指代当前分支）
- [ ] 团队后续 issue 默认引用 `tradeagent` 作为基线

## 进展记录

### 2026-02-22 23:10

- [x] 创建本地 issue #002
- [x] 初版记录迁移背景与目标

### 2026-02-23 00:15

- [x] 复核 issue 范围，去除与当前任务无关的实现内容
- [x] 明确本 issue 仅跟踪“main -> tradeagent”迁移

### 2026-02-23 00:30

- [x] 二次确认：当前应使用 `tradeagent`（非 `translation`）
- [x] 写入迁移快照（HEAD/remote/ahead-behind/merge-base）

## 备注

- 当前仓库仍有本地未提交改动，本 issue 仅作为迁移跟踪，不代表这些改动已推送远端。
