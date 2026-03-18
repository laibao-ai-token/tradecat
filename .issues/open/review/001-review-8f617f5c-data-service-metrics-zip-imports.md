---
title: "001-review-8f617f5c-data-service-metrics-zip-imports"
status: review
created: 2026-03-17
updated: 2026-03-17
owner: codex
priority: high
type: review
linear: TRA-25
commit: 8f617f5ca4984884e612bbc769663d1380fa4be8
---

# [Review-001][8f617f5c] data-service metrics + zip imports

## 执行记录

- 已执行：`codex review --commit 8f617f5c`
- 审查对象提交：`8f617f5ca4984884e612bbc769663d1380fa4be8`
- 提交标题：`test(data-service): cover collector metrics and zip imports`

## Review 结果（回填）

### 结论

- 需要修改后再合入（存在 1 个中风险问题，1 个低风险覆盖缺口）。

### Findings

1. `中风险`：测试在模块导入时全局注入 `sys.modules`，可能污染同进程其它测试。
   - 位置：`services/data-service/tests/test_collection_pipeline.py:14`, `services/data-service/tests/test_collection_pipeline.py:80`
   - 说明：`_install_collector_import_stubs()` 在 import 阶段直接写入 `sys.modules["config"]`、`sys.modules["adapters.*"]`，且没有 teardown。若后续测试在同一 Python 进程中导入相关模块，可能命中桩模块而非真实模块，导致结果失真或顺序相关失败（flaky）。
   - 建议：改为 fixture 作用域内注入（`monkeypatch.setitem(sys.modules, ...)`），并在用例结束后自动恢复；避免在模块顶层执行注入。

2. `低风险`：ZIP import 失败主路径覆盖不足。
   - 位置：`services/data-service/src/collectors/backfill.py:519`, `services/data-service/src/collectors/backfill.py:560`
   - 说明：当前新增用例覆盖了成功路径和“坏行跳过”，但未覆盖 ZIP 文件级失败（如损坏 ZIP/无 CSV）对应的 `except` 分支与 `return 0` 行为。
   - 建议：补充 `_import_kline_zip` / `_import_metrics_zip` 的失败用例（坏 ZIP、空 ZIP、非 CSV entries），并断言不写入 Timescale 适配器。

### 关注点核对

- 指标断言稳定性：当前断言整体稳定。时间对齐使用固定时间戳并按 5 分钟桶计算，未发现时间漂移型 flaky 风险。
- zip import 成功/失败路径覆盖：成功路径覆盖充分；文件级失败路径仍有缺口（见 Finding #2）。
- 外部依赖/全局状态污染：无外部网络依赖；存在 `sys.modules` 全局污染风险（见 Finding #1）。

## 验证记录

- `cd services/data-service && pytest -q tests/test_collection_pipeline.py` -> `3 passed`
- `cd services/data-service && pytest -q tests` -> 环境依赖错误（`ModuleNotFoundError: common`），与本次提交变更无直接关系。

## 建议后续动作

- 优先修复 `sys.modules` 污染方式（避免顺序依赖）。
- 补齐 ZIP 失败路径测试后再进行 re-review。

## 后续处理（2026-03-17）

- 已完成 `sys.modules` 污染修复：
  - `services/data-service/tests/test_collection_pipeline.py` 改为 fixture 作用域内用 `monkeypatch.setitem` 注入依赖桩；
  - 移除模块级全局注入，避免跨用例污染。
- 已补齐 ZIP 失败路径测试（坏 ZIP / 空 ZIP / 非 CSV entries）。
- 验证：`cd services/data-service && pytest -q tests/test_collection_pipeline.py` 通过（`7 passed`）。
