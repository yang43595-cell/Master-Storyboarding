# Master-Storyboarding 三层依赖恢复备份

备份日期：2026-10-10。**这是有来源记录的历史源码＋项目知识依赖快照，不是最新正式生产版本，也不恢复正式提交6120及其未提交改动。** main和先前历史源码备份分支均保持不变。

## 已包含什么

- 第一层：9个Skill的84份执行文件，脚本、测试、schemas、配置、必要模板/示例、被引用的可恢复文档和资产注册表
- 第二层：13个已确认绑定对应12份不同的项目专业方法正文，保留原版本与enabled/current状态；不重新认定它们适合任何新任务
- 第三层：经验索引及2篇已登记条件性案例。案例事实、推断、未验证修复方向分开，不能当作2条已验证解决方法
- 外部知识：仅本项目依赖的112份文本/知识文件，包含上述L2/L3、2份运行模板、项目动态规则载体及来源正文；不是整个659文件知识库
- 原知识审计可解析75个动态知识块、14项仓库权威规则和43份被规则引用的来源页。文件、知识块与来源页是不同计数单位，不能相加
- 6张产品图、6段动作视频、3份L3图/QA包保留外部Library ID、归档成员和SHA256，不把大媒体塞进Git

## 来源与版本

代码基底是2026-10-08历史修复候选，而非生产采用版：Library libfile_9a507399ab9481918eb5bc1366f0c000，v1；ZIP SHA256为5eb6e2801e54a0c7c02da9500c3a46ed52237403558beb1359bf942448d21b10。

知识主要来自2026-10-05 20:51 UTC已校验归档：Library libfile_571c8b8874208191af637d15c93fe4fd；归档SHA256为bc2910ea40737e255d8583fefe9c31ca1763ddc3f5502a03b99614f7e0463dd5。2026-10-08导演知识定向导出libfile_b1be794c054081919f588403a8078987补充来源核对，所用正文与上述历史字节一致。逐文件来源、原成员、hash见backup/source_manifest.json。

本次不接纳后续情绪拍法/云经验等candidate，不复活被清除的旧经验，不复制无关账号、本机配置、认证、代理、私密运行日志或整个Wiki。

## 空目录恢复

1. 检出这个备份分支到一个新目录，使用Python3.13和Node
2. 按config/requirements-dev.txt在自己的隔离环境安装声明依赖
3. 在仓库根运行：python backup/check_restore.py。它只使用仓库内knowledge_snapshot/vault，验证文件hash、方法绑定、运行模板及原知识审计
4. 执行已有工具前，仅为当前shell设置MSB_VAULT_ROOT为本仓库knowledge_snapshot/vault的绝对路径。POSIX shell可从仓库根运行：. ./backup/use_snapshot.sh
5. 若需运行依赖真实产品图的原测试，先按backup/external_assets.json从已验证Library归档取回6张产品图，检查hash后放入清单给出的repo_path。动作视频和L3证据按任务需要单独恢复
6. 从仓库根执行原测试：python -m pytest -q -c config/pytest.ini scripts/tests；node --test scripts/tests/*.cjs

这只是恢复配置，不改业务架构、不连接原Vault、不设置自动同步、不生成媒体或发布生产项目。

## 明确未闭合项

- docs/development/cloud-knowledge-manual.md：历史Skill有引用，但原字节未找到。历史原型包不能冒充该正式文档；云知识维护功能须先取得并核对该原件
- 14条教程原始附件/目录引用在现有来源包不可恢复；相关知识与来源说明正文已保留，见backup/knowledge_references.json。外部教程链接或摘要不等于原媒体已备份
- 部分L3原制作工程/确认记录不在本备份，备份条件性经验正文不能证明全部实证产物已恢复
- 不包含最新完整制作状态、原生产Git历史、正式6120及dirty、模型权重和运行凭据。不能直接把本分支覆盖main或称为最新全量生产备份

最终实测结果与限制见backup/RESTORE_REPORT.md。
