# v78 网页审核修复：补丁交付

**当前项目适配器连接：固定 d0ee6ce 版本，15/15 通过。af212c 的 10/11 历史红灯与原测试保留供追溯；仅限真实本地 D1/R2，不代表生产、浏览器或自动采用验收。**

这是针对精确 Sites v78 源码的补丁和验证证据，不是完整站点源码，也不是主流程或 Windows 迁移实现。保留全部历史静态内容结构需要外部源文件和媒体，因此本分支没有复制删减版站点。禁止将本目录直接部署。

基线：`5d2e1c451c6e428f92f50788d75939a37b39fec7`，Sites 项目身份及全部 717 个文件的路径、字节数、SHA256 见 `v78-source-manifest.json`。该清单是来源证明，不包含文件内容。修复只涉及 `provenance.json` 列出的五个文件。临时执行器曾将归档初始化成新 Git 仓库，其根提交不是 Sites 历史；本分支从 GitHub main `235864def0d4341d4bdda43394d0dfa355e5dcb7` 新建，没有导入归档根历史。

## 修复范围

`content-subscription.js` 三处最小改动：优先使用包内 benchmark；未声明 source-binding 的旧包保留上游已验证、同 case 的 registry 视频；bound 新包缺 benchmark 时明确提示缺失，不套用旧或其他 case 视频。声明但无效的元数据继续失败关闭。未改 CSS、布局组件、审核结论、历史 MD 或任何线上数据。

两个测试文件覆盖当前/历史版本、全部六阶段、包内来源优先、缺失及无效来源和无自动播放。启动测试精确允许恢复的视频触发既有的只读媒体状态请求，内容请求仍全部校验 caseId。

契约说明保留历史 MD 回看；新 ContentPackage 发布不强制 MD。source-binding 的 source/native hash 当前仅验证格式，不证明与外部实际输入字节一致。内容 GET 的默认 pending 不是审批凭据，审批应读 currentState/currentFeedback。保存反馈不代表 agent 接单，本轮没有增加持续服务。

## 资产媒体状态加载补修

独立复核确认首轮修复仅保留了视频来源 HTML；`app.js` 仍将 assets 排除在媒体状态加载之外，所以真实 Case05 registry（没有 verified/available 字段）在资产页仍只有链接。本次去掉该阶段排除条件，保留同 case/currentRoute 异步校验、已有 video 不覆盖、真实 available 状态和来源身份匹配。没有写入或伪造 verified。

`tests/content-bootstrap.cjs` 新增四组真实 app/subscriber/asset-controller DOM 测试，使用真实 Case05 registry 与合成阶段内容：资产启动、资产↔导演阶段链接切换、状态缺失/404/失败/错误身份、bound 缺失和包内视频保护，以及旧 case 延迟请求不污染新 case。新增测试在首轮修复上红灯，补修后绿灯。

`changes.patch` 已更新为从精确 v78 一次应用的完整补丁；只对已经应用首轮四份 patch 的 checkout，使用 `hydration-increment.patch`。不要重复叠加两者。`patches/` 现在共有六份，最后两份为本轮测试和修复。Library 初始 repair ZIP 保持原样，它不包含本次补修；本 GitHub 分支是更新后的交付。

## 恢复与重现

通过当前 Library 官方物化流程取回 `libfile_50d8ae386fac8191a9723472939b35ef`：

- 文件：`four-ad-review-v78-test-source-20261017.zip`
- 字节数：`404312480`
- SHA256：`607421269da75dd3e9ee877690cbd48f7a28780b26aba8eb37ed2526f83a0977`

该包包含完整源码、锁文件、schemas、测试与跟踪媒体。先验证 ZIP 的大小与 hash，再解压到独立目录。不得从网页猜下载地址或复制凭据。

以下命令中的路径须替换为实际绝对路径。`PATCH_DIR` 指本目录，`RESTORED` 指解压后包含 site-source 的目录；不覆盖已有 checkout。

```sh
python3 "$PATCH_DIR/tests/verify-restored-source.py" "$RESTORED"
cd "$RESTORED/site-source"
git apply --check "$PATCH_DIR/changes.patch"
git apply "$PATCH_DIR/changes.patch"
node "$PATCH_DIR/tests/benchmark-source-selection.cjs" "$RESTORED/site-source"
# Node >=22.13；依赖缺失时按项目 TESTING.md 使用官方 installer。
# 缓存路径指向可写位置；不修改锁文件。
npm_config_cache=/absolute/writable/npm-cache npm run install:ci
npm run verify
```

组合 patch 和 `patches/` 六份顺序 patch 是同一变更的两种应用方式，只选一种。源文件校验必须在应用之前执行，应用之后预期有五个文件与基线不同。

