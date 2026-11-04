---
title: "AI 人物活人感"
type: concept
aliases: [AI人物去AI味, 去AI味, 活人感, 人物活人感]
created: 2026-07-09
updated: 2026-07-09
tags: [ai视频, ai人物, 质感, 微表情]
sources: [wiki/sources/去除人物ai味-活人感]
---

# AI 人物活人感

> AI 人物活人感是通过人物基础设定、肤质约束、微表情拆解和动作动因，让生成角色摆脱油亮、塑料、僵硬和夸张动作的一组控制方法。

<!-- msb-knowledge
{
  "id": "MSB-KB-HUMAN-001",
  "canonical_key": "character-realism.foundation-skin",
  "kind": "rule",
  "status": "enabled",
  "authority": "wiki",
  "projects": [
    "Master-Storyboarding"
  ],
  "stages": [
    "creative-director",
    "asset-image-assistant",
    "storyboard-director",
    "voiceover-to-video-script"
  ],
  "topics": [
    "character-realism"
  ],
  "triggers": [
    "活人感",
    "人物设定",
    "肤质",
    "塑料感",
    "油亮"
  ],
  "conflicts_with": [],
  "priority": 60,
  "sources": [
    "sources/去除人物ai味-活人感"
  ],
  "applies_when": "人物生成需要具体身份状态和自然肤质约束"
}
-->
## 定义

在 AI 图像或视频生成中，“活人感”指人物看起来像真实人类：皮肤不过度磨皮或油亮，表情不是单一僵硬的情绪标签，动作幅度和动因符合当前情境。它不是单纯提高分辨率或真实风格，而是要在生图和生视频两个环节分别给模型更具体的人物约束。

## 核心方法

1. **人物基础设定具体化**
   - 明确年龄、形象、身份、当前状态。
   - 避免只写“美女”“老人”“古风人物”等宽泛标签。

2. **肤质约束**
   - 写清真实肤质、自然纹理、轻微瑕疵、不过度磨皮、不过度油亮。
   - 用于修正 AI 人物常见的“脸太油”“皮肤像塑料”问题。

^msb-kb-human-001
<!-- /msb-knowledge -->
<!-- msb-knowledge
{
  "id": "MSB-KB-HUMAN-002",
  "canonical_key": "character-realism.microexpression-motivation",
  "kind": "rule",
  "status": "enabled",
  "authority": "wiki",
  "projects": [
    "Master-Storyboarding"
  ],
  "stages": [
    "creative-director",
    "storyboard-director",
    "voiceover-to-video-script"
  ],
  "topics": [
    "character-realism",
    "character-performance"
  ],
  "triggers": [
    "微表情",
    "动作动因",
    "眼神",
    "肢体动作",
    "提示词密度"
  ],
  "conflicts_with": [],
  "priority": 60,
  "sources": [
    "sources/去除人物ai味-活人感"
  ],
  "applies_when": "人物情绪和动作需要拆成克制、可见且有动因的局部变化"
}
-->
3. **微表情拆解**
   - 把“开心、哭、生气、惊讶”等抽象情绪拆成局部状态。
   - 可拆到眉毛、眼睛、鼻子、嘴巴、嘴角、面部肌肉紧张度。

4. **肢体伴生动作**
   - 真实人物做表情时通常会有身体配合。
   - 例如紧张时捏衣角或低头，害羞时眼神躲开或轻咬唇。

5. **动作三要素**
   - 哪里在动：动作发生在身体哪个部位。
   - 怎么动：动作方向、幅度、速度和节奏。
   - 为什么动：动作背后的情绪或情境原因。

6. **控制提示词密度和动作幅度**
   - 生视频时底图已经决定大部分质感，视频提示词不要写得太满。
   - 活人感来自细小、合理、有动因的动作。
   - 大幅、无动因、全身乱动容易显得“假的离谱”。

## Prompt 检查清单

生成人物图或人物视频前，至少检查：

- 是否写清人物年龄、身份、形象和状态？
- 是否明确肤质，避免油亮、塑料、过度磨皮？
- 是否把情绪拆成可见的微表情？
- 是否为表情补充了自然的肢体伴生动作？
- 是否说明动作发生部位、运动方式和动因？
- 视频提示词是否过满，是否干扰底图既有质感？
- 是否限制夸张动作和不必要的大幅摆动？

^msb-kb-human-002
<!-- /msb-knowledge -->
## 示例结构

```text
人物：18 岁古风女生，淡青色交领汉服，当前状态是安静等待。
肤质：自然皮肤纹理，轻微真实瑕疵，不过度磨皮，不过度油亮。
微表情：眉毛轻微上挑，眼神直视镜头，嘴角轻轻上扬。
伴生动作：手指轻轻握住团扇边缘，幅度很小，因为她正在克制紧张。
视频动作：眼神短暂躲开后重新看向镜头，嘴角轻微上扬，因为她从紧张转为放松。
禁止：夸张大笑、脸部油亮、塑料皮肤、动作幅度过大。
```

## 关联

- 资料来源：[[sources/去除人物ai味-活人感]]
- 相关方法：[[wiki/concepts/03-AI生成与视频/ai视频生成方法论]]
- ➕ **表演的克制与层次**：[[concepts/03-AI生成与视频/ai人物反向表演]]（[[sources/ai人物反向表演-刺猬星球ai]]）——把"微表情拆解/动作动因/克制"落成 3 条可操作写法：①给情绪加隐藏目的（表里不一）②用大动作掩盖小动作 ③给情绪加时间顺序（微表情是变化过程，非静态结果）。
- 工作流：[[synthesis/ai-视频制作工作流]]
