---
title: "004-feature-news-feed-integration"
status: open
created: 2026-02-27
updated: 2026-03-08
owner: lixh6
priority: high
type: feature
---

# 新闻资讯能力集成（面向交易辅助）

## 背景

当前 TradeCat 已具备行情、指标、规则与 TUI 展示能力，但缺少稳定的“全球新闻/政策/公司事件”流。
目标不是做完整彭博终端，而是先做 **交易可用的新闻辅助层**（风险过滤 + 事件解释）。

## 本期目标（MVP）

在不引入高成本付费源的前提下，先打通：

1. 免费信源采集（全球宏观 + 公司事件 + 加密新闻）
2. 统一入库（复用现有 alternative schema）
3. TUI 只读展示（新闻列表 + 来源 + 时间 + 关联标的）
4. 交易侧先做“风险过滤器”，不直接自动下单

## 范围收敛（已定）

- 不新建独立 `news-service`（本期）
- 复用 `services-preview/markets-service` 的 provider + collector 架构
- 优先接“免费且条款清晰”的信源
- 情绪分析先规则基线，FinBERT/GPT 放后续

## 当前状态结论（2026-03-08）

- [x] **MVP 已达到可用状态**：新闻主采集链路、统一入库、TUI DB-first 展示、首版事件化/聚类去重均已跑通
- [x] **适合先投入使用**：当前版本已经可以作为 TradeCat 的实时资讯底座，供人工盯盘与后续 Agent 监控复用
- [ ] **增强项继续追踪**：后端事件层、稳定 symbol tagging、rss-proxy / persistent-cache、补充源池治理仍作为后续迭代

## 当前现状（已确认）

- [x] 数据库表底座已存在：`alternative.news_articles` / `alternative.news_sentiment` / `alternative.economic_calendar`
  - 见 `services-preview/markets-service/scripts/ddl/05_fundamental_alternative.sql`
  - 见 `services-preview/markets-service/scripts/migrate_5434.sql`
- [x] `markets-service` 已有 provider 注册与采集主链路，可承载新闻 provider
- [x] 已落地 MVP 新闻 Provider：`providers/rss`（RSS/Atom 拉取 + 标准化）
- [x] 已落地 news 入库写入器：写入 `alternative.news_articles`（按 `dedup_hash` 去重）
- [x] 已落地 CLI：`collect-news` / `collect-news-poll`
- [x] TUI 已接入真实新闻页（按键 `7`），支持新闻流 / 详情 / 搜索 / 分类 / 时间窗过滤
- [ ] GDELT/SEC/央行等专用 provider 尚未落地（后续按源逐个接入）

## 关键设计结论（2026-03-07 对齐版）

### 1. 总体策略：不是“整库合并”，而是“源合并 + 能力迁移”

对 `repository/worldmonitor` 的结论已经明确：

- 不整库并入 TradeCat
- 不迁移其 UI / 地图 / Tauri / 整站编排
- 只迁移对新闻链路最有价值的能力，并把可用源纳入候选源池

当前确定采用：

1. **源合并**：吸收 `worldmonitor` 中经过验证的 RSS 白名单，作为补充源池
2. **能力迁移**：迁移/复刻其 RSS 代理、失败冷却、缓存、聚类等关键能力
3. **主链路保留在 TradeCat**：统一落到 `markets-service -> alternative.news_articles -> tui-service`

### 2. 数据源定位：高频主链路与覆盖补充链路要分开

经过本轮验证，源需要分三层看：

| 层级 | 作用 | 当前来源 | 是否作为默认主链路 |
|---|---|---|---|
| L1 高频直连源 | 提供 7x24 快讯密度 | `J10 / THS / SINA / EM24 / CLS / GLH / WSCN / EEO` | 是 |
| L2 精选 RSS 源 | 提供稳定公开资讯补充 | `Benzinga / FXStreet / CNBC / Cointelegraph / SEC / GlobeNewswire / The Block` 等 | 是 |
| L3 广覆盖 RSS 源池 | 提供覆盖面、补盲和备用 | `worldmonitor` 白名单 | 否（只作为补充层） |