无需媒体的独立合成测试读取恢复后的真实 renderer 和元数据模块；48 项检查在修复后通过，原版失败。它不代替真实 DOM、媒体保全或完整门禁。既有 717 文件清单已在原始恢复时全部核验。不能声称缺少完整媒体也能跑完整保全测试。

## 验证与限制

- 原版与最终 supported collection 均为 36/38 通过；27 historical 和 5 environmentRequired 未运行。
- 两项既有失败：`case01-dr05-r1.cjs` 依赖缺失 Git 对象 `795ff4419599eaaf4b72904848dbb973650e38dd`；`case01-r08-candidate.cjs` 依赖 `f01b834152b02124f34d935c2d77be03c4e7227f`。归档不含这些对象，须由真实历史仓库或经验证的历史 fixture 补齐，不能用当前文件冒充；未跳过或修改这些测试。
- 订阅测试 49/49、启动测试 23/23 通过；typecheck、lint 和实际生产构建通过。`npm run verify` 仍会因上述两项停止，不能标为全绿。
- `logs/` 是本地合成/隔离测试日志，`hydration-*.log` 为补修验证；未包含线上快照、真实审批记录、客户输入、凭据或媒体。已扫描提交候选中的凭据模式、签名链接、PII 和禁入文件。
- 没有可用浏览器工具，本轮未执行浏览器视觉 QA。确定性 DOM 测试不证明浏览器外观、播放或认证行为已验收。
- 未部署、未推送 Sites 源仓库、未改 D1/R2、未提交线上表单、未改变可见性或配置 CI。

此分支是待审核补丁交付。没有合并 main，也没有改主流程审计分支。


## 独立真实本地存储集成

`tests/persistent-review.cjs` 使用已有依赖中的 Miniflare/workerd 启动全新、仅 loopback 的本地 Worker。薄路由只转发到实际 content/content-feedback/content-media/review-attachments/MCP 路由，未替换 D1、R2 或业务存储函数。所有外部 fetch 均阻断；不读取生产 wrangler 配置、真实登录信息或远端数据库。

本次 14 项检查在四个独立 Node/workerd 进程中通过。D1 使用 Miniflare 的实际 SQLite 持久化文件，R2 使用其本地持久化对象存储；关闭并重启进程后重新读取审核回执、内容图片和参考图原字节，验证 SHA256、range 和 MCP 图片读取。它比原内存 R2 替身验证更进一步，但不是远端 Cloudflare/Sites 验收。

覆盖新审核保存与刷新、currentState/currentFeedback 与 MCP read_case_reviews 一致、同 ID 重试/冲突、两并发发布 CAS、并发审批只生成一条、审批后的修改意见不被旧回执覆盖、历史审批不继承到新版本、QA/主体隔离以及来源更新后的 stale 拒批。通用 content GET 的默认 pending 不作为审批证据。局部身份仅为本地合成 header，未创建凭据，不能证明 Sites 平台登录策略。

```sh
node "$PATCH_DIR/tests/persistent-review.cjs"   --source "$RESTORED/site-source"   --output /absolute/writable/isolated-test-results
```

先按前文恢复源码并应用完整修复，安装原锁文件依赖；需要 Node >=22.13、可运行 workerd 的主机和可写本地目录。无需部署、远程账号或网络隧道。每次执行创建新的隔离目录，内部 `live` 只是本地命名空间，用于验证 MCP 正式读取契约，绝非生产写入；测试数据全部为合成内容。该脚本不纳入原 38 套自动门禁，须明确手动执行，缺运行能力时报告失败，不能换成内存 mock 宣称通过。

`local-persistence-report.json` 和 `logs/local-persistence.log` 为本轮结果。数据库、R2 对象、Worker bundle 和状态文件只留执行器，没有提交。上述14项存储测试没有执行外部项目适配器；下节记录后续真实适配器连接验证，不能据前一轮推断其通过。浏览器实际保存/生产媒体播放仍需分别验收。没有发现需要修改的产品源码，原源码 HEAD 仍为 bad04fd。


## 项目真实适配器连接：10通过，1红灯

指定 `audit/20261017` 的 `af212c140b7ed94f14b0944add3318770d4027de`。读取并校验 Git blob 后，在独立只读源码目录导入实际 `scripts.core.web_content.compile_content` 和 `effective_feedback`；没有复制解析逻辑或用 stub 替代适配器。`adapter-source-provenance.json` 列出本轮实际读取的最小依赖文件，测试入口还固定校验 web_content.py 的 SHA256。

