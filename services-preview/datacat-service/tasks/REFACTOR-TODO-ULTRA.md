# Datacat-Service 重构 TODO（极致细粒度）

> 目标：**只读旧服务**，在新服务内完成完整迁移与结构对齐。

## A. 只读约束与审计
- [x] 记录旧服务路径与只读约束（禁止任何写入）
- [x] 在任务文档头部写明“仅操作 services-preview”
- [x] 对旧服务 `collectors/` 全文件清点
- [x] 对旧服务 `adapters/` 全文件清点
- [x] 对旧服务 `__main__.py` 调度逻辑清点
- [x] 固化“旧功能 → 新路径”的一对一映射清单
- [x] 列出旧服务所有入口命令与运行方式
- [x] 列出旧服务所有配置项与环境变量

## B. 新结构骨架一致性
- [x] 新结构树与标准模板完全一致
- [x] `<channel>` 必须包含 rest/ws/file/stream/kafka/grpc
- [x] `<type>` 必须包含 klines/trades/aggTrades/metrics/bookDepth/bookTicker/indexPriceKlines/markPriceKlines/premiumIndexKlines/alpha
- [x] `<impl>.py` 文件名只允许：ccxt/cryptofeed/http/http_zip/raw_ws/official_sdk
- [x] 粒度只允许出现在实现文件内部（禁止目录化）

## C. 逐文件迁移任务（不可阉割逻辑）

### C1. WebSocket 1m K线
- [x] 从 `collectors/ws.py` 复制 WS 订阅与批量写入逻辑
- [x] 落位到 `.../realtime/push/ws/klines/cryptofeed.py`
- [x] 仅保留 WS 订阅、缓冲、批量写入逻辑
- [x] 删除/迁移 gap 回填逻辑到 backfill
- [x] 适配 adapters 引用路径
- [x] 添加 run/main 入口函数
- [x] 记录导入依赖与环境变量

### C2. REST Metrics
- [x] 从 `collectors/metrics.py` 复制 REST 指标采集逻辑
- [x] 落位到 `.../realtime/pull/rest/metrics/http.py`
- [x] 保持限流逻辑一致
- [x] 修正代理读取逻辑一致性
- [x] 添加 run/main 入口函数

### C3. ZIP 回填（Klines）
- [x] 从 `collectors/backfill.py` 抽离 ZIP Klines 回填逻辑
- [x] 落位到 `.../backfill/pull/file/klines/http_zip.py`
- [x] Zip/解压/写库流程完整保留
- [x] 合并 downloader 逻辑
- [x] 添加 run/main 入口函数

### C4. ZIP 回填（Metrics）
- [x] 从 `collectors/backfill.py` 抽离 ZIP Metrics 回填逻辑
- [x] 落位到 `.../backfill/pull/file/metrics/http_zip.py`
- [x] 合并 downloader 逻辑
- [x] 添加 run/main 入口函数

### C5. REST 回填（Klines）
- [x] 从 `collectors/backfill.py` 抽离 REST Klines 回填逻辑
- [x] 落位到 `.../backfill/pull/rest/klines/ccxt.py`
- [x] 分页/时间窗口策略保持一致
- [x] 适配 CCXT adapter 引用
- [x] 添加 run/main 入口函数

### C6. REST 回填（Metrics）
- [x] 从 `collectors/backfill.py` 抽离 REST Metrics 回填逻辑
- [x] 落位到 `.../backfill/pull/rest/metrics/http.py`
- [x] 适配 HTTP adapter 引用
- [x] 添加 run/main 入口函数

### C7. Alpha
- [x] 从 `collectors/alpha.py` 复制逻辑
- [x] 落位到 `.../sync/pull/rest/alpha/http.py`
- [x] 适配环境变量
- [x] 添加 run/main 入口函数

### C8. Downloader
- [x] 从 `collectors/downloader.py` 抽取 ZIP 通用逻辑
- [x] 合并到 `http_zip.py` 内部
- [x] 删除对 downloader 的依赖
- [x] 复核 Zip 任务可独立运行

## D. 入口调度一致性
- [x] `__main__.py` 改为调度新路径
- [x] `--ws` 启动 WS 采集器
- [x] `--metrics` 启动 REST 指标采集器
- [x] `--backfill` 启动 ZIP + REST 回填
- [x] `--all` 覆盖全部（含 alpha 可选）
- [x] 任何采集器不允许“存在但不可运行”
- [x] 记录每个子进程命令路径
- [x] 日志文件名与新任务名称对齐

## E. 依赖与配置对齐
- [x] adapters 引用路径统一
- [x] proxy / limit / db 统一从 config 读取
- [x] requirements.lock 与 requirements.txt 一致
- [x] 运行日志路径统一
- [x] 记录依赖版本变更
- [x] 输出配置矩阵（默认值/来源/优先级）

## F. 验证（最小可执行）
- [x] py_compile 通过
- [x] 入口启动无 import 错误
- [x] REST 指标任务单独启动通过
- [x] ZIP 回填任务单独启动通过
- [x] WS 任务单独启动通过
- [x] alpha 任务单独启动通过
- [x] 入口调度覆盖性检查

## G. 文档同步（不可缺失）
- [x] README：结构树、入口、运行方式更新
- [x] AGENTS：模块职责与边界更新
- [x] collectors/README：规范与模板更新
- [x] 任务变更日志记录

## H. 拆分偏差修复（必须完成）

### H1. WS 文件去回填残留
- [x] 扫描 `.../realtime/push/ws/klines/cryptofeed.py` 内是否存在 ZIP/REST 回填逻辑
- [x] 删除 BINANCE_DATA_URL / ZipBackfiller / RestBackfiller / GapScanner 相关代码
- [x] 仅保留 WS 订阅、缓冲、批量写入逻辑
- [x] 若需要缺口触发，保留“触发钩子”而非回填实现

### H2. REST/ZIP 完全解耦
- [x] 扫描 `.../backfill/pull/rest/klines/ccxt.py` 是否残留 ZipBackfiller/MetricsRestBackfiller/DataBackfiller
- [x] 扫描 `.../backfill/pull/rest/metrics/http.py` 是否残留 ZIP 逻辑
- [x] 扫描 `.../backfill/pull/file/klines/http_zip.py` 是否残留 REST 逻辑
- [x] 扫描 `.../backfill/pull/file/metrics/http_zip.py` 是否残留 REST 逻辑
- [x] 确保 ZIP 文件内仅包含“下载/解压/解析/写库”
- [x] 确保 REST 文件内仅包含“REST 分页/拉取/写库”

### H3. 入口调度完整覆盖
- [x] `__main__.py` 调度项包含 WS + REST metrics + REST backfill + ZIP backfill
- [x] Alpha 采集器加入入口或明确为手动运行（需在 README 标注）
- [x] 入口命令与日志文件名对齐新路径

---

## 完成标准（必须全部满足）

1) 新结构完整  
2) 旧逻辑不阉割  
3) 入口可运行  
4) 文档同步  
5) 旧服务零修改  