结论：

- **高频主链路** 不能依赖普通 RSS
- `worldmonitor` 更适合作为“源仓库 / 补盲层”，不适合作为主快讯引擎
- 默认不应把 `460+` RSS 全量直接塞进主链路，否则噪音高、交易相关性弱

### 3. worldmonitor 的实际价值（已验证）

已完成的客观验证结果：

- 直连情况下：约 `132` 个 RSS 可用，`362` 个抓不到，网络/出口问题明显
- 接入容器内代理后：约 `462` 个 RSS 可用，`0` 个抓不到，剩余主要是 `stale/empty`
- 但即使可用源大幅增加，整体密度仍只有：
  - 最近 `30s` 快照约 `5` 条
  - 稳态折算约 `2~3` 条 / `30s`

因此结论是：

- 代理解决的是“能不能抓到”
- 但不能单独解决“新闻密度够不够高”
- 密度问题的核心仍然是 **高频交易型源占比太低**，不是 RSS 总数不够

## 推荐接入方案（记录版）

### 方案总览

目标链路：

`源层 -> 采集层 -> 标准化/去重 -> 入库 -> 事件增强 -> TUI/Agent 消费`

对应到本项目：

1. **源层**
   - 高频直连：Jin10 / 同花顺 / 新浪 7x24 / 东方财富 / 财联社 / 格隆汇 / 华尔街见闻 / 经观
   - 精选 RSS：Benzinga / FXStreet / CNBC / Cointelegraph / SEC / GlobeNewswire / The Block
   - 补充 RSS：worldmonitor 白名单中与交易相关的高价值源

2. **采集层**
   - `markets-service` 作为唯一采集主链路
   - `tui-service` 逐步只做展示，不再长期承担主采集职责

3. **标准化/去重层**
   - 统一标准字段：`source / title / url / published_at / summary / content / symbols / categories`
   - 统一 `dedup_hash`
   - 高频源和 RSS 都落到同一篇章模型

4. **入库层**
   - 持续写入 `alternative.news_articles`
   - 后续在此基础上补 `news_sentiment` / `event_severity` / `symbol tagging`

5. **事件增强层**
   - 对重复资讯做聚类/事件化
   - 对标题做 symbol tagging 和 category 归类
   - 对高风险事件做强提醒或风险过滤

6. **消费层**
   - `tui-service` 新闻页优先读取统一新闻库
   - 后续 Agent 直接订阅数据库或统一查询接口

## 推荐模块迁移清单（来自 worldmonitor）

| 模块 | 作用 | 对 TradeCat 的价值 | 处理方式 |
|---|---|---|---|
| `repository/worldmonitor/api/rss-proxy.js` | RSS 代理、allowlist、relay 兜底、缓存控制 | 解决 RSS 直连失败问题 | 迁移思路 / 复刻能力 |
| `repository/worldmonitor/src/services/rss.ts` | RSS 抓取、失败冷却、缓存、持久化兜底 | 提升稳定性 | 迁移核心逻辑 |
| `repository/worldmonitor/src/services/clustering.ts` | 多条新闻聚成事件 | 解决重复刷屏 | 后续迁移 |
| `repository/worldmonitor/src/services/persistent-cache.ts` | 持久化缓存 | 降低重复抓取 / 提升容错 | 借思路实现 |
| `repository/worldmonitor/src/config/feeds.ts` | 大规模源清单与分层 | 作为候选源仓库 | 合并为候选配置，不全量启用 |

## 在 TradeCat 内的落地映射

