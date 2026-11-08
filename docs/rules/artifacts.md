# 业务 JSON、采用与阅读导出

本文件是项目规则的职责细则；适用范围及默认流程以 [项目总规则](../../PROJECT_RULES.md) 为准。只在本轮职责或明确选用的分支内读取。

## JSON 交付与按需导出

业务候选、修订与交接只维护现有 JSON 合同；故事原文、对白、资产需求和导演决定完整保存在对应字段。沿用开工决定，集中澄清新增且确需用户决定的事实缺口，再完善 JSON 并自查。统一入口的编导稿及资产图走网站人审，导演、按需分镜和视频提示词走下游自主采用。未知事实不猜填，不以结构完整冒充用户确认。整稿与局部确认边界不变。

已登记开工的项目通过[审核站决定命令](../development/review-to-canvas-entry.md#审核站-api-对接要求)首次采用或返修编导稿。采用事务核对网站当前有效的整稿通过及本地送审路径、修订和准确字节。兼容流程取得准确整稿确认后，仍可对编导、导演或平台无关视频稿运行 `python scripts/stage_context_cli.py adopt-package --project <项目> --artifact-id <新ID> --logical-id <逻辑产物> --path <项目内JSON路径> --revision <正文修订> --approval-ref <准确确认依据> [--depends-on <上游逻辑产物> ...]`。

采用入口在登记 current 前重跑适用的原文/来源、上游确认与哈希、导演引用及生成段续跑检查，并写入整稿确认和当前产物选择。`next_stage` 仍由项目流转决定。已有 current/stale 正式版本的返修沿 `scripts.core.revision_control` 的既有变更事务另存版本。渠道冻结快照走自身检查入口。历史导入仍可登记并读取，不能通过普通 `promote_candidate`、`save_manifest` 或网页审查结果晋升为正式 current。历史 Markdown 与旧事件包须先整理成当前 JSON 合同，再经正式入口重新确认。外部直接改文件或 manifest 不在仓库 API 门禁范围内，交接消费会重新校验已确认产物。

不再强制产出同义编导稿、资产表、导演稿或阶段 Markdown，也不把导出文件登记为新业务依赖。用户需要阅读文件时，校验后运行 `python -m scripts.core.story_sources --project <项目> --narrative <叙事包> [--storyboard <导演包>] --export-markdown <新路径>`。导出包含完整字段，只是阅读副本；不反向覆盖 JSON，不覆盖已确认文件。操作说明、专业方法、原始证据和必要审核记录保留。

新包省略 `human_readable_path`。历史包保留此字段时沿原双文件一致性检查只读兼容；不得为绕过历史差异而删除字段，迁移须另建有明确来源的新 JSON 修订并重新审核确认。历史采用与用户确认记录不改写。旧操作说明中的 Markdown 制作/审稿顺序仅供此历史路径参考，新生产按本节执行。



## 确认范围

记录用户实际确认的准确版本、段落范围与依据；一段确认不代表全片，局部依据不能作为整稿采用凭据。未变的已确认部分保留其准确依据；上游或正文变化须重新核对受影响范围，不把 agent 自查写成人工确认。

机器字段以 `scripts/core/confirmation_scope.py` 为唯一权威：`scope` 仅为 `artifact`（整稿）、`segment`（创作段落）、`dialogue`（对白核对）；中文说明放 `scope_description`，实际镜头、对白、段落编号写入对应 ID 字段。`revision_control.record_scoped_confirmation()` 记录局部确认，不把变更案推进到可提交；只有整稿确认才能提交人工采用。用 `python -m scripts.core.confirmation_scope --project <项目>` 检查记录。旧中文 `scope` 依据原始确认整理，不按文字猜测或自动升级范围。

统一入口下游自主验收沿既有流程授权、自查与采用入口执行，不伪造上述人工确认。首次采用与返修仍分别沿本文件及 [统一入口](../../PROJECT_RULES.md#网站审核到画布的统一入口) 的既有合同处理。
