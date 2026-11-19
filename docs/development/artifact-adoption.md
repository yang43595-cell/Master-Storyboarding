# 编导确认与下游自主采用

按[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)选择当前操作。编导网站通过按[网站决定与采用](review-workbench-operations.md#编导网站决定与采用)办理，资产图沿本轮网站或对话人审。

## 编导对话确认

计划在工作台审图时，首次交编导审阅就给准确网站版本的入口，由用户在站点通过，协调方读取并采用同一决定。计划在对话审图时沿用已有项目授权，交准确编导稿供对话确认。通道沿用到资产审核，用户明确变更时说明新通道所需依据。

交给用户审阅时保留业务项目、产物 ID、叙事包 JSON 路径、修订和文件原始字节的 SHA-256。用户在项目对话里明确说「编导稿 R03 整稿通过」后，协调方当轮执行以下入口。用户只需确认稿件，命令及确认依据由协调方处理。

```sh
python scripts/stage_context_cli.py confirm-script-in-chat \
  --project '<业务项目绝对路径>' --artifact-id '<产物 ID>' \
  --path '<准确叙事包 JSON>' --revision R03 --sha256 '<审阅时登记的 SHA-256>' \
  --confirmation-json '<本次确认依据 JSON>'
```

依据 JSON 使用 `scope: "artifact"`、`quote`（用户原话）、`at`（发送时间，含时区）、`review_ref`（能定位实际消息的对话引用）。只从真实用户消息填写；一条消息涉及多个项目、版本或仅确认某段时，只按可明确匹配的整稿范围办理。首次采用可登记准确候选；已有正式版本的返修附加 `--change-id`，指向已登记该候选的活动变更案。确认与采用在同一事务提交，真实确认渠道写为 `chat_confirmation`。重复办理同一消息时复用原回执。文件与审阅时的字节有变化时，先交回准确新稿。

使用原片硬字幕并请用户随整稿核对白及说话人时，交稿前用 `python -m scripts.core.story_sources --project <项目> --narrative <叙事包> --review-ready --chat-review --export-markdown <阅读稿>` 导出实际披露范围。用户明确通过该版整稿后，在同一个确认依据 JSON 增加 `review_markdown: {"path": "<审阅稿>", "sha256": "<审阅时登记的 SHA-256>"}`。入口核对审阅稿正文、附录及披露的对白 ID，只沿这次整稿回执采用本版可核条目。来源冲突或说话人未定位的条目仍须解决，硬字幕依据仍标明未人工听核、未核时码。

回读确认版本和 `current` 后，按[编导通过后接导演](#编导通过后接导演)继续导演，并按已定通道继续已获授权的资产工作。临时改走工作台时，资产发布仍依赖同版编导的网站通过。有新一版编导时复查受影响的导演和资产依赖。

## 编导通过后接导演

运行 `python scripts/stage_context_cli.py handoff --project '<业务项目绝对路径>' --to-skill storyboard-planner`。当前整稿已确认并正式采用、项目尚未登记下一阶段时，命令复用该版网站通过决定，或复用覆盖导演的既有流程授权，登记交接并生成交接卡。遇修订阻塞、阶段冲突或确认版本漂移时，先核对原记录。需要隔离导演任务时沿[任务身份参数](../../skills/_shared/knowledge-access.md#普通编导和导演的任务隔离)交接，由导演执行者核输入和准备状态。

## 登记下游流程授权

下游自主采用开放给 `storyboard-planner`、`storyboard-director` 和 `voiceover-to-video-script`。

**开发接线授权不等于真实业务流程授权。** 用户同意开发本能力，只允许实现和合成验证。执行真实业务前，必须已有用户对准确业务项目及列明阶段的流程授权；只把该真实依据传给下列命令，不把本轮开发指令、文档示例或 agent 自查当成用户授权。

已有准确业务流程授权后，由协调方登记其原引用；阶段可重复列出，未获授权的阶段不加进来：

```sh
python scripts/stage_context_cli.py authorize-workflow \
  --project '<业务项目绝对路径>' \
  --authorization-id 'FLOW-R01' \
  --authorization-ref '<用户对该业务项目及阶段的准确流程授权引用>' \
  --skill storyboard-planner \
  --skill storyboard-director \
  --skill voiceover-to-video-script
```

该命令只登记授权，不确认任何稿件、不启动下一 Skill，不授权额外生图、上传、付费或视频生成。候选试跑项目不能借此转成生产。

## 下游自查证据

生产执行者完成本职检查后，由阶段负责人复用 `scripts.core.artifacts.register_workspace_artifact()` 统一登记准确候选；采用命令不代登记候选，不猜文件或修订。图片批次自查须保留实际观察者及准确范围，阶段负责人核完整覆盖和跨批关系，不能把一句“各批通过”当成整阶段检查，也不冒写为自己已看图。自查保存在项目内同一份 JSON，使用 `scripts.core.agent_acceptance.acceptance_binding(project, entry)` 返回的完整实际绑定，包括 `artifact`、`upstreams` 和该产物适用的附加绑定；`review_profile_sha256` 取目标 Skill 当前 `review-profile.json` 的原字节 SHA256。报告必须完整覆盖该 profile 的 `production_artifact_checks`：每项有真实观察与正文／图片定位；不适用项使用 `not_applicable` 并给出理由，不能用不适用掩盖未检查或失败。核心检查必须通过，不可标为不适用；准确 ID 以 [agent_acceptance.py 的 CORE_CHECKS](../../scripts/core/agent_acceptance.py) 为准。源稿仍为 `blocked`／`rejected`、尚有未闭合 `open_questions` 或生产阻塞时，不能用自报 `pass` 覆盖，仍保留候选。

报告字段随当前产物填写。顶层 `schema_version: "1.0"`、`kind: "agent-self-check"`、`scope: "artifact"`、`verdict: "pass"`，另填真实 `authorization_id`、`agent_id`、带时区的 `checked_at`、`review_profile_sha256`，以及 `acceptance_binding()` 的对应字段。`checks` 每项包含 `id`、`result`、`observation`、`locator`，`not_applicable` 项另填 `reason`。

独立补图在同一报告的 `usage.source_shot_ids` 列出实际获准镜号；`acceptance_binding()` 还绑定 `quality_evidence_sha256` 与 `product_reference_sha256`，须原样带入，不只复制图片文件 hash。保留适用的 `reference_groups` 和真实看图依据；导演稿与视频稿不为字段完整填图片用途。报告及源稿换字节、直接来源变化、看图证据或产品参考变化、授权撤回或检查 profile 变化后，旧依据不能继续作为本版通过。

## 首次自主采用

准确候选已登记且本轮完整自查完成后，首次采用执行：

```sh
python scripts/stage_context_cli.py accept-artifact \
  --project '<业务项目绝对路径>' \
  --artifact-id 'DIR-R01' \
  --self-check-json '00_项目管理/导演R01自查.json'
```

`--self-check-json` 接受项目内相对路径或绝对路径。命令校验授权、准确候选、自查及直接上游，再沿现有采用事务写入；缺项保持候选。它不把 agent 验收写成人工确认，不上传或生成，也不自动改变阶段流转。已有正式版本的返修继续走修订控制入口，不能用首次采用覆盖。兼容人工流程继续用 `adopt-package --approval-ref`，编导走[网站决定](review-workbench-operations.md#编导网站决定与采用)或本页的[对话确认](#编导对话确认)。

## 自主修订

自主返修仍用 `scripts.core.revision_control` 的 `open_change(..., authorization_id=<已登记流程授权ID>)` 保留原目标、依赖冻结及失效传播，用 `record_candidate(...)` 登记新修订和准确依赖。之后使用现有 `accept-artifact` 附加 `--change-id`，在同一次调用中验收本版自查、提交原变更案并回读采用状态；底层继续复用 `confirm_agent_candidate` 和 `commit_change`。报告须针对新候选重新绑定；提交前仍核授权、报告、来源和实际字节。该分支不调用 `confirm_impact`／`confirm_candidate` 生成逐稿人审；既有人审变更仍沿原 API，也不绕过原有生成段增删、重排、覆盖与正文替换对账。

### 单段采用与写前预检

首次采用和自主修订都可在同一命令追加 `--preflight-json`，消费已落盘的准确单段 frozen JSON：

```sh
python scripts/stage_context_cli.py accept-artifact \
  --project '<业务项目绝对路径>' --artifact-id '<准确候选 ID>' \
  --self-check-json '<本版自查 JSON>' --change-id '<原活动变更案 ID>' \
  --preflight-json '<单段 frozen JSON>'
```

首次采用省略 `--change-id`。该入口先核单段计划包含本次产物的准确 ID、revision、sha256 来源，再办理采用与任务选择登记，回读 manifest 和 project_state 后，调用现有正式 `validate_submission(..., formal_delivery=True, preflight=True)`。修订提交在原事务内选中新候选，即使旧 ID 已被上游失效同步移出任务状态，也不再手动补 state；不把 manifest 中其他 current 项自动选进来。

单段预检失败时返回非零及 `adoption_completed: true`，保留已经成立的采用事实，修正原单段计划后沿同一命令续作。对准确已采用的自主验收产物，入口核原自查、授权、字节和上游，必要时只补选该 ID；已有完整登记时不再写事务，不重造人审或重复采用。不同报告、失效依据和不匹配变更案不能借此恢复。独立 `canvas_readback.py --preflight` 仍只读，不隐式补状态。预检通过不代表远端保存、刷新、人脸认证或生成授权，保存后真实回读及结果未知先查原任务的控制继续有效。

导演返修提交时，程序对照新旧导演稿与已采用补图的明确镜号。前序场景、该图所用场景与共享导演条件一致时，继续使用原补图和原自查回执。镜号或场景变化、共享条件变化、依据缺失时，按现有依赖复核受影响产物。