| TradeCat 目标位置 | 计划内容 |
|---|---|
| `services-preview/markets-service/src/providers/rss/` | 增强现有 RSS provider：加入代理、失败冷却、按源状态统计 |
| `services-preview/markets-service/src/providers/` | 新增高频直连 provider（或 direct-news provider）承接 `direct://*` 源 |
| `services-preview/markets-service/src/storage/news_writer.py` | 保持统一写入入口，继续按 `dedup_hash` 去重 |
| `services-preview/markets-service/src/config.py` | 扩展新闻源配置、代理配置、并发/超时配置 |
| `services-preview/tui-service/src/` | 从“直接抓为主”逐步转向“统一库读取为主，直抓为辅/兜底” |

## 执行拆分（Sprint A / B / C）

### Sprint A（P0）：统一主采集链路

目标：把当前 TUI 直接抓取的高频源正式下沉到 `markets-service`，形成“统一采集 -> 统一入库 -> TUI 读取”的主链路。

任务：

- [x] 在 `markets-service` 中新增高频直连 provider（承接 `direct://*` 这类源）
- [x] 将 `J10 / THS / SINA / EM24 / CLS / GLH / WSCN / EEO` 标准化为统一 `NewsArticle`
- [x] 保持统一写入 `alternative.news_articles`，继续使用 `dedup_hash` 去重
- [x] 保留 TUI 直抓能力作为短期 fallback，不再作为长期主链路
- [x] 跑通最小闭环：`collect-news-poll -> DB -> TUI 资讯页`

验收：

- [x] `markets-service` 可以持续采集高频直连源并写库
- [x] TUI 至少支持切换到“读统一新闻库”模式
- [x] 默认主链路不依赖 demo RSS

### Sprint B（P0）：稳定性与可观测性

目标：补齐代理、超时、失败冷却、源健康状态，让链路能长期跑而不是“偶尔能用”。

任务：

- [x] 给 RSS provider 增加统一代理支持（优先复用现有 `HTTP_PROXY/HTTPS_PROXY`）
- [x] 增加按源超时、失败冷却、错误统计、最后成功时间
- [x] 给高频直连源也补齐相同的健康状态记录
- [x] 在 TUI 页头增加：`抓取周期 / 上次抓取 / 最新新闻 / 源健康`
- [x] 增加最小测试覆盖：默认源、代理配置、provider 结果标准化、健康状态统计

验收：

- [x] 单个源故障不会阻塞整轮抓取
- [x] 页头可以区分“上次抓取时间”和“最新新闻时间”
- [x] 失败源可见、可统计、可降级

### Sprint C（P1）：补充源池与事件化

目标：引入 `worldmonitor` 的高价值补充源，并开始把“新闻流”升级成“事件流”。

任务：

- [x] 从 `worldmonitor` 白名单中筛出“交易相关高价值源”子集（以 curated `worldmonitor_trading` 子集接入）
- [x] 将补充 RSS 源并入候选源池，但不全量默认开启（只纳入 curated 子集）
- [x] 引入 source tier / source group 概念，区分主链路与补充链路
- [x] 接入/复刻首版聚类逻辑，把重复新闻在 TUI 侧压成事件（内存态，不改 schema）
- [ ] 补基础 `symbol tagging / category tagging / event severity`

验收：

- [x] 默认主链路仍以高频直连源为主
- [x] worldmonitor 只作为补盲层，不稀释资讯密度
- [x] 同一事件的重复快讯数量已在 TUI 事件流中明显下降（首版规则聚类）

## 本轮落地（2026-03-07）

已继续继承 `repository/worldmonitor` 的 RSS 稳定性能力，并把 TUI 里的高频直连快讯源一并下沉到 `markets-service`：

- `services-preview/markets-service/src/providers/rss/news.py`
  - provider 现在同时支持普通 RSS/Atom URL 与 `direct://*` 高频直连源
  - 新增按源失败计数、冷却窗口、最后成功时间、最后错误、最近抓取条数
  - 连续失败达到阈值后，临时跳过坏源，避免每轮都卡在同一批异常源上
  - 统一暴露健康快照/汇总接口，供 CLI 日志与后续 TUI 状态栏复用
