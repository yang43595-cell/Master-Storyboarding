---
name: asset-image-assistant
description: 在叙事包正式采用后直接按已登记 AST 需求核对和复用已有图片，只为缺口制作角色、场景、道具、产品与风格资产。首批与导演规划并行，视觉质量由用户终审。
---

# 图片资产

开始本职任务前读取[共用三层规范](../_shared/skill-layers.md)。本 Skill 适用边界：按实际资产库/项目 Web 或普通阶段入口读取本职指南；Web 由 library_runtime.py 组装，不强套 prepare。只制作获准资产，不决定故事或拍法。

图片生产按[图片生产批次](../../docs/rules/execution.md#图片生产批次)确定执行者，每批最多 8 张目标图。涉及界面操作的批次由主对话负责输入、提示词、生成、实看、修正及回执，其他批次沿既有委派规则。

## 入口模式

- 短剧项目默认走普通对话入口。协调方正式采用准确编导稿后，当轮把同版叙事包直接交本 Skill，按其中人物造型、地点、道具及所需状态和用途列全需求，启动可制作项，可与导演规划并行。
- 本轮明确沿用已接入的项目 Web 任务包时，按 [modules/project-web-mode.md](modules/project-web-mode.md) 读取同一份已采用叙事包并回填项目。
- 用户明确要求跨项目独立资产库时：执行 [modules/library-mode.md](modules/library-mode.md)。这是本 Skill 的独立入口，不是另一个 Skill；资产库需求代替叙事包 AST 输入，不能伪造项目事实。按 Web 受限读取合同读取共享红线/分类指南及动态知识，并执行本 Skill 的语言审查和确认门禁。

先选资料入口，二者不叠加：

- **普通对话**：盘点或复用按[普通准备与选知](../_shared/knowledge-access.md#普通阶段准备与选知)。制作图片时，委派、并行或跨执行者交接的批次按[批次隔离](../_shared/knowledge-access.md#图片批次的隔离准备)使用同一批次的 prepare／阅读登记／ensure；主对话直做批次沿用本阶段 `task_basis`，不传 `--context-id`，但仍冻结 `image_targets` 并写回现有回执。请求用 `image_targets` 列准确输出，用 `image_output_kinds` 为本批每项注明实际出图类型，准备包只带命中的人物模板和灰模。读取启动包、适用指南及方法快照，同批返修沿用有效原文与记录。
- **两种 Web 入口**：按 [modules/web-input-contract.md](modules/web-input-contract.md) 读取本次 task-package；不执行 stage prepare / ensure-prepared，不额外登记普通对话的阅读声明。

两种入口均须读到 `PROJECT_RULES.md`、`../_shared/draft-lifecycle.md` 等适用共享规范（含 `../_shared/prompt-language.md`）、`modules/asset-method.md`、`references/00-asset-production-workflow.md`、`references/00a-asset-complexity-routing-guide.md`、`references/05-asset-manifest-handoff-guide.md` 与 `../../schemas/visual-asset-manifest.schema.json`；Web 的单项写词裁剪以受限读取合同为准。

分类指南只读本轮适用部分：`references/01-style-master-guide.md`（含整片色卡）、`references/02-character-master-guide.md`、`references/02a-body-character-master-guide.md`、`references/03-scene-master-guide.md`、`references/04-prop-product-master-guide.md`。产出前读取[图片连续性的共用检查](../storyboard-director/references/image-continuity-hard-rules.md#共用检查)及本类指南。制作或引用 1×4 时按实际用途读取[身份板构图](references/02-character-master-guide.md#五人物身份资产板)或[已有身份板的引用](references/02-character-master-guide.md#唯一面部来源)。场景把本时段可见的现场状态写进实际生图提示词，同轮实看这些细节和剧情动线；具体取舍见[场景母图提示词](references/03-scene-master-guide.md#十一image2-提示词)。场景读取[背景母版](references/03-scene-master-guide.md#背景母版与单变量派生)和[多机位覆盖](references/03-scene-master-guide.md#多角度场景参考组)的适用范围；写提示词必读并遵守 `../voiceover-to-video-script/references/prompt-language-hard-rules.md`；涉及灯光风险时核对 `../storyboard-planner/references/lighting-prompt-standard.md` 的对应条目。按 `modules/asset-method.md` 开头清单核对适用资料齐全，不因引用其他阶段文件扩大职责。

## 本轮执行 SOP

项目资产图沿[项目已定审核通道](../../PROJECT_RULES.md#网站审核到画布的统一入口)人审。普通对话先列全本轮需求，标明可复用、需制作、无需独立图及确有缺口的项目。本轮需人审的源图按 `AST@输出` 登记在现有 `00_项目管理/project_request.json` 的 `asset_review_targets`，随有效需求调整同步更新。列完即开工，无须用户另批清单。执行批次完成后收回执，齐备时按[整轮交审](references/05-asset-manifest-handoff-guide.md#普通对话整轮交审)一次展示准确图片和用途。用户明确要求提前看某批时可单独送审，其余目标继续。返修仅重新送受影响的成图，已有效确认且用途不变的参考沿用原依据。独立库及兼容项目沿原确认路径。

按顺序完成适用步骤；核对对象和结论写回本模式现有制作项、候选/审核备注与清单，不另建执行表。只有本轮实际执行并有对应结果的步骤才可报完成。

阶段负责人先按角色、场景、服装、状态及实际参考依赖分组；为各批分配不重叠的目标和候选路径。有各自参考的图在平台并发及已有授权内同时启动。确需共同母图的派生项在该图可作参考后接续，其他项继续。Web 沿本次 task-package 受限读取，一图一独立会话的现有制作项可保持；8 张不是最低数量，也不改变网页并发 3 项或其他原有数量限制。图片执行者不写整份 manifest、当前清单或阶段状态；交回本批准确结果后由阶段负责人统一登记、送审和采用。

普通对话图片批次由同一个实际执行者跟到成图和候选记录落盘。协调方为内置 ImageGen 批次在 prepare 或首次 handoff 绑定请求及回执，直接将结果中的 `spawn_agent` 对象作为宿主同名工具的完整实参，保留 `fork_turns: "none"`。批次已有执行者时沿用原任务。涉及界面操作的批次由主对话执行。委派、并行或跨执行者交接的批次用现有 `--context-id` 和 `--request-json` 在 `prepare` 或首次阶段 `handoff` 附上 `--batch-receipt`，固定本批准确 `AST@输出` 和候选记录路径；主对话直做批次不传 `--context-id`／`--batch-receipt`，直接写入并核对现有回执，但仍须冻结 `image_targets`。候选记录顶层沿用本批 `context_id`（如本批有）和 `request_sha256`，只归入实际按该请求生成的结果；主对话批次以本阶段请求哈希为准，不另造批次 `context_id`。委派批次完成后只回传结论、未完项和回执路径，中间日志和图留在原处。阶段负责人收到终态通知后，以项目和批次 ID 运行 `handoff --to-skill asset-image-assistant --receive-batch`，一次读取紧凑结果。需要确认资产已经正式可用时，仍按受影响目标使用 `asset_refs.py --require-final-image`。等待和回传按[对应小节](../../docs/rules/execution.md#等待与工具回传)，遇到报错按[原处续作](../../docs/rules/execution.md#工具报错与原处续作)。计量时只读实际适用的[检查点](../../docs/development/token-budget.md#普通准备的计量检查点)和[资产协调窗口](../../docs/development/token-budget.md#资产批次协调)。

1. **锁定入口与范围。** 项目模式核对有效叙事包、AST 和已登记用户调整；独立库核对本次库需求，不创建项目 AST。按上节完成本入口的资料准备与实际阅读。缺真实输入或规范只停止受影响项，返回本入口补齐。
2. **先复用合用图。** 按准确 AST/output 定位本项目已确认输出及本轮已登记的可用原图，实看身份与造型、空间、道具状态及本次用途，核对原件字节和确认范围。原确认覆盖图的准确版本、字节及当前项目用途时直接沿用。跨项目选图或用途变化时，按本项目已定通道确认准确原图及新用途。头照固定身份，服装按实际依赖核对。无需独立图、取消项与待定项不进入制作；已锁定事实需修改时，只把受影响条目交回上游。
3. **只为缺口制作。** 现有图覆盖后，仅对仍需制作且缺合用图的输出选择单图、身份板、场景视角或必要状态，依据实际参考写提示词。从项目开工记录核对工具、模型、额度及输出范围，沿已有授权执行。明确复用的项缺合用来源时，先处理该项来源或沿现有入口登记需求调整。独立库沿自身授权入口。
4. **产出并做技术检查。** 图片执行者在授权范围内生成/编辑或接入真实导入文件，独占写入分配的候选路径，保留准确身份、路径、来源、实际参考、尺寸/格式及结果。红帧固定人物带脸资产（含后续补图）取得原图即按[生图入口](../_shared/redframe-operations.md#生图)办理当前源卡认证，已有人物或旧版图的通过记录不得替代新图认证。技术失败或结果未知不自动重提；成图有视觉问题时保留原图和实际观察，按[总规则](../../PROJECT_RULES.md#持续制作授权不逐批重复询问)交用户决定如何重生或按什么用途接受。用户确定重生方向后沿正确参考修正并另存版本。人物四格图按[人物 1×4 拼版](../../PROJECT_RULES.md#人物-14-拼版)直接拼接两张已确认源图。拼版成品作为新候选交用户确认。
5. **展示最终文件。** 在展示之前完成所需格式转换和尺寸处理。展示准确的候选版本、用途和原件，让用户本人决定通过或退回。
6. **交审、确认与收尾。** 图片执行者在现有回执或 Web 制作项记录准确原件、AST/output、修订/字节、实际参考与用途、用户退回意见。阶段负责人按实际回执核覆盖与跨批关系。普通对话取得完整交审清单即发消息展示整轮准确图片和用途。启用性能复盘时，展示后再从原日志取数；若以最终答复交审，待本轮结束后取数，生产预算检查仍在原检查点执行。用户明确说“全部通过”后按此次展示一次登记，完成已就绪的人物拼版并展示新成品。资产齐备即接续项目已授权的下一阶段；部分通过时只补受影响项。网站通道按[送审](../../docs/development/review-workbench-operations.md#资产图送审)及[决定与交接](../../docs/development/review-workbench-operations.md#资产网站决定与交接)办理。沿用项目 Web 任务包且采用对话确认时，使用[项目 Web 的确认接入](modules/project-web-mode.md#资产对话确认)。真实确认后备份并交付已确认输出，候选和待补项保留在原回执。红帧素材卡及生成节点落位时按[生图 SOP](../_shared/redframe-operations.md#生图)归位，整批完成后复看布局。

## 专业方法适用条件

人物面容替换或年龄版本复用时，执行[身份替换与年龄版本检查](modules/asset-method.md#身份替换与年龄版本)。它检查参考职责，不自行扩大图片需求。

`IMAGE-CONTINUITY-METHOD` 为本 Skill 必读。分类方法按制作内容选择：风格/色卡=style，人类/人形角色及服装/人物1×4=character，动物/非人形/PET 角色外观=body，场景=scene，道具/产品=object。普通对话 prepare 请求中由执行者据当前 AST 设置 `asset_method_scopes` 对应键为 true，覆盖本轮全部实际类别；多类别同时设置，不凭任务关键词猜测。仅明确选定类别时排除未选分类指南，条件未知仍保守保留。新增适用本地合同在 prepare 请求的 `local_references` 列出仓库相对路径，只增加、不删除固有标准；重建 prepare 后再用 required-reference 核对，不现场补登记哈希。已明确只有人类/人形时同时写 `body: false`，只有非人形时写 `body: true` 且不启用 character，混合角色两键均为 true；旧请求仅有 `character: true` 时兼容读取两类指南。范围未明确或全为 false 不用于排除规范。Web 由当前制作项与 AST 类别自动计算相同条件。body 的专项正文由本地非人形指南提供，并保留共同连续性方法，不新增专业方法绑定；其他类别逐项核对适用绑定方法完整加载，缺失时不得只靠本地模板产出。

本轮明确制作、返修或核对造物者抛光面膜的真人上脸效果时，在当前请求设 `creator_mask_face_effect: true`，按 knowledge-profile 加载 `VGEN-CREATOR-POLISHING-MASK-USE` 全文，并读取该方法指定的现有真人上脸模板；把模板实际路径加入当前 `source_paths`，使准备依据包含其版本。普通资产、碗内搅拌和产品展示不设置此条件；这不新增效果图需求或阶段授权。现有Web仅登记效果需求而未建立对应制作入口时，不把通用大头照job当作上脸任务；仅已明确由普通会话执行的本项按原prepare流程准备，不宣称网页会自动传入该条件。

## Web 与普通对话的资料入口

资料准备只按上节所选入口执行；Web 替代的是准备方式，输入事实、适用方法、授权和确认门槛仍须满足。

确认记录和文件存在只供机械筛选。普通对话可在同一有效上下文、同一用途复用已看过的对应输出；新的执行者或用途/版本变化必须实际查看该 AST/output。网页每次调用都是新的视觉上下文，按 Web 合同查看本次全部参考图，不能把旧调用或“已确认”当作已看图。

## 输入与启动

- 制作入口是已正式采用的叙事包内结构化 AST 需求。canonical JSON（schema_version=2.0 且省略 human_readable_path）从已正式采用的 narrative.package 内 asset_requirements 读取，不要求独立 narrative.assets 或 Markdown；保留 human_readable_path 的历史包才沿原双文件及适用的同版独立资产表检查，不得删除该字段绕过差异。当前有效状态统一由项目核心产物读取代码核验登记与采用凭据；候选文件正文可保留采用前原文，不以正文状态重复裁决。历史 MD 与 JSON 的一致性在上游审查/采用时完成。网页读取同一有效来源中的必要背景和当前结构化条目，不重新从编导稿提取或解析另一份 AST。旧版未拆表项目兼容原已确认包；仅历史合同要求独立表而缺表时返回上游。
- 从已采用叙事包的 `asset_requirements` 与已登记的[用户调整](../../docs/rules/visual-production.md#用户调整资产图需求)取得本轮有效 AST 白名单。未经授权或尚未登记的表外项停止并报告；不得自行创建、改名或扩写 AST，不把用户新增需求伪称为原叙事包事实。
- 读取同版 AST 的处理结论与依据，按上文顺序核对可复用输出和实际缺口。首批使用叙事包已明确的出现范围、状态和用途。历史无字段的确认包保留兼容，返修时由上游补齐。
- 用户主动提供已有选用包 `selection.json` 时，只读取其中已选图片及来源，按上文第 2 步落实当前项目用途与确认。
- 广告产品及明确要求还原的实物必须读取实际参考 manifest/brief 并查看所需图片；参考不足时阻塞该 AST。普通剧情物品按共享 product-reference 的例外执行，无参考不单独构成阻塞，候选外观由用户确认。

## 执行

流程统一按上文六步 SOP。实现深度按 `references/00a-asset-complexity-routing-guide.md` 选择：普通一次配角可用单张综合参考；环境默认四图，已有充分覆盖时可少补并保留相机变化余量。复杂度不产生新资产需求。

每个 AST 在 `visual-asset-manifest.json` 中登记：类别、名称、状态、来源 revision、事件范围、可选镜头范围、交互、展示面、朝向、所需状态、必须继承、不可继承、真实参考、候选和确认资产。不可继承内容用于阻止身份资产继承剧情布光、表情、动作、临时污损或特定镜头构图等错误状态。

生成授权核对见 SOP 第 3 步及 [持续制作授权](../../PROJECT_RULES.md#持续制作授权不逐批重复询问)；工具及具体模型（平台不公开时记录真实披露）可由本项目已有授权覆盖，首次生图可沿已有授权直接进行，成图有问题时由用户决定重生方向或可用范围；决定重生后无须重复询问额度。

本轮选用红帧生图时，按[红帧生图 SOP](../_shared/redframe-operations.md#生图)执行素材卡上传、当场排版、可视接线、正文核对与提交。

## 候选与确认

- 图片质量由用户本人验收。通过只覆盖此次展示的文件和用途。
- 按 [候选交付](modules/asset-method.md#候选交付) 记录路径、格式、尺寸、AST 和参考来源。退回意见随该版保留；返修另存候选并重新展示。
- 交用户审核时只提供候选文件夹绝对路径、准确文件名、对应 AST 和需要判断的视觉事项，不嵌入图片载荷。
- 用户明确确认具体候选后，才登记 `confirmed_asset` 和确认引用；旧确认版本保留为过期或被替代，不覆盖历史文件。

## 后续交接

已确认输出按准确 AST/output 交给实际依赖它的下游。导演随后发现初批未覆盖的镜头需求时，按[视觉生产与资产调整](../../docs/rules/visual-production.md)仅核对受影响的镜号、用途和输出，先复用已确认的合用图，再按实际缺口补充。复杂动作、精准表情和空间关系仍由 `storyboard-director` 按已采用导演稿判断。本 Skill 只办理本职资产交接。

## 硬禁区

- 不生产当前有效白名单之外的资产，不自行修改剧情、对白、事件、AST 需求或导演镜头；用户明确调整资产需求按项目授权分支登记，不改已确认编导正文。
- 不把下载图、试验图、拼图、候选或技术检查通过的图片冒充确认母版。
- 不决定下一阶段、不调用其他 Skill、不写视频模型、生成分段或视频提示词。

用户明确淘汰某张图时，执行 [用户明确淘汰资产图](../../docs/rules/visual-production.md#用户明确淘汰资产图)。

用户明确新增、删除和恢复资产需求时，执行 [用户调整资产图需求](../../docs/rules/visual-production.md#用户调整资产图需求)。该授权分支允许按已核对出现位置加入需求表，不适用“表外一律停止”的自动推断限制；未授权的新物件仍须停止。下游读取 asset_requirement_decisions，取消项不是缺图，新增项须沿用其出现范围。


## 视觉生产衔接细则

本阶段涉及资产、按需分镜或场景状态时，读取 [视觉生产与资产调整](../../docs/rules/visual-production.md)，仅执行本职及已授权分支。
