# 任务 03：REST 指标采集迁移（细化版）

## 目标

- 迁移 REST 指标采集逻辑到严格路径：
  `binance/um_futures/all/realtime/pull/rest/metrics/http.py`

## 子任务拆分

- 03-01：Metrics wrapper 复用旧逻辑
- 03-02：Metrics 内聚化
- 03-03：限流/ban 逻辑一致性校验

## 关键依赖

- rate_limiter: acquire/release/parse_ban
- timescale: upsert_metrics
- metrics: 计数与计时
- config: HTTP_PROXY / db_exchange

## 执行步骤（更细）

1) 先用 wrapper 方式复用 `MetricsCollector`。
2) 复制 MetricsCollector 逻辑到新 <impl>.py。
3) 内聚 requests 会话池与限流逻辑。
4) 保持 5 分钟对齐与字段一致。

## 验收

- 字段一致、时间对齐一致。
- 请求失败与限流指标一致。


## 进度

- 已完成：03-01 Metrics wrapper 复用旧逻辑
- 已完成：03-02 内聚化、03-03 限流一致性校验