- `services-preview/markets-service/src/providers/rss/direct.py`
  - 新增 `J10 / THS / SINA / EM24 / CLS / GLH / WSCN / EEO` 直连快讯适配
  - 统一标准化到 `NewsArticle` 所需字段（标题 / 摘要 / 时间 / 类别 / 标的 / 语言）
- `services-preview/markets-service/src/news_defaults.py`
  - 默认源切到“高频直连 + 精选 RSS + curated worldmonitor_trading 子集”混合模式
  - 同时保留 `core` 预设，便于回退到旧的高频主链路口径
- `services-preview/markets-service/src/providers/rss/parser.py`
  - 增加 `parse_error`，可区分“空结果”和“XML 解析失败”
- `services-preview/markets-service/src/__main__.py`
  - `collect-news-poll` 新增健康汇总日志与异常源样本日志
- `services-preview/markets-service/tests/test_news_direct_parse.py`
  - 补充直连快讯解析测试
- `services-preview/markets-service/tests/test_rss_news_runtime.py`
  - 补充失败冷却、恢复后清零两类关键测试

本轮本地 smoke 结果：默认混合源单轮抓取可直接拿到 `20` 条，来源覆盖 `SINA / GLH / WSCN / CLS / J10 / THS / EM24`。

补充进展（2026-03-08）：

- 已给默认新闻源补齐 `source tier / source group` 元数据，明确区分 `core` 主链路与 `worldmonitor_trading` 补充链路
- `markets-service` 写库时会把来源分层信息写入内部 category tags（不改 schema）
- `tui-service` 资讯页已支持来源过滤：`全部 / 主链 / 补充 / 具体来源代码`，按键 `s` 可轮换
- 已完成一轮运行态恢复：PostgreSQL 14（`5434`）已拉起，`collect-news-poll` 已恢复写库，TUI DB-first 读取验证通过
- 运行态快照：`alternative.news_articles` 最近 `24h` 现存 `319` 条，历史 `24h` 外原始新闻为 `0` 条，保留策略已生效
- 当前密度观察：最近 `10m` 约 `5` 条，主因不是链路挂了，而是免费源本身在该时段的活跃度有限
- 已清理默认预设中的长期不稳定 RSS：`Yahoo Top Stories / Benzinga feed / SEC press.xml / FT home / Coindesk outbound RSS` 已从默认预设移除，继续保留为后续按需手动接入候选
- 默认预设重启后当前健康度已恢复为 `19/19 healthy`，TUI DB-first 快照读取正常，当前主要矛盾重新收敛到“免费源密度不足”，不再是“默认源大面积坏掉”

继续推进后，TUI 资讯页主链路也已切到“统一库优先、直抓兜底”：

- `services-preview/tui-service/src/news_db.py`
  - 新增统一新闻库读取器，优先从 `alternative.news_articles` 拉取最近新闻
  - DB URL 解析顺序为 `TUI_NEWS_DATABASE_URL -> MARKETS_SERVICE_DATABASE_URL -> DATABASE_URL -> config/.env -> 默认 5434`
  - 当配置的是本地 DB 地址时，会自动重试 `5434 / 5433 / 5432`，兼容当前 `.env` 端口漂移
- `services-preview/tui-service/src/tui.py`
  - `RssNewsPoller` 改为 DB-first，只有 DB 不可用或暂时无数据时才回退到本地直抓
  - 页头区分 `同步=...前`（上次成功同步）和 `最新=...前`（最新新闻发生时间）
  - 页头新增 `健=H/F/C`，优先显示 collector 健康汇总，fallback 模式下显示本地直抓健康摘要
- `services-preview/tui-service/src/news_events.py`
  - 新增首版规则聚类：标题归一化 + 中英混合 token + 时间窗约束
  - 不改数据库 schema，直接把 DB-first 新闻流在 TUI 读路径上压成事件流
  - 当前实测：最近 `24h / 300` 条原始新闻可收敛为 `112` 个事件
- `services-preview/tui-service/tests/test_news_db.py`
  - 补充默认 DB fallback 与本地端口重试测试

