# TradeCat 重构组件使用指南

> 生成时间: 2026-01-29  
> 本文档描述重构后的核心组件使用方法

---

## 1. 统一配置管理 (libs/common/config.py)

### 1.1 概述

`libs/common/config.py` 提供 TradeCat 全局配置的统一入口，所有服务应从此模块导入配置，而非直接读取 `os.environ`。

### 1.2 安装与导入

```python
# 在服务的 src/ 目录下，添加 libs 路径
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[3] / "libs"))

# 导入配置
from common.config import config

# 或导入配置函数
from common.config import get_config
```

### 1.3 配置访问

```python
from common.config import config

# 数据库配置
timescale_url = config.database.timescale_url
sqlite_path = config.database.sqlite_market_data

# 服务配置
max_workers = config.service.max_workers
compute_backend = config.service.compute_backend

# 项目路径
project_root = config.project_root
```

### 1.4 配置结构

```python
TradeCatConfig
├── project_root: Path              # 项目根目录
├── database: DatabaseConfig
│   ├── timescale_url: str          # TimescaleDB 连接串
│   ├── sqlite_market_data: Path    # 指标数据库路径
│   ├── sqlite_cooldown: Path       # 冷却数据库路径
│   └── sqlite_history: Path        # 历史数据库路径
└── service: ServiceConfig
    ├── max_workers: int            # 并行工作线程数
    ├── compute_backend: str        # 计算后端 (thread/process)
    ├── default_locale: str         # 默认语言
    └── http_proxy: str             # HTTP 代理
```

### 1.5 环境变量覆盖

配置模块会自动加载 `config/.env` 文件，但环境变量优先级更高：

```bash
# 命令行覆盖
MAX_WORKERS=8 python -m src

# 或在启动脚本中
export DATABASE_URL="postgresql://user:pass@host:5434/db"
```

### 1.6 最佳实践

```python
# ✅ 推荐：使用统一配置
from common.config import config
conn = psycopg2.connect(config.database.timescale_url)

# ❌ 不推荐：直接读取环境变量
import os
conn = psycopg2.connect(os.getenv("DATABASE_URL"))

# ❌ 不推荐：硬编码路径
sqlite_path = "/path/to/market_data.db"
```

---

## 2. 统一日志配置 (libs/common/logging.py)

### 2.1 概述

`libs/common/logging.py` 提供统一的日志配置，支持 JSON Lines 格式输出，便于日志聚合分析。

### 2.2 基本用法

```python
from common.logging import setup_logging
import logging

# 初始化日志（在服务入口调用一次）
setup_logging(
    level="INFO",
    service="trading-service",
    json_format=True,
    log_file="logs/service.log"
)

# 使用标准 logging
logger = logging.getLogger(__name__)
logger.info("服务启动")
logger.error("处理失败", exc_info=True)
```

### 2.3 日志格式

**JSON 格式（推荐生产使用）**:
```json
{"ts":"2026-01-29T12:00:00Z","level":"INFO","logger":"main","msg":"服务启动","service":"trading-service"}
```

**文本格式（调试使用）**:
```
2026-01-29 12:00:00 - INFO - main - 服务启动
```

### 2.4 添加上下文字段

```python
import logging

logger = logging.getLogger(__name__)

# 创建带上下文的日志记录
extra = {"symbol": "BTCUSDT", "interval": "1h", "trace_id": "abc123"}
logger.info("计算指标", extra=extra)

# 输出：{"ts":"...","level":"INFO","msg":"计算指标","symbol":"BTCUSDT","interval":"1h","trace_id":"abc123"}
```

### 2.5 日志级别指南

| 级别 | 用途 | 示例 |
|:---|:---|:---|
| DEBUG | 详细调试信息 | SQL 查询、变量值 |
| INFO | 正常运行信息 | 服务启动、任务完成 |
| WARNING | 非致命问题 | 数据缺失、降级处理 |
| ERROR | 错误但服务继续 | API 调用失败、数据解析错误 |
| CRITICAL | 严重错误需人工介入 | 数据库连接失败、配置错误 |

