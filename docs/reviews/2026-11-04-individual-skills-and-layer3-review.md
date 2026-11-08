# 逐个 Skill 审查、优化与第三层知识建议

本轮按用户“单独对逐个 skill 进行细致的审查和优化，然后从知识库看看有没有可以进入 skill 三层的知识”执行。审查对象是本仓库 9 个 Skill；逐项核职责、触发、输入输出、SOP、实际引用、专业方法、检查清单与停止点。知识库只读检索与候选判断不等于正式采用。

本轮基线包含已有的 11-04 统一入口修改，完整保留。规则以当前 [PROJECT_RULES.md](../../PROJECT_RULES.md)、[共用三层规范](../../skills/_shared/skill-layers.md) 和各 Skill 入口为准；历史审查结论没有代替本次读取。此次修改为 Skill 合同维护，没有执行真实业务生产。

## 逐个审查与实际优化

### 1. creative-director

读取入口、knowledge/review profiles、story-method、对白来源契约、资产必要性、模板、压力场景及绑定的编导方法。原有故事与对白方法已覆盖动机、阻力、后果、最小充分改写和来源保全，无须另加同义理论。

问题是运行边界仍将“网站通过”和“人工确认”一概分开，并要求确认后再次等待继续；方法也仍写成只从 Vault 读取。已在 [入口](../../skills/creative-director/SKILL.md)、[方法](../../skills/creative-director/modules/story-method.md) 和 [检查清单](../../skills/creative-director/review-profile.json) 明确：按准确 JSON 修订读取已提交意见并返修，网站准确范围通过即该范围确认及后续授权；无回执不报通过，局部不升级整稿。默认知识根与显式外部根分别处理。

第三层判断：本轮“不会写剧本”来源的欲望—阻力—失败代价与现行方法重复；多方案固定数量及“爆款”效果没有验证，不纳入新要求。编导当前案例白名单为空，保持不变。

### 2. storyboard-planner

读取入口、两个 profiles、shot-planning、全部本地参考、三份影视基础、模板、压力场景与绑定导演方法。选路比较、场景节拍、观看对象、声画关系和全片复核已有实质方法，无须再套一张导演理论表。

发现 SOP、交付合同、执行规范、审查门槛和冻结清单仍无条件要求导演送网站及逐段人审。已同步 [入口](../../skills/storyboard-planner/SKILL.md)、[方法](../../skills/storyboard-planner/modules/shot-planning.md)、[执行规范](../../skills/storyboard-planner/references/专业影视分镜执行规范.md)、[门槛](../../skills/storyboard-planner/references/专业影视分镜审查门槛.md)、[清单](../../skills/storyboard-planner/review-profile.json) 和原压力场景：统一入口逐段自查继续，整稿另做全片复核；人工模式保留兼容。自主采用未接通时保留候选，不借旧入口补造人审。

第三层判断：一般空镜、动作切、视线和声音接续已覆盖；面部机械感与群像主次的具体案例可作下述有界来源候选，不把单人左右脸反应同步误读成多人动作同步。

### 3. asset-image-assistant

读取入口、普通制作/Web 两模式、输入合同、library profile/runtime、专业方法、各类别指南、handoff、review profile 及相关 schema/CLI。

已优化 [入口](../../skills/asset-image-assistant/SKILL.md)、项目 Web 方法、生产/人物/场景/清单指南和检查项：项目资产候选按网站准确 AST、输出、文件与用途确认；独立资产库不套项目网站；图片实际观察和网站回执分开。场景指南遗留的 `asset_manifest.md` 改为现行 `visual-asset-manifest.json`；没有独立风格 AST 时继承已确认风格决定，不为了模板新增风格图。

第三层判断：混合画风失效案例、多图寻找风格共性适合候选参考。它们提供具体反例或取证方式，不替换现有风格维度、素材职责及资产数量判断。

### 4. storyboard-director

