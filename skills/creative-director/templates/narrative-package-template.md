# 编导内容模板（JSON 真源，Markdown 按需导出）

> 本模板规定完整稿件的内容结构；交互顺序见 [项目串行交付规则](../../../docs/rules/artifacts.md#json-交付与按需导出)。新稿直接在 JSON 中完善，以下 Markdown 是可选阅读排版参考。问题在对话中集中提出；完善、自查后才交用户审稿。

## 阅读排版参考（可选导出）

```markdown
---
document_type: creative-script
project_id: <项目 ID>
revision: <Rxx>
status: candidate
---

# <项目名>编导稿

## 事实与范围锁定
## 最终对白
| 对白 ID | 说话者 | 原文 | 来源段落 | 状态 | 来源证据 |
### 人工对白核对附注（仅按需）
- 审查范围：<完整对白中需核对的范围；不等同整稿审稿>
- 疑点与已有时间点/画面：<逐条注明受影响 DLG、待确认映射及已有证据>

## 故事正文（含已核对对白）
### STP-001｜<定位标题>
<连贯写出场景初始状态、人物动作、关键物件变化及其因果；嵌入完整对白并标注说话人和 DLG ID。后续原文段落使用稳定 STP 编号。>

## 资产需求
| 资产 ID | 类别 | 名称 | 叙事用途 | 必须稳定 | 所需状态/交互 | 人物最终规格与服装状态、依据及输出 ID | 空间范围（仅场景） | 真实参考 | 未知项 | 处理结论 | 判断依据 |
## 变更说明
```

人工核对的对白归属依据和对应时间点或画面，记录在同一份[对白来源记录](../references/dialogue-lineage-contract.md)。

## `<revision>_叙事包.json`

严格使用 `schemas/narrative-package.schema.json` 2.0，逐段保存原文，不含 events。JSON 保存完整业务原文与稳定引用，新包省略 human_readable_path。以下阅读排版仅供按需导出，不能另行维护剧情。

## Artifact manifest 登记

- 结构化叙事包：稳定 `logical_id` 建议为 `narrative.package`，不依赖同义 Markdown。
- JSON 自查后交用户确认准确修订；采用记录绑定该 JSON，不覆盖旧修订，不登记导出为依赖。
- 普通候选不写 checksum；只有修订账本明确管理时才登记。

在 JSON 中直接填写人物或物件的实际名称及叙事用途。角色登记 `character_type`（HUMAN 人类、HUMANOID 人形角色、ANIMAL 动物、NON_HUMANOID 非人形角色）。动物和非人形角色登记稳定外观结构，动作状态留在剧情中。人类及人形角色的 `wardrobe_states` 按正文填写 `name` 与 `evidence`。需要制作或复用图片时，按下文所链判断填写 `character_output_kind`。如果选 `人物1x4`，每套服装另写唯一的最终 `output_id`，单套也登记。正文明确换装才新增，款式未定时交资产选图决定。自查最终规格、服装名称、换装数量和依据与正文一致。

先按[资产图需求判断](../references/asset-image-necessity.md)在 `asset_requirements` 填写 `image_decision` 和 `image_reason`，再确定需要独立选图或制作的粒度。可独立选图、制作和确认的对象分别登记，例如同用于送礼的酒瓶与卡片。服装归角色服装状态，产品的固定成套构成可共用一项。独立包装和不同展示状态写明各自的制作位置。上游修订须对齐项目 `04_资产/当前资产需求表.md` 和 `资产需求调整.json` 中的现行决定。
