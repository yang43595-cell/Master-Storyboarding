# 审核工作台操作

按[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)选择当前动作对应小节。命令使用本机凭据文件路径。换站、接口字段和联调按[项目送审接口合同](review-api-contract.md)。

## 编导文本送审命令

[review_text_cli.py](../../scripts/review_text_cli.py) 的 `publish` 一次完成编导稿校验、同版阅读稿、发布前核对、送审和网站状态回读。沿可信交接、工作台或原发布回执核项目 ID、案例 ID 和预期当前版本。原片对白先核来源和说话人，合格的原创或授权改写对白随整稿审核。

需要新建案例时，沿已有授权执行 `create-case`，用返回的案例 ID 和网站项目 ID。

```sh
python scripts/review_text_cli.py create-case \
  --project '<业务项目绝对路径>' --name '<本轮获准案例名>' \
  --operation-id '<本次创建的UUID>' --token-file '<本机凭据文件>' \
  --receipt '00_项目管理/审核发送/创建案例.json'
```

首次发布传 `--expected-head-version none`。修改后重发时传本次返修依据的准确网站版本。

```sh
python scripts/review_text_cli.py publish \
  --project '<业务项目绝对路径>' --case-id '<真实案例ID>' \
  --expected-project-id '<真实网站项目ID>' --expected-head-version none \
  --narrative '02_编导/R01_叙事包.json' --markdown '02_编导/R01_审核阅读稿.md' \
  --operation-id '<本次发布的UUID>' --token-file '<本机凭据文件>' \
  --receipt '00_项目管理/审核发送/R01_发布.json'
```

同版阅读稿缺失就新建，已有文件须与本版 JSON 的投影逐字节匹配。只需本地阅读稿时用 `prepare --narrative <编导JSON> --output <阅读稿>`。已有单独状态回执时仍可传 `--preflight <状态回执>`，沿原来的发布前差异检查及独立 `status` 回读。

