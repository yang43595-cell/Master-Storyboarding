"""Read Codex response usage without exposing conversation bodies or treating counters as bills.

Only token_usage_record and compacted.latest_token_usage_record are accounting
records. token_count is a UI snapshot and must never be summed with them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def measure(logs: list[Path], turn_id: str, *, through_ordinal: int | None = None,
            after_response: str | None = None) -> dict[str, Any]:
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise ValueError("计量须给出非空准确 turn_id。")
    if through_ordinal is not None and (type(through_ordinal) is not int or through_ordinal < 0):
        raise ValueError("through_ordinal 必须是非负整数。")
    records: dict[tuple[str, str], dict[str, Any]] = {}
    unknown: list[str] = []
    scanned = 0
    for path in dict.fromkeys(p.resolve() for p in logs):
        with path.open(encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    if not line.endswith("\n"):
                        unknown.append(f"incomplete_tail:{path.name}:{line_number}")
                        continue
                    raise ValueError(f"损坏日志：{path}:{line_number}")
                scanned += 1
                ordinal = event.get("ordinal")
                payload = event.get("payload") or {}
                kind = event.get("type")
                if kind == "token_usage_record":
                    record = payload
                elif kind == "compacted":
                    record = payload.get("latest_token_usage_record") or {}
                else:
                    continue
                if record.get("turn_id") != turn_id:
                    continue
                if through_ordinal is not None:
                    if type(ordinal) is not int:
                        unknown.append(f"missing_usage_ordinal:{path.name}:{line_number}")
                        continue
                    if ordinal > through_ordinal:
                        continue
                response = record.get("response_id")
                thread = record.get("thread_id")
                if not response or not thread:
                    unknown.append(f"missing_response_identity:{path.name}:{line_number}")
                    continue
                usage = record.get("usage") or {}
                values = {field: usage.get(field) for field in FIELDS}
                for field, value in values.items():
                    if value is not None and (type(value) is not int or value < 0):
                        raise ValueError(f"无效用量：{response}:{field}")
                if values["input_tokens"] is not None and values["cached_input_tokens"] is not None:
                    if values["cached_input_tokens"] > values["input_tokens"]:
                        raise ValueError(f"缓存输入超过输入：{response}")
                required = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
                if (all(values[field] is not None for field in required)
                        and values["total_tokens"] != values["input_tokens"] + values["output_tokens"]):
                    raise ValueError(f"输入输出与总用量冲突：{response}")
                if (values["reasoning_output_tokens"] is not None and values["output_tokens"] is not None
                        and values["reasoning_output_tokens"] > values["output_tokens"]):
                    raise ValueError(f"推理输出超过输出：{response}")
                key = (thread, response)
                if key in records:
                    if records[key]["usage"] != values:
                        raise ValueError(f"同一 response 出现冲突用量：{response}")
                    if kind == "compacted":
                        records[key]["compaction"] = True
                    continue
                records[key] = {"response_id": response, "thread_id": thread,
                    "ordinal": ordinal, "timestamp": event.get("timestamp"),
                    "source": str(path), "line": line_number, "compaction": kind == "compacted",
                    "usage": values}
    rows = sorted(records.values(), key=lambda row: (row["timestamp"] or "", row["ordinal"] or 0))
    if len({row["thread_id"] for row in rows}) > 1:
        raise ValueError("同一 turn 的日志指向多个 thread，拒绝混算。")
    if after_response:
        positions = [i for i, row in enumerate(rows) if row["response_id"] == after_response]
        if not positions:
            raise ValueError("计量起点 response 不在所选 turn/日志/截止范围内。")
        rows = rows[positions[0] + 1:]
    for row in rows:
        if any(row["usage"][field] is None for field in
               ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")):
            unknown.append(f"missing_response_usage:{row['response_id']}")
    totals = {}
    for field in FIELDS:
        values = [row["usage"][field] for row in rows]
        totals[field] = sum(values) if not unknown and values and all(v is not None for v in values) else None
    if not rows:
        unknown.append("no_response_usage_in_selected_scope")
    inputs = totals["input_tokens"]
    cached = totals["cached_input_tokens"]
    totals["uncached_input_tokens"] = inputs - cached if inputs is not None and cached is not None else None
    known_inputs = [row["usage"]["input_tokens"] for row in rows]
    return {"scope": {"turn_id": turn_id, "through_ordinal": through_ordinal,
                     "after_response": after_response, "logs": [str(p.resolve()) for p in logs]},
        "accounting": "unique_response_usage; cache is a subset of input; reasoning is a subset of output",
        "requests": len(rows), "totals": totals,
        "first_input_tokens": known_inputs[0] if known_inputs else None,
        "peak_input_tokens": max(known_inputs) if not unknown and known_inputs and all(v is not None for v in known_inputs) else None,
        "last_response_id": rows[-1]["response_id"] if rows else None,
        "last_timestamp": rows[-1]["timestamp"] if rows else None,
        "unknown": unknown, "cost": None, "cost_note": "无账单或订阅额度换算证据；不推算费用。",
        "scanned_events": scanned, "calls": rows}


def check_budget(report: dict[str, Any], *, max_input: int | None = None,
                 max_total: int | None = None, max_requests: int | None = None) -> dict[str, Any]:
    reasons = []
    for name, limit, actual in (("input", max_input, report["peak_input_tokens"]),
                                ("total", max_total, report["totals"]["total_tokens"]),
                                ("requests", max_requests, report["requests"])):
        if limit is None:
            continue
        if type(limit) is not int or limit <= 0:
            raise ValueError("预算必须大于零。")
        if actual is None or report["unknown"]:
            reasons.append(f"{name}:usage_unknown")
        elif actual >= limit:
            reasons.append(f"{name}:{actual}>={limit}")
    return {"ok": not reasons, "stop_reasons": reasons,
            "limits": {"max_input": max_input, "max_total": max_total, "max_requests": max_requests},
            "enforcement": "checkpoint only; cannot cancel an in-flight host model request"}


def measure_boundaries(boundaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate explicit turn/log boundaries only; never discover host history."""
    if not isinstance(boundaries, list) or not boundaries:
        raise ValueError("边界集合必须是非空数组。")
    records: dict[tuple[str, str], dict[str, Any]] = {}
    unknown: list[str] = []
    scopes, scanned = [], 0
    for boundary in boundaries:
        if not isinstance(boundary, dict) or not isinstance(boundary.get("turn_id"), str) or not boundary["turn_id"] or not isinstance(boundary.get("logs"), list) or not boundary["logs"]:
            raise ValueError("每个边界必须含非空 turn_id 和 logs 数组。")
        report = measure([Path(item) for item in boundary["logs"]], boundary["turn_id"], through_ordinal=boundary.get("through_ordinal"), after_response=boundary.get("after_response"))
        scopes.append(report["scope"]); unknown.extend(report["unknown"]); scanned += report["scanned_events"]
        for row in report["calls"]:
            key = (row["thread_id"], row["response_id"])
            if key in records and records[key]["usage"] != row["usage"]:
                raise ValueError(f"跨边界同一 response 出现冲突用量：{row['response_id']}")
            records[key] = row
    rows = sorted(records.values(), key=lambda row: (row["timestamp"] or "", row["ordinal"] or 0))
    # A missing turn or incomplete log means the observed rows are only a
    # subtotal. Retain them for diagnosis, but do not label them task totals.
    totals = {field: (sum(row["usage"][field] for row in rows) if not unknown and rows and all(row["usage"][field] is not None for row in rows) else None) for field in FIELDS}
    totals["uncached_input_tokens"] = totals["input_tokens"] - totals["cached_input_tokens"] if totals["input_tokens"] is not None and totals["cached_input_tokens"] is not None else None
    known_inputs = [row["usage"]["input_tokens"] for row in rows]
    return {"scope": {"boundaries": scopes}, "accounting": "explicit_boundaries; unique(thread_id,response_id); no host-history discovery; requests count observed records only", "requests": len(rows), "totals": totals, "first_input_tokens": known_inputs[0] if known_inputs else None, "peak_input_tokens": max(known_inputs) if not unknown and known_inputs and all(item is not None for item in known_inputs) else None, "last_response_id": rows[-1]["response_id"] if rows else None, "last_timestamp": rows[-1]["timestamp"] if rows else None, "unknown": unknown or ([] if rows else ["no_response_usage_in_selected_scope"]), "cost": None, "cost_note": "无账单或订阅额度换算证据；不推算费用。", "scanned_events": scanned, "calls": rows}


