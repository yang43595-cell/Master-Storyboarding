---
title: "资料摘要：Seedance 2.5 官方发布"
aliases: [Seedance 2.5 官方发布, Seedance2.5 30秒长叙事]
type: source
created: 2026-08-23
updated: 2026-08-23
tags: [seedance, seedance2.5, ai视频, 30秒, 多模态参考, 白模控制, 导演预演]
source_url: https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5
source_type: official-article
source_id: bytedance-seed-seedance-2.5-launch-2026-07-31
read_date: 2026-08-23
---

# Seedance 2.5 官方发布

## 基本信息

- 发布方：ByteDance Seed Team
- 发布日期：2026-07-31
- 模型：Seedance 2.5
- 官方页面：<https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5>

## 可核验能力

1. 单次可生成最长 30 秒的音视频内容，并支持多轮延长。
2. 强化长叙事和单次多镜头组织能力，可在较长片段中处理铺垫、发展、转折与收束。
3. 单次最多可输入 30 张图片、10 个视频和 10 个音频作为参考。
4. 模型能理解构图、场景、风格、人物、道具，以及参考视频中的动作和镜头语言。
5. 支持按时间戳控制叙事、镜头视角、动作和节奏，并强调白模控制、绿幕和参考编辑等专业能力。

## 对生产流程的直接影响

- 静态分镜图不再适合作为所有视频任务的普遍硬前置；它应退回为关键状态和高风险视觉的可选锚点。
- 人物、场景、风格、产品与道具资产仍是身份和世界一致性的基础。
- 人物站位、人物/道具路径、表演节拍、摄影机路径、景别变化和切点应优先进入导演预演或控制素材。
- 视频提示词需要从“让图片动起来”升级为逐时段调度与多模态素材职责说明。
- 是否拆段应依据完整事件、控制负载和所选模型能力，不应继续沿用 Seedance 2.0 的 15 秒上限或按图片数量拆分。

## 边界

官方资料说明模型能力，不保证所有复杂调度一次生成必然成功。真实产品、关键结构、互斥状态和模型易误读的中间态仍可使用静态视觉锚点；具体 CLI 输入限制以实时帮助为准。

## 关联

- [[wiki/concepts/03-AI生成与视频/ai视频生成方法论]]
- [[concepts/03-AI生成与视频/ai分镜设计]]
- [[concepts/02-镜头与剪辑/专业影视分镜设计与执行方法]]
