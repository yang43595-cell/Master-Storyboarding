# 项目送审接口合同

供 Master-Storyboarding 接入或更换审核站时使用。默认工作流只向站点发送编导阅读稿和资产图。项目保有业务 JSON、原图、正式采用和下游流转记录。审核站保有发布版本、已提交意见、附件和审核决定。[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)定义审核与授权范围，[审核工作台操作](review-workbench-operations.md)提供现用命令。

前四节是**新接入目标合同 v1**。HTTP 路径与字段给出可直接实现的参考。不同供应商可以通过适配器转换传输格式，须通过相同的行为验收。最后一节列出现有工作台的实际映射与验证状态。

## 身份与数据归属

| 对象 | 项目侧保留 | 审核站须识别或返回 |
| --- | --- | --- |
| 项目与案例 | 本地业务项目 ID、站点来源、远端项目及案例映射 | `projectId`、`caseId`。案例 ID 在所属项目内解释。不同站点的同名 ID 各自独立 |
| 编导稿 | 本地产物 ID、修订号、源 JSON SHA-256、阅读稿 SHA-256。正文段落 `STP-*` 与对白 `DLG-*` 保留稳定身份 | 不可变的 `version`、阶段代数 `headGeneration`、实际正文回读、版本页地址。标注须能定位到同版正文块或对白 |
| 资产包 | 上游编导版本、成员的 `artifactId`、`astId`、`outputId`、用途和原图 SHA-256 | 包的 `version`、`sourceScriptVersion` 及准确成员列表。每个成员返回可持久引用的远端媒体 ID 和原图哈希 |
| 审核记录 | 原始读取回执、实际意见的返修对照、正式采用结果 | 有稳定事件 ID 的已提交决定、审核者身份、提交时间、版本、批准范围和当前有效性 |

以站点来源、项目、案例、阶段和版本共同定位每次发布和审核。远端版本为站点生成的不透明标识，本地修订号和 SHA-256 分别保留。回执保存在对应业务项目的审核记录内，供返修、核对和恢复。URL 用于打开页面，项目按回执身份定位。

## 对外操作与请求返回

以下以 JSON over HTTPS 表达。项目发布和读取使用专用服务凭据。审核员使用站点的人审身份提交决定。凭据和短期签名 URL 留在运行环境，回执保留可追溯的资源 ID 和实际响应。

| 操作与参考路径 | 输入 | 成功返回 |
| --- | --- | --- |
| `POST /v1/review/cases` 建立案例 | `operationId`、`name`，以及站点分配案例所需的项目范围。已有授权案例可直接绑定 | `projectId`、`caseId`、`name`、`createdAt` |
| `GET /v1/review/cases/{caseId}/stages/{stage}` 发布前读取 | 项目身份，`stage` 为 `script` 或 `assets` | 案例身份、`headVersion`、`headGeneration`、`reviewRevision`、`sourceScriptVersion`。首次发布的空 head 用 `null` 和代数 `0` 表示 |
| `POST /v1/review/cases/{caseId}/uploads` 暂存新文件 | 项目身份、文件名、MIME、原件字节和 SHA-256 | `uploadId`、核验后的哈希和字节数。暂存文件尚无发布版本 |
| `POST /v1/review/cases/{caseId}/stages/{stage}/releases` 发布候选 | 项目和案例身份、`operationId`、预期 head 与审核修订、候选正文或资产包 | `projectId/caseId/stage/version/headGeneration/operationId/url`、送审内容绑定。资产包另返回准确编导版本与成员 |
| `GET /v1/review/cases/{caseId}/stages/{stage}/releases/{version}` 读实际发布物 | 项目身份、准确版本 | 该版实际正文或可核验的正文原件、完整媒体成员及其哈希和稳定身份 |
| `GET /v1/review/cases/{caseId}/stages/{stage}/releases/{version}/decision` 读决定 | 项目身份、准确版本 | `pending\|changes\|approved`、最新已提交事件标识、批准范围及有效性 |
| `GET /v1/review/cases/{caseId}/stages/{stage}/releases/{version}/feedback` 读意见 | 项目身份、准确版本、可选的 `cursor` | `items`、`hasMore`、`nextCursor`。一页为空与完整读取分别表达 |
| `GET /v1/review/attachments/{attachmentId}` 读附件 | 项目和版本上下文、已提交意见引用的附件 ID | 受权限控制的实际文件或短期受控下载地址，及 MIME、字节数、原件哈希 |
| `GET /v1/review/operations/{operationId}` 核未知提交结果 | 项目身份、原操作 ID | `pending\|succeeded\|failed` 及已落地的原始回执。服务明确未见该操作时返回 `404` |