读取入口、storyboard-method、连续性/参考契约/观察准入/制作检查、本职模板与 profiles。保留逐镜检查而按需出图、先查共同空间与剧情后态、失败图不得作为活动参考等有效要求。

已在 [入口](../../skills/storyboard-director/SKILL.md) 和其直接方法、参考、模板、清单中统一自主验收与兼容人工选图的分支；不再在下层重新要求网站逐图或逐图停等。自主采用缺口继续显式保留。动作参考、产品实拍改为本轮适用条件，普通非产品镜头不因未提供产品资料而停住。

第三层判断：2D/3D 混合失败及已有尺度案例有明确参考范围；不把姿势草图、剖面或白模变成每镜必备。

### 5. blender-previs

核入口、previs-method、项目接入、CLI/配方契约、三项覆盖检查及真实命令帮助。默认关闭、选用后复用同一空间基座、静态/运动验收分开及真实图片参考职责均保留；没有发现需要重写主体方法的问题。

仅将 [项目接入](../../skills/blender-previs/references/项目接入.md) 中通用命令的 Windows `exe` 示例改为实际 Blender 可执行文件占位，避免将某台电脑路径当成必需环境。未运行 Blender 渲染。

第三层判断：S03-030 案例可用于“真实身体接触与代理球不同”“静态通过不证明整条运动路径通过”的条件性复核；一般 Blender 工作流介绍与现方法重复，不新增生产要求。

### 6. voiceover-to-video-script

读取入口、两个 profiles、video-method、全部本地 references、模板、内容压力场景、内置提示词基础/素材职责方法及相关检查命令。

已在 [入口](../../skills/voiceover-to-video-script/SKILL.md)、方法、[画布交付](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md)、正文模板和原压力场景明确：统一入口逐段自查后继续，另做全片检查；不恢复 prepare、阅读包或 production_plan；只在采用及消费接线合法时写指定画布，保存、刷新、原生回读和布局检查后停止。兼容人工采用入口不能承接伪造的自主验收人审字段。

第三层判断：现有尺度案例已经有条件入口；面部机械感与主次诊断可以补具体反例。多人分阶段提示与现方法部分重复，“局部改一人、保留其余”的视频编辑示例需真实编辑能力和另行授权，暂不作为常规生成方法。

### 7. voice-reference-manager

读取入口、方法、profiles、压力场景、两份声音 schema 及 CLI 绑定/验证实现。来源授权、实际试听、用户选用、用途、current 与候选分离已有完整约束。

仅在 [入口](../../skills/voice-reference-manager/SKILL.md) 补明：网站通过及下游自主检查授权不代替明确启用声音，也不代替具体声音版本、角色与用途确认。未选声音不增加清单或占位。

第三层判断：同一角色的分情绪短音频确有增量，详见候选 A。当前方法尚未覆盖该条件，不据此新增声音管理数据库或自动生成任务。

### 8. stage-reviewer

读取入口、profiles、review-method 和 agents 元数据。发现合同审核虽被声明独立，读取步骤仍笼统要求生产候选及冻结包。

已在 [入口](../../skills/stage-reviewer/SKILL.md)、[审核方法](../../skills/stage-reviewer/modules/review-method.md) 和清单明确两种输入：合同审查直接读当前规则、目标入口、清单和直接依赖；产物审查才消费准确候选与冻结证据。旧清单不能重新引入统一入口已取消的人审；不刷新冻结包或改候选。

第三层判断：`topics: []` 合理；审核应使用目标 Skill 的方法与任务证据，不另绑全部生产理论。本轮未为它增加专属知识。

### 9. knowledge-maintainer

读取入口、维护方法、审计合同、profiles、agents 元数据、当前 runtime/manifest 入口，以及真实 Vault 的检索、生命周期和项目分流规则。

