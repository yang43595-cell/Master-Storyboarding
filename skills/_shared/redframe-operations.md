# 红帧操作

本轮选用红帧并获准平台操作时读取。先核对业务项目登记的渠道、目标和操作范围。

## 连接与登录

- 通过可达内网的本机连接。先读本机 `~/.local/share/codex-headless-browser/README.md`，默认由实际配置为 `gpt-6.1-terra` 的执行者使用 `task_browser(redframe=True)`；用户在本轮明确指定其他执行者时，按其指定执行，并在业务项目记录中登记指定原话与范围（2026-11-13 普拉提 vlog 案例即由用户指定 Muse 本人执行）。登录态失效时走 `redframe_login_browser()`，由用户本人登录，再在新后台会话中验证。会话只保存在本机。
- 页面打不开时先查内网连通性、域名解析及代理分流；权限、节点插件或原生控件缺失时，记录具体缺项并暂停受影响操作。页面卡住或报错先刷新，再核对原项目已保存范围；结果未知先只读确认，避免重复提交。具体按[刷新与重试](../voiceover-to-video-script/references/redframe-canvas-layout.md#卡住或报错先刷新)执行。

## 生图

1. 剧情制作打开登记的红帧画布，在同一画布的图片节点中制作或补图。剧情人物选 Doubao Seedream 5.5，开工核对原生模型控件和授权。独立生图可用[对话创作](https://redframe-ai.xmpaohong.com:28888/#/creation)。其余图片按本轮获准模型和规格执行。
2. 同批沿一个后台会话完成上传、提交和取图。按控件就绪和参考预览到位推进。人物资产从本批 `task_basis.json` 取对应模板及灰模，先传实际参考，灰模最后，等原生引用落位再填正文。灰模只负责位置和大小。
3. 提交前在同一页面用 `RedframeWorkspace.wait_for_image_request(node_id, prompt, reference_sha256, parameters)` 核持久化正文、原生引用及其原件顺序、`model/size/ratio/count`，并记录返回的旧结果路径。核实后提交一次，再用 `wait_for_image_result(node_id, previous_output)` 等同一节点的新结果。超时先查原任务。取原图、核宽高比和实看后登记本批回执；统一保存或布局验收时再独立重开。
4. 后台命令负责有界等待，向模型只回节点 ID、阶段变化、结果路径或失败摘要。沿同一进程续等，不逐次上报空轮询，也不反复打印画布、编辑器 HTML 或图片字节。只在异常时取关联节点细节。

## 视频画布

从[我的画布](https://redframe-ai.xmpaohong.com:28888/#/projects)打开登记的项目，核对项目和子画布。新建按本轮授权执行，并登记实际地址和项目 ID。项目使用 Seedance 2.5，其余参数核对开工记录和当前原生控件。

草稿按[红帧交付要求](../voiceover-to-video-script/references/seedance-delivery-package.md#红帧与小云雀的交付边界)和下文写入。首次写入、换写法或批量排版时做[代表段验收](../voiceover-to-video-script/references/seedance-delivery-package.md#红帧代表段验收)，含写实人脸时做[人脸认证](../voiceover-to-video-script/references/seedance-delivery-package.md#红帧草稿的人脸认证)。右侧对话中的 `@` 用于对话引用，视频草稿使用对应视频节点「生成提示词」主编辑器中的原生 `@`。统一流程交付到草稿；获准提交的生成任务结果未知时，先只读查询原任务。

### 成批写入和整节点回读

新平台操作任务按[执行交接](../../docs/rules/execution.md#画布执行与交接的宿主边界)交干净执行者（默认 Terra，用户本轮明确指定其他执行者时从其指定），沿同一任务后台 Edge 会话完成。已定稿草稿没有本轮修改授权时保持只读。正式写入消费同一份已采用源稿与冻结快照。先核[写前内容与快照](../voiceover-to-video-script/references/seedance-delivery-package.md#正式草稿写前检查)，按本页相应字段和操作执行。

1. 写前对实际目标和本批冻结 JSON 运行[正式预检 `--delivery --preflight`](../../docs/development/delivery-snapshot-checks.md#操作与采用)，按返回的 `ok: true`、目标、段号和 `frozen_sha256` 执行。缺图的段记下具体依赖，等生图回执齐备后另批；素材齐备的独立段先写。首次上传可按[新建直传计划](#新建直传计划和回读)只列计划直传槽。更新走[最后可信基线和实际写前读数](#更新前核已有编辑)。
2. 同一批次沿用同一份通过的预检。目标、正文、参数、镜号或引用来源／顺序变化时，只重跑受影响段。素材区复用已核卡片，缺需认证或审阅的卡片才按[素材区上传](../voiceover-to-video-script/references/redframe-canvas-usage.md#素材区上传)补。各草稿从自己的原生「上传参考」控件按序直传，每槽发起一次上传。用持久画布的槽数和有序槽 ID 确认就绪，再写原生 `@` 和参数。异常先读原节点，定位缺失槽后才补。后台命令有界续等，输出仅留段号、状态及异常，完成时交一次回执。
3. 用 `verify_placements` 按实际尺寸一次核本批预留位置。新建时直接放入预留位置，平台落点偏移时才调整。每个新节点先以原节点 ID 确认持久化，再继续批次。节点未读到时按原 ID 只读回查。参数以工作区原生字段为准，按[整节点字段](#红帧整节点的源连线用途与时间)准备并核对。
4. 保存收到回执后，用 `verify_saved` 核实际写入的节点内容，含正文、参数和原生槽。发现旧值先查原节点，确认差异后处理受影响段，暂不启动整批附件下载。全批稳定后独立重开一次，读取受影响节点的全部原生字段，并从当前槽的工作区路径下载原件计算 SHA-256。只对暂态失败槽重试一次。首次直传用 [`finalize-new`](#新建直传计划和回读) 绑定实际槽和出正式回执。重开时一并做布局检查和截图，排版有修改时复查变动范围。已有节点同值复测沿用[写前基线和当前读数](#更新前核已有编辑)，只在授权内保存一次。相同故障持续时保留原节点 ID、最后可信快照和最小复现。

离线比较只消费实际采集值。原生引用字段、输入槽字节下载、持久化、认证传递和布局均需实际执行者在合法平台通路实测。常规直传用现有工作区采集；旧连线按[既有连线草稿](#既有连线草稿)。页面字段采集异常、接线或明确使用 port 模式时，再查[平台采集](#平台采集与离线合同的接缝)。交回差异、结论、截图及原生数据路径，协调方核当前回执与依赖。统一入口到草稿收口，用户点击生成。

下一次正常红帧生产顺带[计量](../../docs/development/token-budget.md#计量)。分别记录首个新建草稿从写入开始到独立刷新核验，以及全片由批量写入开始到草稿回读完成的未缓存输入 token 和墙钟耗时。两组各保留红帧执行者的实际日志起止和完成段数，包含各自窗口内的失败与恢复，窗口重叠时不相加。《摇钱树》沿用已有复测回执作为历史证据，取数使用下一次本来就要做的项目。

### 新建直传计划和回读

红帧正式草稿写入执行者用[正式预检命令](../../docs/development/delivery-snapshot-checks.md#操作与采用)核本批实际目标、已采用视频稿及导演稿、来源编导的确认和本段各引用的采用依据，按返回的段号和快照哈希执行。首次直传尚未取得平台身份时，段内保留 `reference_checks` 的准确文件身份、顺序和直传用途，填写 `edges: []`、`source_nodes: []`，省略 `ordered_media`，`node_id` 暂用唯一的计划标识。`upload_mapping_pending_nodes` 标记仍待平台身份的节点。合法无媒体段明确使用 `ordered_media: []`。

首次直传从创建回执保存段号到真实节点 ID 的 JSON 对象（如 `{"W03":"实际节点ID"}`）。独立重开后，用 `native_draft` 整理实采节点和已下载槽，记录实际目标、项目 ID、修订与 `phase: after_refresh`。用以下命令将实采身份绑定到通过预检的计划，一次出冻结交付、回读和正式检查三份 JSON。该命令已执行正式回读比较，正文、参数和引用顺序仍按原计划核对。

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

红帧新草稿统一用 `direct`，包括产品与动作参考。直传槽不填端口，整段均直传时 `edges` 和 `source_nodes` 均为空列表。素材区供认证和查看的卡片独立于槽位图谱。真实商品识别仍靠已有产品参考登记与实看，不从名称推断。原片裁切与完整画幅按准确文件版本区分。已有实际连线时另读[既有连线草稿](#既有连线草稿)。

正文逐镜秒数范围直接从 `prompt` 读取，例如 `0.00–1.00秒` 和 `1.00–4.00秒`，支持 s/秒及横线、波浪线、至、到。各范围须从 0 连续递增，与 `shot_durations` 一一匹配，最后时间点等于 `parameters.duration`。未写可解析范围、只写总秒数或范围有歧义时拒绝该合同，执行者回准确来源修订并核实，不能在交付稿静默重写已采用正文。该有限格式检查不证明实拍动作足时，也不解析任意自然语言时间。正文仍保留时间不渲染为文字的既有约束。

保存刷新后，读回整个受影响节点的正文、参数、全部输入槽、正文原生实体、源节点 hash 和全部入边。回读 `source_nodes` / `edges` 不分列表顺序，其他原有有序媒体与正文比较保持严格。无正文引用槽只从预期 mentions 中排除，其来源、槽、用途及边继续检查。仅换两份素材也核完整节点。`canvas_input_checks` 给逐节点结论，技术通过仍不认证采集行为、媒体远端来源或视觉效果。

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

### 画布排版

按[红帧画布排版](../voiceover-to-video-script/references/redframe-canvas-layout.md)执行模块分类和行列，并核[命名、尺寸及重叠](../voiceover-to-video-script/references/redframe-canvas-usage.md#命名与排版)。批量改动既有节点时再读[批量修改节点](../voiceover-to-video-script/references/redframe-canvas-usage.md#批量修改节点)。

1. 按红帧副本将人物、场景、物品、分镜、视频生成及视频参考分类成模块，各模块内部保持场景和片段顺序，不按剧情拆成混合资产区。同组卡片对齐，卡片之间只留便于辨认边界和点击的窄缝，组间再留分区间距。节点和连线不得挡住标题或正文。提示词留在视频草稿节点，省去重复文字卡。平台「整理画布」一键排布只作为起点，整理后仍须按模块分类与片段顺序逐项复核调整，不以一键结果为合规排版。
2. 批量铺开前，先选一个代表段按实际卡片尺寸试排。在正常审阅缩放下确认标题可读、素材和草稿便于对照，再按同样方式铺开余段。排版时保留原编号、正文、素材绑定和参数。
3. 保存后重开，检查全景和正常审阅缩放下的关键局部，确认顺序、间距和对应关系都清楚。发现大块留白或遮挡先调整，再重开复核。交付附全景及关键细节截图。

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