### 发布载荷

共同字段包括 `projectId`、`operationId`、`artifactId`、`revision` 和 `expected`。后者包含本次发布依据的 `headVersion`、`headGeneration` 和 `reviewRevision`。资产发布再带 `sourceScriptVersion`。

- **编导 `script`** 发送 `name`、`mediaType`、`text` 或内容上传引用、`textSha256`、`sourceSha256`，以及可从阅读稿解析或明确提供的稳定锚点。阅读稿由该版业务 JSON 导出。站点读回同版正文与来源绑定，附录须完整保留该版 JSON。
- **资产 `assets`** 发送 `members[]`。每项包含 `artifactId/astId/outputId/purpose/mime/sha256`，并提供 `uploadId` 或同案例、同编导版本的既有 `mediaId`。一个包内每组 `astId + outputId` 唯一。项目可以分批送审，也可在返修包中引用未变化的图片。上传方式可由站点选用 multipart、Base64 或预签名方式，落版时统一返回准确成员。文件类型和大小上限由适配器在上传前声明和检查。

JSON 中的哈希采用 64 位小写十六进制。编导 `textSha256` 按实际传输的 UTF-8 正文原字节计算，`sourceSha256` 按本地原 JSON 文件字节计算。资产哈希按原图字节计算。远端媒体 ID、正文渲染结果和缩略图各自保留身份。

### 决定与意见载荷

- **决定** 返回 `projectId/caseId/stage/version`、`status`、`latestEventId` 与 `validity`。有效性使用布尔字段 `isCurrent`、`sourceStale`、`dependencyStale`、`dependencyUnapproved`、`reviewable`、`stillEffective`。没有已提交事件时 `latestEventId` 为 `null`。通过时提供 `approval`，含 `eventId`、`actorId`、`decidedAt` 和批准范围，并与最新已提交的通过事件对应。待审和退回的 `approval` 为 `null`。
- **批准范围** 编导整稿通过绑定该版稿件。资产通过绑定该版包及所覆盖成员的 `astId/outputId/mediaId/sha256`，站点可以显式返回成员，也可通过该版不可变的包成员清单核对。局部正文意见和单图批准只作用于其范围。
- **意见** 每条带 `feedbackId`、`eventId`、`actorId`、`submittedAt`、`kind`、`comment`、`target` 和 `attachments[]`。`kind` 覆盖总体意见、正文标注、图片批注。正文定位含稳定块或对白 ID，必要时加选区文本与范围。图片定位含包成员身份，圈画可用归一化坐标。附件项带 `attachmentId`、用途、说明与文件元数据。分页须做到同一版本和读取范围内无遗漏，读取器沿游标读到 `hasMore=false` 后再记录完成。

## 发布保护和项目流转

