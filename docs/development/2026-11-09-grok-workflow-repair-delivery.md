# 全链路修正实施交付

2026-11-09。本轮执行对象为指定隔离树中的源码、规则入口和合成验收数据。

## 源码与执行身份

- 工作树为 `/Users/liuyang/Documents/Codex/2026-11-09/grok-workflow-repair.8AcvWY/worktree`。
- 分支为 `codex/grok-workflow-repair-8AcvWY`，基线 HEAD 为 `6833d775449f8dbca9373126ebd7b930a518bbfc`。
- 本轮交付为本地工作树修改。环境回执记录了三个实际加载模块的路径，均指向本工作树。
- 开工读取了仓库 AGENTS、PROJECT_RULES、执行分工、审核到画布、快照、计量、红帧、产品使用、独立启动、共享读取及视频 Skill 入口，并只读消费用户指定的两份检查/修正报告。
- 验证使用 Python 3.12.14、pytest 与本机已有依赖。FFmpeg/ffprobe 使用 VideoFusion 随附二进制，仅在测试进程中增加 PATH。所有测试输入及输出落在本工作树。

环境证据见 [environment-checks.txt](../../diagnostics/grok-workflow-repair/environment-checks.txt)。

## 实际修改文件

下表路径均相对工作树根。

| 路径 | 修改范围 |
| --- | --- |
| `scripts/stage_context_cli.py` | handoff 目标/停止点与来源字节，复用共享计量，实现只读 usage-check |
| `scripts/session_usage.py` | 共用 checkpoint，完整边界、未知小计、无效用量与停止摘要 |
| `scripts/canvas_readback.py` | 正常提交预检/回读消费源节点、入边、用途、正文时间，写前基线比较，接同一计量检查点 |
| `docs/rules/execution.md` | 干净 Terra、五类交接、有界等待、完成回执及宿主责任 |
| `docs/development/token-budget.md` | 准备、直读与画布检查点的调用、范围、未知及到限行为 |
| `docs/development/delivery-snapshot-checks.md` | 同一快照内的字段合同、历史兼容、整节点回读和写前漂移检查 |
| `docs/development/review-to-canvas-entry.md` | 正常生产入口接到上述执行与检查点 |
| `skills/_shared/redframe-operations.md` | 按依赖成批操作、整节点采集、异常停止及交接 |
| `skills/_shared/knowledge-access.md` | 视频直读的独立计量入口，准备包例外的准确边界 |
| `skills/voiceover-to-video-script/SKILL.md` | 视频直读及画布阶段使用独立 usage-check |
| `scripts/tests/test_workflow_repair_cli.py` | 新增本轮正常 CLI 的合成正反例 |
| `scripts/tests/test_canvas_readback.py` | 红帧合成输入按新合同准备 |
| `scripts/tests/test_delivery_preflight.py` | 复用合成画布夹具，时长测试明确 PNG/MOV 编码 |
| `scripts/tests/test_quality_evidence_consumers.py` | 合成视频明确 PNG/MOV，继续检查实际时长与门禁 |
| `scripts/tests/test_stage_context_cli.py` | 正式 handoff 的目标、停止点、来源 hash 及漂移回归 |

交付说明为本文件。独立验收日志保存在 `diagnostics/grok-workflow-repair/`。

## 三块实施对照

