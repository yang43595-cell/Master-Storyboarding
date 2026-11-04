# 编导文本送审 API 现网验收

日期：2026-11-05（Asia/Shanghai）。实施分支：`codex/review-text-publish`。用户批准独立合成案例的真实创建、发布、精确重试、新修订及网页核对；不包括人工批准、真实业务稿或下游生产。记录形成时尚未提交 Git，前序工作保留；后续提交与合并以 Git 历史为准。

## 实现与本地校验

- [agent_review_api.py](../../scripts/core/agent_review_api.py)：标准库 HTTPS 客户端，Bearer 鉴权、JSON POST、禁止跳转、超时不自动重试、有限回包、文档字段与返回身份核验；密钥不进入响应、回执或错误正文。
- [review_text_cli.py](../../scripts/review_text_cli.py)：`prepare`、`create-case`、`publish`、`status`；只送 `script`。复用已有完整阅读导出，发布前核同版 JSON 来源及 Markdown 精确字节，回执独占新建并绑定源修订与双份 hash，不改本地确认或采用。
- 命令与凭据使用方式见[编导文本送审命令](../development/review-to-canvas-entry.md#编导文本送审命令)。未新增依赖、服务或自动调度，也未改旧 v78 编译器。

相关客户端、CLI、编导 JSON、来源与正式采用回归 **128 项通过，无失败／跳过**。其中新客户端 55 项、CLI 16 项；测试包含拒绝跳转、失败不自动重发、错误不泄露密钥、UTF-8 字节上限、完整导出、源稿／投影漂移、项目外路径、回执不覆盖与旧修订保留。没有把测试用模拟批准发送到网站。

## 真实案例与结果

只新建了一个明确标记的合成案例：`13`，名称 `MSB API 合成验收 20261105-003954 940C31F6`，服务端项目 `four-ad-review`。源包项目标记为 `MSB-API-SMOKE-20261105-940C31F6`，R01／R02 均为 `candidate`、无对白；只有一个“无需独立图”的稳定 AST，没有图片制作／上传，没有本地 manifest 或人工确认。

| 检查 | 实际结果 |
| --- | --- |
| 创建后读状态 | 案例 `13`，script head 为空，generation 为 0 |
| 首次 R01 发布 | `assetId=d7549fc59c5b4dbf9781ccca`，version 前缀 `9c8577088451`，generation 为 1 |
| 原请求精确重试 | 相同 UUID、案例、文件名、全文与用途；整个返回与首次相同，随后 status 仍为同版第 1 代，没有创建第二个发布版本 |
| R02 新修订发布 | 新 UUID、新 JSON 修订、新 Markdown；version 前缀 `ac2a19ed536a`，generation 为 2 |
| R02 状态回读 | `head.version` 与 R02 发布回执完全一致，`head.generation=2`，`sourceScriptVersion` 为同一 R02 version |

[R02 现网准确版本](https://yangyangyang.click/?projectId=four-ad-review&scope=live&caseId=13&stage=script&version=ac2a19ed536ac4ac140d1aea0d401d270b3f0d8a837472b6473349ef4b5561a2)。完整版本、操作 UUID、输入 hash 和原始回包在实际回执中；上述结果来自现网请求，不是文档成功示例。

服务端 version 是其内容模型的 SHA256，不是本地源 JSON 或 Markdown 的文件 hash；通过同一份绑定回执建立对应，不混为一个值。本轮没有重启服务、并发改稿或验证跨进程幂等，也未改变授权或部署现网。

## 网页正文回读与登录历史

首次实际设置为 `gpt-6.1-terra` 的执行者用 Chrome 打开上述精确 R02 URL，页面跳至 `https://yangyangyang.click/login`，标题“登录 · 私人工作空间”。执行者在登录处停止，未读取凭据、点击登录、审核按钮或生成按钮。当时没有看到测试正文、STP 编号或结构化附录，因此首次网页正文验收未完成。

用户随后指定“浏览器用edge”。同一实际模型的执行者改用 Microsoft Edge 打开准确 R02 URL，最初 Edge 会话也跳到相同登录页，标题仍为“登录 · 私人工作空间”；新增 `ui/edge-login.png` 和 `ui/edge-verification.json`，保留 Chrome 历史截图。此时保留待登录范围，没有把指定浏览器当成已登录。

用户明确回复“Edge 已登录，继续核对”后，实际模型执行者已进入已登录工作台，从真实可见的案例 `13` 控件进入当前编导稿。页面标题“刘洋 · 个人工作台”，外层 URL 保留案例 `13` 和准确 R02 version；正文显示准确案例名、R02、两段 STP、新句和完整 JSON 附录，状态为“正在查看当前稿”“待审核”。未执行批准、评论、修改或生成。

协调方独立解析网页提取的 JSON 附录，与发布回执绑定的 R02 源 JSON 做完整深度比较，全字段相等；两段故事原文逐字存在于阅读正文，R01 旧末句不存在，稳定 STP／AST 与 candidate 状态保留。另一次只读 API 回查确认当前仍为同一 R02 version、第 2 代，`expectedReviewRevision=0`。最终内容核对记录为 `completion-verification.json`，与初次 API-only 记录分开保留。

已观察到深链定位限制：发布回包 URL 初次显示项目总览，必须从实际案例 `13` 入口进入正文，不能仅凭外层查询参数判断已打开准确稿件。该服务端／前端接线未在本轮修改。执行者通过已可用的后台 Edge 页面文本读取完成回读，未抢占用户在其他应用的操作；未取得正文的新鲜截图，也未保留呈现旧总览的截图作为正文证据。`status` API 只返回 head 和 expectations，正文通过依据来自实际 Edge 页面文本，不是 API 元数据。

本次实现仍不支持资产媒体写入，也没有任意新案例的批注／准确批准读取。MVP 把 Markdown 存为一个 `body` 块；虽然编号和完整 JSON 保留为正文，尚未变成网站原生逐项锚点。真实发布、网页可见和人工批准分别核对，不能把其中一项当成其他项。

## 本机证据位置

全部测试业务文件在源码仓库之外：

`/Users/liuyang/Desktop/Master-Storyboarding-test-runs/review-text-20261105-003954-940C31F6/`

- `02_编导/`：R01、R02 源 JSON 与只读阅读稿；旧版本均保留。
- `receipts/01-create-case.json` 至 `09-Edge-session-status.json`：实际 9 次请求的独立回执，共 4 次 POST（创建、首次、重复、新修订）及 5 次 GET。
- `api-verification.json`：API 对应关系与已核检查，明确保留 `body_browser_verified:false`、不推断批准。
- `ui/chrome-result.png`：实际登录页截图。
- `ui/edge-login.png`、`ui/edge-verification.json`：依用户指定的 Edge 实测登录缺口。
- `ui/edge-r02-body.txt`、`ui/edge-r02-story.txt`、`ui/edge-r02-observation.json`：Edge 登录后实际回读与操作记录；原始页面含导航选择器元数据，定向正文另存。
- `ui/edge-r02-story-only.txt`、`completion-verification.json`：协调方从实际读回文本提取的正文、完整 JSON 深度比较及最终核对结果；`body_browser_verified:true`，批准仍未推断。
- `validation/pytest.log`、`validation/result.json`：本轮相关回归日志与命令。

密钥只由运行时从本机文件读取，未进入源码、聊天或证据。真实案例与两个待审版本保留；本轮不删除案例、不伪造批准或改本地项目状态。
