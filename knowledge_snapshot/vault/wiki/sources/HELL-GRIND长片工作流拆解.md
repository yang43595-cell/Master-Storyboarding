---
title: "资料摘要：Higgsfield《HELL GRIND》长片工作流拆解（八大技巧）"
type: source
aliases: [HELL GRIND 长片工作流, Higgsfield 八大技巧, 长片工作流拆解, AI长片制作, 学长细节]
created: 2026-08-22
updated: 2026-08-22
tags: [ai视频, 长片, 工作流, 角色一致性, 空间一致性, 声音一致性, Higgsfield, AI电影]
source_url: "https://v.douyin.com/KVjiwEq8RuI/"
source_type: video
read_date: 2026-08-22
screen_artifact: raw/transcripts/HELL-GRIND长片工作流拆解-画面.md
segments:
  - {start: "0:00", end: "0:33", type: "开场钩子", summary: "Higgsfield 把 95 分钟 AI 长片《HELL GRIND》幕后制作方法全开源：15人/14天/50万美元；博主学长总结八大技巧"}
  - {start: "0:35", end: "1:42", type: "干货·技巧1角色资产", summary: "面部近景+正/背全身图，正面全身图去头逼模型只读高清脸防角色脸迁移；多版本角色资产+PS蒙版局部修复保皮肤质感"}
  - {start: "1:42", end: "2:16", type: "干货·技巧2声音资产", summary: "为每个主要角色固定一条声音描述，每次开口前放入提示词；先试声防漂移，稳定后作为正式声音姿态"}
  - {start: "2:29", end: "3:10", type: "干货·技巧3场景资产", summary: "场景少用完全正面，优先三分之四侧面；放明显参照物（柱子/灯/沙发），角色位置围绕参照物写"}
  - {start: "3:10", end: "3:38", type: "干货·技巧4空间地图", summary: "提示词里用几行文字写清环境与摄影机位置，让模型明确知道空间"}
  - {start: "3:38", end: "4:03", type: "干货·技巧5多人戏开头放全景", summary: "每场多人戏开头先放约1秒无复杂动作全景，再切近景，防角色换位置"}
  - {start: "4:03", end: "4:47", type: "干货·技巧6参照物证明大小", summary: "写'30米巨人'会被缩成2-3米；要用熟悉参照物在画面里植入证据"}
  - {start: "4:47", end: "5:38", type: "干货·技巧7用角度而非焦段", summary: "模型不理解85mm等相机数据，更理解视场角；更有效的是写出画面结果"}
  - {start: "5:44", end: "6:56", type: "干货·技巧8重量惯性重力+反例", summary: "写'非常真实的黏液'没用，要写物理性质；动作加重量惯性；不堆8K/毛孔等长段通用提示词"}
  - {start: "6:56", end: "7:22", type: "收尾", summary: "真正值得学的不是单个技巧，而是15人完成95分钟长片的完整工作流；博主分享官方文档与3个skill"}
---

# 资料摘要：Higgsfield《HELL GRIND》长片工作流拆解（八大技巧）

> 抖音博主「学长」把 Higgsfield 开源《HELL GRIND》长片制作文档啃完后，总结出的 8 个可落地技巧：角色资产、声音资产、场景资产、空间地图、多人戏全景、参照物证明大小、用角度描述镜头、重量/惯性/重力。

## 基本信息

| 项目 | 内容 |
|------|------|
| 平台 | 抖音 |
| 作者 | 学长（抖音 AI 教程博主，账号名待确认） |
| 时长 | 约 7 分 22 秒 |
| 视频文件 | `raw/videos/AI自媒体/AI电影元年到了？拆解Higgsfield95分钟长片…《HELL GRIND》…工作流.mp4` |
| 转写稿 | `raw/transcripts/HELL-GRIND长片工作流拆解-转写稿.txt` |
| 来源链接 | https://v.douyin.com/KVjiwEq8RuI/ |
| 阅读日期 | 2026-08-22 |

## 段分析

