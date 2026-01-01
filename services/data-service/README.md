# Data Service

Binance 期货市场数据采集服务，提供 1m K线和 5m 期货指标的实时采集与历史补齐。

## 功能

- **WebSocket K线采集** - 订阅 600+ USDT 永续合约 1m K线，时间窗口批量写入
- **期货指标采集** - 5分钟周期采集持仓量、多空比、主动买卖比
- **数据补齐** - ZIP 历史下载 + REST API 分页补齐 + 缺口巡检
- **限流保护** - 全局限流器，自动检测 IP Ban 并等待

## 目录结构

```
src/
├── adapters/           # 外部服务适配层
│   ├── ccxt.py         # 交易所 API
│   ├── cryptofeed.py   # WebSocket 适配器
│   ├── timescale.py    # TimescaleDB 适配器
│   ├── rate_limiter.py # 限流器
│   └── metrics.py      # 监控指标
├── collectors/         # 数据采集器
│   ├── ws.py           # WebSocket K线采集
│   ├── metrics.py      # 期货指标采集
│   ├── backfill.py     # 数据补齐
│   ├── alpha.py        # Alpha 代币列表
│   └── downloader.py   # 文件下载器
├── config.py           # 配置管理
└── __main__.py         # 入口
```

## 快速开始

### 环境要求

- Python >= 3.10
- TimescaleDB
- 代理服务（访问 Binance）

### 安装

```bash
pip install cryptofeed ccxt psycopg[binary] psycopg-pool requests
```

### 启动

```bash
# 方式一：启动脚本
./scripts/start.sh start      # 启动全部
./scripts/start.sh daemon     # 启动 + 守护
./scripts/start.sh status     # 查看状态
./scripts/start.sh stop       # 停止

# 方式二：单独启动
cd services/data-service
PYTHONPATH=src python3 -m collectors.ws        # WebSocket
PYTHONPATH=src python3 -m collectors.metrics   # Metrics
PYTHONPATH=src python3 -m collectors.backfill --all  # 补齐

# 方式三：调度器
python3 -m src --all          # 全部
python3 -m src --ws           # 仅 WebSocket
python3 -m src --metrics      # 仅 Metrics
```

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql://...@localhost:5433/market_data` | TimescaleDB 连接串 |
| `HTTP_PROXY` | `http://127.0.0.1:9910` | 代理地址 |
| `BINANCE_WS_GAP_INTERVAL` | `600` | 缺口巡检间隔（秒） |
| `BINANCE_WS_SOURCE` | `binance_ws` | 数据来源标识 |
| `KLINE_DB_SCHEMA` | `market_data` | 数据库 schema |
| `BINANCE_WS_DB_EXCHANGE` | `binance_futures_um` | 交易所标识 |

### .env.example

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/market_data
HTTP_PROXY=http://127.0.0.1:7890
RATE_LIMIT_PER_MINUTE=1800
```

## 数据表

| 表名 | 说明 |
|------|------|
| `market_data.candles_1m` | 1分钟 K线 |
| `market_data.binance_futures_metrics_5m` | 5分钟期货指标 |

## 数据流

```
Binance
   │
   ├── WebSocket ──→ ws.py ──→ 3秒窗口缓冲 ──→ TimescaleDB (candles_1m)
   │
   ├── REST API ──→ metrics.py ──→ 批量写入 ──→ TimescaleDB (metrics_5m)
   │
   └── ZIP Files ──→ backfill.py ──→ COPY 写入 ──→ TimescaleDB
```

## 常见问题

### IP 被 Ban (418/429)

系统自动检测并等待解除。如需手动：
1. 查看日志中的 ban 解除时间
2. 等待后重启
3. 降低并发：`--workers 1`

### WebSocket 连接失败

```bash
# 检查代理
curl -x http://127.0.0.1:9910 https://fapi.binance.com/fapi/v1/ping

# 设置环境变量
export HTTP_PROXY=http://127.0.0.1:9910
export HTTPS_PROXY=http://127.0.0.1:9910
```

### 数据库连接失败

```bash
pg_isready -h localhost -p 5433
psql $DATABASE_URL -c "SELECT 1"
```
