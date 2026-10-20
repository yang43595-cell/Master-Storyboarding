# 现有交付快照的只读核验

此入口只检查已经形成的 JSON 交付内容，使用现有 `canvas_readback.py`、项目 artifact manifest、确认记录与 revision_control，不重新引入 production_plan 或独立账本。不产生图片、视频、上传、网页操作或用户批准。实际内容和来源由执行者核对，机器检查不替代视觉阅读。

## 同一份 JSON

复用既有 `drafts`、`prompt`、`parameters`、`ordered_media`。业务修订写在顶层 `revision`，来源写 `source_artifacts`（每项准确 artifact_id、revision、sha256）。网页目标仍用准确 `target_url`。历史快照必须显式以 `--legacy-read-only`（Python API 为 `legacy_read_only=True`）按原范围只读比较，返回 submission_checks=not_checked 与 production_blocked=true；删除 revision 不能降级为默认历史通过，不得据此宣称完整提交检查或用于新交付采用。

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

`--delivery` 是每次新画布交付的明确调用模式，要求准确项目和非空修订，继续复用当前来源、引用及原生回读校验。它不是后台拦截器：绕过此入口的外部操作不会自动获得检查。历史只读比较须用独立的 `--legacy-read-only`，不能与 project/delivery/preflight 混用；历史结果不能替代正式交付。缺 manifest 或真实确认时如实报告，不自动补审批。

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

## 候选修正：新交付的文档身份与来源（2026-10-20，尚未启用生产）

沿用同一视频业务 JSON，不建立 production_plan 或额外账本。新交付使用 `schemas/video-prompt-identity.schema.json` 的窄身份合同：`schema_version: video-prompt-1.0`、`document_type: video.prompt`、`stage: voiceover-to-video-script`、现有 `project_id` 与准确 `revision`。`source_storyboard`、`source_narrative` 各自列实际 artifact_id、logical_id、revision、path、sha256；drafts 每项保留正文，并明确 `source_shot_ids`。来源的 ID、类型、路径、修订和字节须与本项目既有 manifest、当前确认和上游业务 JSON 同时一致。项目身份取既有叙事包 project_id，并与导演包核对；不得另起编号系统或按目录名猜填。

检查实际导演/叙事包 schema；导演包指向的叙事来源必须与本次提示词声明的是同一已登记版本。不得只改文件名、顶层类型或 revision 冒充另一阶段；携带导演 scenes 等结构的重标文件明确拒绝。每段引用的镜头必须真实存在于所选导演包并保持其内部顺序。测试候选来源只从该 run 的明确选择解析，结果始终 unreviewed/production_blocked；生产入口不接受测试来源。

- JSON 文档交付身份核对：`python scripts/check_video_prompt_focal_intent.py <本修订JSON> --project <项目>`。明确的候选试写另加 `--candidate-test`，不会取得生成或采用授权。
- 画布交付继续运行现有 `canvas_readback.py --delivery`；回读的文档身份/来源关联也须与冻结快照一致，连同正文、参数和原生素材引用一起检查。
- 每段回读的 `source_shot_ids` 以及重复来源清单必须与冻结稿一致；重复清单若提供 logical_id/type/project_id/path/path_or_url，不得与准确来源冲突。来源 JSON 在同一字节快照上计算 SHA-256 并解析，返回前再核对文件未改变；这不是对后续编辑的永久锁定。
- 历史快照只读比较必须显式使用 `--legacy-read-only`，不得同时传 `--delivery`、`--preflight` 或 `--project`。结果保留 `submission_checks: not_checked`、`production_blocked: true`，不得拿来批准新交付。旧版本本身不改、不删；需要新交付时按实际来源建立新候选版本，缺失元数据须核对后补，不由程序推断或自动迁移。
- TXT/Markdown 焦段辅助检查仍只检查已知文字问题；其 PASS 不是文件类型、项目或来源合格证据。正式业务稿仍须通过上述 JSON 身份核对，TXT 仅是经核对业务稿的提交副本。

该合同不判断自由正文是否真正完成导演稿到提示词的转译，不证明已人工看图、对白完整、动作成立或生成质量。身份合格结果明确返回 `semantic_translation: requires_manual_review` 与 `generation_authorization: not_granted`。写有“导演稿”的合法对白不能靠关键词封禁；把导演文字装入合法视频外壳仍须人工发现并拒绝。manifest/确认记录是现有本地可信边界，不是密码学签名；本检查防普通重标、错项目、错版本与错链，不声称可抵御能修改全部文件及确认记录的人。

迁移影响：旧视频 JSON 即使引用/时长曾通过，只要缺少身份字段，新交付检查就会拒绝。只读历史比较不回填字段、不改旧确认；新候选登记、复核、用户确认及生产采用仍走现有流程。本修改当前只存在隔离测试副本，需独立复核和明确生产应用范围后才可接入默认入口。
