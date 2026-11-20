# 过度防御审计（供独立复核）

- 审计对象：`Master-Storyboarding` 仓库现行规则、Skill、校验脚本与测试
- 基线：`main` @ `12fa5ae662cefc688d42dfe374de61c29d595e34`（2026-11-20）
- 审计时间：2026-11-21
- 工作树：无跟踪改动；未跟踪 `.DS_Store`、本报告及其复核副本
- 性质：**只读审计报告**，不是规则正文，不构成生产、付费、发布或规则变更授权

> 给复核 agent：本文用于找出“可以减、减了不伤出片”的流程重量。
> 请只读复核，不修改仓库文件、不提交、不运行任何生成/上传/发布操作。
> 逐项判断“成立 / 部分成立 / 不成立”，给出你自己的证据，不要以本文的结论替代你的核对。
> 行号以基线 commit 为准；如工作树已变化，用 `git show 12fa5ae:<path>` 对照。

---

## 一、范围与方法

### 读了什么

- 总规则与细则：[PROJECT_RULES.md](../../PROJECT_RULES.md)、[execution.md](../rules/execution.md)、[knowledge-access.md](../../skills/_shared/knowledge-access.md)、[artifact-adoption.md](artifact-adoption.md)、[delivery-snapshot-checks.md](delivery-snapshot-checks.md)、[review-workbench-operations.md](review-workbench-operations.md)、[token-budget.md](token-budget.md)、[project-layout.md](../rules/project-layout.md)、[decisions.md](../decisions.md)、[production-improvements.md](../production-improvements.md)
- 8 个 Skill 的 `SKILL.md` 与 `review-profile.json`
- 相关脚本入口：`stage_context_cli.py`、`agent_acceptance.py`
- 全仓 Markdown 的重复段落扫描、行数/测试数/体积统计

### 没做什么（局限）

- 未运行完整测试套件（3135 项只做了收集）；未实测一部真实片的 token 与耗时
- 未修改任何规则、代码或数据；未验证本文提出的改法能通过现有回归
- 未审计业务项目（桌面上的具体片子）的产物目录
- “5 处状态”“4 份引用”是读规则后的映射结论，不是程序里的字段计数；复核时请按你看到的字段重新数

### 判定标准

一条流程算“过度防御”，需同时满足两点：

1. **它防的风险已经有另一层在防**（同一事实多处登记／多次校验），或**它防的东西程序根本验证不了**（只能靠执行者自律）；
2. **删掉或降级后，不影响出片质量、不碰安全红线**。

“安全红线、真实参考、人审、付费授权、不覆盖已确认产物”不在此列，见第六节。

---

## 二、结论摘要

项目方向写的是“个人轻量、只加帮出片的能力、有疑问选更轻”（[PROJECT_RULES.md:26](../../PROJECT_RULES.md)、[PROJECT_BLUEPRINT.md:52](../PROJECT_BLUEPRINT.md)），但现行实现的重量主要来自两处：

1. **同一事实在多处登记**：版本、确认、引用、授权各有 2–7 份表示，每过一道门就重查一次。
2. **把“防 agent 不老实”写成了流程**：大量“不证明／不替代／不冒称”的元声明，加上无法由程序验证的纪律条款。

| ID | 发现 | 类型 | 严重度 | 建议 | 需用户决定 |
| --- | --- | --- | --- | --- | --- |
| F01 | “当前已确认版本”存 5 处 + revision 与 6 类 hash | 结构 | 高 | 保留；双索引列长期项（见九） | 否（实施需设计） |
| F02 | 同一引用集存 4 份 | 结构 | 高 | 保留（见九） | 否 |
| F03 | 同一授权登记 2 处 | 结构 | 中 | 单处保存，另一处引用 | 否 |
| F04 | 每个产物写逐项结构化自查报告 | 步骤 | 高 | 保留逐项；局部返修复用（见九） | 是 |
| F05 | 阅读＝prepare/record/ensure 三次调用，且禁止合并 | 步骤 | 高 | 登记并入 ensure | 否 |
| F06 | 图片小批次全套隔离准备 | 步骤 | 中 | 仅主对话直做批次免独立目录；保留目标冻结与回执（见九） | 是（边界） |
| F07 | 采用与画布预检重复跑同一套交付校验 | 步骤 | 中 | 撤回（见九） | 否 |
| F08 | 每次压缩恢复输出五项核对 | 步骤 | 中 | 五项保留，输出压到 3 行（见九） | 是 |
| F09 | 每轮正常制作必须记 token/耗时并回填 | 步骤 | 中 | 改为按需 | 是 |
| F10 | 场景状态两套并行记录 | 结构 | 中 | 保留（见九） | 否 |
| F11 | `review-pack`／旧稿归档被写成常规步骤 | 文本 | 低 | 改文档 | 否 |
| F12 | 133 处元声明、重复段、规则放错层 | 文本 | 中 | 合并到共用一处 | 否 |
| F13 | 3135 项测试、6.4 万行脚本、单阶段必读约 1100 行 | 体量 | 中 | 按“是否挡住出片”砍 | 是 |
| F14 | 114 MB 本地忽略目录、未忽略的 `.DS_Store` | 清理 | 低 | 删无用源码快照；`visual-library`/`vault` 是资产库与备份目标，不删 | 是（哪些快照无用） |

“高”＝每部片多次付出或造成返工；“中”＝每阶段／每次恢复付出；“低”＝一次性清理。

> **2026-11-21 补记：** 第九节已按 git 历史、`decisions.md`、归档复盘和代码路径核对来源。核对后 **F07 撤回**，**F01／F02／F04／F10 改判为保留**；表中 F01／F02／F04／F07／F10 行已按第九节更新，其余条目按第九节调整处置（删除／降级／清理）。独立复核（9.5）进一步确认 **F02／F07／F10 不成立**，**F09／F11 成立**，其余为部分成立；F14 的 vault 判断已按复核更正。

