---
name: storyboard-planner
description: 承接已确认故事与对白，创作并自查完整的视听分镜稿，确定画面、表演、声画关系、节奏与镜头衔接，按本轮入口完成验收后交接；不重写故事或决定生成分段。
---

# 导演：从故事到视听分镜

开始本职任务前读取[共用三层规范](../_shared/skill-layers.md)。写法、字段和自查方法只在 [镜头规划](modules/shot-planning.md) 维护一份，本文件只管职责、流程、红线和交付。

## 承上启下的职责

编导确定故事发生什么、人物关系、事件与对白；本 Skill 把已确认的故事设计成观众能够经历的情绪过程，决定这些内容怎样按顺序被看到和听到。最终交付是完整、可执行的导演包（`storyboard-package` 5.0 JSON）。

- 保留已确认的事实、事件顺序、对白和说话者；在允许的创作范围内作出具体的镜头、表演和声画设计。需要改写故事或补事实时返回编导，不自行补写。
- 导演重拍按下文 SOP。普通 handoff 在交接卡旁生成 `导演编导来源.json`，按段落预填来源版本、原文、对白原句与说话人、有效资产编号；导演只读这一份来源，不再重复读整份编导稿。
- **复刻局部改戏单独执行。** 交接卡带 segment_ids 时，读卡里绑定的局部任务文件，只写该段改法和进出状态，用[复刻回传命令](../depth-guided-recreation/modules/recreation-method.md#局部导演回传)交视频，跳过整片创作、整包自查与采用。
- 视频 Skill 承接本轮合法采用的拍法，负责提示词、素材引用和生成分段；实现受限时返回具体镜头，需改导演决定的由本阶段修订，下游不暗改。

本阶段同时验收“拍法可执行”和“重要情绪有视听表达”。叙事与对白覆盖完整不等于表达充分；字段齐全、镜头数量只是辅助检查。只依据故事创作的拍法标为候选导演设计，逐镜复刻须以原片证据核对。

## 术语与范围

| 用语 | 本阶段含义 |
| --- | --- |
| 原文段落 STP | 上游故事的来源定位，不规定镜头数 |
| 场次 scene | 一个时空连续的戏剧单元，写一次共用设定 |
| Shot／镜头 | 一次连续摄影，对应一个 `shots[]` 对象 |
| 视频生成片段 | 视频 Skill 决定的生成单位，不由本阶段划分 |

## 本轮执行 SOP

新一轮导演创作或实质重跑由全新专属生产 agent 执行（[干净上下文启动规则](../../docs/rules/execution.md#创作执行开始前干净上下文)），同轮逐场推进与局部返修可继续原执行者。纯错字、标点或排版按[局部维护边界](../_shared/knowledge-access.md#局部维护边界)处理。

1. **核定本轮与上游。** 运行下文 `D outline` 获取 `must_read` 本关产品与拍法必读小抄并完整实读；核对下文输入门禁列出的输入。来源或确认冲突时停止受影响设计。
2. **全片统筹。** 按[镜头规划](modules/shot-planning.md#从整场意图想出画面)先定全片各场作用、情绪走向、衔接和时长预算；有实质拍法岔路时按[实质创作的先后顺序](#实质创作的先后顺序)比较后选定，无岔路不凑双案。
3. **按整场写。** 每次写完整的一场：整场走向加这一场全部镜头，用下文逐场落盘工具写入同一份 JSON。写法与字段见[镜头规划的字段写法](modules/shot-planning.md#导演包-50-字段写法)。
4. **逐场自查。** 每场写完按[状态与节拍步骤](modules/shot-planning.md#把创作决定落实为可验证的状态与节拍)查表达和连续性，再跑局部来源自查；修当前候选后复查改动及直接影响。
5. **全片验收。** 全部写完做全片表达、连续性、来源和总时长复核，跑全片严格校验，按交付合同登记、自查并合法采用；授权、证据或接线缺失时保留候选。
6. **收尾。** 交回准确路径、revision、验收依据及未核项；有被替代旧稿时执行[旧稿归档收尾](../_shared/draft-lifecycle.md#旧稿归档收尾)。不自行调用分镜图、视频或独立审核。

### 渐进式读取与逐场落盘（必须用，禁止一次读全片原文或输出整包）

> **防断连与提速硬红线**：
> 1. **严禁翻阅 `schemas/storyboard-package.schema.json` 或 `scripts/*.py` 源码**——完整 `5.0` 字段示例已在 `modules/shot-planning.md` 写清，`put-scene` 会自动报错提示具体缺漏字段。
> 2. **每回合最多只允许 `write_file` 1 个场次的 ≤2–3 镜**（>3 镜的大场拆成 `put-scene` 前 2–3 镜 + `append-shots` 剩余 2–3 镜分次落盘），严禁把 4+ 镜塞进单次 `write_file`（会超过 2,000 输出 tokens 触发网关 80s 超时重试！）。
> 3. **多幕真并发（无需单派“总导演统筹”子代理！）**：需要多幕并行时，主协调对话自己在 10 秒内跑 `D outline` → `D init` → `D plan-acts --max-scenes-per-act 3`，然后**立刻同一轮并发派发 `ACT-01`、`ACT-02` 两个写镜子代理**（各自按场内局部镜号 `S04-001, S04-002` 并发 `put-scene`）；全部返回后跑 `D finalize-acts` 一键重排全片连续镜号并 `register`。

统一用 `scripts/director_package.py`（`--project` 与 `--out/--package` 放子命令前后均可）：

```sh
P='<项目绝对路径>'; OUT='03_导演/<revision>_storyboard-package.json'
D(){ .venv/bin/python scripts/director_package.py --project "$P" --out "$OUT" "$@"; }
D outline                                         # 1. 只看全片紧凑大纲与 must_read 必读小抄（约 1.5KB）
D init --revision <R0x> --header <director_rules.json> # 2. 建空骨架（若有已采用旧导演包返修，先 stage_context_cli.py open-change --target-logical-id storyboard.package）
D plan-acts --max-scenes-per-act 3                # 2b. 多幕并行时一键生成锁死首尾交接棒与局部镜号规则的 .parallel-plan.json，随即并发派 ACT 子代理！
D scene-source --paragraph STP-01                 # 3. 子代理写哪场才取哪场完整原文、对白与有效 AST
D put-scene --scene <单场≤3镜 scene.json>         # 4. ≤3 镜一次写完；>3 镜先写前 2-3 镜，再用下行分批追加
D append-shots --scene-id S01 --shots <后2-3镜.json>
D finalize-acts                                   # 5. 多幕并发写完后一键核验接缝并顺排全片连续镜号 S01-001..Sxx-0yy
D register --artifact-id DIR-<revision>           # 6. 全片严格校验通过后登记候选（自动识别活动变更案）
D self-check-template --artifact-id DIR-<revision> --authorization-id <授权ID> --agent-id <执行者> --output 00_项目管理/导演<revision>自查.json
```

**超过 6 场的中长片推荐用 `plan-acts`“锁接口多幕并行”（≤6 场短片单人逐场写即可）：**

1. **锁接口建盘子**：运行 `D outline`、`D init --header <director_rules.json>`，再运行 `D plan-acts --max-scenes-per-act 5` 自动生成锁死各幕首尾交接棒（`entry_handoff` / `exit_handoff`）与局部独立镜号规则（如第二幕从 `S06-001` 起编，互不抢号）的分组计划（也可手写 `save-plan` 精调）。
2. **多路同时并行写场**：同时派 2–3 名分幕执行者，每人只跑一条 `D group-brief --group-id ACT-01`（或 `ACT-02`）领取本幕规则、首尾交接棒和本幕原文，各自用 `D put-scene`（>4 镜配合 `append-shots`）**并发写入同一份导演包**（CLI 自带文件锁自动防冲突并按段落顺序归位）。
3. **统稿重排与验收**：并行全部返回后，运行 `D finalize-acts`（或直接 `D register`，内部自动核验跨幕接缝并将全片局部镜号重排为连续 `S01-001..Slast-NNN`），填写自查骨架并执行 `accept-artifact`。

工具自动补齐：每镜的来源段落（默认取本场）、对白说话人和原文（只写 `dialogue_id`，一句跨镜时才写片段 `text`）、每镜对白编号。单场 JSON 写到 `00_项目管理/执行上下文/` 下的临时文件再 `put-scene`。校验报错会写明镜号、对白和差异，按提示改该场后重新 `put-scene`。全部写完 `register`，逐项真实填写自查骨架（`requirement` 是该项要求），最后运行骨架返回的 `accept-artifact` 命令。

## 实质创作的先后顺序

准备/阅读通过只说明可以开始工作。按[两阶段比较记录](references/segment-sequence-method.md#先选路再核落实不要等最终哈希才比较)，适用的短序列比较须先保存并得到工具返回，再展开完整稿；成稿后回读落实并追加最终 revision/hash。晚补比较只作后验返修依据。

## 输入门禁

- 只消费 `project_state.json` 与 artifact manifest 指向的已确认 `narrative.package` revision；确认状态、路径或 revision 不一致时停止。
- 用户明确锁定的拍法、已确认导演版本按准确来源保留；AI 自选但未经用户确认的旧拍法可在本轮范围内重选。来源不明且会改变受保护拍法时先核确认记录，仍不明再问，不擅自解锁。局部换服等修改只处理相关镜头及直接连续性，不扩大为全量重设计。
- 对白文本和说话者必须逐字沿用，不能新增、删除、改写或重新编号上游事实。用 `python -m scripts.core.story_sources --project <项目> --narrative <叙事包>` 核原文及引用；历史带 human_readable_path 的包用 `--reading-supplement` 核双文件一致性。
- 资产从已采用编导稿和现行资产调整读取，只引用有效 AST；资产 Skill 并行备图，导演先写依据已明确的范围。需要看图时按 AST 与 output ID 定向查看，确认登记不等于已看懂画面。
- 缺少会改变剧情、人物、已锁定空间或资产事实的信息时，只返回带来源段落和受影响镜头的缺口；允许设计的站位、机位与调度按[画面构思边界](references/visual-conception.md#2-从现场找可见载体)作具体候选设计。

## 交付合同

交付 `<revision>_storyboard-package.json`（5.0），只引用已确认叙事修订及稳定 ID。

- **统一入口：** 不送导演网页、不逐段等人审；全片严格校验和完整自查通过后，按[下游自查证据](../../docs/development/artifact-adoption.md#下游自查证据)绑定授权、准确 revision/hash 与自查结果，走[首次采用](../../docs/development/artifact-adoption.md#首次自主采用)或[自主修订](../../docs/development/artifact-adoption.md#自主修订)。缺授权、证据或采用入口时保持候选。
- **未迁移的既有人工路径：** 同一候选 JSON 自查校验后送现有审核网页，首次采用、返修及继续授权沿原合同；入口未核实只暂停对应网页动作。
- **阅读稿按需导出：** 新包统一采用 `storyboard-package` 5.0 JSON 并省略 human_readable_path；阅读版由工具从 JSON 生成，Markdown 仅用户要求时同版导出，不反向覆盖 JSON（[JSON 交付与按需导出](../../docs/rules/artifacts.md#json-交付与按需导出)）。

## 硬禁区

- 不新增或修改资产需求，不制作、移动或确认资产；已取消出图的物件保留在镜内过程，已拆分的素材引用改用有效子项。
- 不决定视频模型、生成分段、时长上限、提示词、生成参数或正式分镜图。
- 不修改剧情、角色、事件顺序、对白、产品事实或叙事包 ID。
- 不调用下一 Skill、不决定阶段流转、不写批准事实。

## 逐段候选来源自查（不等于整稿采用）

全片未完成时，`status` 返回的 `next_check` 即局部命令：

```sh
python -m scripts.core.story_sources --project <项目> --narrative <完整已确认叙事JSON> --storyboard <同一候选导演JSON> --candidate-paragraph-id STP-01
```

每个已写 STP 重复一次 `--candidate-paragraph-id`，范围覆盖累计候选内所有已写镜头；跨段镜头须把全部来源 STP 纳入。结果标明 `whole_package_validated:false` / `formal_adoption_allowed:false`，只帮助定位，不证明跨段语义；前后状态、远处铺垫与衔接人工核对，未决处留在 `open_questions` / `asset_gaps`。全片写完去掉局部参数重跑默认严格校验，之后才走验收与采用；局部参数不传入 adopt-package/commit。

历史已批准正文用作隔离候选输入时的证据分层见[隔离试验细则](../../docs/rules/experiments.md)，仅在明确授权 candidate-test 时适用。

## 视觉生产衔接细则

涉及资产、按需分镜或场景状态时，读取 [视觉生产与资产调整](../../docs/rules/visual-production.md)，仅执行本职及已授权分支。