| 修正报告范围 | 已实施及本地验证 | 仅执行约定或待实测部分及原因 |
| --- | --- | --- |
| 协调和交接 | 沿原 handoff 增加本轮目标、停止点、所有有效输入的实际 SHA-256，指向当前需求、证据、授权和未决字段。消费时重查字节。历史候选 hash 字段仍可读取。测试覆盖有效采用、错误确认、来源变化及重建交接。图片批次原有独占路径、上下文身份、8 张上限及阅读门禁继续受测。 | 干净上下文、实际 Terra 模型、隔离 Edge、一次有界等待与完成通知属于宿主执行约定。仓库不持有宿主委派和等待控制权，故本轮以操作入口和后续轨迹验收要求落实。生产过程日志及大图沿原路径保留，协调方仅收当前回执与必要差异。 |
| 红帧交付 | 复用冻结 JSON 的 ordered_media/reference_checks，增加同稿 canvas_binding、edges/source_nodes。核文件 hash、具体用途、源输出、端口、目标槽、正文实体及逐镜秒数。保存后比较整个受影响节点的核心字段，写前比较完整已采集节点。正式 CLI 覆盖缺边、错源、缺字段、同用途重复槽、无引用槽缺说明、正文/总时长冲突、已有编辑漂移、上游字节变化及实际动作视频时长。 | 真实字段采集、保存刷新、认证传递、媒体远端身份、布局截图和平台失败复现需要 Terra 的合法平台通路。按依赖分批与连续两次同因失败停止沿执行规则落实。CLI 对本地声明和读数检查，采集真实性和远端并发变化须平台/执行者核验。 |
| 计量检查 | 从既有阶段门禁抽出 session_usage.checkpoint，prepare/ensure-prepared、usage-check、画布 preflight/readback 共用。覆盖去重、压缩记录、UI 快照排除、显式多轮边界、峰值、累计量、调用数、到限/未知停止。候选必需合同继续限制准确 turn 与预算。 | 项目检查点只能看到已落盘 response，宿主在途请求及未暴露后台请求仍在其边界之外。实际 token 下降、等待/查询次数及费用需要下一次真实运行日志，合成日志只验证计算与拒绝行为。 |

## 关键实现决策

1. 新红帧提交从开工平台或准确红帧域名识别，强制整节点合同。其他平台显式声明这些图字段时同样核验。历史无项目的只读比较继续保留原范围，返回相关 not_checked。
2. 同一文件、同一具体 purpose 重复占槽会拒绝。同文件承担不同用途须准确分开声明，观察与确认仍须覆盖。无正文原生引用的槽必须保存实际用途说明，其来源与边继续检查。用途语义由执行者核实。
3. 正文秒数范围直接读取实际 prompt，逐项对照 shot_durations 与总长。现支持明确的 s/秒范围格式，复杂或未识别时间表达须回来源核实。机器检查不能证明动作估时充分。
4. 已有节点更新时，按操作规则提供最后可信 after_refresh 基线与本次 before_write。CLI 对配对输入、目标和节点差异强制拒绝。是否属于更新、是否完整采集及检查后的远端变化须执行者核实。
5. 计量的必需条件沿原合同与任务范围启用。普通旧项目省略可选计量参数沿原行为。usage-check 自身要求实际边界和预算，视频保持直接读取。计量缺证和预算到限停止下一批，失败摘要保留已知峰值、总量、观测调用数与原因。
6. 新增小型合成 CLI 测试复用既有夹具、manifest、确认/采用及质量检查。时长夹具采用显式 PNG/MOV，因为本机现有 FFmpeg 未提供原测试依赖的默认 MP4/MPEG-4 或 libx264 编码器，实际 ffprobe 断言继续执行。

## 验证命令与关键输出

以下命令在工作树根执行。临时目录也位于该树内，重复验收每次新建目录。

```sh
msb_python=/Users/liuyang/Desktop/Master-Storyboarding/.venv/bin/python
mkdir -p .workflow-tests
msb_test_dir=$(mktemp -d "$PWD/.workflow-tests/acceptance.XXXXXX")
PATH="/Applications/VideoFusion-macOS.app/Contents/Resources:$PATH" \
TMPDIR="$msb_test_dir" PYTHONDONTWRITEBYTECODE=1 "$msb_python" -m pytest \
  -q -ra --tb=short -c config/pytest.ini \
  scripts/tests/test_workflow_repair_cli.py \
  scripts/tests/test_session_usage.py \
  scripts/tests/test_candidate_usage_contract.py \
  scripts/tests/test_canvas_readback.py \
  scripts/tests/test_delivery_preflight.py \
  scripts/tests/test_stage_context_batches.py \
  scripts/tests/test_stage_context_cli.py::test_handoff_revision_receipt_relative_path_and_hash \
  scripts/tests/test_stage_context_cli.py::test_prepare_rejects_replaced_handoff_input_and_accepts_regenerated_card \
  scripts/tests/test_native_port_binding.py \
  scripts/tests/test_reference_delivery_contract.py \
  scripts/tests/test_private_canvas_preview.py \
  scripts/tests/test_quality_evidence_consumers.py::test_xiaoyunque_probes_actual_video_not_internal_cuts \
  --basetemp="$msb_test_dir/pytest" -o cache_dir="$msb_test_dir/cache"
```

