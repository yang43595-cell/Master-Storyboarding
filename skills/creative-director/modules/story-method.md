# 故事方法加载与合同入口

## 本 Skill 产出须命中的规范（缺即停）

本 Skill 按“准备 → 阅读 → 登记 → 校验 → 产出”执行。先按 `skills/_shared/knowledge-access.md` 整理准确输入；需要动态选知时，在 prepare 命令追加 `--request-json <本轮请求文件>`。已有有效准备包和阅读证据按共享规则复用。

以下命令从仓库根目录执行；`<执行者ID>` 与 `<任务ID>` 由当前执行者确定，并在同一任务的三条命令中保持一致，不需要用户填写。

```powershell
python scripts/stage_context_cli.py prepare --project <项目> --skill creative-director --objective "本轮目标" --actor-id <执行者ID> --task-id <任务ID>
```

准备完成后，实际读取本轮 `stage_startup.md`、`method_reading_packet.md` 完整正文及下列适用本地合同。读完才登记，不能用登记命令代替阅读：

```powershell
python scripts/stage_context_cli.py record-method-reading --project <项目> --skill creative-director --method CREATIVE-DIRECTION-METHOD --actor-id <执行者ID> --task-id <任务ID>
python scripts/stage_context_cli.py ensure-prepared --project <项目> --skill creative-director --actor-id <执行者ID> --task-id <任务ID>
```

校验通过后才产出。缺少输入、确认冲突或阅读证据失效时，按具体缺口处理；动态规则与本地合同分别核对，本地合同无需重复嵌入知识包。

- **项目根规则**：`PROJECT_RULES.md`（唯一正文）与当前知识包（stage_startup 选中的规则块）。
- **已确认专业方法**：本 Skill profile 绑定的 `CREATIVE-DIRECTION-METHOD` 全文快照；它承担故事诊断、人物目标/阻力/转折/代价/结尾回收、最小充分改写与对白目的判断。不另存本地方法正文；当前知识包的适用补充内容仍按共享规则读取。
- **本地合同**：`references/dialogue-lineage-contract.md`（对白逐字保留、来源绑定）和模板（Markdown 内的稳定 STP/DLG/AST）；schema 与双文件一致性在用户确认后的结构化环节检查。

## 本地输出合同

完整故事正文是唯一剧情依据。正文用 `### STP-001｜简短定位` 标记稳定原文段落；一个段落保留完整的行动、反应及相应对白，可含多个自然段，不按镜头数量拆分。新增段落使用未占用编号，既有编号不因插入而整体重排；拆分或删除要更新受影响引用，不复用旧编号。

不再创建 events、EVT 或独立事件摘要表。用户确认正文后，叙事包 2.0 的 `story_paragraphs` 按正文顺序保存 `paragraph_id` 和该标题下面的完整原文 `text`，不得改写为摘要、增加 start_state/change/end_state 或再建一份事件层。每条对白通过 `source_paragraph_id` 指向含该句的原文段落；资产通过 `source_paragraph_ids` 指向需要它的段落。对白先行阶段正文尚未产出，来源段落列可留空，不能为填引用提前编写故事。完整稿完成后再补齐。

结构化后执行 `python -m scripts.core.story_sources --project <项目> --narrative <叙事包>`，校验原文、顺序及引用；不以程序通过代替来源核对或用户确认。历史 narrative 1.0 只按 `narrative-package-v1.schema.json` 读取，不自动将 EVT 改名成 STP；继续返工时从真实正文建立新修订。

## 完整故事正文

含对白的任务按 SKILL.md“对白先行”执行：先交对白供用户核对，核对完成后才产出完整故事正文，不是先写正文再补对白表。没有对白则跳过该步骤。不能把对白通过当成正文也已确认。

正文按现场发生顺序连贯写出，可以按场景分段，以人物能做出的动作和能观察到的变化表达故事。交代开场谁已在场、在哪里、正在做什么，谁后来进入；写清触发冲突的动作、人物回应及其后果。叙事关键物件须交代从哪里出现、由谁持有，发生掉落、拾取、交接或离开时写清前后状态。不要用“众人来到”“展示产品”“冲突升级”概括掉会改变理解的具体过程，也不要靠含混代词省略行动者。

把每条最终对白的完整文字按原顺序嵌入相应行动段落，注明说话人和 DLG ID；已核对的对白表是文字与归属的唯一依据，正文不另作润色、合并或增句。非对白动作也须独立核对来源，不能因为字幕没写就省略。用户已明确的画面纠正作为当前约束；原片复刻与保留原事件的改写以画面及用户要求核对动作，资料缺口在对应处标出并集中答疑，不用想象填满。自由创作可以设计动作，但不能改动锁定事实。

完整不等于文学铺陈：让读者能还原谁做了什么、为何引起下一步即可；情绪可通过表情、动作、停顿及其处境表达，不以大段内心解释替代事件。不写景别、机位、运镜或切镜。叙事必需的状态与动作写在原文中，并由相应资产需求引用；结构化逐段保留原文，不另写事件梗概。

## 原样对白与自查

完整 Markdown 送审前必须按 `references/dialogue-lineage-contract.md` 的“送审前检查与确认范围”运行候选检查，并对新稿及实质修订追加 `--check-asset-decisions` 检查资产表是否填过处理结论和判断依据（方法见 `references/asset-image-necessity.md`）；有有效独立对白依据时同时比对；这不要求先生成叙事包。程序结论、原始来源核对、动作因果自查和用户确认分别报告，不把其中一项通过扩展为其余项通过。

- 所有含对白任务均先核对白、后产出故事正文；1:1 / 原样任务另按 `references/dialogue-lineage-contract.md` 核对原文保留与来源。
- 自查时逐项核对原句覆盖与授权删除、人物映射和原始顺序，以及故事原文、资产需求与每条对白的来源段落是否互相一致；不能只检查 ID 是否连续。另对照画面及用户约束核对开场状态、无对白动作、关键物件的来源与去向；检查正文内对白与最终对白表逐字、归属、顺序一致。
- 映射缺口、稳定 ID、已确认修订和按需人工核对的处理均以该本地合同为准。

## 场景资产粒度

- 一个 `LOCATION` AST 只能代表一个可连续成立的空间身份，并写 `spatial_scope: "SINGLE_CONTIGUOUS_SPACE"`。同一房间的时段、人物状态、使用状态或下游镜头视角不新建场景 AST；人物换装也仍属于人物需求。
- 餐厅与家中客厅这类不能共用同一空间身份、需要独立资产事实的地点，必须各有 AST、各自的事件范围与空间约束。不能用名称、状态文字或网页分组把多个地点塞回同一 AST。
- 走廊与门口等连续的入口区域可以保留为一个 AST，但要在身份约束中说明其连续关系；后续如确认需要拆分空间身份，应回到编导稿的新版本处理，不由资产页猜测。
