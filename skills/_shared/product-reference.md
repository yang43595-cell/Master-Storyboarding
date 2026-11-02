# 产品参考

- 广告产品及明确要求还原的实物外观（含包装、拟人元素、服装或道具）先核对项目与 `assets/product-references/` 中的真实参考。
- 不用概念色、行业惯例或模型猜测替代参考图事实。
- 上述实物还原缺少足够参考时阻塞相关项，不虚构。普通剧情物品（PROP）按 PROJECT_RULES.md 的“用户调整资产图需求”执行：无参考可按名称、剧情用途及用户方向设计候选，有品牌名不自动变成广告产品；已有参考须遵循，未定外观交用户审图，不声称已核验实物。

## 出镜包装不能由动作或传输检查代验

先依据当前商品 brief、原文及用户决定逐项识别出镜包装：对应商品、SKU、版本、来源依据与镜号。未知就保留未知，不能为了继续制作改写成普通道具；也不能仅因“盒子”或品牌词出现把真正普通 PROP 当商品。已有参考但尚未批准采用的新商品版本不得自行升级为真值。原文排除商品身份、而本镜要求商品还原时须先解决冲突，不靠一句保真提示覆盖。

沿现有导演 shot 的 `packaging_requirements` 记录 `entity_id / classification / source_basis`，商品项另带 `product_reference={product_id,sku,version}`；classification 为 `product / ordinary_prop / unknown`。不新建商品账本，不改已确认稿；需要修订时另存候选。已选导演的要求在正式交付中不能被删项或降为普通道具；同现有后态合同，携准确视频源及 source_segment_id，核镜号映射，不能改成其他镜号绕过。

本轮**正式新提交**的现有 `quality_evidence.checks.packaging_identity` 必须明确范围：
- 无出镜包装：`scope=none, items=[], result=not_applicable`，观察和定位写清准确范围；这不是包装还原通过。
- 有包装：`scope=present, result=pass`，items 逐项写上述实体、分类、依据、`source_shot_ids`；普通道具写 `prompt_claim=ordinary_prop`。商品项带目标 product_reference、`prompt_claim=product` 及实际正文中的逐字 `prompt_quote`；未知归属不能通过。
- 核正文是否否定该商品身份，并核所选参考真的覆盖它。代码仅检测声明一致和少量已知否定表达，不替代全文语义阅读，也不能发现被故意遗漏的出镜物。

商品包装参考复用现有 artifact 登记的 `product_reference`，保留 `product_id / sku / version / reference_role=packaging / evidence`，`source={path,sha256}` 指向实际对照的准确商品源文件或 brief，`coverage` 明确 `shape_proportions / opening_structure / color_marks / product_version`。实际 `reference_checks` 携同一登记值，并以 appearance 职责、准确 entity_ids 和镜号使用真实图片；hash 正确只能证明字节，不能证明 SKU 或采用依据。来源登记与用户确认仍按原合同，不能用合成字段或无来源的 evidence 代替批准。

已有图片 `quality_evidence` 内增加包装专项 `packaging`，沿原 observer、带时区 checked_at、checks/result/observation/locator 记录四项逐项并排观察，四项均须实际可核；遮挡/分辨率不足或未查看保持 unknown，不能用 not_applicable 充当包装通过。`product_sha256` 为上述 product_reference 的 fingerprint，准确 source 同时列入 source_files；uses 自动绑定商品版本和用途。换版、换SKU、换来源或用途后旧 QA 失效，只重核受影响用途，不覆盖旧图或原确认。

视频结果审核也须在**原有 QA 记录**明确列出同四项及准确输入商品版本、视频/帧与时间位置，动作抽查、草稿导入或四引用一致都不能写成包装通过。本仓库当前没有统一的视频结果包装验收消费入口；本次代码覆盖正式提交与图片引用 QA，不冒称已自动拦住所有成片批准路径，不新增平行结果台账。实际看图/看片、确认商品来源及用户采用决定仍必须完成。

## 可见包装原件版本与物理SKU分开

有明确SKU/物理版本要求时，继续使用原 product_id/sku/version 分支，不得降级。仅当本镜确实只使用已确认产品的闭合包装可见外观、不依赖批次规格时，可用互斥分支：product_id、product_name、packaging_reference_revision={sha256}、physical_variant={status:not_applicable,scope:visible_packaging_only,basis}。原件hash仅标记包装参考字节，不是商品物理版次；未知用途不能标不适用。

basis 必须含实际来源 source={path,sha256}、准确 source_shot_ids、具体 rationale、visible_faces（front必含，可加实际有依据的top/right/left）以及全部排除项 excluded_uses=[back_face,opened_packaging,formula_or_specification,efficacy_claims]。两份来源文件都须存在并核hash；所选导演若要求具体SKU/版本，下游不得用此分支替换。以上是须实际核对的用途声明，不是自动识别正文或像素语义。

仍须逐项实际包装QA，product_version 此时核所选原图外观版本，不核物理批次。包装QA须写 version_scope=source_artwork_only、physical_variant_verified=false；basis.source 同列 source_files。本镜用途和可见面纳入现有uses完整指纹，改用途/源版本就使旧QA失效。未来翻背、开盒或要求规格功效时须回上游取得相应真实来源/身份要求，不能沿用visible-only。未完成图审、未确认资产、未取得真实原生绑定者继续阻塞，不因新身份分支而放行。
