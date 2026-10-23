"""对桌面项目的 production_plan.json 做语义预检（CLI 入口）。

**已退役**：只在需要预检旧项目的 `production_plan.json` 时使用；现行视频阶段不生成该文件
（见 `voiceover-to-video-script` 的交付合同）。口径与 `production_plan.py` 顶部标注一致。

用法：
    python scripts/validate-production-plan.py --project-path <桌面项目目录> --mode enforce-hard
    python scripts/validate-production-plan.py --project-path <桌面项目目录> --mode shadow

`enforce-hard`：读取 `00_项目管理/production_plan.json`，预检后把报告写入
`00_项目管理/production_preflight.json`；任何 ERROR 使退出码为 1（禁止提交）。
`shadow`：只输出报告，不写入任何文件；项目没有 production_plan.json 时输出迁移建议。

预检规则见 `scripts/core/production_plan.py`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 统一 CLI 输出为 UTF-8：避免 Windows GBK 控制台下中文乱码（与 validate-seedance-* 一致）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from .core.io import read_json, write_json
    from .core.production_plan import validate_production_plan
except ImportError:  # 直接执行时 scripts/ 在 sys.path 上，回退到核心导入
    from core.io import read_json, write_json
    from core.production_plan import validate_production_plan

PROJECT_MANAGEMENT_DIR = "00_项目管理"
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="production_plan.json 语义预检")
    parser.add_argument("--project-path", required=True, help="桌面项目目录绝对路径")
    parser.add_argument(
        "--mode",
        choices=["enforce-hard", "shadow"],
        default="shadow",
        help="enforce-hard 写入预检报告并拒绝 ERROR；shadow 只读不写（默认）",
    )
    return parser.parse_args()


def _print_report(report: object) -> None:
    for issue in report["errors"]:
        print(f"[ERROR] {issue['code']}: {issue['message']}")
        if issue["detail"]:
            print(f"        {issue['detail']}")
    for issue in report["warnings"]:
        print(f"[WARN]  {issue['code']}: {issue['message']}")
        if issue["detail"]:
            print(f"        {issue['detail']}")


def main() -> int:
    args = _parse_args()
    project_root = Path(args.project_path).resolve()
    management_dir = project_root / PROJECT_MANAGEMENT_DIR
    plan_path = management_dir / "production_plan.json"

    if not plan_path.exists():
        if args.mode == "enforce-hard":
            print(f"ERROR: 未找到 {plan_path}，无法执行语义预检。")
            return 1
        print(f"[shadow] 未找到 {plan_path}。")
        print("        旧项目没有 production_plan.json —— 建议建立 3.3 计划：")
        print("          1. 引用 confirmed 导演包与其 revision，同时引用 confirmed 叙事包。")
        print("          2. 用 segment_assignment 按导演包顺序完整分配镜头，不复制镜头合同。")
        print("          3. 登记最终对白、自然语速估时、音频策略与用户授权状态。")
        print("        迁移后运行本脚本 --mode enforce-hard 验证，再进入即梦 CLI。")
        return 0

    try:
        plan = read_json(plan_path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: production_plan.json 无法解析：{exc}")
        return 1

    if args.mode == "enforce-hard" and str(plan.get("schema_version") or "") in {"1.0", "3.1", "3.2"}:
        print(f"ERROR: production_plan {plan.get('schema_version')} 仅供旧项目只读检查；新建、修订或提交必须迁移到 3.3。")
        return 1

    report = validate_production_plan(plan, project_root, mode=args.mode).to_dict()

    if args.mode == "enforce-hard":
        preflight_path = management_dir / "production_preflight.json"
        write_json(preflight_path, report)
        print(f"preflight 报告已写入 {preflight_path}")

    _print_report(report)

    if not report["ok"]:
        print(f"FAIL: {len(report['errors'])} 个硬错误，禁止提交 CLI。")
        return 1
    if report["warnings"]:
        print(f"PASS(有警告): {len(report['warnings'])} 个警告，由用户或主 Agent 决定是否继续。")
    else:
        print("PASS: 语义预检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
