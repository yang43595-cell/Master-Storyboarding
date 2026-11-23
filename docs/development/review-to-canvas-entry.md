# 审核到画布入口

[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)规定谁审核、何时采用和做到哪里。只打开当前任务对应的小节。跨阶段协调时用下表找交接点。

## 阶段结束与恢复

编导或导演成稿、确认采用、资产制作或确认、分镜依赖检查、视频稿采用、草稿保存回读，每次把结果写入该阶段已有记录后，协调方运行同一个交接命令。中断后也从同一入口继续。

```sh
python scripts/stage_context_cli.py handoff --project '<业务项目绝对路径>'
```

`status --project` 只读展示同一份就绪结果。`handoff` 将可继续的动作列在 `next_actions`，等待及其范围列在 `steps`。读到未收口的事务时先停在原记录，持锁交接沿已有事务恢复，确认后的返修只暂停触及冻结分支的动作。

普通阶段沿 `context_id` 使用交接卡，同证据重跑复用原卡。可以从卡中选镜号或准确输出子集，准备和续作检查同一本轮范围。导演和已授权资产可并行。批次沿原请求与回执核 `AST@输出`，已经成图待审的目标继续走原审核通道。主对话直做需让程序跟踪原回执时，也可在本阶段准备传现有 `--batch-receipt`，沿原阶段目录登记。

草稿沿现有画布交付回执和准确段号续作。已回读通过的段收口，已取得节点 ID 的段续用原节点，创建结果未知的段先核对原画布。显式目标用于绑定本轮接收者、图片批次或办理已选的兼容人工流程。

导演稿采用后须经过分镜阶段的逐镜核对。能用现有素材时，Agent 在原[视觉补充请求](../rules/visual-production.md#导演到视频的视觉交接)登记实际镜号、采用导演版本、所用图片及实看结论；无需补图时按同一入口写明理由。缺本版分镜检查记录时，程序暂停对应镜段写词。声音与生成按本轮有效决定和授权执行。默认收口为准确草稿保存并回读。

| 当前要做的事 | 直接读取 | 交接点 |
| --- | --- | --- |
| <a id="开工确认单"></a><a id="使用已选产品参考包"></a>新项目开工或补齐决定 | [开工确认单](../rules/project-layout.md#开工确认单)，选用商品包时看[产品参考包](../rules/project-layout.md#使用已选产品参考包) | 登记来源、范围及当前工作依赖的决定 |
| <a id="核对真实使用的固定演示窗"></a>写编导稿 | [编导 Skill](../../skills/creative-director/SKILL.md)，广告真实使用且演示时长固定时查[固定演示窗](../rules/project-layout.md#核对真实使用的固定演示窗) | 交准确版本给用户审核 |
| <a id="编导文本送审命令"></a><a id="编导对话确认"></a>编导送审或采用 | [网站送审](review-workbench-operations.md#编导文本送审命令)、[网站通过](review-workbench-operations.md#编导网站决定与采用)或[对话确认](artifact-adoption.md#编导对话确认) | 整稿确认后采用，解锁导演和已授权资产 |
| 对标编导整稿已采用，选择拍摄路线 | [开工记录中的路线选择](../rules/project-layout.md#开工确认单) | 选导演重新拍或按原片结构复刻，再运行同一交接命令。导演在复刻线中继续负责真实产品操作段和衔接 |
| <a id="审核站-api-对接要求"></a>资产制作或审核 | [资产 Skill](../../skills/asset-image-assistant/SKILL.md)、[资产送审](review-workbench-operations.md#资产图送审)、[网站通过](review-workbench-operations.md#资产网站决定与交接)或[对话确认](../../skills/asset-image-assistant/modules/project-web-mode.md#资产对话确认) | 逐项交接已确认且可取用的输出 |
| 在红帧生成资产图或补图 | [红帧生图 SOP](../../skills/_shared/redframe-operations.md#生图) | 上传素材卡后当场归位，以原生连线和正文引用核对实际生成输入 |
| 在红帧上传素材、排版或批量整理 | [素材区上传](../../skills/voiceover-to-video-script/references/redframe-canvas-usage.md#素材区上传)、[命名与排版](../../skills/voiceover-to-video-script/references/redframe-canvas-usage.md#命名与排版)、[批量修改节点](../../skills/voiceover-to-video-script/references/redframe-canvas-usage.md#批量修改节点) | 只读实际要执行的小节，核对素材归位与旧节点状态 |
| <a id="网站往返"></a>查退回意见与附件 | [网站往返](review-workbench-operations.md#网站往返) | 按受影响范围返修新版本 |
| <a id="本地流程授权与自主采用"></a>完成导演、按需分镜或视频稿 | [导演](../../skills/storyboard-planner/SKILL.md)、[按需分镜](../../skills/storyboard-director/SKILL.md)、[视频提示词](../../skills/voiceover-to-video-script/SKILL.md) | 全稿自查后按[首次采用](artifact-adoption.md#首次自主采用)或[自主修订](artifact-adoption.md#自主修订)办理 |
| 从导演和分镜交接到视频写词 | [导演到视频的视觉交接](../rules/visual-production.md#导演到视频的视觉交接) | 按实际镜号核场景母图、动作参考与关键位置关系，交已就绪的镜段 |
| 写入并回读视频草稿 | 红帧走[成批写入与回读](../../skills/_shared/redframe-operations.md#成批写入和整节点回读)，小云雀看[平台交付](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#小云雀网页草稿)和[排版](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#画布紧凑排版) | 保存后刷新，核原生正文、素材对应、参数与布局截图；交草稿时附准确画布链接、登录入口和段号 |
| 提交已明确授权的视频生成 | [已授权生成的取证](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#已授权生成的取证) | 复用覆盖本次范围的有效授权，按准确节点提交并保留原任务身份。结果未知时先只读查原任务 |
| <a id="agent-审核-api"></a><a id="接线现状与开发交接"></a>接入、换站或核对接口限制 | [项目送审接口合同](review-api-contract.md)，现网差异见[现有工作台适配](review-api-contract.md#现有工作台适配) | 开发联调沿对应接口和验收用例 |
