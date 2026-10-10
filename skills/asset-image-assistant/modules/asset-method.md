# 资产方法

Web 两种模式按 [Web 受限读取合同](web-input-contract.md) 替代下文的 prepare / ensure-prepared；本节的适用方法与规范仍须完整送达并阅读，不能省略。

## 本 Skill 产出须命中的规范（缺即停）

产出人物/场景/道具/产品图前，先 `python scripts/stage_context_cli.py ensure-prepared --project <项目> --skill asset-image-assistant` 校验准备状态，再核对下列适用规范。包缺失时按 `skills/_shared/knowledge-access.md` 自行准备或报告真实阻塞；动态规则与直接读取的本地规范分别核对，本地规范无需重复嵌入知识包：

- **色卡统一风格**（锁整片跨镜头配色）：见 `references/01-style-master-guide.md` 的"整片色彩统一（色卡）"工作流；有 `STYLE_REFERENCE` 或需锁整片配色时必做，它是与背景母版、多角度参考组并列的第三种一致性路线。
- **图片连续性规则（背景母版 + 多角度参考图组）**：必读 `../storyboard-director/references/image-continuity-hard-rules.md`，不再视为"涉风险才读"；按需用背景母版锁固定层、用多角度参考组锁场景空间。
- **对应类别 master-guide**：按当前 AST 类别必读 `references/01-style-master-guide.md`（风格/色卡）、`references/02-character-master-guide.md`（人物）、`references/03-scene-master-guide.md`（场景）、`references/04-prop-product-master-guide.md`（道具/产品）的对应部分（非可选），把抽象词落成可观察的拍摄/材质/光线条件。
- **提示词语言合规**：写提示词必读 `../voiceover-to-video-script/references/prompt-language-hard-rules.md` 并以其为准；禁用 影视级/8K/唯美/高级 等炫技含糊词。人物/空间/产品/光线四项连续性分别检查。

先从已登记确认的独立资产表结合按 PROJECT_RULES.md“用户调整资产图需求”核对并登记的用户调整，建立本轮（同修订 JSON 核对完整性，旧版未拆表项目兼容叙事包入口） `AST-*` 白名单，再为每个登记需求建立稳定身份、状态、交互、展示面、朝向、适用范围、必须继承和不可继承字段。生成能稳妥覆盖实际用途并留有连续性余量的母版与必要状态；每个候选记录来源参考和单变量变化，人物、空间、产品与光线连续性分别检查。

复杂度和风险只决定已登记 AST 的实现深度，不授权新增资产。已有确认母版足够时复用；发现需求本身缺失或矛盾时报告缺口并停止该项。

母版按各资产类型的指南构建，生成顺序以其为准：**人物**=先按实际风险选择单张综合参考或身份板路线；仅身份板路线执行身份母图→全身/服装验证→合成 1×4 身份资产板（`references/02-character-master-guide.md` §四）；**场景**=母图先确认→再派生机位（`references/03-scene-master-guide.md`）；**道具/产品**=母图→正面/尺寸比例验证（`references/04-prop-product-master-guide.md`）。

凡本 Skill 写出图片/资产提示词，必先读 `../voiceover-to-video-script/references/prompt-language-hard-rules.md` 写词规范并以其为准；不得再视为"涉相关风险才读"。提示词禁用 影视级/8K/高端/唯美 等炫技含糊词，按对应 master-guide 把抽象词落成可观察的拍摄、材质与光线条件。

## 身份替换与年龄版本

替换人物面容时，定向核对受影响的既有年龄/状态版本、确认输出及下游引用；同一人物身份不等于同一年龄状态。多个年龄条目共用一张新照片时，说明它只锁身份，年龄由提示词表现，不能据此判定年龄已经被图片覆盖或取消原年龄要求。旧年龄图若属于旧面容，不直接混入新身份。

已有适用年龄图就按获准职责复用；只有身份图但本段允许通过提示词表达年龄时，明确职责和未验证效果即可，不把同一路径自动判成缺图或阻塞。确需补图时仍由用户决定需求与生成，不自动恢复旧版本、新增AST或生成各年龄一套图片。年龄来源用当前剧情的明确节点，不自行补算未知年龄。

## 角色类型与身体外观

CHARACTER 是叙事角色类别。先按 character_type 选择：人类及人形角色沿用人物身份与服装方法；动物和非人形角色读取 references/02a-body-character-master-guide.md，使用角色外观母版，不加载人类脸部、无头人台及服装拆分方法。所有路线仍执行图片连续性方法、动态提示词规则与用户审图确认。旧版未登记类型保持旧路线；已明确的类型更正写入项目资产需求调整，不改已确认剧情。