本轮额外 smoke：
- 先用 `services-preview/markets-service` 执行一轮 `collect-news`，已成功写入 `alternative.news_articles`
- TUI 侧随后可直接通过 DB 读到 `5` 条最近新闻
- 启动 `RssNewsPoller` 后首个快照已进入 `mode=DB`，并返回 `20` 条新闻，说明 `DB -> TUI` 路径已跑通

这一步仍属于“能力迁移”，不是“整仓迁移”：
- 已迁移：高频直连源下沉 / RSS 失败冷却 / 健康追踪 / 可观测性 / TUI 侧首版新闻事件化与聚类去重
- 未迁移：rss-proxy / 持久缓存 / 后端持久化事件层 / worldmonitor UI

## 任务到文件映射（初版）

| 任务 | 主要文件 | 说明 |
|---|---|---|
| 高频直连源下沉到 `markets-service` | `services-preview/markets-service/src/providers/` | 新增直连新闻 provider，承接当前 `direct://*` 逻辑 |
| 参考当前直连实现 | `services-preview/tui-service/src/tui.py` | 当前 `J10 / THS / SINA / EM24 / CLS / GLH / WSCN / EEO` 的抓取逻辑在这里 |
| RSS provider 增强（代理 / 超时 / 健康状态） | `services-preview/markets-service/src/providers/rss/news.py` | 当前 RSS 主逻辑在这里增强 |
| 新闻配置项扩展 | `services-preview/markets-service/src/config.py` | 增加代理、超时、并发、健康状态相关配置 |
| 默认 RSS 源管理 | `services-preview/markets-service/src/news_defaults.py` | 维护默认精选 RSS 源 |
| 默认 TUI 新闻源管理 | `services-preview/tui-service/src/news_defaults.py` | 维护 TUI 默认源；后续应逐步降级为展示侧配置 |
| 新闻写库与去重 | `services-preview/markets-service/src/storage/news_writer.py` | 统一入库入口，继续按 `dedup_hash` 去重 |
| 新闻 CLI 与轮询命令 | `services-preview/markets-service/src/__main__.py` | `collect-news` / `collect-news-poll` 参数入口 |
| 新闻后台启动脚本 | `services-preview/markets-service/scripts/start.sh` | `start-news` 的运行参数与守护逻辑 |
| TUI 启动时联动新闻链路 | `services-preview/tui-service/scripts/start.sh` | 当前会联动 `collect-news-poll`，后续需要对齐统一主链路 |
| TUI 资讯页状态展示 | `services-preview/tui-service/src/tui.py` | 增加 `抓取周期 / 上次抓取 / 最新新闻 / 源健康` |
| env 模板与默认值 | `config/.env.example` | 统一记录新闻源、超时、轮询、代理配置 |
| 文档同步 | `services-preview/markets-service/README.md`, `services-preview/tui-service/README.md`, `README.md`, `README_EN.md` | 记录最终主链路和配置方式 |
| 默认源测试 | `services-preview/markets-service/tests/test_news_defaults.py`, `services-preview/tui-service/tests/test_news_defaults.py` | 维护默认源不回退 |
| worldmonitor 候选源参考 | `repository/worldmonitor/src/config/feeds.ts`, `repository/worldmonitor/server/worldmonitor/news/v1/_feeds.ts` | 只作为候选源仓库，不直接并入主链路 |
| worldmonitor 可迁移能力参考 | `repository/worldmonitor/api/rss-proxy.js`, `repository/worldmonitor/src/services/rss.ts`, `repository/worldmonitor/src/services/clustering.ts` | 作为后续能力迁移参考 |

## 数据源策略（MVP）

### P0 主链路（先做）

1. **高频直连源**：J10 / THS / SINA / EM24 / CLS / GLH / WSCN / EEO
2. **精选 RSS**：Benzinga / FXStreet / CNBC / Cointelegraph / SEC / GlobeNewswire / The Block
3. **必要代理能力**：为 RSS/直连源补齐代理、超时、失败冷却、健康统计

