# Datacat-Service 重构计划（极致详细版）

## 0. 总原则（不可违背）

1) **不得改动原服务**：`/home/lenovo/.projects/tradecat/services/data-service` 只读  
2) **仅在新服务内执行**：所有实现、重构、调整仅发生在 `services-preview/datacat-service`  
3) **结构即语义**：路径表达来源/市场/范围/模式/方向/通道/类型  
4) **粒度内聚**：interval/depth 等粒度只在 `impl.py` 内处理  
5) **实现即工具**：`<impl>.py` 仅代表工具/协议实现（ccxt/cryptofeed/http/http_zip/raw_ws/official_sdk）  

---

## 1. 输入与输出

### 1.1 输入（只读）

- 旧服务：`services/data-service`  
  - `src/collectors/*.py`  
  - `src/adapters/*.py`  
  - `src/__main__.py`  
  - `README.md`  

### 1.2 输出（重构目标）

- 新服务：`services-preview/datacat-service`  
  - 全量目录骨架 + 仅搬运有效实现  
  - 旧能力被拆分为新路径下的 `impl.py`  
  - 入口调度对齐新路径  

---

## 2. 新结构总览（必须一致）

```
collectors/<source>/<market>/<scope>/<mode>/<direction>/<channel>/<type>/<impl>.py
```

固定解释：
- source：binance / third_party / internal
- market：spot / um_futures / cm_futures / options
- scope：all / symbol_group / symbol
- mode：realtime / backfill / sync
- direction：push / pull / sync
- channel：rest / ws / file / stream / kafka / grpc
- type：klines / trades / aggTrades / metrics / bookDepth / bookTicker / indexPriceKlines / markPriceKlines / premiumIndexKlines / alpha …
- impl：ccxt / cryptofeed / http / http_zip / raw_ws / official_sdk

---

## 3. 旧模块 → 新路径映射（核心拆分清单）

### 3.1 collectors/ws.py
- 目标：
  - WS 实时采集主逻辑迁移
  - Gap 检测与回填触发解耦
- 新路径：
  - `binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py`
- 必拆内容：
  - 仅保留 WS 订阅 + 批量写入
  - Gap Scanner/Backfill 逻辑迁移至 backfill 路径

### 3.2 collectors/metrics.py
- 目标：
  - REST 指标采集迁移
- 新路径：
  - `binance/um_futures/all/realtime/pull/rest/metrics/http.py`
- 注意：
  - 不包含历史回填逻辑

### 3.3 collectors/backfill.py
- 目标：
  - ZIP 与 REST 回填彻底解耦
  - Gap Scanner 复用但分层清晰
- 新路径：
  - ZIP：`.../backfill/pull/file/klines/http_zip.py`
  - ZIP：`.../backfill/pull/file/metrics/http_zip.py`
  - REST：`.../backfill/pull/rest/klines/ccxt.py`
  - REST：`.../backfill/pull/rest/metrics/http.py`
- 禁止：
  - ZIP 逻辑出现在 REST 文件内
  - REST 逻辑出现在 ZIP 文件内

### 3.4 collectors/downloader.py
- 目标：
  - 作为 ZIP 通道内部辅助
- 新路径：
  - 逻辑内嵌到 `http_zip.py` 或抽出到 `libs/common`

### 3.5 collectors/alpha.py
- 目标：
  - Alpha Token 采集归档
- 新路径：
  - `binance/um_futures/all/sync/pull/rest/alpha/http.py`

### 3.6 adapters/*.py
- 目标：
  - 保持为公共适配层
  - 不进入层级路径
- 使用方式：
  - 每个 `<impl>.py` 内部引用

---

## 4. 入口调度适配策略

旧入口：`src/__main__.py` 调度 `collectors/ws.py / metrics.py / backfill.py`  
新入口：调度到新的 `impl.py` 路径（按模式/通道/类型拆分）

要求：
1) 入口必须覆盖 WS + REST + ZIP + Alpha  
2) 不存在“文件落位却不可运行”  
3) 入口支持未来扩展（type 列表可配置）

---

## 5. 任务分解（极致细粒度）

### Phase 0：约束与环境锁定
1) 记录旧服务路径与只读约束  
2) 在任务文档中写明“任何改动只发生在 services-preview”  
3) 对比 `services-preview/datacat-service` 与旧服务结构差异  
4) 固化“新结构定义与命名规范”  
5) 锁定“实现即工具”原则与可用 impl 列表  
6) 明确“粒度内聚”与“目录中不出现 interval/depth”

### Phase 1：清点旧服务能力
1) 逐文件读取 `collectors/ws.py`  
2) 标记：WS 入口函数、订阅逻辑、批量写入逻辑  
3) 标记：gap 检测与回填触发逻辑  
4) 逐文件读取 `collectors/metrics.py`  
5) 标记：REST 拉取流程、分页策略、写入逻辑  
6) 逐文件读取 `collectors/backfill.py`  
7) 标记：ZIP 逻辑、REST 逻辑、Gap Scanner 逻辑  
8) 逐文件读取 `collectors/downloader.py`  
9) 标记：下载/解压/缓存与错误处理  
10) 逐文件读取 `collectors/alpha.py`  
11) 标记：alpha 拉取入口与输出  
12) 逐文件读取 `adapters/*.py`  
13) 标记：每个 adapter 被调用的位置  
14) 输出旧 → 新能力映射表  
15) 对每个功能标记“可复用逻辑 / 需拆分 / 需改写”

