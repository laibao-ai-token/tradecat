---
name: binance-api-audit
description: "Use internal Datacat/Tradecat APIs for Binance-related data flows. Trigger when you must call API endpoints, return structured data (coins/indicators/snapshot/events), or verify data-source paths for these repos."
---

# binance-api-audit Skill

目标：让 AI **直接调用内部 API 获取结构化数据（偏 Binance 数据流）**，并在必要时校验端点与数据源路径。

## When to Use This Skill

触发条件（任一满足）：
- 需要通过内部 API 拉取数据（币种列表、指标数据、单币快照、事件流）
- 需要给出“可直接执行的请求命令”
- 需要确认 API 是否可用/端口是否正确
- 需要核对端点来源与数据源路径
- 用户要求“分析/研判/报告/交易计划”，且希望使用内部分析提示词

## Not For / Boundaries

- 不负责修改数据库或写入数据
- 不做安全渗透或漏洞利用
- 默认不向用户追问端口/服务状态，改为自动检测与自启动（如可用）。
 - 如需修复缺失字段，只允许使用 `repair_timescale_klines.py` 且必须显式 `--apply`

## Quick Reference

**项目根目录**
```bash
PROJECT_ROOT=/home/lenovo/.projects
```

**检查 Tradecat API 是否在运行（默认 8088）**
```bash
ss -tlnp | rg 8088
```

**启动 Tradecat API（未运行时自动启动）**
```bash
cd $PROJECT_ROOT/tradecat/services-preview/api-service
./scripts/start.sh start
```

**检查 Datacat API 是否在运行（默认 8000）**
```bash
ss -tlnp | rg 8000
```

**启动 Datacat API（未运行时自动启动）**
```bash
cd $PROJECT_ROOT/datacat/services/api-service
./scripts/run.sh
```

**Tradecat：支持币种**
```bash
curl -sS http://127.0.0.1:8088/api/futures/supported-coins
```

**Tradecat：指标表清单**
```bash
curl -sS http://127.0.0.1:8088/api/indicator/list
```

**Tradecat：指标明细**
```bash
curl -sS --get "http://127.0.0.1:8088/api/indicator/data" \
  --data-urlencode "table=布林带扫描器.py" \
  --data-urlencode "symbol=BTC" \
  --data-urlencode "interval=1h" \
  --data-urlencode "limit=200"
```

**Tradecat：基础数据（成交额/主动买卖比，直读 SQLite）**
```bash
curl -sS "http://127.0.0.1:8088/api/futures/base-data?symbol=BTC&interval=1h&limit=200"
```

**Tradecat：指标质量检查（成交额/主动买额空值率）**
```bash
python3 /home/lenovo/.projects/skills/binance-api-audit/scripts/check_tradecat_metrics_quality.py \
  --symbol BTC --interval 4h --limit 200 --auto-start
```

**Tradecat：修复 K 线缺失字段（写入 Timescale，慎用）**
```bash
python3 /home/lenovo/.projects/skills/binance-api-audit/scripts/repair_timescale_klines.py \
  --symbols BNBUSDT --intervals 1h --lookback 7 --apply
```

**Tradecat：刷新连续聚合（修复旧聚合未更新）**
```bash
python3 /home/lenovo/.projects/skills/binance-api-audit/scripts/refresh_timescale_cagg.py \
  --views candles_1h,candles_4h,candles_1d,candles_1w --lookback-days 30 --apply
```

**Tradecat：单币完整快照（结构化）**
```bash
curl -sS "http://127.0.0.1:8088/api/indicator/snapshot?symbol=BTC"
```

**Datacat：事件流**
```bash
curl -sS "http://127.0.0.1:8000/v1/events?limit=50"
```

**Datacat：币种命中扫描（新闻/公告/信号/清算/链上/大额转账）**
```bash
python3 /home/lenovo/.projects/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,BNB,ETH \
  --categories news,announcement,signal,liquidation,onchain,transfer \
  --since-hours 24 --scan-limit 5000 --format json
```

**Datacat：最新事件 ID**
```bash
curl -sS "http://127.0.0.1:8000/v1/events/latest"
```

**校验 Skill 结构**
```bash
bash $PROJECT_ROOT/skills/binance-api-audit/scripts/validate-skill.sh \
  $PROJECT_ROOT/skills/binance-api-audit --strict
```

**分析提示词选择（必须交互确认）**
- 当用户要求“分析/研判/报告/交易计划”时，**先让用户在以下提示词中选择其一**：
  - A：威科夫大师（风格化主观读图）
  - B：市场全局解析（机构量化模板，严格结构）
  - C：NoFX（按原文提示词）
- 提示词索引：`references/prompts/index.md`
  - 选择 A → `references/prompts/wyckoff.md`
  - 选择 B → `references/prompts/market-global.md`
  - 选择 C → `references/prompts/nofx_prompt.md`

**可解释性输出（思维链）**
- 必须输出完整推理过程或逐步思维链。
- 必须输出：结论 + 关键依据（3-5条） + 假设/不确定性 + 验证步骤。
- 假设/不确定性必须绑定**数据来源与证据**（端点/表/字段/空值率/时间窗），禁止无证据的“可能/大概”。
- 若出现“空值/为 0/缺口”描述，需先执行 `check_tradecat_metrics_quality.py` 并引用其结果。
- 输出模板：
  - 结论：……
  - 关键依据：① … ② … ③ …
  - 假设/不确定性：……
  - 验证步骤：……

## Examples

### Example 1: 拉取币种列表
- 输入：Tradecat API 服务已启动（端口 8088）
- 步骤：
  1) `curl -sS http://127.0.0.1:8088/api/futures/supported-coins`
- 验收：
  - 返回 `data` 数组
  - `success=true`

### Example 2: 拉取单币完整快照
- 输入：`symbol=BTC`
- 步骤：
  1) `curl -sS "http://127.0.0.1:8088/api/indicator/snapshot?symbol=BTC"`
- 验收：
  - 返回 `data.panels` 与 `data.base`（默认包含）
  - `data.source_db` 为 market_data.db 路径

### Example 3: 拉取 Datacat 事件流
- 输入：Datacat API 服务已启动（端口 8000）
- 步骤：
  1) `curl -sS "http://127.0.0.1:8000/v1/events?limit=50"`
- 验收：
  - 返回 `data.list` 与分页信息

### Example 4: Datacat 币种命中扫描
- 输入：`coins=BTC,BNB`，时间窗 24h
- 步骤：
  1) `python3 /home/lenovo/.projects/skills/binance-api-audit/scripts/query_unified_events.py --coins BTC,BNB --categories news,signal,liquidation --since-hours 24 --scan-limit 5000`
- 验收：
  - `summary.counts_by_coin` 非空
  - `items` 含命中事件

## References

- `references/index.md`
- `references/endpoints.md`
- `references/paths.md`
- `references/queries.md`
- `references/workflow.md`
- `references/quality-checklist.md`

## Maintenance

- Sources:
  - `/home/lenovo/.projects/datacat/services/api-service`
  - `/home/lenovo/.projects/tradecat/services-preview/api-service`
  - `claude-skills` 规范（`/home/lenovo/zip/vibe-coding-cn/i18n/zh/skills/00-元技能/claude-skills`）
  - `Skill_Seekers` 方法（`/home/lenovo/zip/vibe-coding-cn/libs/external/Skill_Seekers-development`）
- Last updated: 2026-01-29
- Known limits:
  - 动态注册路由需要运行时导入，默认不启用
  - API 返回数据为结构化原始值，不做 UI 格式化
