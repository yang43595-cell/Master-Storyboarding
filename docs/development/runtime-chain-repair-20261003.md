# 运行链路根因修复（2026-10-03）

范围：本轮运行链路审查复现的 14 项问题，以及修复期间发现的同根因入口遗漏。保留并复用工作区已有修复；本记录不代表同日其他任务的全部修改。

## 根因与处理位置

| 运行问题 | 根因修正 | 共用入口 |
| --- | --- | --- |
| 切换项目后迟到响应覆盖页面，旧控件操作其他项目 | 加载结果绑定请求项目；项目页所有任务操作携带当前项目；服务在任何保存或启动任务前核对归属。独立资产接口省略项目字段仍兼容原协议 | `visual_asset_ui/projects.js`、`Library.operation_job`、`Projects.context/action` |
| 四视图切单图、重做母图、切回四图后复活旧角度 | 交付数量只控制 required，不改制作依赖图；确认记录保存具体前置图片和确认编号；返工沿依赖图撤回下游当前确认 | `Projects.dependency_basis/dependent_keys/reset_steps` |
| 已导出的旧混图绕过检查，服务重启后继续交接 | 导出、幂等返回及继续项目共用前置确认版本核验；没有历史版本证据时要求重新核对，不根据现存依赖图补造证据 | `Projects.validate_dependency_confirmation/validate_export_basis` |
| 上传后重启丢失名称、识别和裁切信息 | 在首次正式入库前完成最终元数据；正式备份与缓存保存同一完整记录 | `Library._upload_file/import_file`、`visual_asset_backup.run` |
| 原始导入文件搬走后导出失败 | 区分历史来源与当前文件；导出消费经过核验的正式图片及项目副本，保留原来源用于溯源 | `Projects.copy_to_project/export` |
| 历史依赖污染当前修订、候选继承最早旧稿依赖 | 依赖图只沿已采用分支，同逻辑编号优先 current；未替代的 stale 只用于传递失效；候选从变更案冻结的基线 ID 继承依赖 | `core.artifacts.dependency_closure`、`revision_control.record_candidate` |
| 失效产物仍留在当前选择，返工入口被旧编号挡住 | 当前选择与清单有效 ID 取交集；正式采纳只替换已选中的对应基线；不自动选择其他产物或增加确认 | `core.project_state.reconcile_current_artifacts/sync_current_artifacts` |
| 修订中断后状态 JSON 与 Markdown 不一致 | 修订事务将状态事实与阅读视图一起保存和恢复；声音事务复用同一状态规范化、核对与渲染规则 | `revision_control`、`voice_reference_cli` |
| 新修订准备阶段仍嵌入旧交接卡 | 消费时核对目标 Skill、当前产物 ID、修订、路径及确认；过期卡不能作为本轮输入，重建交接后可继续 prepare/阅读登记/检查 | `stage_context_cli._current_handoff_text` |
| 同声音编号换音频不失效且覆盖旧快照 | 声音身份包含音频内容哈希与适用范围；变化沿统一依赖图使提示词、视频失效；新快照使用新路径并保留旧音频 | `voice_reference_cli._snapshot_core/_prepare_voice_anchor` |
| 声音范围未执行，显式场景范围暗中附加 project | 写入、同步、验证共用范围判定；project / scene:<ID> / segment:<ID>，排除优先；仅省略参数时默认 project，schema 与 CLI 同步 | `voice_reference_cli.voice_scope_errors`、`voice-reference-manifest.schema.json` |
| Blender 基座切镜标记抢回旧相机，重跑覆盖文件，缺视频也验收通过 | 本轮解除基座相机绑定；构建和运镜共用输出目录、指定文件计划、输入指纹、文件内容及视频帧数/时长核验；渲染前拒绝命名冲突，视频不重复结束时刻 | `blender_previs/output_contract.py`、`runner.py`、`shoot.py`、`verify.py` |
| 索引写入中断损坏下次启动依据 | 索引 JSON/CSV 复用既有原子写；中断保留完整旧索引，待备份记录可重试 | `visual_asset_management.write_index`、`core.io` |

## 知识接线登记

13 个块外正文指纹差异均逐项对照了原登记指纹对应的知识库 Git 版本：仅来源路径从 `sources/` 修正为 `wiki/sources/`，目标文件均存在，方法正文未变。因此只更新项目 `knowledge-entrypoints.json` 的登记指纹和复核说明，保留原审计规则；未写入知识库正文。

## 验证与历史数据边界

回归使用临时项目、临时资产库与本机临时 HTTP 服务，覆盖完整故障序列、重启恢复、事务中断、跨项目拒绝和原流程兼容。Blender 使用本机真实渲染与编码核验；正式生产项目及付费生成没有参与测试。

最终验证：

- 默认 Python 测试入口：`py -3.13 -B -m pytest -c config/pytest.ini -q -p no:cacheprovider --tb=short`，**603 passed、61 subtests passed**。
- 全部网页回归：`node --test` 执行 `scripts/tests/*.cjs`，**50 passed**。
- 知识接线审计：**0 errors、0 warnings**（9 个 Skill profile、75 个动态规则块、14 个本地规则块）。
- 本机 Blender 5.2.1：运镜 6 张 96×96 PNG 与 40 帧/4 秒 MP4，17 项验收通过；普通建模选定镜 10 帧/1 秒、俯视全段 20 帧/2 秒，15 项验收通过。改配方、同数量替换必需图片、缺视频均拒绝；重跑拒绝且原 blend 字节不变。
- `git diff --check` 通过。

Blender 完整输出证据位于本机临时目录 `C:/Users/LIUYANG/AppData/Local/Temp/ms-blender-contract-kddt_pky/check/result.json`，日志在其父目录 `blender.log`。实际场景是验证用简化场景，没有验证生产人体资产或深度模型下载。

旧场景角度若缺少前置图片版本依据，需要重新选用、查看并确认；旧图片与确认历史保留。旧预演清单缺少本轮配方和输出证据时，不能据此确认当前配方的验收结果。代码修复不自动推断此前被覆盖、缺失或混用的数据。
