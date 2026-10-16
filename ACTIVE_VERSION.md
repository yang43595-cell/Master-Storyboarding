# msb-working-20261014-mask-r3

2026-10-14 按用户明确要求，把本轮认可的造物者真人上脸效果标准与流程写回既有方法/模板，并启用为后续工作入口。

- 基线：b25758ee3fcfbf7d89100bcdd17f5b5bba06a3f6（失败图重生规则工作版）
- 授权：Sentinel_30ad826689ac8191b1fb5c46171b48ed；只涉及上脸标准/流程，不实施整份其他案例复盘
- 质量样例：R13，Library libfile_c0f45ebc678881919b127315c4a9af49，SHA256 2c30768d557abf65db2de56e3929e52d89754da245f0b52da2dfab2e7cf239d2；是用户确认生成图，不是实拍或默认换人输入
- 默认入口仍为 active-storyboarding/current 与 run-current.sh；先读 AGENTS、PROJECT_RULES 和适用Skill
- 完整330工作文件保留；旧代码和知识工作版保留，不覆盖项目产物

## 读取与依赖

知识工作副本：active-storyboarding/knowledge/msb-working-20261014-mask-r3/vault。继承character-r2，改动仅既有造物者使用方法、既有真人上脸模板、经验索引及一篇本轮问题证据页。源码的具体条件指向与普通会话prepare读取在此版本提交；知识改前/改后hash见恢复包knowledge-change-manifest.json；该清单SHA256为 52f24d01ce583d95f96d92a34cb2c8e439a542cddeb57a508e9963f7cfb43b94。

资产与分镜请求只有明确真人上脸效果时设置creator_mask_face_effect=true，加载同一个专项方法和其template_path；碗内搅拌、英雄展示与其他产品不触发。其他既有读取与生成授权边界不变。

恢复包保存完整源树、Git bundle及本次必要知识/真实证据小快照。完整Wiki仍依赖已合法恢复的外部Vault（Library libfile_cc8905d9d1d8819199bed2b193574166）；前版人物模板改动继承自完整包libfile_c5e48e4e72b881918bcd8008cd6fc964，不称新包包含全Wiki。

## 回退

同时把current和launcher的Vault根切回保留的msb-working-20261014-character-r2目录；代码回b25758e，知识亦回旧工作副本。已确认图片、C5采用记录及网站由项目版本记录管理，不在Skill回退时覆盖。无远端、不自动push。