| # | 时间段 | 段内容 | 段类型 |
|---|--------|--------|--------|
| S1 | 0:00-0:33 | 开场：Higgsfield 把 95 分钟 AI 长片幕后方法全开源（15人/14天/50万美元预算），不只成片，还有角色/场景/空间/提示词怎么写的整套方法论 + 内部 Skill；博主总结八大技巧 | 开场钩子 |
| S2 | 0:35-1:42 | **技巧1·角色资产**：先生成满意的角色图马上做视频是错的。要为角色准备①高清面部近景 ②正面+背面全身视图，且**正面全身图把头去掉**，逼模型只能从高清脸图读脸（否则全景镜头会从全身图的小糊脸上提特征，脸被迁移成某个明星）。角色要有多个版本资产（正常/临时/受伤/带血/换装）。局部修改用 Cdream/Banana 做伤疤 + **PS 蒙版贴回原图**，最大程度保留初始皮肤质感（AI 生成次数越多图片质量越差，局部问题只改局部） | 干货·技巧 |
| S3 | 1:42-2:16 | **技巧2·声音资产**：不止上传原音色，给每个主要角色固定一条**声音描述**（不是同一句台词，而是固定声音特征，如"低沉粗犷的男中音、语速缓慢、带一点伦敦街头口音、平静中带着威胁、越生气说得越轻"）。每次角色开口前把这段描述放进提示词。最好先试声几次测音高/速度/口音会不会飘移，稳定后才作为正式"声音姿态" | 干货·技巧 |
| S4 | 2:29-3:10 | **技巧3·场景资产**：场景姿态少用完全正面（太对称太扁平缺空间纵深），优先**三分之四侧面**（能同时看到房间前后左右，模型更易理解空间）。场景里放一个明显参照物（柱子/灯/沙发），所有人位置围绕它写：不要写"站在房间里"（哪里都能站），要写"站在柱子左边一米，身体朝向门口" | 干货·技巧 |
| S5 | 3:10-3:38 | **技巧4·空间地图**：提示词里用几行文字写清环境布局 + 摄影机位置，如"平台在画面中央，巨石在平台右边三米，仪式中心在画面左边，摄影机始终站在门口这一侧，背后的红光从平台后方照过来"。模型就会明确知道空间长什么样 | 干货·技巧 |
| S6 | 3:38-4:03 | **技巧5·多人戏开头放全景**：尤其是多人戏，每场戏开头先放约 **1 秒全景**（不做复杂动作），让模型看清谁站在哪里，再切近景，人物不会突然换位置。每次浪费 1 秒，但大幅提升不同镜头间角色空间位置一致性 | 干货·技巧 |
| S7 | 4:03-4:47 | **技巧6·参照物证明大小**：写"30 米高的巨人"，模型会生成成 2-3 米高——因为 30 米只是数字，画面里没东西证明。要用熟悉参照物在画面里植入证据："一个人只到他的脚踝，他的手掌和一辆汽车一样宽，他的头伸进屋顶的黑暗里" | 干货·技巧 |
| S8 | 4:47-5:38 | **技巧7·用角度而非焦段**：写"85mm 长焦/超广角"，模型不一定真懂这些相机数据，更懂**视场角**。84°≈广角、47°≈接近正常视角、29°≈适合拍人物的长焦、8°≈超长焦（不用死记，不同模型可能不同）。更有用的是**写出画面结果**：长焦=摄影机离人物很远但拍面部特写、前景背景虚化；广角=摄影机离人物近、周围环境都展现、空间延续到画面边缘 | 干货·技巧 |
| S9 | 5:44-6:56 | **技巧8·重量/惯性/重力（+反例警示）**：写"非常真实的黏液"没用，要写"明显黏性、贴在皮肤表面、沿重力方向缓慢流动、接触衣服留痕"。动作同理：沉重手臂把肩膀往下拉、突然停住时托盘里的水先倾斜再恢复平稳。**反例**：官方文档里那段超长统一要求（8K 细节、皮肤毛孔、不能闪烁、不能复制人物等）不建议整段照抄——通用提示词堆太多又长又乱，反而干扰模型理解重点 | 干货·技巧+反例 |
| S10 | 6:56-7:22 | 收尾：真正值得学的不是单个技巧，而是他们根据自身情况制作的**完整工作流**——这才是 15 人完成 95 分钟长片的关键。博主分享官方公开文档 + 3 个 Skill（评论区打"学长细节"自取） | 收尾 |

## 画面内提示词（屏幕上未口播的文字）

