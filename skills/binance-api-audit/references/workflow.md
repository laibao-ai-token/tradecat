# 内部 API 数据获取流程（Decision Tree + Steps）

## 决策树

- 只需“拿到数据结果” → 执行 Step 1-3
- 需要“结构化快照/完整字段” → 执行 Step 1-4
- 需要“端点与数据源对照表” → 执行 Step 1-5

## Step 1: 定义范围
- 明确服务范围（Datacat / Tradecat）
- 确认端口与启动方式（参考 `references/endpoints.md`）
- 统一 `PROJECT_ROOT`，避免路径漂移

## Step 2: 确认端点
- 直接使用 `references/endpoints.md` 的已知端点
- 或运行路由扫描脚本，自动生成端点清单

```bash
PROJECT_ROOT=/home/lenovo/.projects
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/scan_fastapi_routes.py \
  $PROJECT_ROOT/datacat/services/api-service/src \
  $PROJECT_ROOT/tradecat/services-preview/api-service/src --mode ast --strict --format json
```

## Step 3: 发起请求拿数据
- 直接调用 API 获取数据（curl/HTTP 客户端）
- 若数据异常或空：检查服务状态、端口、依赖库

```bash
PROJECT_ROOT=/home/lenovo/.projects
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/inspect_sqlite.py \
  $PROJECT_ROOT/datacat/libs/database/unified.db --schema --count --tables events,sources
```

> 生产环境建议使用只读副本；线上必须执行时，避免 `--count-all` 并设置 `--timeout`。

## Step 4: 结构化完整快照（Tradecat）
- 使用 `/api/indicator/snapshot?symbol=BTC`
- 可通过 `panels/periods/include_base/include_pattern` 控制体积

## Step 5: 端点-数据源对照
- 建立 “接口 → 表 → 字段” 映射
- 需要时使用 `inspect_sqlite.py` 验证表结构

## Step 6: 输出交付
- 输出结构化 JSON
- 输出端点、请求命令与数据源说明

## 验证建议
- 请求失败：先检查端口监听与日志
- SQLite 只读：执行后确认无 `-wal/-journal` 文件新增