已在 [入口](../../skills/knowledge-maintainer/SKILL.md) 和 [维护方法](../../skills/knowledge-maintainer/modules/maintenance-method.md) 修正五处边界：普通合同自检不被“不得审查自己证据”一概阻断；授权不能由实验结果代替；候选不能因新建主题就直接写 enabled；纯仓库合同维护不强加全库审计；manifest 已选运行副本不因主库有同文就被当作冗余删除。Vault 清洁要求改为服从暂存区、声明范围与门禁，保留范围外既有改动。

增加“从知识库查找第三层候选”的短流程：比较现有覆盖、原状态、增量与限制，沿本轮审查记录给具体建议；“看看有没有”不自动修改绑定、白名单、活动规则或 runtime。知识维护本身无需为了第三层非空而绑定生产理论。

## 知识库检索范围与状态

真实读取 `/Users/liuyang/Documents/Obsidian Vault`。先查项目索引、三层分流、经验索引与候选入口，再对叙事对白、镜头剪辑、AI 视频、synthesis 和本项目记录执行 6 组只读 query；候选不足处沿已知来源文件名和原文扩展，不扫描其他业务项目或全量原片。对 11 个最终相关页执行 lifecycle check，读取 7 份来源摘要与 2 篇项目经验；检查这 7 份来源登记的原片、转写和画面分析共 21 个路径均存在。进一步读了声音校订稿、混合动画和风格来源的画面分析。

检索属于词法召回，query 返回数与 issues 包含未采用页面，不能当作“全部知识已核”。11 个精确页检查均退出 0，其中色卡和第一视角页各有 `review_metadata_gap`，没有据此推断过期或无效。5 份来源仍为 `candidate`、两份旧来源未声明成熟度；两篇经验为 `recorded/legacy_unreviewed`。候选在普通当前知识检索中被排除，仍可在本次来源评估中读取，不能自动晋升。没有未决裁定请求不证明全库无冲突。

以下给出首轮筛出的 4 项来源候选和 2 项已有案例的保留范围；初次审查时尚未写入生产绑定或运行副本。四项后续作为第三层可选来源接入的结果见末节，候选未晋升正式方法。本报告不是第二套知识正文或长期候选数据库。

## 四项第三层候选的具体建议

### A. 同角色按情绪分库存的短声音参考

来源：Vault `wiki/sources/AI声音一致性-刺猬星球super-i.md`，原视频 ID `7617801990097112366`；更新 2026-10-25，`candidate/supporting`，60.5–84.24 秒。来源摘要与校订稿一致，本轮未试听生成声音。目标为 `voice-reference-manager`，拟在 `modules/voice-method.md` 的试听段后放一个按需来源入口。

**增量与触发**：现方法已有固定音色和试听；新增的是已选同一角色在正常、愤怒、伤心等情境下可能需要不同参考。只在本轮已经明确使用声音且确有情绪跨度问题时参考。

**拟加入的完整参考说明**：“同一角色跨情绪对白的参考选择，可按需查声音情绪样本来源。作者展示分别保存正常、愤怒、伤心短语音并按剧情选样本的做法；这是未复现的单来源候选，不证明同音色或跨模型稳定。沿现有角色、授权、用途和试听记录核对，不自动增加情绪样本生成，也不套用来源中未具名工具的 Pitch/Stability 数值。”

### B. 2D 角色与 3D 场景混用的画风漂移反例

来源：Vault `wiki/sources/ai动画制作流程-刺猬星球.md`，原视频 ID `7699801423160085794`；更新 2026-10-25，`candidate/supporting`。71.5 秒参考职责、95.064 秒“全部变成 3D”反例、97.5 秒空间剖面与 107–109.5 秒姿势草图已有原文和画面分析。目标为资产与按需分镜，拟在 `references/01-style-master-guide.md` 和 `references/reference-asset-contract.md` 各加指向同一来源的条件入口。

**增量与触发**：现方法已有媒介一致性；该例提供明确失败形态，以及人物方向/表情、空间参考与姿势草图各自的用途。只有项目明确混合媒介且出现该缺口时参考。

