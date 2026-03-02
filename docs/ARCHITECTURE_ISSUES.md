# TradeCat 架构问题汇总

> 生成时间: 2026-03-02
> 分析范围: services/、services-preview/、libs/、scripts/

---

## 问题清单（20 项）

| 优先级 | # | 问题 | 影响 | 发现位置 |
|:---|:---|:---|:---|:---|
| **P0** | 1 | `sys.path` 黑魔法泛滥 | 可维护性 | 27 处 `sys.path.insert()` |
| **P0** | 2 | 配置加载不统一 | 一致性 | 3 种不同实现（trading/signal/data） |
| **P0** | 3 | 全局变量滥用 | 可测试性 | 21 处 `global` 声明 |
| **P0** | 4 | 同步阻塞调用泛滥 | 性能 | 43 处 `time.sleep()` |
| **P1** | 5 | 公共库 `libs/` 未被使用 | 代码复用 | 无标准 import，依赖 sys.path hack |
| **P1** | 6 | data-service 无测试 | 质量 | tests/ 目录为空 |
| **P1** | 7 | CI 检查过于宽松 | 质量 | 只抽样 50 文件、忽略 E402 |
| **P1** | 8 | 日志规范混乱 | 可观测性 | 538 处 `print()` |
| **P1** | 9 | 裸 `except:` 捕获 | 稳定性 | 吞掉所有异常 |
| **P1** | 10 | 引擎架构混乱 | 可维护性 | 3 个并行实现（Engine/EventEngine/FullAsyncEngine） |
| **P2** | 11 | 依赖版本不一致 | 可维护性 | 各服务 requirements.txt 版本声明不同 |
| **P2** | 12 | 双数据库架构 | 复杂度 | TimescaleDB + SQLite，端口混乱 |
| **P2** | 13 | 服务边界模糊 | 可扩展性 | tui-service 可启动其他服务 |
| **P2** | 14 | 指标注册机制原始 | 扩展性 | 手动注册，无自动发现 |
| **P2** | 15 | 错误处理不统一 | 可维护性 | logger/print/raise 混用 |
| **P2** | 16 | 子模块管理混乱 | 可维护性 | repository/ 下 7 个子仓库 |
| **P3** | 17 | 规则引擎硬编码 | 灵活性 | 129 条规则在代码中定义 |
| **P3** | 18 | 可观测性碎片化 | 运维 | 各服务日志格式不同 |
| **P3** | 19 | 缺乏接口抽象层 | 可扩展性 | 138 个类无统一协议 |
| **P3** | 20 | 配置验证缺失 | 稳定性 | 无格式校验、无连接测试 |

---

## P0 - 严重问题

### 1. sys.path 黑魔法泛滥

**发现位置**: 27 处 `sys.path.insert()`

```python
# data-service/src/collectors/ws.py:26
sys.path.insert(0, str(Path(__file__).parent.parent))

# trading-service/src/core/compute.py:32
if service_root not in sys.path:
    sys.path.insert(0, service_root)

# signal-service/src/engines/pg_engine.py:166
if libs_path not in sys.path:
    sys.path.insert(0, libs_path)
```

**问题**：
- 隐式依赖，难以追踪模块来源
- 不同入口点行为不一致
- IDE 静态分析失效
- 测试时 import 顺序敏感

**建议**：
1. 统一使用 `pyproject.toml` 的 `[project.scripts]` 定义入口
2. 或在服务根目录放置 `__init__.py` + 正确的 `PYTHONPATH` 设置
3. 删除所有运行时 `sys.path` 操作

---

### 2. 配置加载不统一

**发现位置**: 3 种不同实现

| 服务 | 配置加载方式 |
|:---|:---|
| trading-service | 手动解析 `.env` 文件 + `os.environ.setdefault()` |
| signal-service | 同上，且在 `pg_engine.py` 重复实现 |
| data-service | 同上 |

```python
# trading-service/src/config.py:21-32
_env_file = REPO_ROOT / "config" / ".env"
with open(_env_file) as f:
    for line in f:
        # 手动解析...
        os.environ.setdefault(k, v)
```

**问题**：
- 重复代码
- 不处理 `.env` 中的引号、转义、注释
- 每个服务自行计算 `REPO_ROOT`

**建议**：
1. 统一使用 `python-dotenv` 的 `load_dotenv()`
2. 或抽取到 `libs/common/config.py`

---

### 3. 全局变量滥用

**发现位置**: 21 处 `global` 声明

```python
# trading-service/src/simple_scheduler.py:62
global _sqlite_conn

# trading-service/src/db/cache.py:304
global _global_cache

# signal-service/src/engines/pg_engine.py:1470
global _pg_engine
```

**问题**：
- 单例模式滥用，难以测试
- 隐式状态，难以追踪依赖
- 多线程/进程环境下的竞态风险

**建议**：
1. 使用依赖注入或工厂模式
2. 或使用 `functools.cache` 装饰器
3. 测试时可 mock 替换

