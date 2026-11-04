# 审核到画布入口

本页供主对话、各阶段执行者及审核站开发者交接。业务规则唯一正文是 [PROJECT_RULES.md 的统一入口](../../PROJECT_RULES.md#网站审核到画布的统一入口)；本页只定位操作、格式和接线任务，不建立新 Skill、多 agent 运行时或平行业务格式。

## 启动方式

向当前项目的协调 agent 提供业务项目路径、准确原始资料与本轮目标，并说明采用“审核到画布入口”。目标画布和本轮生图工具／模型／费用已有明确授权时沿用；确有缺项时只补问该项，不让用户填写内部 JSON。

例如：“按审核到画布入口推进这个业务项目；审核站用已确认工作台，编导稿和资产图在网站审核，其他阶段自行检查；最终保存到本轮指定画布，由我生成视频。”

这是一份执行入口说明，不是已实现的一键运行命令。开始真实任务前先核下文接线现状；不把 API 规划当作可调用接口。

## 阶段与读取入口

| 顺序 | 执行者及格式入口 | 检查与交接 |
| --- | --- | --- |
| 1 | [编导](../../skills/creative-director/SKILL.md)，[narrative 2.0 schema](../../schemas/narrative-package.schema.json) | 同一叙事 JSON 保存故事、对白、来源与 AST 需求；格式及内容自查后送网站，按标注返修，准确整稿通过后采用 |
| 2 | [导演](../../skills/storyboard-planner/SKILL.md)，[storyboard 4.0 schema](../../schemas/storyboard-package.schema.json)；[资产](../../skills/asset-image-assistant/SKILL.md)，[manifest 2.0 schema](../../schemas/visual-asset-manifest.schema.json) | 均承接有效编导输入，可按既有依赖并行。导演自行完成检查；资产候选由 agent 实看后送网站，按准确 AST、输出文件和用途确认，不把已确认输出展示器当成候选送审入口 |
| 3 | [按需分镜](../../skills/storyboard-director/SKILL.md)，[补图请求 schema](../../schemas/visual-supplement-request.schema.json) | 先盘点已核素材；复用充分则不出新图。需要补图时在已有生成授权范围内制作、实看、修正并验收准确镜号用途，不返回网站逐图停等 |
| 4 | [视频提示词](../../skills/voiceover-to-video-script/SKILL.md)，[当前正文模板](../../skills/voiceover-to-video-script/templates/video-script-template.md) | 同一业务 JSON 保存完整正文、参数、实际素材顺序与职责；运行现有来源、分段、引用检查，完成动作链、内容及全片接续自查；不恢复退役 production_plan |
| 5 | 视频 Skill 的 [画布交付](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md) 与 [快照核对](delivery-snapshot-checks.md) | 所需人审和自主检查均通过且正式采用接线有效后，再写指定画布、排版、保存、刷新回读并查看截图。交付草稿后停止，由用户点击生成 |

图片二进制与参考原件按原目录、文件和输出 ID 管理，JSON 保存其实际引用；网站阅读视图只是同版投影，不维护第二份可编辑正文。白模、声音等可选分支沿原 Skill 条件进入，不因本入口而默认开启。

## 网站往返

站点由用户确认为 [审核工作台](https://review.yangyangyang.click/workbench/)。每轮先读取该项目和阶段的真实当前版本，再发送同一业务 JSON 或准确资产候选及必要媒体。发布成功须有实际回执并能读回准确版本；不能以本地编译成功、HTTP 成功码或同名文件代替。

拉取已提交意见时，先处理总体方向，再处理逐处标注、图片批注和参考附图；保留意见对应的稳定正文／AST／输出身份。针对当前有效版本返修，重跑修改及直接影响的检查后发布新修订；新修订等待自己的通过回执。不可变旧记录保留，不自动关闭遗漏意见；旧版本、上游已变或仅局部通过的记录不能批准整稿。

2026-11-05 用户提供正式站 Agent 写入 API 说明，现网版本标为 `20261105-agent-review-write-rc1`；三路入口及已知请求字段见下节。它与 connector 是不同能力：connector 的 `read_case_reviews` 仅声明可读已注册案例 `01–07` 的已提交审核、`contentCurrent` 准确回执与来源有效性，并按 `contentFeedbackPage` 继续分页；`read_review_attachment` 只读已有附件。不能把这些旧案例读取能力泛化为任意项目发布能力；本轮未指定具体私人案例，不读取旧案例充当验收。

接口说明和鉴权探测不等于发布与审核往返通过。若本轮有明确网页操作授权且真实会话可访问，可按实际支持能力完成有限的网页操作并记录结果；界面操作统一由实际设置为 GPT-6.1 Terra 的执行者负责。遇登录或缺合法通道时报告具体缺口，不以历史插件、猜测接口或手填回执替代。

## 已提供的 Agent 写入 API

正式基址为 `https://yangyangyang.click`，鉴权使用 `Authorization: Bearer <token>`，JSON POST 另须 `Content-Type: application/json`；不携带 Cookie 或 Origin。凭据由当前执行环境提供，不放进源码、聊天、业务 JSON 或验收证据；本页不保存本机凭据路径。

| 方法与路径 | 已提供的字段与范围 | 本轮验证边界 |
| --- | --- | --- |
| `POST /api/agent/review/create-case` | JSON 为 `name`、`operationId`；回包含 `ok` 与 `case.id/name/projectId/createdAt` | 已按用户授权创建一个明确标记的独立合成案例 `13`，未操作已有业务案例 |
| `POST /api/agent/review/publish` | JSON 为 `caseId`、`stage`、`name`、`text`、`operationId`，`purpose` 可选；成功字段有 `projectId/scope/caseId/stage/version/headGeneration/url/assetId` | 合成 R01 发布为第 1 代；原请求精确重试回同一回执且未增代；R02 新 UUID 与新正文发布为第 2 代。用户登录 Edge 后已回读 R02 故事原文及完整 JSON 附录，内容一致，仍待审核 |
| `GET /api/agent/review/status?caseId=<准确案例ID>&stage=script` | 实测返回 `case`、`stage`、`head`、`expectations`、`sourceScriptVersion`，不含意见或批准回执 | 已核新案例初始 head 为空、R01／重复请求仍第 1 代、R02 第 2 代且版本准确匹配发布回执；没有以 head 代替正文读取或批准 |

当前 MVP 仅文本，`stage` 为 `script | director | prompts`，非 `script` 要求已有编导稿 head；`name` 限 `.md` 或 `.txt`，`text` 为 UTF-8 且不超过 100KB。`operationId` 是客户端 UUID，本轮已实测同内容／同标识的发布重试幂等，未进行服务重启或并发冲突验收。统一 SOP 仍只送编导和资产人审，不因接口支持导演／提示词文本而新增其网站审核。

发布请求白名单仅 `caseId/stage/name/text/operationId/purpose`，不能向它发送 `expectedHeadVersion`、`expectedHeadGeneration` 等额外字段；配套实现内部从预读状态的 `expectations` 取得版本保护依据，不能把 status 返回字段写成客户端可提交参数。顺序发布和精确重试已核，内部并发版本保护仍待冲突验收。

上述 `name/text` 是传输字段，不取代业务 JSON 权威。编导正文须从准确同版 JSON 形成阅读投影并保留完整来源和稳定身份，再核发布回读；不为满足文件扩展名维护第二份可编辑稿。当前 MVP 不支持资产图片或业务 JSON 原包上传，status 也不提供意见／批准回执；完整人审消费仍缺独立的已核读取合同，不能把 head 或成功发布当成批准。README 与用户说明标记了上述现网 release，配套全文仍带“本地 MVP／不上现网”范围；本地源码与现网部署的准确对应关系未验，不把文档或源码存在说成线上整合通过。

## 编导文本送审命令

[review_text_cli.py](../../scripts/review_text_cli.py) 只开放编导 `script` 文本，复用现有完整 Markdown 导出。先取得该项目及送审范围的真实授权；指定已有案例时直接使用该 ID，没有案例且本轮已授权创建时才调用 `create-case`。凭据参数只给本机文件路径，不把内容放进命令或稿件。

先校验并生成只读阅读副本；该动作不读密钥、不联网：

```sh
python scripts/review_text_cli.py prepare \
  --project '<业务项目绝对路径>' \
  --narrative '02_编导/R01_叙事包.json' \
  --output '02_编导/R01_审核阅读稿.md'
```

需要新建案例时：

```sh
python scripts/review_text_cli.py create-case \
  --project '<业务项目绝对路径>' --name '<本轮获准案例名>' \
  --operation-id '<本次创建的UUID>' --token-file '<本机凭据文件>' \
  --receipt '00_项目管理/审核发送/创建案例.json'
```

发送准确同版投影并读当前版本：

```sh
python scripts/review_text_cli.py publish \
  --project '<业务项目绝对路径>' --case-id '<真实案例ID>' \
  --narrative '02_编导/R01_叙事包.json' --markdown '02_编导/R01_审核阅读稿.md' \
  --operation-id '<本次发布的UUID>' --token-file '<本机凭据文件>' \
  --receipt '00_项目管理/审核发送/R01_发布.json'

python scripts/review_text_cli.py status \
  --project '<业务项目绝对路径>' --case-id '<真实案例ID>' \
  --token-file '<本机凭据文件>' --receipt '00_项目管理/审核发送/R01_版本回读.json'
```

发布会重跑现有来源检查并精确比较 JSON 重导出的 Markdown 字节；已失效、有未决问题、正文漂移或输出路径越出项目时拒绝。候选送审不要求先登记为 current；以上命令不改业务 JSON、manifest、人工确认或项目流转。回执包含准确源路径／修订／hash、投影 hash、请求标识和原始返回；回执独占新建，不覆盖已有记录。

同一次操作重试必须保持相同 `operationId`、案例、文件名、正文及用途，并指定新的本地回执路径；改内容、文件名或用途则为新操作。超时结果未知时先核原操作和当前状态，不自动换 UUID 重发。返修另存新 JSON 修订并重新导出；不要修改已发布 Markdown 反向覆盖 JSON。程序检查不能代替实际读稿和网页正文核对，`status` 也不代替意见／批准读取。

本次独立案例、实际回执与 Edge 网页回读见[文本送审现网验收](../reviews/2026-11-05-review-text-api-smoke.md)。

本轮实际打开发布回包的 URL 时，外层地址保留了案例与版本参数，内嵌工作台仍先显示项目列表；须从实际可见的案例 `13` 入口进入后才能读取正文。不要把带参数的 URL 或项目总览当作准确正文已打开；该深链定位限制尚未修改服务端。网页回读已核完整内容，不表示批注／批准回拉已接通。

## 审核站 API 对接要求

以下是完整 SOP 仍需双方核对的要求，不是对上节 MVP 已全部实现的声明。已提供的入口与字段沿上节；完整响应、媒体传输、版本保护和批准语义以实际补齐的服务端合同为准，不从旧 v78 或示例补猜。

1. **项目与当前版本读取。** 能定位任意已授权业务项目，区分编导稿和资产图，返回当前产物版本、准确来源及其是否失效；不强行塞入旧案例 01–06 枚举，也不把广告对标资料强加给原创故事。
2. **候选及媒体上传。** 编导保留原 JSON 全字段和稳定正文／对白 ID；资产支持尚未确认的候选图片，关联真实 AST、输出、文件身份及用途，便于网页审图。图片上传成功不等于确认，不先伪填 confirmed_outputs 才能送审。
3. **带版本保护的发布及回读。** 更新绑定读取到的当前版本，冲突时重新读取，不覆盖他人新稿；重试有可识别的提交身份，结果未知先查询原提交，防止重复创建。回读能核对发布版本及实际媒体。
4. **准确意见与批准回执。** 能读取已提交的总体意见、逐处标注、图片意见与附图，并完成分页；回执包含批准人、项目、阶段、准确产物版本、范围、决定和来源有效性。空意见或未提交浏览器草稿不等于通过。
5. **人工审批与后续授权分开表达。** 网站回执只证明编导／资产的实际人工审批；用户对后续自主检查与保存画布的授权另保留准确依据，不由网站虚构导演、分镜或提示词已经人工审阅。

优先复用审核站自身项目、版本、意见和权限实现；不为本项目建设第二套审核数据库、重复稿件或通用调度系统。

## 本地流程授权与自主采用

本地自主验收沿既有产物清单和项目状态记录，不创建逐稿 `user_confirmations`，不将 `user_approved` 改成 true。当前开放范围为 `storyboard-planner`、`storyboard-director`、`voiceover-to-video-script`；编导和资产图仍走真实人审，可选声音及白模不因本入口启用。

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

生产执行者完成本职检查后，复用 `scripts.core.artifacts.register_workspace_artifact()` 登记准确候选；采用命令不代登记候选，不猜文件或修订。自查保存在项目内同一份 JSON，使用 `scripts.core.agent_acceptance.acceptance_binding(project, entry)` 返回的完整实际绑定，包括 `artifact`、`upstreams` 和该产物适用的附加绑定；`review_profile_sha256` 取目标 Skill 当前 `review-profile.json` 的原字节 SHA256。报告必须完整覆盖该 profile 的 `production_artifact_checks`：每项有真实观察与正文／图片定位；不适用项使用 `not_applicable` 并给出理由，不能用不适用掩盖未检查或失败。核心检查必须通过，不可标为不适用；准确 ID 以 [agent_acceptance.py 的 CORE_CHECKS](../../scripts/core/agent_acceptance.py) 为准。源稿仍为 `blocked`／`rejected`、尚有未闭合 `open_questions` 或生产阻塞时，不能用自报 `pass` 覆盖，仍保留候选。

以下仅展示字段形状和两种检查结果，不是完整自查报告，也不是任何业务通过证据。完整报告须补齐当前 profile 的全部检查 ID，并以实际绑定、观察及时间替换示例值，不照抄通过：

```json
{
  "schema_version": "1.0",
  "kind": "agent-self-check",
  "authorization_id": "FLOW-R01",
  "agent_id": "<实际生产执行者>",
  "checked_at": "<实际检查时间>",
  "scope": "artifact",
  "verdict": "pass",
  "artifact": {
    "artifact_id": "DIR-R01",
    "logical_id": "storyboard.package",
    "type": "storyboard.package",
    "revision": "R01",
    "path": "03_导演/R01.json",
    "sha256": "<acceptance_binding 返回的准确 SHA256>"
  },
  "upstreams": [
    {
      "artifact_id": "NARRATIVE-R01",
      "logical_id": "narrative.package",
      "type": "narrative.package",
      "revision": "R01",
      "path": "02_编导/R01.json",
      "sha256": "<acceptance_binding 返回的直接上游 SHA256>"
    }
  ],
  "review_profile_sha256": "<当前目标 Skill review-profile.json 原字节 SHA256>",
  "checks": [
    {
      "id": "PLAN-TRACE",
      "result": "pass",
      "observation": "<实际核对的来源、要求及落实结果>",
      "locator": "<准确镜号、正文位置及来源定位>"
    },
    {
      "id": "PLAN-GAPS",
      "result": "not_applicable",
      "observation": "<本轮已核事实>",
      "locator": "<本轮准确范围>",
      "reason": "<该项确实不适用的具体依据>"
    }
  ]
}
```

独立补图在同一报告的 `usage.source_shot_ids` 列出实际获准镜号；`acceptance_binding()` 还绑定 `quality_evidence_sha256` 与 `product_reference_sha256`，须原样带入，不只复制图片文件 hash。保留适用的 `reference_groups` 和真实看图依据；导演稿与视频稿不为字段完整填图片用途。报告及源稿换字节、直接来源变化、看图证据或产品参考变化、授权撤回或检查 profile 变化后，旧依据不能继续作为本版通过。

准确候选已登记且本轮完整自查完成后，首次采用执行：

```sh
python scripts/stage_context_cli.py accept-artifact \
  --project '<业务项目绝对路径>' \
  --artifact-id 'DIR-R01' \
  --self-check-json '00_项目管理/导演R01自查.json'
```

`--self-check-json` 接受项目内相对路径或绝对路径。命令校验授权、准确候选、自查及直接上游，再沿现有采用事务写入；缺项保持候选。它不把 agent 验收写成人工确认，不上传或生成，也不自动改变阶段流转。已有正式版本的返修继续走修订控制入口，不能用首次采用覆盖。`adopt-package --approval-ref` 保留原人工确认含义，不增加自主验收参数。

自主返修使用既有 `scripts.core.revision_control` 的 Python API：`open_change(..., authorization_id=<已登记流程授权ID>)` 保留原目标、依赖冻结及失效传播；`record_candidate(...)` 登记新修订和准确依赖后，调用 `confirm_agent_candidate(project, change_id=..., report_path=...)` 核本版完整自查，再调用 `commit_change(project, change_id=...)`。报告须针对新候选重新绑定；提交前会再次核授权、报告、来源和实际字节。该分支不调用 `confirm_impact`／`confirm_candidate` 生成逐稿人审；既有人审变更仍沿原 API。它没有新增命令行返修子命令，也不绕过原有生成段增删、重排、覆盖与正文替换对账。

## 接线现状与开发交接

核对日期为 2026-11-05，以下区分本地接线与实际远端结果；本轮已实测独立合成案例的文本发布，并重新打开其准确 R02 链接核对会话。

| 项目 | 本次已核事实 | 下一步与验收 |
| --- | --- | --- |
| 网站 | Chrome 与最初 Edge 的登录缺口已保留。用户随后完成 Edge 登录；实际 GPT-6.1 Terra 从可见案例 `13` 入口进入 R02，读取正文与完整附录。协调方核两段原文准确、JSON 全字段深度一致；页面显示“正在查看当前稿”“待审核”。未点击批准或生成 | 文本送达及内容核对已完成。发布深链仍先落总览，需选择实际案例；未取得正文新鲜截图，不以旧总览截图冒充。批注与批准另核 |
| 上传／回拉 API | 三路已由 [review_text_cli.py](../../scripts/review_text_cli.py) 接入编导文本。独立合成案例 `13` 已实测创建、R01 发布、精确重试不增代、R02 新版本及 status 回读，用户登录后网页正文核对通过；status 不含意见／批准，publish 不接收客户端 expectedHead 字段；connector 仍只读 `01–07` | 补并发版本保护、意见／批准读取合同和资产媒体路径。真实发布回执不等于批准；未操作已有业务案例，未做完整审核往返 |
| 旧适配 | [web_content.py](../../scripts/core/web_content.py) 只离线编译；v78 固定合同不是当前站点合同，旧资产路径拒绝未确认候选 | 新接口接真实源稿与候选媒体，不伪改资产状态，也不为本 SOP 额外开发导演／提示词网站审核 |
| 下游自主验收采用 | 本地增加 [流程授权与自查绑定](../../scripts/core/agent_acceptance.py) 及 [已登记候选首次采用](../../scripts/core/artifacts.py)，CLI 见上节；采用保留人工与 agent 两种依据 | 以合成项目检查授权、准确字节、全稿自查及来源失效；真实业务授权另行取得，编导／资产不可走此分支。返修仍走修订控制，不用首次采用覆盖 |
| 下游消费 | [阶段输入](../../scripts/stage_context_cli.py) 改为核有效采用依据；[图片用途](../../scripts/core/asset_refs.py) 与 [画布回读](../../scripts/canvas_readback.py) 沿各自入口检查准确引用、版本及用途 | 本地消费通过只证明所覆盖的合成路径；真实上游回执、实际图片观察及平台原生回读仍须分别核对，不改成人审，不以本地结果宣称远端交付 |
| 画布 | 已有两平台排版及刷新回读要求；旧 RHTV 操作工具、离线比较器不等于当前平台写入／采集器已实现 | 指定准确画布后，核其真实能力、保存结果、原生引用／参数和布局；缺字段就报缺口，不手填预期结果，不点生成 |

自主验收采用分支的最低验收：无授权不得采用；编导和资产仍需真实网站人审；agent 采用不得生成逐份人审事实；局部检查、旧修订、错 hash、失败或未知图片用途、失效上游均不得越过；首次采用与返修、补图引用和画布交付一致消费同一有效依据；候选试跑仍被隔离。复用现有相关测试验证，不另建工作流运行时。

本地实现和合成验证不代表整条自动生产链已可执行。能独立完成的授权登记、自查与本地采用按上节执行；真实网站送审、批准来源、上传和平台回读仍停在对应缺口处，不把远端未验写成通过。

## 画布排版文件

现行规则在 [素材与提交交付](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md)：小云雀和红帧分别说明按场景／片段排序、参考／草稿／已有结果分组、节点对齐与间距、避免遮挡、保存后刷新及全景／关键细节截图。未找到固定坐标、列宽或现成自动布局模板；正文模板用于提示词组织，不能当作画布布局模板。
