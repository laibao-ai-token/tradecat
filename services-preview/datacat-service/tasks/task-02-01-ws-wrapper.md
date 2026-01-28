# 任务 02-01：WS wrapper 复用旧逻辑

## 目标

- 在新 <impl>.py 中以 wrapper 方式调用旧 WSCollector。

## 执行记录（已完成）

- 已在目标路径创建 wrapper：
  `services-preview/datacat-service/src/collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py`
- 通过 `_legacy_src()` 定位旧服务 `services/data-service/src` 并导入 `collectors.ws.WSCollector`

## 验收

- 可运行且行为等价。
