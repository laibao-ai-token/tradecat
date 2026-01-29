# Datacat Service DB 验证报告（生产前）

## 1. 连接检查

- 时间：2026-01-29T11:42:01Z
- 数据库：market_data
- 连接状态：✅ 成功
- 代理：http://127.0.0.1:7890

## 2. 短跑写入验证

- WS 实时写入：短跑约 2 分钟（见 logs/ws-quick.log）
- REST 指标写入：单次写入成功（见 logs/metrics-quick.log）

## 3. Backfill 执行

- 已启动 backfill pipeline（见 logs/backfill-once.log）

## 4. 备注

- 当前为“短跑验证”，未执行 24h 长跑。
