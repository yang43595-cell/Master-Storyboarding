# 独立 A+B 组合验证（未启用）

准确 A 基线：`1b4959656f20e7b9796ccacbb3620a3908552b27`。
准确 B 文件来源：`6cf4e382a46101fc2a72e4e5139e33165c67752c`。

本组合只将 B 的八份候选文件按原字节放在 A 上，另加 combination-lock.json、本说明和 validate_combination.py。不整分支合并旧候选历史，不修改 main、生产入口、业务 schema、编导内容或历史测试证据。

原 candidate.json 和 validate_candidate.py 保留原字节及原基线哈希；原验证器在 A 上因 PROJECT_RULES.md 变化明确拒绝，这是正确结果。新组合验证器固定上述 A/B commit，分别锁定从远端 Git tree 获取的六份 A 运行文件和八份 B 候选文件 blob；没有删除或清空任何原 B 哈希。当前组合检查报告原 B 的预期拒绝，然后独立核对新的精确快照锁、12 项已有字段映射和实际准备加载器的候选隔离。

验证结果：

- `python candidates/director-segmented-20261022/validate_combination.py`：通过，明确 production_enabled:false、semantic_continuity_verified:false、artistic_benefit_verified:false、model_calls:0。
- 临时副本内分别修改 A 的 PROJECT_RULES.md、B 的 segment_tasks.py：组合锁均正确拒绝。原文件未改。
- 用已有 Python 3.12/pytest 环境运行既有导演合同、确认范围回归及新增候选任务测试：41 passed。其中八项是合成结构夹具测试，不是真实导演创作；原套件的可选 jsonschema 分支仍因缺依赖提前返回，未声称完整业务 schema 验证。
- 任务入口的 init/task/record/status/assemble 已用合成数据验证调用、落盘、carry/ID/revision/hash/段序/缺失及依赖失效、局部重查和候选 JSON 合成；carry 非空或字面相等不证明实际连续，选择记录不证明艺术收益。

文档合同层：A 已将新增网页/导出要求限导演，原全局 JSON 规则保持原文。结构工具层：B 的手动任务入口和精确组合锁已完成。实际创作接入层：提供可调用命令及人工接续模板，尚未运行真实作者、分段生成、生产 stage 或线上网页交付；不称完成自动分段生成。没有新模型调用器、凭据、调度平台或默认接线。

jsonschema/referencing 仍缺；未安装依赖、未改系统 Python。未跑全回归，缺 20260518-100605.png 的旧阻塞保留。候选导出缺 schema 依赖时只可作为 production_blocked 的隔离资料，不能正式交接。未运行创作测试、盲评或媒体生成。网页实际入口及部署版本仍待核实，原用户改动保留。
