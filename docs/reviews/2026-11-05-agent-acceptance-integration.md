# 本地自主验收与消费接线

日期：2026-11-05。本阶段使用分支：`codex/agent-stage-acceptance`。记录形成时修改尚未提交，前序 Skill 审查、普通 prepare 分流和第三层条件来源的工作继续保留；后续提交与合并以 Git 历史为准。业务规则以 [PROJECT_RULES.md](../../PROJECT_RULES.md) 为准，实际操作见[审核到画布入口](../development/review-to-canvas-entry.md#本地流程授权与自主采用)。

## 完成范围

- 新增 `scripts/core/agent_acceptance.py`：登记已有流程授权，将整稿自查绑定本项目、获准阶段、准确产物／修订／字节、直接上游和当前检查 profile；图片另绑定实际观察与产品参考的内容 hash。报告必须覆盖全部现行检查 ID，核心检查不能以不适用跳过。未决问题和阻塞／失效源稿不能靠自报通过获采用。
- 复用既有产物清单、项目状态与修订事务：首次自主采用、授权返修和提交均保留 `user_approved:false`，不新增逐稿 `user_confirmations`。`artifact_is_confirmed` 继续只表示人工确认，`artifact_is_adopted` 读取两种合法依据；原人工采用入口不承接自主记录。
- 阶段交接、补图请求、图片引用及画布快照消费读取同一有效依据，继续核准确上游、图像观察、镜号用途、参考职责、同态组和上传映射冲突。图片观察范围不能扩大获准用途，证据和授权漂移会使旧依据失效。
- 三个生产 Skill 已指向真实的 `authorize-workflow`／`accept-artifact` 操作说明和自主修订 Python API。编导、资产、声音及白模的既有人审边界保持；候选试跑不能进入生产。

代码只检查声明、来源和证据是否一致，不把合成观察当成真实创作／看图质量。未启动真实业务阶段、付费生成或画布操作。

## 验证

完整本地 Python 回归：**2527 passed，177 subtests passed，3 skipped**，没有失败。跳过项为一项 Windows 项目验证器和两项未提供私人源稿的只读回归；没有为通过测试读取私人案例。另补实际自主导演→视频首次采用→视频返修及生成段对账正反例后，`test_agent_acceptance.py` 的 **56 项**全部通过（与全量结果有重叠，不相加）。

全量证据目录：`/private/var/folders/hq/x035j8j52754y03_pys1s4nh0000gn/T/msb-agent-stage-acceptance-final-20261105-7l1pcx9o/`，含 `pytest.log`、`result.json`。临时目录可能被系统清理。首次全量运行暴露了 macOS `/var`／`/private/var` 临时路径差异，以及采用后修改图片观察的测试夹具；最终使用规范化进程 `TMPDIR`，把观察配置移至首次采用之前，未放松生产校验或重签旧 current。

测试进程复用本机现有 Python、Node 和 FFmpeg；没有安装依赖或修改全局环境。知识包验证仍为 **56 文件**，动态规则仍仅 `MSB-KB-PROMPT-002`；四份新来源仍为第三层条件参考，没有升格生产方法。

## 该阶段当时的审核站状态

用户本轮提供三路正式站接口及本机说明，版本标为 `20261105-agent-review-write-rc1`。已读取更新后的 README、提供的全文及相邻实现，并只读验证 `GET /api/agent/review/status?caseId=&stage=script`：有鉴权返回 400“缺少案例编号”，无鉴权返回 401 `AGENT_REVIEW_WRITE_UNAUTHORIZED`。密钥未输出或写入仓库／证据。

这只验证了认证与空参数处理。未调用 `create-case` 或 `publish`，未读取具体私人案例，没有真实发布、批准或往返回执。当前合同只收 `.md`／`.txt` 文本，POST 须 Bearer 与 JSON Content-Type，不带 Cookie／Origin；`status` 返回 head／expectations／sourceScriptVersion，不含批注或人工批准。资产图片和 JSON 原包上传、任意项目的准确意见／批准读取仍未接通。全文仍带本地 MVP 范围，尚未核该源码与现网 release 的准确对应关系。

后续按现有[API 接线要求](../development/review-to-canvas-entry.md#审核站-api-对接要求)补真实文本发布／版本冲突、资产媒体与准确批准读取。上传成功、head 存在和本地回归通过均不作为人工批准或画布交付依据。
