# skills - AGENTS

本文档面向 AI 编码/自动化 Agent，描述 /home/lenovo/.projects/skills 的结构、职责与边界。

---

## 1. 目录结构树

```
skills/
└── binance-api-audit/   # 内部 API 数据获取 Skill（脚本 + 参考文档）
```

---

## 2. 模块职责与边界

- `skills/<skill-name>/`: 每个 Skill 独立目录，入口为 `SKILL.md`。
- `skills/binance-api-audit/`: 内部 API 数据获取与端点核对。

边界约束：
- Skill 脚本默认只读，禁止写库或修改业务代码。
- 需要网络/外部依赖时必须在文档中显式说明。

---

## 3. 关键设计原则

- 严格遵循 `skills/<skill-name>` 标准结构。
- 使用 `validate-skill.sh --strict` 进行结构校验。
- 路径示例使用 $PROJECT_ROOT 保持可移植性。

---

## 4. 变更日志

- 2026-01-28: 初始化 skills 目录说明。
- 2026-01-28: api-chain-audit 更名为 binance-api-audit。
