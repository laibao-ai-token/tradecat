---
title: "006-review-ca4b0850-workbench-launcher-skill-runbook"
status: review
created: 2026-03-17
updated: 2026-03-18
owner: lixh6
priority: medium
type: review
linear: TRA-30
commit: ca4b085094c6b94fd37241561fd0b172be657be0
---

# [Review-006][ca4b0850] codex review workbench launcher + skill runbook

## 提交信息

- Commit: `ca4b085094c6b94fd37241561fd0b172be657be0`
- Subject: `feat(workbench): add trade-agent launcher and skill runbook`

## 执行记录

1. 已执行：`codex review --commit ca4b0850`
2. 为获取稳定输出补充执行：`codex review --commit ca4b0850 -c model_reasoning_effort=low`
3. 建议验证命令执行结果：
   - `./scripts/launch_trade_workbench.sh --dry-run` -> `Permission denied`
   - `./scripts/launch_trade_workbench.sh --print-manual` -> `Permission denied`
   - `./scripts/install_openclaw_tradecat_skill.sh` -> `Permission denied`

## 检查重点核对

- `tmux / openclaw` 缺失降级提示：
  `scripts/launch_trade_workbench.sh` 内已有明确提示与降级文案（`require_tmux` + `print_manual_fallback`，以及 `build_openclaw_cmd` 的缺失提示）。
- 可重复执行与已有会话保护：
  通过 `tmux has-session` 检查后仅在不存在时创建会话，已存在会话会直接 attach，不会重复 split。
- runbook 与参数/环境变量一致性：
  `docs/learn/openclaw_tradecat_skill_runbook.md` 与 `scripts/install_openclaw_tradecat_skill.sh` 的安装路径与命令一致，未发现参数名冲突。

## Review 结果（回填）

### Findings

1. **[P1] 两个新脚本缺少可执行权限，文档主路径不可直接运行**
   - `scripts/install_openclaw_tradecat_skill.sh` 在该提交中为 `100644`，按文档 `./scripts/install_openclaw_tradecat_skill.sh` 执行会报 `Permission denied`。
   - `scripts/launch_trade_workbench.sh` 在该提交中为 `100644`，按文档 `./scripts/launch_trade_workbench.sh ...` 执行会报 `Permission denied`。

2. **影响范围**
   - 新增工作台启动链路与 skill 安装链路在 fresh checkout 下不可直接使用。
   - runbook / AGENTS 中给出的 `./scripts/...` 入口会被权限问题阻断。

3. **复现证据**
   - `./scripts/launch_trade_workbench.sh --dry-run` -> `/bin/bash: ... Permission denied`（exit 126）
   - `./scripts/launch_trade_workbench.sh --print-manual` -> `/bin/bash: ... Permission denied`（exit 126）
   - `./scripts/install_openclaw_tradecat_skill.sh` -> `/bin/bash: ... Permission denied`（exit 126）

### 建议修复

- 将以下文件以 `100755` 提交：
  - `scripts/install_openclaw_tradecat_skill.sh`
  - `scripts/launch_trade_workbench.sh`
- 修复后重跑：
  - `./scripts/launch_trade_workbench.sh --dry-run`
  - `./scripts/launch_trade_workbench.sh --print-manual`
  - `./scripts/install_openclaw_tradecat_skill.sh`

## 闭环更新（2026-03-18）

- 修复已落地：
  - `scripts/install_openclaw_tradecat_skill.sh` 已设置为可执行。
  - `scripts/launch_trade_workbench.sh` 已设置为可执行。
- 验证结果：
  - `./scripts/launch_trade_workbench.sh --print-manual` 可正常输出降级命令。
  - `./scripts/install_openclaw_tradecat_skill.sh` 可正常执行并输出安装结果。
- 结论：`006` 的 P1 finding 已闭环。
