# Datacat Service Bugfix 计划（细粒度）

## 目标

- 彻底解耦回填 REST/ZIP，实现“路径即职责”。
- 符号兜底加载统一代理行为（DATACAT_HTTP_PROXY 可用）。
- 依赖锁定文件同步。
- Alpha 采集器可调度可运行。

## 问题清单

1) 回填 REST/ZIP 未解耦，路径职责混乱。
2) 符号兜底使用私有 REST，代理不一致。
3) 依赖锁定文件未同步。
4) Alpha 采集器无调度入口。

## 任务拆分

- Bugfix-01：回填解耦与入口修复
- Bugfix-02：符号兜底代理一致化
- Bugfix-03：依赖锁定同步
- Bugfix-04：Alpha 调度入口补齐

## 执行顺序

1) Bugfix-01
2) Bugfix-02
3) Bugfix-04
4) Bugfix-03

## 验收

- REST/ZIP 分层清晰，无重复实现。
- backfill pipeline 可运行，保持“先 ZIP 后 REST”。
- DATACAT_HTTP_PROXY 覆盖 REST 兜底符号加载。
- requirements.lock 与 requirements.txt 对齐。
- Alpha 可由入口触发执行。
