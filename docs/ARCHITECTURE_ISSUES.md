# TradeCat 架构问题复核（状态刷新）

> 刷新时间: 2026-03-04（rev13）  
> 复核范围: `services/`、`services-preview/`、`libs/`、`scripts/`、`.github/workflows/`  
> 复核目标: 将旧文档中的“问题数量统计”重刷，并标注为 **已验证 / 部分成立 / 过时**

---

## 0. 本次增量更新（rev13）

- 已修复 markets-service 命名空间不一致导致的 CLI/ProviderRegistry 失效问题。
- 已恢复 markets-service tests 对 `providers.*` 导入的可用性（pytest 收集恢复）。
- `services-preview/markets-service` 本地测试结果：`11 passed`（仅保留 pydantic deprecation warnings）。
- CI 已扩展为全仓检查：`services/`、`services-preview/`、`libs/`、`scripts/` 均纳入 ruff 与语法校验。
- `services/data-service` 已新增 11 个 smoke 测试（配置、下载器、调度），并补充 3 个采集链路集成测试（采集/解析/落盘）。
- 已在 CI 增加 `scripts/check_no_print_services.py`，阻止 `services*/src` 回归使用 `print()`。
- 已清理一批脚本层 `print()`（回填/同步/分析/导入脚本），全仓 `print()` 从 129 降到 106。
- 继续清理核心脚本（`download_hf_data.py`、`etf_backtest.py`），全仓 `print()` 进一步降到 53。
- 继续清理检查脚本与 shell 内联 Python 输出，`print()` 进一步降到 0（扫描范围内已清零）。
- 已将脚本侧 `.env` 加载统一到共享兼容入口 `scripts/lib/repo_env_loader.py`。
- 已移除工具模块中的 `load_dotenv` 直读，统一改为 `common.config_loader`。
- trading-service 已明确 `core` 为默认主引擎；`event/full_async` 进入实验态并由 `ENABLE_EXPERIMENTAL_ENGINES=1` 显式开启。
- `run_async_full` / `run_event_engine` 已增加运行时保护，避免被旁路入口误启用。
- 主引擎与实验引擎已解耦：`Engine/EventEngine` 的高优先级选择改为共享 `core/priority.py`，不再依赖 `async_full_engine`。
- `scripts/start.sh` / `scripts/init.sh` / `scripts/check_env.sh` 已统一接入 `scripts/lib/db_url.sh`，脚本侧 DATABASE_URL 解析口径对齐。
- Python 服务配置已统一改为 `common.db_url.resolve_database_url`，去除多处硬编码 URL fallback。
- 历史迁移脚本 `sync_from_old_db.sh` 已改为显式 `MIGRATION_OLD_DATABASE_URL` / `MIGRATION_NEW_DATABASE_URL`，运行时口径与迁移口径已分层。
- 下一主问题保持 **P1#10 引擎架构并行维护**（继续做代码层收敛）。

---

## 一、量化快照（旧值 vs 当前）

| 指标 | 旧文档（2026-03-02） | 当前复核（2026-03-04） | 结论 |
|:---|:---:|:---:|:---|
| `sys.path.insert()` | 27 | 0 | 问题已收敛（核心与辅助路径均清零） |
| 核心运行路径 `sys.path.insert()`（排除 tests/scripts） | 27（未区分） | 0 | 主链路已完成收敛 |
| `global` 关键字 | 21 | 0 | 问题已收敛（业务代码已清零） |
| `time.sleep()` | 43 | 2 | 已收敛到调度助手与测试用例 |
| async 中 `time.sleep()` | 未区分 | 0 | 已完成（通过脚本检查） |
| `print()` 调用 | 538 | 0 | 已清零（扫描范围内） |
| `except Exception: pass` | 未统计 | 0 | 已清零 |
| 裸 `except:` | 有 | 0 | 已清零 |

> 注：本表按当前代码实时扫描结果更新，旧文档中的数量统计已不再可直接使用。

---

## 二、问题清单（20 项状态刷新）

状态定义：
- **已验证**：问题仍然成立（或仍需作为风险项跟踪）
- **部分成立**：问题方向成立，但已有阶段性修复/收敛
- **过时**：原结论已不成立，或原描述已被新实现替代

