# TradeCat Base 速览（精简版）

这份文档只回答 3 件事：
- 各 service 干什么
- 数据怎么流
- 最快怎么跑起来

一句话：**采集 -> 入库 -> 计算 -> 信号 -> 展示/API**。

## 1. 核心数据流

```text
data-service      -> TimescaleDB/PostgreSQL
markets-service   -> TimescaleDB/PostgreSQL
datacat-service   -> TimescaleDB/PostgreSQL

TimescaleDB       -> trading-service -> SQLite market_data.db
TimescaleDB       -> signal-service

SQLite            -> telegram-service -> ai-service
SQLite            -> vis-service

TimescaleDB       -> api-service
SQLite            -> api-service
```

## 2. 服务分工（只看重点）

### 稳定版 `services/`
- `data-service`：加密数据采集，写入 TimescaleDB
- `trading-service`：指标计算（读 PG -> 写 SQLite）
- `signal-service`：信号检测/规则引擎
- `telegram-service`：Bot 交互与推送 UI
- `ai-service`：AI 分析能力（配合 telegram）
- `aws-service`：SQLite 同步等辅助服务

### 预览版 `services-preview/`
- `markets-service`：多市场采集（美股/A股/港股/宏观）
- `datacat-service`：采集基建预览（实验链路）
- `api-service`：对外 REST API（读库）
- `vis-service`：可视化渲染（读库）
- `tui-service`：终端看板
- 其他：`order-service` / `predict-service` / `fate-service` / `nofx-dev`

## 3. 最短启动路径

### 3.1 初始化
```bash
./scripts/init.sh
# 需要预览服务时
./scripts/init.sh --all
```

### 3.2 配置
```bash
cp config/.env.example config/.env
chmod 600 config/.env
```

至少确认：
- `DATABASE_URL`
- `BOT_TOKEN`（用 Telegram 时）

### 3.3 启核心服务
```bash
./scripts/start.sh start
./scripts/start.sh status
```

### 3.4 启 TUI（可选）
```bash
./scripts/start.sh run
```

### 3.5 验证
```bash
./scripts/verify.sh
```

## 4. 回测（最常用）

```bash
# 默认回测（使用 services/signal-service/src/backtest/strategies/default.crypto.yaml）
./scripts/backtest.sh

# 指定时间窗口 + 币种
./scripts/backtest.sh \
  --start "2026-01-15 00:00:00" \
  --end "2026-02-14 00:00:00" \
  --symbols BTCUSDT,ETHUSDT

# 覆盖率检查（只检查不执行）
./scripts/backtest.sh \
  --check-only \
  --start "2026-01-15 00:00:00" \
  --end "2026-02-14 00:00:00"

# 历史信号 vs 规则重放对比
./scripts/backtest.sh \
  --mode compare_history_rule \
  --symbols BTCUSDT,ETHUSDT \
  --start "2026-01-15 00:00:00" \
  --end "2026-02-14 00:00:00"
```

回测产物目录：

- 每次运行会创建 `artifacts/backtest/YYYYMMDD-HHMMSS/`
- 单模式（history/offline_replay/offline_rule_replay）结果直接在该目录下
- `compare_history_rule` 会在该目录下生成：`<base>-history`、`<base>-rules`、`<base>-compare`
- `--walk-forward` 会在该目录下写汇总文件，并生成每折子目录：`<run_id>-wf01`、`<run_id>-wf02`...
- `artifacts/backtest/latest` 始终指向最新一次结果目录（通常是 rule 结果目录）

## 5. 最常见问题

- 采集了但查不到：通常是 `DATABASE_URL` 指向的端口/库不一致（5433 vs 5434）
- `api-service`/`vis-service` 没内容：它们只读库，不产数据
- `signals` 区块空：通常是上游没写 `signal_history.db`，不是 TUI 本身故障

## 6. 快速命令

```bash
# 核心服务
./scripts/start.sh start|stop|status

# 单服务（稳定版/预览版都类似）
cd services/<name> && make run|start|stop|status
cd services-preview/<name> && make run|start|stop|status
```
