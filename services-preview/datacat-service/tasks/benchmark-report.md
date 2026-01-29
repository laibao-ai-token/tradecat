# Datacat Service 基准测试报告

- 日期：2026-01-29
- 环境：Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39
- Python：3.12.3
- 采样符号：BTCUSDT, ETHUSDT

## Metrics（REST 并发采集）

- 行数：6
- 耗时：6.20s
- 吞吐：0.97 rows/s

## Klines（CCXT 单次拉取）

- 行数：1000
- 耗时：3.64s
- 吞吐：274.74 rows/s