### P1 补充覆盖（后做）

1. **worldmonitor 白名单**：只挑交易相关、高频、可靠的子集进入候选池
2. **央行/监管专源**：Fed / ECB / BIS / SEC 等官方公告源
3. **GDELT**：作为事件确认层，不作为主密度来源

### P2 事件增强（再后做）

- 聚类去重
- symbol tagging
- 事件分级
- 风险过滤因子

## 明确不采用的方案

- 不把 `worldmonitor` 整个前端 / Tauri / 地图层合并进 TradeCat
- 不把 `460+` RSS 源默认全部打开
- 不把普通 RSS 当成“高频快讯源”使用
- 不在本期新建独立 `news-service`

## 交付里程碑

### SCU1 / Phase 1（当前主线）：高频新闻主链路打通

目标：先把“免费源 -> 标准化 -> 展示/入库”这条主链路做成真正可用版本，优先解决新闻速度、覆盖面、可观测性，而不是先做情绪分析。

已完成：
- [x] `markets-service` 已具备 RSS/Atom 通用采集器 `providers/rss`
- [x] `markets-service` 已支持 `collect-news` / `collect-news-poll` 持续轮询写入 `alternative.news_articles`
- [x] `news_articles` 已按 `dedup_hash` 去重写入
- [x] `tui-service` 资讯页已接入真实新闻流，不再是 demo/mock
- [x] `tui-service` 已接入高频直连源：Jin10 / 同花顺 / 新浪 7x24 / 东方财富 / 财联社 / 格隆汇 / 华尔街见闻 / 经观
- [x] TUI 默认新闻轮询已下调到 `2s`，并支持并行抓取

本阶段剩余：
- [x] 将 TUI 侧高频直连源下沉到 `markets-service`，形成统一入库链路
- [x] 为 `markets-service` RSS provider 增加代理支持、失败冷却、按源健康统计
- [x] 将高频直连源标准化后统一写入 `alternative.news_articles`
- [x] 补齐端到端联调：验证“采集 -> 入库 -> 查询/展示”完整闭环
- [x] 增加更清晰的状态展示：抓取周期 / 上次抓取 / 最新新闻时间 / 源健康状态

本阶段验收：
- [x] 免费高频源默认可用，资讯页不再依赖 demo RSS
- [x] 默认状态下资讯页可持续刷新，TUI 侧轮询间隔 <= 2s
- [x] 至少保留 `8` 个可用高频源，并允许通过 env 覆盖
- [x] RSS 入库链路可持续运行，重复新闻不重复写入
- [x] `markets-service` 成为统一新闻主采集链路，TUI 不再长期承担主抓取职责

### Phase 2：统一入库 + 强过滤

- [x] 支持按来源/关键词/标的过滤
- [ ] 基于词典做 symbol tagging（BTC/ETH/NVDA/AAPL 等）
- [x] 让 TUI 默认读取统一新闻库，而不只读本地抓取快照
- [x] 从 `worldmonitor` 白名单中筛出“交易相关高价值源”并作为补充层接入（curated 子集）

### Phase 3：事件化 + 交易辅助

- [x] 接入/复刻新闻聚类能力，将重复新闻合并为事件（首版在 TUI 侧内存态实现）
- [ ] 构建基础新闻因子：`buzz_30m`、`sentiment_score`、`event_severity`
- [ ] 先接入风险过滤：高冲击负面事件时降杠杆/暂停开仓
- [ ] 回测对比：原策略 vs 新闻过滤策略（回撤/收益/胜率）

## 验收标准

### 工程验收

- [ ] 端到端延迟（采集到入库）P95 < 120s
- [ ] 去重准确：重复 URL/标题不重复入库
- [ ] 采集任务 24h 运行稳定（无崩溃）
- [ ] 高频源故障不会阻塞其他源（并发 + 超时 + 降级生效）

### 业务验收