---

## 三、发现明细

### F01 “当前已确认版本”存 5 处，并带 revision 与 6 类 hash

- **位置**：[delivery-snapshot-checks.md:128-136](delivery-snapshot-checks.md)、[artifact-adoption.md:56-71](artifact-adoption.md)、[agent_acceptance.py:290-328](../../scripts/core/agent_acceptance.py#L290-L328)
- **现状**：同一条“当前版本已确认/已采用”的事实分别写在：
  1. `project_state` 的 current 产物；
  2. artifact manifest 的 current 条目；
  3. `user_confirmations` / 采用回执；
  4. 结构化自查报告；
  5. 网站或对话审核回执。
  
  同一份产物还带 `revision`、`sha256`、`input_sha256`、`frozen_sha256`、`evidence_sha256`、`review_profile_sha256`、`authorization_sha256` 等多个 hash。
- **为什么算过度防御**：任何一处写入失败或语义漂移，就要靠“另一处重查”兜底；规则本身也承认检查通过的局限（[delivery-snapshot-checks.md:90](delivery-snapshot-checks.md)）。多份记录不能互相证明真实性，只增加同步面。
- **建议**：明确唯一权威（manifest 条目 + revision/hash），其余登记只做缓存/索引；消费时重算而不是重读第二份。
- **收益 / 风险**：每阶段少一轮对账；改动涉及采用事务与失效传播，需专门设计。
- **复核问题**：这 5 处是否真有独立写入路径？哪一处删掉后，现有校验会失去什么具体能力（不是“少一层保险”）？

### F02 同一引用集存 4 份

- **位置**：[delivery-snapshot-checks.md:25-34](delivery-snapshot-checks.md)、[delivery-snapshot-checks.md:52-57](delivery-snapshot-checks.md)
- **现状**：同一组素材同时以 `ordered_media`、`reference_checks`、manifest 的 `upload_bindings`、回读的 `native_mentions` 四种形式保存。
- **为什么算过度防御**：四份描述同一集合，任一版本变化都要同步四处；规则还专门写“同图可多次引用不去重”“一份素材可承担多个职责”来解释差异，说明结构本身在制造歧义。
- **建议**：保留 `ordered_media` + `reference_checks` 为主，`upload_bindings` 与 `native_mentions` 作为“平台事实”单独存但不再参与逐项覆盖校验，只在提交/回读时比对。
- **收益 / 风险**：少一处易漂移的映射；需回归“原生实体”“反向一致性”两类既有检查。
- **复核问题**：`reference_checks` 与 `upload_bindings` 是否存在信息差（一个是计划、一个是实测）？若保留两份，能否只让实测一份参与门禁？

### F03 同一授权登记两处

- **位置**：[project-layout.md:32](../rules/project-layout.md)、[project-layout.md:46](../rules/project-layout.md)
- **现状**：既有自主继续授权时，协调方在 `kickoff` 记 `decision_ref`，同时用 `authorize-workflow` “登记相同的真实用户依据及阶段范围”。
- **为什么算过度防御**：同一份用户原话写两处，任一处更新都会产生“授权版本不一致”；规则又要求下游核对“准确授权”，等于自己制造核对对象。
- **建议**：用户依据只存 workflow 授权一处；kickoff 只记与本轮创作有关的事实和范围，需要时引用授权 ID。
- **收益 / 风险**：少一份重复来源；需检查 `kickoff` 消费者是否直接读 `decision_ref` 判断授权。
- **复核问题**：`kickoff` 的 `decision_ref` 是否被生产代码当作授权依据读取？若是，能否改为读授权 ID？

### F04 每个产物写逐项结构化自查报告

- **位置**：[artifact-adoption.md:50-54](artifact-adoption.md)、[agent_acceptance.py:313-328](../../scripts/core/agent_acceptance.py#L313-L328)
- **现状**：`accept-artifact` 要求报告恰好覆盖 profile 所有 `production_artifact_checks`，每项有 `observation` 和 `locator`，核心项不得写 `not_applicable`，还要绑定 `review_profile_sha256`、`acceptance_binding()` 与各层 hash。
- **为什么算过度防御**：这些检查在同一 Skill 的 `SKILL.md` SOP 和 `review-profile.json` 文本里已经各写了一遍；报告里再写一遍，程序只能验证“字符串非空”，无法验证观察是否真实。实际产出是一份给程序看的自证文档，不是给用户看的结论。
- **建议**：文本阶段（编导、导演、视频提示词）改为“2–3 个核心项 + 一段自由自查”，保留绑定与 hash；图片/分镜图保留逐项（视觉证据确实多）。同一上游、仅局部改动的返修复用上一份报告，只补改动项（现有 `storyboard-director` 已有按“未变导演范围”复用的先例）。
- **收益 / 风险**：每个产物少一份结构化文档；会失去“逐项书面覆盖”的形式化约束，需要用户接受。
- **复核问题**：逐项覆盖实际拦下过哪一类真实问题？如果只用核心项 + 自由文本，哪一类问题会漏？请给出具体反例，而不是原则性担心。

### F05 阅读登记三步，且禁止合并

- **位置**：[knowledge-access.md:39-45](../../skills/_shared/knowledge-access.md)、[knowledge-access.md:79-104](../../skills/_shared/knowledge-access.md)；代码门禁 [stage_context_cli.py:2608-2662](../../scripts/stage_context_cli.py#L2608-L2662)
- **现状**：`prepare` → 实读 → `record-method-reading` → `ensure-prepared`；规则明确“首次阅读须等全文返回并阅读后，再单独调用登记；不能在同一 shell 批次把输出正文与登记串联”。`ensure-prepared` 在 manifest 有必读方法时，没有登记记录会让 `ok=false`。
- **为什么算过度防御**：程序验的是“存在一条身份匹配的登记”，不是“读过”。把登记拆成单独一次调用，只增加一次往返，不增加校验能力；“不能同一 shell 批次”防的是假读，但假读同样可以分两次调用完成。
- **建议**：`ensure-prepared` 增加 `--mark-read --method ...`（读完后再调用，仍要求显式声明），一次调用完成“登记 + 产前门禁”。
- **收益 / 风险**：每阶段少 1 次调用；门禁语义不变。
- **复核问题**：现有门禁是否依赖“登记”与“准备通过”之间的时间顺序？合并后有无代码路径把“登记”当成“已读证明”？

### F06 图片小批次全套隔离准备

- **位置**：[execution.md:16-23](../rules/execution.md)、[knowledge-access.md:69-104](../../skills/_shared/knowledge-access.md)
- **现状**：每批 ≤8 张，独立 `context-id`，独立 prepare/record/ensure，批次回执 + `handoff --receive-batch`；主对话直接做的界面批次也要回执。
- **为什么算过度防御**：隔离是为“多个并行图片执行者”设计的；主对话直接做、或同阶段负责人连续做 ≤2 张时，不存在上下文污染和并发写冲突，却仍要付全套准备成本。规则自己说“8 张不是最低数量”，说明数量不是主要风险。
- **建议**：≤2 张、或由主对话直接执行的批次，沿用阶段准备 + 一张回执，不建独立 `context-id`；保留 8 张上限、独占路径和“不并发写 manifest”。
- **收益 / 风险**：小批次少 3–4 次调用；需确认回执接收方是否依赖批次目录路径。
- **复核问题**：`--receive-batch` 和 `context_id` 是否被下游用来定位候选？如果是，能否只保留回执路径、不建独立准备目录？

### F07 采用与画布预检重复跑同一套交付校验

- **位置**：[artifact-adoption.md:73-86](artifact-adoption.md)、[delivery-snapshot-checks.md:38-49](delivery-snapshot-checks.md)
- **现状**：`accept-artifact --preflight-json` 在采用事务内再调 `validate_submission(..., formal_delivery=True, preflight=True)`；同一份单段 frozen JSON 通常已由 `canvas_readback --preflight` 跑过。
- **为什么算过度防御**：同一份字节、同一套规则，在相邻两步各跑一次全量校验；字节没变时结果必然相同。
- **建议**：预检结果带冻结 hash 与完整性凭证，采用时校验凭证并复用；字节或来源变化才重跑。已有“预检结果交给 `finalize-new`”的先例（[decisions.md:42](../decisions.md)）。
- **收益 / 风险**：每段少一次全量交付检查；需防止“旧预检配新稿”，用 hash 绑定即可。
- **复核问题**：采用时重跑是否还核了预检之后才产生的状态（例如采用事务内的 manifest 变化）？如有，请指出具体检查项。

### F08 每次压缩恢复输出五项核对

- **位置**：[knowledge-access.md:116-129](../../skills/_shared/knowledge-access.md)、[PROJECT_RULES.md:22](../../PROJECT_RULES.md)
- **现状**：压缩/恢复后首次继续前，逐项核 REC-STATE、REC-OPTION、REC-PARAM、REC-ADOPT、REC-FLOW 五项，并按 REC-RESULT 汇报结论。
- **为什么算过度防御**：恢复时真正要回答的是“当前到哪、下一步做什么、哪里不能动”。六项表格把“可选分支、平台参数、采用方式”都拉进来，多数轮次并不涉及；容易变成机械填表。
- **建议**：固定两项（当前阶段/版本/授权 + 下一步与停止点），其余按本轮实际涉及的阶段补问。
- **收益 / 风险**：每次恢复少一段固定输出；可能漏核平台参数——但参数在真正操作平台前仍会被专门核对。
- **复核问题**：过去是否有真实事故是“只核了状态、没核可选分支/参数”导致的？请给具体例子。

### F09 每轮正常制作必须记 token/耗时并回填

- **位置**：[PROJECT_RULES.md:7](../../PROJECT_RULES.md)、[production-improvements.md:44-48](../production-improvements.md)、[token-budget.md](token-budget.md)
- **现状**：开工记 token 和耗时，交付后按台账回填；另有四个“正常生产测量窗口”要顺带采集。
- **为什么算过度防御**：这是给“优化项目本身”收集数据，不是出片必需；个人工具每天开工都要先读台账、收尾都要回填，成本固定在每一部片上。
- **建议**：默认不计量；用户要求或怀疑异常消耗时才开，沿用现有 `usage-check`。
- **收益 / 风险**：每部片省一次台账读取 + 一次回填；失去连续用量数据（可接受，需要时再采）。
- **复核问题**：台账数据目前有没有真实消费者（除本文件外）？没有的话，保留它的理由是什么？

### F10 场景状态两套并行记录

- **位置**：[delivery-snapshot-checks.md:120-126](delivery-snapshot-checks.md)、[delivery-snapshot-checks.md:79-90](delivery-snapshot-checks.md)
- **现状**：`quality_evidence.checks.persistent_objects` 记录常驻物/场景判断，同时又有 `scene_state_assessment: changed | unchanged` 和 `source_shot_ids`。
- **为什么算过度防御**：两者都在回答“这段前后场景态是否一致”，字段不同、判定同源；规则也承认“程序不能从名称自动识别环境”，最终仍靠人读。
- **建议**：保留 `persistent_objects` 为唯一判断，`scene_state_assessment` 由它派生或作为可选摘要；旧稿只读兼容。
- **收益 / 风险**：少一个容易“填了但没核”的字段；需迁移已提交稿。
- **复核问题**：是否存在只有 `scene_state_assessment` 能表达、`persistent_objects` 表达不了的情况？

### F11 `review-pack`／旧稿归档被写成常规步骤

- **位置**：[PROJECT_BLUEPRINT.md:27](../PROJECT_BLUEPRINT.md)、[creative-director/SKILL.md:32](../../skills/creative-director/SKILL.md)、[storyboard-planner/SKILL.md:52](../../skills/storyboard-planner/SKILL.md)、[storyboard-director/SKILL.md:33](../../skills/storyboard-director/SKILL.md)、[draft-lifecycle.md:12-17](../../skills/_shared/draft-lifecycle.md)
- **现状**：蓝图第 27 行把 `review-pack` 与 `prepare`/`ensure-prepared`/`handoff`/`validate` 并列为“每步之间”的命令；两个创作 SKILL（导演、分镜）收尾写“执行旧稿归档收尾”；编导本来就是“按需归档”，而 `draft-lifecycle.md` 已说“没有可归档项不创建空目录或空报告”。README 的 `review-pack` 小节已写“仅在用户明确要求独立审核时”，可作为正确写法参照。
- **为什么算过度防御**：规则正文已经限定“独立审核只在用户明确要求时启动”，但上手文档仍按常规写，容易让执行者多做一步；归档同理，无旧稿时是空动作。
- **建议**：文档改成“仅在明确要求/确有旧稿时”；SKILL 收尾统一为条件句。
- **收益 / 风险**：减少误执行；无行为风险。
- **复核问题**：是否有场景必须默认准备审核包/默认归档（例如跨电脑交接）？

### F12 文本层：元声明、重复段、规则放错层

- **位置**：全仓（统计见附录 A3）；重复段示例 [project-layout.md:42](../rules/project-layout.md) 与 [project-layout.md:44](../rules/project-layout.md)；规则放错层 [PROJECT_RULES.md:70-81](../../PROJECT_RULES.md)
- **现状**：
  - 活跃规则文档中“不证明”64 处、“不替代”42 处、“不冒”27 处（md 口径，未含本文件；复现见附录 A3）；
  - `project-layout.md` 有一段逐字重复；
  - 4 个 `SKILL.md` 共用同一段“视觉生产衔接细则”，资产与分镜 SKILL 共用同一段批次说明；
  - 三份 foundations 文档重复 5 个段落；
  - 1×4 拼版、人物命名等专业方法写在总规则里，所有阶段都要读。
- **为什么算过度防御**：元声明用于防止过度声称，但不产生动作；每段都加一句，读的人（模型）每轮都要多读。专业方法放总规则，扩大必读面并制造与 Skill 正文的双份维护。
- **建议**：元声明收敛到 `skill-layers.md` 一处总则，具体段落只留动作；重复段抽到 `_shared` 一处；总规则只留跨阶段约束，方法下沉到对应 Skill。
- **收益 / 风险**：单阶段少读数百行；纯文本改动，需回归链接与锚点。
- **复核问题**：这些重复句是否有“每处都必须出现，否则读者会漏”的实际案例？如果有，请指出哪一句不能删。

### F13 体量：3135 项测试、6.4 万行脚本、单阶段必读约 1100 行

- **位置**：数据与命令见附录 A1–A3
- **现状**：pytest 收集 3135 项；`scripts/` 下 Python 64,427 行；`stage_context_cli.py` 15 个子命令；导演阶段必读合计约 1,105 行，资产阶段约 1,029 行。
- **为什么算过度防御**：项目自己写明“最小充分测试、不扩成大型套件”（[decisions.md:160-166](../decisions.md)、[PROJECT_BLUEPRINT.md:52](../PROJECT_BLUEPRINT.md)），当前数量已属大型；测试中包含旧版本兼容、投影/文案与 fixture 类用例（具体比例需复核者按类别分类）。
- **建议**：按“这条测试失败时，会不会挡住一次真实出片”分类；保留门禁类（采用、交付、红帧、批次回执、授权边界），删除/合并纯文案、历史格式、重复 fixture。必读改为“核心方法全读 + 场景相关按需”。
- **收益 / 风险**：维护面和阅读量下降；可能漏掉旧项目兼容问题，需保留只读兼容的最小用例。
- **复核问题**：3135 项里哪一类是“删了以后真实出片会坏”的？请按类别给比例，不要只给原则。

### F14 本地忽略目录与未忽略文件

- **位置**：`.master-storyboarding/`（`du` 见附录 A4）、`.gitignore:22,51-52`；`.DS_Store` 未被忽略
- **现状**：本地忽略目录 114 MB，其中 `vault/` 97 MB、`visual-library/` 12 MB、`visual-library-deps/` 3.4 MB，另有 `dead-code-cleanup-before-*`、`dead-code-tail-before-*`、`retired-asset-ui-*` 全量源码快照；`.DS_Store` 未进 `.gitignore`。
- **为什么算过度防御**：全量源码快照与 [D-03“历史交给 git”（decisions.md:118-124）](../decisions.md) 冲突；`vault/` 与 `visual-library/` **不是**普通缓存，不能一律删（见下条修正）。
- **建议**：删除已确认无用的源码快照；`.DS_Store` 加入 `.gitignore`。**`visual-library/` 与同级 `vault/` 保留**：`visual_asset_library.py:194` 把 `root.parent/'vault'` 作为默认资产备份目录，`reconcile_vault`（[:207](../../scripts/visual_asset_library.py#L207)、[:220](../../scripts/visual_asset_library.py#L220)）会在启动时读取它；`DEFAULT_LIBRARY` 就是 `.master-storyboarding/visual-library`。
- **收益 / 风险**：回收磁盘与辨认成本；删 `vault/` 会丢资产备份，必须先由用户确认。
- **复核问题**：哪些 `dead-code-*`/`retired-*` 快照确认无引用？`vault/` 中是否仍有用户资产备份？

---

## 四、建议的省略清单（按执行顺序）

> 本节已按第九节与 9.5 复核更新；第 4、5、7、8 行为复核后的最终表述。

| 顺序 | 动作 | 对应发现 | 行为变化 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | 删无用快照、`.DS_Store` 进 `.gitignore`、修重复段、`review-pack`/归档文案改条件句 | F11 F12 F14 | 无 | 无 |
| 2 | `record-method-reading` 并入 `ensure-prepared` | F05 | 每阶段少 1 次调用 | 低 |
| 3 | 图片 ≤2 张/主对话直做免独立准备包 | F06 | 小批次少 3–4 次调用 | 低 |
| 4 | 文本阶段保留逐项自查；局部返修复用上一份报告 | F04 | 少一份重复报告 | 低 |
| 5 | 撤回：预检是采用后的必要复核 | F07 | 无 | — |
| 6 | 恢复核对压到两项；计量改按需 | F08 F09 | 每次恢复/每部片少固定动作 | 低 |
| 7 | 授权原话单处保存、另一处引用 ID；场景状态保留两字段 | F03 F10 | 少重复抄写 | 低 |
| 8 | 状态与引用保留分层；仅记长期收敛项 | F01 F02 | 本轮无变化 | — |
| 9 | 按门禁砍测试、按需减必读 | F13 | 维护与阅读量下降 | 中（需分类） |

前 3 组可先做且不改行为；第 4 组起需要用户明确接受。

---

## 五、与既有记录的关系

- 项目已记录的方向：[D-01 个人轻量（decisions.md:102-108）](../decisions.md)、[D-03 历史交给 git（decisions.md:118-124）](../decisions.md)、[D-08 最小测试（decisions.md:160-166）](../decisions.md)。
- [production-improvements.md:31-42](../production-improvements.md) 已有 8 条待优化，均不与本文条目重复；其中“准备包装入局部任务未用依赖”与 F05 相邻，“整画布快照”与 F01 相邻，可在实施时合并。
- 本文发现若实施，按项目规矩应在 `decisions.md` 追加一条“为什么减、减掉什么、验证依据”，不改写旧条目。

---

## 六、明确保留（不随本次削减）

以下不属于“过度防御”，删了会直接伤出片或安全，复核时请不要建议删除：

1. 不覆盖已确认产物、返修另存版本；
2. 广告产品与明确要求还原的实物必须用真实参考；
3. 编导稿、资产图走人审；
4. 付费、上传、发布、生成的明确授权；
5. 不补造确认、阅读、看图、试听事实；
6. 每批最多 8 张、同批返修沿用原执行者、独占路径；
7. 跨阶段职责边界（编导不改镜头、导演不定分段、禁止下游暗改）；
8. `.master-storyboarding/visual-library` 与同级 `vault`：资产库默认目录与备份目标（`visual_asset_library.py:194` 的 fallback），不能当普通缓存删除。

---

## 七、需要用户决定的问题

1. **F04**：是否允许“同一上游、仅局部改动”的返修复用上一份自查报告（仍保留逐项覆盖）？不采用“降为核心项 + 自由文本”，因为它是自主采用替代逐稿人审的凭证。
2. **F06**：主对话直做的图片批次免独立准备目录时，是否保留 `image_targets` 冻结与批次回执？
3. **F08**：五项检查保留、输出压到 3 行是否够？
4. **F09**：是否取消“每轮计量”，改为“用户要求才测”？
5. **F13**：测试是否允许按“门禁相关性”分类删减？是否需要保留旧项目兼容的最小集？
6. **F14**：哪些 `dead-code-*`/`retired-*` 源码快照确认无用？`vault` 中的资产备份是否仍需保留？

---

## 八、复核清单（给复核 agent）

> 以下按第九节修正后的问题复核；初稿中 F01／F02／F04／F07／F10 的建议作废。

逐项回答，格式：`Fxx：成立 / 部分成立 / 不成立 — 证据（file:line 或命令输出）— 你会怎么改`。

1. F01：这 5 处是否分别是不同权威（人工确认／自主验收／当前选择／产物身份／网站决定）？删任一处会混淆哪种权威？
2. F02：计划（`reference_checks`）、实测（`upload_bindings`）、回读（`native_mentions`）三份分别能发现什么其他两份发现不了的问题？
3. F03：`kickoff.decision_ref` 是否被当作授权依据读取？能否只保留一份用户原话、另一处引用 ID？
4. F04：逐项覆盖是否是自主采用替代逐稿人审的必要条件？允许局部返用的边界在哪？
5. F05：`ensure-prepared` 是否依赖登记与通过的时间顺序？合并会丢什么？
6. F06：`context_id`/`--receive-batch` 是否被下游用于定位候选？主对话直做批次能免到哪一步？
7. F07：撤回是否正确？采用前跑 `validate_submission` 是否必然因来源未 current 而失败？
8. F08：五项检查保留、输出压到 3 行，是否会漏掉原来逐项输出能发现的问题？
9. F09：台账数据除“验证优化”外还有消费者吗？
10. F10：`scene_state_assessment`（判定）与 `persistent_objects`（观察）是否互补？合并会丢什么？
11. F11：是否有必须默认准备审核包/归档的场景？
12. F12：133 处元声明里，哪几句是“每处都必须出现”的？重复段能否合并？
13. F13：3135 项测试按类别分，哪些删了会坏真实出片？
14. F14：`visual_asset_library.py:194` 的 vault fallback 是否覆盖当前用途？哪些源码快照确认无引用？

**复核结论请额外回答两个总问题：**

- 本文是否漏掉了更该先减的东西？如果有，请给出位置与理由。
- 本文是否有“看似过度、实际必要”的条目被误判？请指出并说明它防住了什么具体事故。

---

## 九、来源核对与处置（2026-11-21 补）

### 9.1 方法与局限

- 用 `git blame` 定位每条规则的引入行，用 `git log --all -S<字段>` 定位机制，读完整 commit message、[decisions.md](../decisions.md)、[归档复盘](../archive/reviews/)、[制作优化台账](../production-improvements.md)，并对关键论断回读代码确认。
- **局限**：`main` 只有 80 个提交，历史经 `6a0cf3c`（2026-11-19）合并整理；部分行的最早记录是导入快照 `e6d96a5`（2026-11-04），原始事故只留在 decisions 或归档复盘里；有些提交只有标题（如 `9425d0c`、`b5122fc`），无法还原更细的现场。下表把“有明确记录”与“从新增文本推断”分开写。
- 本节结论优先于第二、四节的初稿建议。

### 9.2 逐项核对

| ID | 原始目的与触发（证据） | 现在还需要吗 | 处置 |
| --- | --- | --- | --- |
| F01 | 2026-09-22 复现“重复确认覆盖、重做回填旧图、候选叙事包冒充当前来源”；2026-10-03 复现“历史依赖混入当前版本、上传备份早于最终元数据”（[decisions.md:258-271](../decisions.md)）。5 处中：`user_confirmations`（人工）、自查报告（自主验收，`user_approved:false`）、网站回执是**三种不同权威**，不能合并；`project_state.current_artifacts` 与 manifest current 是同一选择的两份索引，理论上可收敛，但被不同入口读取，属高成本重构。 | 需要 | **保留**；人工/自主/网站三种权威不合并；state 与 manifest 的收敛列为长期项，不现在做 |
| F02 | 2026-11-15 前后 Project 2 红帧预检连续缺口（错误源、槽、字节）：`4bd9d89`、`6bab365`、`103ca22`、`5e87cba`。`reference_checks` 是计划、`upload_bindings` 是实测、`native_mentions` 是回读，三者互相核对才能发现“计划与实际不符”。 | 需要 | **保留**；不合并 |
| F03 | `9425d0c`（2026-11-17）加“同时登记相同用户依据”：kickoff 供生产、`authorize-workflow` 供自主采用；两者都要真实依据，防止“开发授权当业务授权”（[artifact-adoption.md:32](artifact-adoption.md)）。 | 目的需要，抄写不需要 | **降级**：同一用户原话只存一处，另一处引用 ID |
| F04 | 2026-11-05 自主采用替代逐稿人审：报告须覆盖全部检查 ID，未决或阻塞项不能自报通过（[归档复盘](../archive/reviews/2026-11-05-agent-acceptance-integration.md)）。它是“没有人审时”的替代凭证。 | 需要 | **保留**；只允许局部返修复用上一份报告 |
| F05 | 与 [D-10（decisions.md:176-187）](../decisions.md) 同源：程序通过不等于 Agent 已读；单独登记用于防“连跑命令假称已读”。 | 登记需要，单独调用不需要 | **降级**：合并进 `ensure-prepared`，仍要求读后显式声明 |
| F06 | `8543243`（2026-11-05）：图片批次交给干净子 agent，最多 8 张，隔离准备上下文、按准确文件交接、保留人审边界。 | 委派批次需要；主对话直做不需要 | **降级**：仅主对话直做批次免独立准备目录，保留 `image_targets` 冻结与回执 |
| F07 | 初稿判断“采用与预检重复跑”。核对代码：`validate_submission` 要求来源已在 `current_artifacts` 且 adopted（[canvas_readback.py:614-621](../../scripts/canvas_readback.py#L614-L621)）；采用事务把候选变为 current 之后才可能通过（[stage_context_cli.py:345-355](../../scripts/stage_context_cli.py#L345-L355)）。首次采用前跑必失败；续作时来源已 current，重跑只是幂等复核。台账 E03 也已把“采用回执接续单段预检”作为设计（[production-improvements.md:23](../production-improvements.md)）。 | **不是重复** | **撤回**；不删 |
| F08 | `11f3666`（2026-11-09）新增“压缩后的主动恢复核对”：核状态、可选分支、平台参数、采用方式、阶段依赖。提交只有标题，具体事故未见存档；从新增文本看是防“用旧摘要继续”。 | 检查需要，逐项长文不需要 | **降级**：五项检查保留，输出压到 3 行 |
| F09 | `12fa5ae`（2026-11-20）把台账与计量写进开工；用途是验证优化（[production-improvements.md:44-48](../production-improvements.md)），不是出片必需。 | 出片不需要 | **删除“每轮必做”**；`usage-check` 工具保留按需 |
| F10 | `db553ac`（2026-11-18）“复用准确场景控制决定、减少红帧重试”；`scene_state_assessment` 是 changed/unchanged 判定，`persistent_objects` 是观察记录（[delivery-snapshot-checks.md:120-126](delivery-snapshot-checks.md)）。 | 需要 | **保留**；不合并 |
| F11 | 蓝图旧句把 `review-pack` 与每步命令并列（[PROJECT_BLUEPRINT.md:27](../PROJECT_BLUEPRINT.md)）；两个 SKILL（导演、分镜）收尾写“执行旧稿归档”；编导本来就是“按需归档”；README 已写成“仅用户明确要求”（[README.md:57-63](README.md)）。 | 不需要 | **删除误导句** |
| F12 | 重复段：`2ddba97` 与 `db553ac` 各引入一份，合并后并存（两行 blame 分别为 42、44）；133 处声明是历次“不要冒称已读/已看/已生成”的累积。 | 重复段不需要；声明部分需要 | **只删除重复段**；声明未批量收敛，具体禁令保留 |
| F13 | 每轮审计都加负例回归（[audit-20261017](audit-20261017.md) 从 910 项增到 1086 项等），[D-08（decisions.md:160-166）](../decisions.md) 要求最小充分。 | 门禁测试需要；退役路径/纯文案不需要 | **降级**：按“删了会不会坏真实出片”分类，先删退役/文案，不设数字目标 |
| F14 | `2ddba97`/`802ad3e`（2026-11-18）删除 Blender 预演与旧资产 UI 前后的手工快照；git 已有历史（[D-03（decisions.md:118-124）](../decisions.md)）。**复核更正**：`visual_asset_library.py:194` 把 `root.parent/'vault'` 作为默认资产备份目录，`reconcile_vault`（[:207](../../scripts/visual_asset_library.py#L207)、[:220](../../scripts/visual_asset_library.py#L220)）启动时读取；`DEFAULT_LIBRARY` 是 `.master-storyboarding/visual-library`。因此同级 `vault` 是资产库备份，不是审计副本。 | 源码快照不需要；vault/visual-library 需要 | **只删无用源码快照**；`visual-library` 与 `vault` 保留，除非用户确认备份可弃 |

### 9.3 修正后的净结论

| 处置 | 条目 | 说明 |
| --- | --- | --- |
| 撤回 | F07 | 采用后重跑是状态变化后的必要检查，不是重复 |
| 保留 | F01、F02、F04、F10 | 分层证据／不同权威；删了会丢“计划 vs 实际”“人工 vs 自主”“观察 vs 判定”的能力 |
| 降级 | F03、F05、F06、F08、F12 | 保留目的，减少重复登记/调用/输出/文本 |
| 删除常规要求或误导文案 | F09、F11 | 计量改按需；文档改条件句 |
| 清理 | F14 | 只删无用源码快照；`visual-library`/`vault` 保留 |
| 分类处理 | F13 | 按门禁相关性分类，不按数量一刀切 |

**修正后的节省**：F05 每阶段 1 次（约 4 次/片）、F06 小批次 3–4 次（视批次）、F09 2 次/片；F08 只是输出变短；F03/F11/F12 不省调用。合计约 **5–8 次调用/片**，而不是初稿的 10–15 次。F01/F02/F04/F10 的结构性重量保留，因为它们不是重复。

### 9.4 给复核者的两个关键问题

- 若认为 F01／F02／F10 仍可合并：请给出合并后仍能发现“计划 vs 实际”“人工 vs 自主”“观察 vs 判定”的具体测试或反例。
- 若认为 F07 应复用预检：请指出“采用前来源已 current”的可行路径，或说明复用哪个凭证、由谁签发。

### 9.5 独立复核结果（2026-11-21）

复核环境：另一台 Windows 工作树（`C:/Users/Administrator/Documents/Master-Storyboarding`），只读；**无 pytest**；工作树中没有 `.master-storyboarding`，因此无法复现本机的 114 MB 现场与 3135 项测试。

| 发现 | 复核结论 | 与本报告的关系 |
| --- | --- | --- |
| F01 | 部分成立 | 三种权威不合并；`project_state` 与 manifest 双索引列为长期收敛项 | 一致 |
| F02 | **不成立** | 计划／实测／回读职责互补 | 与本报告“保留”一致，不再列为削减项 |
| F03 | 部分成立 | 可减少原话抄写，**不能删 `kickoff` 字段** | 一致；补“保留字段” |
| F04 | 部分成立 | 逐项自查是自主采用凭证；普通写稿不必每次额外建报告；局部复用限**严格范围** | 一致；补“严格范围” |
| F05 | 部分成立 | 可减调用，但保留实读后显式登记、身份与版本校验 | 一致 |
| F06 | 部分成立 | 主对话直做可研究省略独立目录／接收；`image_targets` 冻结与回执不能省 | 一致 |
| F07 | **不成立** | 采用前预检因状态条件失败 | 与本报告“撤回”一致 |
| F08 | 部分成立 | 五类核对必要，输出可压缩，**不能只写“已恢复”** | 一致；补“必须有具体结论” |
| F09 | 成立为可削减项 | 规则确实要求每轮记录，但主要服务优化；改按需需改规则 | 一致 |
| F10 | **不成立** | 观察依据 vs changed/unchanged 判定 | 与本报告“保留”一致 |
| F11 | 成立 | 条件化表述 | 一致 |
| F12 | 部分成立 | 重复段可合并；具体禁令仍保留 | 一致 |
| F13 | 部分成立 | 行数一致；复核环境无 pytest，未复跑；按门禁分类 | 一致；补环境说明 |
| F14 | 部分成立（含更正） | 源码快照可清理；`visual-library`/`vault` 不能删；`visual_asset_library.py:194` 有 vault fallback 并 `reconcile_vault` | **修正本报告原句** |

**采纳的更正**

1. **F14 原句错误**：本报告曾写“脚本未引用 `.master-storyboarding/vault`”。实际 `DEFAULT_LIBRARY = ROOT/.master-storyboarding/visual-library`；`Library.__init__` 的 fallback 是 `root.parent/'vault'`（[visual_asset_library.py:194](../../scripts/visual_asset_library.py#L194)），并在初始化时调用 `reconcile_vault()`（[:207](../../scripts/visual_asset_library.py#L207)）。同级 `vault` 是资产库**备份目标**，不是审计副本。F14、第六节、9.3 已改。
2. **环境差异**：114 MB、3135 项是本机（macOS）基线；复核机没有 `.master-storyboarding` 与 pytest，相关数值不能在其环境复现。A1/A4 已标注“本机”。
3. **措辞补充**：F03 保留 `kickoff.decision_ref` 字段；F04 局部复用限“同一上游、仅局部改动”；F06 保留目标冻结与结果回执；F08 压缩输出仍须有具体结论。

**复核后的实施优先级**

1. 纯文案：F11 条件化、F12 重复段（零风险）。
2. 清理：F14 只删无用源码快照 + `.DS_Store`；`vault`/`visual-library` 不动。
3. 规则/调用：F09 计量改按需；F05 合并登记调用（保留读后声明与身份校验）。
4. 小批次：F06 仅主对话直做批次免独立目录，保留 `image_targets` 与回执。
5. 输出：F08 五项保留、压到 3 行且逐项有结论。
6. 测试：F13 按门禁分类；复核环境无 pytest，分类清单先在本机跑。

### 9.6 落地状态（2026-11-21）

- 已落地：F03、F05、F06、F08、F09、F11、F12、F14。F06 复核补强：请求带 `image_targets` 时仍校验并冻结；完全省略目标的普通准备路径不由程序区分；批次不得改变已冻结目标。
- 保留：F01、F02、F04、F10；F07 撤回。
- 待办：F13 测试按门禁分类（未删测试）。
- 验证：`test_doc_reference_integrity` 4 passed；`test_stage_context_cli.py` 123 passed（含 `--mark-read` 与无批次目录目标冻结用例）；全量 3077 passed、63 failed（环境缺少 Node/ffmpeg、`/private/var` 别名及 3 个私有画布 fixture）、9 skipped、222 subtests passed。
- 改动在本分支 `cleanup/overdefense-20261121`。

---

## 附录：复现命令与数据（基线 12fa5ae）

### A1 测试数量

以下为本机（macOS，`.venv` Python 3.12）基线；复核环境无 pytest，未复跑。

```sh
.venv/bin/python -m pytest -q -c config/pytest.ini --collect-only scripts/tests tests | tail -1
# 3135 tests collected in 0.40s
```

### A2 代码与文档行数

以下数值为审计基线（未含本文件与其复核副本）；含本文件时 `docs` 为 2912 行，连同 429 行副本共 3341 行。

```sh
find scripts -name '*.py' | xargs wc -l | tail -1                     # 64427
find docs -name '*.md' ! -name 'audit-20261121-overdefense*' | xargs wc -l | tail -1   # 2442
find skills -name '*.md' | xargs wc -l | tail -1                      # 4920
```

导演阶段必读（合计 1105 行）：

```sh
wc -l PROJECT_RULES.md skills/_shared/skill-layers.md skills/storyboard-planner/SKILL.md \
  skills/storyboard-planner/modules/shot-planning.md \
  skills/storyboard-planner/references/foundations/*.md \
  skills/storyboard-planner/references/visual-conception.md \
  skills/storyboard-planner/references/segment-sequence-method.md \
  "skills/storyboard-planner/references/专业影视分镜审查门槛.md" \
  "skills/storyboard-planner/references/专业影视分镜执行规范.md" \
  skills/storyboard-planner/templates/*.md | tail -1
```

### A3 重复段落与元声明

```sh
grep -n "用户已明确选定的造型" docs/rules/project-layout.md   # 42、44 两处逐字重复
for w in 不证明 不替代 不冒; do
  printf '%s ' "$w"
  grep -rho --include="*.md" --exclude="audit-20261121-overdefense.md" -- "$w" \
    docs skills AGENTS.md PROJECT_RULES.md | wc -l
done
# 不证明 64 / 不替代 42 / 不冒 27（未含本文件）
```

跨文件重复段落扫描（排除归档/依赖目录）：

```sh
python3 - <<'PY'
import re, pathlib, collections
files=[p for p in pathlib.Path('.').rglob('*.md')
       if not any(x in p.parts for x in ('.git','.master-storyboarding','node_modules','.pytest_cache','.venv','archive'))]
g=collections.defaultdict(set)
for p in files:
    for b in re.split(r'\n\s*\n', p.read_text(encoding='utf-8', errors='ignore')):
        s=' '.join(b.split())
        if len(s)>=50 and not s.startswith(('|','#','```','>')):
            g[s].add(str(p))
for s,ps in g.items():
    if len(ps)>1: print(len(ps), s[:80], sorted(ps))
PY
```

### A4 本地体积

以下为本机实测；复核环境工作树中没有 `.master-storyboarding`，无法复现该体积。`vault`/`visual-library` 是资产库与备份目标，不能按“缓存”删。

```sh
du -sh .master-storyboarding .master-storyboarding/*
# 114M; vault 97M; visual-library 12M; visual-library-deps 3.4M;
# dead-code-cleanup-before-3u2pi1_2 536K; dead-code-tail-before-7bkepnos 512K;
# retired-asset-ui-c5cgpv5b 108K; archive 24K
git check-ignore -v .DS_Store    # 无输出（未被忽略）
```

### A5 关键行号索引

| 主题 | 位置 |
| --- | --- |
| 轻量方向 | [PROJECT_RULES.md:26](../../PROJECT_RULES.md) |
| 每轮计量 | [PROJECT_RULES.md:7](../../PROJECT_RULES.md) |
| 恢复核对 | [PROJECT_RULES.md:22](../../PROJECT_RULES.md)、[knowledge-access.md:116-129](../../skills/_shared/knowledge-access.md) |
| 阅读登记三步 | [knowledge-access.md:39-45](../../skills/_shared/knowledge-access.md) |
| 图片批次 | [execution.md:16-23](../rules/execution.md) |
| 自查报告 | [artifact-adoption.md:50-54](artifact-adoption.md)、[agent_acceptance.py:313-328](../../scripts/core/agent_acceptance.py#L313-L328) |
| 预检与采用 | [artifact-adoption.md:73-86](artifact-adoption.md) |
| 交付字段 | [delivery-snapshot-checks.md:25-34](delivery-snapshot-checks.md)、[delivery-snapshot-checks.md:79-90](delivery-snapshot-checks.md)、[delivery-snapshot-checks.md:120-136](delivery-snapshot-checks.md) |
| 授权双登记 | [project-layout.md:32](../rules/project-layout.md)、[project-layout.md:46](../rules/project-layout.md) |
| review-pack 常规化 | [PROJECT_BLUEPRINT.md:27](../PROJECT_BLUEPRINT.md)；“仅用户明确要求”的正确写法参照 [README.md:57-63](README.md) |
| 测试/轻量边界 | [decisions.md:160-166](../decisions.md)、[PROJECT_BLUEPRINT.md:52](../PROJECT_BLUEPRINT.md) |
