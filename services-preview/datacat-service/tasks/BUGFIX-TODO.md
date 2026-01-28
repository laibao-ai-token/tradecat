# Bugfix TODO（细粒度执行清单）

- [x] Bugfix-01：回填解耦与入口修复
  - [x] 01-01：REST K线回填文件裁剪，仅保留 REST 逻辑
  - [x] 01-02：ZIP K线/ZIP Metrics 文件职责确认
  - [x] 01-03：REST Metrics 文件职责确认
  - [x] 01-04：__main__ 回填 pipeline 顺序执行（ZIP → REST）
  - [x] 01-05：文档同步（README/collectors/AGENTS 变更日志）

- [x] Bugfix-02：符号兜底代理一致化
  - [x] 02-01：统一注入 HTTP_PROXY/HTTPS_PROXY（DATACAT_HTTP_PROXY）
  - [x] 02-02：校验所有使用 REST 兜底的 collector

- [x] Bugfix-03：依赖锁定同步
  - [x] 03-01：生成 requirements.lock.txt

- [x] Bugfix-04：Alpha 调度入口补齐
  - [x] 04-01：__main__ 增加 alpha 入口
  - [x] 04-02：文档同步
