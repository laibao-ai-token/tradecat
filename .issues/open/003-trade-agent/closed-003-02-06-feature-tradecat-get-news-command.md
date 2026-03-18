---
title: "003-02-06-feature-tradecat-get-news-command"
status: closed
created: 2026-03-11
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent 003-02-06：`tradecat_get_news` 最小只读命令

## 背景

`#004` 已经把新闻采集能力沉淀到了 TradeCat。对 Agent 来说，这部分是最重要的研究输入之一。

现在需要做的是把现有新闻读取能力，收口成一个稳定的、只读的、可被 `openclaw` skill 调用的命令，而不是让右侧去读左侧 TUI。

## 目标

提供一个最小可用的 `tradecat_get_news` 本地命令，支持按 symbol / query / limit / since_minutes 等条件拉取最近新闻。

## 本期范围

1. 确定命令入口与参数形式
2. 支持按 symbol 过滤
3. 支持按 query 过滤
4. 支持 `limit`
5. 支持 `since_minutes`
6. 返回稳定 JSON 与最小 smoke 步骤

## 建议输出字段

至少包含：

- `ok`
- `tool`
- `ts`
- `source`
- `request`
- `data`
- `error`

`data` 内建议至少包含：

- `title`
- `summary`
- `published_at`
- `provider`
- `url`
- `symbols`
- `category`

## 建议写入范围

- `scripts/` 下桥接入口或子命令
- 必要时补只读适配代码到 `services-preview/markets-service/` 或共享脚本
- 若命令对外可直接使用，同步最小文档到 `README.md` / `README_EN.md` / `AGENTS.md`

## 非目标

- 不重写新闻采集器
- 不新增新闻源接入
- 不改 `repository/openclaw`
- 不解析 TUI 画面

## 验收标准

- [x] 可返回最近新闻列表
- [x] 支持 symbol / query / limit / since_minutes 的最小过滤
- [x] 输出为稳定 JSON，可被脚本直接解析
- [x] 输出包含 `source`、`ts`、`error`
- [x] 无数据时返回结构化空结果，而不是裸异常
- [x] 有一组最小 smoke 命令可复现

## 为什么适合 Sym

- 需求边界清晰
- 结果直接服务 `#003-06`
- 与 quotes / signals 可以并行推进

## 相关 Issue

- Parent: `#003`
- Parent: `#003-02`
- Related: `#004`
- Parallel with: `#003-02-04`, `#003-02-05`
- Feeds: `#003-06`

## 关闭边界

- 命令可调用、JSON 稳定、smoke 可跑即可关闭
- 若新闻数据在当前环境中无法稳定读取，也要把 blocker 和降级说明写清楚后收口

## 进展记录

### 2026-03-11

- [x] 已从 `#003-02` 拆分为独立子单，便于并行推进
- [x] 已派单到 Linear / Symphony：`TRA-16`
- [x] 已完成：新闻读取路径与 JSON 字段已收口为 `scripts/tradecat_get_news.py` + `scripts/lib/tradecat_news.py`
- [x] 已完成：symbol / query / 时间窗口过滤已落地

### 2026-03-12

- [x] 已人工确认脚本、helper、测试与文档均已回灌主仓，本单正式关闭