---

### 4. 同步阻塞调用泛滥

**发现位置**: 43 处 `time.sleep()`

```python
# trading-service/src/simple_scheduler.py:398
time.sleep(10)

# data-service/src/__main__.py:49
time.sleep(5)

# tui-service/src/tui.py:2944
time.sleep(sleep_for)
```

**问题**：
- 阻塞整个事件循环
- 假异步（声明 async 但内部阻塞）
- 性能瓶颈

**建议**：
1. 在异步环境中改用 `asyncio.sleep()`
2. 或将阻塞操作放到线程池 `asyncio.to_thread()`

---

## P1 - 中等问题

### 5. 公共库 `libs/` 未被使用

**发现**: `libs/common/` 只有 5 个模块（i18n、symbols、proxy_manager、utils），但各服务直接用 `sys.path` hack 引入

```python
# 期望用法
from libs.common.symbols import get_symbols

# 实际用法（通过 sys.path hack）
sys.path.insert(0, _libs_path)
from symbols import get_symbols  # 隐式依赖
```

**建议**：
1. 将 `libs/` 打包为 `tradecat-common` 包
2. 各服务通过 `requirements.txt` 依赖
3. 或使用 workspace 机制

---

### 6. data-service 无测试

```
services/data-service/tests/
├── __init__.py
└── conftest.py  # 仅配置文件，无测试用例
```

**建议**：补充核心功能测试

---

### 7. CI 检查过于宽松

```yaml
# .github/workflows/ci.yml
- name: Lint with ruff
  run: ruff check services/ --ignore E501,E402  # 忽略长行和导入位置

- name: Syntax check
  run: find services -name "*.py" -type f | head -50 | xargs ...  # 只检查前50个文件
```

**问题**：
- 忽略 `E402`（导入位置）掩盖了模块结构问题
- 语法检查只抽样 50 个文件
- 无测试执行
- 无类型检查（mypy）

**建议**：
1. 移除 E402 忽略
2. 检查所有文件
3. 添加 pytest 执行
4. 添加 mypy 类型检查

---

### 8. 日志规范混乱

**发现**: 538 处 `print()` 调用

```python
# services/trading-service/src/simple_scheduler.py:82
print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

# scripts/download_hf_data.py
print("=" * 50)
print("  HuggingFace 历史数据下载工具")
```

**问题**：
- 无法统一控制日志级别
- 无法结构化输出（JSON）
- 生产环境难以追踪

**建议**：统一使用 `logging` 模块

---

### 9. 裸 `except:` 捕获

```python
# libs/common/utils/LLM客户端.py:172
except:
    print(response)
```

**问题**：
- 吞掉所有异常（包括 KeyboardInterrupt）
- 无法定位问题根因
- 违反 AGENTS.md 明确禁止的规范

**建议**：改用 `except Exception as e:` 并记录日志

---

### 10. 引擎架构混乱

| 引擎 | 文件 | 状态 |
|:---|:---|:---|
| `Engine` | `core/engine.py` | 同步版 |
| `EventEngine` | `core/event_engine.py` | 事件驱动版 |
| `FullAsyncEngine` | `core/async_full_engine.py` | 全异步版 |

**问题**：
- 功能重叠，维护成本高
- 调用者不知道用哪个
- 测试覆盖分散

**建议**：收敛到单一实现

---

## P2 - 设计问题

### 11. 依赖版本不一致

| 依赖 | data-service | trading-service | signal-service |
|:---|:---|:---|:---|
| `psycopg` | `>=3.1.0` | `>=3.1.0` | `>=3.1.0` ✅ |
| `pandas` | - | `>=2.0.0` | - |
| `numpy` | - | `>=1.24.0` | - |

**建议**：创建 `requirements-common.txt` 存放共享依赖

---

### 12. 双数据库架构

```
TimescaleDB (:5434)     SQLite
├── candles_1m          ├── market_data.db (指标结果)
├── futures_metrics_5m  ├── cooldown.db (冷却状态)
└── ...                 └── signal_history.db (信号历史)
```

**问题**：
- 数据双写风险（PG→SQLite 同步）
- 端口混乱（5433 vs 5434）
- 回测时数据源选择复杂

**建议**：逐步收敛到单一数据源（推荐 TimescaleDB）

---

### 13. 服务边界模糊

| 服务 | 职责 | 实际问题 |
|:---|:---|:---|
| data-service | 数据采集 | ✅ 职责清晰 |
| trading-service | 指标计算 | ⚠️ 包含调度逻辑、数据读取 |
| signal-service | 信号检测 | ⚠️ 包含冷却/历史持久化 |
| tui-service | 终端展示 | ⚠️ 可启动其他服务（职责越界） |

**建议**：服务编排由外部工具管理（如 systemd、Docker Compose）

---

### 14. 指标注册机制原始

```python
# trading-service/src/indicators/base.py
INDICATORS = {}

def register_indicator(cls):
    INDICATORS[cls.__name__] = cls
    return cls
```

