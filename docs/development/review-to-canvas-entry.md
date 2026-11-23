# 审核到画布入口

[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)规定谁审核、何时采用和做到哪里。只打开当前任务对应的小节。跨阶段协调时用下表找交接点。

## 已有产物被打回或只改一处

先按用户点名的图、段或镜头回到原版本和回执。范围会跨镜时，连同实际受影响的接缝、引用或共享素材一起核对。

| 收到的修改 | 从哪里接着做 |
|---|---|
| 待审编导稿改对白或某一场 | 沿[编导修订交稿](artifact-adoption.md)用原候选 ID 替代交稿，按回执的变更编号定位正文，整稿沿原入口交审。 |
| 已有资产图或分镜补图改画面 | 沿[红帧生图](../../skills/_shared/redframe-operations.md#生图)的单图返修命令，读取本次请求、原候选回执和原卡；生成结果未知时继续查询同一次提交。 |
| 只改复刻段的深度窗 | 用[局部深度导出](../../skills/depth-guided-recreation/references/depth-video.md)指定受影响的段号。 |
| 网站打回、换声或局部改戏 | 网站按[原发布回执](review-workbench-operations.md#网站往返)收齐意见与附件；换声沿既有角色绑定找受影响段；局部改戏沿[复刻分段交接](../../skills/depth-guided-recreation/modules/recreation-method.md#分段交接)只派相关段。 |

## 阶段结束与恢复

编导或导演成稿、确认采用、资产制作或确认、分镜依赖检查、视频稿采用、草稿保存回读，每次把结果写入该阶段已有记录后，协调方运行同一个交接命令。中断后也从同一入口继续。

```sh
python scripts/stage_context_cli.py handoff --project '<业务项目绝对路径>'
```

程序在 `next_actions` 给出已就绪任务，在 `steps` 说明等待范围和原因。编导整稿采用后，直接把同版叙事包和所需资产 ID 交给资产 Skill，导演拍法待选时也可开始盘点、复用及准备。实际出图沿既有授权，候选图按原通道审核。普通阶段沿返回的 `context_id` 使用现有交接卡。视频和草稿接续依赖已就绪的镜段。显式目标用于绑定现有任务的接收者、图片批次或按已选兼容流程办理。

只读查看时运行 `status --project '<业务项目绝对路径>'`。已登记开工决定的普通项目用同一套交接判断返回 `next_actions` 和 `steps`。有未完成的提交事务时，先沿修订恢复入口处理该事务。

导演稿采用后，分镜直接核实际镜号所用的已确认图。在现有[视觉检查记录](../rules/visual-production.md#导演到视频的视觉交接)登记导演版本、镜号、引用和结论。同一依据的普通镜头可成组记录，具体缺图才发补图请求。缺本版分镜检查记录时，程序暂停对应镜段写词。声音与生成沿本轮有效决定和授权，默认收口为正式草稿保存、回读及排版验收。协调方交用户审查前在原交付回执核同批布局。

| 当前要做的事 | 直接读取 | 交接点 |
| --- | --- | --- |
| <a id="开工确认单"></a><a id="使用已选产品参考包"></a>新项目开工或补齐决定 | [开工确认单](../rules/project-layout.md#开工确认单)，选用商品包时看[产品参考包](../rules/project-layout.md#使用已选产品参考包) | 登记来源、范围及当前工作依赖的决定 |
| <a id="核对真实使用的固定演示窗"></a>写编导稿 | [编导 Skill](../../skills/creative-director/SKILL.md)，广告真实使用且演示时长固定时查[固定演示窗](../rules/project-layout.md#核对真实使用的固定演示窗) | 交准确版本给用户审核 |
| <a id="编导文本送审命令"></a><a id="编导对话确认"></a>编导送审或采用 | [网站送审](review-workbench-operations.md#编导文本送审命令)、[网站通过](review-workbench-operations.md#编导网站决定与采用)或[对话确认](artifact-adoption.md#编导对话确认) | 整稿采用后直接交资产 Skill，导演按已有拍法决定和授权接续 |
| 对标编导整稿已采用，选择拍摄路线 | [开工记录中的路线选择](../rules/project-layout.md#开工确认单) | 路线待选时主动给用户两个选项，按用户选择登记后再运行同一交接命令。导演重拍按导演稿逐镜写词。深度复刻按已采用编导稿分段，剧情用深度视频，产品用目标产品实操参考，直接交视频提示词 Skill。仅改变戏剧动作或人物互动的段落交导演。 |
| <a id="审核站-api-对接要求"></a>资产制作或审核 | [资产 Skill](../../skills/asset-image-assistant/SKILL.md)、[对话逐项确认](../../skills/asset-image-assistant/references/05-asset-manifest-handoff-guide.md#状态规则)或[网站送审](review-workbench-operations.md#资产图送审)与[通过](review-workbench-operations.md#资产网站决定与交接) | 逐项交接已确认且可取用的输出 |
| 在红帧生成资产图或补图 | [红帧生图 SOP](../../skills/_shared/redframe-operations.md#生图) | 上传素材卡后当场归位，以原生连线和正文引用核对实际生成输入 |
| 在红帧上传素材、排版或批量整理 | [素材区上传](../../skills/voiceover-to-video-script/references/redframe-canvas-usage.md#素材区上传)、[命名与排版](../../skills/voiceover-to-video-script/references/redframe-canvas-usage.md#命名与排版)、[批量修改节点](../../skills/voiceover-to-video-script/references/redframe-canvas-usage.md#批量修改节点) | 只读实际要执行的小节，核对素材归位与旧节点状态 |
| <a id="网站往返"></a>查退回意见与附件 | [网站往返](review-workbench-operations.md#网站往返) | 按受影响范围返修新版本 |
| <a id="本地流程授权与自主采用"></a>完成导演、按需分镜或视频稿 | [导演](../../skills/storyboard-planner/SKILL.md)、[按需分镜](../../skills/storyboard-director/SKILL.md)、[视频提示词](../../skills/voiceover-to-video-script/SKILL.md) | 全稿自查后按[首次采用](artifact-adoption.md#首次自主采用)或[自主修订](artifact-adoption.md#自主修订)办理 |
| 从导演和分镜交接到视频写词 | [导演到视频的视觉交接](../rules/visual-production.md#导演到视频的视觉交接) | 按实际镜号核场景母图、动作参考与关键位置关系，交已就绪的镜段 |
| 写入正式视频草稿并交画布审查 | 红帧走[成批写入与回读](../../skills/_shared/redframe-operations.md#成批写入和整节点回读)及[排版验收](../../skills/voiceover-to-video-script/references/redframe-canvas-layout.md#执行与验收)，小云雀走[平台交付与排版](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#小云雀网页草稿) | 配齐原生视频节点、归位相关素材、保存刷新。完成字段回读、全景和正常审阅缩放的局部验收后，附准确画布链接、登录入口和段号，交用户在正式节点内审查 |
| 提交已明确授权的视频生成 | [已授权生成的取证](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#已授权生成的取证) | 复用覆盖本次范围的有效授权，按准确节点提交并保留原任务身份。结果未知时先只读查原任务 |
| <a id="agent-审核-api"></a><a id="接线现状与开发交接"></a>接入、换站或核对接口限制 | [项目送审接口合同](review-api-contract.md)，现网差异见[现有工作台适配](review-api-contract.md#现有工作台适配) | 开发联调沿对应接口和验收用例 |