---

## 3. 排行榜卡片基类 (BaseRankingCard)

### 3.1 概述

`BaseRankingCard` 提供排行榜卡片的通用实现，子类只需定义配置即可。

### 3.2 创建新卡片

```python
from cards.base_ranking_card import BaseRankingCard, CardConfig

class RSIRankingCard(BaseRankingCard):
    @property
    def config(self) -> CardConfig:
        return CardConfig(
            name="rsi_ranking",           # 唯一标识
            table="RSI相对强弱扫描器.py",   # SQLite 表名
            title_key="cards.rsi.title",  # i18n 标题键
            default_sort_field="RSI值",   # 排序字段
            default_sort_asc=True,        # 升序排列
            intervals=["5m", "15m", "1h", "4h", "1d"],
            direction_field="方向",       # 多/空 方向字段
        )
    
    def format_row(self, row: dict, rank: int) -> str:
        """自定义行格式"""
        direction = "🟢" if row.get("方向") == "多" else "🔴"
        rsi = row.get("RSI值", 0)
        symbol = row.get("币种", "N/A")
        return f"{rank}. {direction} {symbol} RSI={rsi:.1f}"
```

### 3.3 CardConfig 参数说明

| 参数 | 类型 | 必填 | 说明 |
|:---|:---|:---:|:---|
| `name` | str | ✅ | 卡片唯一标识，用于 callback_data |
| `table` | str | ✅ | SQLite 表名 |
| `title_key` | str | ✅ | i18n 翻译键 |
| `default_sort_field` | str | ✅ | 默认排序字段 |
| `default_sort_asc` | bool | - | 默认是否升序，默认 False |
| `default_limit` | int | - | 默认条数，默认 10 |
| `intervals` | List[str] | - | 可选周期列表 |
| `direction_field` | str | - | 方向字段名（如有） |

### 3.4 可覆盖方法

```python
class CustomCard(BaseRankingCard):
    def format_row(self, row: dict, rank: int) -> str:
        """格式化单行数据"""
        return f"{rank}. {row['币种']}"
    
    def format_message(self, data: list, interval: str) -> str:
        """格式化完整消息"""
        return super().format_message(data, interval) + "\n\n📝 自定义尾注"
    
    def build_keyboard(self, current_interval: str) -> InlineKeyboardMarkup:
        """构建自定义键盘"""
        keyboard = super().build_keyboard(current_interval)
        # 添加额外按钮...
        return keyboard
```

### 3.5 注册到 registry

```python
# cards/registry.py
from .advanced.rsi_ranking_card import RSIRankingCard

CARD_REGISTRY = {
    "rsi_ranking": RSIRankingCard,
    # ... 其他卡片
}
```

---

## 4. API 数据访问层 (Repositories)

### 4.1 概述

Repository 模式将数据访问逻辑与业务逻辑分离，提供统一的数据查询接口。

### 4.2 使用 TimescaleRepository

```python
from repositories.timescale import TimescaleRepository
from common.config import config

# 创建 repository
repo = TimescaleRepository(config.database.timescale_url)

# 查询 K 线数据
candles = repo.query("""
    SELECT * FROM market_data.candles_1m
    WHERE symbol = %s AND bucket_ts >= NOW() - INTERVAL '1 hour'
    ORDER BY bucket_ts DESC
""", ("BTCUSDT",))

# 结果是 List[Dict]
for candle in candles:
    print(f"{candle['bucket_ts']}: {candle['close']}")
```

### 4.3 使用 SQLiteRepository

```python
from repositories.sqlite import SQLiteRepository
from common.config import config

# 创建 repository
repo = SQLiteRepository(str(config.database.sqlite_market_data))

# 查询指标数据
indicators = repo.query("""
    SELECT * FROM 'RSI相对强弱扫描器.py'
    WHERE 周期 = ?
    ORDER BY RSI值 DESC
    LIMIT 10
""", ("1h",))
```

