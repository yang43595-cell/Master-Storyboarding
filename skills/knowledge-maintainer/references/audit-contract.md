# 知识审计合同

- 唯一入口：`scripts/audit-knowledge-entrypoints.py`。
- 输入：项目内全部 `knowledge-profile.json`、`references/workflow/knowledge-policy.json`、`references/workflow/knowledge-entrypoints.json` 与真实 Vault。
- 检查：profile 合法性、topic 可达性、reference trigger、confirmed 冲突、重复 canonical key、来源存在、repo 载体存在、Skill 直接引用、关键执行词和旧中央路由残留。
- 运行方式：手动按需执行；不参与 `prepare`，不扫描每次生产任务。
- 结果：零错误返回 0；任一确定性错误返回非零，并给出可定位的文件或规则标识。