> 以下由抽帧逐帧读取得到，是博主展示在屏幕上但**配音未读出来**的原文，可补足纯音频转写的盲区。视频文件：`raw/videos/AI自媒体/…《HELL GRIND》…工作流.mp4`。
>
> **提取方法（严格版·二次扫描）**：首次按关键帧抽取 14 张；后升级为 **1fps 全量抽帧 442 张 + 本地 PaddleOCR 预筛**（零 token，筛出含大段文字的面板帧），再对候选帧做**多模态精读**拼接滚动文本。本次共确认 **8 处真实提示词/规则面板**，并对首次版本做了 2 处修正（①「three-quarter→**three-panel** layout」；②「左上角/琥珀色→**左下角/琥珀黄色**」）。

### ① 角色资产·保真 prompt（约 0:45，Nano Banana Pro，英文）

博主展示角色「面部近景 + 正/背全身图」三件套时的平台 prompt 原文：

> **Take the attached image. Keep absolutely everything identical** — same character, same face, same outfit, same dirt, same scratches on body, same wet hair, same poses, same three-panel layout, same backdrop, same expression. **Do not change anything in the image.**

- 参数：Model Nano Banana Pro，Quality 4k，Size 5504×3072，Creator aitore，Created April 28, 2026 at 6:27 AM。
- 印证：三个角色视图（左：正面全身 / 中：背面全身 / 右：高清面部近景）同屏展示，正是技巧1的三件套；字幕「足够清晰的面部近景」—「再准备正面和背面的全身视图」。

### ①b 多状态角色·post-mortem 版 prompt（约 0:50，Nano Banana Pro，英文）

> **Edit this character reference sheet image to produce a subtle post-mortem version of the character with restrained realistic injury detail. Apply the following changes while keeping everything else identical.**

- 参数：Model Nano Banana Pro，Quality 2k，Size 2752×1536，Creator racingkangaroo1187，Created May 9, 2026 at 12:30 AM。
- 印证：这是技巧1「多状态角色资产（受伤/带血版）」的真实 prompt；同屏字幕「并且他们会把正面〔全身视图上的头给去掉〕」。

### ② 背景母版·局部编辑 prompt（约 3:20-3:33，Nano Banana Pro，中文）

博主「比如说」展示一段「**仅做两处最小局部更改**」的图像编辑 prompt（示例画面为火山/雪山地貌）：

> 仅需对图像进行**两处最小的局部更改**即可编辑此图像。保留其他一切，保持原样——构图、树干。还有树根、前景的岩石、草丛和灌木、天空、云朵。温暖的地平线光晕、朦胧的大气、雪的质感、氛围、色彩等级和整体照明均保持不变。
>
> **更改 1 — 仅在背景中添加更多火焰**：添加额外右侧中景和远景散布着零星小点火。在不同距离的地平线上，可以看到更多燃烧的斑块。烟雾弥漫，每场新火灾都会升起额外的细烟柱。并与现有的雾气融合、保持现有火势。
>
> **修改 2 — 调整树冠的枝叶，使其与古代树冠相匹配**：日本枫树更显眼：密度略高，数量更多；树冠以深红色为主的茂密树叶为主，较小且清晰可见的乳白色淡霜状叶片簇；树冠右上角更加突出，清晰可见。保留左下角的小块琥珀黄色区域。只需调整即可。只保留叶子本身——不要改变树干、树枝和根。树的系统、轮廓或位置。**整体变化幅度最大为 10%。输出一张统一的照片。与现有宽高比保持一致。**