**拟加入的完整参考说明**：“2D 人物与 3D 场景的混合媒介，可按需查该来源的整体转为 3D 反例，比较角色造型、方向/表情和空间参考是否各自明确。剖面与草图只提供该例的空间或姿势信息，不接管正式外观，不因案例存在就要求固定数量的角色板、每镜草图或开启白模。提示词有裁切，禁止补成完整模板；本库尚未复现修复效果。”

### C. 跨内容多图提取风格共性的取证方式

来源：Vault `wiki/sources/AI风格提取的不变条件与共性结构-刺猬星球super-i.md`；原视频 ID `7594419004559723795`，更新 2026-10-25，`candidate/supporting`。已有唯一主题落点 `wiki/concepts/03-AI生成与视频/色卡统一风格.md#摄入来源候选补充：AI风格提取的不变条件与共性结构`。目标为资产风格，拟在 `references/01-style-master-guide.md` 的参考选择处加可选来源。

**增量与触发**：当前风格方法已有九项风格锁、参考与色卡；该来源的增量是从同风格不同内容中寻找重复保留项的比较方式，适用于单张参考难分清“题材内容”和“风格约束”时。

**拟加入的完整参考说明**：“当参考只覆盖单一题材、难以区分内容和风格时，可按需查多图寻找不变条件的来源例，比较空间构图、光线氛围、色彩质感及限制的共性。结果只作本轮风格候选解释，不覆盖已确认的风格决定，也不要求额外购买或使用 Midjourney。来源没有受控生成对照，不能把作者的长期一致性断言当效果保证。”

### D. 面部机械感与群像主次的诊断案例

来源：Vault `wiki/sources/超真实AI电影六技巧拆解-刺猬星球super-i.md`；原视频 ID `7695725442308885775`，更新 2026-10-27，`candidate/supporting`、`secondary_report`；89.34–131.88 秒。主题中已有 `wiki/concepts/03-AI生成与视频/ai人物活人感.md#摄入来源候选补充：超真实AI电影大拆解`。目标为导演规划与视频表达，拟在各自现有表演参考/问题诊断入口附来源。

**增量与触发**：现方法已有动因、微表情与主次动作；来源补充视线僵硬、眨眼间隔过于规律、左右脸反应同步的诊断例，以及群像中主角和配角的视觉主次建议。仅在相应具体问题出现时参考。已回查校订稿 104.06–108.88 秒，“同步”指同一人物左右脸，不能转述成多人动作同步。

**拟加入的完整参考说明**：“人物视线或面部反应显得机械时，可按需查该来源的规律眨眼、左右脸同步案例；多人画面则另看其主次表现建议。它不要求随机眨眼或不对称，不为配角加戏，也不一律模糊配角；先按本镜观看任务及已确认拍法判断。作者属于二次拆解，本轮未完整动态观看或复现效果，不能用它覆盖已确认导演决定。”

采用以上任一建议前，需再次核原页与目标版本、既有来源重复和具体授权。若为新电脑提供可读副本，按独立仓库流程显式选择原字节与必要依赖、维护 manifest 并审查；本轮没有执行这些采用动作。

## 两项已有第三层案例及暂不新增内容