- [ ] TUI 可查看最近 100 条新闻并可过滤
- [ ] 标的关联抽样准确率 >= 80%（100 条抽样）
- [ ] 加入新闻风险过滤后，最大回撤优于基线（同区间回测）
- [ ] 资讯页默认感知到的刷新速度明显快于纯 RSS 模式

## 风险与缓解

1. **源稳定性波动**：多源冗余 + 失败重试 + 来源降级
2. **噪音高**：先做过滤器，不直接做交易触发信号
3. **合规风险**：只接条款允许的公开源，落库保留来源 URL 与采集时间
4. **RSS 密度天然有限**：高频主链路必须依赖直连快讯源，RSS 只做补充
5. **网络/出口限制**：代理只解决可访问性，不解决新闻密度本身

## 进展记录

### 2026-03-03

- [x] 完成架构盘点：确认不单开 news-service，复用 markets-service
- [x] 完成信源调研：明确 MVP 免费源优先级（GDELT/SEC/RSS）
- [x] 确认库表可复用：`alternative.news_articles/news_sentiment/economic_calendar`

### 2026-03-05

- [x] markets-service: 新增 `providers/rss`（RSS/Atom）与 `collect-news` / `collect-news-poll`
- [x] markets-service: 新增 `alternative.news_articles` 写入器（去重插入）
- [x] tui-service: 新闻页（按键 `7`）已接入基础新闻布局与交互
- [x] 待联通验证：真实 feed 拉取 + TimescaleDB 入库联调（本地/容器环境）

### 2026-03-08

- [x] `markets-service` 新闻写入器新增原始新闻保留策略：默认仅保留最近 `24h`，避免 `alternative.news_articles` 无上限膨胀
- [x] 新增 `NEWS_RETENTION_HOURS` / `NEWS_RETENTION_CLEANUP_INTERVAL_SECONDS`，便于后续按环境调整清理窗口与执行频率
- [x] 明确策略：原始新闻只作为实时消费层，长期归档后续改由事件化/聚合层承担

### 2026-03-07

- [x] tui-service: 默认新闻源切换为 `MIX(13)`，主路径改为高频直连源 + 补充 RSS
- [x] tui-service: 已接入 `J10 / THS / SINA / EM24 / CLS / GLH / WSCN / EEO` 等高频源
- [x] tui-service: 资讯页左侧资产面板已去掉，页面聚焦新闻流 / 事件影响 / 详情
- [x] tui-service: 新闻轮询已支持并行抓取，默认轮询频率下调到 `2s`
- [x] 完成 `worldmonitor` RSS 能力摸底：确认其适合作为“源仓库 + 能力参考”，不适合作为主快讯引擎
- [x] 完成代理验证：代理前 RSS 可用约 `132`，代理后可用约 `462`
- [x] 完成密度验证：即使 `460+` RSS 可用，稳定密度仍仅约 `2~3` 条 / `30s`，不能替代高频直连源
- [x] 已完成统一链路第一步：高频直连源已下沉到 `markets-service` 默认采集链路
- [x] 已补齐基础观测性：页头已区分“同步时间 / 最新新闻时间 / 健康状态”，剩余高级指标后续继续增强

## 下一步（立即执行）

1. [x] 先把 TUI 高速直连源下沉到 `markets-service`，统一入库
2. [x] 给 `markets-service` 新闻链路补代理、失败冷却、健康状态统计
3. [x] 给资讯页补 `抓取周期 / 上次抓取 / 最新新闻` 三段状态
4. [x] 从 `worldmonitor` 白名单中筛出“交易相关子集”，只作为补充源层引入
5. [ ] 再评估 `providers/gdelt` / `providers/sec` 是否作为确认层接入

## 当前收口判断

- **是否可关闭 004**：暂不直接关闭
- **原因**：作为“新闻 MVP / 可用版”已经完成，但作为“完整增强版”仍有后续项
- **管理建议**：将 004 视为新闻能力 Epic，当前阶段可以按“已达到可用状态”管理，后续增强继续在本 issue 或拆分子 issue 追踪

