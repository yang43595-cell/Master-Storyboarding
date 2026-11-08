# 主动恢复核对接线与真实压缩复测

日期：2026-11-05（Asia/Shanghai）。用户明确要求将此前漏答细项纳入主动恢复，不再等待定向追问。本轮仅修改恢复指令及输入投影，没有执行真实生产或修改业务确认。

## 实现

权威正文仅维护于 [知识读取合同／压缩后的主动恢复核对](../../skills/_shared/knowledge-access.md#压缩后的主动恢复核对)。发生压缩、恢复或工作区重置后，在首次继续判断、产出或外部操作前，主动核五项并汇总结论：

- REC-STATE：目标、阶段、准确修订、授权及确认范围、未决条件与停止点。
- REC-OPTION：可选分支默认状态、分镜按需且默认 9:16，提示词之前仍先检查素材。
- REC-PARAM：准确平台与既有参数，小云雀 480p 不套红帧，真实缺项保留。
- REC-ADOPT：首次自主采用、已有正式版本返修、人工整稿采用，及 status/head 不等批准。
- REC-FLOW：本职阶段、全片统筹及复核、局部范围、素材/场景状态依赖及草稿停止点。

REC-RESULT 要求首次恢复答复或现有任务记录简列各项“已核／不适用／未核（阻塞范围）”、具体结论及原文定位，再说明下一步。不得只说“已恢复”或只列路径；不适用必须有本轮范围依据，缺料、缺参数、未检查和冲突不能当不适用。同次恢复完成后不逐消息重复，不新建平行清单、schema、审批层或业务记录；只读任务不为留证改业务文件。

接线如下：

1. 总规则、共用三层入口及视频 Skill 明确定位该段。视频仍直接读取，不恢复 prepare 或阅读凭据。
2. 普通 prepare 从权威文件抽取该完整二级标题正文，逐字嵌入 stage_startup，缺该段即失败；来源文件仍沿现有 local_documents 哈希冻结与漂移检查，不手抄第二份政策。
3. 资产 Web 通过现有 rules 中的 knowledge-access 正文接入；新段独立于单项裁掉的局部维护／读取开销章节，单项与批量均保留。Web 受限读取合同补充恢复范围，不叠加普通 prepare。
4. 共享恢复段按任务定位原文，不直接链接视觉细则而令所有阶段加载；默认编导仍不加载 visual-production，兼容及试验仍需明确选择。

## 回归

执行 stage_context_cli、visual_asset_reading、visual_asset_library、visual_asset_projects 四个现有测试文件：**238 passed、2 skipped、74 subtests passed**。其中新增检查涵盖：startup 与权威段逐字一致且只出现一次、来源哈希冻结、缺恢复正文拒绝准备、Web 投影保留、视频直读入口不恢复 prepare。既有职责范围及试验选择测试继续通过。

## 无定向追问的真实压缩实验

使用本机默认模型 gpt-6.1-sol 的真实 Codex app-server；隔离会话 ephemeral=true、read-only、approvalPolicy=never。前一条用户输入要求先按项目入口读规则、回复 READY，待下一条消息再交付只读流程判断；题设为网站准确通过后的全链路判断、可能返修一个正式版本、拟用红帧但参数/画布未指定。没有列恢复检查 ID，也没有给三组预期答案。

- thread：`01a1aa12-16ef-7373-a6f9-35342138cee9`。
- 压缩 turn：`01a1aa12-93ec-7731-8861-6f51e629844e`。
- 实际调用 thread/compact/start，同线程 contextCompaction 的 item/started 和 item/completed 均观察到，压缩 turn 为 completed。
- 原始 after userMessage 的唯一文字为 **“继续。”**，没有再要求重读、没有补问可选分支、平台或采用问题。
- 恢复后有 5 个完成的只读命令；首份 final 主动输出 REC-STATE、REC-OPTION、REC-PARAM、REC-ADOPT、REC-FLOW 的具体结果，而非事后补答。

该首答明确包含白模关闭、声音明确选用、分镜按需及 9:16；小云雀 480p 不套红帧；accept-artifact、四步自主修订 API、人工 adopt-package 的区别，以及 status/head 非批准。AUTO-REC-42D1、TEST-R08 测试身份仍保留；红帧参数、画布、待返修阶段及真实业务依据明确未核，不冒充不适用或已确认。

本轮有界行为检查通过。历史实验首轮 **9 pass／3 unknown** 原判仍保留，没有被新机制追认。本次只证明当前模型、当前规则与该案例在一次真实压缩后能主动按清单恢复；不保证任意连续压缩、其他模型或所有真实业务零遗漏。程序也不能证明执行者永远理解或执行到位。

## 证据

源码仓库外：`/Users/liuyang/Desktop/Master-Storyboarding-test-runs/active-recovery-20261105-xkLR1F/`。

保存本次原始前后事件、压缩生命周期、真实恢复输入、首份恢复答复、机械与行为核验、组包路由、回归日志及实验脚本。没有图片/视频生成、外部平台写入或业务采用。原始隔离会话已结束。
