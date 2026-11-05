# 现有交付快照的只读核验

此入口只检查已经形成的 JSON 交付内容，使用现有 `canvas_readback.py`、项目 artifact manifest、确认记录与 revision_control，不重新引入 production_plan 或独立账本。不产生图片、视频、上传、网页操作或用户批准。实际内容和来源由执行者核对，机器检查不替代视觉阅读。

## 同一份 JSON

复用既有 `drafts`、`prompt`、`parameters`、`ordered_media`。业务修订写在顶层 `revision`，来源写 `source_artifacts`（每项准确 artifact_id、revision、sha256）。网页目标仍用准确 `target_url`。历史无 revision 的快照继续按原范围只读比较，返回 submission_checks=not_checked，不得据此宣称完整提交检查或用于新交付采用。

逐 draft 增补本轮确有需要的声明，仍存在同一 JSON：

- `duration_bounds: [min, max]`：来自本轮已核实的平台能力，单位秒；不内置或猜测模型上限。
- `shot_durations: [seconds, ...]`：按提交顺序保存实际承载的时间，合计等于 parameters.duration；不能漏时间、写负数、NaN 或布尔值。
- `required_entities: [entity_id, ...]` 与 `required_reference_roles: {entity_id: [role, ...]}`：实际需要参考覆盖的实体及职责。没有独立参考的剧情实体不虚造参考。
- `reference_checks`：与 ordered_media 一一对应，按实际上传顺序保存每份参考的 artifact_id、revision、sha256、kind（image/video/audio）、roles 职责集合、entity_ids（兼容旧单值 role）；不省掉未在提示词中显式提及的上传素材。role 为 identity/appearance/start_frame/end_frame/action/scale/layout/voice。首尾帧同时保存明确 state_id；action 须为视频，duration 与本地 ffprobe 实际时长核对。此入口要求参考作为具体已确认产物登记，不能以旧快照的 confirmed 字样或文件名替代确认。AST 母图仍先走现有 asset_refs 的具体 output 检查，不伪造通用登记来绕过。
- `continuity: {event_id, previous_node_id?}`：真实连续事件保持相同 event_id；确因时长拆段时，后段准确指向实际前段，并同时提供前段 end_frame 与后段 start_frame。沿每个事件的前驱链核对，不要求两个段落在全片列表中相邻。重复出现的事件必须指向同事件上一片段，拒绝漏前驱、断链、环、分叉或跨事件引用。边界 state_id、entity_ids 必须相同；可共用确认图，也可使用下述准确获准的不同机位参考组。
- `native_entity_type`：有媒体引用时，填写本轮平台实际原生引用实体类型；回读 native_mentions 逐项提供准确 node_id/media_id、相同 entity_type 及 native_noneditable_entity=true。纯文本同名标签或图边不足。合法无媒体文字稿（ordered_media=[]）不编造实体类型，但仍须实际回读 native_mentions=[]；缺少回读或多出引用均失败。仅此空引用状态记 native_reference_checks.status=not_applicable，不能用于有引用的稿件，也不代表获得生成许可。

角色、动作、比例、布局的视觉语义仍须实际看图/视频，与上游镜头核对。文件扩展名和申明职责不是视觉证据；程序不推断图片里是谁、不证明视觉一致性，不证明远端素材字节来源或生成授权。不要以同一 entity_id 混淆角色身份、动作示范者和产品实体。

## 操作与采用

```sh
python scripts/canvas_readback.py --delivery --project <隔离项目> --frozen <本修订JSON> --preflight
python scripts/canvas_readback.py --delivery --project <隔离项目> --frozen <本修订JSON> --readback <实际刷新后读数JSON>
```

`--delivery` 是每次新画布交付的明确调用模式，要求准确项目和非空修订，继续复用当前来源、引用及原生回读校验。它不是后台拦截器：绕过此入口的外部操作不会自动获得检查。历史只读比较可不带该开关，但缺项目/修订的历史结果不能替代正式交付。缺 manifest 或真实确认时如实报告，不自动补审批。

