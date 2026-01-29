# binance-api-audit 使用文档

本文档描述 binance-api-audit 的完整使用方式、参数说明与安全建议。

---

## 1. 概览

binance-api-audit 的核心目标是：**通过内部 API 直接获取结构化数据（偏 Binance 数据流）**，并在必要时辅助扫描路由/核对数据源。适用于 Datacat/Tradecat 的内部数据接口调用与审计。

**核心能力**
- API 直接调用：币种/指标/快照/事件流
- 路由扫描：AST 解析 FastAPI 装饰器，输出端点清单与 include_router 映射
- 数据源检查：只读查询 SQLite 表结构与统计
- 文档参考：路径速查与常用查询模板
- 全量端点与用法：见 `references/endpoints.md`
- 可解释性输出：结论 + 关键依据 + 假设/不确定性 + 验证步骤（必须输出）

**默认安全策略**
- SQLite 只读连接（除非显式 `--readwrite`）
- 解析失败可观测（stderr 或 JSON errors）

---

## 2. 依赖与环境

- Python 3.10+
- 只读脚本仅依赖标准库
- 字段修复脚本依赖 Tradecat data-service 环境（ccxt/psycopg 等）
- 推荐设置 `PROJECT_ROOT=/home/lenovo/.projects`（可按实际路径调整）

---

## 3. 目录结构

```
skills/binance-api-audit/
├── AGENTS.md
├── SKILL.md
├── USAGE.md
├── scripts/
│   ├── scan_fastapi_routes.py
│   ├── inspect_sqlite.py
│   ├── repair_timescale_klines.py
│   ├── refresh_timescale_cagg.py
│   └── validate-skill.sh
└── references/
    ├── index.md
    ├── endpoints.md
    ├── workflow.md
    ├── paths.md
    ├── queries.md
    ├── quality-checklist.md
    └── skill-seekers.md
```

---

## 4. 快速开始（API 调用优先）

```bash
PROJECT_ROOT=/home/lenovo/.projects

# 1) Tradecat 支持币种
curl -sS http://127.0.0.1:8088/api/futures/supported-coins

# 2) Tradecat 单币完整快照
curl -sS "http://127.0.0.1:8088/api/indicator/snapshot?symbol=BTC"

# 3) Datacat 事件流
curl -sS "http://127.0.0.1:8000/v1/events?limit=50"
```

---

## 5. 路由扫描脚本（辅助）

### 5.1 命令

```bash
python3 scripts/scan_fastapi_routes.py <roots...> [--mode ast|regex] [--format json|table] [--strict]
```

### 5.2 参数说明

- `roots`: 目录或文件列表（递归扫描 .py）
- `--mode`: 解析方式
  - `ast`（默认）：更稳健，能处理多行装饰器与别名对象
  - `regex`: 兼容旧风格，速度快但易漏报
- `--format`:
  - `json`（默认）：结构化输出，适合自动化
  - `table`: 便于人工阅读
- `--strict`:
  - 解析/读取出错时返回非 0 退出码

### 5.3 输出格式（JSON）

```json
{
  "routers": [
    {"router": "v1", "prefix": "/v1", "file": "routers/v1.py", "line": 12}
  ],
  "routes": [
    {"method": "get", "path": "/health", "decorator": "@app.get", "file": "main.py", "line": 20}
  ],
  "errors": [
    "routers/legacy.py: invalid syntax (<unknown>, line 3)"
  ]
}
```

**说明**
- 动态路径或运行时计算路径会被标记为 `<dynamic>`
- `errors` 为空表示无解析错误

---

## 6. SQLite 检查脚本（辅助）

### 6.1 命令

```bash
python3 scripts/inspect_sqlite.py <db_path> [--tables t1,t2] [--count] [--count-all] [--schema] \
  [--format json|table] [--timeout <sec>] [--readwrite] [--strict]
```

### 6.2 参数说明

- `db_path`: SQLite 文件路径
- `--tables`: 指定表名列表（逗号分隔）
- `--count`: 输出行数统计（大表会较慢）
- `--count-all`: 允许对全部表执行 COUNT（默认不允许）
- `--schema`: 输出表结构（`PRAGMA table_info`）
- `--format`:
  - `json`（默认）
  - `table`
- `--timeout`: 忙等待超时（秒），默认 5.0
- `--readwrite`: 允许读写连接（默认只读）
- `--strict`: 遇到错误立即退出（非 0）

### 6.3 输出格式（JSON）

```json
{
  "db": "/abs/path/unified.db",
  "tables": ["events", "sources"],
  "counts": {"events": 1200},
  "schemas": {"events": [{"cid": 0, "name": "id", "type": "INTEGER"}]},
  "errors": []
}
```

---

## 7. Datacat 币种命中扫描（二次检查）

用于按币种 + 类别（新闻/公告/信号/清算/链上/大额转账）进行二次命中筛选。

### 7.1 命令

```bash
python3 scripts/query_unified_events.py \
  --coins BTC,BNB,ETH \
  --categories news,announcement,signal,liquidation,onchain,transfer \
  --since-hours 24 --scan-limit 5000 --format json
```

### 7.2 参数说明

