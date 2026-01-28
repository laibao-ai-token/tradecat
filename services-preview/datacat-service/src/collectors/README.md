# Datacat Service - Collectors README（严格规范）

本文档是采集层的**固定标准结构模板**，用于长期基建的严格分层约束。任何变更必须遵守本模板。

---

## 1. 严格分层顺序（不可变）

```
source → market → scope → mode → direction → channel → type
```

> 粒度（interval/depth）不再作为目录层级，统一在实现文件内部处理。

---

## 2. 各层定义（逐层固定含义）

1) source：数据来源（交易所/第三方/内部）
2) market：市场与产品（现货/合约/期权）
3) scope：符号范围（全市场/分组/单一标的）
4) mode：采集场景（实时/回填/同步）
5) direction：方向（拉取/推送/同步）
6) channel：通道协议（REST/WS/FILE/STREAM/GRPC/KAFKA）
7) type：数据类型（klines/trades/aggTrades/metrics/bookDepth/bookTicker/.../alpha）

---

## 3. 固定取值集合（当前可用全集）

- source: binance / third_party / internal
- market: spot / um_futures / cm_futures / options
- scope: all / symbol_group / single_symbol
- mode: realtime / backfill / sync
- direction: pull / push / sync
- channel: rest / ws / file / stream / grpc / kafka
- type: aggTrades / bookDepth / bookTicker / indexPriceKlines / klines / markPriceKlines / metrics / premiumIndexKlines / trades / alpha
- impl (文件名): http / ccxt / cryptofeed / raw_ws / official_sdk / http_zip

> 注意：新增取值只能扩展集合，**禁止删除**或重排已有层级。

---

## 4. 标准路径模板（必须遵守）

```
<source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/<impl>.py
```

---

## 5. 示例路径（标准对齐）

```
binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py
binance/um_futures/all/backfill/pull/rest/metrics/http.py
binance/um_futures/all/backfill/pull/file/klines/http_zip.py
binance/um_futures/all/sync/pull/rest/alpha/http.py
third_party/spot/all/sync/pull/rest/metrics/http.py
internal/um_futures/symbol_group/sync/sync/kafka/trades/official_sdk.py
```

---

## 6. 目录边界与职责

- collectors 只负责采集/回填/拉取及必要的落库，不做业务规则与策略计算。
- 采集实现必须位于 `type/` 下，以 `<impl>.py` 命名。
- 粒度（interval/depth）在实现文件内部约束与处理，不作为目录层级。

---

## 7. 命名与风格（必须遵守）

- 目录名使用小写英文或既定驼峰类型名（如 `aggTrades`）。
- 文件名固定为实现工具名（如 `http.py`、`ccxt.py`）。
- 注释与文案使用中文。

---

## 8. 变更日志

- 2026-01-28: 初始化严格分层采集目录结构与固定模板。
- 2026-01-28: 由“granularity/impl 目录”改为“impl 文件”，粒度转为文件内部参数。
