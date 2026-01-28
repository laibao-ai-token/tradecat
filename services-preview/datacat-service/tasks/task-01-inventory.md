# 任务 01：原服务方法清点与依赖图（细化版）

## 目标

- 列出 `services/data-service/src` 所有采集/适配/配置/入口模块。
- 输出映射表与依赖图。

## 子任务拆分

- 01-01：逐文件扫描函数/类清单
- 01-02：模块级依赖关系图（文本）
- 01-03：原路径 → 新路径映射表

## 输入

- 原服务目录：`services/data-service/src`

## 输出

- 方法清单
- 依赖关系图
- 映射表

## 执行步骤（更细）

1) 扫描 collectors/ 与 adapters/ 文件：记录类/函数名。
2) 标记每个文件的外部依赖（requests/ccxt/cryptofeed/psycopg/aiohttp）。
3) 标记内部依赖（rate_limiter/timescale/metrics/config）。
4) 为每个模块建立目标路径（严格分层）。
5) 输出映射表并核对覆盖率。

## 验收

- 映射表覆盖率 100%。
- 依赖关系清晰到模块级。
