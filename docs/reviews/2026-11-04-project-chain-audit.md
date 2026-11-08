# 项目补丁链路审查与修复

日期：2026-11-04。对象：当前仓库及本轮工作区补丁，重点覆盖项目状态、产物登记、返工恢复、资产导入/确认/导出、Blender 确认、声音同步及阶段交接。实际生产素材和已确认业务产物未修改。

## 根因与修复

| 问题 | 根因 | 修复与直接验证 |
| --- | --- | --- |
| 旧状态快照吞掉后来追加的确认 | 仅锁最终写入，未校验读取版本 | 状态读取携带原始 JSON 的内存版本，写前拒绝冲突；状态筛选保留版本，成功保存刷新原对象版本。跨导入别名、重复保存及声音扩展字段回归覆盖 |
| 恢复旧事务覆盖后来成功的写入 | 部分写入口互斥，但未在读取前完成已决定的事务 | 项目写锁内先恢复，再读取/变更；返工写入口统一接入。恢复先预检全部目标原字节及暂存内容，遇到第三方改动整次拒绝，不先覆盖其他目标 |
| Blender 确认的恢复与写入存在空隙 | 单独恢复后释放锁，再取原始锁 | 确认入口在同一项目写锁内完成恢复、生产门禁与确认事务 |
| 导出失败的图片提前成为下游素材 | 正式目录先写 confirmed 清单，登记失败后被历史兼容发现逻辑消费 | 先登记 candidate，再写清单；登记失败不暴露新 confirmed 文件，candidate 保持不可消费。未普遍禁用历史素材读取 |
| 导出重试改写已确认记录 | 只凭网页缓存判断提交是否完成 | 以已登记 candidate/current 与原清单、确认记录恢复；保留原字节，版本/选图/记录不符则拒绝，不重写确认依据 |
| 未重新确认的叙事正文进入资产制作 | 无独立资产表时漏查叙事包已有 checksum | 资产入口补核叙事正文与登记 hash；篡改测试拒绝。未为真实历史项目补造 hash |
| 声音同步写入另一个项目 | plan 路径未与被锁项目做归属核对 | resolve 后统一拒绝项目外计划/清单，包括绝对路径、../ 和符号链接；合法相对/绝对路径保留 |

另一并行任务对锁所有权、跨模块 manifest 版本检查、声音超时异常、授权用途、现代声音依赖、对白证据和产品核对声明的补丁均保留并合并验证。没有用本轮修复覆盖其新增实现或测试。Windows 空文件可直接锁定文件尾后的字节这一点另核过 [Microsoft _locking 官方说明](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)；文档核对不替代 Windows 实机验证。

## 防止误伤的处理

- 先在独立工作树检查和修改，再与原目录最新内容三方合并；应用前核对原文件字节未变化，仅回写本轮增量。保留另一边的 Skill/SOP、schema、声音和产品核对修改。
- 复用现有合同与事务，没有新增生产阶段、批准事实或自动生成/发布动作。遇到状态过期或恢复冲突要求重新读取/人工核对，不自动合并用户确认。版本检查适用于 load 返回的快照；普通 dict 初始化/显式替换的兼容行为保留。
- 新增回归先复现失败后修复；旧测试中为构建新场景而改写的合成叙事包补齐对应登记 hash。正式采用/交接夹具复用合法合成对白证据；刻意篡改的负例保留，未放松产品门禁，也未改写真实项目的确认依据。
- 测试目录每次独占，使用真实 /private/var 路径，避免 macOS /var 别名造成字符串断言误报。Python 依赖装在仓库忽略的 .venv，使用已有 Node/FFmpeg/FFprobe，不修改全局环境或依赖清单。

## 最终验证

- 原项目完整 Python 回归：**2375 passed，4 skipped，177 subtests passed**（46.30 秒，退出码 0）。范围包含 scripts/tests 与根 tests；本次运行期间 scripts/skills/schemas/config/tests 的修改文件字节未变化。
- JavaScript 回归：**77 passed，0 failed**。
- 内置知识包：**52 个文件校验通过**，动态集合仍只有 MSB-KB-PROMPT-002；不是全 Vault 审计。
- pip check、阶段 CLI --help 及保留 CRLF 的差异检查通过。
- 4 项跳过分别为可选本地 ASR 依赖、Windows 项目校验器，以及两个须显式提供私有素材路径的只读回归。未以合成数据替代这些未运行范围。

原目录最终结果：`verified-final-pytest.log`、`original-final-node.log`、`final-validation.json`。所有修复已回写当前工作区。审查完成时尚未提交；用户随后明确要求本地提交，本次版本记录以 Git 日志为准，未推送远端。

完整日志位于本轮独占目录 `/private/var/folders/hq/x035j8j52754y03_pys1s4nh0000gn/T/msb-chain-review-7ro0xtla/`，包含失败复现、隔离版本、合并补丁及最终回归。主要命令：

```sh
# 在项目根，先激活 .venv，并为本进程提供既有 Node/媒体工具的 PATH。
python -m pytest -q -rs -c config/pytest.ini scripts/tests tests
node --test scripts/tests/*.cjs
python -m scripts.core.knowledge_runtime
python -m pip check
git -c core.whitespace=cr-at-eol diff --check
```

每次 pytest 使用新的独占 --basetemp / cache_dir，并把 TMPDIR 设置为真实的 /private/var 路径。

## 验证边界

这些结果覆盖源码合同、合成项目和本机进程/线程行为。未执行 Windows 原生锁实机验收、真实生产素材审查、浏览器/桌面界面验收、Blender 渲染或付费生成；未发布、推送或更改业务项目确认。
