# 仓库架构与流水线分支规范

本文件规定 `scripts/` 的**分层依赖方向**、**业务流水线分支边界**与**防耦合红线**，由 `scripts/tests/test_architecture_boundaries.py` 自动门禁校验。

## 一、五层依赖架构（单向依赖，禁止反向引用）

依赖方向严格为 **L4 → L3 → L2 → L1 → L0**；`scripts/core/*` 严禁反向导入顶层 `scripts/*.py` CLI 入口。

| 层级 | 职责 | 代表模块（`scripts/core/` 或顶层） |
| --- | --- | --- |
| **L0 基础设施** | 错误、IO/锁、媒体类型、字符/图像/工作区底层原语、确认范围、远端 API 客户端 | `errors`, `media_types`, `io`, `character_assets`, `character_layout`, `image_technical`, `director_reading`, `depth_reference_review`, `knowledge_runtime`, `native_port_binding`, `native_references`, `runtime_deps`, `scene_realism`, `confirmation_scope`, `agent_review_api`, `redframe_workspace` |
| **L1 项目与契约原语** | 清单与变更存储原语、项目状态、开工决定、产品参考包、提示词体、端点控制 | `manifest_store`, `project_state`, `revision_store`, `product_reference_pack`, `project_kickoff`, `endpoint_control`, `prompt_body`, `prompt_scene_control`, `endpoint_states` |
| **L2 领域模块（按阶段/分支隔离）** | 产物清单、资产与视觉选择、故事来源、导演输入、质量证据、复刻分段、草稿交付账本、红帧 UI 与分支交付 | 共享：`artifacts`, `candidate_test`, `schema_contracts`, `delivery_files`, `quality_evidence`, `generation_boundaries`, `director_inputs`, `story_sources`<br>共享资产（并行分支）：`asset_choice_rules`, `asset_batch_receipt`, `asset_refs`, `character_boards`, `product_refs`, `visual_asset_choices`, `visual_asset_product_usage`, `visual_asset_source`<br>分镜（Branch A）：`visual_readiness`<br>复刻（Branch B）：`recreation_assets`, `recreation_segments`<br>红帧：`draft_delivery`, `redframe_image_ui`, `redframe_draft_ui`, `canvas_readback`, `redframe_creative`（Branch A）, `redframe_recreation`（Branch B） |
| **L3 治理、审核与交接** | 自主采用、审核发布、事务修订控制、跨阶段交接、阶段上下文与引导 | `agent_acceptance`, `review_publication`, `revision_control`, `production_handoff`, `stage_context`, `stage_guidance`, `stage_handoff` |
| **L4 阶段 CLI 与浏览器脚本** | 唯一命令行入口（含 `main()`）；只调用 `core/*`，不充当共享库 | `init_desktop_project.py`, `stage_context_cli.py`, `narrative_package.py`, `asset_stage_cli.py`, `director_package.py`, `visual_stage_cli.py`, `video_prompt_package.py`, `redframe_draft.py`, `canvas_readback.py`, `review_text_cli.py`, `voice_reference_cli.py`, `rhtv_*.js` 等 |

## 二、业务流水线分支（明确归属，禁止跨分支互引）

```text
Step 0: init_desktop_project.py + stage_context_cli.py kickoff
   │
   ▼
Step 1 [共享上游]: creative-director (narrative_package.py) ──► 02_编导/
   │
   ├──► [并行分支] asset-image-assistant (asset_stage_cli.py, chat_asset_review.py, redframe_image_revision.py)
   ├──► [可选分支] voice-reference-manager (voice_reference_cli.py)
   │
   ├──► [Branch A: creative — 原创 / 对标导演重拍]
   │       storyboard-planner (director_package.py)
   │       ──► storyboard-director (visual_stage_cli.py)
   │       ──► voiceover-to-video-script (video_prompt_package.py + redframe_draft.py / core.redframe_creative)
   │
   └──► [Branch B: depth-guided-recreation — 1:1 深度复刻]
           depth-guided-recreation (prepare_depth_clips.py, core.recreation_segments, core.recreation_assets)
           ├── 就绪段 ──► voiceover-to-video-script (video_prompt_package.py + redframe_draft.py / core.redframe_recreation)
           └── 改戏段 ──► return_director_segment.py ──► storyboard-planner (director_package.py) ──► 视频交付
```

- **共享上游资产边界**：`[并行分支] asset-image-assistant` 及 `core.asset_*`、`core.character_boards`、`core.product_refs`、`core.visual_asset_*` 服务于 Branch A 与 Branch B 两条下游路线，严禁反向导入任一下游分支专属模块（如 Branch A 的 `core.visual_readiness` 或 Branch B 的 `core.recreation_*`）。
- **共享画布原语下沉**：Branch A（`core.redframe_creative`）与 Branch B（`core.redframe_recreation`）共同依赖的红帧画布控件与节点操作统一放在 `core.redframe_draft_ui`；**两分支严禁互相导入**，也不得导入顶层 `redframe_draft.py`。（`core.director_inputs` 为共享上游导演输入契约，供 Branch A 与 Branch B 改戏回流桥接共同使用。）
- **视频阶段入口边界**：`voiceover-to-video-script` 直接读取已确认来源，不走 `stage_context_cli.py prepare`；其余生产阶段统一走 `stage_context_cli.py`。
- **历史兼容垫片**：`scripts/visual_asset_choices.py`、`scripts/visual_asset_product_usage.py` 与 `scripts/visual_asset_source.py` 仅为兼容旧导入路径的薄转发垫片（禁止包含函数/类定义），真实实现位于 `scripts/core/`，新代码一律直接从 `core.*` 导入。

