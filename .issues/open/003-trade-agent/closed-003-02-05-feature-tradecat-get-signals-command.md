---
title: "003-02-05-feature-tradecat-get-signals-command"
status: closed
created: 2026-03-11
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: high
type: feature
---

# Trade Agent 003-02-05：`tradecat_get_signals` 最小只读命令

## 背景

右侧 `openclaw` 要做交易研究，除了行情，还需要读取 TradeCat 当前已有的信号资产。

这项能力的关键不是“把 signal-service 重做一遍”，而是：

- 用只读方式拿到最近信号
- 统一输出结构
- 让 `openclaw` 能稳定消费

## 目标

提供一个最小可用的 `tradecat_get_signals` 本地命令，支持按 symbol / timeframe / limit 等条件查询最近信号。

## 本期范围

1. 确定命令入口与参数形式
2. 支持按 symbol 过滤
3. 支持按 timeframe 过滤
4. 支持 `limit`
5. 直接读取现有信号数据源，不解析 TUI 文本
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

- `symbol`
- `signal_type`
- `direction`
- `timeframe`
- `signal_ts`
- `provider`

若当前没有命中数据，建议返回空数组和明确说明，而不是报错。

## 建议写入范围

- `scripts/` 下桥接入口或子命令
- 必要时补只读适配代码到 `services/signal-service/` 或共享脚本
- 若命令对外可直接使用，同步最小文档到 `README.md` / `README_EN.md` / `AGENTS.md`

## 非目标

- 不改信号生成规则
- 不做写操作
- 不改 `repository/openclaw`
- 不解析 TUI 画面

## 验收标准

- [x] 可按 symbol 查询最近信号
- [x] 可按 timeframe / limit 过滤
- [x] 输出为稳定 JSON，可被脚本直接解析
- [x] 输出包含 `source`、`ts`、`error`
- [x] 无数据时返回结构化空结果，而不是裸异常
- [x] 有一组最小 smoke 命令可复现

## 为什么适合 Sym

- 输入输出边界清晰
- 主要是只读聚合与 JSON 收口
- 与 quotes / news 可并行推进

## 相关 Issue

- Parent: `#003`
- Parent: `#003-02`
- Parallel with: `#003-02-04`, `#003-02-06`
- Feeds: `#003-06`

## 关闭边界

- 命令可调用、JSON 稳定、smoke 可跑即可关闭
- 若当前本地信号数据源存在缺口，也要明确 blocker 和临时降级后收口

## 进展记录

### 2026-03-11

- [x] 已从 `#003-02` 拆分为独立子单，便于并行推进
- [x] 已派单到 Linear / Symphony：`TRA-15`
- [x] 已完成：读取路径与字段语义已收口为只读 `signal_history.db` 查询
- [x] 已完成：symbol / timeframe / limit 的最小过滤能力已落地

### 2026-03-12

- [x] 已人工确认脚本、只读存储层与测试均已回灌主仓，本单正式关闭
