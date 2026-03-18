---
title: "002-review-c6a6feba-news-unified-news-db"
status: review
created: 2026-03-16
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-26
---

# [Review-002][c6a6feba] codex review unified news db preference

## 元信息

- Commit: `c6a6febaa2bda1728888ee60e615d27b223fa32b`
- Subject: `feat(news): prefer configured unified news database`
- 重点检查：
  - 统一新闻库配置优先级和 fallback 次序
  - TUI 读取失败时可恢复退路
  - 读路径保持只读边界

## Review 结果（回填）

### 1) `codex review --commit c6a6feba` 执行结果

- 已按要求执行该命令。
- 在当前环境中该命令会长时间停留在自动检查阶段，最终被 `timeout` 终止（`EXIT:124`），未产出最终汇总段。
- 过程中可见其已实际检查提交 diff、关键源码和测试文件，并尝试运行定向测试。

### 2) 代码审查结论（基于上述运行过程 + 人工复核）

- 配置优先级与 fallback：符合预期。
  - `resolve_news_database_url()` 优先级为 `TUI_NEWS_DATABASE_URL -> MARKETS_SERVICE_DATABASE_URL -> DATABASE_URL -> config/.env -> 默认值`。
  - 本地 DB URL 会自动候选重试 `5434/5433/5432`，保证本机端口切换场景可恢复。
- TUI 失败退路：符合预期。
  - DB 读取失败、空数据或 stale 时，`RssNewsPoller` 会退回直连/RSS 拉取；若 live 拉取失败且已有 DB 数据，会保留 DB 数据作为退路。
- 只读边界：符合预期。
  - TUI 侧通过 `psql COPY (SELECT ...) TO STDOUT` 读取 `<schema>.news_articles`，无写入行为。

### 3) 发现项（非阻塞）

- 严重级别：Low
- 说明：`ALTERNATIVE_DB_SCHEMA` 已成为 TUI 读取路径的有效配置项，但 `config/.env.example` 与 README 仍未明确该变量，存在认知偏差风险（默认文案仍偏向固定 `alternative.news_articles`）。
- 影响：在非默认 schema 部署时，排障成本增加。

### 4) 验证记录

按要求执行（原样）：

```bash
cd services-preview/markets-service
pytest -q tests/test_news_defaults.py

cd ../tui-service
pytest -q tests/test_news_db.py tests/test_news_defaults.py
```

结果：当前环境直接执行会因 `common` 包路径未注入导致 `ModuleNotFoundError`。

补充验证（注入仓库 `libs` 与服务路径）：

```bash
cd services-preview/markets-service
PYTHONPATH=../../libs:$PWD pytest -q tests/test_news_defaults.py

cd ../tui-service
PYTHONPATH=../../libs:$PWD pytest -q tests/test_news_db.py tests/test_news_defaults.py
```

- `markets-service`: `8 passed`
- `tui-service`: `19 passed`

## 后续处理（2026-03-17）

- 已补配置模板：`config/.env.example` 新增 `ALTERNATIVE_DB_SCHEMA=alternative`，明确统一新闻表 schema 配置。
- 已补文档说明：
  - 根 README / README_EN 的 `tradecat_get_news` 说明改为 `<ALTERNATIVE_DB_SCHEMA>.news_articles`（默认 `alternative.news_articles`）；
  - `services-preview/tui-service/README.md` 与 `services-preview/markets-service/README.md` 同步补充 schema 语义与默认值。