中断时没有完整刷新回读就未完成交付核验，先只读核实已保存部分，不自动重试写入/生成。重复运行这两条命令只读且重新核验当前来源；源版本、字节、引用或正文改变后必须重跑，不能复用先前 PASS。动作语义仍执行视频方法的同次自查，不将技术通过写为人工已看图。

第二条命令只比较读数，不执行网页操作。读取者必须真实刷新并读取原生编辑器；输出含本修订、frozen_sha256、实际读数摘要及各项差异。没有实际读取时不可填造通过。

将这份业务 JSON 用现有 `record_candidate` 登记。`record_review` 仍记录执行者实际审核结论；若候选为网页交付快照（target_url + drafts），approved 还要求 report_ref 指向项目内本修订的完整比较结果。程序核对 revision/hash、全部实体检查、当前参考，以及报告字节摘要。之后 `confirm_candidate` 与 `commit_change` 重查同一候选和当前参考，旧 PASS 不会验证新内容。普通技术通过仍不等于用户确认，也不授予上传、生成或发布权限。

## 来源映射与同状态参考组

现有 artifact manifest 的来源条目用 upload_bindings 保存实际上传/读取证据映射，每项为 target_url、node_id、media_id、revision、sha256。执行者在实际上传并核对原生标识后登记，不按待验证稿倒填，不让用户手工制造证据。校验将 ordered_media 的每个实际输入与其 reference_checks 指向的当前来源映射核对；同图可在同段或不同段多次引用，不去重顺序。此字段属于原产物登记的附属来源事实，不是新的确认账本。缺真实映射时阻塞相关提交。

一份素材可用 roles: [identity, end_frame] 等承担多个真实职责，required_reference_roles 检查集合覆盖，不要求复制上传。

不同机位边界图在现有整稿 user_confirmations 凭据中使用 reference_groups：每组含 group_id、event_id、state_id、entity_ids、members；members 逐份绑定 artifact_id、revision、sha256、roles 与获准 draft_node_ids。receipt 必须具有真实 approval_ref，并在 artifact_ids 中覆盖两侧成员。两张图本身仍各有有效整图确认；组的本次用途可按现有 confirmation_scope 使用整产物凭据，或合法 segment 凭据且 segment_ids 覆盖本次两侧段号。只检查实际两侧获准用途，不要求因此重审全片；缺段、越界或对白确认不能替代。两侧 reference_checks 用同一 reference_group_id 指向此准确组。程序核对当前确认文件、组状态/实体/事件及两侧用途，不能仅凭组名相同接受，也不能为了过检改 hash。视觉上的同状态仍需实际核对。

T0 等价副本仍免重复人工审稿，但确认和采用时仍重查当前来源、参考文件字节和映射；等价复制不是来源有效性的豁免。JSON-only 导演包可以直接进入白模读取；保留 human_readable_path 的历史包继续核对原同修订正文。05/06 的 JSON 引用与显式产品标记也进入现有扫描，不因没有 MD 静默漏检。

当前已确认的上传来源映射还检查反向一致性：同一 target_url/node_id/media_id 不得指向两份不同字节，不能通过改选矛盾登记中的另一项求通过。相同字节的合法多次复用仍允许。

validate-project.ps1 的通用图片路径检查复用现有 Python product_refs 命令的 --include-images，覆盖 JSON 正文与解码后的结构字段、MD/TXT。路径含空格或 JSON 转义反斜杠时按实际值解析，迁移仍使用明确 MSB_PROJECT_PATH_MAP。Linux 已验证 Python 命令及缺图负例；本云环境没有 PowerShell，未声称执行过 Windows 包装器或迁移过 Windows111。

## 依赖发现与文件包预算