核心验收结果为 `262 passed, 11 subtests passed in 5.31s`，退出码 0。完整摘要见 [final-acceptance-tests.txt](../../diagnostics/grok-workflow-repair/final-acceptance-tests.txt)。这些是本地合成证据，夹具中的批准与原生身份均为合成断言。

正常生产命令由上述测试实际调用，主要行为如下。

| 正常入口 | 合成观察 |
| --- | --- |
| `stage_context_cli.py handoff` 与后续准备消费 | 有效输入、目标/停止点与来源 hash 可交接，来源字节改变拒绝消费 |
| `canvas_readback.py --delivery --preflight` | 源、边、槽、用途、时间齐全通过，缺边/重复用途/时间冲突等反例非零退出 |
| `canvas_readback.py --delivery --readback` | 全节点核心字段及原生引用匹配通过，任一受测字段变化返回差异，production_ready=false |
| `--preflight --baseline ... --current ...` | 完整基线通过，已有编辑改变第二节点时返回 changed_node_ids=[two] 和退出码 2 |
| `prepare`、`ensure-prepared`、`usage-check`、画布入口 | 合成单 response 的 input=100、cached=90、uncached=10、output=7、total=107、requests=1。低于所选预算通过，等于输入100/总量107/调用1任一阈值即停。缺 total、截断尾行及截止范围内无法定位 ordinal 的用量拒绝。 |
| 共享原生/质量/采用分支 | 错 hash、失效来源、缺/错原生身份、失败图片用途和越界预览继续被拒绝 |

其他检查使用下列命令。

```sh
PYTHONDONTWRITEBYTECODE=1 "$msb_python" -m scripts.core.knowledge_runtime
git -c core.whitespace=cr-at-eol diff --check
```

知识包返回 `{"ok":true,"files":57,"dynamic_rule_ids":["MSB-KB-PROMPT-002"]}`，差异空白检查退出码 0。session_usage.py 保留基线 CRLF，此处只让检查器识别该行尾。知识回执见 [knowledge-runtime.txt](../../diagnostics/grok-workflow-repair/knowledge-runtime.txt)。

### 扩展回归及具体阻塞

在核心命令相同的环境下，将两个 stage_context_cli 单项替换为整个 `scripts/tests/test_stage_context_cli.py`，将最后一个 quality_evidence_consumers 单项替换为整个 `scripts/tests/test_quality_evidence_consumers.py`，使用新的独占临时目录运行。

输出为 `13 failed, 420 passed, 11 subtests passed in 9.12s`，退出码 1。完整记录见 [final-regression-tests.txt](../../diagnostics/grok-workflow-repair/final-regression-tests.txt)。13 项均在源码夹具准备处被阻塞，错误为以下原文。

```text
scripts/tests/source_fixture.py:63
ValueError: source fixture destination must be outside its source
```

其中 7 项为阶段阅读缓存恢复，2 项为可选编导方法，2 项为分段规则漂移，1 项为恢复规则缺失，1 项为导演方法快照。它们要求源码副本位于仓库外，与本次唯一可写目录冲突，相关断言尚未执行。这些失败保留在报告中，后续需在获准的外部测试目录运行原测试入口。最终扩展范围中的视频时长测试已实际执行。