发布回执保留源稿修订和字节、阅读稿摘要、写前状态和实际发布响应。`readback.status=current` 表示当前版本、代数和网站项目均匹配。`historical` 表示网站已有更新版本，`unknown` 表示暂未读回准确状态，沿原发布回执查询。需要查看正文或标注时，沿回执中的案例和版本进入对应页面。正式采用等用户网站通过后沿[编导网站决定与采用](#编导网站决定与采用)办理。

同一操作结果未知时先核原操作和当前状态。状态未变才用原 `operationId`、案例、文件名、正文和用途精确重试，`--preflight` 指向原发布回执或此前的独立状态回执，`--receipt` 使用新路径。状态已前进时先核清原发布结果，再决定是否处理新版。改内容、文件名或用途需新操作。

## 编导网站决定与采用

编导送审后，运行 `python scripts/review_text_cli.py decision --project <项目> --publication <编导发布回执> --stage script --adopt-script-id <叙事包产物ID> --token-file <本机凭据文件> --receipt <新决定回执>`。命令在采用时读取当前有效的网站整稿通过，并核对实际采用的文件与送审 JSON 的路径、修订和 SHA-256。返修先沿现有修订控制登记变更案及新候选，再在命令追加 `--change-id <活动变更案ID>`，用本次网站决定确认和提交修订。待审、修改意见、历史版、失效版及送审后字节漂移的稿件保留候选。

`decision` 已核网站通过、随后本地采用失败时，错误输出给出 `step: local_adoption`、准确版本、回执和失败代码。同一份决定回执保留网站响应及本地阻塞。沿[原处续作](../rules/execution.md#工具报错与原处续作)只读核当前采用状态，条件改变后，对原发布版本用新决定回执路径重走同一 `decision`。现场新读的网站决定和原件字节仍须匹配，旧回执供定位。已采用的同版从下一工作继续。

网站决定采用成功后，沿[编导通过后接导演](artifact-adoption.md#编导通过后接导演)生成交接卡。

## 资产图送审

选定实际制作项后，用 `python scripts/review_text_cli.py status --project <项目> --case-id <案例ID> --stage assets --token-file <本机凭据文件> --receipt <新状态回执>` 保存发布前状态。核对网站项目、案例、资产当前版本和同版编导通过后，运行 `python scripts/review_text_cli.py publish-asset --project <项目> --preflight <资产发布前状态回执> --script-publication <编导发布回执> --image <项目内候选图> --artifact-id <产物ID> --ast-id <AST-ID> --output-id <输出ID> --purpose <制作项准确用途> --operation-id <UUID> --token-file <本机凭据文件> --receipt <新发布回执>`。送审沿用当前有效资产需求及已登记的调整。原件上限 8 MiB，支持 PNG、JPEG 和 WebP。

上传后核回执的图片 ID、版本和 `readback.status`。`unknown` 表示状态待核，`historical` 表示已非当前版，两者可用于同一编导版的 `--reference-publication`。读回发现项目或上游版本不符时，保留上传证据并阻止该回执成为审核包成员。

## 资产网站决定与交接

用户在网站通过准确资产版后，运行 `python scripts/visual_asset_library.py --library <现有制作库> apply-website-review --project-id <制作库项目ID> --publication <资产当前版发布回执> --token-file <本机凭据文件>`。命令在线读取决定，核对上游、选图字节、制作依赖和用途。多图时用重复的 `--reference-publication` 纳入准确成员，返修包可引用未变图片的原发布记录。网站和对话采用命令在同一次调用内备份待交接的已确认原件。备份后复核审核依据、当前选图和文件字节，复用已登记的逐图确认继续导出。备份失败按返回原因处理后复跑同一命令。部分交接按已导出的准确输出联动下游，其余仍待审或待补。

每版以工作台实际列出的包成员为准。整表交接以本地 `complete: true` 和实际导出为准。

局部换图时，从同一次资产交接中保留仍有效的准确输出及原母版文件。网站对当前同一版本退回时，沿其发布回执和确认依据撤出仅靠该版确认的输出，较早已独立确认且仍有效的参考图可继续使用。多图退回的具体返修内容仍从该版审核意见核对。视频稿同时用到变更和未变更输出时，按 `artifact_manifest.json` 对应稿件最新 `stale_reasons` 中的 `asset_output_segment_ids` 定位本次局部返修，并沿原修订入口提交混合整稿。范围为 `unknown` 或有其他未核清的后续变更时，先核当前引用情况。正式画布仍核当前已采用的稿件。

## 网站往返

站点由用户确认为 [审核工作台](https://review.yangyangyang.click/workbench/)。每轮先读取该项目和阶段的真实当前版本，再发送同一业务 JSON 或准确资产候选及必要媒体。发布成功须有实际回执并能读回准确版本；不能以本地编译成功、HTTP 成功码或同名文件代替。

1. **协调方接棒。** 送审后在业务项目里留下案例、阶段、发布回执和待审核范围。当前任务正在等审核时，由协调方检查决定。跨轮续跑、受影响的正式采用或画布写入前，先按发布回执核对网站决定。读到有效通过就按[编导网站决定与采用](#编导网站决定与采用)或[资产网站决定与交接](#资产网站决定与交接)办理，并安排已解锁的工作。待审、接口不可访问或有效性无法确认时，按[总规则的停点](../../PROJECT_RULES.md#网站审核到画布的统一入口)暂停受影响工作，保留各自的实际状态。
2. **退回后按准确版本收齐意见。** 先读总体方向，再逐处读取已提交的正文标注、图片批注和参考附图。沿用项目已有审核记录，写明网站项目、案例、阶段、版本、决定和读取时间。逐条记下原文或图上位置、稳定正文／对白 ID 或 `AST-ID@OUTPUT-ID`、附件身份和读取状态。读完分页并逐一打开可用附件。范围定位不清、分页未完或附件打不开时，保留已核项并标出缺项，暂停依赖这些意见的返修。
3. **使用实际可用的意见读取路径。** 已注册案例 `01–07` 可用 `read_case_reviews`。以 `contentCurrent` 核版本和来源有效性，按 `contentFeedbackPage.nextCursor` 读到 `hasMore` 为 false，再用 `read_review_attachment` 读取附件。其他案例由主对话按[项目专用界面分工](../rules/execution.md#画布执行与交接的宿主边界)在获授权的会话中核该版已提交意见。页面缺字段或未能登录时记录缺口。通用意见和附件 API 仍待审核站提供已核合同。
4. **返修另发新版。** 逐条记录修改和定位，重跑修改及直接影响的检查。正式稿已有下游时先按现有修订控制登记影响。已有用户指令准确覆盖影响范围时直接引用其依据，超出范围时只核新增影响。新修订取得自己的正式通过后，再更新本地采用和直接受影响的下游。已有意见的关闭依据留在原记录里。
