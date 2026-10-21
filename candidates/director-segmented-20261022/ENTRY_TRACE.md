# 执行入口与审核网页只读溯源

## 正式项目执行源码仍未定位

已读取指定私有项目的 README、ACTIVE_VERSION.md、AGENTS/规则，以及 Mac 已知项目备份索引；仅检查已列路径，未扫私有会话日志、未连接公司网络。

- README 的恢复审计工作区：`/workspace/msb-recovery/repo`；知识依赖和网站参考分别位于该云环境的 knowledge、web-contract/site-source。它明确是恢复审计树，不是生产认可。
- ACTIVE_VERSION.md 列出云工作区的 `active-storyboarding/current` → `projects/Master-Storyboarding`、同级 run-current.sh/ACTIVE.json；其 c572d588… 声明仅适用于当时云环境，不代表 Mac 已部署。
- Mac 已知 Obsidian 项目索引列出的执行路径仍是 `C:/Users/LIUYANG/Documents/Master-Storyboarding/`。Mac 对应已知 Documents/active-storyboarding 及这些云路径不存在；不能把 Windows 路径改写成猜测的 Mac 工作树。
- Obsidian 备份属于 Vault 自己的 Git 工作树，无目标项目 remote；其用户未提交内容未触碰。当前本任务只有明确版本的隔离物化源码。

正式接入尚需实际启动器/ACTIVE.json 所指的只读工作树路径、准确 Git commit/dirty 状态和项目 manifest/state 来源。仅有恢复候选提交不足以认定生产入口。

## 已核实的网页身份与部署版本

GitHub `audit/review-web-20261017` 的只读源码清单位于 `reviewed-site-patches/v78-source-manifest.json`，siteId 为 `appgprj_6ac7de4ba2fc81919412067931406a29`。该 ID 与 Sites 已有 owner 清单中的“四案例广告改编审阅”一致，不是按标题猜 ID。

通过既有连接只读查询版本和部署状态，当前取得：

- 网页地址：`https://four-ad-trials-review.yang43595.chatgpt.site`。
- 已保存版本 v80，源码 commit：`d23a7ba5be57f432484ff1670e46cfc66a5d701c`。
- 版本 ID：`appgprj_6ac7de4ba2fc81919412067931406a29~appgver_a7f0fd866c688191924ebfea9b7a0fa9`。
- 对应部署 ID：`appgdep_6ad339889bf8819189fe273081dfec58`，状态 succeeded，返回上述同一网页地址。

这证明所查 v80 已成功部署；不证明本候选已接入该站。未创建凭据、安装插件、修改权限、发布版本或向业务接口写入任何内容。

## JSON 导入接口的已知证据及缺口

项目已有 `scripts/core/web_content.py` 和 `docs/development/web-content-adapter.md`：从同一业务 JSON 内存编译 PublishRequest/ContentPackage，显式传 sourceScriptVersion、expectedVersion，返回 published:false，不执行发布。固定契约是 v78 快照。

网页审计分支 README、provenance 和文档补丁明确记录历史约定：新发布读取 `PublishRequest.content` JSON，不强制 Markdown；`GET /api/content` 默认 pending 不是批准凭据，应读取准确版本的 currentState/currentFeedback。v78 文件清单包含 `app/api/content/route.ts`、`lib/content-store.ts`、`scripts/publish-content.mjs`、content-feedback/heads/history 路由；这些是已知源路径，未把路径推测当成现在线上接口接线。该分支仅补丁，不是完整站点，不能直接部署。

现有记录还指向已保存 v79 a029413f… 及其两文件显示修复；这不是当前 v80 路由源码证明。本次没有只读源文件通道取得 v80 的上述具体文件，也没有新建源仓库凭据或下载 265 MB 含媒体的归档来扩大读取。

实际集成所需的最小后续信息是：

1. 从已授权的 v80 精确源码 checkout 读取 `.openai/hosting.json`、`CONTENT_DELIVERY.md`、`app/api/content/route.ts`、`app/api/content-feedback/route.ts`、`lib/content-store.ts`、`scripts/publish-content.mjs`，核对导入/发布及回执的当前约定；无需原视频或完整媒体归档。
2. 按该版本已确认的读取接口获取拟接入 case/stage 的当前 script head/current pointer、expectedVersion 和用户确认的准确来源 artifact/revision，不能从本地 R02 或旧 v78 推算。
3. 与前述正式项目工作树身份对齐后，才考虑另行授权的真实接入或创作试跑。本次不执行这些操作，不把元数据读取说成业务集成验收。
