# binance-api-audit - AGENTS

本文档面向 AI 编码/自动化 Agent，描述该技能目录的结构、职责与边界。

---

## 1. 目录结构树

```
binance-api-audit/
├── .gitignore                # 忽略 Python 缓存产物
├── AGENTS.md                 # 本文档
├── SKILL.md                  # 技能说明与流程
├── USAGE.md                  # 完整使用文档
├── scripts/                  # 辅助脚本
│   ├── scan_fastapi_routes.py # FastAPI 路由扫描
│   ├── inspect_sqlite.py      # SQLite 结构/统计检查（默认只读）
│   ├── query_unified_events.py # unified.db 币种命中二次检查（只读）
│   ├── check_tradecat_metrics_quality.py # Tradecat 指标空值/缺口检查（只读）
│   ├── repair_timescale_klines.py # Timescale 字段修复（需 --apply 才写入）
│   ├── refresh_timescale_cagg.py # Timescale 连续聚合刷新（需 --apply 才执行）
│   └── validate-skill.sh      # Skill 结构校验脚本
└── references/               # 参考资料
    ├── index.md              # 参考索引
    ├── endpoints.md          # 内部 API 端点速查
    ├── prompts/              # 分析提示词（分文件管理）
    │   ├── index.md          # 提示词索引
    │   ├── wyckoff.md        # 威科夫大师原文
    │   ├── market-global.md  # 市场全局解析原文
    │   └── nofx_prompt.md    # NoFX 提示词原文
    ├── workflow.md           # 数据获取流程与决策树
    ├── paths.md              # 关键路径速查（使用 $PROJECT_ROOT）
    ├── queries.md            # 常用查询模板
    ├── test-matrix.md        # 15 场景测试矩阵
    ├── quality-checklist.md  # 交付质量闸门
    └── skill-seekers.md       # Skill_Seekers 方法摘要
```

---

## 2. 文件职责与依赖边界

- `SKILL.md`: 定义内部 API 数据获取流程与示例。
- `scripts/scan_fastapi_routes.py`: 静态扫描 FastAPI 路由；不执行导入。
- `scripts/inspect_sqlite.py`: 只读查询 SQLite 元信息与统计。
- `scripts/query_unified_events.py`: 只读按币种/类别扫描 unified.db。
- `scripts/check_tradecat_metrics_quality.py`: 只读检查成交额/主动买额空值与时间缺口。
- `scripts/repair_timescale_klines.py`: 修复 Timescale K 线缺失字段（默认只读，显式 --apply 才写入）。
- `scripts/refresh_timescale_cagg.py`: 刷新 Timescale 连续聚合（默认只读，显式 --apply 才执行）。
- `references/*.md`: 路径与查询模板，供人工或脚本复用。

依赖边界：
- 只读脚本仅使用 Python 标准库。
- `repair_timescale_klines.py` 依赖 data-service 环境（ccxt/psycopg）。
- 允许读取代码与 SQLite；**仅在显式 --apply 时写入 Timescale**。

---

## 3. 关键设计原则

- 默认只读，显式允许才可读写。
- 解析失败必须可观测（stderr 或结构化输出）。
- 路径示例使用 $PROJECT_ROOT，避免环境漂移。

---

## 4. 变更日志

- 2026-01-27: 初始文档，补充目录结构与职责边界。
- 2026-01-27: 新增 .gitignore，忽略 Python 缓存文件。
- 2026-01-27: 新增 USAGE.md 完整使用文档。
- 2026-01-28: 新增 references 索引与质量闸门，补充校验脚本。
- 2026-01-28: 更名为 binance-api-audit，并更新路径引用。
- 2026-01-29: 新增 unified.db 币种命中二次检查脚本。
- 2026-01-29: 扩展币种命中脚本（主流币合并、来源过滤、关键词策略）。
- 2026-01-29: 更新为 30 场景测试矩阵与执行记录。
- 2026-01-29: 端口检查自动化，取消交互式询问。
- 2026-01-29: 新增 Tradecat 指标数据质量检查脚本与文档。
- 2026-01-29: 新增 Timescale 字段修复脚本（默认只读，显式写入）。
- 2026-01-29: 新增 Timescale 连续聚合刷新脚本（默认只读，显式执行）。
