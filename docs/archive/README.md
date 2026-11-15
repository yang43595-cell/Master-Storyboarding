# docs/archive：历史归档索引

本目录是历史归档，不是现行正文，现行规则以 PROJECT_RULES.md 为准；本目录内容不得作为生产依据。这里收纳原 docs/reviews/（8 篇）与仓库根 recovery/（7 个文件）移出的材料：12 篇 Markdown 已压缩为摘要版，3 个 JSON 为机器溯源数据原样保存。

## 逐文件索引

### reviews/（原 docs/reviews/，每篇均为摘要版）

- [2026-11-04-generation-boundaries.md](reviews/2026-11-04-generation-boundaries.md) — 生成分段检查的根因与六项独立复审修复（含删前驱绕过核验）记录。
- [2026-11-04-individual-skills-and-layer3-review.md](reviews/2026-11-04-individual-skills-and-layer3-review.md) — 逐个 Skill 审查与四项第三层知识候选的筛出及后续接入记录。
- [2026-11-04-project-chain-audit.md](reviews/2026-11-04-project-chain-audit.md) — 项目状态/产物/恢复/导出补丁链路七类根因的审查与修复记录。
- [2026-11-04-skill-sop-audit.md](reviews/2026-11-04-skill-sop-audit.md) — 9 个 Skill 的 SOP 审查、共用完成判定、四项程序修复与本机补验记录。
- [2026-11-04-standalone-repository.md](reviews/2026-11-04-standalone-repository.md) — 独立仓库补齐 52 份选定内容并验收通过的当时证据，现行入口已由开发文档承接。
- [2026-11-05-agent-acceptance-integration.md](reviews/2026-11-05-agent-acceptance-integration.md) — 本地自主验收（agent_acceptance）接线与消费依据落地的记录。
- [2026-11-05-creative-director-prompt-optimization.md](reviews/2026-11-05-creative-director-prompt-optimization.md) — 编导 Skill 六项提示词优化的方案与落实自查，修改已落编导本体。
- [2026-11-05-video-prompt-skill-correction.md](reviews/2026-11-05-video-prompt-skill-correction.md) — 视频提示词 Skill 十项修正的方案与落实自查，修改已落视频 Skill 本体与检查代码。

### recovery/（原仓库根 recovery/）

- [README.md](recovery/README.md) — 2026-10-13 历史恢复基线的总说明摘要，整段保留原文「版本边界」口径与证明范围声明。
- [recovery-delivery-audit-20261018.md](recovery/recovery-delivery-audit-20261018.md) — 恢复交付链有界合成审计摘要：product_refs 增加 --require-file 与交付边界确认。
- [structured-dependency-transfer-checks-20261018.md](recovery/structured-dependency-transfer-checks-20261018.md) — 结构化依赖发现与成品包预算检查（--package，默认 <19,000,000 字节）的实现记录摘要。
- [structured-dependency-end-to-end-20261018.md](recovery/structured-dependency-end-to-end-20261018.md) — 依赖检查端到端验证摘要：越界相对引用被拒、离线审核包通过预算检查。
- source-manifest.json — 机器溯源数据，非阅读材料，一字节未改：325 个源文件的逐文件 SHA-256 与纳入/排除记录。
- candidate-changes.json — 机器溯源数据，非阅读材料，一字节未改：两条候选分支（编导 9 文件/导演 5 文件）的前后哈希与字符数。
- source-provenance.json — 机器溯源数据，非阅读材料，一字节未改：源归档 SHA-256、固定来源引用与钉住文件的 blob 核对结果。

## 恢复包的证明范围（P5 边界声明）

recovery/ 是 2026-10-13 的历史恢复基线留痕，不是生产版本。它能证明的是所含文件可按哈希恢复；不能证明找回了当时的生产 HEAD、未提交修改、画布/网站状态或外部知识库。任何人不得以本目录内容宣称"生产状态已恢复"。

## 原文恢复方法

本目录的 Markdown 均为摘要版，但被摘要的原文件都是 git 跟踪文件，原文完整保留在 git 历史中，没有另存第二份副本。取回某篇原文：先用 `git log --follow -- docs/archive/reviews/<文件名>`（recovery 摘要版对应 `docs/archive/recovery/<文件名>`）找到把文件移入本目录的那次提交，或按移动前路径 `docs/reviews/<文件名>`、仓库根 `recovery/<文件名>` 用 `git log --follow` 查历史；取移动提交的父版本（`git show <移动提交>^:docs/reviews/<文件名>`，recovery 同理换路径）即为原文。3 个 JSON 不在本目录做摘要，无需恢复。

## 反向登记：knowledge/runtime 正文对本归档的引用（R2）

knowledge/runtime 两份方法正文以移动前路径引用本归档内两篇 reviews 记录作为回退依据：

- 应用知识/编导/编导方法.md 引用 docs/reviews/2026-11-05-creative-director-prompt-optimization.md，现对应本目录 reviews/2026-11-05-creative-director-prompt-optimization.md。
- 应用知识/视频提示词/素材职责与引用方法.md 引用 docs/reviews/2026-11-05-video-prompt-skill-correction.md，现对应本目录 reviews/2026-11-05-video-prompt-skill-correction.md。

这两份方法正文是 knowledge/manifest.json 逐字节哈希登记的绑定文件，本轮未改动它们，原文中的移动前路径字样保持原样；这两处引用的改指后续按知识维护流程处理，不在本轮搬运中顺手修改。
