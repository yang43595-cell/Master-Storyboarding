# 逐 Skill 执行 SOP 与产物审查核对

日期：2026-11-04。范围：仓库现有 9 个 Skill 的入口、直接执行方法、产物审查清单及相关程序接线。按用户要求分别审查并落地 SOP；没有运行真实编导生产、生图、视频生成、试听、Blender 渲染或线上网页审查。

结论：9 个入口均已明确适用步骤、实际核对对象、失败回退和交付停止点。缺项补入原有方法或检查项，多余要求按适用范围收窄。SOP 的执行证据沿既有任务/候选/审查记录或交付说明保留，不新增运行时、执行台账、同义业务稿或默认独立审核。

后续更新：用户要求修复后，四个具体程序问题已修正，见下文“后续代码修复”。原发现按首轮状态保留，不代表当前仍全部待修；程序不能证明看懂/听懂的宿主边界仍保留。

## 共用的完成判定

规则落在 [共用三层规范的执行与完成核对](../../skills/_shared/skill-layers.md#执行与完成核对)，9 个 Skill 原入口均会读取它。

- 先定本轮入口、交付对象、范围和停止点，按本 Skill SOP 执行适用步骤。
- 实际读取要有返回的原文；程序检查要有本轮对象和结果；内容自查定位准确正文；看图/试听定位准确文件、用途、观察及局限。有效旧证据可以复用，但不能说成本轮重新执行。
- 交付前核产物清单与执行轨迹。必需项未执行、缺证或失败，就保留未完成/未验证；不能写成不适用，也不能因问题列表为空报全部通过。
- 发现问题回到相应步骤，修正后核当前版本及直接影响。用户确认、技术检查、正式采用和下一阶段授权分别判断。

这些是执行及审查约定。现有程序仅能强制已实现的门禁，无法证明模型理解、真实思考或看懂/听懂，也不能拦截所有宿主调用。下列遗留项没有被本次文字修改技术封闭。

## 逐个审查结果

| Skill 与正式 SOP | 缺少的检查或执行连接 | 多余、过时或需收窄的要求 | 本轮处理与完成停点 |
|---|---|---|---|
| [creative-director](../../skills/creative-director/SKILL.md#本轮执行-sop) | 准备、事实核对、实际读稿与最终 JSON 校验分散；产品获得/使用因果、新任务对白证据绑定未完整进入对应产物项 | 对白合同点名调用本仓库不存在的外部纠错 Skill；压力场景的职责禁区易漏传用户已锁定拍法 | 固定为核输入/准备→实读→同一完整候选→读稿/校验/返修重查→送审。补入 CREATIVE-STORY / CREATIVE-DIALOGUE，修正跨 Skill 调用和锁定事实传递。停在准确修订待确认；正式交接另核整稿确认和继续授权 |
| [storyboard-planner](../../skills/storyboard-planner/SKILL.md#本轮执行-sop) | 审查只核存在比较，未明确查产前保存、实际工具返回和成稿后落实顺序；缺集中返修及同版送审闭环 | 不需要新增每镜双案、Markdown 前置、全套分镜图或重复审核角色；这些已有边界保留 | 固定为确认上游/准备→先选观看路线→当前段 JSON→表达/连续性自查及校验→同版网页→收尾。补 PLAN-SEGMENT-SEQUENCE 的实际顺序检查；局部仍限定范围，全片另严格复核。网页入口/版本未核实时不能报已送审 |
| [asset-image-assistant](../../skills/asset-image-assistant/SKILL.md#本轮执行-sop) | 固定流程从技术检查直接交用户，成图实看及当前要求核对容易跳过；缺清楚的参考/制作/返修回路 | 新 canonical 叙事包被旧指南无条件要求独立 narrative.assets；独立库把全部道具实拍、环境四图泛化成必需 | 固定为范围→实看盘点→实现/提示词→授权产出/技术检查→准确成图自查→交审/确认/导出。修来源合同和库入口；沿现有 ASSET-QC 等检查，不增同义项。复用充分不重生成；候选与项目确认分开 |
| [storyboard-director](../../skills/storyboard-director/SKILL.md#本轮执行-sop) | 方法已有专业标准，但缺集中顺序与实际检查镜号范围；容易以通用观察字段齐全替代本次目标核对 | 不需要每镜新图、每镜独立请求、重复抄写请求到选择记录，或给 AST 母图强套补图观察字段 | 固定为锁来源→实看/先查共用关系及后态→决定/请求校验→授权补图→实看要求及观察准入→交审/复核引用。沿既有 VISUAL-TARGET / VISUAL-QC 核查。可无新图；失败/未核图不能进活动参考，未检查范围不报完成 |
| [blender-previs](../../skills/blender-previs/SKILL.md#执行顺序) | verify 通过与实际画面自查之间需要明确连接；三项覆盖需分别给依据、结果与局限，不能只报总通过数 | 快速网页预览被列成必经步骤；“全部视图”措辞误阻合法少视图路径 | 快速预览改可选，所需空间无法核实时才阻断相关操作；补各步结果及失败回退，BLENDER-QC 纳入规模/排布、人物承托/站位、导演信息可见三项实查。仍复用同一空间基座；未确认停候选，确认不自动授权生图 |
| [voiceover-to-video-script](../../skills/voiceover-to-video-script/SKILL.md#执行顺序与停止点) | 清单侧重提示词，遗漏实际平台保存/回读、任务和落盘媒体的分对象完成依据；需明确逐段与全片复核 | 普通剧情被广告机制/英雄镜头项覆盖；不能因写提示词强加上传、实际生成、prepare 或完整内容 QC | 固定为真实输入/只读检查→当前段写作自查→段落确认/全片复核→获准平台操作→获准生成/下载→分对象收尾。补 VIDEO-COMPLETION / SUBMISSION / MEDIA；广告专项按适用条件检查。提示词、草稿、媒体和用户采用分别结论 |
| [voice-reference-manager](../../skills/voice-reference-manager/SKILL.md#执行) | 只列“试听”而无实际听核范围/适配观察；清单漏试听；候选、用户选定与 current 的停止点不够清楚 | 候选交付不必先绑定；没有下游稿件时不建退役 production_plan 凑检查；未选用声音不增加占位 | 固定为角色/输入→来源授权/技术→实际试听→确认后绑定/校验→已有下游引用核对→分状态交付。补 VOICE-AUDITION / COMPLETION，收窄 CURRENT / DOWNSTREAM；换声回查适配、范围和直接依赖 |
| [stage-reviewer](../../skills/stage-reviewer/SKILL.md#执行-sop) | 报告重发现、轻实际覆盖；缺必要来源时空发现列表可能被误当通过；需检查目标 SOP 是否执行 | 普通自查不套独审包；视频独审不建 prepare/review-pack；无须每项另建通过证据文件 | 固定为授权/对象→准确候选来源/清单→实际产物和轨迹→有范围的发现/结论→原审核者复审。补入原 REVIEW-EVIDENCE / CONCLUSION，保持原 4 项；必需项缺证禁止该范围全部通过，不修改候选或写批准 |
| [knowledge-maintainer](../../skills/knowledge-maintainer/SKILL.md#执行顺序) | 缺授权修改后的差异核对、受影响消费者/检查与交付闭环；检查未区分上游、只读快照和仓库运行版本 | 仓库 SOP 维护被旧检查要求主库写锁、全 Vault 零错误；“仓库无专业副本”与已选 runtime 合同冲突 | 固定为定范围→查依据→本职维护→验改动→修正复核→交付停止。修 OWNERSHIP / GUARD / TRUTH / AUDIT，补 CHANGE；纯仓库合同不要求全库审计，晋升/回退仍保持来源、授权与必要独立证据审查 |

资产与按需分镜原有产物 QC 已覆盖本轮要求到成图的核对，因此补执行接线，没有重复添加同义审查项。导演原有 JSON→自查→现有网页→准确版本确认的顺序、声音/白模可选、环境四图默认合理及已确认文件保护均保留。

## 首轮查出的程序缺口（发现时状态）

以下为首轮审查时的事实；当前处理结果见后续修复。

1. **资产导出存在无依据的事实通过字段。** [visual_asset_projects.py 的 export](../../scripts/visual_asset_projects.py) 仍按 REAL_PRODUCT 类别直接写 `product_fact_check: passed`；相邻 confirm/export 路径核技术结果及用户确认，没有自动执行 Agent 成图事实审查。SOP 已要求核真实参考和实际图，不能把该字段当作已执行证据。代码修复应使状态来自准确图片的真实审查结果，缺证不得写 passed；本轮未新增记录接口或修改导出代码。
2. **声音允许用途未被 CLI 完整核验。** [voice_reference_cli.py](../../scripts/voice_reference_cli.py) 的 add 允许空 permitted_uses，`_snapshot_core` / validate_project 主要核 authorization.status，manifest schema 却要求非空用途。SOP 已要求核本次用途是否真实获准，单独的 authorized 标签不能过关。代码仍缺非空用途检查，语义覆盖也不能仅由非空数组证明。
3. **声音下游部分路径仍依赖旧计划。** 同一声音 CLI 的 invalidate_character_segments、_validate_replace_contract、derive_voice_sync 等仍处理旧 production_plan；既有测试证明旧计划/依赖失效，不证明现代视频 JSON 在无旧计划时的映射发现完整。没有真实下游映射时不得报引用检查已完成，不为补测试创建退役计划。
4. **对白证据与实际执行轨迹存在兼容/宿主边界。** [story_sources.py](../../scripts/core/story_sources.py) 对 dialogue_evidence_path 的检查仍是有字段才进入，删除全部字段可能落回历史兼容；新任务已要求人工核对，不允许删字段绕过。adopt-package 及网页离线编译也不能证明真正读稿、产前比较或线上审查已经执行；不得仅据机器通过宣布这些步骤完成。

首轮仅修改 SOP、对应清单和直接矛盾文档，当时将上述程序问题保留待修；下节记录用户要求后的代码修复，没有宣称宿主所有跳步出口已封闭。

## 后续代码修复

| 原问题，通俗说明 | 已实现的修复 | 核对范围和保留边界 |
|---|---|---|
| 产品图没审过，也自动写“事实检查通过” | 资产导出不再按 REAL_PRODUCT 自动写 passed。没有单独结果就省略可选字段，交接说明明确“用户选图已确认，未记录 Agent 产品事实核对” | 真实导出合成测试核产品不产生 passed、其他类别仍 N/A、准确确认字节/schema/幂等保持。用户确认仍有效，历史清单不原地重写；旧 passed 需回查真实观察依据 |
| 声音只看“已授权”，不看“允许做什么” | authorized 候选和 current 必须有合格授权引用及非空 permitted_uses；新 bind/replace/snapshot 显式传 --intended-use，逐项准确匹配，快照记录 intended_uses | unknown/restricted 仍可保留候选。旧快照未登记本次用途时只读返回 usage_checked=false，不补写确认；程序不猜自然语言授权范围 |
| 把对白证据字段删掉，就可能冒充老稿放行 | 正式采用、返修确认/提交、正式交接复用严格检查：含对白的叙事必须绑定真实证据，缺失/错字节/错对白/未核身份不能进入正式链 | narrative/storyboard/video 三类正式入口均有拒绝负例；添加历史 Markdown 字段也不能豁免。真正历史包仍可只读，报告未核范围且不改变原批准；无对白、显式隔离候选及有限批准正文分支保留 |
| 换了声音，新版视频稿仍可能沿用旧引用 | 换声先读准确登记的现代 video.prompt、导演和叙事来源，核确认、字节、镜号与对白/角色映射，再核声音依赖；合格则沿现有依赖使相关产物失效 | 不依赖旧 production_plan；保留旧格式兼容。支持确认实体表里的 CHR-X/X 别名，歧义停止。来源或依赖错误时在新快照复制前拒绝，业务字节不变；共享锁的 owner 时间戳可刷新。未选声音不强加声音步骤 |

声音 schema、本职命令说明和视频选用声音时的 JSON 引用要求已同步；资产事实检查项也已区分技术检查、真实观察和用户确认。本次没有添加审图服务、平行证据数据库或新的审批层。

机器能核的是记录、准确来源、字节和依赖；真实看图/试听、授权语义及实际生成效果仍按本职 SOP 判断。四个程序问题的修复不表示已经完成真实媒体验收，也不表示程序能证明 AI 已理解。

## 首轮直接验证

使用宿主已有 Python 3.12.14 和 Node.js 24.22.1。缺少的已声明 Python 测试依赖安装到本次独占临时 venv，不改全局解释器或仓库依赖清单。隔离临时路径先解析为真实 /private/var 路径，并为本次测试进程提供 Node PATH。

- 9 个 Skill 通过 skill-creator 的 quick_validate；9 份 review-profile JSON 身份、检查 ID 唯一性和正文非空检查通过。
- 修改的 Skill 文档中 98 个本地 Markdown 文件链接可达；保留原 CRLF，`git -c core.whitespace=cr-at-eol diff --check -- skills` 通过。
- `python -m scripts.core.knowledge_runtime`：52 个已选文件字节通过，动态集合只有 MSB-KB-PROMPT-002；不是全 Vault 审计。
- 11 个相关现成测试文件：**401 passed，16 subtests passed**。覆盖阶段准备/冻结验收、对白和导演候选来源、JSON 管线、项目资产、补图请求、白模项目、声音、视频输出、轻复审及便携知识。
- 对共享核对、知识维护和审核入口另做三种只读场景推演：纯仓库 SOP 维护；视频无准备包独审；必需来源缺失且发现为空。未发现误加全 Vault、恢复退役包或无证据全部通过的规则矛盾。这不是实际生产执行测试。

测试使用：test_stage_context_cli.py、test_story_dialogue_consistency.py、test_planner_candidate_sources.py、test_json_only_pipeline.py、test_visual_asset_projects.py、test_visual_supplement_request.py、test_blender_previs_project.py、test_voice_reference_cli.py、test_video_output_policy.py、test_review_delta.py、test_portable_knowledge.py。首次运行暴露的新增审核项数量不兼容，已通过并入既有项修正；Node PATH 与临时目录别名问题修正后，同一组测试全部重跑通过。

首轮未修改业务项目、已确认产物、内置方法快照或全局模型配置；未发布、提交 Git、同步 Vault 或执行下一阶段。当时工作区同时存在其他任务的 scripts 改动，未改写或回退；回归反映运行时当前工作区，不代表对那些改动做了完整审查。

## 后续修复验证与剩余环境缺口

用户要求代码修复后，针对实际正常入口和拒绝入口追加了隔离合成测试，不使用真实业务项目或伪造实际用户确认。正式采用/返修测试只在本测试文件创建明确标注 synthetic 的有效证据，原历史只读 fixture 保留；同一工作区其他任务已补的合法正式交接/迁移 fixture 也原样保留。声音失败回滚比较覆盖全部业务文件，精确排除允许刷新 owner 时间戳的共享锁文件，不排除管理 JSON、音频或确认记录。

代码修复阶段当时的完整 Python 回归结果：**2373 passed，177 subtests passed，19 skipped，3 failed**，43.67 秒。19 个跳过项没有计作通过；3 个失败均为 `test_quality_evidence_consumers.py::test_xiaoyunque_probes_actual_video_not_internal_cuts` 的参数用例，直接报测试进程找不到 `ffmpeg`，未进入该视频测试的实际断言。当时未为凑全绿跳过失败、放宽测试或安装新的系统视频工具；该次整套测试不能称为全部通过。后续本机补验结果见下节。

四处修复的正常及拒绝路径均通过当前工作树测试：产品事实不自动 passed；声音用途明确匹配；无旧计划的现代换声依赖失效及错误前不落业务文件；叙事/导演/视频三类采用和返修缺证拒绝；正式交接缺证不出交接卡、历史只读不改批准。声音与别名歧义负例复核通过；两份声音 schema、9 份审查 profile、文档链接及 CRLF 兼容差异检查通过。内置知识 52 文件验证仍通过。

测试解释器仍为本次独占临时 Python 3.12 venv；仅在临时 venv 补齐其已有间接依赖 typing_extensions，以支持隔离 `-I -S` 检查，不改项目依赖或全局环境。完整回归命令为 `python -m pytest -q -c config/pytest.ini --tb=short`，临时目录、缓存与 Node PATH 按前述方式隔离。

该阶段留下的项目是测试进程的 FFmpeg 可用性，以及真实媒体/页面/授权语义的实际验收；后续已在下节补齐前者，真实验收按用户答复暂不执行。程序修复和测试不授予这些操作的授权，也不修改历史已确认产物。该阶段保留工作区其他任务的并行代码变更，本任务当时没有提交、发布或同步到远端。

## 本机技术补验（用户要求继续，暂不做真实验收）

本轮范围按用户后续答复限定为本机技术验收，真实项目、私人案例、付费生成、线上页面和真实媒体验收均不进入本轮。

已纠正环境判断：上一轮只能证明测试进程的 PATH 找不到工具，不能据此断言本机未安装。沿已登记本机工具入口定向核实后，`/Users/liuyang/Documents/AIGC/.runtime/bin/ffmpeg` 和同目录 `ffprobe` 均真实存在、可执行，版本均为 8.0。本轮仅给验收子进程提供该 PATH，不修改系统配置、项目依赖或 AIGC 项目。

- 用现有工具生成 2 秒合成 H.264/AAC 媒体，ffprobe 读回 2.000 秒、64×64、16 kHz；`ffmpeg -v error -xerror -map 0 -f null -` 全量解码退出 0，stderr 为空。该样本只核工具，不代表真实成片质量。
- 此前受工具环境影响的四个测试模块先补跑：174 passed、24 subtests passed、1 skipped。原 3 个失败及 15 个可补跑跳过项已通过；随后在临时环境补齐仓库已声明 ASR 依赖，精确兼容用例单跑 1 passed，最终完整回归也实际运行并通过该用例。
- 最终完整 Python 回归：**2393 passed、177 subtests passed、0 failed、3 skipped**，45.47 秒。命令为 `python -m pytest -q -rs -c config/pytest.ini --tb=short`，本轮临时目录和缓存均隔离；精确命令、起止时间与版本保存在 `python-results.json`。仅为遵守本轮范围，在该子进程移除两个私人案例环境变量，没有改测试或将失败改成跳过。
- Node 自动化：77 passed、0 failed、0 skipped。它验证前端逻辑和离线操作合同，不代表真实浏览器页面已人工操作。
- 阶段、声音绑定、白模 CLI 的帮助入口均退出 0；这只说明命令可加载，不等于实际白模渲染或真实生产执行。

最终 3 个未运行项逐项保留，均未计作通过：

| 用例 | 未运行原因与范围 |
|---|---|
| `test_product_refs.py::test_project_validator_uses_product_category_boundary` | 需要 Windows 且能找到 `powershell.exe`；本机为 macOS，未模拟平台冒充通过 |
| `test_prompt_source_review.py::test_real_sources_read_only[MSB_TEST_CASE2_R12-19]` | 需要指定私人案例；按用户“先不做真实验收”不提供、不读取 |
| `test_prompt_source_review.py::test_real_sources_read_only[MSB_TEST_CASE3_R04-16]` | 同上，保留条件跳过 |

运行时 HEAD 为 `303a9a4654ebdf25af16f8b0d2838dc031c81adb`。完整回归前后 HEAD 与已跟踪非 docs 文件的内容摘要一致；本轮只修改验收报告和独立仓库环境说明。Python 3.12.14；可选依赖包含 faster-whisper 1.2.1、av 19.0.1、ctranslate2 4.8.5、onnxruntime 1.31.1，`pip check` 无依赖冲突。依赖安装和测试均在独占临时 venv 中，不下载语音模型。

验收证据目录：`/private/var/folders/hq/x035j8j52754y03_pys1s4nh0000gn/T/msb-acceptance-20261104-w5qekbsk/`，包含合成媒体、`tool-smoke.json`、`python-results.json`、完整 Python/ASR/Node 日志及 CLI 日志；该目录为本机临时证据，可能被系统清理，本文保留结论、版本、命令和未验范围。

本轮可在本机运行的技术检查通过，原 3 个失败已消除，原 19 个跳过中 16 个已补跑通过。剩余 3 项条件检查、真实产物内容、真实页面操作和生产效果仍未验收；不据此宣布真实业务全部通过或所有宿主跳步出口已封闭。

## 联合验收与本地提交补充

上文保留本任务独立环境当时的结果；同一工作区随后使用已有 Node 和媒体工具路径完成联合回归：2375 passed、4 skipped、177 subtests passed，JavaScript 77 项通过，内置知识 52 个文件通过，运行期间源码未变化。完整范围及 4 项未运行原因见[项目链路审查报告](2026-11-04-project-chain-audit.md)。前述 FFmpeg 缺口属于当时测试进程的 PATH，不表示本机未安装。

用户随后明确要求本地 Git 提交；本次只保存本地版本，不推送远端、不合并主分支，也不改变业务确认或发布状态。
