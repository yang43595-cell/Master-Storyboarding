# v78 网页审核修复：补丁交付

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
