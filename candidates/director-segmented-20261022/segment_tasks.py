"""Manual candidate task files, never model calls or production adoption.

Sidecars describe work, not a new business schema. Source and all task/result
versions remain immutable; only this isolated run's manifest pointer changes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
STATE_KEYS = ("position", "action", "gaze", "props", "emotion", "sound")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def state(value):
    check(isinstance(value, dict) and set(value) == set(STATE_KEYS), "six carry state keys required")
    check(all(isinstance(v, str) and v.strip() for v in value.values()), "empty carry state")
    return value


def save(run, manifest):
    # Only isolated sidecar pointers change. Versioned inputs/tasks/results do not.
    tmp = run / "manifest.pending.json"
    write_new(tmp, manifest)
    tmp.replace(run / "manifest.json")


def load(run):
    manifest = read(run / "manifest.json")
    check(manifest["production_blocked"] is True, "not an isolated run")
    for relative, expected in manifest["inputs"].items():
        check(digest(run / relative) == expected, "frozen input changed: " + relative)
    check(digest(manifest["source_path"]) == manifest["source_sha256"], "source bytes changed")
    return manifest, read(run / "inputs/plan.json"), read(manifest["source_path"])


def init(run, source_path, plan_path, base_path):
    check(not run.exists(), "run directory already exists")
    source, plan, base = read(source_path), read(plan_path), read(base_path)
    check(source["project_id"] == base["project_id"], "project ID mismatch")
    check(base["source_narrative"]["logical_id"] == "narrative.package", "wrong source kind")
    check(source["revision"] == plan["source_revision"] == base["source_narrative"]["revision"], "source revision mismatch")
    check(base["source_narrative"]["artifact_id"] == plan["source_artifact_id"], "source artifact ID mismatch")
    check(base["status"] == "candidate" and base["schema_version"] == "4.0", "base must be candidate v4")
    check("human_readable_path" not in base, "new candidate must not adopt a legacy reading file")
    check(plan["director_rules"] == base["director_rules"], "global rules differ from base JSON")
    for field in ("narrative_purpose", "information_emotion_route", "style", "timing_status"):
        check(isinstance(plan[field], str) and plan[field].strip(), "missing global plan: " + field)
    paragraphs = [p["paragraph_id"] for p in source["story_paragraphs"]]
    dialogues = [d["dialogue_id"] for d in source["dialogues"]]
    check(base["source_paragraph_ids"] == paragraphs and base["source_dialogue_ids"] == dialogues, "base source coverage/order differs")
    segments = plan["segments"]
    check(bool(segments), "no segments")
    ids = [s["segment_id"] for s in segments]
    check(len(ids) == len(set(ids)) and all(re.fullmatch(r"SEG-[0-9]{2,}", s) for s in ids), "invalid/duplicate segment IDs")
    check(all(isinstance(s["budget_seconds"], (int, float)) and not isinstance(s["budget_seconds"], bool) and math.isfinite(s["budget_seconds"]) and s["budget_seconds"] > 0 for s in segments), "invalid segment budget")
    total = plan["total_budget_seconds"]
    check(isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total) and sum(s["budget_seconds"] for s in segments) <= total, "segment budgets exceed global budget")
    covered, covered_dialogue, scene_order, last_first = set(), set(), [], -1
    for segment in segments:
        check(bool(segment["source_paragraph_ids"]), "empty segment source")
        check(set(segment["source_paragraph_ids"]) <= set(paragraphs), "unknown paragraph")
        check(set(segment["source_dialogue_ids"]) <= set(dialogues), "unknown dialogue")
        first = min(paragraphs.index(p) for p in segment["source_paragraph_ids"])
        check(first >= last_first, "segments reorder source")
        last_first = first
        covered.update(segment["source_paragraph_ids"])
        covered_dialogue.update(segment["source_dialogue_ids"])
        if not scene_order or scene_order[-1] != segment["scene_id"]:
            scene_order.append(segment["scene_id"])
        state(segment["target_state"])
    check(covered == set(paragraphs) and covered_dialogue == set(dialogues), "plan omits source IDs")
    check(scene_order == [s["scene_id"] for s in base["scenes"]], "scene order differs from base")
    state(plan["initial_state"])
    manifest = {"production_blocked": True, "source_path": str(Path(source_path).resolve()),
                "source_sha256": digest(source_path), "inputs": {}, "tasks": {}, "results": {}, "history": []}
    for name, value in (("plan", plan), ("base", base)):
        relative = "inputs/" + name + ".json"
        write_new(run / relative, value)
        manifest["inputs"][relative] = digest(run / relative)
    write_new(run / "manifest.json", manifest)
    return {"run": str(run), "production_blocked": True, "model_called": False}


def progress(run):
    manifest, plan, source = load(run)
    previous, rows = None, []
    predecessor_valid = True
    for segment in plan["segments"]:
        sid = segment["segment_id"]
        result = manifest["results"].get(sid)
        valid = False
        reason = "missing result"
        if result:
            check(digest(run / result["path"]) == result["sha256"], "immutable result bytes changed")
            value = read(run / result["path"])
            task_ref = manifest["tasks"].get(sid)
            check(task_ref is not None and digest(run / task_ref["path"]) == task_ref["sha256"], "task bytes changed")
            valid = (predecessor_valid and value["task_sha256"] == task_ref["sha256"]
                     and value["predecessor_sha256"] == previous)
            reason = "structurally current" if valid else "stale task or predecessor"
        rows.append({"segment_id": sid, "current": valid, "reason": reason})
        predecessor_valid = valid
        previous = result["sha256"] if valid else None
    return {"production_blocked": True, "segments": rows, "all_current": all(r["current"] for r in rows)}


def task(run, segment_id, reason):
    manifest, plan, source = load(run)
    segments = plan["segments"]
    indexes = [i for i, s in enumerate(segments) if s["segment_id"] == segment_id]
    check(bool(indexes), "unknown segment")
    index = indexes[0]
    predecessor, carry = None, plan["initial_state"]
    if index:
        check(progress(run)["segments"][index - 1]["current"], "predecessor is missing/stale")
        predecessor = manifest["results"][segments[index - 1]["segment_id"]]
        carry = read(run / predecessor["path"])["end_state"]
    check(isinstance(reason, str) and reason.strip(), "task/revision reason required")
    segment = segments[index]
    value = {"production_blocked": True, "segment": segment, "sequence": index,
             "source_path": manifest["source_path"], "source_sha256": manifest["source_sha256"],
             "source_revision": plan["source_revision"], "source_artifact_id": plan["source_artifact_id"],
             "global_plan": {k: v for k, v in plan.items() if k != "segments"},
             "predecessor_sha256": predecessor["sha256"] if predecessor else None,
             "start_state": carry,
             "next_target_state": segments[index + 1]["target_state"] if index + 1 < len(segments) else None,
             "paragraphs": [p for p in source["story_paragraphs"] if p["paragraph_id"] in segment["source_paragraph_ids"]],
             "dialogues": [d for d in source["dialogues"] if d["dialogue_id"] in segment["source_dialogue_ids"]],
             "reason": reason,
             "instruction": "Read full frozen source and WORKFLOW.md. Author only this segment; no changed dialogue/facts. Record few real options only at substantive forks; score is auxiliary. No model is called by this tool."}
    number = len(manifest["history"]) + 1
    path = "tasks/" + segment_id + "-" + str(number) + ".json"
    write_new(run / path, value)
    ref = {"path": path, "sha256": digest(run / path)}
    manifest["tasks"][segment_id] = ref
    manifest["history"].append({"event": "task", "segment_id": segment_id, **ref})
    save(run, manifest)
    return {**ref, "production_blocked": True, "model_called": False}


def record(run, segment_id, result_path):
    manifest, plan, source = load(run)
    task_ref = manifest["tasks"].get(segment_id)
    check(task_ref is not None and digest(run / task_ref["path"]) == task_ref["sha256"], "missing/changed task")
    work = read(run / task_ref["path"])
    value = read(result_path)
    check(value.get("production_blocked") is True, "result sidecar must stay production blocked")
    index = work["sequence"]
    expected_previous = None
    if index:
        check(progress(run)["segments"][index - 1]["current"], "stale predecessor")
        expected_previous = manifest["results"][plan["segments"][index - 1]["segment_id"]]["sha256"]
    check(value["segment_id"] == segment_id and value["source_revision"] == plan["source_revision"], "result ID/revision mismatch")
    check(value["task_sha256"] == task_ref["sha256"] and value["predecessor_sha256"] == expected_previous == work["predecessor_sha256"], "stale task/predecessor binding")
    check(state(value["start_state"]) == work["start_state"], "carry differs from actual predecessor")
    state(value["end_state"])
    check(value["scene_id"] == work["segment"]["scene_id"], "scene mismatch")
    check(isinstance(value["audiovisual_flow"], str) and value["audiovisual_flow"].strip(), "missing flow")
    check(isinstance(value["boundary_review"], str) and value["boundary_review"].strip(), "explicit boundary review required")
    options = value["options"]
    check(isinstance(options, list) and options, "choice record missing")
    check(len({o["id"] for o in options}) == len(options), "duplicate option IDs")
    selected = [o for o in options if o["id"] == value["chosen_option_id"]]
    check(len(selected) == 1 and selected[0]["hard_conflicts"] == [], "chosen option has hard conflicts")
    for option in options:
        check(isinstance(option["hard_conflicts"], list), "hard conflict list required")
        for field in ("evidence", "tradeoffs"):
            check(isinstance(option[field], str) and option[field].strip(), "missing option evidence/tradeoff")
    shots = value["shots"]
    check(isinstance(shots, list) and shots, "missing shots")
    shot_ids = [s["shot_id"] for s in shots]
    check(len(shot_ids) == len(set(shot_ids)), "duplicate shot IDs")
    for shot in shots:
        check(re.fullmatch(re.escape(value["scene_id"]) + r"-[0-9]{3,}", shot["shot_id"]), "shot ID/scene mismatch")
        check(set(shot["source_paragraph_ids"]) <= set(work["segment"]["source_paragraph_ids"]), "shot source outside segment")
        check(set(shot["source_dialogue_ids"]) <= set(work["segment"]["source_dialogue_ids"]), "shot dialogue outside segment")
        check(set(shot["source_dialogue_ids"]) == {d["dialogue_id"] for d in shot["audio"]["dialogue"]}, "shot dialogue references disagree")
        check(set(shot["source_asset_requirement_ids"]) <= set(read(run / "inputs/base.json")["source_asset_requirement_ids"]), "unknown asset reference")
    path = "results/" + segment_id + "-" + str(len(manifest["history"]) + 1) + ".json"
    write_new(run / path, value)
    ref = {"path": path, "sha256": digest(run / path)}
    manifest["results"][segment_id] = ref
    manifest["history"].append({"event": "record", "segment_id": segment_id, **ref})
    save(run, manifest)
    return {**ref, "production_blocked": True, "semantic_continuity_verified": False}


def assemble(run):
    manifest, plan, source = load(run)
    check(progress(run)["all_current"], "missing/stale segments")
    base = deepcopy(read(run / "inputs/base.json"))
    results = [read(run / manifest["results"][s["segment_id"]]["path"]) for s in plan["segments"]]
    all_shots = [shot for result in results for shot in result["shots"]]
    check(len({s["shot_id"] for s in all_shots}) == len(all_shots), "duplicate assembled shot IDs")
    check(set(p for s in all_shots for p in s["source_paragraph_ids"]) == set(p["paragraph_id"] for p in source["story_paragraphs"]), "assembled paragraph coverage incomplete")
    fragments = [d for s in all_shots for d in s["audio"]["dialogue"]]
    order, text = [], {}
    originals = {d["dialogue_id"]: d for d in source["dialogues"]}
    for item in fragments:
        identity = item["dialogue_id"]
        check(identity in originals and item["speaker_id"] == originals[identity]["speaker_id"], "dialogue ID/speaker changed")
        if not order or order[-1] != identity:
            order.append(identity)
        text[identity] = text.get(identity, "") + item["text"]
    check(order == [d["dialogue_id"] for d in source["dialogues"]], "dialogue reordered/repeated/missing")
    check(all(text.get(k) == v["text"] for k, v in originals.items()), "dialogue text changed")
    for scene in base["scenes"]:
        selected = [r for r in results if r["scene_id"] == scene["scene_id"]]
        scene["shots"] = [s for r in selected for s in r["shots"]]
        scene["audiovisual_flow"] = "\n\n".join(r["audiovisual_flow"] for r in selected)
        scene["start_state"] = json.dumps(selected[0]["start_state"], ensure_ascii=False)
        scene["end_state"] = json.dumps(selected[-1]["end_state"], ensure_ascii=False)
    schema_check = "unavailable: jsonschema/referencing not installed"
    from scripts.core.runtime_deps import require_runtime_deps, RuntimeDepsError
    try:
        require_runtime_deps(ROOT)
        import jsonschema  # noqa: F401
        import referencing  # noqa: F401
    except (ImportError, RuntimeDepsError):
        pass
    else:
        from scripts.core.schema_contracts import require_schema
        require_schema(base, "storyboard-package.schema.json", label="isolated assembled candidate")
        schema_check = "passed"
    signature = hashlib.sha256(json.dumps(manifest["results"], sort_keys=True).encode()).hexdigest()[:16]
    output = "exports/" + signature + "-storyboard-package.json"
    write_new(run / output, base)
    receipt = {"production_blocked": True, "schema_check": schema_check,
               "semantic_continuity_verified": False, "artistic_benefit_verified": False,
               "source_sha256": manifest["source_sha256"], "results": manifest["results"],
               "output": output, "sha256": digest(run / output)}
    write_new(run / (output + ".receipt.json"), receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init")
    for name in ("source", "plan", "base"):
        create.add_argument("--" + name, required=True, type=Path)
    build = commands.add_parser("task")
    build.add_argument("--segment", required=True)
    build.add_argument("--reason", required=True)
    ingest = commands.add_parser("record")
    ingest.add_argument("--segment", required=True)
    ingest.add_argument("--result", required=True, type=Path)
    commands.add_parser("status")
    commands.add_parser("assemble")
    args = parser.parse_args(argv)
    run = args.run.resolve()
    try:
        if args.command == "init":
            result = init(run, args.source, args.plan, args.base)
        elif args.command == "task":
            result = task(run, args.segment, args.reason)
        elif args.command == "record":
            result = record(run, args.segment, args.result)
        elif args.command == "status":
            result = progress(run)
        else:
            result = assemble(run)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
