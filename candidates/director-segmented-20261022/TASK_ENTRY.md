# 手动分段任务入口（候选，默认关闭）

这是一组可调用的文件操作命令，不是模型调用器。项目现有 `stage_context_cli.py` 的职责也是构建/验证上下文而不启动 agent；本工具复用由当前执行者完成 Skill 的方式，不另造调度系统。尚未在真实导演创作中执行，也没有分段生成、艺术效果或语义连续性通过证据。

本入口没有加入生产 Skill、profile、索引或准备加载器。仅隔离任务显式选择后才使用；当前实现与结构测试授权不授权启动创作。正式确认输入、prepare/ensure-prepared、方法读取等既有门禁仍须由执行者先完成；本工具不查询真实项目 manifest，不认证来源已确认，不生成确认记录、不登记生产采用或交接。

## 准备与调用

选用已经核对的完整 narrative JSON 和同一来源的候选 v4 导演 JSON 作为只读 base。base 保存全局 director_rules、场景共用字段、准确 source_narrative artifact/revision 和来源数组；合成只替换各场 shots、audiovisual_flow、start_state/end_state，不改上游编导稿。base 不能是 confirmed，不采用 human_readable_path。必要的全局规划由执行者依据完整原文完成，工具不代写。

手工创建隔离 plan JSON，必需内容如下（这是任务 sidecar，不是新业务 schema）：

- `source_artifact_id`、`source_revision`：必须与 base 及叙事版本一致。
- `narrative_purpose`、`information_emotion_route`、`style`、`timing_status`：全局计划与估算/实测边界。
- `director_rules`：与 base 中同名对象完全一致，不另造一份业务权威。
- `total_budget_seconds`：正的有限数；`initial_state`：六项非空字符串状态。
- `segments`：按剧情顺序排列。每项含唯一 `SEG-01` 等 `segment_id`、已有 `scene_id`、`source_paragraph_ids`、`source_dialogue_ids`、正的 `budget_seconds` 和六项 `target_state`。段预算总和不得超过全片预算，来源覆盖不得遗漏；不按字数切段。

六项状态 key 为 `position / action / gaze / props / emotion / sound`。这是候选任务边界 sidecar，业务 JSON 继续使用原字段；非空和完全相等只证明字面状态传递，不能证明镜头实际连续、状态真实或目标已达成。

从仓库根目录执行，使用已有项目解释器；`<隔离运行目录>` 必须不存在，不写正式项目状态：

```sh
python candidates/director-segmented-20261022/segment_tasks.py --run <隔离运行目录> init --source <完整叙事JSON> --plan <隔离计划JSON> --base <候选v4导演JSON>
python candidates/director-segmented-20261022/segment_tasks.py --run <隔离运行目录> task --segment SEG-01 --reason <本段目标或修订原因>
```

第一条冻结输入字节与全局计划；不会创建模型任务。第二条落盘不可覆盖的 `tasks/SEG-01-N.json`，输出路径与哈希。文件包含全局约束、源路径/哈希/revision、准确本段原文/对白、上一段实际结果的哈希和结束状态、本段目标及下一段目标。执行者先通读完整冻结来源，再按 WORKFLOW.md 处理这个任务；任何工具通过都不能代替实际阅读。

## 回填与进度

执行者手工形成结果 sidecar：

```json
{
  "production_blocked": true,
  "segment_id": "SEG-01",
  "source_revision": "R01",
  "scene_id": "S01",
  "task_sha256": "复制当前 task 命令返回的准确哈希",
  "predecessor_sha256": null,
  "start_state": {"position":"实际输入","action":"实际输入","gaze":"实际输入","props":"实际输入","emotion":"实际输入","sound":"实际输入"},
  "end_state": {"position":"实际结果","action":"实际结果","gaze":"实际结果","props":"实际结果","emotion":"实际结果","sound":"实际结果"},
  "boundary_review": "具体衔接证据、目标是否达成及未核风险；不能只写通过",
  "audiovisual_flow": "本段已选定的真实视听展开，含必要取舍理由",
  "chosen_option_id": "route-A",
  "options": [{"id":"route-A","hard_conflicts":[],"evidence":"指向具体画面/动作/切点的依据","tradeoffs":"代价与未验证处"}],
  "shots": []
}
```

这是结构示意，空 shots 不能提交；shots 必须由执行者提供完整原 v4 Shot 对象。实际 source revision、前段哈希和开始状态从当前 task 精确取值，不照抄示例。仅关键岔路记录少量真正不同选项；没有实质分歧时一个已选路线即可，不强造三案。工具只检查选择身份、证据字段及被选项硬冲突列表为空，不替作者判分或证明证据真实。

```sh
python candidates/director-segmented-20261022/segment_tasks.py --run <隔离运行目录> record --segment SEG-01 --result <手写结果sidecarJSON>
python candidates/director-segmented-20261022/segment_tasks.py --run <隔离运行目录> status
python candidates/director-segmented-20261022/segment_tasks.py --run <隔离运行目录> task --segment SEG-02 --reason <本段目标>
```

record 仅保存候选结果，current 仅表示与本次任务及前序字节绑定一致，**不等于用户确认或生产 current**。status 报告各段缺失/失效依赖；上一段缺失或失效时禁止创建后段任务。已记录版本都留在 results/；不覆盖旧任务或旧结果。

局部回改时，对实际受影响段重新调用 task 并给原因，再 record 新版本。后续依赖随即失效；只重查实际受影响的邻段及依赖链。未受表达影响的原段可在人工核对后复用原 shots/flow，按新 task 哈希和真实前态另存一个结果版本，不必重新生成全篇。工具不自动重绑或把旧语义检查伪装成新检查。全局计划、来源或 base 变化时创建新隔离 run，保留旧 run，不原地改冻结输入。已确认生产内容的修订与重新确认仍走原合同。

## 合成与边界

```sh
python candidates/director-segmented-20261022/segment_tasks.py --run <隔离运行目录> assemble
```

所有任务/前序绑定当前后，assemble 核对来源覆盖、镜号唯一、对白按顺序逐字拼接及说话者，合成原 v4 JSON 到本 run 的新 exports 路径，不改源文件。它不执行语言意义上的事实覆盖、时间可演性、状态物理一致或艺术判断；正文可能与 sidecar 声称的状态不符，必须人工按 WORKFLOW.md 复核。

有现有 jsonschema/referencing 运行依赖时复用原 require_schema；真实 schema 错误会阻止导出。依赖缺失时，隔离候选可落盘，但 receipt 明确 `schema_check: unavailable`、`production_blocked: true`、语义连续性及艺术效果未验证，不能据此送正式交接或称 schema 已通过。不安装替代依赖、不伪造 fixture。结果/回执路径冲突时拒绝覆盖。

用户确认范围、逐段确认后才继续的既有规则不由本工具验证或放宽；全部工作保持隔离。最终正式审核仍要求独立 A 的 JSON/既有网页合同、完整业务校验和真实用户确认。未核实线上入口与版本时，不发布或声称已集成。