`tests/project-adapter-connect.cjs` 配合薄调用器 `tests/adapter_probe.py`，重新打开上一节真实本地 D1/R2。实际 GET 回读合成 PNG 并核验 hash/大小，形成明确的本地传输回读证据；真实编译器生成资产 ContentPackage，经实际网页 schema 校验后发布到本地 D1，服务端 version 与项目编译 version 相同。保存的合成审批返回给原项目 effective_feedback，按准确 case/stage/version 读为 approved。三个独立 Node/workerd/Python 进程验证重启后的相同审批、历史不继承、错身份拒绝、媒体证据缺失拒绝和来源 stale 不采纳。未自动登记项目确认或推进下一阶段。

**当前不是全绿**：11项检查中10项通过。删除真实合成审批响应两份 currentFeedback 的 id 后，原 af212c 适配器仍返回 approved；此行为不满足“无回执ID不能采纳审批”的验收要求。精确位置为 `scripts/core/web_content.py` 的 effective_feedback：身份循环未检查 id，末尾仅凭 decision=approve 返回 approved。本轮不改主流程源码，红测和失败说明保持原样，待主项目任务修复后以新准确提交复核。

复现需要已授权的项目源码 checkout **af212c**（或按 provenance 逐文件原样恢复的只读最小目录）、前文修复后的网页源码、其锁定依赖，以及上一节 persistent-review.cjs 成功输出的独立本地状态目录。Python 需现有 Pillow、jsonschema、referencing；Node 使用网页现有 Zod/Miniflare。入口不自动下载源码、安装包、创建凭据或连接外部服务。

```sh
node "$PATCH_DIR/tests/project-adapter-connect-af212c.cjs"   --web-source "$RESTORED/site-source"   --adapter-source /absolute/path/to/pinned-af212c-source   --backend-state /absolute/path/printed/by/persistent-review
```

按当前固定 af212c 源码，预期退出码为1，报告一项 missing approval receipt ID 红灯；不能将失败改成通过摘要。`project-adapter-connection-report.json` 与日志记录实际结果。现有本地测试库被本连接测试追加合成版本和审批，原14项历史报告仍是前一检查点的证据；若重新验证14项，请运行 persistent-review.cjs 创建全新库。不要向生产 URL 注入合成身份。

本次只提交测试、去敏结果和来源指纹。项目源文件、合成数据库/R2对象、业务输入副本及 worker bundle 均不上传。网页源码 HEAD 仍为 bad04fd；项目适配器原字节不变。没有部署、生产写入或历史从零盲跑。


## d0ee6ce 回执边界修复复验：15/15通过

固定项目提交 `d0ee6ce15edc17a1c6d83318908858b6e62263f2`，web_content.py SHA256 `de8f4cad64c12cb58c9b8325cc628d286cfafcf37f241389e43bec0bec4c0b1a`。新源文件和15个未变依赖均按 Git blob 核验后只读加载，没有编辑项目源码。来源指纹见 `adapter-source-provenance-d0ee6ce.json`。

保留上一套真实本地 D1/R2→实际项目 compile_content/effective_feedback 连接全部原断言，只更新准确 source pin 与相应消息；逐行确认旧断言仍按原顺序存在。另加缺/空白/错误类型 ID、两份当前反馈冲突、真实合法审批及无审批 pending 不误拒检查。三独立进程共15项通过：4项初连、10项重启/边界、1项最终重启；每阶段外部 fetch 均为0。

旧缺 ID 红测转绿；空串、纯空白、tab/newline、null、数字、布尔、数组和对象 ID 均报 WebContentError。两份反馈的 ID/decision/version 冲突，以及任一份为 null 而另一份为有效对象，均拒绝。真正从持久化后端读回的合法审批，在两份匹配或只有权威 currentState 时保持 approved；新包无回执保持 pending；历史、错 case/stage/version、缺 media id/mediaVerified 和来源 stale 的原检查继续通过。未放宽失败标准。

当前测试入口固定新版本：

```sh
node "$PATCH_DIR/tests/project-adapter-connect.cjs"   --web-source "$RESTORED/site-source"   --adapter-source /absolute/path/to/pinned-d0ee6ce-source   --backend-state /absolute/path/printed/by/persistent-review
```

依赖与隔离条件同前一节。按 d0ee6ce 预期退出码0；报告 `project-adapter-connection-d0ee6ce-report.json` 和日志保留实际15/15结果。旧 `project-adapter-connection-report.json` 与旧日志不覆盖；旧报告中当时的 project-adapter-connect.cjs hash 对应现在原样归档的 `project-adapter-connect-af212c.cjs`，该旧入口仍固定 af212c 并保留预期红灯。

没有改网页产品源码或主流程源码；网页仍为 bad04fd。没有提交本地数据库/R2对象、原项目源文件、媒体、凭据或 worker bundle；没有部署、生产写入、项目自动采用/确认或历史盲跑。结论是本地保存→真实适配器读取→重启后仍一致及上述边界通过，不扩展成远端平台或浏览器验收。