- 印证：这是 [[concepts/02-镜头与剪辑/图片连续性规则#背景母版工作流]] 的标准写法——「固定层不可变 + 只改一个动作层」，且用「变化幅度 ≤10%」量化控制，比中文库里现有模板更具体。
- ⚠️ 时间偏差：该画面实际展示的是「**背景母版·局部编辑**」prompt（约 3:20-3:33），但博主此时口播的却是**技巧4·空间地图**——这正是「抽帧文字与口播段可能错位」的典型证据；需按**画面内容**而非时间戳归属。

### ③ 场景资产·三分之四侧面 prompt（约 2:43-2:49，Nano Banana Pro，英文）

博主展示「三分之四侧面」场景设定图时的 prompt 原文（示例场景为**废弃商业披萨厨房**）：

> **Photorealistic ultra-wide cinematic interior shot of a dark, gritty commercial pizza kitchen, shot from a low angle.** The kitchen is located in the back of a closed, abandoned-feeling pizzeria. No windows — the only ambient light bleeds faintly through a pass-through counter opening from the…

- 第二个变体（约 2:48）：**Ultra-wide cinematic photo of a completely dark, abandoned commercial pizza kitchen at night.** No light sources visible in the frame — total darkness, only the faintest residual ambient night glow seeping through the pass-through counter gap from the unlit dining hall. The entire scene is swallowed in…
- 参数：Model Nano Banana Pro，Quality 2k，Size 3168×1344，Creator levitatingant1150，Created April 29, 2026。
- 印证：技巧3「场景优先三分之四侧面」的实例；字幕「而当镜头变成 3/4 角度时」—「这个房间的前后左右」—「模型就更容易理解整个空间」。
- ⚠️ **修正**：此前记录为「烛光神庙」参考图有误，实际为**商业披萨厨房**场景。

### ④ 声音资产 prompt（约 2:00-2:16，Project Brief，英文）

> **The voice is not an asset**（标题）
>
> Seedance holds three or four voices per character inside one tonality — enough for a feature film, but only if you manage the voice. Lock every hero's voice in pre-production, before the dialogues: register, tempo, accent, manner. The voice prompt is pasted into the audio field as is, every time the hero speaks, and it never changes:
>
> **Voice: deep, gravelly bass-baritone; slow, calculated pacing; London street accent; menacing calm - he never raises his voice.**
>
> Test how the voice holds between generations — the same way you stress-test the look; if it drifts, go back and lock the wording harder.

- 印证：技巧2「固定一条声音描述 + 每次开口前放入提示词」的英文原文，正是 [[concepts/03-AI生成与视频/ai视频生成方法论|AI 生视频方法论·声音资产]] 所描述「低沉粗犷的男中音、语速缓慢、带一点伦敦街头口音、平静中带着威胁」的原始 prompt。
- 开头还有一条行为/验证规则：`breaks when he shares the frame with someone. If the test fails, the problem is your description, not the model. Rewrite the words, test again.`
- 同屏下方继续讲「**行为画像**」：`…a character acts is locked the same way as the look and the voice` — 每个英雄在开拍前先写一段行为 profile（怎么动、手的习惯、怎么在压力下崩溃），每场戏只做适配、核心不变；"a behavior that is impossible in a scene is transferred, not deleted."

### ⑤ 参照物证明大小（比例锚点）prompt（约 4:31-4:44，Project Brief，英文）

> **Giants live on scale anchors**: a size comparison in every prompt, plus a human figure in the frame to measure against. Without both, the model quietly shrinks the giant back toward human height. Our thirty-metre stone guardian carried this constraint in every shot he appears in:
>
> **POSITIVE CONSTRAINTS — THE SCALE LAW (VISIBLE PROOF IN THE PICTURE):** the stone guardian stands THIRTY METRES tall — his head is lost in the darkness of the dome, his open palm is as wide as a family car, and ROCO at his foot reaches just above the ankle. In every frame the guardian's silhouette is at least FIVE TIMES the height of the human figure beside him, and the frame cannot hold both his feet and his head at once. A guardian that reads as a large man, or fits comfortably in frame next to a standing human = **failed shot**.

- 印证：技巧6「用熟悉参照物在画面里植入证据」的**完整英文原文**，与 [[concepts/02-镜头与剪辑/图片连续性规则#角色与空间一致性（长片级）]] 中「参照物证明大小比例」直接对应（头没入穹顶黑暗、手掌如汽车宽、到脚踝）。

### ⑥ 角色资产·"资产 = 描述 + 参考图" 与 去头规则（约 6:41-6:44，Project Brief，英文）

> **An asset is a simple pair: text + image.** The text is a full description of the character or place — we call it a **descriptor**. It goes into every prompt, word for word. The image is a reference — the model uses it as an anchor. Together they keep your hero the same person from shot to shot.
>
> **A character sheet is three images:** a close-up of the face, a full body from the front, and a full body from the back. And the front full-body figure **has no head**. This sounds insane, but it fixed a whole class of broken shots. On wide shots the model kept taking the face from the small full-body figure on the sheet — where the face is tiny and blurry. Remove that head, and the model has only one place to take the face from: the close-up.

- 印证：技巧1「脸部分离 + 正面全身图去头」的**底层原理英文原文**，解释为何去头能断掉模型从低清全身图取样的路。

### ⑦ 角色脸蛋·质感与选脸规则（约 6:44-6:46，Project Brief，英文）

> **Faces were born in Soul Cinema.** It gives the best skin texture, but it is a creative model: one prompt returns several different versions of the face. **Pick the most believable one, not the most beautiful one.** A "beautiful but fake" face will show its fakeness later, in video — when it is too late to fix. And always check the eyes: even dark eyes need a small light pupil (a catch-light). Without it the face looks dead, and no video model can act with a dead face.

- 印证：技巧1「脸部要真实可信而非漂亮」——选最可信的脸，而非最漂亮的脸；眼睛必须有高光（catch-light）。

### ⑧ 反例·Project Brief 的蒙版规则 + "keep the sheet boring"（约 6:46-6:48，Cinema Studio「Project Brief」，英文）

博主提醒"这段不建议完整照抄"时的 Project Brief 原文：

> **Keep the sheet boring on purpose.** Neutral grey background. Flat light. Real skin with visible pores, no retouch. The cinema look does not live in the character sheet — it lives in the locations and in the video prompts. Bake film grain and cinematic lenses into the sheet, and the character will carry that look into every scene and stop reacting to new light. One more thing we learned: the sheets the model understands best have a large portrait in 3/4 view (the face turned slightly, not straight-on).
>
> **Clothes, scars and blood were added as point changes.** Our workflow — one of the possible ones, but it kept the quality for us: make the point change on a character sheet in **Nano Banana Pro or Seedream 4.5**, then bring it onto the original by hand, that works with masks. **The mask places only the changed part (the jacket, the scar, the blood) on top of the original; everything else stays untouched, so the original skin texture survives. The rule behind it: an image never runs through a model twice in full.** Every extra pass destroys texture and drifts color — after two passes the face turns symmetrical, plastic and lifeless, and that dead texture later hurts the acting in video.

- 印证：这是技巧1「PS 蒙版局部修复」背后的底层原理原文（图不重跑两次全文；每多跑一次就损纹理、漂颜色），并补充了「keep the sheet boring」与「3/4 大肖像」两张角色卡规则。
- 左下角为 Higgsfield 项目资产树：`Hell Grind / Project brief`，文件夹含 `1. COLD OPEN(1748) / Assets(4886) / Credits(1883) / Flashbacks(3194) / Regenerations(14593) / Scene 13-19…` —— 即「资产 + 场次 + 镜头 + 生成 + 迭代」项目表结构，与第三方 `Hell-Grind-AIGC-Skill` 的 schema 一致。

### 其他仅有画面的段（无文字）说明

- 约 0:04：HELL GRIND 成片（格斗/红色石柱），字幕「幕后制作方法全部开源」。
- 约 1:18：Higgsfield `Assets` 界面 + 多状态角色资产网格，字幕「他们会分别制作不同的角色资产」；约 1:19-1:26 同屏字幕「并且还会用到 PS 的蒙版技术」，配资产文件夹树（`Assets Pizzeria / Assets Tibet Sa... / Hand Scanner / Kitchen Assets / Roko's Shield` 等）。
- 约 2:05-2:16：**技巧2 声音资产** 段为「声音描述 + 行为画像」配置面板（见 ④）。
- 约 2:40：4 张场景三分之四视角参考图（**商业披萨厨房**），字幕「因为正面的图太对称太扁平」；右上角 `HIGGSFIELD UNLIMITED / SEEDANCE` 标识（见 ③）。
- 约 3:48：多人全景成片帧（黄帽男/紫衣女/受伤男），字幕「而这一秒先不做复杂的动作」。
- 约 4:22-6:00：博主口播讲技巧6-8，无屏幕文字。

## 核心主张

1. **角色资产（脸部分离）**：为角色同时准备"高清面部近景 + 正/背全身图"，并把正面全身图的头去掉，逼模型只从高清脸图读脸 → 防止全景镜头下角色脸被迁移成明星。角色按状态做多版本资产；局部修改用 AI 工具 + PS 蒙版贴回原图，保皮肤质感。
2. **声音资产（声音描述 + 声音姿态）**：声音一致性靠"固定一条声音特征描述 + 每次开口前放入提示词"，而不是让角色说同一句台词；先用试声验证音色稳定再定为正式姿态。
3. **场景资产（三分之四侧面 + 参照物）**：场景设定图优先三分之四侧面（空间纵深），并放置明显参照物；人物位置一律围绕参照物用"相对距离 + 朝向"描述（"柱子左边一米，朝向门口"）。
4. **空间地图**：用几行文字把环境布局 + 摄影机位置写成"地图"（谁在中央、巨石在右边三米、光从哪来、摄影机站哪侧），让模型对空间有确定性。
5. **多人戏 1 秒全景**：每场多人戏开头放约 1 秒无复杂动作的全景，锁定"谁站在哪里"，再切近景，解决跨镜头人物换位/穿帮。
6. **参照物证明大小**：抽象数字（30 米高）无效，要用画面内可感的参照物（到脚踝、手掌如车宽、头没入屋顶黑暗）植入尺度证据。
7. **用角度描述镜头**：用视场角度数（84°/47°/29°/8°）替代焦段数字，更好的是直接写出观众能看到的结果（远机位+面部特写+背景虚化 = 长焦感）。
8. **重量/惯性/重力替代"真实感"**：写物理行为（黏性、重力流动、手臂拉肩、水先倾后平）比反复写"真实感/电影感/大片感"更有效；不要堆长段通用负面提示词。

## 关键词

角色资产、声音资产、场景资产、空间地图、多人戏全景、参照物、视场角、重量惯性、PS蒙版、角色一致性、空间一致性、声音一致性、长片工作流、Higgsfield、HELL GRIND

## 与现有知识的关系

- ➕ **补充**：[[concepts/02-镜头与剪辑/图片连续性规则]] 目前锁「背景」「场景空间」；本条新增**角色脸部分离 + 空间地图 + 多人戏 1 秒全景**三类一致性手段。
- ➕ **补充**：[[concepts/03-AI生成与视频/ai分镜设计]] —— 新增「角色资产多版本 + 参照物定位 + 空间地图」的写法；「用角度/画面结果描述镜头」可并入其镜头词汇。
- 🔗 **关联**：[[concepts/03-AI生成与视频/ai视频生成方法论]] —— 六种任务中的「图生视频」可复用角色资产（面部近景+全身图）与「多模态参考」职责分配。
- 🔗 **关联**：[[concepts/02-镜头与剪辑/景别理论]] —— 用视场角度数（84°/47°/29°/8°）描述镜头的做法，是「景别 × 焦距」的另一种落地。
- 🔗 **关联**：[[concepts/01-叙事与对白/短剧电影叙事法则]] —— 长片 vs 短剧：短剧是竖屏高密叙事，长片技巧需取舍（如 1 秒全景在短剧里更金贵）。
- 🆕 **实体**：[[entities/higgsfield]] —— HELL GRIND 背后的 AI 视频公司，本次摄入新建。

## 可复用表达

- 「正面全身图去掉头，逼模型只从高清脸图读脸。」
- 「不要写'站在房间里'，要写'站在柱子左边一米，身体朝向门口'。」
- 「30 米只是数字，画面里要放进证据。」
- 「写物理行为，别写'真实感'。」
- 「每个动作拆半秒级步骤，场景先给 1 秒全景定位。」

## 事实与风险提示

- ⚠️ **转写疑点**（`raw/transcripts/HELL-GRIND长片工作流拆解-转写稿.txt`）：
  - "Xfield" = **Higgsfield**（模型丢失首音节）。
  - "面目镜景" = 应为**面部近景/脸部特写图**（取后者义）。
  - "Cdream 或 banana" = 两个 AI 图像编辑工具名，原文如此，具体平台未核实。
  - "C-Dance 模型" = 疑为 **Seedance**（同音误转写）。
  - "深声几次" = 应为**试声几次**；"南中音" = **男中音**；"清澈有效" = **亲测有效**。
  - "三个 skill" = 视频声称 Higgsfield 官方公开文档含 3 个 Skill，未在本视频内逐一展示；与 GitHub 上第三方抽取的 `Hell-Grind-AIGC-Skill`（renmu2017，模型无关 AIGC 生产工作流，七层提示词架构 + 失败诊断 + 迭代机制）互为印证，但**非同一物，需实测核验**。
- "评论区打'学长细节'自取"为引流话术，下载链接未验证。
- 方法均来自 Higgsfield 官方开源口径，未在本 Vault 实测；建议在短剧/长片项目跑批时验证后升级概念页为已验证。

## 延伸资料

- GitHub 第三方抽取：`renmu2017/Hell-Grind-AIGC-Skill` — 把《Hell Grind》全部提示词与制作方法抽成 Codex Skill（模型无关，七层提示词架构 L1-L7、12 段镜头契约、22 个方法模块、schema v2 十四张项目表、失败码+迭代机制）。
- 相关背景（检索核实）：《HELL GRIND》2026-05 在戛纳电影节行业放映（非官方竞赛单元），Seedance 2.0 生成；15 人/14 天/约 50 万美元，传统同级约 5000 万美元。
