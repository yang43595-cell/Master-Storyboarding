# Agent 执行效率与代码体积减重 · 2026-11-25

治理批次 `Agent-Execution-Efficiency-Reset`。首轮本地定向验证通过，随后独立审查判为 FAIL。本次修复批次独立审查仍为 FAIL，分镜、宿主续等和复现命令通过，资产证据判定仍有一项 P0。详见文末。分支 `codex/agent-weight-reduction-2026-11-25`，HEAD `46c4828c150fd46d48ab0f71d0a3aea160987e63`。基线取进场时的工作区，含 19 份未提交修改。下文首轮减重数据以当时的 19 份修改为基线。文末修复批次另以该轮进场工作区为基线。

## 本批实际改动与测量

| 入口 | 原因与实施 | 验证结果 |
|---|---|---|
| 单图返修及其他长命令 | 已有单图 CLI 能从回执接续。此次把空终端会话的第一次续等移进同一个宿主调用，外层覆盖两次等待。原进程有输出时交回；超过窗口仍交回原 ID。单图 Skill 直接链接可执行范例，原生恢复说明保留一份，含人脸认证分支。 | 无浏览器的命令静默 31 秒后输出标记并以 7 退出。一个外层调用取得结果，内层先启动一次，再续同一个 session 一次，未把非零退出吞作成功。[宿主调用范例](../rules/execution.md#等待与工具回传) |
| 资产网页写词及只读复审 | 原 CLI schema 只规定检查数组长度。模型写对证据却用中文栏目名，会触发全包补查。现在由原检查 ID 构成 schema 的必填键，模型填写内容，程序按原顺序输出旧版记录格式。空白证据继续算缺项。只读复审从本轮输入绑定被审原文，旧格式意外返回改稿仍沿已有分支处理。 | 用同一服装原稿、7 项模拟观察跑改前和改后 worker。旧数组首答用中文栏目，补查后通过，2 次 CLI；新对象首答通过，1 次 CLI。模拟 CLI 接收的完整指令 UTF-8 字节合计为 138,802 + 139,000 → 132,865。两端输出同一原文、7 项证据，均未批准生图。新格式持续缺一项时补查一次，最终仍退回。[模型边界](../../scripts/visual_asset_codex.py) · [回归](../../scripts/tests/test_visual_asset_library.py) |
| 资产网页批量准备 | 批次过去每隔一秒读一次任务文件。现在在锁外等待刚启动的原审查线程，以 run ID 和终态判断本项结果。单项 HTTP 调用照常即时返回。 | 双项并行夹具把一秒 sleep 设为直接失败，两项仍成功。失败项保留具体原因，运行 ID 已被替换的项不会被后来一次通过冒领。外部生成任务仍按任务状态、退避及原结果核验。[批量入口](../../scripts/visual_asset_projects.py) · [回归](../../scripts/tests/test_visual_asset_projects.py) |
| 资产 Web 任务包 | 只读模型无需承担宿主等待工作，写词和盘点投影省去执行分工的“等待与工具回传”章节。其它执行分工原文、实际适用合同、模板、动态知识和专业方法仍送达。草稿和本次模板正文逐字相同时，输入直接指向同包正文；用户编辑过的草稿单独提供。组包证据消费刚读的 profile 与本包检查项。普通物品只组装其适用指令一次。 | 源文逐节对照覆盖独立库、项目写词与盘点；进场版本的宿主章节为 4,582 UTF-8 字节。缺方法、缺模板、来源不符仍阻断。项目受限入口无需额外跑普通 prepare。[受限合同](../../skills/asset-image-assistant/modules/web-input-contract.md) · [输入测试](../../scripts/tests/test_visual_asset_reading.py) |
| 阶段 review-pack | 在一轮打包里，从已加载的本任务依据取一次阅读证据，供当前输入核验、采用知识身份及审核包引用共用。失效时仍分别报告“无有效快照”与“已登记的候选证据无效”。 | 两侧对同一临时分镜项目打包。task_basis.json 4→2 次，method_reading_evidence.json 4→1 次，method_reading_packet.md 3→1 次。最终 CLI 结果相同，审核包和 scope 文件字节相同。无必读方法的编导夹具里，task_basis.json 同样由 4→2。未跨调用缓存。[打包入口](../../scripts/stage_context_cli.py) · [身份与漂移测试](../../scripts/tests/test_review_identity.py) |

上述 CLI 次数是离线调用边界的计数。真实宿主内一次 CLI 仍可能产生多条模型响应。字节数只计算传给 CLI 的文本，不含系统提示、图片或 schema；没有换算为生产 token、费用或耗时。

## 生产代码与兼容

六份运行时 Python 文件相对进场工作区净减 **26 行、1,706 字节**。其中现行入口净减 5 行、1,217 字节；旧项目 `production_plan` 预检净减 21 行、489 字节。测试、决策记录和本报告单列，不抵扣生产代码。

- **有证据可删除的内容。** 大头照和服装早已在 `executable_template` 的人物分支返回。核过 recipe、实际模板来源及返回路径后，删除后面两条永远匹配不到这两类模板的旧画质词替换。四视角环境的原模板变换、历史视角标签和显式旧来源校验保留。阶段候选核对直接比较原有记录，删除仅转发一次比较的私有函数。
- **完全相同的实现。** 资产网页沿用同一项目模块的时间格式和 ID 生成；同目录模块直接导入。Skill 文件仍按真实路径动态加载。旧版生产计划的段内和跨段预检共享同一组转场字段，保留各自的错误码、位置与判断。
- **可发现的消费者。** 检查了 scripts 的 Python 定义和引用、Skill 与共用页命令、argparse 分派、受限 Web 任务入口、网页任务线程、CLI 脚本、测试和历史读取。单图 CLI、资产库后台任务、外部回执接口、网站决定及旧项目预检都有非 import 消费路径。没有凭静态扫描删除整支脚本、外部字段或历史读取分支。重复的产品派生图逐像素验证涉及受保护文件和反向模块依赖，留待边界合适时处理。
- **已完成批次的边界。** 本次 `stage_context_cli.py` 的实质改动在 review-pack 和一个私有比较转发处。已审的启动包、锁前预检、比较型哈希、画布比较、正式回读和 OverflowError 修复均未重做。本批与既有文件修改只有三处文件重叠，即 stage_context_cli.py、test_stage_context_cli.py 和 test_visual_asset_library.py，新增改动可按 review_pack 及资产审查用例定位。三份指定原文件以进场字节复核；既有整份工作区补丁可在最终工作区反向 dry-run。没有提交、推送或暂存。

## 历史请求逐条核对

原始日志取自主制作聊天 `01a207d7-75d2-7331-bfbe-f460658cc020`。以 `token_usage_record` 的 turn 和 response 身份计数，结合 response_item 的调用、回执、用户插话与终态定位原因。数字为各轮主对话响应序号。日志能恢复新增输入、调用及返回，不能重建服务端每次完整提示词；下表记录影响判断的输入和输出。相同处理的多个序号列在同一行。仅返回 cell/session 身份是宿主状态变化，不是业务阶段前进。

### 候机厅单图返修

轮次 `01a20990-ae1c-7203-92f2-bf647a807ddf`，41 次，459.097 秒，14,569,266 个累计 token（含缓存输入）。25 次 exec、15 次 wait、1 次最终答复。15 个工具回包只有运行中的 cell；另有 8 个回包只转交了空输出的终端 session。触发这些回包的部分请求同时启动了实际工作，两个计数不能相加当作可删的业务请求。

| 请求 | 目的、输入 → 返回 | 新事实或状态；合并依据 |
|---|---|---|
| 1 | 用户要求座位大部分坐满、增加行李 → 查原场景规则、旧卡、排版与引用 | 定位底图、范围和须保留的走位。保留创作判断，已有同版输入可复用。 |
| 2 | 按整轮旧请求写新提示词、另设输出 → 写请求并尝试 prepare，交接范围报错 | 有新画面要求，也写了项目请求；一个目标扩成整轮造成返工。现有单图 prepare-request 可沿旧目标起步。 |
| 3 | 查 handoff 和适用决定 → 得到原输出范围 | 这是范围冲突的诊断；已知目标时直接用程序定位。 |
| 4 | 按结果缩为单图 → 本轮准备及阅读身份通过 | 有效状态改变。保留对正确请求和实际阅读的门禁。 |
| 5 | 手写浏览器脚本、启动建卡 → cell | 启动了真实任务。现行单图入口已封装正常路径；此处可同调用等结果。 |
| 6、10、13、16、20、25、29、33 | 各自用上一步的 cell 等待 → 八个约一秒的空 session 交接 | 八次均无新业务事实、无决策。宿主可直接承接 session，真正的读回或错误仍要处理。 |
| 7、17、21、26、30 | 用各自 session 等同一进程 → 外层提前返回 cell | 只启动内层等待，收到的仍是句柄。外层覆盖内层即可，无需模型选择新动作。 |
| 8 | 等待 #7 → 建卡持久化结果未知 | 新阻塞。保留原回执与只读找回，不能新建替代卡。 |
| 9 | 同回执再次运行原脚本 → cell | 尝试恢复；应从建卡未知分支接续，不能假定首次未成功。 |
| 11 | 用 #10 的 session 续等 → 恢复代码的唯一节点断言失败 | 有定位意义；保留节点身份判断。 |
| 12 | 读取原图节点集合 → cell | 启动必要的只读核查，可等到有内容后返回。 |
| 14 | 用 #13 的 session 收结果 → 未发现新增候选，前后计数均为 50 | 有新事实，数量相同本身不能认领或授权重建。 |
| 15 | 修改临时建卡逻辑并运行 → cell | 涉及写入；归属不明时必须守住 creation_unknown。现场脚本不作为现行安全实现。 |
| 18 | 等待 #17 → 布局写前遇到 canvas_changed | 真冲突。保留写前检查，只核受影响操作。 |
| 19 | 再次运行脚本 → cell | 续作已有节点，重复进入先前步骤可省；不省略后续编辑与提交检查。 |
| 22 | 等待 #21 → 编辑器焦点超时 | 新阻塞，现行单图入口只刷新并重绑该节点编辑器。 |
| 23 | 读适配器局部及当前回执 → 有节点，正文空，尚无提交记录 | 新信息区分提交前与提交后；定位后可按已知状态恢复。 |
| 24 | 修改脚本，尝试接续编辑及控件设置 → cell | 实施恢复。现行入口承担机械操作，真实提示词仍由创作者决定。 |
| 27 | 等待 #26 → 模型参数保存未确证 | 真实阻塞，必须核实持久参数。 |
| 28 | 再运行已有节点脚本 → cell | 可由确定性程序核当前控件，只处理未完成部分。 |
| 31 | 等待 #30 → 画幅保存仍未确证 | 真实阻塞；模型匹配不能证明画幅匹配。 |
| 32 | 调整控件保存等待并运行 → cell | 启动有实际写入可能的动作；保留单次提交约束。 |
| 34、35 | 等同一 session、再等外层 cell → 仍为 cell | 没有新业务结果；#34 同时向用户说明进度，沟通与查询可分开。 |
| 36 | 等待 #35 → 首次收到“已提交”及节点 ID | 真状态变化，仅代表已提交，应继续查原任务。 |
| 37 | 同一 session 完成 → 刷新核对原节点、候选路径、原件字节和尺寸 | 必须保留的生成完成检查。 |
| 38 | 对原件制作预览并实看 → 返回图像 | 新视觉证据。人流密度、座位和通道是否合用需要判断。 |
| 39 | 将具体偏差绑定该版图片并 show-round → 交审清单 | 交审状态改变，不能以程序成功代为用户确认。 |
| 40 | 读已核回执、原轮次计量并清理临时物 → 本轮记录 | 计量为当时用户要求；同版已核字段可引用，不必重复读图。 |
| 41 | 用实际画面和回执答复 → 提交候选并说明预留座被占 | 用户获得新结果和用途限制，保留。 |

### 第 4 组替换产品料体参考

轮次 `01a20976-3ad9-7db2-8deb-3bd45993119f`，16 次，192.455 秒，4,917,491 个累计 token。这里有一次真实的结构断言失败，也有 4 个只回 cell 的工具回包。

| 请求 | 目的、输入 → 返回 | 新事实或状态；处理 |
|---|---|---|
| 1 | 用户指定的已确认参考、产品包、04 回执 → 准确原图与旧槽 | 保留实物版本、授权和目标身份。 |
| 2 | 发起读取原节点与边 → cell | 真实只读操作，可等待结果。 |
| 3 | 等 #2 → session 和临时目录 | 目录不是业务结论；可合并等待。 |
| 4 | 续 session → 原生引用、边和基线 | 必须保留原生身份。 |
| 5 | 定位用户原话、方法、后台浏览器说明 → 来源与操作范围 | 首次读取必要；同版已读条目可复用。 |
| 6 | 准备并运行局部换参考脚本 → cell | 启动定向写操作，不等于完成。 |
| 7、12 | 等各自 cell → 空 session | 无新业务数据；随原调用承接。 |
| 8 | 查 NativeTag 与当前临时文件 → 确定解析数据形状 | 真实诊断，仅查相关结构。 |
| 9 | 修正对解析数据的断言并查阶段文件 → 当前执行进度 | 修改的是临时脚本，不能由此推定运行中的进程已修好。 |
| 10 | 收原进程 → 边集合断言失败 | 真错误；只恢复受影响项。 |
| 11 | 修正比较方式，运行局部任务 → cell | 有新的恢复动作，保留对其他草稿的保护。 |
| 13 | 等原 session → 运行中 cell | 无独立判断，宿主接续。 |
| 14 | 收结果 → 局部替换及刷新回读完成 | 新结果，原生引用、槽位与刷新均保留。 |
| 15 | 核对回执和其他组，写计量 → 其他七组一致、零视频提交 | 范围及业务边界证据，不能由一句“成功”替代。 |
| 16 | 返回目标草稿与核对结果 | 完成交付沟通。 |

### 编导局部改戏及用户中途补充

轮次 `01a208f0-d546-7372-95dd-99c7f164c777`，26 次，3,373,619 个累计 token。原始 task_complete 为 **854.219 秒**；“改成暗示”的补充消息到结束约 **415 秒**。不能把整个回合的 26 次与后半段的 415 秒当成同一起点。下表只计主对话；独立编导的创作请求不在此数内。代理通信正文在日志中不可直接读出的部分不作逐字推断。

| 请求 | 目的、输入 → 返回 | 新事实或状态；处理 |
|---|---|---|
| 1 | 返修要求、原候选及交稿入口 → 定位准确版本与边界 | 创作范围需保留。 |
| 2 | 用户消息与项目需求 → 登记原话及来源 | 项目事实改变，保留。 |
| 3 | 把本轮编导任务派给独立执行者 → 任务身份 | 有真实创作职责，保留独立执行上下文。 |
| 4 | 起始会话与计量文件 → 登记该轮边界 | 本案例明确要求计量，普通生产不必照搬。 |
| 5、6、8、9、10、17、18、19、20 | 各次等原执行者 → 9 次超时 | 无新业务信息。优先完成通知，无须额外查目录或日志。#9 同时给了用户进度说明。 |
| 7 | 查原片对应稿及来源 → 取得对照剧情 | 若协调方需核相邻剧情，定向保留；不复述给已有同版源稿的编导。 |
| 11 | 扫执行会话尾部 → 进程事件 | 没有稿件结论，可省这次跨渠道探测。 |
| 12 | 原等待被新输入中断 → 用户明确要求用暗示 | 真正改变方向，必须立即接收。 |
| 13 | 把新要求发给原编导 → 已发送 | 应沿原任务修正。 |
| 14 | 向用户说明具体暗示方式 | 有创作解释；不必为相同解释再起执行者。 |
| 15 | 回查新增消息 → 精确消息 ID 与时间 | 可和发送依据合流，保留真实来源。 |
| 16 | 向原执行者补发信息 → 已发送 | 若仅是来源补充，可随第 13 次一次提供；无法证明两条正文完全相同。 |
| 21 | 原等待收到完成事件 | 有新状态，只收目标产物与回执。 |
| 22、23 | 分次读前两段、后续段与文件身份，并查 handoff → 实际 R02 正文、状态 | 同一稿可合并取数；实际核查后半部是否被误改仍必要。 |
| 24 | 再等执行者 → 完成事件 | 已交稿时可按回执消费；不把另一次通知当独立内容审核。 |
| 25 | 登记请求次数及交付时点 → 计量记录 | 属于案例计量，不构成稿件通过。 |
| 26 | 向用户返回修订稿 | 人审停点，不能由协调方自动采用。 |

## 实际调用链与未实施项

- 普通生产由宿主决定模型响应与工具续等，仓库的 prepare、ensure-prepared、handoff、review-pack 本身是本地命令。AGENTS.md 和 PROJECT_RULES.md 定位职责，Skill 选择适用方法与 knowledge/runtime 文件。交接卡支持按段取来源；prepare 的任务身份、冻结范围和门禁仍可用于中断恢复。未发现这三个样本逐请求显式读取整套历史的证据。宿主反复携带上下文与脚本反复打开文件是两种不同开销。
- Web 路线为项目批次／单项入口 → audit_prompt 的原审查线程 → library_runtime.prepare → visual_asset_codex.run → 原 Skill 的 validate_review → 用户确认。审查线程内部等待 CLI 完成文件不会唤醒宿主模型。保留首轮真实观察、缺证复核、失败交回。生成、图像实看、用户确认各在其原有入口。
- 产品动作匹配另经 product_usage 的项目原料、剧情和 signature 处理；未触发动作匹配时沿现有分支。显式要求重新核对与当前稿件的判断具有业务含义。不给已通过的提示词或产品匹配跨修订自动免审，旧记录不能证明新参考、用户用途和产品版本相同。
- 浏览器和画布内部的有界只读检查用于证明原节点、原件、控件持久化、提交结果和正式回读。本批未降低实际生成完成条件，没有把“程序等待一秒”统一当成“请求了一次模型”。建卡身份不唯一时停止写入；失败后无依据自动认领、重建或重新提交，均可能重复生成。
- 跨阶段合并创作、导演、实看或采用，会丢失职责和事实边界；未实施。跨任务持久缓存还需解决来源变更、确认撤回和不同执行者阅读身份；本批只做一次调用内复用。外部网站、旧回执和动态分派缺少穷尽所有消费者的证据，未删接口或兼容字段。
- 原始长回合来自较早代码，单图 CLI、按段取源、合批预检等后续工作已在本批前落地。原始 41 次不能当作当前分支优化前的同输入基线，也不能凭这次离线结果推算新的线上请求数。

## 审查复核

历史三批从仓库根目录运行。下面函数完整保留三次调用的文件和原始 `-k` 条件，省去只影响输出位置的临时目录与日志重定向。直接运行 `original_batches` 可逐批执行；复核时使用同一代码块末尾的收集、去重和一次执行，避免重复跑相同节点。需要 Bash，以及仓库的 `.venv/bin/python`。

```bash
set -e
set -o pipefail
original_batches() {
  .venv/bin/python -m pytest -q "$@" scripts/tests/test_visual_asset_reading.py scripts/tests/test_visual_asset_projects.py scripts/tests/test_visual_asset_library.py scripts/tests/test_stage_context_cli.py \
    -k 'WebReadingTests or batch_starts or prepare_handles or batch_sends or batch_scheduler or preflight_failure_does_not or check_current_prompt or single_call_self_check or object_plan_and_review or empty_optional or missing_review_evidence or complete_cli_message or all_native_cli_consumers or codex_review_approval or codex_blank_failed or review_pack or review_kind_contract or same_scene_split or internal_shot or merged_internal'
  .venv/bin/python -m pytest -q "$@" scripts/tests/test_visual_asset_reading.py scripts/tests/test_visual_asset_projects.py scripts/tests/test_visual_asset_library.py scripts/tests/test_session_usage.py \
    -k 'WebReadingTests or check_current_prompt or single_call_self_check or object_plan_and_review or empty_optional or missing_review_evidence or review_binds_current_text or all_native_cli_consumers or codex_review_approval or codex_blank_failed or usage_docs_keep_routine'
  .venv/bin/python -m pytest -q "$@" scripts/tests/test_review_identity.py scripts/tests/test_visual_asset_projects.py scripts/tests/test_visual_asset_reading.py scripts/tests/test_visual_asset_library.py \
    -k 'TestNonexistent or test_light_revision or test_legacy_byte_hash_scope or test_corrupt_active_reading or test_review_round_key or test_current_consumption or test_irrelevant_changes_preserve or batch_records_failure or test_review_binds_current_text or test_empty_optional_observations or test_single_call_self_check or test_check_current_prompt or test_design_template_and_real_transport or test_actual_cli or test_codex_blank_failed or test_missing_review_evidence'
}

(
  work=$(mktemp -d)
  trap 'rm -f "$work/collection" "$work/nodes" && rmdir "$work"' EXIT
  original_batches --collect-only > "$work/collection"
  awk '/^scripts\/.*::/ {print}' "$work/collection" | LC_ALL=C sort -u > "$work/nodes"
  test "$(wc -l < "$work/nodes")" -eq 61
  xargs .venv/bin/python -m pytest -q < "$work/nodes"
)
```

42、29、24 是各批次选中的 pytest 节点数。前两批复用了 Web 读取及资产审核用例，第三批再次覆盖部分读取和补查用例。三组两两交集（1∩2、1∩3、2∩3）分别有 27、6、7 项，三组共同入选 6 项。按节点 ID 去重为 42 + 29 + 24 − 27 − 6 − 7 + 6 = 61 项。原 61 项另有 54 个 unittest 子测试通过，单列统计。未选中项未执行。
另做旧新审核包字节对照及宿主续等实验，均通过。已按仓库既有 CRLF 约定执行 Git 空白检查。真实模型输出质量、生产回合请求数和耗时、Windows 宿主与在线画布的本批表现尚未实测。下一次自然发生的同类返修可按同一 turn 起止、主执行者及子执行者分别计量。

## 独立审查 FAIL 修复批次

### 四项修复与调用链

| 问题与根因 | 进场调用链 → 本轮调用链 | 实际修改 |
|---|---|---|
| 分镜依据在内存初验后可被替换。写入引用时仍沿用先前结论。 | `review_pack → _load_task_basis → _evidence_current → scope / packet` → 首读原始字节供原有解析、版本校验和 scope 使用，渲染及 validate_text 结束后逐个重读比对，再写 scope、归档和当前包。缺失、空文件、拒读或字节差异返回文件名及重新准备／打包提示。无必读方法和候选的流程继续支持 prepare 合法省略的文件。 | `scripts/stage_context_cli.py`、`scripts/tests/test_stage_context_cli.py` |
| 对象转列表时丢掉空项并生成通用拒绝原因；旧校验只提取分隔符前的 ID。worker 以首答是否有问题决定补查，补答还会整体替换首答。 | `run → 格式转换 → validate_review → 整份重审` → 对象与旧数组经 `review_evidence` 解析，worker 按本次 profile 求差集，只向原有 CLI 请求缺证 ID；补答合入首答，按相同规则作最终判定。编辑保存、只读审核和确认使用同一原稿。 | `scripts/visual_asset_codex.py`、`scripts/visual_asset_library.py`、`skills/asset-image-assistant/library_runtime.py`、`scripts/tests/test_visual_asset_library.py`、`scripts/tests/test_visual_asset_projects.py`、`scripts/tests/test_asset_semantics.py` |
| 宿主示例在 await 抛错时到不了最后的 text(r)。 | `exec_command → write_stdin → 异常外抛` → 保存首次 ID，续等异常转成含原 ID、异常名／消息和失败状态的回包。测试从文档提取实际 JS 并执行。 | `docs/rules/execution.md`、`scripts/tests/test_session_usage.py` |
| 报告将六条较窄的选择命令列作复现入口，合计仅覆盖 40 个不同节点。 | 六条摘要选择 → 上节三条原始选择、收集 pytest 节点、去重后执行。 | 本报告「审查复核」一节 |

### 依据与审核结果

对同一临时分镜项目、同一审核产物连续运行三个版本的打包入口，在文件打开处计数，并直接比较输出字节。

| 版本 | task_basis.json | method_reading_evidence.json | method_reading_packet.md | 合计 |
|---|---:|---:|---:|---:|
| 分支 HEAD 中的优化前版本 | 4 | 4 | 3 | 11 |
| 修复批次进场版本 | 2 | 1 | 1 | 4 |
| 本批写入前复核 | 2 | 2 | 2 | 6 |

同一夹具的审核包 4,987 字节，scope 694 字节，三个版本的两份输出分别相等。定时点测试在最终内容校验返回时分别替换、删除、清空或模拟拒绝读取三个依据文件，12 种情况全部中止，原包、原 scope 和原正式归档均保留。当前写入方没有共同持有的文件锁；此验证覆盖写前比较点。沿用既有 scope 哈希字段，原始字节比较未新增哈希、版本字段或跨调用缓存。

| 首答 | 新对象／旧数组的证据判定 | 模拟模型 CLI 次数与结果 |
|---|---|---|
| 每项 ID 与具体观察齐全 | 对象按 profile 顺序交付，旧数组保留原行顺序、分隔符和空白。含结论及其具体理由的观察可用。 | 1 次，通过 |
| 空白、纯状态、只有 ID、ID 错配 | 缺项不充当问题或证据。一次调用只请求缺证 ID，多个 ID 合并补查。 | 共 2 次。补齐可通过，仍缺证则退回 |
| 真实问题加六项缺证 | 已明确的问题与首答有效证据进入最终记录。补答无法清除首答的问题。 | 共 2 次。六项补齐后仍退回 |
| 单项缺证或补答改写原稿 | 已有六项按原内容保留。只读补答绑定同一正文，改稿产生的证据无效。 | 共 2 次。有效补齐可通过，改稿退回 |

夹具从实际 worker 到模拟 CLI 回包，验证请求的必填 ID、调用次数、最终问题、原稿与逐条证据。原稿含 CRLF、制表符、引号、特殊字符和尾空格。模型负责针对该检查项作事实判断，程序把正确 ID 下的原始证据行绑定同一稿件，区分来源位置、状态、缺证声明及观察正文，并保留明确拒绝和具体问题；事实是否与图像或方法相符由本轮模型判断。离线结果不能证明自然生产中模型观察属实。

实际执行等待示例，首次启动返回 session 831，模拟续等抛出 `Error: broken pipe`。捕获结果为 `{"session_id":831,"error":"Error: broken pipe","ok":false}`。调用记录恰为启动一次和续等 831 一次。

### 验证、改动量与工作区保护

- 原三批在本报告代码块中准确复现 **61 passed，54 subtests passed**。另选 17 个不同节点，其中资产 worker 的 22 个场景按子测试单列。共 **78 个节点通过，0 失败，0 跳过**；76 个子测试通过（54 + 22）。涉及的 8 个测试文件共收集 403 个节点，325 项未选中。仓库其他测试文件未收集。首次执行有一项既有断言因为新错误提示缺少“任务依据”而失败；加入明确的依据名称后复测。既有物品规则测试换用带具体证据的输入时发现视图短语被误判；已在定向复测中覆盖。
- 生产代码相对本批进场工作区新增 176 行、删除 55 行，**净增 121 行、7,425 字节**。其中打包入口净增 37 行，CLI 适配净增 65 行，worker 净增 16 行，Skill 运行时净增 3 行。新增复杂度在写前原件比对、两种格式的证据解析以及定向补答合并。测试、范例和报告单列。没有新增运行依赖、Schema、通用锁或测试工具。
- 进场实测为 31 份已跟踪修改及本报告 1 份未跟踪文件。上一轮历史 19 份修改中，16 个文件与本轮进场字节相等，另外 3 个文件的历史修改块分别为 15、1、1 块，已逐块反向检查可应用。指定的 `scripts/core/recreation_segments.py`、`scripts/core/redframe_image_ui.py`、`scripts/core/redframe_recreation.py` 均逐字节相等。收尾暂存区为空，分支与 HEAD 相同。验证仅使用临时项目和模拟模型，没有调用线上生图或审核。

本批唯一一次只读独立审查发现，写有「图1：待补证，尚未记录具体观察」的回包可误过；首答明确拒绝且要求改回蓝色，但问题只写在检查正文中时，其否决会被补答覆盖；与原稿没有相同词汇的「双排黄铜扣，侧缝平直」可能丢失。修正后，对象与旧数组分别验证三项：待补证仍退回；六项补齐后仍保留首答拒绝及其原文；简短具体描述保留。只对受影响的资产调用链补测，10 个节点和 30 个子测试通过，这些已计入上面的不同节点和子测试总数。

### 最终独立审查结论

**整批 FAIL。** 分镜依据换版、宿主续等和 61 项复现通过。资产审核原三种反例在两种格式下独立复核 6/6 通过，但审查者又实测出纯状态「合规」仍能获批。构造一个正常原稿，让模型返回 `passed=true`、空 issues，七个正确 ID 各对应「合规」；对象格式和等价旧数组的最终 `validate_review` 均返回 `passed=True`、七条 checks、空 issues。问题位于 `scripts/visual_asset_codex.py::_has_observation`：去除定位符、已知状态和缺证说明后，只要残留文字就可能算作观察。这个条件不能证明存在具体事实。资产 P0 尚未修完；上述 78 个测试节点和 76 个子测试通过不覆盖该反例。本轮在此独立审查结论处停止。

> 现行状态注（2026-11-26）：后续提交 `54f6233` 已移除 `scripts/visual_asset_codex.py` 中的自动复审路径与 `_has_observation`，上述 P0 对应代码已不再存在。
