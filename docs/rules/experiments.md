# 明确授权的隔离试验

本文件是项目规则的职责细则；适用范围及默认流程以 [项目总规则](../../PROJECT_RULES.md) 为准。只在本轮职责或明确选用的分支内读取。

## 独立候选链路试跑

仅在用户明确要求独立盲测、从头跑候选流程时适用：测试执行授权不等于对白核对、产物审核、正式采用或生成授权。只在独立项目目录试写故事、导演、资产制作说明与视频提示词，全部保持 `candidate` / `unreviewed`，不得读取旧案例答案。此处允许未确认候选进入下一候选阶段，是生产阶段下游只读确认输入规则的测试例外；编导先自行核对来源与剧情后送完整候选整稿审核，不强制用户逐句先批，不改变正常生产门禁。

复用同一业务 JSON 与稳定 ID，不另建测试稿格式。对白保留真实来源证据和未核状态，疑点、缺图与假设写现有未决字段；不制造 verification、locked、confirmed_outputs、user_confirmations 或 current。结构与来源一致性检查继续执行，未知事实、错字节、错引用不能用测试模式掩盖。未生成图片时只能验资产需求及提示词链，不能宣称视觉效果或成片已经验证。

本轮运行在现有 `project_state.json` 保存 `candidate_test`，现有 manifest 中候选标记 `unreviewed` 和生产阻塞。以下命令只记录用户已给出的测试授权引用，不自行取得授权；输入 ID 每次列全本阶段实际用到的准确候选及其依赖，不按最新文件猜选：

- 配置或更新输入：`python scripts/stage_context_cli.py candidate-test --project <独立项目> --run-id <本轮ID> --authorization-ref <用户原话或消息引用> [--skill <当前候选Skill>] [--input-artifact <准确候选ID> ...]`
- 新建隔离项目的 `current_stage` 默认为空，首次配置须显式传 `--skill`（如 `creative-director`）；不从输入或 prepare 猜阶段。候选交接先用 `handoff --candidate-test --to-skill <目标Skill>`，随后在原测试授权范围内沿同一 run-id、authorization-ref 和完整准确输入 ID，用 `candidate-test --skill <目标Skill>` 显式记录下一候选阶段。只接受仓库中真实存在的 Skill；不写入生产 `next_stage`、current 或确认，也不代表阶段创作已完成。省略 `--skill` 保留原阶段；空阶段仍阻止 status/交接。
- 普通候选仍用 `artifacts.register_workspace_artifact` 登记；不登记确认 checksum。测试标记由登记入口继承，不写入 current_artifacts。
- `prepare`、`ensure-prepared`、`handoff` 额外传 `--candidate-test`。检查 `ok` 只表示本次候选结构/准备检查通过；结果始终含 `production_blocked: true`。专业方法读取不豁免。
- 故事与导演检查：`python -m scripts.core.story_sources --project <独立项目> --narrative <候选JSON> [--storyboard <候选JSON>] --candidate-test`。未核对白列在结果中；去掉测试参数仍按生产规则阻塞。
- 视频沿现有直接读取和候选试写入口，读取本轮测试上下文与准确候选，不启用退役 prepare/production_plan。资产网页仍不接受候选项目；本轮在本职 Skill 写候选需求和制作说明，不导入正式资产库。

禁止正式采用、图片/视频生成、发布及其他外部提交；仅下文“明确授权的私有候选画布预览”允许指定私有画布的参考上传与未生成草稿保存，不放开正式生产。已接线的产物 promote、修订确认/commit、付费授权、canvas 提交前检查及项目关联图片生成入口会拒绝测试项目；移除状态开关不清除 manifest 的测试标记。直接浏览器、独立提供商 CLI 及脱离项目身份的工具并未形成统一技术沙箱，执行者不得调用，不宣称全部出口已被程序封闭。进入正常生产须重新建立真实确认与合法输入，不能删标记自动晋升测试稿。


## 计量试验与工作流合规测试分开

协调方先按用户的具体目标区分：验证 token/成本/预算控制，还是验证来源、方法、创作、交接与质量流程。只有明确要求前者，或已有需要控制的异常消耗，才为对应运行初始化必需计量合同；“实测”“盲测”“循环修复”本身不构成每案必须先计量的依据。无法导出宿主用量时仍记未知，不能报告成本节省或计量通过。

