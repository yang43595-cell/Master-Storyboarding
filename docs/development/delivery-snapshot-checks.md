# 正式交付快照核验

此入口只检查已经形成的 JSON 交付内容，使用现有 `canvas_readback.py`、项目 artifact manifest、确认记录与 revision_control，不重新引入 production_plan 或独立账本。不产生图片、视频、上传、网页操作或用户批准。实际内容和来源由执行者核对，机器检查不替代视觉阅读。

## 按任务读取

| 本轮情况 | 入口 |
| --- | --- |
| 正式草稿 | [写前自查和快照](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#正式草稿写前检查)，红帧另按[成批写入和整节点回读](../../skills/_shared/redframe-operations.md#成批写入和整节点回读) |
| <a id="私有候选预览"></a><a id="已核原生首尾输入的严格候选路径"></a><a id="恢复素材与非硬首尾状态参考"></a>明确授权的私有候选 | [隔离试验的预览快照](../rules/experiments.md#私有候选预览)，原生首尾帧另核[实际首尾输入证据](../rules/experiments.md#已核原生首尾输入的严格候选路径) |
| <a id="平台采集与离线合同的接缝"></a><a id="红帧富文本的局部只读采集"></a><a id="红帧-node--输出-port--实际输入槽下载显式分支"></a>维护平台采集器或明确使用特殊身份模式 | [平台采集与离线合同的接缝](../../skills/_shared/redframe-operations.md#平台采集与离线合同的接缝)，选中 port 模式时直达[实际输入槽下载](../../skills/_shared/redframe-operations.md#红帧-node--输出-port--实际输入槽下载显式分支) |
| <a id="红帧整节点的源连线用途与时间"></a><a id="更新前核已有编辑"></a>红帧节点字段或更新已有节点 | [整节点字段](../../skills/_shared/redframe-operations.md#红帧整节点的源连线用途与时间)或[更新前基线](../../skills/_shared/redframe-operations.md#更新前核已有编辑) |
| <a id="依赖发现与文件包预算"></a>本地依赖或发送文件包 | [正式交付的文件依赖](#正式交付的文件依赖)或[文件包传递](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#跨目录恢复与文件包传递) |

## 同一份 JSON

复用既有 `drafts`、`prompt`、`parameters`、`ordered_media`。业务修订写在顶层 `revision`，来源写 `source_artifacts`（每项准确 artifact_id、revision、sha256）。网页目标用准确 `target_url`。开工或冻结快照登记了平台 `target_project_id` 时，正式回读也须提供刷新后实际读取的同一 ID，纯文本和带素材草稿都适用。历史无 revision 的快照继续按原范围只读比较，返回 submission_checks=not_checked，不得据此宣称完整提交检查或用于新交付采用。

正式交付的冻结正文须逐字匹配已采用来源段正文。平台原生引用按独立实体核对，回读正文取编辑器的实际可提交文字，引用芯片单独采集。修订对白或其他正文应先修订来源稿，不能靠将已改文字同时写入冻结稿和编辑器通过比对。

逐 draft 增补本轮确有需要的声明，仍存在同一 JSON：

- `duration_bounds: [min, max]`：来自本轮已核实的平台能力，单位秒。红帧正式交付须与已采用源稿 `selected_video_model` 的上下限一致。
- `shot_durations: [seconds, ...]`：按提交顺序保存实际承载的时间，合计等于 `parameters.duration`。数组须非空且各项为有限正数。源稿同时声明逐镜安排和生成总时长时，分段检查、正式采用和画布预检共用同一合计校验，时长别名相互冲突时拒绝。源稿尚无生成总时长时仍可核逐镜安排，正式画布继续要求平台参数。
- `required_entities: [entity_id, ...]` 与 `required_reference_roles: {entity_id: [role, ...]}`：实际需要参考覆盖的实体及职责。没有独立参考的剧情实体不虚造参考。
- `reference_checks`：与 ordered_media 一一对应，按实际上传顺序保存每份参考的 artifact_id、revision、sha256、kind（image/video/audio）、roles 职责集合、entity_ids（兼容旧单值 role）；不省掉未在提示词中显式提及的上传素材。role 为 identity/appearance/start_frame/end_frame/action/scale/layout/voice。首尾帧同时保存明确 state_id；action 须为视频，duration 与本地 ffprobe 实际时长核对。此入口要求参考作为具体已确认产物登记，不能以旧快照的 confirmed 字样或文件名替代确认。AST 母图仍先走现有 asset_refs 的具体 output 检查，不伪造通用登记来绕过。
- `continuity: {event_id, previous_node_id?}`：真实连续事件保持相同 event_id；确因时长拆段时，后段准确指向实际前段，并同时提供前段 end_frame 与后段 start_frame。沿每个事件的前驱链核对，不要求两个段落在全片列表中相邻。重复出现的事件必须指向同事件上一片段，拒绝漏前驱、断链、环、分叉或跨事件引用。边界 state_id、entity_ids 必须相同；可共用确认图，也可使用下述准确获准的不同机位参考组。
- `native_entity_type`：有媒体引用时，填写本轮平台实际原生引用实体类型；回读 native_mentions 逐项提供准确 node_id/media_id、相同 entity_type 及 native_noneditable_entity=true。纯文本同名标签或图边不足。合法无媒体文字稿（ordered_media=[]）不编造实体类型，但仍须实际回读 native_mentions=[]；缺少回读或多出引用均失败。仅此空引用状态记 native_reference_checks.status=not_applicable，不能用于有引用的稿件，也不代表获得生成许可。

角色、动作、比例、布局的视觉语义仍须实际看图/视频，与上游镜头核对。文件扩展名和申明职责不是视觉证据；程序不推断图片里是谁、不证明视觉一致性，不证明远端素材字节来源或生成授权。不要以同一 entity_id 混淆角色身份、动作示范者和产品实体。

## 操作与采用

```sh
python scripts/canvas_readback.py --delivery --project <项目> --frozen <本修订JSON> --preflight
python scripts/canvas_readback.py --delivery --project <项目> --frozen <本修订JSON> --readback <实际刷新后读数JSON>
```

`--delivery` 是每次新画布交付的明确调用模式，要求准确项目和非空修订，继续复用当前来源、引用及原生回读校验。它不是后台拦截器：绕过此入口的外部操作不会自动获得检查。历史只读比较可不带该开关，但缺项目/修订的历史结果不能替代正式交付。缺 manifest 或真实确认时如实报告，不自动补审批。

中断且回读尚未完成时，先只读核实已保存部分。源版本、字节、引用或正文改变后，重跑受影响范围的预检。动作语义仍执行视频方法的同次自查。

回读命令只比较读数，不执行网页操作。实际刷新并读取原生编辑器后，在读数中记录 `phase: after_refresh`，并以 `scripts.canvas_readback.digest` 计算冻结对象的规范 JSON SHA-256 作为 `frozen_sha256`。结果包含本修订、实际读数摘要及各项差异。没有实际读取时保留未核。

将这份业务 JSON 用现有 `record_candidate` 登记。`record_review` 仍记录执行者实际审核结论；若候选为网页交付快照（target_url + drafts），approved 还要求 report_ref 指向项目内本修订的完整比较结果。程序核对 revision/hash、全部实体检查、当前参考，以及报告字节摘要。目标已登记项目 ID 时，比较报告须含实际回读的 `observed_target_project_id`。在途旧报告缺该字段时，刷新并生成新报告，用 `record_review` 为同一候选补登，保留已有确认。`confirm_candidate` 与 `commit_change` 重查同一候选和当前证据。普通技术通过仍不等于用户确认，也不授予上传、生成或发布权限。

## 来源映射

现有 artifact manifest 的来源条目用 upload_bindings 保存实际上传/读取证据映射，每项为 target_url、node_id、media_id、revision、sha256。执行者在实际上传并核对原生标识后登记，不按待验证稿倒填，不让用户手工制造证据。校验将 ordered_media 的每个实际输入与其 reference_checks 指向的当前来源映射核对；同图可在同段或不同段多次引用，不去重顺序。此字段属于原产物登记的附属来源事实，不是新的确认账本。缺真实映射时阻塞相关提交。

一份素材可用 roles: [identity, end_frame] 等承担多个真实职责，required_reference_roles 检查集合覆盖，不要求复制上传。

当前已确认的上传来源映射还检查反向一致性：同一 target_url/node_id/media_id 不得指向两份不同字节，不能通过改选矛盾登记中的另一项求通过。相同字节的合法多次复用仍允许。

## 同状态参考组

不同机位边界图在现有整稿 user_confirmations 凭据中使用 reference_groups：每组含 group_id、event_id、state_id、entity_ids、members；members 逐份绑定 artifact_id、revision、sha256、roles 与获准 draft_node_ids。receipt 必须具有真实 approval_ref，并在 artifact_ids 中覆盖两侧成员。两张图本身仍各有有效整图确认；组的本次用途可按现有 confirmation_scope 使用整产物凭据，或合法 segment 凭据且 segment_ids 覆盖本次两侧段号。只检查实际两侧获准用途，不要求因此重审全片；缺段、越界或对白确认不能替代。两侧 reference_checks 用同一 reference_group_id 指向此准确组。程序核对当前确认文件、组状态/实体/事件及两侧用途，不能仅凭组名相同接受，也不能为了过检改 hash。视觉上的同状态仍需实际核对。

使用已采用产物的自主验收报告时，也可按现有消费者读取其中已绑定的 `reference_groups`。两侧图片、准确状态、事件、实体、实际节点和当前获准用途仍按同一组核对。

## 正式交付的文件依赖

正式 validate_submission（包括 --delivery 的 preflight、回读和现有 revision_control 调用）递归检查本交付 JSON 可达的结构化依赖。支持 path/paths/path_or_url/file/files/localPath 及 *_path(s)/*_file(s)、reference(s)/image(s) 的已知文件扩展名、source_image/source_video/source_audio、reference_images/reference_videos、准确 artifact_id/source_artifact_id/artifact_ids/source_artifact_ids，以及 depends_on 的唯一 current logical_id（候选检查仅按本轮 input_artifact_ids 选择，保留 production_blocked/unreviewed）；相对路径始终按项目根解析，与 manifest 一致。每处引用分别核对 manifest 身份。同一文件路径对应多个登记时，该处须有准确 `artifact_id` 和匹配的 SHA-256，引用顺序不改变结果。JSON 循环去重，不全项目扫描，不从自由文字猜文件、不解析 Markdown 链接；空的可选 path 字段表示未选用，不制造声音/补图需求。已声明 required_reference_roles 的职责覆盖仍由原校验负责，未结构化的业务必要性仍须人工核对。

外置输入只在直接读取 CLI 中通过准确 --input-root 允许；正式画布交付依赖要求位于项目内。丢失、空文件、越界、无效 JSON、manifest 缺失/歧义或远端不可核验均不返回成功。文件可读不是确认或版本证据，既有 current/revision/hash/确认检查继续执行，候选生产阻塞不改变。

图片质量证据既有 `source_files` 是观察来源的准确原件：逐份检查可读、JSON 完整性及 SHA256，不把这些原件内的历史制作计划再次展开为本次活动输入。例如已完成图的原计划仍可记着失败的 v001 目标，而当前交付实际选择 v002；这不要求恢复失败图。该处理仅适用于完整图片 QA 结构，不适用于普通 `source_files` 或本次明确选择的输入；同一文件另被活动引用时仍递归检查，当前图片、来源原件或字节有缺失/变化继续失败。原质量及失败记录不改写，不产生确认或生成授权。

manifest 的 `metadata.previous_quality_evidence_records` 保存已被替代的准确观察，不作为当前依赖再次展开或按今天的来源字节追认旧观察。当前 `quality_evidence` 的源字节与获准用途仍由图片准入校验；开启 `verify_hashes` 时依赖发现也核其摘要。同一旧文件若另被当前明确选用，仍执行当前检查。

本地引用发现与最终引用类型检查共用 `scripts/core/media_types.py` 的扩展名集合，含 WEBM、MKV、M4A（大小写不敏感）。被发现只表示进入存在性和来源检查，不证明编码、时长、平台支持或内容已验收。

## 本轮观察证据与输入版本

正式 `validate_submission` / `--delivery` 对每个 draft 消费同稿 `quality_evidence`；不改用户确认/审核权限。`input_sha256` 为 `scripts.core.quality_evidence.fingerprint(draft_binding(draft))`，覆盖 draft 全部字段但排除 quality_evidence 本身。正文、参数、有序引用、职责、镜号、时长和连续边界任一变化都会使旧检查失效。不要只改hash冒充重查。

同稿 evidence 含 status、observer、带时区checked_at，以及 checks 的六项 `action_chain / persistent_objects / state_boundaries / framing / timing / reference_conflicts`，每项保存 result（pass/fail/unknown/not_applicable）、observation、locator。无关项写具体理由及来源，不要求为无关动作虚构过程。正式入口缺项、失败或未核拒绝；只保存私有待审文字稿可不具备这个正式放行证据。

- action_chain：在原稿定位承托、左右手占用、接触、分离/落点、释放和后态；手已占用却无人接触脱头须返回上游，不以“动作自然”替代。
- persistent_objects：同场常驻物完整继承，核身份/类别/尺度/位置/内容物；改一件也回归同桌其他物件，撤参考后必须有真实替代身份依据或合法不出镜处理。
- state_boundaries：相邻全文和真实尾首观察，膜态按五区核，清水前末处湿擦→巾离脸/放下→无大片连续主膜；不以时间省略掩盖必需状态，未生成不能称动态已核。
- framing、timing、reference_conflicts：具体画幅草验；自然语速试读/实际操作估时与实测区分；阶段参考职责不冲突。保留锋利嘴部特写与有意义快切。

所有活动 image 引用（包括私有候选预览）还从现有 manifest 条目读取 [图像观察证据](../../skills/storyboard-director/references/image-evidence-admission.md)，draft 用 source_shot_ids 指明实际镜号。音频、视频和文档不套静态图检查。预览的外置文件仅沿原有明确 roots/input_roots 范围读取，不放宽磁盘根。

检查输出只称 evidence_consistency_only，并明确 visual_quality_verified=false、generation_input_provenance=unknown。完整但虚假的观察文字仍可能通过，不能据此证明已看图或模型执行正确。

## 必保画面与产品动作参考

导演包在具体镜号声明 `required_visuals` 时，来源视频分段校验和画布预检分别在实际 `prompt` 正文检查对应指令独立成行。若声明 `min_seconds`，源稿和实际交付均按该段准确镜号顺序提供逐镜 `shot_durations`，同一镜的一条完整承载须达到最低时长。源视频覆盖的首尾镜号之间漏掉必保镜头也会失败；部分交付只核本次实际镜号，仍以准确全源校验为前提。适用于带这些声明的正式交付和私有候选，历史未声明稿保持兼容。此检查核对作者选定的要求是否传到最终正文与计划，不能发现上游未登记的语义遗漏或验证模型输出。

导演为具体必保画面登记 `action_reference` 时，视频源校验按该镜号、产品资产和动作核对 `materials`（或兼容拟传清单）里的实际动作视频。画布预检再核相应原片是否进入本次活动引用，并核对镜号与文件身份。缺项直接报镜号与动作；局部交付按实际提交镜号核对。动作需求和媒体声明写法见[产品使用交接](../../skills/_shared/product-usage-contract.md#广告中的产品展示与使用)。

## 分段参数

正式分段、自查与交付共用生成段读取入口。历史稿如带 `independent_candidate_ranges`，须按可靠的绝对剪辑时窗或已有全片顺序展开，并核声明范围、重复段和缺段。无法确定完整顺序或范围时报错，保持原稿字节。

现行生成段的实际 `parameters.model`、参考模式（`reference_mode` / `mode`）与生成时长（`duration` / `duration_seconds`）绑定准确源稿；别名可适配，但不得互相冲突。红帧正式写入和回读均从已采用源稿 `selected_video_model` 读取 `min_duration_seconds`、`max_duration_seconds`，并核目标平台、模型、参考模式及原始能力证据的文件哈希。冻结稿必须沿用这一对上下限。源段须声明 `target_edit_duration_seconds` 或准确的 `edit_in_seconds` 与 `edit_out_seconds`，两种同时声明时须一致。红帧独立段目标剪辑低于本轮已核下限时，按[短镜优先并入下一段](../../skills/voiceover-to-video-script/modules/video-method.md#红帧短镜优先并入下一段)在源稿中处理；单独增加生成或逐镜时长不会解除拒绝。高于下限的原剪辑可保留合理的原生时长余量。需改参数时先另出源修订，不在交付时静默替换。

红帧能力证据保留实际模型读数的原生 JSON，其中 `model` 含模型身份、`mode` 和 `durationResolutionMap`。按本段分辨率核准确时长选项，源稿与冻结稿分辨率须一致。上下限与原生选项相符，生成时长须是其中一个选项。

同一已选模型使用多种实际参考模式时，可在既有 `selected_video_model` 增补 `reference_modes` 和逐模式 `reference_mode_evidence`；每种模式都须有准确能力证据且支持所声明的共同上下限。各段仍逐项绑定自己的实际模式，不接受未知模式、跨模型套用或只给模式名而无证据。没有该集合的旧稿仍按原单模式精确匹配。

## 已有前驱与分批范围

分开交付已有稿与新增稿时，`scope.type` 使用 `selected_ranges`，在现有 `scope.independent_ranges` 中按成片顺序列准确段号。仅当两范围之间确有未覆盖的导演镜头和剪辑时码空档时，略过该处的接缝检查。同一范围、相邻范围间真实接续及全片稿仍核镜头与剪辑时码连续。

红帧写前预检沿准确导演稿核每个跨场段边界。后段须有[跨场接续判断](../../skills/voiceover-to-video-script/modules/video-method.md#红帧跨场写前检查)及两侧导演镜头依据；动作继续时还须记录动作前后态并核实际引用。局部写入同样核源稿中相邻的前段。

只交付连续事件的后段时，保留真实 `continuity.previous_node_id` 和 `event_id`，并在同一 `continuity` 内添加 `previous_delivery`，仅含已有前次快照的 `artifact_id`、`revision`、`sha256`。该快照也须列于本次 `source_artifacts`，沿用正式已确认来源或当前私有候选范围，不创建新的批准。没有已有可核证据就报告缺口，不能删前驱、另起事件或补造快照通过。

检查器只读加载所需前驱节点，核原快照字节、同项目/目标/正式或私有模式、准确源修订与段号、实际参数、引用和切点。当前 `drafts` 仍只列本次要交付的节点，不要求重传前段。先前节点的更早前驱元数据保留，但不递归加载旧链；本次已加载节点中的自环、互环仍拒绝。无关旧段不会因过期文件而阻塞本次检查。该结果不证明远端节点当前存在、视觉正确或获准生成，实际回读与原流程仍须执行。

## 剧情场景状态的交付核对

交付消费者回读 `source_artifacts` 指向的准确导演及视频源；其中必须包含视频实际引用的 `source_storyboard`／`source_director` 文件，不能用另一份导演稿或冻结稿自报状态绕过来源。视频已定义 `generation_segments` 时，交付草稿用 `source_segment_id` 绑定原 `segment_id`，草稿的 `source_shot_ids`／`source_shots` 必须逐镜等于该源段映射，不能只报段首或删除变化后的镜头来缩小检查范围。有 `scene_states` 的新提交必须同时携带准确视频源的 `generation_segments` 和草稿 `source_segment_id`，不能删除视频源、降级为仅导演来源来放行；旧包没有这些字段仍可只读，不等于满足新提交准入。

草稿存在 `layout` 引用、来源 `scene_states` 命中，或已有 `scene_state_assessment` 声明时，在原 `quality_evidence.checks.persistent_objects` 的 `observation`／`locator` 中记录具体判断依据，并添加 `scene_state_assessment: changed | unchanged`、`source_shot_ids: [全段镜号]`。`changed` 必须有准确来源 `scene_states` 链；`unchanged` 必须有实际来源依据，不能由省略或删除 `scene_states` 推定。没有判断或判断未覆盖全段时阻止提交。此声明仍只证明所填证据一致，不代替独立阅读剧情、看图或用户终审。 泛用 `state_reference` 也可承担演员动作切点，不能全部视作环境或强制场景判断；这类引用保持原切点合同。场景用途的 `state_reference` 作者必须按本节显式填写场景判断。程序不能从角色／实体 ID 名称自动识别环境，未登记场景状态却假装演员用途的缺漏不在自动识别能力内；须由原文阅读和人工核对揭示，不宣称已被技术检查全面拦截。

一个生成段涉及多个场景状态且没有逐镜或真实起终原生控制证据时，本轮消费者阻止提交；包括前半段沿用前态、后半段切到一个已声明后态的情形，即使只登记了一个变化后态也不能冒充全段同态。可以保留明确起终态的文字候选，待核实平台能力并接入可验证控制，或在不违背导演与用户产品同段限定的前提下修订分段。不会自动切段，也不会把用途文字当成平台能力。

## 新稿不得继承旧回读通过

`native_binding.status=persisted_readback_verified` 是需核验的声明，不是可复制的业务常量。当前稿新增revision、改正文/参数/有序本地或原生引用后，旧声明不能用于本稿；保留历史回执，当前候选明确记unknown/stale/not_bound等未核状态即可，不补造新回读、不自动重写业务稿。

若声明verified，同一native_binding需保存当前`revision`、`input_sha256`（`fingerprint(native_input_binding(当前段))`，排除native_binding/quality_evidence，其他业务字段完整覆盖）、准确`evidence_path/evidence_sha256`、`canvas_url`和当前node身份。真实证据沿用canvas回读结构：`phase=after_refresh`、当前revision/target_url、drafts内准确node_id及prompt/parameters/ordered_media/native_mentions；不得把旧平台原始片段包一层新revision冒称刷新。原始证据格式无法核对时保留未核，不猜转换。消费者核当前内容、修订、hash、原生实体顺序；仍不认证采集行为或真实job输入。

现有canvas formal/private-preview和视频稿`check_video_prompt_focal_intent.py <稿件JSON> --project <准确项目根>`检查此声明；没有verified声明的文字候选可保存，不能说已回读。平台无关稿件采用入口也复核，旧回读不证明生成结果。focal检查器从固定main真实源码恢复，本轮新增这一声明核验，非stub。

兼容旧别名时，当前活动段的native_binding_verified、materials等活动引用里的native_entity_verified若为true，也必须由上述同修订同输入的逐段native_binding证明；native_readback_source旧路径本身不证明当前实体。跨段边界或整包级的旧true字段不具备逐节点正文/参数，不自动转换成verified；回读结论落在实际逐段native_binding，旧边界结论仅保留历史。legacy native_delivery_scope字符串/已核摘要及final_native_receipt_path不能单独当本轮通过；需要留历史时放历史记录，当前状态明确未核。检查仅遍历当前drafts/generation_segments/creative_sections及其活动引用，不消费history、previous_revision_self_check、excluded_materials_this_revision为当前证据。不会修改或删除这些历史对象。当前目标必须来自顶层target_url或target_canvas.url并与回读一致，原生实体类型也须一致；换画布或纯文本实体不得沿用旧通过。

## 草稿、提交与结果不混用

沿既有交付/任务记录保存三个不同事实：current draft 是当前编辑值；submission snapshot 是当次实际提交前冻结的完整正文、有序node/media/源文件hash、参数、带时区提交时间及可取得的真实job/version；output 是真实结果media/hash、检查范围与用户采用状态。不得拿当前草稿倒填历史任务。平台没有不可变job输入或结果关联时，input_provenance保持unknown，能判断输出错误但不能唯一归责模型。草稿比较器一律不从通过报告推出job已核；本次没有自动抓取平台job的能力。未知项不补假ID。

## 历史格式与等价副本

T0 等价副本仍免重复人工审稿，但确认和采用时仍重查当前来源、参考文件字节和映射；等价复制不是来源有效性的豁免。JSON-only 导演包可以直接进入白模读取；保留 human_readable_path 的历史包继续核对原同修订正文。05/06 的 JSON 引用与显式产品标记也进入现有扫描，不因没有 MD 静默漏检。

validate-project.ps1 的通用图片路径检查复用现有 Python product_refs 命令的 --include-images，覆盖 JSON 正文与解码后的结构字段、MD/TXT。路径含空格或 JSON 转义反斜杠时按实际值解析，迁移仍使用明确 MSB_PROJECT_PATH_MAP。历史 Linux 验证覆盖 Python 命令及缺图负例，未运行 Windows 包装器。