## 三、红帧浏览器 JS 脚本分层与跨语言边界（`scripts/rhtv_*.js`）

`scripts/rhtv_*.js` 是本机 Edge/CDP 红帧浏览器辅助与只读 DOM 探针层，按 **JS-L2 → JS-L1 → JS-L0** 单向依赖，由 `scripts/tests/test_rhtv_*.cjs` 与 `test_architecture_boundaries.py` 双重校验：

| 层级 | 文件 | 职责与硬边界 |
| --- | --- | --- |
| **JS-L0 纯 DOM 只读回调** | `rhtv_rich_editor.js` | 导出自包含 `readRichEditor(root)`，零 `require`、零 Node I/O、零网络与写操作；既可由 `rhtv_target.js` 内联注入，也可供受控浏览器 `locator.evaluate` 调用；绝不伪造 `media_id`、`sha256` 或 `after_refresh` |
| **JS-L1 显式目标与结构化 CDP** | `rhtv_target.js` | 仅引用 `./rhtv_rich_editor`；强制要求显式 `RHTV_TARGET_ID` 或 `RHTV_CANVAS_URL`（严禁按标签页顺序猜目标），在操作前后双端校验页面未导航，并只接受封闭结构化指令（`read` / `read-rich-editor` / `click` / `set-value`），拒绝任意 JS 字符串 |
| **JS-L2 独立 CLI 入口** | `rhtv_canvas.js`（配 `rhtv_canvas.cmd`）、`rhtv_cdp.js`、`rhtv_shot.js` | 仅单向调用 `./rhtv_target` 且各自具备 `main()`：分别负责持久资料夹 Edge 启动、结构化 CDP 单步执行与导航受保护截图；禁止被库模块反向 `require` |

- **跨语言隔离红线**：Python 生产与交付核心（`scripts/core/*.py`）统一通过 `core.redframe_workspace` / `core.redframe_*_ui`（Playwright）或消费已采集 JSON（`core.canvas_readback`）工作，**严禁在 `scripts/core/*.py` 内子进程暗调 `rhtv_canvas.js` / `rhtv_cdp.js` / `rhtv_shot.js`**。

## 四、防架构腐化硬红线（自动化测试门禁）

`scripts/tests/test_architecture_boundaries.py` 在每次 `pytest` 时强制校验以下规则：

1. **禁止 `core -> CLI` 反向导入**：`scripts/core/*.py` 不得导入任何顶层 `scripts/*.py`。
2. **禁止 L0 基础设施与 L1 契约原语向上依赖**：L0 模块（含 `core.io` 内的 `recover_transactions` 等）不得导入 L1–L4 模块；L1 模块（`manifest_store`、`project_state`、`revision_store`、`product_reference_pack`、`project_kickoff`、`endpoint_control`、`prompt_body`、`prompt_scene_control`、`endpoint_states`）不得导入 L2–L4 模块，且 L1 内部保持无环 DAG。
3. **禁止 Branch A / Branch B 交叉导入及共享上游/画布原语反向依赖**：Branch A（`director_package`、`visual_stage_cli`、`core.visual_readiness`、`core.redframe_creative`）与 Branch B（`prepare_depth_clips`、`return_director_segment`、`core.recreation_*`、`core.redframe_recreation`、`core.depth_reference_review`）之间零互引；共享资产模块（`asset_stage_cli`、`chat_asset_review`、`redframe_image_revision`、`core.asset_*`、`core.character_boards`、`core.product_refs`、`core.visual_asset_*`）与共享红帧画布原语（`core.redframe_draft_ui`、`core.redframe_image_ui`、`core.redframe_workspace`）不得反向导入 Branch A 或 Branch B。
4. **禁止红帧层出现循环依赖**：`core.redframe_creative`、`core.redframe_recreation`、`redframe_draft`、`core.redframe_draft_ui`、`core.redframe_image_ui`、`core.redframe_workspace` 保持无环 DAG。
5. **禁止在 `scripts/` 顶层新增无 `main()` 的库文件**：共享领域逻辑必须放在 `scripts/core/`，顶层 `scripts/*.py` 只放具备独立 CLI 入口的脚本（薄转发兼容垫片除外）。
6. **禁止既有循环依赖集合扩大（并在解耦后锁死零环）**：`scripts/` 模块依赖图不得新增任何循环强连通分量。
7. **锁死双入口同步与维护挂载**：`AGENTS.md` 与 `CLAUDE.md` 字节严格一致，且 `docs/ARCHITECTURE.md` 仅挂在仓库维护段落，不进入常规制作开工必读行。
8. **锁死 `rhtv_*.js` 三层纯度与跨语言隔离**：`rhtv_rich_editor.js` 保持零外部依赖与零网络/文件副作用，`rhtv_target.js` 不反向引用 CLI，且 `scripts/core/*.py` 不暗调 `rhtv_*.js` CLI。