错误扩大范围形成的旧受控运行保持原合同、失败和未测记录，不删字段续跑、不改失败成通过。经任务范围核实后，另建明确工作流合规范围的独立候选运行，保新 run/目录身份，不继承原批准或方法阅读声明；这不是用新 ID 绕过仍适用的预算、明确成本要求或异常消耗停止条件。原计量验证单独保留未完成，工作流分支结果不能替它结案。

工作流分支仍核准确来源、实际阅读、产品/素材用途、真实声画事实、QA与批准边界。只有内容确实含声画来源时才要求对应声画核对，ASR安装不是唯一合法核对方法；机器转写不等于听核。离线候选可检内容与依赖，但缺实际平台绑定不得称已可提交生成。Windows专项、旧私有回归样本、特定平台能力等仅限制各自的覆盖，不自动成为每个不依赖它们的编导/导演检查前置。记录可做/未测/明确豁免，不能用“可独立检查”放宽真实制作标准。

测试清单若有目标耦合，应保留旧冻结清单和回执，另建带明确适用范围与原因的新分支；不得直接删原失败项或把未测挪成全局通过。流程、计量、平台提交三类结果分开报告，总范围未全部验证时仍不得宣称全部完成。

## 受控候选的计量合同

当用户明确要求计量试跑时，由协调方在创建该隔离运行时，把任务选定的要求登记进已有 `candidate_test`，再交给执行者；不能把是否登记再次变成执行者的可选步骤。普通未要求计量的项目仍沿用原行为。

`candidate-test --usage-contract-json <本轮合同.json>` 的合同为：

```json
{"required": true, "turn_id": null, "limits": {"max_input": 80000, "max_total": 600000, "max_requests": 8}}
```

这些数字仍只是示例，必须按本轮任务确定。未知准确 turn 可先登记 `null`：这表示要求已启用但缺证，`prepare` / `ensure-prepared` 会拒绝，不表示免计量。合法宿主证据到位后，可在同运行、同授权下把未知 turn 补成准确值。已确定 turn 不可换；重复配置省略合同会保留原要求，预算只能收紧，不能抬高；不能在同项目更换 run ID 绕过旧要求。

执行入口仍传准确 `--usage-turn-id` 和合法 `--usage-log`；预算省略时使用已登记值，传入值只能更严。缺日志、未知用量、错 turn、达到预算均拒绝。此单 turn 合同不接受换成任意多轮边界文件；原有未启用该合同的边界集合计量方式不变。不要访问被禁止的宿主路径、用合成测试日志冒真实计量，或将检查点说成宿主硬预算。

