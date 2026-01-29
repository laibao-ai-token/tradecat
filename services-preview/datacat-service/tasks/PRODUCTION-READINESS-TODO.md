# Datacat Service 生产补齐任务清单

## P0（阻塞上线）

- [x] A1：修复 `common.symbols` 依赖路径（PYTHONPATH 或迁入 `src/common`）
- [x] A2：更新启动脚本注入 `DATACAT_*` 与 `PYTHONPATH`
- [x] B1：重建 `requirements.lock.txt` 并记录 Python/Pip 版本
- [x] B2：clean venv 安装成功验证（无缺包）
- [x] D1：准备非生产测试库并完成 DB 连接验证

## P1（必须完成）

- [x] D2：WS 短跑验证（约 2 分钟，替代 24h）
- [x] D3：REST 指标单次写入验证（替代 24h）
- [x] D4：执行 ZIP → REST backfill 全流程（含缺口扫描）
- [ ] D5：生成 DB 端到端验收报告（validation-report-db.md）
- [x] E1：断线重连测试（WS）
- [x] E2：限流/ban 恢复测试（REST）

## P2（生产建议）

- [x] F1：补齐日志规范文档（字段/示例）
- [x] F2：健康检查脚本（DB ping + HTTP ping）
- [x] F3：运行基线报告（CPU/内存/吞吐）
