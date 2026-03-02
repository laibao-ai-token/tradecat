---
title: "003-feature-trade-agent-development"
status: open
created: 2026-02-26
updated: 2026-03-02
owner: lixh6
priority: high
type: feature
---

# Trade Agent 开发（基于 tradeagent 分支）

## 背景

当前仓库已经具备数据采集、指标计算、信号检测与 TUI 展示能力，但缺少“可直接对话执行任务”的 Trade Agent 闭环。

用户目标是：
- 在 TUI 中直接发任务（行情查询、新闻检索、规则解释、排障）
- 通过 API 直连模型稳定执行
- 明确看到工具调用与结果来源，避免“看起来查了但无证据”

## 目标

交付一个可用的 Trade Agent MVP，满足“能用、可观测、可切模型、可回溯”。

### MVP 范围

1. **API 直连优先**：默认走 OpenAI-compatible API，不依赖 CLI relay。
2. **模型切换可用**：`/model` 切换后立即生效，状态栏与实际请求一致。
3. **工具链可观测**：工具调用在 TUI / 日志 / session history 三处可追溯。
4. **结果可信标注**：回答中标注是否调用工具、调用了哪些工具、时间戳。

## 非目标（本期不做）

- 不重构 129 规则引擎核心逻辑
- 不引入复杂多 Agent 编排
- 不做策略自动交易执行（仅查询/分析/解释）

## 开发里程碑

### Phase 1：Agent 运行主链路（P0）

- [ ] 明确 provider 抽象：OpenAI-compatible `base_url + api_key + model`
- [ ] 移除/关闭默认 CLI relay 路径（仅保留可选 fallback）
- [ ] `/model` 切换后的实际请求模型与 UI 显示一致
- [ ] 新会话 `/new` 后上下文正确重置

### Phase 2：工具调用与可观测性（P0）

- [ ] 实时类意图（天气/价格/新闻）默认优先触发工具
- [ ] 写入结构化工具事件：`tool_start / tool_update / tool_end`
- [ ] TUI 可看到工具卡片或事件摘要
- [ ] session `jsonl` 可回溯同一轮工具调用轨迹

### Phase 3：Trade 场景增强（P1）

- [ ] 增加行情查询模板（BTC/ETH 等）
- [ ] 增加信号解释模板（读取规则/指标后再回答）
- [ ] 增加失败兜底文案（明确“未调用工具/工具失败”）

## 验收标准

- [ ] `openclaw tui` 启动后无需额外手工 relay，也可直接完成 API 请求
- [ ] `/model` 在 `glm-4.6` 与 `qwen3-max` 间切换成功率 100%（连续 10 次）
- [ ] 实时查询任务工具触发率 ≥ 95%（普通模式），`strict_tool_mode=on` 时 100%
- [ ] 同一轮请求在 TUI、日志、session 文件可交叉验证
- [ ] 回答中始终包含来源标注（工具/非工具）

## 相关 Issue

- Related: #004（后续可接新闻 feed）

## 执行约束

- 基线分支：`tradeagent`
- 先本地验证后再提交
- 任何 commit / push 需先经 owner 明确同意

## 建议任务拆分（可直接开工）

1. [x] 代码定位：provider 选择、模型切换、会话重置入口（已完成初步摸底）
2. [ ] 打通 API 直连路径并补齐配置校验
3. [ ] 增加工具事件统一落盘与展示
4. [ ] 增加最小 E2E（天气/价格/新闻）
5. [ ] 更新 issue 进展与回归结果

## 进展记录

### 2026-03-01

- [x] 将 #003 从“AI 信号增强”重定义为“Trade Agent 开发”
- [x] 已引入 openclaw 代码基线（`repository/openclaw`）
- [x] 已补齐 relay 启停脚本与 TUI 启动脚本（用于过渡验证）
- [ ] 待完成：从 relay 过渡到“API 直连优先”主链路
- [ ] 待完成：工具调用可观测性闭环（TUI/日志/session）

### 2026-03-02

- [x] 进度梳理对齐：按“只推进 #003”收敛范围
- [x] 取消 #005 依赖：工具可观测性任务并入 #003 直接推进
- [x] 当前状态重置：以 API 直连优先为唯一主线，relay 方案不再作为依赖

## 当前进度评估（2026-03-01 快照）

### 已完成（约 30%）

- 基线确定：`tradeagent` 分支推进，Issue 目标与边界已明确
- 代码基线：接入 openclaw 子仓（submodule）用于并列开发
- 过渡能力：已有 relay 脚本可跑通初步联调链路

### 进行中（约 20%）

- TUI 并列形态对齐（Trade TUI + Agent 面板）方向已明确
- provider/model/session 的入口已完成初步定位，待统一抽象

### 未开始 / 关键缺口（约 50%）

- API 直连主链路未完成（目前仍偏 relay 过渡态）
- `/model` 切换一致性与 `/new` 会话重置缺少回归证明
- 工具调用事件尚未形成结构化落盘与三端可观测闭环
- E2E 回归与验收数据（触发率/成功率）尚未建立

## 下一步推进计划（建议）

### Sprint A（P0，1~2 天）：主链路收敛

- [ ] 固化 provider 抽象：`base_url + api_key + model`
- [ ] 默认切到 API 直连，relay 改为 fallback（显式开关）
- [ ] 完成 `/model` 与 `/new` 两条关键路径自测（至少各 10 次）

### Sprint B（P0，2~3 天）：可观测闭环

- [ ] 统一事件模型：`tool_start / tool_update / tool_end / tool_error`
- [ ] 同步写入：TUI 事件区 + 文件日志 + session jsonl
- [ ] 回答强制来源标注：是否调用工具、工具名、时间戳

### Sprint C（P1，1~2 天）：交易场景模板

- [ ] 行情查询模板（BTC/ETH）
- [ ] 信号解释模板（读取规则/指标后再回答）
- [ ] 失败兜底模板（明确失败原因与下一步建议）

## 风险与阻塞

- `repository/openclaw` 当前以子模块形式接入，后续需确定是否长期保留子模块管理方式
- 新增能力跨 TUI 与 Agent，若缺少最小 E2E 用例，容易出现“UI显示成功但链路未真实调用”
