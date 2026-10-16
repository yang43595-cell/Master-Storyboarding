#!/usr/bin/env bash
# 可开关的知识审计钩子（默认关闭）：每个【新 AI 剧情任务开始前】跑一次审计。
#
# 用法（由 .claude/settings.local.json 的 PreToolUse 钩子调用，传入本机 vault 路径）：
#   bash scripts/knowledge-audit-hook.sh "<vault路径>"
#
# 开关：
#   创建 .claude/audit_hook_on  -> 开启（每个新任务开始前审计一次）
#   删除 .claude/audit_hook_on  -> 关闭（触发后直接什么都不做）
#
# 触发时机：本项目的"新任务" = 用 stage_context_cli.py prepare 起一个阶段/任务。
# 钩子会读 Bash 调用输入，只有命令里含 stage_context_cli + prepare（= 任务开始）
# 才审计；其余 Bash 命令一律跳过，避免每条命令都跑。
set -u

VAULT="${1:-}"
if [ -z "$VAULT" ]; then
  echo "【知识审计】缺少 vault 路径参数，跳过。" >&2
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
TOGGLE="$ROOT/.claude/audit_hook_on"

# 默认关：没有开关标记就立刻返回，零成本、零噪音
if [ ! -f "$TOGGLE" ]; then
  exit 0
fi

# 只对"新任务开始"的 prepare 命令生效；其余 Bash 命令立刻跳过
INPUT="$(cat 2>/dev/null || true)"
case "$INPUT" in
  *stage_context_cli*prepare*) ;;   # 命中 stage_context_cli.py prepare = 新任务开始
  *) exit 0 ;;
esac

export PYTHONUTF8=1
OUT="$ROOT/.claude/audit_hook_last.json"

# 跑一次审计，只输出简短结果，不倾倒整份 JSON
if python "$ROOT/scripts/audit-knowledge-entrypoints.py" --vault "$VAULT" --output "$OUT" >/dev/null 2>/dev/null; then
  echo "【知识审计】OK，0 错误"
  exit 0
fi

python - "$OUT" <<'PY'
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception as exc:
    print("【知识审计】无法读取结果：", exc)
    sys.exit(0)
errors = data.get('errors', [])
print(f"【知识审计】发现 {len(errors)} 个问题：")
for item in errors[:10]:
    print("  -", item)
if len(errors) > 10:
    print(f"  …共 {len(errors)} 个")
PY
exit 0