## 后续独立实跑的验收缺口

- 用下一次已授权的准确业务任务核宿主实际 fork/model、图片批次身份、等待/查询调用轨迹、完成回执和定向取证范围。仓库文档和合成回执只能覆盖其本地合同。
- Terra 在任务独立后台 Edge 采集真实源节点、入边、全部槽、原生实体、正文、参数及源字节证据，保存刷新后以同版快照运行正式比较，并核全景/局部布局。平台保存稳定性与人物认证传递分别留实际证据。
- 现有 node_port_download 仍是图像证据分支，本轮动作视频及无引用槽的合成验收使用 media_id 分支。真实视频槽身份、无引用槽采集及完整平台适配仍需实测，缺合法原生身份时保持阻塞。
- 更新任务核最后可信基线、实际写前读取、用户编辑的准确范围及保存后的整节点。平台结果未知先查原操作，同因连续失败两次的接续点按规则收口。
- 用实际逐 response 边界分别测生产、返工和后续复盘，报告峰值、缓存/未缓存输入、输出、总量与调用数。费用缺证仍为 null，实际用量下降须相近范围的真实运行对照。
- 成片声画、动作可见时长、口型和接缝效果待可核结果文件，按原质量与采用门禁另验。

本次结论限于上述实施及本地验收范围。源稿确认、来源和用途、合法采用、质量、实际看图/听核与生成授权继续由现有入口分别校验。


## 同一任务补充验收的两处修复

根据独立验收复现，补强以下两个位置。

- `scripts/stage_context_cli.py` 的 `_current_handoff_text` 先比较 artifact_id、logical_id、revision、type 和 path，再比较来源 hash。采用 R02 后旧卡准确返回“已有交接卡的产物、版本或路径已过期……”。身份相同而 hash 不符返回“交接卡来源字节已改变……”。原用例已加强为匹配完整过期消息前缀，并增加同身份 hash 不符的单独断言。
- `scripts/canvas_readback.py` 的 `compare_baseline` 对 after_refresh 和 before_write 的每个节点都要求显式列表 `edges` 与 `source_nodes`。任一侧缺字段或字段类型错误即拒绝。新增九组合成反例，覆盖单侧/双侧省略一个/两个字段，直接函数与正常 CLI 均拒绝。另有显式空列表的通过例。

修复前先运行新增反例及加强后的 R02 用例，结果为 `10 failed in 0.28s`，退出码 1。原始记录见 [supplement-before-tests.txt](../../diagnostics/grok-workflow-repair/supplement-before-tests.txt)。

修复后在工作树根执行以下相关回归。

```sh
msb_python=/Users/liuyang/Desktop/Master-Storyboarding/.venv/bin/python
mkdir -p .workflow-tests
msb_test_dir=$(mktemp -d "$PWD/.workflow-tests/supplement.XXXXXX")
PATH="/Applications/VideoFusion-macOS.app/Contents/Resources:$PATH" \
TMPDIR="$msb_test_dir" PYTHONDONTWRITEBYTECODE=1 "$msb_python" -m pytest \
  -q -ra --tb=short -c config/pytest.ini \
  scripts/tests/test_workflow_repair_cli.py \
  scripts/tests/test_canvas_readback.py \
  scripts/tests/test_delivery_preflight.py \
  scripts/tests/test_stage_context_cli.py::test_prepare_rejects_replaced_handoff_input_and_accepts_regenerated_card \
  scripts/tests/test_stage_context_cli.py::test_handoff_revision_receipt_relative_path_and_hash \
  --basetemp="$msb_test_dir/pytest" -o cache_dir="$msb_test_dir/cache"
```

输出为 `108 passed in 3.34s`，退出码 0。完整回执见 [supplement-after-tests.txt](../../diagnostics/grok-workflow-repair/supplement-after-tests.txt)。本节记录当前两处补强的验证，前文主三块日志保留其原运行范围和时间，后续实跑缺口继续按前文验收。
