# [Feature] ETF 自动驾驶选基（国内基金页）

**Issue ID**: #001  
**Status**: Open  
**Priority**: High  
**Created**: 2026-02-19  
**Updated**: 2026-02-22  
**Assignee**: Unassigned  
**Labels**: feature, enhancement, etf, china-fund, tui

---

## 目标（对齐版）

在**指定领域**内（不是全市场）自动筛选 ETF，输出 TopN 候选、理由和风险提示，给选基提供决策辅助。

## 需求范围

### 本期做（In Scope）

1. 自动驾驶领域候选池筛选（固定领域，不扩展到其他主题）
2. 评分排序（趋势 + 动量 + 流动性 + 风险）
3. 输出 TopN + 原因标签 + 风险等级
4. TUI 展示策略标签、版本、更新时间、推荐清单
5. 30 天离线评估（只评估当前领域）

### 本期不做（Out of Scope）

1. 自动下单
2. 仓位管理与实盘风控执行
3. 跨资产联合优化

## 输入与输出

### 输入

- `domain`：领域名（必填，当前为 `自动驾驶`）
- `universe`：该领域 ETF 列表
- `top_n`：输出数量（默认 5）
- `weights`：评分权重
- `thresholds`：风险阈值

### 输出

- TopN ETF 列表
- 每只 ETF 的总分/子分
- 风险等级（LOW/MED/HIGH，UI 显示低/中/高）
- 理由标签（至少 2 条）
- 更新时间、策略版本

## MVP 规则（先简单可解释）

`score_total = 0.35*trend + 0.25*momentum + 0.20*liquidity + 0.20*risk_adjusted`

- `trend`：趋势方向
- `momentum`：强弱变化
- `liquidity`：成交活跃度
- `risk_adjusted`：波动/回撤惩罚

## 自动驾驶领域（当前约束）

- 市场范围：默认国内场内 ETF（`SH/SZ + 6 位`），支持混入场外基金（6 位代码）
- 领域范围：自动驾驶产业链（整车智能化、激光雷达、智能座舱、车规芯片、汽车电子）
- 评估窗口：近 30 天
- 输出目标：Top5 候选

## TUI 最小展示

- 策略标签：`ETF-AUTO-V1`
- 策略版本：`v1.x`
- 领域名 + TopN 推荐榜
- 每只 ETF 的风险等级 + 理由标签
- 数据更新时间

## 实施计划（精简）

### Phase 1：设计

- [x] 定义领域池与评分口径
- [ ] 定义配置结构（YAML）

### Phase 2：实现

- [x] 实现评分排序模块
- [x] 输出理由标签与风险等级
- [x] 接入 TUI 展示

### Phase 3：验证

- [ ] 30 天离线评估
- [ ] 与等权基准做对比

### Phase 4：结果一致性与可解释（当前推进）

- [ ] 统一“左侧候选序”和“右侧模型排名”的口径说明（避免误读为同一排序）
- [ ] 基金页补充 MRank/sRank 含义提示（页内文案 + 一行帮助说明）
- [ ] Top5 展示切换为“领域内 Top5（自动驾驶）”并保留全池榜单入口（避免芯片主题干扰）
- [ ] 选票信息固定输出“编号+代码+名称+角色（主选/备选/观察）”

## 验收标准（DoD）

- [x] 能稳定输出指定领域 TopN
- [x] 每只 ETF 至少 2 条解释标签
- [x] TUI 显示策略标签/版本/更新时间
- [x] 仅在 `market_fund_cn` 展示，不新增页面/view
- [ ] 参数支持 YAML 配置化
- [ ] 评估结果可复现
- [ ] 左右栏排序口径可解释（用户不再混淆候选序与模型排名）
- [ ] Top5 明确为自动驾驶领域榜（可与全池榜切换）

## 进展记录

### 2026-02-19 11:03

- [x] 创建本地 issue
- [x] 完成需求初稿

### 2026-02-19 11:22

- [x] 按“领域选基”目标精简 issue
- [ ] 进入 design doc 阶段

### 2026-02-19 12:08

- [x] 落地自动驾驶候选池与默认参数配置（tui-service profile）
- [x] 实现评分排序模块（趋势/动量/流动性/风险）
- [x] 基金页（market_fund_cn）右下接入 ETF 筛选摘要与 TopN
- [x] 支持场内ETF + 场外基金（6位代码）混合盯盘
- [ ] 30 天离线评估与等权对照

### 2026-02-20 09:30（SSU1）

- [x] 确认本期采用**递进式更新**：保留历史记录，新增增量进展，不覆盖旧结论
- [x] 完成当前“收敛快照”：
  - 线上页面仍为 `market_fund_cn`（不新增 view）
  - 生产评分仍是 `ETF-AUTO-V1`（趋势/动量/流动性/风险）
  - 策略标签/版本/更新时间/TopN 已在 TUI 展示
- [x] 完成 Cybercab 关联度分析产物（分析层，不直接替换生产打分）：
  - `artifacts/analysis/cybercab_fund_relevance_20260219.csv`
  - `artifacts/analysis/cybercab_fund_relevance_full_20260219.csv`
  - `artifacts/analysis/cybercab_fund_relevance_unique_20260219.csv`
  - `artifacts/analysis/cybercab_fund_relevance_expanded_20260219.csv`
- [x] 核验 ETF 相关测试通过（57 passed）
- [ ] 待完成：30 天离线评估 + 等权基准对照（Issue 关闭前必须完成）
- [ ] 待完成：决定是否把 Cybercab 打分并入生产策略（需要开关与回归验证）

### 2026-02-22 15:20（SSU2）

- [x] 仓库实现对齐确认（基于 commit `966ec11`）：
  - 已新增 `etf_profiles.py` + `etf_selector.py` 与对应单测
  - `market_fund_cn` 页面已接入策略摘要与 TopN（策略标签 `ETF-AUTO-V1`）
  - 已支持从 `artifacts/analysis/cybercab_fund_relevance_*.csv` 动态加载候选池（TopN 回退到静态 profile）
- [x] 回归核验更新：
  - ETF 定向测试通过：`tests/test_etf_profiles.py` + `tests/test_etf_selector.py`（5 passed）
  - TUI 全量测试通过：`services-preview/tui-service/tests`（65 passed）
- [ ] 现阶段仍未完成（保持 Open）：
  - YAML 配置化尚未落地（当前仍为代码内 profile/dataclass 配置）
  - 30 天离线评估与等权基准对照尚缺可复现脚本/报告闭环
- [ ] 备注：`artifacts/analysis/fund_recent_performance_20260220.csv` 与
  `artifacts/analysis/fund_recent_portfolio_20260220.csv` 已存在，但仓库内尚未形成与 Issue 验收直接绑定的可复现流程说明

### 2026-02-22 23:25（SSU3）

- [x] 与当前执行方式对齐：Issue 继续本地推进，不提交 Git
- [x] 确认下一阶段目标聚焦“自动驾驶领域一致性解释 + Top5 口径修正”
- [ ] 待完成：MRank/sRank/cnd 三者口径说明落地到基金页（避免“左1右22”理解冲突）
- [ ] 待完成：Top5 默认改为“自动驾驶领域榜”，全池榜改为可选视图
- [ ] 待完成：输出一份可直接用于实盘参考的“编号+名称+结论+风险”固定格式卡片

---

## 备注

- 本期定位：选基决策辅助，不做自动交易执行。