**问题**：
- 新增指标需要手动导入
- 无自动发现机制
- 无元数据校验

**建议**：使用插件机制自动发现

---

### 15. 错误处理不统一

```python
# 有的服务用 logger.error
logger.error("错误: %s", error, exc_info=True)

# 有的用 print
print(f"❌ 测试失败: {e}")

# 有的用 raise
raise ValueError(f"Invalid config: {val}")
```

---

### 16. 子模块管理混乱

```
repository/
├── codex/
├── iflow-cli/
├── longbridge-terminal/
├── nofx/
├── OpenAlice/
├── openclaw/
└── tradecat-upstream/
```

**问题**：`.gitmodules` 存在但结构不清，多仓库耦合严重

---

## P3 - 长期改进

### 17. 规则引擎硬编码

```python
# signal-service/src/rules/__init__.py
ALL_RULES: list[SignalRule] = (
    CORE_RULES + MOMENTUM_RULES + TREND_RULES + ...
)
```

**建议**：规则配置化（YAML/JSON），支持热加载

---

### 18. 可观测性碎片化

```python
# trading-service 有完整实现
from ..observability import get_logger, metrics, trace, alert

# signal-service 只有 logging
import logging
logger = logging.getLogger(__name__)

# data-service 混用 print 和 logging
```

---

### 19. 缺乏接口抽象层

138 个类但缺乏统一接口，如 `Engine` / `EventEngine` / `FullAsyncEngine` 三者没有共同基类或协议。

---

### 20. 配置验证缺失

```python
@dataclass
class Config:
    db_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    # 无 URL 格式校验
    # 无连接测试
    # 无默认值兜底
```

**建议**：使用 `pydantic` 进行配置验证

---

## 改进路线

### 短期（1-2 周）
- [ ] 统一配置加载（使用 python-dotenv）
- [ ] 修复 CI（添加测试执行、mypy）
- [ ] 补充 data-service 基础测试
- [ ] 消除裸 `except:`

### 中期（1-2 月）
- [ ] 消除 sys.path hack，正确打包 libs/
- [ ] 收敛数据库架构（SQLite → TimescaleDB）
- [ ] 规则配置化（YAML）
- [ ] 统一日志规范

### 长期（季度）
- [ ] 服务边界重构
- [ ] 统一可观测性
- [ ] 引擎收敛
- [ ] 完整集成测试套件

---

## 核心问题根源

1. **缺乏架构治理** - 无统一的代码规范、设计模式
2. **快速迭代债务** - 多版本引擎并存，未及时清理
3. **测试驱动不足** - data-service 零测试，CI 形同虚设
4. **模块边界模糊** - 服务职责重叠，依赖隐式路径






P0（本周必须）

  - 统一配置加载：抽一个共享配置模块（建议 libs/common/config_loader.py），各服务
    只保留“字段定义”，不再各自 load_dotenv。
  - 去掉 sys.path hack：把 libs/common 做成可安装包（editable），服务通过标准
    import 引用。
  - 收敛全局变量：把关键 runtime 状态收进 AppContext/RuntimeState，通过参数传递，
    逐步移除 global。
  - 阻塞调用分层治理：异步路径禁用 time.sleep（改 asyncio.sleep），同步调度保留但
    集中到 scheduler 层。
  - 修正文档口径：docs/ARCHITECTURE_ISSUES.md 里“数量统计”全部重刷，标注“已验证/部
    分成立/过时”。

  P1（下周）

  - CI 提升到“全仓覆盖”：检查范围扩到 services-preview/、libs/、scripts/，不再只抽
    样 50 个文件。
  - 建立 data-service 最小测试集：至少补 5~10 个 smoke/核心逻辑测试（配置、采集、
    解析、落盘）。
  - 日志规范落地：禁止 print（仅 CLI 入口可例外），统一 logger；加 lint 规则兜底。
  - 错误处理统一：禁止 except Exception: pass，改为记录日志 + 明确降级策略。

  P2（两周内）

  - 引擎收敛：明确一套主引擎，其他引擎标记实验态（feature flag），减少并行维护成
    本。
  - DB/端口单一真源：所有脚本从 .env 读取端口，去掉 5433/5434 混用默认。
  - 服务边界修正：TUI 自动拉起 data/signal 改为可选能力，默认只读展示模式。
  - 指标注册升级：从手工 import 迁移到自动发现/注册机制（保持兼容期）。

  验收标准（建议）
n ew 
  - sys.path 动态注入降到 0（或仅 1 处过渡入口）。
  - global 数量下降 70%+。
  - CI 覆盖到核心目录，失败能拦截 preview/libs/shell 脚本问题。
  - except Exception: pass 清零（或仅允许白名单并附注释）。
  - 文档与代码状态一致，不再出现过时计数。

  如果你同意，我下一步就按这个顺序先做 P0 第一批（配置统一 + 文档修正），做完给你
  一个可提交的 commit。