1. **提交时原子比较。** 站点在实际落版事务里检查预期 head、审核修订和资产的上游编导版本。两个请求以同一旧状态提交时，最多一个发布成功，另一请求返回 `409` 与当前状态。上传暂存、客户端先读一次状态或按到达顺序排队都可以存在，最终落版仍须满足这项比较。
2. **按操作身份恢复。** 同一 `operationId` 与同一请求内容重复提交返回原结果，代数只增加一次。相同 ID 配不同请求返回 `409`。服务应先识别已完成的精确重试，再判断新操作的版本冲突。超时先查原操作，核清后才继续。
3. **发布与审核分别读取。** 发布回执证明送出的版本，状态读取证明当前 head，决定读取证明审核结论。退回时先按该版读取总体意见、逐处批注和附件，记录缺项。项目在原业务产物上另存修订并重新送审，保留旧版意见和事件。
4. **采用按通过范围。** 项目核对实际人审事件、发布回执、准确字节及上游后正式采用。未提交草稿、空意见、历史通过、来源失效都不能使新稿进入正式链。资产分批可按已确认且已备份的成员交接，其余成员继续等待。新的编导版本进入审核或既有批准失效时，暂停受影响的采用或交付，执行口径见[项目总规则](../../PROJECT_RULES.md#网站审核到画布的统一入口)。
5. **明确错误类型。** 建议用 `400/422` 表达数据问题，`401/403` 表达认证和权限问题，`404` 表达资源不存在，`409` 表达版本或操作身份冲突，`413` 表达大小超限。读失败、网络中断或回包身份不符时，项目保存当前证据，保持结果待核。

## 换站联调验收

在新建的独立案例验收，每项留准确请求、脱敏回执和读回证据。

| 用例 | 预期结果 |
| --- | --- |
| 首发编导 R01 | 发布、状态、网页正文和技术附录一致。未审时决定为 `pending`，项目保持候选 |
| 提交退回 | 总体意见、正文逐处标注和附件分别能从准确 R01 读回。以小页分页读全，附件可从授权通道打开并核原件。第二批资产另验图上批注和附图 |
| 改稿后通过 | R02 分配新版本，R01 的退回事件留存。R02 通过事件可读，项目仅采用送审时绑定的版本和字节，并解锁对应下游 |
| 资产分批 | 第一包通过后只交接已确认的首批输出。第二包退回一张并重新送审后，能引用未变图、沿用其有效依据，再按实际通过和备份收尾 |
| 重试与并发 | 同一请求重试只提一次。两个不同请求同时基于同一 head 提交，其中一条发生版本冲突，胜出版本与最终 head 一致。超时可按原操作 ID 找回结果 |
| 错配与失效 | 错项目、错版本、已换上游、已撤回或越权的意见、附件和批准均无法被正式采用 |

## 现有工作台适配

现用基址为 `https://yangyangyang.click`。Agent 客户端使用 `Authorization: Bearer <token>`，JSON POST 带 `Content-Type: application/json`。凭据由执行环境提供。状态读取只提供阶段 head、`expectations` 等发布依据，审核结论按准确版本读取 `decision`。

现用客户端走 `/api/agent/review/` 下的 `create-case`、`status`、`publish`、`publish-assets`、`decision`。`status.expectations` 提供预读字段。文本发布请求使用 `caseId/stage/name/text/operationId/purpose`。图片发布使用单图加同包 `references` 的方式，客户端同步记录 `AST-ID@OUTPUT-ID`、文件哈希和编导来源。当前文本限 100 KiB，图片原件限 8 MiB，格式为 PNG、JPEG、WebP。[现用客户端](../../scripts/core/agent_review_api.py) · [发布回执与采用](../../scripts/core/review_publication.py)

- **线上已核** 独立案例 25 的编导 R01 发布和状态回读一致。按准确版本请求 `decision`，实际读到 `changes`、已提交事件的 `comment`「把蓝色纸盒改成绿色」以及审核者、时间、修订与版本。
- **线上仍待核** 上次 R01 的 `annotations` 为空，未提交附件。任意新案例的逐处批注、图上批注、附件原件和完整分页，以及 R02 通过后的续跑与资产分批通过，仍按独立案例验收。现有旧案例 connector 仅覆盖其注册范围。[网站意见读取入口](review-workbench-operations.md#网站往返)
- **并发实测差异** 案例 25 的导演阶段使用同一现有文本发布端点做并发测试。两个从同一空 head 出发的请求都成功，依次生成第 1、2 代。现有请求白名单没有提交旧 head 的字段，CLI 的发布前状态复核仍留有时间窗口。新适配器按上面的原子比较用例验收。
- **切站改动点** 现用编导采用入口仍校验正式站固定域名。换站需同步改造该校验，让可信站点配置与业务项目绑定，并复用已有本地采用门禁。旧站回执继续留原站来源，按项目逐个切换，未决审核按准确原版本处理。[现用正式采用门禁](../../scripts/core/review_publication.py)