### 4.4 在 FastAPI 路由中使用

```python
from fastapi import APIRouter, Depends
from repositories.timescale import TimescaleRepository
from common.config import config

router = APIRouter()

def get_timescale_repo() -> TimescaleRepository:
    """依赖注入"""
    return TimescaleRepository(config.database.timescale_url)

@router.get("/candles/{symbol}")
async def get_candles(
    symbol: str,
    repo: TimescaleRepository = Depends(get_timescale_repo)
):
    candles = repo.query(
        "SELECT * FROM market_data.candles_1m WHERE symbol = %s LIMIT 100",
        (symbol,)
    )
    return {"data": candles}
```

### 4.5 扩展 Repository

```python
from repositories.base import BaseRepository

class CandleRepository(TimescaleRepository):
    """K线专用 Repository"""
    
    def get_latest(self, symbol: str, limit: int = 100):
        return self.query("""
            SELECT * FROM market_data.candles_1m
            WHERE symbol = %s
            ORDER BY bucket_ts DESC
            LIMIT %s
        """, (symbol, limit))
    
    def get_ohlcv_agg(self, symbol: str, interval: str, start_time, end_time):
        """获取聚合 K 线"""
        # 使用物化视图
        view = f"market_data.candles_{interval}_last"
        return self.query(f"""
            SELECT * FROM {view}
            WHERE symbol = %s AND bucket BETWEEN %s AND %s
        """, (symbol, start_time, end_time))
```

---

## 5. 信号规则开发指南

### 5.1 规则结构

```python
from rules.base import SignalRule, ConditionType

# 定义规则
my_rule = SignalRule(
    id="rsi_oversold",                    # 唯一 ID
    name="RSI 超卖",                       # 显示名称
    category="momentum",                   # 分类
    table="RSI相对强弱扫描器.py",           # 数据表
    condition_type=ConditionType.LESS,     # 条件类型
    field="RSI值",                         # 判断字段
    threshold=30,                          # 阈值
    cooldown=300,                          # 冷却时间（秒）
    message_template="{symbol} RSI={value:.1f} 超卖信号",
)
```

### 5.2 ConditionType 枚举

| 类型 | 说明 | 示例 |
|:---|:---|:---|
| `GREATER` | 大于阈值 | RSI > 70 |
| `LESS` | 小于阈值 | RSI < 30 |
| `EQUAL` | 等于阈值 | 方向 == "多" |
| `BETWEEN` | 区间内 | 30 < RSI < 70 |
| `CHANGE` | 变化超过阈值 | 价格变化 > 5% |

### 5.3 注册规则

```python
# rules/momentum/__init__.py
from .rsi_rules import RSI_OVERSOLD, RSI_OVERBOUGHT

MOMENTUM_RULES = [
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    # ... 其他规则
]
```

---

## 6. 常见问题

### Q: 如何切换数据库端口？

修改 `config/.env` 中的 `DATABASE_URL`：
```
DATABASE_URL=postgresql://postgres:postgres@localhost:5434/market_data
```

### Q: 如何添加新的配置项？

1. 在 `libs/common/config.py` 中添加字段
2. 在 `config/.env.example` 中添加示例
3. 更新 AGENTS.md 文档

### Q: 日志文件在哪里？

- 顶层守护进程: `logs/daemon.log`
- 各服务日志: `services/<name>/logs/*.log`
- 预览服务日志: `services-preview/<name>/logs/*.log`

### Q: 如何调试卡片？

```python
# 在 Python REPL 中测试
from cards.advanced.rsi_ranking_card import RSIRankingCard

card = RSIRankingCard()
data = card.get_data(interval="1h", limit=5)
print(card.format_message(data, "1h"))
```
