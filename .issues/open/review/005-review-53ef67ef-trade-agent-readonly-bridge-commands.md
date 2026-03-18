---
title: "005-review-53ef67ef-trade-agent-readonly-bridge-commands"
status: review
created: 2026-03-17
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-29
commit: 53ef67ef71fdb49369d0dd90b9e017b09c2b094f
---

# [Review-005][53ef67ef] codex review trade-agent readonly bridge commands

## Review 范围

- Commit: `53ef67ef71fdb49369d0dd90b9e017b09c2b094f`
- Message: `feat(trade-agent): add read-only bridge commands`
- Files:
  - `scripts/lib/tradecat_news.py`
  - `scripts/tradecat_get_quotes.py`
  - `scripts/tradecat_get_signals.py`
  - `scripts/tradecat_get_news.py`
  - `scripts/tradecat_get_backtest_summary.py`
  - `tests/test_tradecat_get_quotes.py`
  - `tests/test_tradecat_get_news.py`
  - `tests/test_tradecat_get_backtest_summary.py`

## 执行记录

1. 已执行：`codex review --commit 53ef67ef`
2. 命令仅输出 PATH 警告后无进一步结果（长时间无输出），因此补充了人工逐文件审查与本地命令验证。

## Review 结果（回填）

### Findings

1. **High** - `tradecat_get_backtest_summary.py` 参数异常时未返回统一 JSON 错误结构，违反“参数异常返回结构化错误码”要求。
   - 位置：`scripts/tradecat_get_backtest_summary.py:503-525`
   - 现状：`_parse_args()` 使用默认 `argparse.ArgumentParser`；当参数缺失/非法时直接输出 usage 并 `SystemExit(2)`。
   - 复现：
     - `python3 scripts/tradecat_get_backtest_summary.py --bad-flag`
     - 实际输出为纯文本 usage/error，不包含统一 envelope（`ok/tool/ts/source/request/data/error`）与结构化 `error.code`。
   - 影响：Trade Agent 桥接层无法稳定按 JSON 契约解析参数错误，导致调用方需额外分支处理 stderr 文本。

### 检查重点结论

- 命令边界（只读）：**通过**
  - 本次新增命令仅执行行情读取、SQLite 读、PostgreSQL SELECT/COPY 读、artifact 文件读，未发现写操作。
- JSON 输出结构统一：**部分通过**
  - 正常路径与大部分错误路径均满足 `ok/tool/ts/source/request/data/error`。
  - 仅 `tradecat_get_backtest_summary.py` 参数解析失败路径未走 JSON envelope。
- 参数异常/数据缺失结构化错误码：**部分通过**
  - 数据缺失类（如 `source_unavailable`、`artifact_not_found`）已结构化。
  - 参数异常类在 backtest summary 命令上存在缺口（见 High finding）。

## 建议修复

- 将 `tradecat_get_backtest_summary.py` 的参数解析改为与其它桥接命令一致的 JSON-safe parser：
  - 覆写 `ArgumentParser.error/exit` 抛出可捕获异常；
  - 在 `main()` 统一输出 `error.code=invalid_request`（或约定码）与稳定 JSON envelope；
  - 为非法参数场景补充测试（例如缺失 `--run-id`、未知参数）。

## 验证记录

### 命令验证

- `python scripts/tradecat_get_quotes.py NVDA --market us_stock --pretty`
  - 返回结构化错误（`quote_fetch_failed`），受当前环境 DNS/外网限制影响。
- `python scripts/tradecat_get_signals.py --symbol BTCUSDT --timeframe 1m --limit 5`
  - 返回结构化错误（`source_unavailable`），因 `signal_history.db` 不存在。
- `python scripts/tradecat_get_news.py --symbol BTCUSDT --limit 5 --since-minutes 120`
  - 返回结构化错误（`query_failed`），受沙箱 socket 限制影响。
- `python3 scripts/tradecat_get_backtest_summary.py --run-id latest`
  - 返回结构化错误（`artifacts_root_missing`），因当前无 `artifacts/backtest`。
- `python3 scripts/tradecat_get_backtest_summary.py --bad-flag`
  - 输出 argparse 纯文本错误（非 JSON），确认 High finding。

### 测试验证

- `pytest -q tests/test_tradecat_get_quotes.py tests/test_tradecat_get_news.py tests/test_tradecat_get_backtest_summary.py`
  - 结果：`13 passed`

## 闭环更新（2026-03-18）

- 修复已落地：
  - `scripts/tradecat_get_backtest_summary.py`
    - 新增 `JsonArgumentParser` + `InvalidRequestError`，参数异常统一返回 JSON envelope。
    - `main()` 在参数错误路径统一返回 `error.code=invalid_request`。
  - `tests/test_tradecat_get_backtest_summary.py`
    - 新增 `test_returns_structured_error_for_invalid_arguments` 覆盖非法参数路径。
- 回归验证：
  - `python3 scripts/tradecat_get_backtest_summary.py --bad-flag` 现在返回结构化 JSON（`invalid_request`）。
  - `pytest -q tests/test_tradecat_get_backtest_summary.py` -> `4 passed`。
- 结论：`005` 的 High finding 已闭环。
