# 红帧操作

本轮选用红帧并获准平台操作时读取。先核对业务项目登记的渠道、目标和操作范围。

## 连接与登录

- 通过可达内网的本机连接。先读本机 `~/.local/share/codex-headless-browser/README.md`，本项目由主对话按[项目专用界面分工](../../docs/rules/execution.md#画布执行与交接的宿主边界)使用 `task_browser(redframe=True)`。登录态失效时走 `redframe_login_browser()`，由用户本人登录，再在新后台会话中验证。会话只保存在本机。
- 页面打不开时先查内网连通性、域名解析及代理分流；权限、节点插件或原生控件缺失时，记录具体缺项并暂停受影响操作。页面卡住或报错时保留当前目标与待写内容；结果未知先只读查原节点，再按原要求刷新并核对已保存范围，避免重复提交。具体按[刷新与重试](../voiceover-to-video-script/references/redframe-canvas-layout.md#卡住或报错先刷新)执行。

## 生图

1. **先上传、当场归位。** 打开已登记的项目画布。按[素材区上传](../voiceover-to-video-script/references/redframe-canvas-usage.md#素材区上传)先把本次的模板图片及已有的对标资产图片放成独立素材卡，同一份可用素材复用现有节点。每个素材卡一落画布就按真实用途命名、排到所属模块的空位，按卡片实际大小留缝，再继续下一项。剧情人物用 Doubao Seedream 5.5；其余按本轮已授权模型和规格。独立资产库、[视频参考直传](#视频画布)及[本地 1×4 拼版](../../PROJECT_RULES.md#人物-14-拼版)分别走各自入口。
2. **建生成节点、接真实端口。** 为本次要出的图建立并命名图片生成节点，当场归位，优先让同模块的素材卡和生成节点按阅读方向可就近对照。从素材卡的实际图片输出端拉线到生成节点的参考输入端口，直到生成节点显示实际参考。共用图片可从同一个源节点接入多项目标；仅需文本或已有明确无对标设计时，按实际适用模板接入灰模等必要参考，无需另找对标图。同批由一个后台会话完成，不为同一目标反复建卡或直传同一参考。
3. **对应提示词，再核输入。** 图片节点主编辑器写本次提示词，用平台原生引用标明每张已接图片的职责及实际对应顺序。人物大头照和服装从本批 `task_basis.json` 取有对标或无对标模板，实际外观参考排在灰模前，只有灰模时从参考图 1 起。灰模只提供主体位置和大小。提交前逐项核实际输入端口、源卡及图片原件、正文里的引用和职责，以及模型、画幅、尺寸与张数。接线后保持画布可读。用当前生成面板及实际持久节点核线、正文、输入、参数和位置，一致后单次提交，不为验前状态正常的图片节点刷新或重开。成图及本批收尾时再核结果与持久化；异常或状态未知先查原节点，不重复提交。视频草稿保存后的整批刷新回读按下文执行。
4. **提交并收口。** 输入、排版和本轮授权核对后，对准确节点提交一次；保存原节点 ID 及可取得的任务 ID。等待同一节点出图，出现超时或未知状态先查原任务，沿原节点处理。成图留在对应节点，检查原件和实际效果，按现有方式送审或交付。发现可见偏差时，保留成图并向用户说明问题和受影响用途，由用户决定重生方向或是否接受现图。在全景及正常缩放下复看本批位置、标题和连线；发现重叠或遮挡当场修正。

接线用实际画布读回源节点、端口、目标及生成面板参考，不凭截图或本地期望值补齐。若现场无法让原生连线成为有效参考，先保存已排好的素材和可核状态，报告准确缺口，不切换为直传代替。提交前记下该节点已有成图路径（如有），结果等待可复用同一节点的 `wait_for_image_result(node_id, previous_output)`。后台命令有界等待，只回节点 ID、阶段变化、结果路径或失败摘要。

### 图片页面共用入口

图片页面动作使用 `scripts/core/redframe_image_ui.py` 的 `image_browser_session` 与 `RedframeImageUI`，由本轮实际获准的界面执行者调用。入口接收本机既有 `task_browser` 工厂，创建并关闭本任务的独立后台上下文，不连接现有浏览器，不运行任意 stdin/exec 常驻循环。控件动作默认 7 秒、导航 30 秒；页面驱动本身失联时退出本任务会话，保留原节点和回执，不依赖循环捕获异常继续操作。不要按进程名批量关闭其他任务或用户浏览器。

适配器使用机场任务实录中的 `data-id`、`image → in` 端口、节点名、生成提示词、引用参考素材、图片序号、生成图片、适应视图和关闭对话控件。创建与上传坐标由执行者在本任务视口中实看选定空位；不使用固定屏幕点。可复用源卡时直接给准确源节点 ID。接线前检查原边，已存在不再画线；提示词通过原生菜单插入引用，随后从真实 `promptModel` 核源节点身份及完整正文。提交前重新核实际连线、模型/画幅/尺寸/张数，并读取源卡原件字节与已确认文件 SHA-256 比较。当前实现可设置已观察到的比例控件；模型、尺寸、张数以实际原生字段核对，不符则保留节点并报告具体差异，不能猜控件或把期望值写成实采结果。

### 已有资产或分镜补图的单图返修

同一资产或分镜请求的一张图打回、沿原生成卡或原参考卡局部修订时，沿本 Skill 的原项目准备、校验与人审范围；已采用的项目走局部修订准备，其他已完成项保留原决定。用户已指出修改方向后，把本项画面要求写入现有单图制作请求的 `prompt`（或该场景的 `scenes[].prompt`），`source_files` 只选本轮适用的底图。空间错、人脸错或后态错的失败图沿[连续性要求](../storyboard-director/references/image-continuity-hard-rules.md#返修参考与固定基准)回到正确母图。多参考按源文件顺序写 `source_roles`。整轮交审仍沿原名单。

只改一句时先用 `python -m scripts.redframe_image_revision prepare-request --project <项目> --previous <原单图回执> --request <新请求.json> --replace '原句' '改后的画面要求'`。它按旧回执的请求哈希在同目录找原制作请求，保留本图参考与职责；多场景按原请求中该场景的独立绑定摘出，其他图不进生产批次。整段改写可用 `--new-prompt` 替换该图正文。未唯一命中时只报缺口。若要换参考或另开输出则按原 Skill 明确选源；换输出编号的新请求注明 `previous_target`。准备和阅读接续后，用同一份请求交给下方生图命令。

```sh
python -m scripts.redframe_image_revision --project <项目> --previous <上一版单图回执> --request <本项制作请求> --receipt <本项独立新回执> --apply --actor-id <实际执行者> --task-id <本轮任务>
```

命令复用已核对的红帧 Seedream 5.5 单图源卡、项目和尺寸，在原图附近的空位建独立候选，接线、写词、提交并保存原件。省略 `--apply` 只核本地请求和原卡，可从已完成项目回放，但不证明当前授权或产前门禁通过；正式运行还核当前准备包和角色。独立批次沿用其 `--context-id`。不另建临时浏览器脚本。提交后中断用同一命令和回执接续，已有完整原件直接实看。仅在建卡结果未知时，按列出的原画布新节点核实归属，再用 `--recover-node <准确ID>` 找回；源卡换了、模型不符或需多张时沿下方原生入口。实看后把具体观察和版本写回原有审图回执；程序的成功表示原件已取回。

### 单图原生调用

下面是单图的调用方式；`target`、`settings`、`source_hashes`、`parts`、空位和回执路径取本轮真实材料。新节点先创建、命名和归位，已有节点直接复用，不重复运行创建/上传。`receipt_path` 是本图现有候选/生成回执，由本批独占写入；不是新增审批账本。

```python
from pathlib import Path
from headless_edge import task_browser  # 按本机 README 加载，不复制会话文件
from scripts.core.io import read_json, write_json
from scripts.core.redframe_image_ui import image_browser_session

receipt_path = Path(receipt_path)
receipt = read_json(receipt_path) if receipt_path.exists() else {"target": target}
with image_browser_session(task_browser, project_id=project_id, canvas_url=canvas_url) as ui:
    if receipt.get("image_submission"):
        result = ui.query(receipt, timeout=60)  # 只查已登记的原节点
    else:
        # 首次需要时：source_id = ui.upload_source(source_file, empty_x, empty_y)
        # 首次需要时：node_id = ui.create_image_node(empty_x, empty_y)
        ui.rename(node_id, node_label)
        for source_id in source_hashes:
            ui.connect(source_id, node_id)
        # parts 中普通字符串直接写入；引用项写：
        # {"index": 1, "source_id": source_id, "text": "本图准确参考职责；"}
        prompt = ui.write_prompt(node_id, parts)
        ui.set_ratio(node_id, settings["ratio"])
        ui.submit_once(node_id, settings=settings, sources=source_hashes,
                       prompt=prompt, receipt=receipt,
                       save_receipt=lambda value: write_json(receipt_path, value))
        result = ui.query(receipt, timeout=60)
    receipt["image_result"] = result
    write_json(receipt_path, receipt)
```

`source_hashes` 为按实际平台槽顺序构造的 `{准确源节点ID: 已确认原件SHA256}`；每个原生 `{{ref N}}` 必须指向该顺序的第 N 个源卡，不能只核源 ID 集合。模型使用原生持久化标识，例如当前已观察的 `model='["volcengineArkVideo","doubao-seedream-5-5-261028"]'`，并填写本轮确定的 `ratio`、`size`、`count`。模型、尺寸或张数不同，由实际界面执行者通过本轮已核的原生控件选择，再以持久化字段预检；尚未核到的菜单不假称已支持。参数示例不授予模型或生图授权。

`submit_once` 在点击前先将准确目标、输入绑定、旧成图路径及 `status: unknown` 写入该图回执；落盘失败不点击，按钮超时不重提。已有 `image_submission` 的回执只可查询；新获准的视觉返修使用原流程的新候选版本和本图回执。查询只返回同一节点的新原图路径，不把按钮返回、图片出现或程序通过称为任务 ID、视觉合格或用户采用。创建/上传中断时，沿此前节点 ID 集合调用 `find_new_nodes` 只读找回新卡，不能因异常重复创建。

该入口及离线测试只覆盖已记录的字段与有界动作协议，尚未证明当前线上完整 DOM、真实鼠标连线、模型菜单、保存持久化及提供方提交仍然有效。下一次正常制作由实际界面执行者用本来要做的首张图核验，保留真实回执；控件缺失、多匹配、视口或遮罩仍阻挡时返回具体缺口，不用合成页面测试冒充线上通过。

## 视频画布

从[我的画布](https://redframe-ai.xmpaohong.com:28888/#/projects)打开登记的项目，核对项目和子画布。新建按本轮授权执行，并登记实际地址和项目 ID。项目使用 Seedance 2.5，其余参数核对开工记录和当前原生控件。

接手续做时，同时核本地现有交付记录及本轮准确节点的持久化数据。平台已有任务或成片时，把可核的任务、结果与文件路径写回原记录，分别说明草稿、平台任务和成片状态。把画布交给用户时附实际子画布地址、对应段号，并说明登录失效时可从[红帧入口](https://redframe-ai.xmpaohong.com:28888/#/projects)用本人账号重新进入。

已有生成回执时，可先离线核对本次范围。交付回执或冻结交付提供段号与节点映射，`--receipt` 传原任务或生成回执，可重复。原交付记录已引用的结果回执用 `--recorded` 指定。归档迁移时可用 `--former-root` 传归档前准确的项目根，重算当前文件指纹。

```sh
python -m scripts.reconcile_video_delivery --project "<业务项目>" \
  --delivery "06_视频/画布交付回执.json" --receipt "06_视频/生成回执.json"
```

按段号、节点和任务将待补结果写回原交付记录，注明实际已校验的文件。同段多版分别保留，已观察的历史结果与当前画布状态分别核对。缺回执、任务状态未知、身份冲突或本机文件未核的范围继续按原节点和任务核实。

草稿按[红帧交付要求](../voiceover-to-video-script/references/seedance-delivery-package.md#红帧与小云雀的交付边界)和下文写入。首次写入、换写法或批量排版时做[代表段验收](../voiceover-to-video-script/references/seedance-delivery-package.md#红帧代表段验收)，含写实人脸时做[人脸认证](../voiceover-to-video-script/references/seedance-delivery-package.md#红帧草稿的人脸认证)。右侧对话中的 `@` 用于对话引用，视频草稿使用对应视频节点「生成提示词」主编辑器中的原生 `@`。默认交草稿。用户已明确授权生成时，沿[已授权生成的取证](../voiceover-to-video-script/references/seedance-delivery-package.md#已授权生成的取证)继续。

### 视频提交结果未知时续查

利用已有写前节点回读，在原提交记录记准确 `node_id`、`previous_task_id`（已读到无旧任务时填 `null`）和带时区的 `click_intent_at`。提交后已取得 `task_id` 时按该 ID 续查。若没收到提交回执且还没有任务 ID，在同一个工作区会话调用 `workspace.read_video_submission(submission)`，直接从原节点的 `data.videoTask` 找任务 ID。它仅返回节点、阶段及可归属原节点的任务 ID。缺已知 ID 且没有写前记录、仍是旧任务、启动时间早于本次点击意图，或尚未读到任务时，保留 `submission_unknown`。发现同节点存在其他提交时保留归属未知。

读到任务 ID 后沿此 ID 查询状态。`task_observed` 只记录原节点上的任务，完成状态、实际生成输入及成片分别按既有证据核验。已完成的节点还可能只在 `outputs`、`draft` 或 `draftHistory` 留有结果。沿原节点核对这些字段与既有提交记录，确证的成片写回本轮交付记录，任务归属未知时仍保留未知。老输出出现或接口超时均继续沿原任务处理，禁止以查询结果未知为由重新点击。

### 成批写入和整节点回读

红帧的上传、接线、调时长和刷新回读默认由主对话用 Playwright 完成。每批只开启一个独立后台 Edge。图片使用 `image_browser_session`，视频使用 `scripts.redframe_draft.open_workspace(task_browser, target_url, project_id)`。在同一个 `with` 块内复用 `workspace` 和页面，按目标节点、槽位、保存回执或控件的实际状态等待。整批保存并收口布局后，在同一会话刷新页面，回读持久化结果。已定稿草稿没有本轮修改授权时保持只读。正式写入消费同一份已采用源稿与冻结快照。先核[写前内容与快照](../voiceover-to-video-script/references/seedance-delivery-package.md#正式草稿写前检查)。新建采用 `scripts.redframe_draft.run_new_draft_batch` 串起本页的原生写入、布局采集和正式回读；它管理整批的同一会话、刷新和槽位下载。现有节点按[更新前核已有编辑](#更新前核已有编辑)处理。

需要在原画布做一次已授权的源卡连线测试时，可在该 `with` 块内这样执行。`source_xy` 和 `draft_xy` 取本轮实看确认的两个空位。已有准确测试草稿或素材卡时，传原节点 ID 并从缺失步骤续作。

```python
from scripts.redframe_draft import RedframeVideoDraftUI, open_workspace

with open_workspace(task_browser, target_url, project_id) as workspace:
    ui = RedframeVideoDraftUI(workspace.page, project_id, target_url, workspace=workspace)
    source_id = ui.upload_draft_reference(reference_path, *source_xy, source_label)
    node_id = ui.create_video_draft(*draft_xy, draft_label)
    ui.connect_reference(source_id, node_id)
    saved = ui.drag_duration(node_id, 9)
    receipt = ui.refresh_linked_draft(node_id, source_id, saved, reference_path)
```

测试入口使用原生素材卡、端口和时长轨道。回执包含刷新后的草稿 ID、实际秒数、源卡/连线身份和参考原件哈希。正式草稿仍走下文的写前预检、原生正文引用和整节点验收。

1. 上传前按[写实人脸认证](../voiceover-to-video-script/references/redframe-canvas-usage.md#写实人脸认证)确认首槽路径和可复用范围，再按[上传前计划](#新建直传计划和回读)冻结每槽方式。同目标、同版来源和同一批就绪段合用一份计划，首次写入调用 `scripts.redframe_draft.run_new_draft_batch`。采用用 `accept-artifact`，由批次入口在打开一次后台会话前执行整批预检。独立排障或已有刷新证据按需使用[正式预检 `--delivery --preflight`](../../docs/development/delivery-snapshot-checks.md#操作与采用)或单段采用附带的 `--preflight-json`。缺图段登记具体依赖，素材齐备的独立段先写。更新走[最后可信基线和实际写前读数](#更新前核已有编辑)。
2. 同一批次沿用同一份通过的预检。目标、正文、参数、镜号或引用来源／顺序变化时，只重跑受影响段。素材区复用已核卡片，缺需认证或审阅的卡片才按[素材区上传](../voiceover-to-video-script/references/redframe-canvas-usage.md#素材区上传)补。各草稿从自己的原生「上传参考」控件按序直传，每槽发起一次上传。已核实须用认证源卡的槽走[写实人脸认证与接线](../voiceover-to-video-script/references/redframe-canvas-usage.md#写实人脸认证)。用持久画布的槽数和有序槽 ID 确认就绪，再写原生 `@` 和参数。异常先读原节点，定位缺失槽后才补。后台命令有界续等，输出仅留段号、状态及异常，完成时交一次回执。
3. 用 `verify_placements` 按实际尺寸一次核本批预留位置。新建用 `RedframeVideoDraftUI.create_video_draft` 的「视频」菜单，保留旧节点、任务和成片，旧结果归位按[批量修改节点](../voiceover-to-video-script/references/redframe-canvas-usage.md#批量修改节点)执行。新节点持久化后核原生 ID 及 `videoTask`、`outputs`、`draft`、`draftHistory` 均无生成记录，再继续写入。节点未读到时按原 ID 只读回查。参数以工作区原生字段为准，按[整节点字段](#红帧整节点的源连线用途与时间)准备并核对。
4. 保存收到回执后，用 `verify_saved` 核实际写入的节点内容，含正文、参数和原生槽。批内中间检查只读这些字段。发现旧值按原节点处理受影响段，布局有偏差先修正，然后在原后台会话刷新，核准确节点及最终布局并截图。布局收口后的同一次刷新采集整批受影响节点，用 `RedframeWorkspace.digest_slots` 一次读取当前各槽原件并计算 SHA-256。工具只重试暂态失败槽一次。已取得的成功槽沿本轮刷新证据交付；仍失败时留下准确槽和错误，后续定向补核该槽，发现原生槽身份、顺序或路径变动才重查其影响范围。批次入口一次绑定实际槽并返回冻结交付、实采回读和正式检查；已有同一批真实采集时可用 [`finalize-new`](#新建直传计划和回读) 独立收口。已有节点同值复测沿用[写前基线和当前读数](#更新前核已有编辑)，只在授权内保存一次。相同故障持续时保留原节点 ID、最后可信快照和最小复现。

离线比较只消费实际采集值。原生引用字段、输入槽字节下载、持久化、认证传递和布局均需实际执行者在合法平台通路实测。常规直传用现有工作区采集；旧连线按[既有连线草稿](#既有连线草稿)。页面字段采集异常、接线或明确使用 port 模式时，再查[平台采集](#平台采集与离线合同的接缝)。交回差异、结论、截图及原生数据路径，协调方核当前回执与依赖。

启用计量验证时，下一次正常红帧生产顺带[计量](../../docs/development/token-budget.md#计量)。分别记录首个新建草稿从写入开始到独立刷新核验，以及全片由批量写入开始到草稿回读完成的未缓存输入 token 和墙钟耗时。两组各保留红帧执行者的实际日志起止和完成段数，包含各自窗口内的失败与恢复，窗口重叠时不相加。《摇钱树》沿用已有复测回执作为历史证据，取数使用下一次本来就要做的项目。

复刻草稿批量落稿时，可用 `scripts.redframe_draft.save_draft_batch` 收口已在原生节点准备好的更新。首次上传前完成[深度切片](../depth-guided-recreation/references/depth-video.md#红帧首轮写入前导出)。局部返修从可信回执固定原产品卡 ID 和字节依据，先核本轮基线，再上传实际变化的槽。替换长视频时先按原生身份定位并移除旧槽，再传新版，避免新旧片同时占用参考时长。上传结果不明时先回查原节点的槽位差异。传入修改前的基线、上传后的准确节点、修改字段、有序附件哈希和受保护的卡 ID。一次合并保存后共用同一刷新图核受影响节点、产品卡和连线，一次读取各目标的有序附件字节。保存冲突时先检查原节点；同一次读回返回的 graph、slots 直接用于布局与交付核对。

### 新建直传计划和回读

批次中断后用原计划重进同一入口。已写完的段只回读，未完成的段用 `resume(ui, draft, node_id)` 补写原节点。若回执显示创建结果未知，先刷新核对原画布，再用 `python scripts/redframe_draft.py resolve-creation --project <项目> --plan <原计划> --segment-id <段号> --node-id <核实的原节点>` 绑定。核实未建卡时使用 `--confirm-absent`。

`run_new_draft_batch` 的 `write` 和 `check_layout` 使用同一个工作区，认证源卡批次再传同次刷新取证的 `collect_linked`。入口核实际目标、准确编导和导演、已采用视频稿及各槽来源，最后返回 `frozen`、`observed` 和 `formal_readback`，按现有交付目录保存。尚未取得平台身份时，段内保留 `reference_checks` 的准确文件身份、用途和顺序，填写 `edges: []`、`source_nodes: []`，省略 `ordered_media`，`node_id` 暂用唯一的计划标识。含图片或视频参考的段在实看后列 `realistic_face_slots`（槽号从 1 开始，无写实人脸填 `[]`）。图片认证槽在对应 `canvas_binding` 填 `mode: edge`、`source_port: image`、`target_port: in`，普通槽按其计划走直传。`upload_mapping_pending_nodes` 标记仍待平台身份的节点，`face_source_card_slots` 标记按段计划走认证源卡的槽。合法无媒体段明确使用 `ordered_media: []`。

含认证源卡的段先核同字节源卡认证或按原授权补卡，经认证后接线，普通槽再按冻结顺序对应直传。保留 `realistic_face_slots`，绑定实际节点与槽身份，按[整节点回读](#既有连线草稿)校验实际连线、源卡认证、原件字节及原生 `@`。预检的槽号只表示计划路径。下述 `finalize-new` 用于纯直传段。

首次直传从创建回执保存段号到真实节点 ID 的 JSON 对象（如 `{"W03":"实际节点ID"}`）。本批刷新后，用 `native_draft` 整理实采节点和已下载槽，记录实际目标、项目 ID、修订与 `phase: after_refresh`。用以下命令将实采身份绑定到通过预检的计划，一次出冻结交付、回读和正式检查三份 JSON。`--preflight` 可直接使用 `accept-artifact --preflight-json` 的完整输出，也可使用独立预检报告。该命令执行正式回读比较，正文、参数和引用顺序仍按原计划核对。

```sh
python scripts/redframe_draft.py finalize-new --project <项目> --plan <写前计划JSON> \
  --preflight <通过的预检报告JSON> --segment-nodes <段号到真实节点JSON> \
  --readback <实际刷新后读数JSON> --output-dir <本批回执目录>
```

### 红帧整节点的源、连线、用途与时间

新红帧正式提交根据开工 `canvas.platform=redframe` 或准确红帧域名启用整节点检查。其他平台显式带下列 `edges` / `source_nodes` 时同样核验。历史无项目的只读比较保留原范围，相关结果为 `not_checked`，不能充当新红帧交付通过。仍使用同一冻结 JSON、现有参考来源和原生回读。

每个 `reference_checks` 沿准确槽位增加 `canvas_binding`。`purpose` 写具体镜号和本次用途，同一文件 SHA-256 与同一用途重复占槽即拒绝。同文件确实承担不同用途时分别说明，观察和确认范围仍须覆盖。`prompt_reference` 指明是否出现在正文原生引用中，保留无正文引用的槽须有 `unmentioned_reason`。执行者核用途语义，程序只能核声明一致性。

```json
{
  "canvas_binding": {
    "mode": "direct",
    "purpose": "S01-003 产品外观",
    "prompt_reference": true
  }
}
```

红帧新草稿默认用 `direct`，包括产品与动作参考。已核实须用认证源卡的槽按[写实人脸认证与接线](../voiceover-to-video-script/references/redframe-canvas-usage.md#写实人脸认证)执行。直传槽不填端口，整段均直传时 `edges` 和 `source_nodes` 均为空列表。真实商品识别仍靠已有产品参考登记与实看，不从名称推断。原片裁切与完整画幅按准确文件版本区分。已有实际连线时另读[既有连线草稿](#既有连线草稿)。

逐镜时长从制作字段 `shot_durations` 读取，合计须等于 `parameters.duration`。新稿正文按镜头顺序写视听过程，省略逐镜秒数区间。画布正文须与采用稿一致。

**2026-11-21 用户更正：视频节点的时长是拖动轨道，不是固定时间。** 生成面板里的 4/8/13/17/21/26/30 秒等只是轨道上的快捷档或显示值，实际时长由拖动时长轨道设置；必须按稿内准确秒数拖动，再以节点原生持久化字段回读核验，不能把 6/7/9 秒四舍五入成固定档位。

保存刷新后，读回整个受影响节点的正文、参数、全部输入槽、正文原生实体、源节点 hash 和全部入边。回读 `source_nodes` / `edges` 不分列表顺序，其他原有有序媒体与正文比较保持严格。对比原始画布连线时按边 ID 核两端节点、端口及其他绑定字段。红帧会重算 `sourceX`、`sourceY`、`targetX`、`targetY`，这些字段用于绘制连线。布局验证另核实际画面。无正文引用槽只从预期 mentions 中排除，其来源、槽、用途及边继续检查。仅换两份素材也核完整节点。`canvas_input_checks` 给逐节点结论，技术通过仍不认证采集行为、媒体远端来源或视觉效果。

### 更新前核已有编辑

交付操作前先只读定位节点并核画布URL、原生node_id、节点标题及当前媒体身份；定位不等于切换右侧面板，不以可见面板内容猜当前目标。确认定位与面板确属该节点后，备份实际当前正文、参数、有序原生引用及结果身份，再进行另有授权的修改。面板不能可靠关联时停在只读核查，不全选、不清空、不重试写入。

沿现有回读文件保存最后可信 `after_refresh` 基线，在实际写前采集同一目标全部受影响节点的 `before_write`。两份数据都须包含完整正文、参数、有序输入及 native_mentions，每个节点还必须显式提供列表类型的 edges 和 source_nodes，并保存本轮采集到的其他节点字段。缺少图谱字段即拒绝，显式空列表表示实际回读的空图谱。更新预检用原命令附加两个只读输入。

```sh
python scripts/canvas_readback.py --delivery --project '<项目>' --frozen '<冻结JSON>' \
  --preflight --baseline '<最后可信原生回读.json>' --current '<本次写前原生回读.json>'
```

程序核目标 URL、项目 ID、准确节点集合，比较完整节点中所有已采集字段，变化返回非零及 changed_node_ids。没有实际采集的字段仍未验证。执行者先核已有编辑的准确范围，正文、来源或时长变化沿现有修订和采用处理，再取得可信基线，不能用当前读数覆盖旧基线求通过。新建节点无历史基线时明确记录创建范围。参数配对及差异可程序核验，更新任务必须使用此比较、采集完整性、用户编辑归属及检查后的并发修改仍由执行者/平台负责，CLI 不是远端事务锁。

### 既有连线草稿

既有连线快照和其他平台按实际 `edge` 绑定核对。绑定填写 `source_port`、`target_port`，源身份来自本槽 `ordered_media.node_id`。逐 draft 的 `edges` 保存全部实际入边，每项严格包含 `source_node_id/source_port/target_node_id/target_port/slot`，slot 从 1 开始。`source_nodes` 保存相应源输出的 `node_id/port/sha256`，同源输出只列一次。程序将源、端口、目标、槽和原片文件 hash 逐项核齐，缺边、多边、错端口、同源输出指向不同字节均拒绝。其他平台采用本合同核产品与动作引用时仍用独立源节点连线。

红帧正式回读中，有 `realistic_face_slots` 的段在刷新后逐槽采集 `face_certifications`。以槽号字符串作键，每项填实际源卡 `node_id`、当前源输出的 `source_url` 和该卡原生 `data.faceCertification`（字段名填 `faceCertification`）。从同一源输出下载的 SHA-256 填入该槽的 `ordered_media`。现场确认卡片显示「人脸已认证」。离线检查对照同一源卡、当前输出、原件字节和原生认证字段，缺失或不符时该槽不能通过正式回读。视频沿实际核得的原生字段采集，图片的 `imageDigest` 须匹配原件。结果见 `face_certification_checks`，回读通过后的生成授权仍沿原入口核实。

### 画布排版

按[红帧画布排版](../voiceover-to-video-script/references/redframe-canvas-layout.md)执行模块分类和行列，并核[命名、尺寸及重叠](../voiceover-to-video-script/references/redframe-canvas-usage.md#命名与排版)。批量改动既有节点时再读[批量修改节点](../voiceover-to-video-script/references/redframe-canvas-usage.md#批量修改节点)。

布局以链接中的唯一正文为准：先识别当前成果、制作来源、待确认候选与历史版本，再按模块归位。人物板、状态图、灰模、产品实拍、动作视频、图片生成节点、视频草稿和结果按各自用途组织；视频交付检查整个相关工作区，不能只排新增的视频节点。保留原生引用和真实连线，不为排版重复上传或改写内容。

试排、模块行列、纯布局前后比较、全景及正常缩放验收，以及用户明确不刷新时的证据范围，均按[执行与验收](../voiceover-to-video-script/references/redframe-canvas-layout.md#执行与验收)办理，不在本入口另维护一套排法。

## 平台采集与离线合同的接缝

仅在接入采集器、定位异常，或本批明确采用非默认原生身份模式时读本节的对应小节。实际采集仍沿本轮获准的独立后台会话和准确节点。

`canvas_readback.py` 消费实际采集的 `after_refresh` / `drafts`，并复核现有 `ordered_media`、`reference_checks` 与登记中的 `upload_bindings`。它不是平台采集器。`rhtv_target.js` 的通用 DOM text/value/attributes 读取只能证明所读字段，不能单靠标签、截图、连线或一次保存回执产生 `native_mentions`、媒体字节来源或刷新后持久化的事实。

接入准确平台时，先用已有获准只读入口确认目标、刷新动作和平台实际支持的原生节点/媒体/不可编辑实体字段；采集结果按现有字段交给 `canvas_readback.py --readback`，不新增平行绑定表。真实目标、完整正文/参数、有序原生身份或刷新事实缺一项即保持未验证；不能猜 selector、补空字段伪装“无引用”或由本地期望值生成 observed。换图同名、顺序交换、换修订、目标变化均重新采集并比对，不继承旧通过。

没有合法可用采集入口时，停在离线合同与 fixture 测试：可以验证接口拒绝反例，但必须写“线上绑定未验证”。Fixture 的 `.invalid` 目标和合成原生实体不是实际小云雀回执，也不授权写画布或生成。现有 `native_binding` 状态、来源 hash 和 confirmation 边界不变。

### 红帧富文本的局部只读采集

`rhtv_target.js` 新增结构化 `read-rich-editor`，仍要求明确目标和唯一编辑器 selector，沿原入口的导航检查执行，不接受任意 JS、不模拟 userGesture、不点击、不刷新、不调用平台内部 API：

```json
{"operation":"read-rich-editor","selector":"<本轮已只读核实且唯一的编辑器 selector>"}
```

已有合法浏览器读取入口也可将 `scripts/rhtv_rich_editor.js` 的自包含 `readRichEditor(root)` 作为准确编辑器 locator 的只读 evaluate 回调；不能把本仓库 CDP 可用性当作当前云浏览器已连接。先验证编辑器确属所选节点，刷新事实由实际操作证据另行建立，函数不补写 `phase=after_refresh`。

本轮实际观察到 `richWrap` 编辑器内的不可编辑 `richCustom` 标签，其后代公开 `data-reference-id`（URI 编码的源 node ID 与 image 类型）及 `data-value={{ref N}}`。采集按 DOM 顺序提取这些字段，沿最近不可编辑祖先核归属，不猜固定嵌套深度。可见标签、缩略图 blob URL、源 node ID 都不等于稳定 media ID 或文件 hash；输出 `native_mentions` 沿既有字段，但缺失的 `media_id` 不回填。直接交现有 `canvas_readback` 仍不能通过，`coverage.media_identity=unchecked`、`production_ready=false`。

`tokens` 仅是采集表示，不是新业务正文或绑定台账：普通 text node 逐字保留空白/FEFF，相邻文本节点合并；BR 与 DIV/P/SPAN 边界显式保留；完整原生引用替换成带 node/type/data-value 与准确 textContent 的 token；先检查其展示子树只含支持的 SPAN/IMG/Text，再跳过布局序列化，保留标签及额外文本变化，避免 `innerText` 的 CSS 换行抖动。禁止全局 trim/去空白、把 tokens 拼回已确认 prompt，或用期望值补 observed。未知结构/冲突实体返回未检查并清空不完整结果。普通块标签支持是保守通用实现，尚无当前红帧完整 DOM 样本证明覆盖；测试只来自已观察的脱敏属性合同与明确合成嵌套/正文，不是完整线上回执。

局部采集也支持已观察到的 `P/richGrid`、`SPAN/richMark`、`SPAN/richWrite`。完整正文与参数、媒体字节身份、刷新后持久化及 frozen 修订仍须各自取得真实证据；该局部适配不宣称线上端到端绑定通过，也不改现有比较器或生成门槛。


### 红帧 node + 输出 port + 实际输入槽下载（显式分支）

默认 `media_id` 合同不变。平台公开 DOM 只提供 `[node UUID, output port]` 时，不能把第二项当媒体 ID；已观察的 `imageReference` 可指向 `image` 或 `main` 输出。只读 reader 保留 `reference_port`，不推断媒体 ID。

当前段可显式声明 `native_identity_mode: node_port_download`，`ordered_media` 每项严格为 `node_id/reference_port/sha256`，并在原有 reference 的 `upload_binding`（正式来源在 manifest 的 `upload_bindings`）绑定实际证据。新分支必须带 project，核准确目标 URL、项目目录、草稿修订/节点/segment、槽位、源修订与文件 hash。`evidence` 引用同一次刷新后的原始项目定位、编辑器实体、逐输入槽正常 UI 下载收据及源节点输出 port DOM；每份报告和实际下载文件必须在明确项目根内、字节 hash 相符。scope 还绑定三个原始报告 hash；槽位顺序、不可编辑实体、原始 percent-JSON tuple、token、下载收据对应实体以及 source output handle 必须交叉一致。任意本地路径/hash、图边、源节点缩略图或同名文件都不能代替该输入槽下载证据。

源节点在视口外时可以不渲染 IMG；不以另一处缩略图同时可见为门槛。此时仍须有真实源节点/输出 port 和实际输入槽下载的完整证据。新分支的回读实体使用 `download_sha256`，旧分支仍须真实 `media_id`，二者不混填。物理商品版本、图像用途 QA、上游权威、场景状态、稿件审核和生产授权检查不因此豁免。

这只是采集证据的一致性校验，不认证采集行为，不从调用方修订标签推出平台存在该修订号。不得把正文做全局去空白后称 exact；DOM canonical tokens 可分别证明实际刷新前后结构与文本保持，不能把 token 串回填原稿或用预期值补 observed。原始正文比较仍保留差异；私有候选通过也不意味着正式交付/生成就绪，所有结果仍 `production_ready=false`。

实际使用严格首尾帧另核[端点独立证据](../../docs/rules/experiments.md#已核原生首尾输入的严格候选路径)。

### 旧 RHTV 工具接线（按需）

旧 RHTV 工具必须显式设置 `RHTV_CANVAS_URL` 或 `RHTV_TARGET_ID`，两者同时给时须同时匹配；无匹配、多匹配或导航离开均停止。`rhtv_cdp.js` 不再执行任意 JS stdin，而通用读写只接受结构化单步 JSON：`operation` 为 `read`、`click` 或 `set-value`，给准确 `selector`；`set-value` 另给字符串 `value`，只支持普通 input、textarea、select。`read` 可用 `attributes` 列所需原生属性，`all: true` 只用于明确多项只读。可给 `wait_ms`（0至10000）等待元素；每次等待后重验目标并重新定位，最终同步作用及每次事件派发前再核对 URL/文档身份。零匹配、多匹配或导航时停止，不通过正则猜任意代码是否读写。需要平台原生chip等复杂写入时先实现专用已验证适配器，不回退任意JS。调用失败不能证明尚未写入，已提交操作可能部分生效，不自动重试；事后报错不撤销已有作用。旧任意表达式调用须迁移至上述受限入口，导航后另起一次操作。此处只适用于已经选用且获准的旧 RHTV 入口；当前红帧草稿走工作区原生采集，小云雀按当前平台实际入口。