def checkpoint(args: argparse.Namespace) -> dict[str, Any] | None:
    """Reuse the stage budget gate for direct reading and canvas checkpoints."""
    try:
        from .core.candidate_test import context as candidate_context
    except ImportError:
        from core.candidate_test import context as candidate_context
    project_arg = getattr(args, 'project', None)
    project = Path(project_arg).expanduser().resolve() if project_arg else None
    if project is not None and not project.is_dir():
        raise ValueError(f'计量项目目录不存在：{project}')
    testing = candidate_context(project) if project is not None else None
    contract = testing.get('usage_contract') if testing else None
    if contract:
        if contract['turn_id'] is None:
            raise ValueError('受控试跑要求先计量：准确 turn 尚未知，保留要求并补合法用量证据，不得继续准备。')
        if getattr(args, 'usage_boundaries_json', None) or getattr(args, 'usage_turn_id', None) != contract['turn_id']:
            raise ValueError('受控试跑计量须使用已登记准确 turn；不能省略或改用另一边界。')
        for key, limit in contract['limits'].items():
            provided = getattr(args, key, None)
            if provided is not None and provided > limit:
                raise ValueError('不能通过命令行提高已登记计量预算。')
    logs = getattr(args, "usage_log", None)
    turn = getattr(args, "usage_turn_id", None)
    boundaries = getattr(args, "usage_boundaries_json", None)
    limits = {key: getattr(args, key, None) for key in ("max_input", "max_total", "max_requests")}
    if contract:
        limits = {key: contract['limits'][key] if value is None else value for key, value in limits.items()}
    if not logs and not turn and not boundaries and not any(value is not None for value in limits.values()):
        return None
    if not any(value is not None for value in limits.values()):
        raise ValueError("计量门禁须至少给出一个预算。")
    if boundaries:
        if logs or turn:
            raise ValueError("--usage-boundaries-json 不能与单轮用量参数同用。")
        report = measure_boundaries(json.loads(Path(boundaries).read_text(encoding="utf-8-sig")))
    elif logs and turn:
        report = measure([Path(p) for p in logs], turn)
    else:
        raise ValueError("计量门禁须给出单轮日志+turn，或显式边界集合。")
    budget = check_budget(report, **limits)
    if not budget["ok"]:
        summary = {key: report[key] for key in ('peak_input_tokens', 'totals', 'requests', 'unknown')}
        raise ValueError(f"执行预算停止：{'; '.join(budget['stop_reasons'])}；"
                         f"{json.dumps(summary, ensure_ascii=False)}；保留当前产物与未完成位置。")
    return {**{key: value for key, value in report.items() if key != "calls"}, "budget": budget}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", action="append")
    parser.add_argument("--turn-id")
    parser.add_argument("--boundaries-json", help="显式边界 JSON：[{turn_id,logs,through_ordinal?,after_response?}]")
    parser.add_argument("--through-ordinal", type=int)
    parser.add_argument("--after-response")
    parser.add_argument("--max-input", type=int)
    parser.add_argument("--max-total", type=int)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--output", help="完整明细 JSON；终端始终只返回摘要")
    args = parser.parse_args(argv)
    source_paths: set[Path] = set()
    if args.boundaries_json:
        if args.log or args.turn_id or args.through_ordinal is not None or args.after_response:
            raise ValueError("--boundaries-json 不能与 --log/--turn-id 同用。")
        boundary_path = Path(args.boundaries_json).resolve()
        report = measure_boundaries(json.loads(boundary_path.read_text(encoding="utf-8")))
        source_paths.add(boundary_path)
    else:
        if not args.log or not args.turn_id:
            raise ValueError("单轮计量必须同时给出 --log 与 --turn-id。")
        report = measure([Path(p) for p in args.log], args.turn_id,
                         through_ordinal=args.through_ordinal, after_response=args.after_response)
    for scope in report["scope"].get("boundaries", [report["scope"]]):
        source_paths.update(Path(p).resolve() for p in scope["logs"])
    report["budget"] = check_budget(report, max_input=args.max_input,
                                    max_total=args.max_total, max_requests=args.max_requests)
    if args.output:
        path = Path(args.output).resolve()
        if path in source_paths:
            raise ValueError("报告不得覆盖原始日志或边界定义。")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}, ensure_ascii=False, indent=2))
    return 0 if report["budget"]["ok"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        raise SystemExit(f"ERROR: {exc}")
