# 本地视觉资产库

原「拾景」网页已停用。本地库继续由 `scripts/visual_asset_library.py` 管理；运行 `--help` 查看导入和审核命令。需要调用本地接口时用 `serve`，它只监听本机，默认端口 8766。

制作记录和缓存仍在 `.master-storyboarding/visual-library/`，收藏原件和索引仍以素材存储根目录的 `资产库/` 为准。`--library` 可指定已有制作库，`--asset-vault` 或 `MSB_ASSET_VAULT_ROOT` 可指定素材存储根目录。

## 外部已生成候选的接入（不替换原生生成）

原 Codex CLI 不可用时，不能假造审查通过或自动切换生成服务。用户已另行授权外部工具产生的真实文件，可用现有 `/api/import → /api/update → /api/project(select)`；这只验证成品接入，不能报告为 `/api/imagegen` 或 `codex_bridge` 已接通。未获得新增生成授权时，只能重用已有文件检查这条链。

给 `/api/import` 的 `source` 指定**单张实际图片**，可选附上 `external_generation`：

```json
{
  "source": "<本次真实输出文件绝对路径>",
  "external_generation": {
    "tool": "<真实外部工具；不要冒充CLI>",
    "model": "<实际已知模型；未披露就明确未披露>",
    "user_authorization_ref": "<本次已有真实授权引用>",
    "prompt": "<本次实际提交全文>",
    "reference_paths": ["<实际使用的参考图片路径；纯文生图可为空数组>"],
    "image_sha256": "<本次工具结果文件的SHA256>"
  }
}
```

这是来源记载，不是未来生成授权或用户成图确认。服务核对结果hash、读取实际参考并记指纹，复用资产 `provenance`、知识库来源记录和正式导出凭据；不创建 CLI run_id、prompt_approval 或 generation authorization。同一路径已入库且来源记录不同会明确报错并保留原记录，不能静默改历史；需要改来源时另存候选。不能给整目录套同一生成记录。

之后将资产设为相应分类、role=候选，并通过 `/api/project` 的 `action=select` 指定 project_id/job_id/index/asset_id。它验证现行需求、前置依赖和文件后进入 awaiting_review；原审查失败继续保留。没有具体图片的用户确认，不调用 `confirm`，整表导出仍会阻塞。获准后沿原项目逐项确认、整表导出与下游 `asset_refs --require-image`；导出记录保留外部工具身份，不借任务默认provider填成CLI。

返工用原 `action=reopen`：保存旧图/确认/审查来源，撤下受影响选用，连带前置依赖失效，重新核对后才可再消费。公共 `/api/attach` 不允许在生成或审查中手工覆盖，也不能写入旧版需求；原 worker 的已提交任务回填仍保留。仅粘贴路径的手工回填不能证明生成工具，来源记为 manual_result_import，不沿用任务默认模型或生成授权；有实际外部记录的图走上文 import 入口。
