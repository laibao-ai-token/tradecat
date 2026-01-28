# 任务 04：回填与文件下载迁移（细化版）

## 目标

- 拆分并迁移 backfill 与 downloader 逻辑。

## 子任务拆分

- 04-01：GapScanner 拆分与落位
- 04-02：ZIP K线回填迁移
- 04-03：ZIP Metrics 回填迁移
- 04-04：REST K线回填迁移
- 04-05：REST Metrics 回填迁移
- 04-06：回填统一入口整理

## 目标路径

- K线 ZIP：`.../backfill/pull/file/klines/interval_1m/http_zip/collector.py`
- Metrics ZIP：`.../backfill/pull/file/metrics/interval_5m/http_zip/collector.py`
- K线 REST：`.../backfill/pull/rest/klines/interval_1m/ccxt/collector.py`
- Metrics REST：`.../backfill/pull/rest/metrics/interval_5m/http/collector.py`

## 执行步骤（更细）

1) 拆出 GapScanner（K线/指标两类扫描）。
2) 将 ZipBackfiller 与 Downloader 合并到 file/…/collector.py。
3) 将 RestBackfiller/MetricsRestBackfiller 合并到 rest/…/collector.py。
4) 保持“先 ZIP 后 REST，复检”策略不变。
5) 保持 ZIP 缓存目录与清理策略一致。

## 验收

- K线与 Metrics 补齐流程一致。
- ZIP 与 REST 回填结果对齐。