| 内容 | 本轮结论与边界 |
|---|---|
| `经验/2026-10-03-消失的公主-s03-030-白模与分镜图复盘.md` | 已有第三层案例；可在选用白模、接触/承托判断时参考真实身体与控制球、静态与运动验收的差别。其 Library 包和旧业务路径不在当前仓库，未重新渲染或核视频；不将过去局部接受升级全片通过。 |
| “不会写剧本”的欲望、阻力、后果 | 已覆盖，复用主方法；不新增固定十方案或“爆款”保证。 |
| 叙事空镜的环境、道具、情绪作用 | 已覆盖，仅保留来源；不新增每场空镜或固定 1–3 秒。 |
| 干净场景与颗粒/虚化等效果分层 | 可留来源例，但现有风格方法也允许成像质感，不能将“所有资产一律无质感”写成硬规则；本轮不改正式方法。 |
| 局部编辑一人、保留其他角色 | 源为 candidate，工具现状、编辑能力与效果未核；不因此扩展视频 Skill 的执行权限。 |
| 未具名工具自动提色、旧 mini 成本与参数 | 不据旧教学页面推荐当前工具或价格；缺准确执行工具、完整流程或效果证据，暂不接入。 |
| 审核/知识维护专属理论 | 当前无需新增，空 topics 保持合理。 |

## 验证与实际限制

本轮主检查实际使用现有 Python 3.12.14 与隔离临时目录：

- 9 个 Skill 均通过 skill-creator `quick_validate.py`；19 份 Skill JSON 可解析，9 份 review profile 身份、检查 ID 唯一性与正文非空检查通过。
- 本轮修改文档及本报告的 159 个本地文件链接存在；链接检查不证明正文语义或锚点含义。9 份 `knowledge-profile.json` 与当前 HEAD 原字节相同。
- `python -m scripts.core.knowledge_runtime` 核 52 个文件通过；动态集合仍只有 `MSB-KB-PROMPT-002`。未更新 manifest 或运行副本。
- 8 个相关现有测试文件实际运行：**213 passed、5 subtests passed、0 failed、0 skipped**，6.14 秒。覆盖编导 JSON、阶段准备/来源、审核身份与差异、知识边界、便携知识和本地方法约束；并非全仓库回归或真实生产测试。
- 视频/声音分组的 4 文件检查另得到 146 passed、1 skipped；其中与主检查重合的方法测试不重复汇总。唯一 skip `test_delivery_preflight.py::test_action_reference_checks_actual_video_duration` 已使用现有 FFmpeg/FFprobe 进程 PATH 单独补跑：**1 passed**。本轮主检查与补跑合计 **214 个不重复测试通过**，另有分组已验测试。
- `git -c core.whitespace=cr-at-eol diff --check -- skills` 通过，保留既有 CRLF。未为本轮新增测试框架、安装依赖或修改全局 PATH。

主检查命令使用 `python -m pytest -q -rs -c config/pytest.ini --tb=short`，显式选择 `test_creative_json_review_contract.py`、`test_stage_context_cli.py`、`test_stage_context_sources.py`、`test_review_identity.py`、`test_knowledge_review_boundaries.py`、`test_review_delta.py`、`test_portable_knowledge.py`、`test_boundary_method_contracts.py`；`--basetemp` 与 cache 均为本轮独占目录。

本机临时证据：`/private/var/folders/hq/x035j8j52754y03_pys1s4nh0000gn/T/msb-skill-knowledge-20261104-6ad2wzg1/`，保存 query、check、来源路径核验摘要、`pytest-result.json`、`pytest-final.log` 与补跑日志，系统可能清理该目录。当前核验 HEAD 为 `28038ed`；执行期间原有统一入口改动已出现在该提交，本轮未发起 Git 提交，也未撤回并行工作。本轮 Skill 改动仍为工作区修改。

