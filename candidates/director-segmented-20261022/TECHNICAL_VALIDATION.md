# 后续确定性验证（隔离候选）

本次基于组合提交 `5da6d80bd33008ebe4404a874de464eaed825f70` 追加技术验证，不改旧证据、业务 schema 或生产默认，不启动真实创作。

## 依赖与实际校验

已先检查已有两个 Python 3.12 虚拟环境：没有可用 jsonschema。通过正常沙箱审批，使用项目原有 `scripts/install_runtime_deps.py` 和原字节 `config/requirements.txt`，指定官方 `https://pypi.org/simple`，安装到本任务的 `.master-storyboarding/visual-library-deps`，不写系统 Python 或既有虚拟环境。

项目版本范围为 jsonschema>=4.26,<4.27 与 referencing>=0.37,<0.38；实际安装 4.26.0、0.37.0 及所需传递依赖，登记原安装器 stamp。未升级 pip 或无关包；安装产物及 stamp 不提交远端。

使用已有虚拟环境解释器，先调用 `require_runtime_deps(Path.cwd())` 激活项目隔离依赖，再运行：

```python
from pathlib import Path
from scripts.core.runtime_deps import require_runtime_deps
require_runtime_deps(Path.cwd())
import pytest
raise SystemExit(pytest.main([
    '-q', '-c', 'config/pytest.ini',
    'scripts/tests/test_storyboard_planner_contract.py',
    'scripts/tests/test_confirmation_scope.py',
    'candidates/director-segmented-20261022/test_segment_tasks.py',
]))
```

结果 **47 passed**：33 项既有合同/确认范围检查，14 项候选技术检查。jsonschema 在测试前已可导入，旧套件中的可选 schema 分支此次实际执行；另有明确调用原 require_schema 的叙事 2.0 与合成导演 v4 正例，以及删除 camera.focus 后真实 schema 失败、禁止导出的负例。不是把缺依赖跳过报告为通过。

夹具全部为标注 SYNTHETIC 的技术 JSON，短文本只写 synthetic/fixture，不来自真实导演稿，也不创建用户确认。旧证据中缺依赖的结果保持历史事实；新结果不反写旧报告。

## 必要小修及结构边界

- 为任务与结果增加自动内部修订号并纳入内容哈希，修复“前段新修订但内容相同，后段可能仍当前”的缺口。旧任务/结果保留，后段必须显式复核并重新绑定；不自动重生成全稿。
- 拒绝重复来源/段 ID、段内来源乱序、对白来源段落与所分段/镜头不符、计划与 base 场景覆盖不符、合成场景与实际 shots 来源覆盖不符。
- 保留既有检查：准确 source revision/hash、缺段、前序失效、任务篡改、carry 字面绑定、选定方案硬冲突、镜号唯一、对白按顺序逐字拼接与说话者、不可覆盖的合成输出。
- 新增和旧负例在同一套件运行。内部 task_revision/result_revision 只在任务 sidecar，不进入既有业务 JSON，不修改业务 schema。

确定性通过仍不证明事实语义、实际连续性、物理可执行、可演时长或艺术收益。carry 非空/相等可能与镜头正文不一致；证据字段非空也不代表比较正确。导出始终 production_blocked，需要原生产门禁和真实确认后才能正式交接。

原 A/B 组合锁、B 验证器与旧证据均保持原字节。此次仅变动 B 的任务工具/技术测试，另加精确技术快照锁与验证入口，显式记录相对旧 B 的两个文件更新，不删除哈希限制。

未跑全回归，旧的 20260518-100605.png 缺失阻塞未解除，未补造 fixture。没有真实分段创作、作者比较、盲评、媒体生成、线上导入或发布。实际入口只读溯源及仍需的最小信息见 ENTRY_TRACE.md。
