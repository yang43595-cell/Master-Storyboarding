# 候选结构验证（2026-10-22）

状态：已实现待集成，默认关闭；基线为 1a57efeb729a95a6df2aac69a04fe5f723d1bb6d。此分支独立于 A，不合并 main，不切换生产入口。

- `python3 candidates/director-segmented-20261022/validate_candidate.py`：通过。核对 6 份关键运行/业务 schema 文件的原始 Git blob 身份，12 个字段映射全部来自现有 schema；实际 `stage_context_cli._local_standard_documents()` 不加载候选。
- 本地与全部已物化基线文件比较：无任何基线文件修改；仅新增本目录文件。Skill、模块、profile、索引及 schema 均保持原字节。
- 临时副本的四项负例均被拒绝：默认启用、指向不存在的新 schema 字段、修改生产基线文件、把候选链接注入实际 Skill 必读收集路径。临时副本之外的基线及用户文件未改。
- `python3 -m unittest scripts.tests.test_storyboard_planner_contract -v`：9 tests，OK；其中可选 jsonschema 分支因本机无该依赖而提前返回，不能称完整 schema 校验。
- 所有本地已读 JSON 可解析，新验证器可被 Python AST 解析。
- 运行确认范围测试模块时缺 pytest，未通过导入；本机也无 Node。未安装新认证或依赖，未声称相关回归已通过。
- 未运行全回归，历史 `20260518-100605.png` 缺失阻塞保持；未伪造 fixture。
- 未调用创作模型、作者对比、盲评、图片或视频生成；不存在艺术收益、真实任务连续性或线上集成结论。

实现形式是可定向采用的 Skill 候选指令及结构隔离验证器，不是自动生成器或打分器。启用和真实任务验证需另行授权。候选最终交付遵循 A 的 JSON/网页合同，集成时须同时核对独立 A 修正；不以本文覆盖基线旧 profile。
