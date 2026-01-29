# 交付质量清单（Quality Gate）

在交付前逐项确认：

1. `SKILL.md` 含 YAML frontmatter，`name` 合法且与目录名一致
2. `description` 具备“做什么 + 何时触发”的可判定描述
3. 含必备章节：When to Use / Not For / Quick Reference / Examples / References / Maintenance
4. Quick Reference 可直接执行，且不超过 20 条
5. Examples ≥ 3，含输入、步骤、验收
6. `references/index.md` 存在且可导航
7. 不确定的内容提供验证路径，禁止编造事实
8. 破坏性操作必须显式标注并加保护说明
9. 通过校验脚本：

```bash
bash scripts/validate-skill.sh /path/to/skills/binance-api-audit --strict
```