| 优先级 | # | 问题 | 最新状态 | 刷新结论 |
|:---|:---:|:---|:---:|:---|
| P0 | 1 | `sys.path` 黑魔法泛滥 | **过时** | 全仓扫描已为 0，原问题描述不再成立 |
| P0 | 2 | 配置加载不统一 | **过时** | 服务与脚本统一走 `common.config_loader`（脚本兼容 fallback 仅保留单一入口） |
| P0 | 3 | 全局变量滥用 | **过时** | `global` 已降到 0，原问题描述不再成立 |
| P0 | 4 | 阻塞调用分层治理不足 | **过时** | 业务路径已收敛到 `common.scheduler`；仅剩 helper 内部实现与测试用例 |
| P1 | 5 | 公共库 `libs/` 未被使用 | **过时** | `tradecat-common` 已可安装并在各服务 Makefile 接入 editable 安装 |
| P1 | 6 | data-service 无测试 | **部分成立** | 已补 14 个测试（11 smoke + 3 采集链路集成），仍缺真实数据库/外部依赖场景集成测试 |
| P1 | 7 | CI 检查过于宽松 | **过时** | CI 已扩展至 `services-preview/`、`libs/`、`scripts/`，语法检查改为全量 tracked Python 文件 |
| P1 | 8 | 日志规范混乱（`print`） | **过时** | `services*/src` 与脚本扫描范围内 `print()` 已清零，且 CI 守护已启用 |
| P1 | 9 | 裸异常捕获 | **过时** | 裸 `except:` 与 `except Exception: pass` 均已清零 |
| P1 | 10 | 引擎架构并行维护 | **部分成立** | 默认主链路已收敛到 `Engine(core)`，且主链路已与实验引擎解耦；`EventEngine/FullAsyncEngine` 代码仍并行存在 |
| P2 | 11 | 依赖版本不一致 | **部分成立** | 服务级依赖仍分散维护，尚无统一策略 |
| P2 | 12 | 双数据库架构与端口口径 | **部分成立** | 运行时统一到 `DATABASE_URL`；双端口仅保留在迁移场景，且改为 `MIGRATION_*_DATABASE_URL` 显式指定 |
| P2 | 13 | 服务边界模糊（TUI 拉起服务） | **部分成立** | 已支持关闭自动拉起，但默认仍会自动启动 data/signal |
| P2 | 14 | 指标注册机制原始 | **已验证** | 仍为手工 import 注册，尚未自动发现 |
| P2 | 15 | 错误处理不统一 | **部分成立** | 有收敛进展，但 logger/print/raise 仍混用 |
| P2 | 16 | 子模块管理复杂 | **已验证** | `repository/` 多子仓库状态仍需治理 |
| P3 | 17 | 规则引擎硬编码 | **已验证** | 规则集合仍主要由代码拼装定义 |
| P3 | 18 | 可观测性碎片化 | **已验证** | 各服务日志与监控能力仍不统一 |
| P3 | 19 | 缺乏统一接口抽象层 | **部分成立** | 核心模块已有分层，但跨引擎接口仍未统一 |
| P3 | 20 | 配置验证缺失 | **部分成立** | 个别模块有校验，尚未形成全局统一校验框架 |

---

## 三、与当前推进清单的对齐结论

### P0（本周必须）

- **配置统一加载**：**已完成（统一到共享入口）**。  
- **去掉 `sys.path` hack**：**已完成（当前扫描 0）**。  
- **收敛全局变量**：**已完成（当前扫描 0）**。  
- **阻塞调用分层治理**：**已完成（业务代码散落调用已收敛）**。  
- **文档口径修正**：本文件已完成重刷并加状态标注。  

### P1（下周）

- **CI 全仓覆盖**：**已完成（覆盖 preview/libs/scripts）**。  
- **data-service 最小测试集**：**已完成（11 smoke + 3 采集链路集成）**，后续补真实数据库/外部依赖集成测试。  
- **日志规范 + lint 兜底**：**已完成（当前扫描 `print=0`）**。  
- **错误处理统一**：部分完成。  

### P2（两周内）

- **引擎收敛**：部分完成（默认主引擎已固定，实验引擎已加开关保护）。  
- **DB/端口单一真源**：部分完成（运行时与迁移口径已分层；后续重点是旧库退役策略）。  
- **服务边界修正**：部分完成（可选关闭已支持，默认策略待调整）。  
- **指标自动注册**：未完成。  

---

## 四、验收标准对照（最新）

| 验收项 | 目标 | 当前状态 |
|:---|:---|:---|
| `sys.path` 动态注入（核心运行路径） | 0（或仅 1 处过渡） | **达成**（当前 0） |
| `sys.path` 动态注入（全量含 tests/scripts） | 尽量趋近 0 | **达成**（当前 0） |
| `global` 数量下降 | 70%+ | **达成**（21 -> 0） |
| CI 覆盖核心目录 | 覆盖 preview/libs/scripts | **达成** |
| `except Exception: pass` | 清零 | **达成**（0） |
| 裸 `except:` | 清零或白名单注释 | **达成**（0） |
| 文档与代码状态一致 | 无过时计数 | **本次已刷新** |

---

## 五、复核命令（可复现）

```bash
rg -n "sys\\.path\\.insert\\s*\\(" services services-preview libs scripts -g '*.py' | wc -l
rg -n "\\bglobal\\b" services services-preview libs scripts -g '*.py' | wc -l
rg -n "time\\.sleep\\(" services services-preview libs scripts -g '*.py' | wc -l
python3 scripts/check_async_sleep.py
rg -n "\\bprint\\s*\\(" services services-preview libs scripts -g '*.py' -g '*.sh' | wc -l
rg -n "except\\s+Exception\\s*:\\s*pass" services services-preview libs scripts -g '*.py' | wc -l
rg -n "^\\s*except\\s*:\\s*$" services services-preview libs scripts -g '*.py' | wc -l
cd services-preview/markets-service && .venv/bin/pytest -q
```

---

## 六、下一步建议（按收益排序）

1. **P1#10 引擎收敛**：继续做代码层收敛（抽共享能力、下线重复路径），最终保留单主引擎实现。  
2. **P1#6 测试补齐**：继续补真实数据库与外部依赖（网络/限流）场景集成测试，形成更完整端到端闭环。  
3. **P2#12 DB/端口单一真源**：制定 5433 旧库退役计划（迁移窗口、回滚策略、最终下线时间）。  
4. **P2#15 错误处理统一**：统一 logger + 明确降级策略，减少 silent failure。  
5. **P2#13 服务边界修正**：TUI 默认只读，自动拉起 data/signal 改为显式开关。  
