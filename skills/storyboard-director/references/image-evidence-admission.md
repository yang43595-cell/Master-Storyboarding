# 成图观察证据与活动引用

沿用原请求、原图登记和同稿自查，不建立另一套批准账本。验收及采用分支以 [Skill SOP](../SKILL.md#本轮执行-sop) 为准，观察证据本身不产生人审或正式采用。

## 先看图，再记录

原尺寸查看候选与它实际继承的原母图/实拍，以及准确导演/请求版本；必要时并排放大衣袖纹理、接触与产品阶段。每项写可见事实和位置，不能只写“合格”或复述提示词。坏像素不能用“只参考手势/中间态/限定用途”抹掉：失败图不进入本次活动引用，需真实修订或选择另一有效来源后重查。未经核验保持 unknown；不自动改历史采用与确认。

检查七项：身份衣装、双手和承托、产品几何、人物/首饰/道具归属、空间常驻物、产品阶段、前后边界。只对真实适用项下观察结论，无关项写 not_applicable 及理由/来源位置，不补造故事。对膜态逐额头、鼻外、左右颊、下巴核对；对接触动作核所固定瞬间的手中物、支撑、接触和落点，静帧不能证明动作过程。图片源本身是真实拍摄还是生成须如实说明，程序不把所有图称为生成图。

## 在现有登记中保存

`visual-supplement-image` 的既有 artifact entry 添加 `quality_evidence`，不更改 status、user_approved、confirmation_ref 或 current：

- `status`: pass / fail / unknown；`observer`、带时区 `checked_at`。
- `image_sha256`: 本张图准确字节；`source_files`: 实际比较的原图及导演/请求文件 `{path, sha256}` 数组（项目内准确路径，至少一项，不是文件夹）。
- `uses`: 已实际核过的确切用途数组，各项为 `{source_shot_ids: [镜号], roles: [职责], entity_ids: [实体], state_id: 状态或null}`，三个数组按字典序保存；同镜更换职责/实体/状态也使旧用途审查失效。canvas按当前真实reference_checks比对，不以重新给draft签hash跳过图审。
- `source_shot_ids`: 实际已核用途镜号；`view`: `{resolution: "original", locator: "实际查看的文件及局部范围"}`。
- `checks`: 七个键 `identity_wardrobe / hands_support / product_geometry / ownership / space_objects / product_phase / boundary_state`；各含 `result`（pass / fail / unknown / not_applicable）、`observation`（具体可见事实；无关项写理由）、`locator`（原图/候选中位置或原稿定位）。

新活动引用执行既有入口：

`python -m scripts.core.asset_refs --project <项目> --require-reference <准确ART-ID> --for-shots <本次镜号...>`

缺证、失败、未核、源或图片hash变化、镜号超范围均拒绝本次引用。asset_refs只检查通用图审与镜号可用性、不承诺特定职责通过；具体职责准入由canvas消费者对照uses完成。记录仍可作为候选保存供修订，旧确认也保留；检查不升级用户确认，不能替代已有正式来源门禁。改图、改来源或用途后重新真实查看并记录，不更新hash来沿用旧观察。

**能力边界：**程序只查声明完整性、字节一致和范围，不识别衣纹/覆膜/物理动作，不认证观察者身份，不证明已看图。人工假填完整观察仍可能骗过程序，因此不得把程序通过称为视觉合格。真实观察仍是必要步骤；兼容路径保留用户终审，统一入口按授权自行验收，不伪造用户逐图确认。该入口覆盖登记补图，不拦截所有浏览器或供应商旁路；AST母图仍沿原入口，不能声称本次已给所有图像加统一技术沙箱。
