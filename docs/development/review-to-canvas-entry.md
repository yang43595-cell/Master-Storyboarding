# 审核到画布入口

[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)规定谁审核、何时采用和做到哪里。只打开当前任务对应的小节。跨阶段协调时用下表找交接点。

| 当前要做的事 | 直接读取 | 交接点 |
| --- | --- | --- |
| <a id="开工确认单"></a><a id="使用已选产品参考包"></a>新项目开工或补齐决定 | [开工确认单](../rules/project-layout.md#开工确认单)，选用商品包时看[产品参考包](../rules/project-layout.md#使用已选产品参考包) | 登记来源、范围及当前工作依赖的决定 |
| <a id="核对真实使用的固定演示窗"></a>写编导稿 | [编导 Skill](../../skills/creative-director/SKILL.md)，广告真实使用且演示时长固定时查[固定演示窗](../rules/project-layout.md#核对真实使用的固定演示窗) | 交准确版本给用户审核 |
| <a id="编导文本送审命令"></a><a id="编导对话确认"></a>编导送审或采用 | [网站送审](review-workbench-operations.md#编导文本送审命令)、[网站通过](review-workbench-operations.md#编导网站决定与采用)或[对话确认](artifact-adoption.md#编导对话确认) | 整稿确认后采用，解锁导演和已授权资产 |
| <a id="审核站-api-对接要求"></a>资产制作或审核 | [资产 Skill](../../skills/asset-image-assistant/SKILL.md)、[资产送审](review-workbench-operations.md#资产图送审)、[网站通过](review-workbench-operations.md#资产网站决定与交接)或[对话确认](../../skills/asset-image-assistant/modules/project-web-mode.md#资产对话确认) | 逐项交接已确认且可取用的输出 |
| <a id="网站往返"></a>查退回意见与附件 | [网站往返](review-workbench-operations.md#网站往返) | 按受影响范围返修新版本 |
| <a id="本地流程授权与自主采用"></a>完成导演、按需分镜或视频稿 | [导演](../../skills/storyboard-planner/SKILL.md)、[按需分镜](../../skills/storyboard-director/SKILL.md)、[视频提示词](../../skills/voiceover-to-video-script/SKILL.md) | 全稿自查后按[首次采用](artifact-adoption.md#首次自主采用)或[自主修订](artifact-adoption.md#自主修订)办理 |
| 写入并回读画布草稿 | 红帧走[成批写入与回读](../../skills/_shared/redframe-operations.md#成批写入和整节点回读)，小云雀看[平台交付](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#小云雀网页草稿)和[排版](../../skills/voiceover-to-video-script/references/seedance-delivery-package.md#画布紧凑排版) | 保存、刷新回读、核布局后交草稿，用户点击生成 |
| <a id="agent-审核-api"></a><a id="接线现状与开发交接"></a>接入、换站或核对接口限制 | [项目送审接口合同](review-api-contract.md)，现网差异见[现有工作台适配](review-api-contract.md#现有工作台适配) | 开发联调沿对应接口和验收用例 |