正式 validate_submission（包括 --delivery 的 preflight、回读和现有 revision_control 调用）递归检查本交付 JSON 可达的结构化依赖。支持 path/paths/path_or_url/file/files/localPath 及 *_path(s)/*_file(s)、reference(s)/image(s) 的已知文件扩展名、source_image/source_video/source_audio、reference_images/reference_videos、准确 artifact_id/source_artifact_id/artifact_ids/source_artifact_ids，以及 depends_on 的唯一 current logical_id（候选检查仅按本轮 input_artifact_ids 选择，保留 production_blocked/unreviewed）；相对路径始终按项目根解析，与 manifest 一致。选中文件也按准确路径反查 manifest 依赖，重复登记报歧义；JSON 循环去重，不全项目扫描，不从自由文字猜文件、不解析 Markdown 链接；空的可选 path 字段表示未选用，不制造声音/补图需求。已声明 required_reference_roles 的职责覆盖仍由原校验负责，未结构化的业务必要性仍须人工核对。

外置输入只在直接读取 CLI 中通过准确 --input-root 允许；正式画布交付依赖要求位于项目内。丢失、空文件、越界、无效 JSON、manifest 缺失/歧义或远端不可核验均不返回成功。文件可读不是确认或版本证据，既有 current/revision/hash/确认检查继续执行，候选生产阻塞不改变。

文件包交付在原命令追加 --package <实际成品.zip>（多包重复），可显式设置 --max-package-bytes；每包实际字节严格小于默认 19000000，成功输出实际 SHA-256。无包时不要求造包，也不声称测过包大小。生成包后发送前重跑；工具不负责创建/拆包、传输或安全解压未知归档。可单独使用 python -m scripts.core.product_refs --project <项目> --package <成品>。外部上传绕过命令不会被自动拦截。

## 私有候选预览

只有执行者已从真实用户对话核实授权、精确私有目标及本轮内容范围后，才选 `--private-preview`，不与 `--delivery` 同用。程序检查声明一致性，不验证消息 ID 的真实性，不获取授权、不操作浏览器、不改项目状态。既有正式入口默认不变，预览快照不能送 revision_control 冒充正式采用。

```sh
python scripts/canvas_readback.py --private-preview --project <准确候选项目> --frozen <本轮预览快照.json> --preflight
python scripts/canvas_readback.py --private-preview --project <准确候选项目> --frozen <同一快照.json> --readback <实际刷新后读数.json>
```

沿原有完整 drafts/source_artifacts 增补以下字段，不另建事件包。所有示例占位值必须由执行者换成真实值，不能作为可执行授权。参数允许 duration、aspect/aspect_ratio/ratio、resolution、model；不允许加入生成/付费/提交控制。prompt 是待保存的正文，不是给执行器的操作命令。

```json
{
  "target_project_id": "实际画布项目ID",
  "status": "candidate",
  "review_status": "unreviewed",
  "production_blocked": true,
  "operations": ["upload_references", "save_drafts"],
  "preview_authorization": {
    "mode": "private_candidate_preview",
    "project_path": "/准确候选项目绝对路径",
    "target_url": "与顶层相同的实际完整URL",
    "target_project_id": "与顶层相同的实际项目ID",
    "visibility": "private",
    "evidence": [{"message_id": "真实用户消息引用", "quote": "用户原话"}],
    "source_projects": ["/准确候选项目绝对路径", "/实际使用的隔离资产试验项目"],
    "input_roots": ["/已核实且确需读取的外置原始素材目录"],
    "allowed_operations": ["upload_references", "save_drafts"],
    "drafts_sha256": "digest(frozen['drafts'])",
    "sources_sha256": "digest(frozen['source_artifacts'])",
    "cleanup_sha256": "digest([])"
  }
}
```

这里 digest 指本脚本的 canonical JSON SHA-256，不是文件文本 hash。`source_projects` 精确列出本轮引用的候选项目，每个项目仍须保有合法 candidate_test 运行身份。`input_roots` 只为原有依赖发现提供实际外置文件的窄范围根，按需省略；不填磁盘根。`source_artifacts` 不可空，继续绑定本轮实际源修订。所有来源及 reference_checks 增补：

- `project_path`：该登记所在的准确候选项目绝对路径。
- `artifact_id`、`revision`：真实 manifest 登记；状态必须 candidate/unreviewed/production_blocked 且无 user_approved。普通候选仍走现有 resolve_inputs 的准确输入与依赖检查。
- `record_sha256`：manifest 的 path_or_url 指向的实际登记文件 SHA-256；已有 checksum 时也须相同，没 checksum 不补造确认 checksum。
- `path`、`sha256`：实际提交文件的绝对路径和 SHA-256。普通图/源 JSON 为登记文件本身；visual-asset-trial 登记的是 result.json，实际图必须是该收据 image 指向的文件，不能填 result.json 充当图片。
- 试验图还附 `trial_provenance`：按顺序列实际 request.json 与 asset_generation_trial.json 的 `{path, sha256}`。`review_evidence` 按文件名排序列该 attempt/review-overrides/*.json 的真实 `{path, sha256}`；没有时用空数组。核对原始引用/方法字节，后续失败审图不能隐藏、换图或漏报。

每个图片引用仍保留原来的 kind、roles、entity_ids、需要时 state_id。另加真实审图用途和实际原生映射（下例仅示字段，不能照填观察结论）：

```json
{
  "limited_use": {
    "conclusion": "usable_for_private_preview",
    "reviewer": "实际看图的执行者",
    "review_ref": "本轮真实审图记录引用",
    "observation": "实际观察及局限，不写虚假PASS",
    "draft_node_ids": ["实际获准片段节点ID"],
    "entity_ids": ["实际适用实体ID"],
    "roles": ["layout", "scale"],
    "excluded_roles": ["identity", "appearance"],
    "failure_scope_note": "仅保留历史失败说明，不能授权本轮活动引用"
  },
  "upload_binding": {
    "target_url": "准确目标URL",
    "target_project_id": "准确目标项目ID",
    "node_id": "实际媒体节点ID",
    "media_id": "实际原生media ID",
    "revision": "准确登记修订",
    "sha256": "实际媒体文件SHA-256"
  }
}
```

映射沿冻结快照保存，不往原 manifest 伪造 current 或确认绑定。必须来自实际上传/已存在素材的原生读取，不能先编节点 ID；缺真实映射时该项未完成，不能宣称通过。此校验检查保存草稿前的最终引用映射及保存后的回读，不充当尚未上传时的上传许可。引用顺序、职责、实体、连续切点、时长与原生 mentions 仍走同一公共检查。不同图片的连续边界仍要求既有真实确认组；本预览不新造候选“确认组”绕过。

原结果为 failed_candidate 或有后续失败 review 时，保留历史记录，但拒绝本轮新增/更新活动引用；excluded_roles、failure_scope_note 或 limited_use 不再豁免失败。非失败图片仍须在原 manifest 条目保存本用途 quality_evidence，并由入口核对。无合格图可保存不带该图的文字候选并明确缺口，不成为生成许可。程序只验声明一致性，不从文字推断或识别像素；预览通过从不等于用户确认。

只在真实用户明确要求清理时，将 `remove_unreferenced_candidates` 加入 operations 和 allowed_operations，并将准确旧候选加入同一快照 `cleanup`。每项字段为 node_id、target_url、target_project_id、status="candidate"、reference_count=0、reference_scan="whole_canvas"、recoverable=true、phase="after_refresh"、readback_ref（实际全画布引用检查来源）、authorization_ref（必须是 evidence 中用户消息 ID）；另将 cleanup_sha256 绑定 digest(cleanup)。不能删当前草稿/媒体、正式产物、非零引用或不可恢复对象。执行者仍须核实整画布真实零引用，不以此声明代替原生读取。

保存/清理后的实际读数仍须 phase=after_refresh、frozen_sha256、准确 target_url，并增补相同 target_project_id。清理时还需 `cleanup_results: [{"node_id": "准确旧候选ID", "absent": true}]`，按 cleanup 顺序逐项回读。快照及机器结果不能证明远端未发生生成、远端字节来源或画布私密性；执行者必须保持“无生成”的操作边界，完成既有布局/截图验收后才报告真实完成范围。

### 恢复素材与非硬首尾状态参考

真实集成中，旧试验收据可能仍保留旧绝对路径、方法文件也可能在下一次执行时已更新。展示原图不是重新执行旧生成流程：仅在 root 已实际核对准确图像/用途时，可在本项目 manifest 新登记 `type="preview-reference"`，保持 candidate/unreviewed/production_blocked，path_or_url 指向实际恢复/复制文件，checksum 固定真实媒体字节。原试验、业务正文、确认记录和失败结论不改，也不删除原状态标记。

该条目 `preview_origin` 必须包含 purpose="private_preview_only_not_generation_baseline"、source_sha256、history_gaps（真实未重验/缺失/迁移事实列表）、historical_evidence（每项 location 为原证据绝对文件名，sha256 为证据实际字节）。原生成收据若存在，必须连同原 request 与全部后续 review-overrides 保存引用；至少一份原证据结构必须绑定该图片 SHA。旧方法缺失/改字节只能如实报告，不伪恢复或改 request 来求通过。字段 location 明确表示历史出处，不把历史 JSON 内旧文件路径重新当本轮生产依赖。

每个引用 limited_use 还必须用 review_evidence={location,sha256} 指向上述证据中本轮图片执行者的真实限定用途复核或沿用的实际审图记录，保留准确观察者与范围，不要求主协调方重复加载全部图片。生成失败、后续失败审图会阻止本轮活动引用，不能把更换登记类型或限定用途当洗白。历史原图仍保留，不自动删除现有画布。结果明确 historical_generation_revalidated=false，并列出 history_gaps；不宣称旧方法链通过，不允许以此作为生成基准或正式交付输入。普通来源与正式入口的既有依赖/确认门禁不变。

预览时，`state_reference` 是可用的图片职责，必须写准确 state_id。连续两段若只共享物理状态、不采用平台硬首尾帧，后一段 continuity 可加 mode="state_reference" 与 boundary：claim="shared_state_only_not_hard_frames"、state_id、description、previous_node_id、current_node_id、previous_sha256s、current_sha256s。两侧哈希须精确等于对应段实际 state_reference 的文件哈希，且 state_id 相同；前驱链仍不得漏段、成环或跨事件。不同机位不冒充像素相同，也不虚造确认组；真实已有采用记录保留其原有范围。正式模式不接受 state_reference 替代原首尾帧门禁。

源稿只有整段生成秒数、未分配逐镜时间时，预览可明确 `timing_scope="segment_total_only"` 并将 shot_durations 写为该实际整段秒数的单项数组；只核对段总秒数和本轮平台上下限，不宣称已编排逐镜秒数。正式交付不接受这个预览标记。

候选输入切换与专用展示登记：`candidate_test.configure` 可保留本轮已登记的 `preview-reference`，只读核验实际媒体字节、历史证据哈希及证据中的精确路径/哈希绑定，不把其项目外路径扩成普通输入根。专用展示条目不得列入普通业务输入或依赖；正式生产仍拒绝。该核验不替代私有预览提交时的明确授权范围、原生绑定和刷新回读，也不自行新增生成许可或否认原记录中的历史候选生成使用。

## 本轮观察证据与输入版本

正式 `validate_submission` / `--delivery` 对每个 draft 消费同稿 `quality_evidence`；不改用户确认/审核权限。`input_sha256` 为 `scripts.core.quality_evidence.fingerprint(draft_binding(draft))`，覆盖 draft 全部字段但排除 quality_evidence 本身。正文、参数、有序引用、职责、镜号、时长和连续边界任一变化都会使旧检查失效。不要只改hash冒充重查。

同稿 evidence 含 status、observer、带时区checked_at，以及 checks 的六项 `action_chain / persistent_objects / state_boundaries / framing / timing / reference_conflicts`，每项保存 result（pass/fail/unknown/not_applicable）、observation、locator。无关项写具体理由及来源，不要求为无关动作虚构过程。正式入口缺项、失败或未核拒绝；只保存私有待审文字稿可不具备这个正式放行证据。

- action_chain：在原稿定位承托、左右手占用、接触、分离/落点、释放和后态；手已占用却无人接触脱头须返回上游，不以“动作自然”替代。
- persistent_objects：同场常驻物完整继承，核身份/类别/尺度/位置/内容物；改一件也回归同桌其他物件，撤参考后必须有真实替代身份依据或合法不出镜处理。
- state_boundaries：相邻全文和真实尾首观察，膜态按五区核，清水前末处湿擦→巾离脸/放下→无大片连续主膜；不以时间省略掩盖必需状态，未生成不能称动态已核。
- framing、timing、reference_conflicts：具体画幅草验；自然语速试读/实际操作估时与实测区分；阶段参考职责不冲突。保留锋利嘴部特写与有意义快切。

所有活动 image 引用（包括私有候选预览）还从现有 manifest 条目读取 [图像观察证据](../../skills/storyboard-director/references/image-evidence-admission.md)，draft 用 source_shot_ids 指明实际镜号。音频、视频和文档不套静态图检查。预览的外置文件仅沿原有明确 roots/input_roots 范围读取，不放宽磁盘根。

检查输出只称 evidence_consistency_only，并明确 visual_quality_verified=false、generation_input_provenance=unknown。完整但虚假的观察文字仍可能通过，不能据此证明已看图或模型执行正确。

### 草稿、提交与结果不混用

沿既有交付/任务记录保存三个不同事实：current draft 是当前编辑值；submission snapshot 是当次实际提交前冻结的完整正文、有序node/media/源文件hash、参数、带时区提交时间及可取得的真实job/version；output 是真实结果media/hash、检查范围与用户采用状态。不得拿当前草稿倒填历史任务。平台没有不可变job输入或结果关联时，input_provenance保持unknown，能判断输出错误但不能唯一归责模型。草稿比较器一律不从通过报告推出job已核；本次没有自动抓取平台job的能力。未知项不补假ID。

小云雀上传视频参考按同一来源hash的实际本地文件用ffprobe核时长，至少2秒；声明duration与实测不符即拒绝，不拿填入“2秒”代替媒体。段内切镜/shot_durations可少于2秒，二者不可混淆。此项不拼接参考视频，也不修改用户时长。

## 新稿不得继承旧回读通过

`native_binding.status=persisted_readback_verified` 是需核验的声明，不是可复制的业务常量。当前稿新增revision、改正文/参数/有序本地或原生引用后，旧声明不能用于本稿；保留历史回执，当前候选明确记unknown/stale/not_bound等未核状态即可，不补造新回读、不自动重写业务稿。

若声明verified，同一native_binding需保存当前`revision`、`input_sha256`（`fingerprint(native_input_binding(当前段))`，排除native_binding/quality_evidence，其他业务字段完整覆盖）、准确`evidence_path/evidence_sha256`、`canvas_url`和当前node身份。真实证据沿用canvas回读结构：`phase=after_refresh`、当前revision/target_url、drafts内准确node_id及prompt/parameters/ordered_media/native_mentions；不得把旧平台原始片段包一层新revision冒称刷新。原始证据格式无法核对时保留未核，不猜转换。消费者核当前内容、修订、hash、原生实体顺序；仍不认证采集行为或真实job输入。

现有canvas formal/private-preview和视频稿`check_video_prompt_focal_intent.py <稿件JSON> --project <准确项目根>`检查此声明；没有verified声明的文字候选可保存，不能说已回读。平台无关稿件采用入口也复核，旧回读不证明生成结果。focal检查器从固定main真实源码恢复，本轮新增这一声明核验，非stub。

交付操作前先只读定位节点并核画布URL、原生node_id、节点标题及当前媒体身份；定位不等于切换右侧面板，不以可见面板内容猜当前目标。确认定位与面板确属该节点后，备份实际当前正文、参数、有序原生引用及结果身份，再进行另有授权的修改。面板不能可靠关联时停在只读核查，不全选、不清空、不重试写入。本轮只修机制，不操作案例画布。

`product_refs --discover-dependencies`会输出独立JSON报告行，追加`--package`时另有packages JSON行，错误还有普通文本且退出码非零。调用者先核退出码，再逐行解析明确JSON报告并保留错误原文；不能对混合多行stdout直接json.loads，也不能取最后一行当总PASS。程序输出协议保持不变。

兼容旧别名时，当前活动段的native_binding_verified、materials等活动引用里的native_entity_verified若为true，也必须由上述同修订同输入的逐段native_binding证明；native_readback_source旧路径本身不证明当前实体。跨段边界或整包级的旧true字段不具备逐节点正文/参数，不自动转换成verified；回读结论落在实际逐段native_binding，旧边界结论仅保留历史。legacy native_delivery_scope字符串/已核摘要及final_native_receipt_path不能单独当本轮通过；需要留历史时放历史记录，当前状态明确未核。检查仅遍历当前drafts/generation_segments/creative_sections及其活动引用，不消费history、previous_revision_self_check、excluded_materials_this_revision为当前证据。不会修改或删除这些历史对象。当前目标必须来自顶层target_url或target_canvas.url并与回读一致，原生实体类型也须一致；换画布或纯文本实体不得沿用旧通过。

本地引用发现与最终引用类型检查共用 `scripts/core/media_types.py` 的扩展名集合，含 WEBM、MKV、M4A（大小写不敏感）。被发现只表示进入存在性和来源检查，不证明编码、时长、平台支持或内容已验收。


## 平台采集与离线合同的接缝

`canvas_readback.py` 消费实际采集的 `after_refresh` / `drafts`，并复核现有 `ordered_media`、`reference_checks` 与登记中的 `upload_bindings`。它不是平台采集器。`rhtv_target.js` 的通用 DOM text/value/attributes 读取只能证明所读字段，不能单靠标签、截图、连线或一次保存回执产生 `native_mentions`、媒体字节来源或刷新后持久化的事实。

接入准确平台时，先用已有获准只读入口确认目标、刷新动作和平台实际支持的原生节点/媒体/不可编辑实体字段；采集结果按现有字段交给 `canvas_readback.py --readback`，不新增平行绑定表。真实目标、完整正文/参数、有序原生身份或刷新事实缺一项即保持未验证；不能猜 selector、补空字段伪装“无引用”或由本地期望值生成 observed。换图同名、顺序交换、换修订、目标变化均重新采集并比对，不继承旧通过。

没有合法可用采集入口时，停在离线合同与 fixture 测试：可以验证接口拒绝反例，但必须写“线上绑定未验证”。Fixture 的 `.invalid` 目标和合成原生实体不是实际小云雀回执，也不授权写画布或生成。现有 `native_binding` 状态、来源 hash 和 confirmation 边界不变。

### 红帧富文本的局部只读采集

`rhtv_target.js` 新增结构化 `read-rich-editor`，仍要求明确目标和唯一编辑器 selector，沿原入口的导航检查执行，不接受任意 JS、不模拟 userGesture、不点击、不刷新、不调用平台内部 API：

```json
{"operation":"read-rich-editor","selector":"<本轮已只读核实且唯一的编辑器 selector>"}
```

已有合法浏览器读取入口也可将 `scripts/rhtv_rich_editor.js` 的自包含 `readRichEditor(root)` 作为准确编辑器 locator 的只读 evaluate 回调；不能把本仓库 CDP 可用性当作当前云浏览器已连接。先验证编辑器确属所选节点，刷新事实由实际操作证据另行建立，函数不补写 `phase=after_refresh`。

本轮实际观察到 `richWrap` 编辑器内的不可编辑 `richCustom` 标签，其后代公开 `data-reference-id`（URI 编码的源 node ID 与 image 类型）及 `data-value={{ref N}}`。采集按 DOM 顺序提取这些字段，沿最近不可编辑祖先核归属，不猜固定嵌套深度。可见标签、缩略图 blob URL、源 node ID 都不等于稳定 media ID 或文件 hash；输出 `native_mentions` 沿既有字段，但缺失的 `media_id` 不回填。直接交现有 `canvas_readback` 仍不能通过，`coverage.media_identity=unchecked`、`production_ready=false`。

`tokens` 仅是采集表示，不是新业务正文或绑定台账：普通 text node 逐字保留空白/FEFF，相邻文本节点合并；BR 与 DIV/P/SPAN 边界显式保留；完整原生引用替换成带 node/type/data-value 与准确 textContent 的 token；先检查其展示子树只含支持的 SPAN/IMG/Text，再跳过布局序列化，保留标签及额外文本变化，避免 `innerText` 的 CSS 换行抖动。禁止全局 trim/去空白、把 tokens 拼回已确认 prompt，或用期望值补 observed。未知结构/冲突实体返回未检查并清空不完整结果。普通块标签支持是保守通用实现，尚无当前红帧完整 DOM 样本证明覆盖；测试只来自已观察的脱敏属性合同与明确合成嵌套/正文，不是完整线上回执。

完整正文与参数、媒体字节身份、刷新后持久化及 frozen 修订仍须各自取得真实证据；该局部适配不宣称线上端到端绑定通过，也不改现有比较器或生成门槛。


### 红帧 node + 输出 port + 实际输入槽下载（显式分支）

默认 `media_id` 合同不变。平台公开 DOM 只提供 `[node UUID, output port]` 时，不能把第二项当媒体 ID；已观察的 `imageReference` 可指向 `image` 或 `main` 输出。只读 reader 保留 `reference_port`，不推断媒体 ID。

当前段可显式声明 `native_identity_mode: node_port_download`，`ordered_media` 每项严格为 `node_id/reference_port/sha256`，并在原有 reference 的 `upload_binding`（正式来源在 manifest 的 `upload_bindings`）绑定实际证据。新分支必须带 project，核准确目标 URL、项目目录、草稿修订/节点/segment、槽位、源修订与文件 hash。`evidence` 引用同一次刷新后的原始项目定位、编辑器实体、逐输入槽正常 UI 下载收据及源节点输出 port DOM；每份报告和实际下载文件必须在明确项目根内、字节 hash 相符。scope 还绑定三个原始报告 hash；槽位顺序、不可编辑实体、原始 percent-JSON tuple、token、下载收据对应实体以及 source output handle 必须交叉一致。任意本地路径/hash、图边、源节点缩略图或同名文件都不能代替该输入槽下载证据。

源节点在视口外时可以不渲染 IMG；不以另一处缩略图同时可见为门槛。此时仍须有真实源节点/输出 port 和实际输入槽下载的完整证据。新分支的回读实体使用 `download_sha256`，旧分支仍须真实 `media_id`，二者不混填。物理商品版本、图像用途 QA、上游权威、场景状态、稿件审核和生产授权检查不因此豁免。

这只是采集证据的一致性校验，不认证采集行为，不从调用方修订标签推出平台存在该修订号。不得把正文做全局去空白后称 exact；DOM canonical tokens 可分别证明实际刷新前后结构与文本保持，不能把 token 串回填原稿或用预期值补 observed。原始正文比较仍保留差异；私有候选通过也不意味着正式交付/生成就绪，所有结果仍 `production_ready=false`。

## 分段参数与已有前驱

现行生成段的实际 `parameters.model`、参考模式（`reference_mode` / `mode`）与生成时长（`duration` / `duration_seconds`）绑定准确源稿；别名可适配，但不得互相冲突。选定能力上限取自源的已核证据，交付自填 `duration_bounds` 不是能力证明。需改参数时先另出源修订，不在交付时静默替换。

只交付连续事件的后段时，保留真实 `continuity.previous_node_id` 和 `event_id`，并在同一 `continuity` 内添加 `previous_delivery`，仅含已有前次快照的 `artifact_id`、`revision`、`sha256`。该快照也须列于本次 `source_artifacts`，沿用正式已确认来源或当前私有候选范围，不创建新的批准。没有已有可核证据就报告缺口，不能删前驱、另起事件或补造快照通过。

检查器只读加载所需前驱节点，核原快照字节、同项目/目标/正式或私有模式、准确源修订与段号、实际参数、引用和切点。当前 `drafts` 仍只列本次要交付的节点，不要求重传前段。先前节点的更早前驱元数据保留，但不递归加载旧链；本次已加载节点中的自环、互环仍拒绝。无关旧段不会因过期文件而阻塞本次检查。该结果不证明远端节点当前存在、视觉正确或获准生成，实际回读与原流程仍须执行。
