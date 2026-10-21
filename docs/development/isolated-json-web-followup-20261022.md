# A 后续修正：仅导演阶段新增交付约束

本次在 0b71e9e0fb10b9ee8aa99034c0dafdf16380cb0e 之后追加提交，不改历史提交、既有测试记录或生产分支。

从隔离基线 1a57efeb729a95a6df2aac69a04fe5f723d1bb6d 核实：“各阶段业务数据仅维护 JSON，Markdown 按需导出”及“JSON 交付与按需导出”整节原本已经存在。此次恢复这些全局规则原文，仅新增“导演 JSON 与网页审查（本次修正）”一节；脚本检查移除该新节后，PROJECT_RULES.md 与原始基线逐字节一致。

新节仅约束 storyboard-planner：候选 JSON → 自查校验 → 既有审核网页 → 用户确认准确 JSON revision/范围；Markdown 仅用户明确请求时从同版 JSON 导出，不作前置、不反写、不成为业务权威。没有修改编导稿内容或给其他阶段增设网页门禁。历史双文件只读兼容、确认及交接授权、线上来源缺口保留。

补查已有虚拟环境 `/Users/liuyang/.local/share/llm-wiki/venvs/obsidian-vault/bin/python`（3.12.14）：已有 pytest，无 jsonschema。未改系统 Python、未安装依赖。通过该解释器运行 `-m pytest -q -c config/pytest.ini scripts/tests/test_storyboard_planner_contract.py scripts/tests/test_confirmation_scope.py`，33 passed。可选 jsonschema 分支仍未执行，不将 33 项通过扩称完整业务 schema 或全回归验证。

状态分层：文档合同已修正；网页适配/发布代码未改；没有线上或真实导演执行接入结论。旧全回归产品 fixture 缺失阻塞保留，无新创作、盲评、媒体生成或 main 合并。
