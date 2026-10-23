# Master-Storyboarding：历史基线与当前两份 Skill 候选备份

这是 2026-10-13 创建的新 Git 恢复仓库，不是遗失生产工作区的恢复，也不是正式生产版本。

## 版本边界

- 历史来源为 Oct8 的 325 文件候选归档，归档 SHA-256 和固定来源引用见 source-provenance.json。固定 GitHub 引用用于证明已核对文件来源，不冒充新仓库的 Git 祖先。
- 为避免带入产品图片、项目历史材料和对话/用量审计记录，325 个源文件中仅 274 个代码、规范、测试及必要操作说明按原字节纳入。另 51 项在 source-manifest.json 记载校验与排除原因；产品参考文件名仅存路径哈希。原始来源目录均未改写或删除。
- 未包含真实媒体、凭据、个人资料文件、用户对话原文、缓存、node_modules 或外部 Vault。源代码/规范内既有的 Windows 路径示例仍保留原字节，不代表这些路径在此存在。
- recovery/base-20261013 是新的历史基线根提交，使用实际提交时间与当前自动化身份，不伪造旧作者、旧时间或遗失的 Git 历史。
- candidate/creative-r2-20261013 是编导 9 文件候选；candidate/director-choice-20261013 是导演 5 文件候选。两条分支均直接基于上述基线，不互相叠加。
- 两候选修改文件没有交集。共同依赖项目规则、共享读取规范、叙事/导演合同及外部知识方法；“文件不重叠”不证明组合行为正确。本备份未创建或宣称通过组合候选。
- 原有两个候选分别报告编导 253 定向、786 全套（1 skipped），导演 173 定向、784 全套（1 skipped）。这些是各自历史候选测试结果，不是未恢复生产版本的验收。额外克隆后测试结果以随包验证报告为准。
- 完整测试需要项目已有外部 Vault，默认本机路径不可用。恢复该外部依赖前，不应把本包当成可直接生产的完整环境。

## 本地恢复

解压交付包后，在新目录执行：

```sh
git clone Master-Storyboarding-recovery-20261013.bundle Master-Storyboarding-recovered
cd Master-Storyboarding-recovered
git branch -a
git switch -c creative-review origin/candidate/creative-r2-20261013
```

如需导演候选，在新的工作目录或干净工作树中切到 origin/candidate/director-choice-20261013。不要覆盖正在使用的生产工作区。没有配置 GitHub 远端，也没有推送、创建远端分支或 PR；远端只限已指定私有仓库，待精确文件范围及权限另行核实。

本包证明能恢复三条 Git 引用所含文件，不证明已找回生产 HEAD6120+dirty、运行中画布、网站状态、未提交修改、图片、视频或外部知识库。
