# 常用查询

## Datacat unified.db
```bash
sqlite3 $PROJECT_ROOT/datacat/libs/database/unified.db "SELECT type, COUNT(*) FROM events GROUP BY type;"
sqlite3 $PROJECT_ROOT/datacat/libs/database/unified.db "SELECT type, COUNT(*) FROM sources GROUP BY type;"
sqlite3 $PROJECT_ROOT/datacat/libs/database/unified.db "SELECT MAX(id), MAX(ts) FROM events;"
```

## Datacat unified.db（按币种命中扫描）
```bash
# 多币种 + 多类别（新闻/公告/信号/清算/链上/大额转账）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,BNB,ETH \
  --categories news,announcement,signal,liquidation,onchain,transfer \
  --since-hours 24 --scan-limit 5000 --format json

# 仅查 BTC + 清算（带 tag 与关键词命中）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories liquidation --since-hours 12 --scan-limit 2000

# 强制只查指定币种（不合并默认主流币）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BNB --strict-coins \
  --categories liquidation,transfer --since-hours 6 --scan-limit 2000

# 只看某个来源（label/source_key 模糊匹配）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,BNB --categories news,announcement --source-like Binance \
  --since-hours 24 --scan-limit 5000
```

## Tradecat 指标质量检查（空值/零值/时间缺口）
```bash
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/check_tradecat_metrics_quality.py \
  --symbol BTC --interval 4h --limit 200 --auto-start
```

## Tradecat signal_history.db
```bash
sqlite3 $PROJECT_ROOT/tradecat/libs/database/services/signal-service/signal_history.db "SELECT COUNT(*) FROM signal_history;"
```

## Tradecat market_data.db
```bash
sqlite3 $PROJECT_ROOT/tradecat/libs/database/services/telegram-service/market_data.db "SELECT COUNT(DISTINCT \"交易对\") FROM \"基础数据同步器.py\";"
```

### 基础数据按币种/周期
```bash
sqlite3 $PROJECT_ROOT/tradecat/libs/database/services/telegram-service/market_data.db \
  "SELECT 交易对, 周期, 数据时间, 成交额, 主动买卖比, 主动买额, 主动卖出额 \
   FROM \"基础数据同步器.py\" \
   WHERE 交易对='BNBUSDT' AND 周期='1h' \
   ORDER BY 数据时间 DESC LIMIT 200;"
```

### 主动买卖比缺失/为 0 统计
```bash
sqlite3 $PROJECT_ROOT/tradecat/libs/database/services/telegram-service/market_data.db \
  "SELECT COUNT(*) AS total, \
          SUM(CASE WHEN \"主动买卖比\" IS NULL THEN 1 ELSE 0 END) AS null_cnt, \
          SUM(CASE WHEN \"主动买卖比\"=0 THEN 1 ELSE 0 END) AS zero_cnt \
   FROM \"基础数据同步器.py\" \
   WHERE 交易对='BNBUSDT' AND 周期='1h';"
```
