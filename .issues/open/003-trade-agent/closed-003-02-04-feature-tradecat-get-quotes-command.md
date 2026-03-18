---
title: "003-02-04-feature-tradecat-get-quotes-command"
status: closed
created: 2026-03-11
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent 003-02-04：`tradecat_get_quotes` 最小只读命令

## 背景

`#003-02` 已收敛为 `TradeCat` 能力桥。第一批能力里，quotes 是最适合先落地的一项：

- 输入简单
- 输出边界清晰
- 对 `openclaw` 侧最容易做 skill 接入和 smoke

本单的目标不是做一套新行情服务，而是把 TradeCat 已有的行情读取能力，收敛成一个稳定、只读、可脚本调用的命令入口。

## 目标

提供一个最小可用的 `tradecat_get_quotes` 本地命令，供后续 `openclaw` skill 直接调用。

## 本期范围

1. 确定命令入口与参数形式
2. 支持查询一个或多个 symbol
3. 如有必要支持 `market` 作为可选参数
4. 直接读取现有数据源 / 服务 / 脚本，不解析 TUI 文本
5. 返回稳定 JSON
6. 补最小 smoke 步骤

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

- `symbol`
- `market`
- `price`
- `quote_ts`
- `provider`

## 建议写入范围

- `scripts/` 下桥接入口或子命令
- 必要时补只读适配代码到 `services/` 或 `services-preview/`
- 若命令对外可直接使用，同步最小文档到 `README.md` / `README_EN.md` / `AGENTS.md`

## 非目标

- 不新增行情采集链路
- 不改 `openclaw` 仓库
- 不解析终端画面
- 不做写操作

## 验收标准

- [x] 可通过本地命令查询单个 symbol 的最新行情
- [x] 可通过本地命令查询多个 symbol 的最新行情
- [x] 输出为稳定 JSON，可被脚本直接解析
- [x] 输出包含 `source`、`ts`、`error`
- [x] 数据缺失时返回结构化错误，而不是裸异常
- [x] 有一组最小 smoke 命令可复现

## 为什么适合 Sym

- 写入边界相对清晰
- 能独立验收
- 即使后续与其他能力桥合并，也能先单点收口

## 相关 Issue

- Parent: `#003`
- Parent: `#003-02`
- Parallel with: `#003-02-05`, `#003-02-06`
- Feeds: `#003-06`

## 关闭边界

- 命令可调用、JSON 稳定、smoke 可跑即可关闭
- 若发现当前行情数据源在本地无法稳定读取，也要明确 blocker 与降级方案后收口

## 进展记录

### 2026-03-11

- [x] 已从 `#003-02` 拆分为独立子单，便于并行推进
- [x] 已派单到 Linear / Symphony：`TRA-14`
- [x] 已完成：命令路径、参数和 JSON 外层结构已落地为 `scripts/tradecat_get_quotes.py`
- [x] 已完成：复用 `services-preview/tui-service/src/quote.py` 与 `watchlists.py` 打通真实行情读取路径

### 2026-03-12

- [x] 已人工确认脚本、测试与文档均已回灌主仓，本单正式关闭