### Phase 2：新结构骨架对齐
1) 新结构树必须完整  
2) 核对 source/market/scope/mode/direction/channel/type 全量枚举  
3) 对每个 `<type>` 建立 `<impl>.py` 文件位  
4) 核对 impl 清单是否仅限允许列表  
5) 非真实实现仅保留占位实现（不可覆盖真实实现）

### Phase 3：逐模块迁移（按类型拆）
1) ws → realtime/push/ws/klines/cryptofeed.py  
1.1) 新建目标文件头部结构（日志、配置、依赖）  
1.2) 从旧 ws.py 复制订阅与回调逻辑  
1.3) 保留批量写入与缓冲策略  
1.4) 删除/迁移 gap 回填逻辑  
1.5) 修正 import 路径到 adapters  
1.6) 添加 main/run 入口（可被 __main__ 调度）  

2) metrics → realtime/pull/rest/metrics/http.py  
2.1) 新建目标文件结构  
2.2) 复制 REST 拉取与写入逻辑  
2.3) 修正限流与代理参数读取  
2.4) 保留日志与错误处理  
2.5) 添加 main/run 入口  

3) backfill ZIP klines → backfill/pull/file/klines/http_zip.py  
3.1) 新建目标文件结构  
3.2) 复制 ZIP 下载/解压/解析/写库逻辑  
3.3) 若依赖 downloader，则内嵌到该文件  
3.4) 修正文件路径与缓存逻辑  
3.5) 添加 main/run 入口  

4) backfill ZIP metrics → backfill/pull/file/metrics/http_zip.py  
4.1) 新建目标文件结构  
4.2) 复制 ZIP 指标回填逻辑  
4.3) 修正路径与缓存  
4.4) 添加 main/run 入口  

5) backfill REST klines → backfill/pull/rest/klines/ccxt.py  
5.1) 新建目标文件结构  
5.2) 复制 REST 分页回填逻辑  
5.3) 修正 CCXT adapter 引用  
5.4) 粒度处理放在文件内部（非目录）  
5.5) 添加 main/run 入口  

6) backfill REST metrics → backfill/pull/rest/metrics/http.py  
6.1) 新建目标文件结构  
6.2) 复制 REST 指标回填逻辑  
6.3) 修正 HTTP 适配与限流  
6.4) 添加 main/run 入口  

7) alpha → sync/pull/rest/alpha/http.py  
7.1) 新建目标文件结构  
7.2) 复制 alpha 采集逻辑  
7.3) 明确输出与缓存策略  
7.4) 添加 main/run 入口  

8) downloader → 归并入 http_zip.py  
8.1) 识别 downloader 的关键函数  
8.2) 复制到 http_zip.py 中  
8.3) 删除对独立 downloader 文件的依赖  
8.4) 校验 ZIP 任务可独立运行

### Phase 4：入口与运行模型
1) `__main__.py` 增加新路径调度  
2) `--ws` 指向 realtime/push/ws/klines/cryptofeed.py  
3) `--metrics` 指向 realtime/pull/rest/metrics/http.py  
4) `--backfill` 同时调度 ZIP + REST 回填  
5) `--all` 覆盖所有任务  
6) alpha 入口：可选开关或纳入 `--all`  
7) 日志路径与子进程路径核对  
8) 保持旧 CLI 使用方式不变

### Phase 5：配置与依赖
1) adapter 引用路径全部校验  
2) 确保每个 impl 文件使用统一配置读取  
3) 代理设置遵循统一环境变量策略  
4) 限流器与 metrics 统计引用统一  
5) requirements.lock 与 requirements.txt 同步  
6) 记录依赖版本变更

### Phase 6：验证与回归
1) 静态导入检查（py_compile）  
2) 采集器最小启动检查  
3) ZIP 独立运行确认  
4) REST 独立运行确认  
5) WS 独立运行确认  
6) 入口调度覆盖性检查

---

## 6. 风险与控制

- 风险：逻辑复制时丢失边界条件  
  - 控制：逐函数核对，保持逻辑不阉割  
- 风险：入口漏调度导致“存在但不可运行”  
  - 控制：入口覆盖清单逐条对照  
- 风险：路径深度导致 import 断裂  
  - 控制：统一在 impl 中导入 adapters，避免跨层硬引用  

---

## 7. 交付物清单

1) 新结构完整目录树  
2) 旧逻辑迁移后的 impl 文件  
3) 入口调度更新  
4) 文档对齐（README + AGENTS + collectors/README）  
5) 任务记录（PLAN/TODO/validation）  

---

## 8. 约束复核

- 原服务不改动：✅  
- 新服务内重构：✅  
- 结构严格按规范：✅  
- 粒度内聚：✅  
