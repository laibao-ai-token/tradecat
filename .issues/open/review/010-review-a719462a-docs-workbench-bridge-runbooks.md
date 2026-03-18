---
title: "010-review-a719462a-docs-workbench-bridge-runbooks"
status: review
created: 2026-03-18
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-34
---

# Review 010 - a719462a（docs workbench + bridge runbooks）

## 基本信息

- Commit: `a719462aa4b648983156ec8eaaab189282b127f0`
- Subject: `docs: add trade-agent workbench and bridge runbooks`
- 范围：`README.md`、`README_EN.md`、`AGENTS.md`、`docs/learn/base.md`、`docs/analysis/*`

## 执行记录

1. 已执行 `codex review --commit a719462a`。在当前环境中该命令持续无输出，使用 `timeout 180` 包装后以退出码 `124` 超时结束，未产出可用审查文本。
2. 按检查重点补做人工 review：
   - 命令参数一致性（脚本 help / 运行验证）
   - 中英文 README 同步性
   - 分析文档结论可追溯性

## Review 结果（回填）

### 结论

- 结果：`Changes Requested`
- 说明：README/README_EN 新增 runbook 主体内容基本同步，但存在 2 个需要修正的问题（1 个高优先级证据问题 + 1 个命令可执行性问题）。

### Findings

1. **[High] 分析文档结论与仓库内证据不一致，不可追溯**
   - 文件：`docs/analysis/agent_observability_tui_log_session.md`
   - 问题：
     - `Repository Evidence` 表中多条路径在当前仓库不存在：
       - `.issues/open/003-trade-agent/003-feature-trade-agent-development.md`
       - `.issues/open/003-trade-agent/closed-003-04-feature-agent-tool-events-schema.md`
       - `.issues/open/003-trade-agent/closed-003-02-01-feature-agent-gateway-contract-mapping.md`
       - `.issues/open/003-trade-agent/closed-003-02-02-feature-agent-transport-state-fallback-mapping.md`
       - `repository/openclaw/docs/gateway/security/index.md`
       - `repository/openclaw/src/tui/gateway-chat.ts`
       - `repository/openclaw/test/helpers/gateway-e2e-harness.ts`
     - 文档摘要同时写了“openclaw repo 内已能确认 ...”，但本仓库 `repository/openclaw` 当前为空目录（未初始化子模块），无法支撑该结论。
   - 影响：分析结论缺少可复核证据，读者无法按文档定位和复现。
   - 建议：要么修正引用到当前真实路径，要么明确“需初始化 submodule 后再验证”，并把结论降级为 `Blocked/待验证`。

2. **[Medium] Runbook 命令与脚本可执行状态不一致**
   - 文件：`README.md`、`README_EN.md`、`AGENTS.md`（涉及 workbench / skill 安装命令）
   - 问题：
     - 文档写法为：
       - `./scripts/launch_trade_workbench.sh`
       - `./scripts/install_openclaw_tradecat_skill.sh`
     - 但仓库中这两个脚本均为 `100644` 非可执行文件，直接运行返回 `Permission denied`（exit `126`）。
   - 影响：按文档直接复制命令会失败，首轮联调体验不一致。
   - 建议：二选一保持一致：
     - 给脚本加执行位（`100755`），保留 `./scripts/...` 写法；或
     - 文档统一改为 `bash ./scripts/...`。

### 检查重点结果

- 文档命令与当前脚本参数：
  - `tradecat_get_quotes.py` / `tradecat_get_signals.py` / `tradecat_get_news.py` / `tradecat_get_backtest_summary.py` 参数与 README/AGENTS 示例一致。
  - `launch_trade_workbench.sh --print-manual` 参数存在并可工作（通过 `bash` 调用验证）。
  - 但存在上文“脚本不可执行位”问题。
- 中英文 README 同步：
  - 新增 “Dual TUI Workbench” 与 “Read-Only Bridge Commands” 两大块内容整体同步，未发现明显语义冲突。
- 分析文档结论可追溯性：
  - `gateway_cli_fallback_smoke_matrix.md` 的 blocker 口径与当前仓库状态一致。
  - `agent_observability_tui_log_session.md` 存在证据链断裂（见 High）。

## 验证记录（关键命令）

```bash
# 1) 指定 commit 的 codex review（无输出，最终超时）
timeout 180 codex review --commit a719462a
# -> exit 124

# 2) 关键词命中验证
rg -n "launch_trade_workbench|tradecat_get_quotes|tradecat_get_signals|tradecat_get_news|tradecat_get_backtest_summary" \
  README.md README_EN.md AGENTS.md docs/learn/base.md

# 3) workbench 手工降级命令验证（可执行）
bash ./scripts/launch_trade_workbench.sh --print-manual

# 4) 直接执行命令验证（按文档写法失败）
./scripts/launch_trade_workbench.sh
./scripts/install_openclaw_tradecat_skill.sh
# -> Permission denied (exit 126)
```

## 闭环更新（2026-03-18）

- 已修复 High：`docs/analysis/agent_observability_tui_log_session.md`
  - `Repository Evidence` 中 parent issue 路径已修正为现存文件：
    `.issues/open/003-trade-agent/closed-003-feature-trade-agent-development.md`。
  - 并补充说明：若本地未初始化 `repository/openclaw`，需要先同步子仓库再验证相关证据路径。
- 已修复 Medium：workbench/skill 脚本已为可执行（`100755`），文档中的 `./scripts/...` 写法可直接执行。
- 验证：
  - 证据路径存在性检查：7/7 命中（含 `.issues/open/003-trade-agent/closed-*` 与 `repository/openclaw/*`）。
  - `./scripts/launch_trade_workbench.sh --print-manual` 可正常执行。
  - `git ls-files --stage scripts/launch_trade_workbench.sh scripts/install_openclaw_tradecat_skill.sh` 显示均为 `100755`。
