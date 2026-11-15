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

同一快照保存准确来源 revision、登记记录和实际素材文件哈希、真实原生媒体映射、本轮图片执行者实际审图后的职责/实体/片段限定范围及观察者依据；历史失败素材及采用记录保留只读，但本轮新增或更新的活动图引用不得以有限关系用途豁免仍可见的身份、服装、工序等冲突。失败或未核图不得加入活动引用；有图的私有候选预览也须核对原登记中的本用途实看证据。缺合格图时可保存不带该图的文字候选并列明缺口，不成为生成许可。字节、依赖、时间、连续边界、原生引用和刷新回读校验不豁免；具体字段与命令见 [交付快照校验](../../docs/development/delivery-snapshot-checks.md#私有候选预览)。程序仅检查声明与证据一致性，不判断视觉质量，也不是能拦截所有浏览器行为的安全沙箱。
