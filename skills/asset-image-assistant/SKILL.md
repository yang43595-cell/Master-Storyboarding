---
name: asset-image-assistant
description: 在叙事包确认后按已登记 AST 需求制作和核对角色、场景、道具、产品与风格图片资产，可与导演规划并行；未经授权的表外资产停止并报告，视觉质量由用户终审。
---

# 图片资产

开始本职任务前读取[共用三层规范](../_shared/skill-layers.md)。本 Skill 适用边界：按实际资产库/项目 Web 或普通阶段入口读取本职指南；Web 由 library_runtime.py 组装，不强套 prepare。只制作获准资产，不决定故事或拍法。

图片生产按[图片生产批次](../../docs/rules/execution.md#图片生产批次)确定执行者，每批最多 8 张目标图。涉及界面操作的批次由主对话负责输入、提示词、生成、实看、修正及回执，其他批次沿既有委派规则。

## 入口模式

- 短剧项目模式：执行下文叙事包、AST 白名单与项目资产清单流程。
- 经本地资产接口接收项目制作任务包时，同时执行 [modules/project-web-mode.md](modules/project-web-mode.md)，仍属于项目模式；由已采用叙事包的有效资产需求驱动制作项，按本轮审核入口确认准确成图后正式回填项目。
- 用户明确要求跨项目独立资产库时：执行 [modules/library-mode.md](modules/library-mode.md)。这是本 Skill 的独立入口，不是另一个 Skill；资产库需求代替叙事包 AST 输入，不能伪造项目事实。按 Web 受限读取合同读取共享红线/分类指南及动态知识，并执行本 Skill 的语言审查和确认门禁。

先选资料入口，二者不叠加：

- **普通对话**：盘点或复用按[普通准备与选知](../_shared/knowledge-access.md#普通阶段准备与选知)。制作图片时，委派、并行或跨执行者交接的批次按[批次隔离](../_shared/knowledge-access.md#图片批次的隔离准备)使用同一批次的 prepare／阅读登记／ensure；主对话直做批次沿用本阶段 `task_basis`，不传 `--context-id`，但仍冻结 `image_targets` 并写回现有回执。请求用 `image_targets` 列准确输出，用 `image_output_kinds` 为本批每项注明实际出图类型，准备包只带命中的人物模板和灰模。读取启动包、适用指南及方法快照，同批返修沿用有效原文与记录。
- **两种 Web 入口**：按 [modules/web-input-contract.md](modules/web-input-contract.md) 读取本次 task-package；不执行 stage prepare / ensure-prepared，不额外登记普通对话的阅读声明。

两种入口均须读到 `PROJECT_RULES.md`、`../_shared/draft-lifecycle.md` 等适用共享规范（含 `../_shared/prompt-language.md`）、`modules/asset-method.md`、`references/00-asset-production-workflow.md`、`references/00a-asset-complexity-routing-guide.md`、`references/05-asset-manifest-handoff-guide.md` 与 `../../schemas/visual-asset-manifest.schema.json`；Web 的单项写词裁剪以受限读取合同为准。

分类指南只读本轮适用部分：`references/01-style-master-guide.md`（含整片色卡）、`references/02-character-master-guide.md`、`references/02a-body-character-master-guide.md`、`references/03-scene-master-guide.md`、`references/04-prop-product-master-guide.md`。产出前读取[图片连续性的共用检查](../storyboard-director/references/image-continuity-hard-rules.md#共用检查)及本类指南。制作或引用 1×4 时按实际用途读取[身份板构图](references/02-character-master-guide.md#五人物身份资产板)或[已有身份板的引用](references/02-character-master-guide.md#唯一面部来源)。场景读取[背景母版](references/03-scene-master-guide.md#背景母版与单变量派生)和[多机位覆盖](references/03-scene-master-guide.md#多角度场景参考组)的适用范围；写提示词必读并遵守 `../voiceover-to-video-script/references/prompt-language-hard-rules.md`；涉及灯光风险时核对 `../storyboard-planner/references/lighting-prompt-standard.md` 的对应条目。按 `modules/asset-method.md` 开头清单核对适用资料齐全，不因引用其他阶段文件扩大职责。

## 本轮执行 SOP

项目资产图沿[项目已定审核通道](../../PROJECT_RULES.md#网站审核到画布的统一入口)人审。一次审阅列出准确成图和用途，可独立判断的图合在同批；能解锁下一项的前置图可先审。返修仅重新送受影响的成图，已有效确认且用途不变的参考沿用原依据。独立库及兼容项目沿原确认路径。

按顺序完成适用步骤；核对对象和结论写回本模式现有制作项、候选/审核备注与清单，不另建执行表。只有本轮实际执行并有对应结果的步骤才可报完成。

阶段负责人先按角色、场景、服装、状态及实际参考依赖分组；共同母图先确定，独立批次再并行，并为每批分配不重叠的目标和候选路径。Web 沿本次 task-package 受限读取，一图一独立会话的现有制作项可保持；8 张不是最低数量，也不改变网页并发 3 项或其他原有数量限制。图片执行者不写整份 manifest、当前清单或阶段状态；交回本批准确结果后由阶段负责人统一登记、送审和采用。跨批需合看时明确交给能读取相关图及来源的图片执行者，不由主协调方默认重新加载全部图片。

普通对话图片批次由同一个实际执行者跟到成图、实看和回执落盘。委派、并行或跨执行者交接的批次用现有 `--context-id` 和 `--request-json` 在 `prepare` 或首次阶段 `handoff` 附上 `--batch-receipt`，固定本批准确 `AST@输出` 和实看回执路径；主对话直做批次不传 `--context-id`／`--batch-receipt`，直接写入并核对现有回执，但仍须冻结 `image_targets`。实看回执顶层沿用本批 `context_id`（如本批有）和 `request_sha256`，只归入实际按该请求实看的结果；主对话批次以本阶段请求哈希为准，不另造批次 `context_id`。委派批次完成后只回传结论、未完项和回执路径，中间日志和图留在原处。阶段负责人收到终态通知后，以项目和批次 ID 运行 `handoff --to-skill asset-image-assistant --receive-batch`，一次读取紧凑结果。需要确认资产已经正式可用时，仍按受影响目标使用 `asset_refs.py --require-final-image`。等待和回传按[对应小节](../../docs/rules/execution.md#等待与工具回传)，遇到报错按[原处续作](../../docs/rules/execution.md#工具报错与原处续作)。计量时只读实际适用的[检查点](../../docs/development/token-budget.md#普通准备的计量检查点)和[资产协调窗口](../../docs/development/token-budget.md#资产批次协调)。

1. **锁定入口与范围。** 项目模式核对有效叙事包、AST 和已登记用户调整；独立库核对本次库需求，不创建项目 AST。按上节完成本入口的资料准备与实际阅读。缺真实输入或规范只停止受影响项，返回本入口补齐。
2. **盘点并实看参考。** 定位准确 AST/output 或库内文件，核对确认依据、实际图与当前用途。用户已选定且适合本次用途的旧服装图，沿用该原图和选择依据。头照确认只固定身份，已有服装方向可并行核对；需要新增服装图时按依赖制作。无需独立图、取消项与待定项不进入制作；修改已锁定的身份、服装要求或故事事实时，只把受影响的条目交回上游。
3. **确定实现并自查提示词。** 按类别及明确要求选择单图、身份板、场景视角或必要状态，核对继承/禁止项、实际参考、提示词与输出计划。从项目开工记录读取生图工具、模型、额度方式和准确输出范围。已有授权覆盖时，完成当版提示词自查后执行；范围外只暂停相应制作项。独立库沿自身授权入口。
4. **产出并做技术检查。** 图片执行者在授权范围内生成/编辑或接入真实导入文件，独占写入分配的候选路径，保留准确身份、路径、来源、实际参考、尺寸/格式及结果。技术失败或结果未知不自动重提；成图有视觉问题时保留原图和实际观察，按[总规则](../../PROJECT_RULES.md#持续制作授权不逐批重复询问)交用户决定如何重生或按什么用途接受。用户确定重生方向后沿正确参考修正并另存版本。人物四格图按[人物 1×4 拼版](../../PROJECT_RULES.md#人物-14-拼版)直接拼接两张已确认源图；来源确认不等于拼版结果已确认。
5. **实看本轮准确交付图。** 按 `asset-method` 对照明确要求、类别指南与原参考，记录准确版本、可见事实和结论。按人物指南只须备注的排版差异随图交审。影响身份、用途或连续性的问题按实际影响返修；用户已明确接受该版已列明的视觉偏差时，记录接受范围并沿用该版的实际观察与技术检查结果。
6. **交审、确认与收尾。** 图片执行者在现有回执或 Web 制作项记录准确原件、AST/output、修订/字节、实际参考与用途、实看结论和未满足项。阶段负责人按实际回执核覆盖与跨批关系，按本轮通道办理[网站送审](../../docs/development/review-workbench-operations.md#资产图送审)及[决定与交接](../../docs/development/review-workbench-operations.md#资产网站决定与交接)，或用[资产对话确认](modules/project-web-mode.md#资产对话确认)。真实确认后备份并导出已确认输出，按输出身份交给实际依赖它的下游。候选和待补项在原回执保留。红帧素材卡及生成节点落位时立即按[生图 SOP](../_shared/redframe-operations.md#生图)归位，整批完成后复看布局。

## 专业方法适用条件

人物面容替换或年龄版本复用时，执行[身份替换与年龄版本检查](modules/asset-method.md#身份替换与年龄版本)。它检查参考职责，不自行扩大图片需求。

`IMAGE-CONTINUITY-METHOD` 为本 Skill 必读。分类方法按制作内容选择：风格/色卡=style，人类/人形角色及服装/人物1×4=character，动物/非人形/PET 角色外观=body，场景=scene，道具/产品=object。普通对话 prepare 请求中由执行者据当前 AST 设置 `asset_method_scopes` 对应键为 true，覆盖本轮全部实际类别；多类别同时设置，不凭任务关键词猜测。仅明确选定类别时排除未选分类指南，条件未知仍保守保留。新增适用本地合同在 prepare 请求的 `local_references` 列出仓库相对路径，只增加、不删除固有标准；重建 prepare 后再用 required-reference 核对，不现场补登记哈希。已明确只有人类/人形时同时写 `body: false`，只有非人形时写 `body: true` 且不启用 character，混合角色两键均为 true；旧请求仅有 `character: true` 时兼容读取两类指南。范围未明确或全为 false 不用于排除规范。Web 由当前制作项与 AST 类别自动计算相同条件。body 的专项正文由本地非人形指南提供，并保留共同连续性方法，不新增专业方法绑定；其他类别逐项核对适用绑定方法完整加载，缺失时不得只靠本地模板产出。

本轮明确制作、返修或核对造物者抛光面膜的真人上脸效果时，在当前请求设 `creator_mask_face_effect: true`，按 knowledge-profile 加载 `VGEN-CREATOR-POLISHING-MASK-USE` 全文，并读取该方法指定的现有真人上脸模板；把模板实际路径加入当前 `source_paths`，使准备依据包含其版本。普通资产、碗内搅拌和产品展示不设置此条件；这不新增效果图需求或阶段授权。现有Web仅登记效果需求而未建立对应制作入口时，不把通用大头照job当作上脸任务；仅已明确由普通会话执行的本项按原prepare流程准备，不宣称网页会自动传入该条件。

## Web 与普通对话的资料入口

资料准备只按上节所选入口执行；Web 替代的是准备方式，输入事实、适用方法、授权和确认门槛仍须满足。

确认记录和文件存在只供机械筛选。普通对话可在同一有效上下文、同一用途复用已看过的对应输出；新的执行者或用途/版本变化必须实际查看该 AST/output。网页每次调用都是新的视觉上下文，按 Web 合同查看本次全部参考图，不能把旧调用或“已确认”当作已看图。

## 输入与启动

- 制作入口是已正式采用的叙事包内结构化 AST 需求。canonical JSON（schema_version=2.0 且省略 human_readable_path）从已正式采用的 narrative.package 内 asset_requirements 读取，不要求独立 narrative.assets 或 Markdown；保留 human_readable_path 的历史包才沿原双文件及适用的同版独立资产表检查，不得删除该字段绕过差异。当前有效状态统一由项目核心产物读取代码核验登记与采用凭据；候选文件正文可保留采用前原文，不以正文状态重复裁决。历史 MD 与 JSON 的一致性在上游审查/采用时完成。网页读取同一有效来源中的必要背景和当前结构化条目，不重新从编导稿提取或解析另一份 AST。旧版未拆表项目兼容原已确认包；仅历史合同要求独立表而缺表时返回上游。可与导演规划并行。
- 按 [用户调整资产图需求](../../docs/rules/visual-production.md#用户调整资产图需求)，由已确认资产表及用户明确、已核对出现位置并登记的调整建立本轮有效 AST 白名单。未经授权或尚未登记的表外项停止并报告；不得自行创建、改名或扩写 AST，不把用户新增需求伪称为原叙事包事实。
- 读取资产表的处理结论与判断依据：无需独立图和待定不进入制作；复用只选用现有素材；需要制作才准备新图。只检查判断是否缺失，不重新详细审查。历史无字段的确认包保留兼容，返修时由上游补齐。
- 用户提供视觉资产库选用包 `selection.json` 时，先读取其中图片、来源与库内确认记录，再按当前 AST 逐项核对适用范围。库内确认不等于当前项目确认；只把匹配项登记为当前项目参考或候选，正式确认仍走本 Skill 原有清单流程。本地资产库命令见[执行说明](../../docs/development/visual-asset-library.md)；独立收藏和照片转通用资产使用本 Skill 的独立资产库模式，不创建项目 AST，也不触发短剧生产阶段。
- 广告产品及明确要求还原的实物必须读取实际参考 manifest/brief 并查看所需图片；参考不足时阻塞该 AST。普通剧情物品按共享 product-reference 的例外执行，无参考不单独构成阻塞，候选外观由用户确认。

## 执行

流程统一按上文六步 SOP。实现深度按 `references/00a-asset-complexity-routing-guide.md` 选择：普通一次配角可用单张综合参考；环境默认四图，已有充分覆盖时可少补并保留相机变化余量。复杂度不产生新资产需求。

每个 AST 在 `visual-asset-manifest.json` 中登记：类别、名称、状态、来源 revision、事件范围、可选镜头范围、交互、展示面、朝向、所需状态、必须继承、不可继承、真实参考、候选和确认资产。不可继承内容用于阻止身份资产继承剧情布光、表情、动作、临时污损或特定镜头构图等错误状态。

生成授权核对见 SOP 第 3 步及 [持续制作授权](../../PROJECT_RULES.md#持续制作授权不逐批重复询问)；工具及具体模型（平台不公开时记录真实披露）可由本项目已有授权覆盖，首次生图可沿已有授权直接进行，成图有问题时由用户决定重生方向或可用范围；决定重生后无须重复询问额度。

本轮选用红帧生图时，按[红帧生图 SOP](../_shared/redframe-operations.md#生图)执行素材卡上传、当场排版、可视接线、正文核对与提交。

## 候选与确认

- 候选、待确认、确认、过期、拒绝和阻塞是不同状态。生成成功、技术检查通过或 Agent 自查／独立审核通过都不能把候选升级为确认；统一入口的真实网站人审回执按上文登记。
- Agent 检查真实路径、格式、尺寸、命名、AST 引用、参考来源和明显产品事实，并按 [本轮要求到实际成图的自查](modules/asset-method.md#本轮要求到实际成图的自查) 实看选定结果、核对当前明确要求及准确交付版本。提示词通过不代表成图通过；未满足或未验证项如实保留，不得把自身检查写成用户视觉确认。
- 交用户审核时只提供候选文件夹绝对路径、准确文件名、对应 AST 和需要判断的视觉事项，不嵌入图片载荷。
- 用户明确确认具体候选后，才登记 `confirmed_asset` 和确认引用；旧确认版本保留为过期或被替代，不覆盖历史文件。

## 后续交接

资产图确认并导出后，交接建议按项目规则指向 `storyboard-director`：先结合已确认导演稿检查复杂动作、精准表情和空间关系，按需补分镜图，之后再编写视频提示词。仅说明后续顺序，本 Skill 不自动进入或执行下一阶段。

## 硬禁区

- 不生产当前有效白名单之外的资产，不自行修改剧情、对白、事件、AST 需求或导演镜头；用户明确调整资产需求按项目授权分支登记，不改已确认编导正文。
- 不把下载图、试验图、拼图、候选或技术检查通过的图片冒充确认母版。
- 不决定下一阶段、不调用其他 Skill、不写视频模型、生成分段或视频提示词。

用户明确新增、删除和恢复资产需求时，执行 [用户调整资产图需求](../../docs/rules/visual-production.md#用户调整资产图需求)。该授权分支允许按已核对出现位置加入需求表，不适用“表外一律停止”的自动推断限制；未授权的新物件仍须停止。下游读取 asset_requirement_decisions，取消项不是缺图，新增项须沿用其出现范围。


## 视觉生产衔接细则

本阶段涉及资产、按需分镜或场景状态时，读取 [视觉生产与资产调整](../../docs/rules/visual-production.md)，仅执行本职及已授权分支。
