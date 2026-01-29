# 关键路径速查

> 说明：请先设置 `PROJECT_ROOT=/home/lenovo/.projects`，以下路径以 `$PROJECT_ROOT` 为根。

## Datacat
- 服务入口: $PROJECT_ROOT/datacat/services/api-service/src/main.py
- 节点服务: $PROJECT_ROOT/datacat/services/api-service/src/node.py
- Telegram 推送: $PROJECT_ROOT/datacat/services/api-service/src/telegram_bot.py
- 统一库: $PROJECT_ROOT/datacat/libs/database/unified.db
- 统一库 SDK: $PROJECT_ROOT/datacat/libs/common/unified_writer.py

## Tradecat
- API 服务入口: $PROJECT_ROOT/tradecat/services-preview/api-service/src/app.py
- 路由目录: $PROJECT_ROOT/tradecat/services-preview/api-service/src/routers/
- 配置: $PROJECT_ROOT/tradecat/services-preview/api-service/src/config.py
- 币种管理: $PROJECT_ROOT/tradecat/libs/common/symbols.py
- 指标库: $PROJECT_ROOT/tradecat/libs/database/services/telegram-service/market_data.db
- 信号历史库: $PROJECT_ROOT/tradecat/libs/database/services/signal-service/signal_history.db
- 信号历史逻辑: $PROJECT_ROOT/tradecat/services/signal-service/src/storage/history.py

## 数据源
- PostgreSQL/TimescaleDB: 由 tradecat 的 DATABASE_URL 指向
- SQLite: unified.db / market_data.db / signal_history.db
