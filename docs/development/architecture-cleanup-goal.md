# 仓库架构收口与核心解耦执行目标（Goal）

> 状态：已完成（2026-11-27 全量收口，`LEGACY_CORE_SCC: 27 → 0`，全仓 184 个 Python 文件零循环依赖）

## 一、总目标（Objective）

在不影响常规制作开工上下文负担（`AGENTS.md` 常规只读 `PROJECT_RULES.md` + 目标 `SKILL.md`）与现有业务契约/CLI 行为的前提下，分三步完成架构入口闭环、红帧 JS 边界纳管与 `scripts/core/` 大代码减重解耦，彻底消除“改一处牵动整片模块”的循环耦合。

## 二、护栏与非目标（Guardrails）

1. **常规制作零增负**：[架构与分支规范](../ARCHITECTURE.md) 只挂在 [AGENTS.md](../../AGENTS.md) 与 [CLAUDE.md](../../CLAUDE.md) 的仓库维护入口，不进入常规制作必读路径。
2. **测试与兼容不降级**：严禁删改或弱化既有业务回归测试；保留既有模块公开导出与兼容垫片，确保全套 `pytest` 与 `node --test` 100% 通过。
3. **不新增运行时框架**：只做职责下沉、死代码清理与单向依赖收口，不引入动态插件或黑箱调度层。

## 三、按序执行清单（Ordered Checklist）

- [x] **Step 1：接入仓库维护入口并锁死双入口同步**
  - 在 [AGENTS.md](../../AGENTS.md) 与 [CLAUDE.md](../../CLAUDE.md) 仓库维护段落接入 [docs/ARCHITECTURE.md](../ARCHITECTURE.md)，保持两文件字节完全一致。
  - 在 `scripts/tests/test_architecture_boundaries.py` 增加自动化校验：`AGENTS.md` 与 `CLAUDE.md` 同步一致，且 `docs/ARCHITECTURE.md` 仅挂在维护段落。

- [x] **Step 2：纳管红帧浏览器 JS 脚本（`scripts/rhtv_*.js`）边界**
  - 在 [docs/ARCHITECTURE.md](../ARCHITECTURE.md) 明确 `rhtv_rich_editor.js`（纯只读 DOM 序列化）、`rhtv_target.js`（显式目标与结构化 CDP 操作）、`rhtv_canvas.js` / `rhtv_cdp.js` / `rhtv_shot.js`（CLI 入口）的三层职责及与 Python 红帧层的边界。
  - 在 `scripts/tests/test_architecture_boundaries.py` 增加 `rhtv_*.js` 依赖方向、纯度与跨语言调用边界门禁。

- [x] **Step 3：大代码清理与 `scripts/core/` 循环依赖彻底清零**
  - [x] **3A 死代码清理**：清理已退役 `visual_asset_library` 与旧 `prepare` 遗留的无引用死函数（`visual_asset_choices.change`/`save_choices`/`production_spec`/`scene_state_coverage`、`visual_asset_product_usage.source_narrative`/`append_table`、`visual_asset_source.reference_evidence`/`resolve_reference`/`local_reference_document`、`review_publication` 废弃网页资产比对等），落地纯规则模块 `asset_choice_rules`（使 `core.visual_asset_choices` 脱离循环 SCC，27 → 26），并修正共享资产模块与 Branch A `visual_readiness` 的分支归属及门禁。
  - [x] **3B L1 原语下沉**：抽离无环的清单与状态存储原语（`manifest_store`、`revision_store`），消除 `project_state`、`project_kickoff`、`prompt_body`、`prompt_scene_control`、`endpoint_states` 的反向依赖并补齐 L1 无向上依赖门禁（顺带使 `visual_asset_product_usage`、`asset_batch_receipt` 脱离循环 SCC，26 → 19）。
  - [x] **3C L2/L3 职责拆分与 SCC 清零**：拆分超千行上帝模块中的底层纯校验规则与高层编排入口（`quality_rules`、`boundary_rules`、`acceptance_rules`、`script_approval`、`visual_handoff`），将循环强连通分量（`LEGACY_CORE_SCC`）从 **27 降至 0（`frozenset()`）**，把门禁收紧为全仓 Python 零循环依赖。
    - [x] **Batch 3-1（19 → 14）**：下沉 `save_manifest` 至 `revision_store`，抽离无向上依赖的 L2 规则模块 `quality_rules`、`boundary_rules`、`acceptance_rules` 并锁死 L2 规则/契约无向上依赖门禁，使 `candidate_test`、`delivery_files`、`generation_boundaries`、`quality_evidence`、`schema_contracts` 脱离循环 SCC。
    - [x] **Batch 3-2（14 → 0）**：补充下沉 `cache_file_digests`/`verify_image_confirmation` 至 `manifest_store`、`exact_narrative_approval`/`narrative_has_exact_approval`/`current_narrative` 至 `acceptance_rules`，抽离 L2 剧本确认契约 `script_approval` 与分镜视觉交接门禁 `visual_handoff`（及测试夹具 `_canvas_sample_fixture`），清零最后 14 个 `LEGACY_CORE_SCC` 模块并锁死全仓 184 个 Python 文件零循环依赖与 L0–L3 全量显式单向分层门禁。

- [x] **Step 4：全量回归验证与技术决策留档**
  - 全量验证通过：`.venv/bin/python -m pytest -q`（`2,226 passed, 21 skipped, 55 subtests passed`）、`node --test scripts/tests/test_rhtv_*.cjs`（`19/19 passed`）、文档链接与锚点完整性测试 `test_doc_reference_integrity.py`（`4/4 passed`）全部零回归。
  - 已在 [docs/decisions.md](../decisions.md) 记录本次架构收口与核心解耦的背景、边界和实测依据，并同步更新 [docs/ARCHITECTURE.md](../ARCHITECTURE.md)。
