"""Compare recorded video results with an existing delivery scope, offline."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path, PurePosixPath
import re
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.core.artifacts import cache_file_digests, project_path
from scripts.core.io import read_json, sha256_file
from scripts.core.media_types import MEDIA_EXTENSIONS
from scripts.core.project_kickoff import load_project_request
from scripts.core.redframe_image_ui import _canvas_id


_FAILURE_STATES = ("failed", "cancelled", "canceled", "rejected", "platform_rejected")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _native_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value))


def _read_receipt(project, path, canvas):
    path = project_path(project, path)
    if not path.is_relative_to(project):
        raise ValueError(f"回执须在准确项目内：{path}")
    receipt = read_json(path)
    if not isinstance(receipt, dict):
        raise ValueError(f"回执不是 JSON 对象：{path}")
    targets = [receipt[key] for key in ("target_url", "canvas_url") if key in receipt]
    if (not targets or any(target != canvas["target_url"] for target in targets)
            or ("target_project_id" in receipt and receipt["target_project_id"] != canvas["target_project_id"])):
        raise ValueError(f"回执与项目登记的目标画布或项目 ID 不符：{path}")
    return receipt, path.relative_to(project).as_posix()


def _delivery_scope(delivery):
    rows = delivery.get("segments", delivery.get("drafts"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("交付回执或冻结交付须有段号与节点映射")
    segments, nodes = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("交付段记录无效")
        segment = row.get("source_segment_id", row.get("segment_id"))
        node = row.get("node_id")
        if not _text(segment) or not _native_id(node) or segment in segments or node in nodes:
            raise ValueError("交付段号和节点必须准确、一一对应")
        segments[segment] = node
        nodes[node] = segment
    return segments, nodes


def _receipt_segments(receipt, nodes):
    rows = receipt.get("segments")
    if isinstance(rows, dict) and rows and all(_text(key) and isinstance(row, dict) for key, row in rows.items()):
        return rows.items()
    if rows is None and _native_id(receipt.get("node_id")):
        return [(receipt.get("segment_id", nodes.get(receipt["node_id"])), receipt)]
    raise ValueError("生成回执须含原任务的节点，或按生成段登记的任务记录")


def _poll_issue(row, receipt, task, project_id):
    initial = row.get("initial_poll")
    query = initial.get("query", {}) if isinstance(initial, dict) else {}
    directory = [f"/app/data/workspaces/{project_id}"]
    initial_matches = (isinstance(query, dict) and query.get("taskId") in (None, [task])
                       and query.get("directory") in (None, directory))
    polls = receipt.get("recent_task_polls", [])
    statuses = []
    for poll in [initial, *(polls if isinstance(polls, list) else [])]:
        if not isinstance(poll, dict):
            continue
        query, response = poll.get("query"), poll.get("response")
        data = response.get("data") if isinstance(response, dict) else None
        if (isinstance(query, dict) and query.get("taskId") == [task]
                and query.get("directory") == directory and isinstance(data, dict)
                and response.get("code") in (200, "200")):
            statuses.append(data.get("status"))
    # A later exact success can resolve a different initial lookup. Conflicting
    # terminal statuses for the same task and workspace remain unresolved.
    if any(status in _FAILURE_STATES for status in statuses):
        return "conflicting_terminal_status"
    if not initial_matches and "succeeded" not in statuses:
        return "result_evidence_conflicts"
    return None


def _recorded_output(row, receipt, node, project_id):
    task = row.get("task_id")
    if not _native_id(task) or task == row.get("previous_task_id"):
        return None, "task_identity_unknown"
    poll_issue = _poll_issue(row, receipt, task, project_id)
    if poll_issue == "conflicting_terminal_status":
        return None, poll_issue
    phase, state = row.get("phase", row.get("status")), row.get("platform_state")
    if not (phase == "download_decoded" and state in (None, "succeeded")
            or state == "succeeded" and phase in (None, "succeeded", "task_observed")):
        return None, "completion_unverified"
    if row.get("decode_passed") is False or poll_issue:
        return None, "result_evidence_conflicts"
    video = row.get("video_output")
    raw_path = video.get("url") if isinstance(video, dict) else None
    if not isinstance(raw_path, str) or not re.fullmatch(r"assets/[\w./-]+", raw_path):
        return None, "output_path_unverified"
    path = PurePosixPath(raw_path)
    if (path.parts[:2] != ("assets", node) or ".." in path.parts or path.as_posix() != raw_path
            or path.suffix.lower() not in MEDIA_EXTENSIONS["video"]):
        return None, "output_node_mismatch"
    expected_sha = row.get("sha256")
    if expected_sha is not None and (not isinstance(expected_sha, str)
                                     or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)):
        return None, "output_hash_unverified"
    return {"task_id": task, "workspace_output": raw_path, "sha256": expected_sha}, None


def _local_file(project, former_root, row, output, file_digest):
    raw = row.get("video_file")
    if not _text(raw):
        return {"status": "path_missing"}
    previous = Path(raw)
    if former_root is not None and previous.is_absolute() and previous.is_relative_to(former_root):
        raw = str(previous.relative_to(former_root))
    path = project_path(project, raw)
    if not path.is_relative_to(project):
        return {"status": "outside_project", "original_path": row["video_file"]}
    item = {"path": path.relative_to(project).as_posix()}
    if path.suffix.lower() not in MEDIA_EXTENSIONS["video"]:
        return {**item, "status": "not_video"}
    if not path.is_file():
        return {**item, "status": "file_missing"}
    if not output["sha256"]:
        return {**item, "status": "hash_missing"}
    try:
        if path.stat().st_size and file_digest(path) == output["sha256"]:
            return {**item, "status": "verified"}
    except OSError:
        return {**item, "status": "unreadable"}
    return {**item, "status": "hash_mismatch"}


def reconcile(project, delivery, receipts=(), *, recorded=(), former_root=None):
    """Report evidence for the selected scope, without mutating any record."""
    project = Path(project).resolve()
    if former_root is not None:
        former_root = Path(former_root)
        if not former_root.is_absolute() or ".." in former_root.parts:
            raise ValueError("旧项目根必须是准确的绝对路径")
    kickoff = load_project_request(project).get("kickoff", {})
    canvas = kickoff.get("canvas") if isinstance(kickoff, dict) else None
    if (not isinstance(canvas, dict) or canvas.get("platform") != "redframe"
            or not _text(canvas.get("target_url")) or not _native_id(canvas.get("target_project_id"))):
        raise ValueError("项目需求须登记准确的红帧画布和项目 ID")
    _canvas_id(canvas["target_url"])
    baseline, delivery_ref = _read_receipt(project, delivery, canvas)
    if baseline.get("target_project_id") != canvas["target_project_id"]:
        raise ValueError("交付回执或冻结交付缺少准确项目 ID")
    segments, nodes = _delivery_scope(baseline)

    file_digest = cache_file_digests(read_digest=sha256_file, check_for_changes=True)
    results, failures, review = defaultdict(list), defaultdict(set), []
    observed = set()
    for path, is_recorded in [(item, False) for item in receipts] + [(item, True) for item in recorded]:
        receipt, ref = _read_receipt(project, path, canvas)
        for segment, row in _receipt_segments(receipt, nodes):
            node = row.get("node_id")
            evidence = {"segment_id": segment, "node_id": node, "source_receipt": ref}
            if not _text(segment) or segment not in segments or segments[segment] != node:
                review.append({**evidence, "reason": "segment_node_mismatch"})
                continue
            observed.add(segment)
            output, issue = _recorded_output(row, receipt, node, canvas["target_project_id"])
            if issue:
                task = row.get("task_id")
                phase = row.get("phase", row.get("status"))
                if (_native_id(task) and task != row.get("previous_task_id")
                        and (phase in _FAILURE_STATES or row.get("platform_state") in _FAILURE_STATES
                             or issue == "conflicting_terminal_status")):
                    failures[(segment, node, task)].add(ref)
                review.append({**evidence, "task_id": task, "reported_phase": phase, "reason": issue})
                continue
            local = _local_file(project, former_root, row, output, file_digest)
            results[output["task_id"]].append(({**evidence, **output, "local_file": local}, is_recorded))

    path_tasks = defaultdict(set)
    for task, references in results.items():
        for item, _ in references:
            path_tasks[item["workspace_output"]].add(task)

    outputs = []
    for task, references in sorted(results.items()):
        claims = {(item["segment_id"], item["node_id"], item["workspace_output"]) for item, _ in references}
        hashes = {item["sha256"] for item, _ in references if item["sha256"]}
        if (len(claims) != 1 or len(hashes) > 1
                or any(len(path_tasks[path]) > 1 for _, _, path in claims)):
            review.append({"task_id": task, "reason": "conflicting_result_identity",
                           "source_receipts": sorted({item["source_receipt"] for item, _ in references})})
            continue
        best = min((item for item, _ in references), key=lambda item: (
            item["local_file"]["status"] != "verified", item["sha256"] is None, item["source_receipt"]))
        if failed := failures.get((best["segment_id"], best["node_id"], task)):
            review.append({"task_id": task, "reason": "conflicting_terminal_status",
                           "source_receipts": sorted(failed | {item["source_receipt"] for item, _ in references})})
            continue
        outputs.append({**best, "registration": "recorded" if any(
            registered and item["sha256"] == best["sha256"] for item, registered in references) else "not_recorded",
                        "source_receipts": sorted({item["source_receipt"] for item, _ in references})})

    review_by_value = {json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in review}
    return {"target_url": canvas["target_url"], "target_project_id": canvas["target_project_id"],
            "delivery": delivery_ref, "segments_without_receipts": sorted(set(segments) - observed),
            "historical_outputs": sorted(outputs, key=lambda item: (item["segment_id"], item["task_id"])),
            "needs_review": [review_by_value[key] for key in sorted(review_by_value)],
            "live_canvas_checked": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--delivery", required=True, type=Path, help="现有交付回执或冻结交付 JSON")
    parser.add_argument("--receipt", default=[], action="append", type=Path, help="已有生成回执，可重复")
    parser.add_argument("--recorded", action="append", default=[], type=Path, help="交付记录已引用的生成回执，可重复")
    parser.add_argument("--former-root", type=Path, help="归档前准确的绝对项目根，仅用于找回本地文件")
    args = parser.parse_args(argv)
    try:
        result = reconcile(args.project, args.delivery, args.receipt,
                           recorded=args.recorded, former_root=args.former_root)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
