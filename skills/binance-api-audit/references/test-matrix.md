# 测试矩阵（30 场景）

说明：全部为只读扫描，用于验证币种命中逻辑在多场景下的稳定性。  
最近一次执行产物目录：`/home/lenovo/.projects/.history/skills/binance-api-audit/test_runs_30_20260129085156`

```bash
# 01 多币种 + 全类别
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,BNB,ETH --categories news,announcement,signal,liquidation,onchain,transfer \
  --since-hours 24 --scan-limit 8000 --format json

# 02 严格 BNB + 清算/转账
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BNB --strict-coins --categories liquidation,transfer \
  --since-hours 12 --scan-limit 6000 --format json

# 03 新闻/公告 + 来源过滤
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories news,announcement --source-like Binance \
  --since-hours 72 --scan-limit 10000 --format json

# 04 信号（tag 模式）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,ETH --categories signal --category-mode tag \
  --since-hours 24 --scan-limit 8000 --format json

# 05 清算（keyword 模式）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories liquidation --category-mode keyword \
  --keywords liquidation,爆仓,清算 --since-hours 24 --scan-limit 8000 --format json

# 06 链上（keyword 模式）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,ETH --categories onchain --category-mode keyword \
  --keywords tx,hash,地址,链上,区块 --since-hours 24 --scan-limit 8000 --format json

# 07 转账（keyword + 排除）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories transfer --category-mode keyword \
  --keywords whale,大额,transfer,withdrawal,deposit --exclude-keywords 测试,test \
  --since-hours 24 --scan-limit 8000 --format json

# 08 SQL 层 tag 过滤
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories signal --tags 信号 \
  --since-hours 48 --scan-limit 12000 --format json

# 09 SQL 层 type 过滤
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories news --types news \
  --since-hours 168 --scan-limit 12000 --format json

# 10 仅币种命中（不指定类别）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --since-hours 6 --scan-limit 4000 --format json

# 11 最小内容长度过滤
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,ETH --categories signal,liquidation \
  --min-content-len 200 --since-hours 24 --scan-limit 8000 --format json

# 12 since_id 增量
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,BNB,ETH --categories news,signal,liquidation,transfer \
  --since-id <MAX_ID-20000> --scan-limit 12000 --format json

# 13 SOL 组合场景
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins SOL --strict-coins --categories signal,transfer \
  --since-hours 24 --scan-limit 8000 --format json

# 14 XRP 长窗口
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins XRP --strict-coins --categories news,announcement \
  --since-hours 168 --scan-limit 12000 --format json

# 15 表格输出
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories liquidation --since-hours 1 --scan-limit 3000 --format table

# 16 ETH 链上（AND 模式）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins ETH --strict-coins --categories onchain --category-mode and \
  --keywords tx,hash,地址 --since-hours 24 --scan-limit 8000 --format json

# 17 BNB 信号（AND 模式）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BNB --strict-coins --categories signal --category-mode and \
  --keywords 做多,做空,signal --since-hours 48 --scan-limit 12000 --format json

# 18 清算（tag only）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories liquidation --category-mode tag \
  --since-hours 24 --scan-limit 8000 --format json

# 19 链上（tag only）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories onchain --category-mode tag \
  --since-hours 72 --scan-limit 12000 --format json

# 20 新闻（keyword only）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories news --category-mode keyword \
  --keywords 快讯,突发,breaking --since-hours 72 --scan-limit 12000 --format json

# 21 公告（keyword only）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories announcement --category-mode keyword \
  --keywords 公告,上线,下线,listing --since-hours 168 --scan-limit 12000 --format json

# 22 舆情（tag）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories sentiment --category-mode tag \
  --since-hours 168 --scan-limit 12000 --format json

# 23 推特（tag）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories twitter --category-mode tag \
  --since-hours 168 --scan-limit 12000 --format json

# 24 来源过滤（Whale）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,ETH --categories transfer --source-like Whale \
  --since-hours 168 --scan-limit 12000 --format json

# 25 来源过滤（Binance）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories signal,liquidation --source-like Binance \
  --since-hours 168 --scan-limit 12000 --format json

# 26 多币种（严格）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC,ETH,BNB,SOL,XRP,DOGE,ADA,AVAX --strict-coins \
  --categories signal,liquidation,transfer \
  --since-hours 12 --scan-limit 12000 --format json

# 27 小窗口
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories transfer \
  --since-hours 1 --scan-limit 300 --format json

# 28 长窗口 30 天
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories news,announcement \
  --since-hours 720 --scan-limit 20000 --format json

# 29 仅币种（严格）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins DOGE --strict-coins --since-hours 24 --scan-limit 8000 --format json

# 30 排除关键词
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/query_unified_events.py \
  --coins BTC --categories signal,transfer \
  --exclude-keywords 机器人,bot,测试,test \
  --since-hours 168 --scan-limit 12000 --format json
```

## 附加检查（基础数据，不计入 30 场景）

```bash
# A1 基础数据直读（SQLite）
curl -sS "http://127.0.0.1:8088/api/futures/base-data?symbol=BNB&interval=1h&limit=200"

# A2 基础数据质量检查（默认 SQLite）
python3 $PROJECT_ROOT/skills/binance-api-audit/scripts/check_tradecat_metrics_quality.py \
  --symbol BNB --interval 1h --format json
```
