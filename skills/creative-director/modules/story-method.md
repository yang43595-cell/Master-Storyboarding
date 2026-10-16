# 编导准备与输出合同

## 准备当前输入

按 `skills/_shared/knowledge-access.md` 建立本轮准备包；已有有效输入和阅读证据按共享规则复用。命令从仓库根执行，执行者和任务 ID 由当前执行者确定，不需要用户填写。

```powershell
python scripts/stage_context_cli.py prepare --project <项目> --skill creative-director --objective "本轮目标" --actor-id <执行者ID> --task-id <任务ID>
```

准备后读取 `stage_startup.md`、当前输入与适用的本地对白/资产合同；如选用下述方法则先阅读登记，随后运行 `python scripts/stage_context_cli.py ensure-prepared --project <项目> --skill creative-director --actor-id <执行者ID> --task-id <任务ID> --show-task-basis`，核对任务依据并通过后产出。需要按任务选知时在现有请求 JSON 写本轮事实与条件，并给 prepare 追加 `--request-json`；目标文字本身不参与选知。涉及产品的真实参考、使用方法和宣称边界仍须核对；缺口只暂停依赖该事实的部分。

## 按需方法与案例

第二层编导方法仍由 `knowledge-profile.json` 指向 Vault 的唯一正文。遇到具体故事或对白问题需要该方法时，在本轮请求写 `"use_story_method": true`；prepare 会载入 `CREATIVE-DIRECTION-METHOD` 全文快照，实际读完后运行 `python scripts/stage_context_cli.py record-method-reading --project <项目> --skill creative-director --method CREATIVE-DIRECTION-METHOD --actor-id <执行者ID> --task-id <任务ID>`，再 ensure-prepared。未选用时不读取该全文，也不登记虚假的阅读证据。

兼容动态补充知识按启动包候选的精确路径与锚点读取，采用时沿共享规则登记。第三层项目经验保持按问题检索，不新增自动读取绑定。专业方法与案例不复制进本层；必要产品事实不由是否选用创作方法决定。

## 本地输出合同

完整故事正文是唯一剧情依据。正文用 `### STP-001｜简短定位` 标记稳定原文段落；一个段落保留完整的行动、反应及相应对白，可含多个自然段，不按镜头数量拆分。新增段落使用未占用编号，既有编号不因插入而整体重排；拆分或删除要更新受影响引用，不复用旧编号。

不再创建 events、EVT 或独立事件摘要表。用户确认正文后，叙事包 2.0 的 `story_paragraphs` 按正文顺序保存 `paragraph_id` 和该标题下面的完整原文 `text`，不得改写为摘要、增加 start_state/change/end_state 或再建一份事件层。每条对白通过 `source_paragraph_id` 指向含该句的原文段落；资产通过 `source_paragraph_ids` 指向需要它的段落。对白先行阶段正文尚未产出，来源段落列可留空，不能为填引用提前编写故事。完整稿完成后再补齐。

结构化后执行 `python -m scripts.core.story_sources --project <项目> --narrative <叙事包>`，校验原文、顺序及引用；不以程序通过代替来源核对或用户确认。历史 narrative 1.0 只按 `narrative-package-v1.schema.json` 读取，不自动将 EVT 改名成 STP；继续返工时从真实正文建立新修订。

## 完整故事正文

正文按现场发生顺序连贯写出，可以按场景分段，以人物能做出的动作和能观察到的变化表达故事。交代开场谁已在场、在哪里、正在做什么，谁后来进入；写清触发冲突的动作、人物回应及其后果。叙事关键物件须交代从哪里出现、由谁持有，发生掉落、拾取、交接或离开时写清前后状态。不要用“众人来到”“展示产品”“冲突升级”概括掉会改变理解的具体过程，也不要靠含混代词省略行动者。

把每条最终对白的完整文字按原顺序嵌入相应行动段落，注明说话人和 DLG ID；已核对的对白表是文字与归属的唯一依据，正文不另作润色、合并或增句。非对白动作也须独立核对来源，不能因为字幕没写就省略。用户已明确的画面纠正作为当前约束；原片复刻与保留原事件的改写以画面及用户要求核对动作，资料缺口在对应处标出并集中答疑，不用想象填满。自由创作可以设计动作，但不能改动锁定事实。

完整不等于文学铺陈：让读者能还原谁做了什么、为何引起下一步即可；情绪可通过表情、动作、停顿及其处境表达，不以大段内心解释替代事件。不写景别、机位、运镜或切镜。叙事必需的状态与动作写在原文中，并由相应资产需求引用；结构化逐段保留原文，不另写事件梗概。

## 送审自查

按 [对白契约](../references/dialogue-lineage-contract.md#送审前检查与确认范围) 检查 Markdown；新稿或实质修订追加 `--check-asset-decisions`，有有效独立对白依据时追加 `--dialogue-baseline`。再对照原片、用户修改和事实来源核对行动者、顺序、无对白动作、关键物件去向及因果。程序证明文件一致性，不代替上述语义核对或用户确认。

## 场景资产粒度

- 一个 `LOCATION` AST 只能代表一个可连续成立的空间身份，并写 `spatial_scope: "SINGLE_CONTIGUOUS_SPACE"`。同一房间的时段、人物状态、使用状态或下游镜头视角不新建场景 AST；人物换装也仍属于人物需求。
- 餐厅与家中客厅这类不能共用同一空间身份、需要独立资产事实的地点，必须各有 AST、各自的事件范围与空间约束。不能用名称、状态文字或网页分组把多个地点塞回同一 AST。
- 走廊与门口等连续的入口区域可以保留为一个 AST，但要在身份约束中说明其连续关系；后续如确认需要拆分空间身份，应回到编导稿的新版本处理，不由资产页猜测。