实际执行按本轮[普通准备计量](../development/token-budget.md#普通准备的计量检查点)或[视频直读与画布计量](../development/token-budget.md#直接读取和画布的同一计量检查点)操作。

## 历史正文批准与原声证据分层（仅隔离候选）

用户真实批准的准确正文，不因历史原声审核附件未恢复就被当成“从未批准”；但正文批准不替代原声保真、资产确认或完整结构化包正式采用。先核原字节、项目/修订、真实批准消息与实际可见范围，不凭文件名、摘要或旧网页 pending 推断。

若本轮明确授权仅用该正文写有限段落候选，沿已有 `candidate-test` 隔离入口登记原 `narrative.package` 和 `source.evidence`，保持原 JSON 字节/历史路径不变，全部仍为 candidate/unreviewed/production_blocked，不写 user_confirmations/current。范围证据亦登记为 source.evidence，内容为：

```json
{"purpose":"approved_text_candidate_input","source":{"artifact_id":"准确叙事ID","project_id":"原项目","revision":"准确修订","sha256":"原文件SHA256"},"paragraph_ids":["本轮允许的STP"],"approval":{"reference":"真实历史用户批准引用","evidence_artifact_id":"真实批准上下文证据ID","sha256":"该证据文件SHA256"}}
```

叙事、批准附件、范围证据均必须登记准确 checksum，并列入本轮 candidate-test 输入白名单。历史批准范围由执行者实读原消息/原显示版本核实；程序只能检查绑定一致性，不自动证明批准语义。不得把候选级记录升为正式确认，也不得以另一个版本/项目/后续待批改动替换原批准正文。

运行 `python -m scripts.core.story_sources --project <隔离项目> --narrative <原JSON> --candidate-test --candidate-source-evidence <范围证据ID>` 先核输入；本轮导演候选再加 `--storyboard <候选JSON> --candidate-paragraph-id <准确STP>`（可重复，必须在证据范围内）。仍核原文、对白/说话者顺序、资产与镜号引用。缺历史对白证据明确报告 missing_unverified，全部原声核验仍未通过；存在但 hash 或对白不一致直接拒绝，不能抓住异常后忽略。

此入口不修改普通正式 adopt-package/返修提交规则，不允许用局部来源检查充整稿交接或生成许可。若无法满足真实输入/范围绑定，保留缺口，不制作虚假附件、不删除原证据字段。


## 明确授权的隔离资产实图试验

可按 [持续制作授权](../../PROJECT_RULES.md#持续制作授权不逐批重复询问) 承接同一项目后续已登记资产及必要返修。每个试验仍冻结准确白名单和真实授权依据；分批冻结是执行记录，不是要求用户逐批审批。每次 preflight 对应一次调用，不将其误读为“每个目标永远只能生成一次”；视觉问题可在既有授权内另建准确修订和尝试记录，失败或结果未知仍不自动重提。

同一项目分批制作换装时，可用 `generated_reference_bindings` 将已实看合格的原创初始综合图绑定为同 AST 的身份参考，不把生成图伪列原始参考，不要求事后改写原生成角色或额外独立审核。入口绑定准确源试验、attempt、原生图片、来源叙事和用途观察；仅支持无图片上游的合格初始综合图，生成前后复核来源字节及撤销状态。失败图、裁切洗白、跨人物或换来源仍拒绝，自查合格不成为用户审图或正式采用。

阅读声明与方法正文分别核验。新 `preflight` 将 `method_reading_evidence.json` 声明冻结到该 attempt 内，并保留原路径和摘要，避免同上下文追加阅读登记破坏旧图片凭据。历史图片若仅此声明发生漂移，而方法正文仍与原摘要一致，可由实际执行者重新读方法、看准确原图并通过当前准备检查，追加 `source_method_revalidation`；保留原 expected/actual 差异及 `historical_method_drift`，不修改旧请求、不追认历史阅读。方法正文变化、失败图片、身份或来源不符仍不得经此入口放行，生成结束时重核新复核依据。

方法正文发生变化时默认仍拒绝沿用旧凭据。用户明确授权恢复后，可用 `asset_generation_trial.py revalidate-methods --trial-root <原试验> --request <新复核JSON>` 为准确 attempt 追加 `method-revalidations/<review_id>.json`：执行者须实读当前方法、实看准确原图，记录 `observer`、`authorization_ref`、`method_observation`、`visual_observation`、`passed: true`、`image_path`，并提供原请求的完整 `historical_methods` 与当前同路径 `current_methods` 摘要。命令绑定原请求、原图及新旧方法字节，不改旧请求、结果、失败或人工确认。已完成图必须原为合格候选且没有后续否决；未完成图只允许绑定真实已有输出，之后仍须正常 finish 及完整依赖检查。图片、原参考或当前方法再次改变即失效；失败图、未知结果和复制参考不能借此转为合格。不生成、不正式采用，也不追认历史执行符合新方法。

用户在候选盲测后另行明确要求实际生成资产图，并确认工具及模型披露时，可使用 `scripts/asset_generation_trial.py` 的独立试验入口。此授权只允许明确清单内的资产候选生成与视觉检查，不改变原 `candidate_test`、上游候选、对白核对或正式生产门禁。新试验目录使用自身运行身份，sidecar 保存准确用户授权原话、消息引用、工具、模型披露、资产白名单及来源哈希；不能复制旧状态冒用授权、删除阻塞标记或经独立资产库绕过项目限制。

执行 `init` 后，每张图先 `preflight` 固定完整提示词、实际查看的原始参考/本轮合格基准、方法阅读依据与字节哈希，再由宿主内置 `image_gen` 调用一次，最后 `finish` 保存真实图片、前后哈希和视觉自查结果。生成失败或结果未知用 `fail` 保留事实，不自动重复提交。方法全文和实际看图要求不豁免，阅读声明与程序通过均不证明视觉质量。只允许白名单原始参考及同资产本轮检查通过的独立基准，不复用旧产物或失败图；所有输出保持 candidate/unreviewed/production_blocked，合格自查不等于用户确认。不得正式采用、生成视频、发布或提交正式画布；另有明确私有预览授权时仅按下节执行，需正常生产时另行建立真实确认与合法输入。


## 明确授权的候选分镜补图

用户明确要求在本轮候选链路中检查并实际补充分镜图，且已有覆盖工具、模型披露及额度方式的持续制作授权时，由 `storyboard-director` 按准确候选导演、现有候选素材和真实产品参考检查并制作必要补图。该例外只开放准确请求范围内的图片生成与用途自查，不开放视频生成、正式采用、发布或画布提交；不将上游候选改成 confirmed。

沿现有 `visual-supplement-request`、普通批次 prepare/ensure、图片制作与 `quality_evidence` 记录执行；生成前冻结请求、完整提示词、准确源修订、实际参考字节及用途，调用内置 `image_gen` 后保存真实图片、技术参数和实看结果。多人物关系图不得伪列为某一个 AST 的独立资产，也不把合格生成参考改标为原始参考来套用资产试验入口。图片执行者只写独占批次及请求的制作/观察依据，阶段负责人统一登记。失败图和未核图不得回灌；切点与场景新态仍须实际覆盖。候选补图完成后可供同轮候选视频提示词按准确镜号试写，不成为可提交生成或人审通过的依据。

## 明确授权的私有候选画布预览

用户另行明确要求把候选放进指定私有红帧或小云雀画布供其验收时，可以沿现有 frozen 交付快照使用 `canvas_readback.py --private-preview`；模式只承接既有授权，不是授权来源。明确要求在指定平台新建画布时，先真实新建并取得准确 URL/项目 ID，再冻结上传与草稿范围，不要求用户先提供尚未创建的链接。执行者从真实对话核实候选范围和允许操作，快照中的消息引用/原话只供核对声明，不证明真人许可。普通 `--delivery` 与正式采用入口继续拒绝候选；不得删测试标记、补造 confirmed/current 或把预览通过写成用户确认。两平台分别核实际参数、原生引用与保存/刷新能力，不把小云雀参数套给红帧。

只允许范围内参考上传、保存未生成草稿，以及另有明确清理授权时删除本画布经全画布刷新核对为零引用、可恢复的旧候选。不得生成图片/视频、付费、正式 promote/current、公开发布或扩大到其他画布。全部保持 `candidate` / `unreviewed` / `production_blocked`；不改业务 JSON、原生成失败记录或确认状态来过门禁。

同一快照保存准确来源 revision、登记记录和实际素材文件哈希、真实原生媒体映射、本轮图片执行者实际审图后的职责/实体/片段限定范围及观察者依据；历史失败素材及采用记录保留只读，但本轮新增或更新的活动图引用不得以有限关系用途豁免仍可见的身份、服装、工序等冲突。失败或未核图不得加入活动引用；有图的私有候选预览也须核对原登记中的本用途实看证据。缺合格图时可保存不带该图的文字候选并列明缺口，不成为生成许可。字节、依赖、时间、连续边界、原生引用和刷新回读校验不豁免；具体字段与命令见下文。程序仅检查声明与证据一致性，不判断视觉质量，也不是能拦截所有浏览器行为的安全沙箱。

### 私有候选预览

核实上文的授权、目标和范围后，在写入前按[提示词内容合规审查](../../skills/voiceover-to-video-script/modules/video-method.md#提示词内容合规审查)查当前正文。沿现有[快照字段](../development/delivery-snapshot-checks.md#同一份-json)记录正文、参数及实际引用，再使用下列只读校验命令。`--private-preview` 与 `--delivery` 互斥。命令不操作浏览器或改项目状态，预览快照不得送 `revision_control` 作正式采用。

```sh
python scripts/canvas_readback.py --private-preview --project <准确候选项目> --frozen <本轮预览快照.json> --preflight
python scripts/canvas_readback.py --private-preview --project <准确候选项目> --frozen <同一快照.json> --readback <实际刷新后读数.json>
```

沿原有完整 drafts/source_artifacts 增补以下字段，不另建事件包。所有示例占位值必须由执行者换成真实值，不能作为可执行授权。参数只允许 `duration`／`duration_seconds`、`aspect`／`aspect_ratio`／`ratio`、`resolution`、`model` 和 `reference_mode`／`mode`。生成、付费及提交控制字段会被拒绝。`prompt` 始终是待保存的正文。

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

这里 digest 指本脚本的 canonical JSON SHA-256，不是文件文本 hash。`source_projects` 以规范化绝对路径列出本项目及本轮引用的候选项目，去重后每个项目仍须保有合法 candidate_test 运行身份。`input_roots` 只为原有依赖发现提供实际外置文件的窄范围根，路径同样须规范化，按需省略，不填磁盘根。`source_artifacts` 不可空，继续绑定本轮实际源修订。所有来源及 reference_checks 增补：

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

#### 已核原生首尾输入的严格候选路径

本轮平台确有首尾输入时，使用现有回读入口的 `first_last_frame_download` 分支，不以模式字符串单独放行。需同一目标、准确稿件修订、同一次刷新后的模式与原生顺序提示、两个真实输出端口、真实连线、首尾输入槽及正常下载字节证据全部相符；旧 `media_id` 与多参考检查保持原规则。

所选源稿须显式登记本段 `endpoint_strategies`，绑定完整状态链、准确镜号、段内初态与末态以及正文中的中间事件。首尾图须有本用途、实际字节、端点镜号与观察范围的独立检查。首帧还可兼身份或外观职责，但这不把首帧控制扩成逐镜控制。画外 `not_visible` 只能作为已注明来源的约束，不贡献视觉覆盖，也不等于不存在；可见画幅内缺席不能证明全场不存在。中间事件仍为来源意图，程序固定报告未验证动态、未提供逐镜控制。

该路径可用于已授权的隔离候选验证，不产生用户采用、正式生产授权或成片验收。缺来源、状态链不完整、旧检查用途失效、槽位/刷新/字节不符仍拒绝；需要原生中间状态锁定的段落不得借首尾两图绕过。

#### 恢复素材与非硬首尾状态参考

真实集成中，旧试验收据可能仍保留旧绝对路径、方法文件也可能在下一次执行时已更新。展示原图不是重新执行旧生成流程：仅在 root 已实际核对准确图像/用途时，可在本项目 manifest 新登记 `type="preview-reference"`，保持 candidate/unreviewed/production_blocked，path_or_url 指向实际恢复/复制文件，checksum 固定真实媒体字节。原试验、业务正文、确认记录和失败结论不改，也不删除原状态标记。

该条目 `preview_origin` 必须包含 purpose="private_preview_only_not_generation_baseline"、source_sha256、history_gaps（真实未重验/缺失/迁移事实列表）、historical_evidence（每项 location 为原证据绝对文件名，sha256 为证据实际字节）。原生成收据若存在，必须连同原 request 与全部后续 review-overrides 保存引用；至少一份原证据结构必须绑定该图片 SHA。旧方法缺失/改字节只能如实报告，不伪恢复或改 request 来求通过。字段 location 明确表示历史出处，不把历史 JSON 内旧文件路径重新当本轮生产依赖。

每个引用 limited_use 还必须用 review_evidence={location,sha256} 指向上述证据中本轮图片执行者的真实限定用途复核或沿用的实际审图记录，保留准确观察者与范围，不要求主协调方重复加载全部图片。生成失败、后续失败审图会阻止本轮活动引用，不能把更换登记类型或限定用途当洗白。历史原图仍保留，不自动删除现有画布。结果明确 historical_generation_revalidated=false，并列出 history_gaps；不宣称旧方法链通过，不允许以此作为生成基准或正式交付输入。普通来源与正式入口的既有依赖/确认门禁不变。

预览时，`state_reference` 是可用的图片职责，必须写准确 state_id。连续两段若只共享物理状态、不采用平台硬首尾帧，后一段 continuity 可加 mode="state_reference" 与 boundary：claim="shared_state_only_not_hard_frames"、state_id、description、previous_node_id、current_node_id、previous_sha256s、current_sha256s。两侧哈希须精确等于对应段实际 state_reference 的文件哈希，且 state_id 相同；前驱链仍不得漏段、成环或跨事件。不同机位不冒充像素相同，也不虚造确认组；真实已有采用记录保留其原有范围。正式模式不接受 state_reference 替代原首尾帧门禁。

源稿只有整段生成秒数、未分配逐镜时间时，预览可明确 `timing_scope="segment_total_only"` 并将 shot_durations 写为该实际整段秒数的单项数组；只核对段总秒数和本轮平台上下限，不宣称已编排逐镜秒数。正式交付不接受这个预览标记。

候选输入切换与专用展示登记：`candidate_test.configure` 可保留本轮已登记的 `preview-reference`，只读核验实际媒体字节、历史证据哈希及证据中的精确路径/哈希绑定，不把其项目外路径扩成普通输入根。专用展示条目不得列入普通业务输入或依赖；正式生产仍拒绝。该核验不替代私有预览提交时的明确授权范围、原生绑定和刷新回读，也不自行新增生成许可或否认原记录中的历史候选生成使用。