审查发现时的缺口：网站 API、下游自主验收采用及消费尚未接通，见[统一入口接线现状](../development/review-to-canvas-entry.md#接线现状与开发交接)。另，资产 Web 已支持 body，普通 prepare 当时只接受 style/character/scene/object；后者的后续代码修复见下节。未以文档修改代替这些实现。

本轮保留原有业务确认、候选白名单、knowledge profiles 和 runtime 原字节；未写 Vault、未生成图片/音频/视频、未运行真实审核站或画布、未提交或发布。

## 2026-11-05 后续修复：普通 prepare 非人形分流

用户同意先修第 1 项缺口后，已修改 [普通准备的本地文档选择](../../scripts/stage_context_cli.py)，增加既有 `asset_method_scopes` 的 `body` 布尔类别，不新增 AST 扫描器、知识方法、状态表或生产步骤。方法解析沿原 profile 条件完成，不改正式绑定或 runtime：

| 本轮明确 scope | 本地角色指南 | 适用专业方法 |
|---|---|---|
| `body: true`，character 未设或 false | 仅非人形 02a | 共同图片连续性方法；不加载人物方法 |
| `character: true, body: false` | 仅人类/人形 02 | 连续性及人物方法 |
| 两者 true | 两类均保留 | 连续性及人物方法 |
| 旧 `character: true`，未写 body | 两类均保留，兼容旧请求 | 保持原人物方法选择 |
| scope 未明确或全部 false | 保守保留本地指南全集 | 方法仍按原 profile 条件选择，不自动全部加载 |

同步修正资产 Skill 入口与 asset-method 中“普通 prepare 不支持 body”的旧说明；类别与需求仍须由执行者按当前 AST 核对，不按名字或任务关键词猜物种。程序不校验 scope 与真实 AST 的语义对应，不能宣称已经自动拦截错误角色分类。仅选择对象/场景/风格的原分流及 Web 路线保持兼容。

在现有 [知识读取边界测试](../../scripts/tests/test_knowledge_review_boundaries.py) 增补 14 个参数化/集成用例，验证两类指南及递归引用的选择、显式附加不适用指南的拒绝、body 必须为布尔值，以及真实 prepare → 阅读登记 → ensure 流程。纯 body 夹具删除人物方法后仍可准备；混合角色缺人物方法则拒绝。旧阅读凭据不能满足新 scope，请求/必读指南变化使旧准备失效，未加载人类指南的变化不误阻断，项目状态原字节不变。

本次实际回归：**197 passed、37 subtests passed、0 failed、0 skipped**，3.46 秒。范围为 stage_context_cli、stage_context_sources、knowledge_review_boundaries、method_documents、visual_asset_reading、visual_asset_characters、portable_knowledge 七个现有测试文件；14 个新增用例已含在其中，不重复累计。Skill 结构、内置知识与差异检查另按当前修改核对。

日志与命令保存在本机临时目录 `/private/var/folders/hq/x035j8j52754y03_pys1s4nh0000gn/T/msb-body-prepare-20261105-ts8x1c2g/` 的 `regression-result.json` 和 `regression.log`；该目录可能被系统清理。使用现有 Python 3.12.14 和本轮进程 Node PATH，没有新增依赖或修改全局环境。网站 API/自主验收采用仍属前述独立缺口，四项第三层知识候选继续保持待审；本次没有执行后两项工作或触发真实生成。

## 2026-11-05 后续接入：四项第三层条件来源

按用户“下一步”继续已同意的推进顺序，本轮完成的是候选来源的可选引用与便携副本，不是第二层方法采用。未填写 `user_directed` / `experiment_validated` 晋升记录，9 份 knowledge profile、既有正式方法、动态规则白名单和生产状态均未改变。来源不进入专业方法阅读包或动态规则包；执行者按已明确的任务问题选择阅读，未命中或可选来源缺失不增加普通任务门槛。

| 来源及本地原字节副本 | 实际条件入口与限定 |
|---|---|
| [声音情绪样本](../../knowledge/runtime/wiki/sources/AI声音一致性-刺猬星球super-i.md) | [voice-method](../../skills/voice-reference-manager/modules/voice-method.md#第三层条件来源按需)：已选声音且确需比较同角色多情绪时才读。不能默认建库、生成、换声，也不能绕过当前一角色一份 current；未具名工具参数和影视声音授权不随引用采用。 |
| [2D/3D 动画案例](../../knowledge/runtime/wiki/sources/ai动画制作流程-刺猬星球.md) | [风格指南](../../skills/asset-image-assistant/references/01-style-master-guide.md#第三层条件来源按需) 的明确混合媒介漂移，以及 [分镜参考契约](../../skills/storyboard-director/references/reference-asset-contract.md#第三层条件来源按需) 的相关参考缺口。候选教程不放入确认图片表；草图/剖面不替代正式外观，不默认加图或白模。 |
| [多图风格共性](../../knowledge/runtime/wiki/sources/AI风格提取的不变条件与共性结构-刺猬星球super-i.md) | 同一风格指南中，单张参考无法分清题材内容和风格时才读。仅补一种取证方式，不改已确认风格或导演机位，不强制 Midjourney，也不把情绪板当色卡。 |
| [面部机械感与群像主次](../../knowledge/runtime/wiki/sources/超真实AI电影六技巧拆解-刺猬星球super-i.md) | [导演选路](../../skills/storyboard-planner/references/segment-sequence-method.md) 中相应注意/表演问题，以及 [视频排错](../../skills/voiceover-to-video-script/references/troubleshooting.md) 中已有观察或用户指出的缺陷。“同步”仅指同一人左右脸；群像主次是另一条建议，不新增错峰反应、随机眨眼或默认模糊配角。 |

四份副本逐字节匹配本轮实际主库原文，均保留 `status: candidate`；前三份 `updated: 2026-10-25`，最后一份 `updated: 2026-10-27`。manifest 使用现有 `reference_only` 角色，登记当前取得日期、原状态、原文大小和 SHA-256。没有修改来源正文、复制 raw 媒体或伪造本轮观看/试听；原文里的媒体、截图、转写及补充资料路径仍是证据定位，不表示它们已随仓库提供。

在本地 `codex/layer3-conditional-sources` 分支进行接入，保留此前全部工作区改动，未提交、推送或发布。原有 manifest 52 项及原字节不变，追加 4 项后共 **56** 份；动态集合仍仅 `MSB-KB-PROMPT-002`。没有新建候选数据库、选择器、方法绑定或自动同步流程。回退本次接入只需撤回这 5 处条件入口及对应 4 项 manifest/来源副本，不触碰原有方法、其他工作区修改或主库来源。

实际验证：

- 内置包及新增四源与主库的字节、大小、candidate 状态、reference_only 角色核对通过；9 份 knowledge profile 与 HEAD 原字节一致；新增源不出现在任何正式方法绑定中。
- 5 个消费 Skill 均有可达的条件入口：四个普通入口的文档收集包含对应指南/模块，视频从现有“生成异常→排错”直读入口到达，不恢复 prepare。准确来源路径可由当前知识根解析；不把路径可达当作已经读过。
- 六个相关现有测试文件实际运行：**169 passed、34 subtests passed、0 failed、0 skipped**，2.71 秒。覆盖 portable_knowledge、knowledge_selector、method_documents、knowledge_review_boundaries、stage_context_cli 和 visual_asset_reading；没有为本次文档/来源接入新建测试框架。
- CRLF 兼容差异检查通过。以上证明结构、原字节和读取接线；不证明模型理解、声音适配或生成效果，四项仍是第三层候选来源。

本轮临时证据目录为 `/private/var/folders/hq/x035j8j52754y03_pys1s4nh0000gn/T/msb-layer3-sources-20261105-6ot4ywrm/`，含 `source-checks.json`、`routes.json`、`regression-result.json` 及 `regression.log`，可能被系统清理。网站 API 与下游自主验收采用仍是下一项独立接线工作，本轮没有操作审核站、画布或生成服务。

## 2026-11-05 后续接线：本地自主采用与消费

已完成本地流程授权、整稿自查绑定、首次自主采用／返修提交，以及阶段输入、补图请求、图片引用和画布快照的合法依据消费；不生成逐稿人审记录。详细改动、全量回归和实际限制见[本地接线验收记录](2026-11-05-agent-acceptance-integration.md)。