- `--db`: SQLite 路径（默认 unified.db）
- `--coins`: 币种列表（逗号分隔，默认 BTC/ETH/BNB/SOL/XRP/DOGE）
- `--strict-coins`: 只使用 `--coins`，不自动合并默认主流币
- `--categories`: 类别（news/announcement/signal/liquidation/onchain/transfer/sentiment/twitter）
- `--tags`: SQL 层 tag 过滤（可选，逗号分隔）
- `--types`: events.type 过滤（可选，逗号分隔）
- `--source-like`: 过滤来源（label/source_key 模糊匹配）
- `--category-mode`: 类别匹配策略（or/and/tag/keyword）
- `--keywords`: 额外关键词（逗号分隔）
- `--exclude-keywords`: 排除关键词（逗号分隔）
- `--since-hours`: 时间窗（小时）
- `--since-id`: 增量拉取（id > since_id）
- `--scan-limit`: 最大扫描行数
- `--min-content-len`: 最小内容长度过滤
- `--max-content`: 输出内容截断长度
- `--format`: json/table

### 7.3 输出说明

- `summary.counts_by_coin`: 每个币种命中数量
- `summary.counts_by_category`: 每个类别命中数量
- `items`: 命中事件列表（含 coins/categories）

---

## 8. Tradecat 指标质量检查

用于解释“成交额为 0 / 主动买卖比为空”等问题，**默认直接读取 Telegram SQLite 基础数据表**，输出空值/零值比例与样例行。

### 8.0 基础数据接口（API 直读 SQLite）

```bash
curl -sS "http://127.0.0.1:8088/api/futures/base-data?symbol=BNB&interval=1h&limit=200"
```

### 8.1 命令

```bash
python3 scripts/check_tradecat_metrics_quality.py \
  --symbol BTC --interval 4h

# 强制 API 模式（可选）
python3 scripts/check_tradecat_metrics_quality.py \
  --source api --symbol BTC --interval 4h --limit 200 --auto-start

# 若 symbol/interval 不匹配，会自动解析为实际值（如 BNB -> BNBUSDT）。
```

### 8.2 输出说明

- `field_stats`：基础表字段空值/零值计数（成交额/主动买卖比/主动买额/主动卖出额）
- `missing_count`：主动买卖比 缺失/为 0 的数量
- `missing_ratio` / `ratio_missing`：主动买卖比 缺失/为 0 的比例（0-1）
- `latest_row`：最新一条原始数据样例
- `source`：包含 SQLite 路径与表名

---

## 9. Timescale K 线字段修复（谨慎）

用于修复 `quote_volume / trade_count / taker_buy_*` 等缺失字段。**默认只读**，需要显式 `--apply` 才会写入。

### 9.1 命令

```bash
python3 scripts/repair_timescale_klines.py \
  --symbols BNBUSDT --intervals 1h --lookback 7 --apply
```

### 9.2 说明

- 传入 `1h/4h/1d` 等周期时，脚本会自动改用 `1m` 修复并刷新对应连续聚合。
- 输出包含 `repair_interval`、`fetched_rows` 与 `upserted_rows`。

---

## 10. Timescale 连续聚合刷新（谨慎）

用于修复“旧的聚合未刷新”的问题。**默认只读**，需要显式 `--apply` 才会执行。

### 10.1 命令

```bash
python3 scripts/refresh_timescale_cagg.py \
  --views candles_1h,candles_4h,candles_1d,candles_1w --lookback-days 30 --apply
```

### 10.2 说明

- `--views` 支持直接传 `candles_1h` 或传 `1h`（自动补全为 `market_data.candles_1h`）。\n*** End Patch}"}"}}

（API 模式时才会输出 `gap_anomalies` / `max_gap_seconds` 与 `source.table_hint`）

---

## 9. 生产安全建议

**强烈建议只在只读副本/拷贝上运行**，避免影响线上写入。

若必须在生产库执行：
- 保持默认只读（不要加 `--readwrite`）
- 避免 `--count-all`，仅对小表或指定表计数
- 设置合理 `--timeout`

---

## 8. 常见问题

**Q1: 为什么没有扫描到某些路由？**  
A: 可能是动态路径或运行时拼装。可检查 JSON 中 `<dynamic>` 标记，必要时加运行时导入模式（需明确开启，默认不启用）。

**Q2: 统计很慢怎么办？**  
A: 避免 `--count-all`，只对重点表使用 `--tables` + `--count`。

**Q3: 出现解析错误如何定位？**  
A: 使用 `--strict` 并查看 `errors` 字段或 stderr 输出。

---

## 9. 典型工作流（推荐）

1) 路由扫描（AST + strict）  
2) 对端点标注数据源（PG/SQLite）  
3) SQLite 表结构与关键统计  
4) 输出“端点-数据源-风险点”对照表  
5) 自动检测服务端口，必要时自启动（Tradecat 8088 / Datacat 8000）
5) 形成稳定性/一致性改进清单

---

## 10. 附录：统计请求方法

```bash
PROJECT_ROOT=/home/lenovo/.projects
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/scan_fastapi_routes.py \
  $PROJECT_ROOT/datacat/services/api-service/src \
  $PROJECT_ROOT/tradecat/services-preview/api-service/src --mode ast --strict --format json > /tmp/api_routes.json

python3 - <<'PY'
import json
from collections import Counter
with open('/tmp/api_routes.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
methods = [r.get('method') for r in data.get('routes', []) if r.get('method')]
counts = Counter(methods)
for m in sorted(counts):
    print(f"{m}: {counts[m]}")
PY
```
