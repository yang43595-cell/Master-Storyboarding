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
- `native_entity_type`：本轮平台实际原生引用实体类型。比较时要求每个 draft 的 native_mentions 逐项提供准确 node_id/media_id、相同 entity_type 及 native_noneditable_entity=true；纯文本同名标签或图边不足。

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
    "failure_scope_note": "如原图曾失败，说明仍失败的用途及本次实看后可用的不同限定用途；未失败时省略"
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

原结果为 failed_candidate 或有后续失败 review 时，必须保留原记录，显式填写非空 excluded_roles 与 failure_scope_note，而且实际使用职责不得命中失败/排除职责。机器不从自然语言推断失败原因，也不证明“换个用途就可用”：root 必须实际看图、确认所申报限定职责确实可用；原失败用途未修复继续拒绝。预览技术通过从不等于用户确认或合格生成基准。

只在真实用户明确要求清理时，将 `remove_unreferenced_candidates` 加入 operations 和 allowed_operations，并将准确旧候选加入同一快照 `cleanup`。每项字段为 node_id、target_url、target_project_id、status="candidate"、reference_count=0、reference_scan="whole_canvas"、recoverable=true、phase="after_refresh"、readback_ref（实际全画布引用检查来源）、authorization_ref（必须是 evidence 中用户消息 ID）；另将 cleanup_sha256 绑定 digest(cleanup)。不能删当前草稿/媒体、正式产物、非零引用或不可恢复对象。执行者仍须核实整画布真实零引用，不以此声明代替原生读取。

保存/清理后的实际读数仍须 phase=after_refresh、frozen_sha256、准确 target_url，并增补相同 target_project_id。清理时还需 `cleanup_results: [{"node_id": "准确旧候选ID", "absent": true}]`，按 cleanup 顺序逐项回读。快照及机器结果不能证明远端未发生生成、远端字节来源或画布私密性；执行者必须保持“无生成”的操作边界，完成既有布局/截图验收后才报告真实完成范围。

### 恢复素材与非硬首尾状态参考

真实集成中，旧试验收据可能仍保留旧绝对路径、方法文件也可能在下一次执行时已更新。展示原图不是重新执行旧生成流程：仅在 root 已实际核对准确图像/用途时，可在本项目 manifest 新登记 `type="preview-reference"`，保持 candidate/unreviewed/production_blocked，path_or_url 指向实际恢复/复制文件，checksum 固定真实媒体字节。原试验、业务正文、确认记录和失败结论不改，也不删除原状态标记。

该条目 `preview_origin` 必须包含 purpose="private_preview_only_not_generation_baseline"、source_sha256、history_gaps（真实未重验/缺失/迁移事实列表）、historical_evidence（每项 location 为原证据绝对文件名，sha256 为证据实际字节）。原生成收据若存在，必须连同原 request 与全部后续 review-overrides 保存引用；至少一份原证据结构必须绑定该图片 SHA。旧方法缺失/改字节只能如实报告，不伪恢复或改 request 来求通过。字段 location 明确表示历史出处，不把历史 JSON 内旧文件路径重新当本轮生产依赖。

每个引用 limited_use 还必须用 review_evidence={location,sha256} 指向上述证据中的真实 root 限定用途复核或沿用的实际审图记录。生成失败、后续失败审图会继续触发失败用途检查，不能把更换登记类型当洗白。只在准确已实看且不含失败用途的限定范围展示。结果明确 historical_generation_revalidated=false，并列出 history_gaps；不宣称旧方法链通过，不允许以此作为生成基准或正式交付输入。普通来源与正式入口的既有依赖/确认门禁不变。

预览时，`state_reference` 是可用的图片职责，必须写准确 state_id。连续两段若只共享物理状态、不采用平台硬首尾帧，后一段 continuity 可加 mode="state_reference" 与 boundary：claim="shared_state_only_not_hard_frames"、state_id、description、previous_node_id、current_node_id、previous_sha256s、current_sha256s。两侧哈希须精确等于对应段实际 state_reference 的文件哈希，且 state_id 相同；前驱链仍不得漏段、成环或跨事件。不同机位不冒充像素相同，也不虚造确认组；真实已有采用记录保留其原有范围。正式模式不接受 state_reference 替代原首尾帧门禁。

源稿只有整段生成秒数、未分配逐镜时间时，预览可明确 `timing_scope="segment_total_only"` 并将 shot_durations 写为该实际整段秒数的单项数组；只核对段总秒数和本轮平台上下限，不宣称已编排逐镜秒数。正式交付不接受这个预览标记。
