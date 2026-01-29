# Datacat Service 日志规范

## 1. 目标

- 统一日志格式，便于定位问题与监控接入。
- 支持 plain 与 json 两种输出。

## 2. 关键字段（JSON 模式）

```
ts / level / logger / msg / module / line / pid / thread / component / error_code / error_detail
```

## 3. 输出模式

- 默认：plain（兼容传统日志）
- 生产推荐：json（结构化采集）

配置：

```
DATACAT_LOG_FORMAT=plain|json
DATACAT_LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
DATACAT_LOG_FILE=service.log
```

## 4. 最小示例

```json
{"ts":"2026-01-29T07:30:00Z","level":"INFO","logger":"realtime.rest.metrics","msg":"保存 120 条","component":"realtime.rest.metrics"}
```

