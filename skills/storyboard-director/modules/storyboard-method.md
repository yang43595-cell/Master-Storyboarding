# 视觉补充执行合同

本 Skill 必读 knowledge-profile 绑定的 `VISUAL-SUPPLEMENT-METHOD` 与 `IMAGE-CONTINUITY-METHOD` 完整正文；专业方法只在 Vault 保存。下文保留资料读取、来源镜头约束、请求数量与交付合同，不将回链当作已阅读。

## 本 Skill 产出须命中的规范（缺即停）

缺口判断与产出前，按 `skills/_shared/knowledge-access.md` 准备本轮资料，实际阅读两份完整方法并登记，再用 `python scripts/stage_context_cli.py ensure-prepared --project <项目> --skill storyboard-director` 校验准备状态，同时核对下列适用规范。包缺失时自行准备或报告真实阻塞；动态规则与直接读取的本地规范分别核对，本地规范无需重复嵌入知识包：

- **视觉补充方法**：`VISUAL-SUPPLEMENT-METHOD` 负责缺口证据、参考覆盖与状态继承判断；本模块 + `references/storyboard-production-checklist.md` 负责请求执行与检查。
- **景别/机位/调度/运镜/切点继承**：补图必须继承导演包的镜头契约与已确认资产状态，不重写景别/机位/调度/运镜/切点；景别与基础连续性剪辑属模型通用能力或导演已定，不靠知识包供给，不重新决定。
- **图片连续性**：必读 `references/image-continuity-hard-rules.md`（背景母版 + 多角度参考组 + 唯一面部来源），不再视为"涉风险才读"。
- **提示词语言合规**：写提示词必读 `../voiceover-to-video-script/references/prompt-language-hard-rules.md` 并以其为准；禁用 影视级/8K/唯美/高级 等炫技含糊词。

视觉补充只解决已确认导演镜头中一个无法由现有材料可靠承担的静态状态；该状态也可以是多个镜头共用的座位或站位关系。

1. 逐项查看确认资产、动作参考、产品实拍和来源镜头文字状态。
2. 按 `VISUAL-SUPPLEMENT-METHOD` 完成判断，在 `visual-supplement-request.json` 登记唯一目标状态、必须出现、禁止出现和保留项；补图填写 gap，复用填写 existing_references.coverage，返回上游填写 return_upstream.reason。
3. 选择最小参考集合并限制候选数量；已足够时复用并停止生成。
4. 技术核对只判断文件和引用事实，视觉好坏交用户终审。

## 场景关系参考的交付约定

- 在选择记录中写明来源场景、共用的位置状态、适用镜头及失效边界；共用状态沿用同一请求 ID 和参考图，不逐镜复制。
- 将导演已定的人物身份、相邻／对面关系、桌子／入口等空间锚点列入请求 `preserve`；必要时辅助标注人物姓名。未定的精确座次先讨论，不由生图工具补猜。
- 标明用途为“空间关系参考”。为看清关系采用的参考展示视角不改变导演镜头；它不是每段的起始画面，不锁定所有镜头的景别或机位。验收核对各机位下的相对位置，不机械锁死画面左右。
- 交接带上已确认关系图、适用镜头、需要保留的位置及明确变化；画外人物的位置仍保留。临时表情、持物、姿态和参考展示视角不作为全场固定项。
- 换座、离场、坐转站等发生后，更新受影响范围的关系说明；原图仍能承担固定关系时复用，不足时才讨论新的关系图。静态关系图不替代动作先后或运动过程。

凡本 Skill 写出视觉补充提示词，必先读 `../voiceover-to-video-script/references/prompt-language-hard-rules.md` 与 `references/image-continuity-hard-rules.md` 并以其为准，不再视为"涉风险才读"；提示词禁用 影视级/8K/唯美/高级 等炫技含糊词。

若问题需要改剧情、镜头设计、资产需求或连续性合同，终止补图并返回对应上游。

读取 v4 镜头时，take只定位单一时间相位；同时读取完整正文、camera、场景公共设定、overrides及相邻状态，按共享提示词语言红线落实相关人物的同刻状态。camera内的观看/反应也是剧情依据，take不是全体人物动作穷举。不重写正确导演，不要求新增Panel表。
