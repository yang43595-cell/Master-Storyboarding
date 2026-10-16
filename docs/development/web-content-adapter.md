# 单一业务 JSON → 网页 ContentPackage（离线适配）

实际网页包：Library `libfile_50d8ae386fac8191a9723472939b35ef`，404,312,480 字节，SHA256 `607421269da75dd3e9ee877690cbd48f7a28780b26aba8eb37ed2526f83a0977`。本轮只读取该包，未访问站点、发 POST、上传、发布或部署。

`references/web-contract-v78/` 保存原样的 `content-contract.ts` 与 `package-metadata.cjs`，逐文件 hash 见 PROVENANCE.json。这是实际 v78 可执行契约的固定验证快照，不称为当前线上部署或另写一套 schema。调用方已有更新的已核网站源码时可传 contractRoot，回执报告实际使用的两个文件 hash。

## 最小接口与真实边界

`compile_content(raw_bytes, stage=..., case_id=..., source_script_version=..., expected_version=..., scope=..., media_registry=...)` 从唯一业务 JSON 内存编译，调用原网页 `PublishRequest`（含 `ContentPackage`）验证，返回生成的 request、可由原发布 helper 消费的内存 mediaMap、实际网页 canonical hash、契约指纹及 `published:false`。不会生成 Markdown、第二份可编辑稿、批准事实、网络请求或发布副作用。

`sourceScriptVersion` 与 `expectedVersion` 必须显式提供。真实值应来自既有站点读取：当前已发布 script head；尚无 script package 时使用已核 legacy script hash；expectedVersion 来自本阶段 current pointer，仅确认首包时使用 null。不能把编译出来但未发布的候选 hash 当作真实 script head，也不能从 R01 等本地修订号猜值。离线 schema 校验不能证明参数仍在线上当前，实际发布仍须原服务端 CAS 和来源重查；本适配器不执行发布。

原 schema 支持案例 01–06 和六个 stage。bound script 必须有 benchmark-reference，source-bound assets 的 PublishRequest 必须有 asset-layout。真实资产包可携带实际 benchmark；schema 并不要求给每个资产包再复制一份 benchmark。source-binding 中 source_sha256 是实际输入字节摘要，native_sha256 是同一业务 JSON 规范表示的摘要，不替代网站 script head。

## 已实现与未恢复的信息

| stage | 现有 JSON 足够的范围 | 明确缺口 |
|---|---|---|
| script | narrative 2.0 的 STP 完整原文、对白、锁定事实及全部原字段；沿用稳定 block ID | bound script 缺真实原视频、海报、片段来源或媒体映射就拒绝，不造 benchmark |
| director | storyboard v4 的场景、拍法、起/过程/止、对白、声音、节奏与全部原字段 | 不把普通 shot ID 伪装为 Case 01 专有 DR05 卡片；固定 renderer 保留通用阅读视图 |
| assets | manifest 2.0 已明确登记的 confirmed_outputs.purpose/view/state、段落/镜头范围、must_not_inherit，派生 asset-layout | 只有旧 confirmed_asset.path、缺逐输出职责或仍有候选而缺对应信息时拒绝；不猜 assets-v2 特定分组、来源或限制 |
| prompts | 现有完整 drafts 的正文、参数、逐实际输入的参考，以及原 JSON 全字段 | 没有准确媒体映射或引用遗漏就拒绝；声音媒体不在本网页 Media 类型内，不假装可上传 |
| white_model / storyboard | 已有 files 登记及实证 PNG/JPEG/WebP/MP4 的参考视图 | 仅参考视图，不开通整阶段批准；缺 files、GLB/Blend 等不支持媒体、完整 assets-v2 接入未恢复时明确拒绝 |

所有阶段都保留从同一输入生成的只读 canonical-source block，避免未映射字段静默丢失；它不是另一份业务稿。script renderer 将非正文内容放入来源区，其他阶段沿用固定 generic/asset 阅读能力。不修改 renderer 或已有线上审核锚点/反馈。新包不会继承旧包批准。

资产的 origin/status 用已有参考视图的 reference 表示，不将项目图片确认转换为网页批准，不按文件名判断来源、角色或分组。无法支持的新 assets-v2 信息必须从真实差量恢复后另加适配，不能把合成 fixture 当作该差量。

## 媒体证据，不造 URL

media_registry 是现有传输/回读事实，不是另一个内容草稿。键为 `AST-ID@OUTPUT-ID`、视频 `artifact_id@revision` 或已登记预览文件路径。每项提供既有 id、localPath、url、sha256、mime、bytes、caption（可选），以及原发布 helper 实际逐媒体回读行 `readback: {mediaVerified, sha256, bytes}`。编译器不产生这些证据、不构造 URL；核对本地文件、实际 hash/大小、图片 MIME 和回读摘要后，将已给出的不可变私有 URL 送入原 schema。服务端仍需验证远端对象是否存在；本轮没有取得实际线上媒体回读，golden 的媒体和回读均为明确合成测试夹具。

benchmark 参数是实际 sourceUrl/sourceVideoId/author/clipStartSeconds/clipEndSeconds/clipLabel/videoMediaId/posterMediaId 与 videoKey/posterKey；两 key 指向上述实证媒体记录。禁止用测试用来源或 URL 替代真实案例。

## 运行与反馈

使用 Node 24（本轮 24.19.0）与原网页声明的 Zod 3.25.76。依赖安装在隔离目录，不装整个网页框架或运行其构建/部署脚本：

```sh
npm install --prefix <隔离依赖目录> --ignore-scripts --no-audit --no-fund zod@3.25.76
NODE_PATH=<隔离依赖目录>/node_modules python -m scripts.core.web_content
# 由调用进程提供 stdin JSON；无需另建业务稿
```

stdin 包含 sourcePath（现有 JSON）、stage、caseId、scope、sourceScriptVersion、expectedVersion、mediaRegistry，以及按需 benchmark/title/reviewable/contractRoot。stdout 只是编译回执，调用者可直接在内存消费 request，无需落成另一份稿件。默认 reviewable:false；schema 通过、项目确认和网页批准均不互换。生成提交仍走现有修订、素材与内容检查。

`effective_feedback()` 只消费实际已读的 `currentState/currentFeedback`：核对 scope/case/stage/version、current pointer、来源 hash 与 stale 状态，不拿内容 GET 的默认 approval:pending 或历史列表顺序当真实结论。缺 currentState 返回 unknown；旧版本、旧来源或不可审核包分别保持 historical/stale/not_reviewable。读取不会把意见登记为已处理，也不修改项目用户确认。分页未结束会明确 historyComplete:false；全量历史仍由原读取工具按游标取得。

## 本轮证据

21 项定向测试，案例 01/05/06 × script/director/assets/prompts 共 12 份固定 golden version，全部通过原执行 schema；另外检查参考视图边界、缺 benchmark/布局/媒体凭据、MIME/hash/URL 反例、显式 expectedVersion、stdin 只读入口及当前反馈有效性。测试只解码固定的微型合成字节，不接站点、不生成生产媒体。未运行网页全部媒体保全套件或 hosted QA，未重跑 Windows111。

长来源视图按原 schema 的 UTF-16 字符上限在内存分块，拼接可完整还原同一 JSON；不截断、摘要或要求用户另建文档。请求总量及 block/media 上限仍由原执行契约验证。
