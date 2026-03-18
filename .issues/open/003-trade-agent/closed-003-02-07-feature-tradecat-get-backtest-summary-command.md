---
title: "003-02-07-feature-tradecat-get-backtest-summary-command"
status: closed
created: 2026-03-11
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: medium
type: feature
---

# Trade Agent 003-02-07：`tradecat_get_backtest_summary` 最小只读命令

## 背景

`backtest summary` 不是最先要打通的能力，但它属于 `#003-02` 约定的第一批接口之一。

这项能力更适合作为：

- `openclaw` 的研究补充输入
- 未来对比策略、解释风险收益时的摘要来源

## 目标

提供一个最小可用的 `tradecat_get_backtest_summary` 本地命令，优先读取已有回测产物摘要，而不是在命令执行时重新跑回测。

## 本期范围

1. 确定命令入口与参数形式
2. 优先支持按 `run_id` 读取
3. 可选支持 `strategy` / `symbols`
4. 读取现有回测产物目录中的摘要文件
5. 返回稳定 JSON 与最小 smoke 步骤

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

- `run_id`
- `strategy`
- `symbols`
- `window`
- `metrics`
- `artifacts`

## 建议写入范围

- `scripts/` 下桥接入口或子命令
- `artifacts/backtest/` 读取适配
- 若命令对外可直接使用，同步最小文档到 `README.md` / `README_EN.md` / `AGENTS.md`

## 非目标

- 不在本单里触发完整回测
- 不做参数搜索
- 不改回测引擎逻辑
- 不改 `repository/openclaw`

## 验收标准

- [x] 可读取一个现有回测产物并输出摘要
- [x] 支持最小的 `run_id` 查询
- [x] 输出为稳定 JSON，可被脚本直接解析
- [x] 输出包含 `source`、`ts`、`error`
- [x] 回测产物缺失时返回结构化错误
- [x] 有一组最小 smoke 命令可复现

## 为什么适合 Sym

- 边界明确
- 主要工作是产物读取与 JSON 规约
- 与 quotes / signals / news 相对解耦

## 相关 Issue

- Parent: `#003`
- Parent: `#003-02`
- Feeds: `#003-06`
- Related: `#006`

## 关闭边界

- 命令可读取已有产物、JSON 稳定、smoke 可跑即可关闭
- 若当前环境缺少可用回测产物，也要明确 blocker 与降级说明后收口

## 进展记录

### 2026-03-11

- [x] 已从 `#003-02` 拆分为独立子单，便于并行推进
- [x] 已派单到 Linear / Symphony：`TRA-17`
- [x] 已完成：优先读取 `artifacts/backtest/` 下 `metrics.json` / `comparison.json` 等产物文件
- [x] 已完成：最小摘要字段与错误语义已落地

### 2026-03-12

- [x] 已人工确认脚本、测试与文档均已回灌主仓，本单正式关闭
