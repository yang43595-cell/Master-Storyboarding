from pathlib import Path

import pytest

from scripts.core.io import sha256_file, write_json
from scripts.reconcile_video_delivery import main, reconcile


CANVAS = "https://redframe-ai.xmpaohong.com:28888/#/workspace?canvasId=canvas-A"
PROJECT_ID = "workspace-A"
DELIVERY = Path("06_视频/现有交付回执.json")
RECEIPT = Path("06_视频/生成回执.json")
VIDEO = Path("06_视频/成片.mp4")


@pytest.fixture
def project(tmp_path):
    write_json(tmp_path / "00_项目管理/project_request.json", {
        "kickoff": {"canvas": {"platform": "redframe", "target_url": CANVAS, "target_project_id": PROJECT_ID}},
    })
    write_json(tmp_path / DELIVERY, {
        "target_url": CANVAS,
        "target_project_id": PROJECT_ID,
        "segments": [{"segment_id": "V01", "node_id": "node-1", "outputs": {}},
                     {"segment_id": "V02", "node_id": "node-2", "outputs": {}}],
    })
    (tmp_path / VIDEO).write_bytes(b"local output bytes")
    return tmp_path


def result_row(project, **changes):
    row = {
        "node_id": "node-2", "task_id": "task-2", "phase": "download_decoded",
        "video_output": {"url": "assets/node-2/2026-Q4/movie-2.mp4"},
        "video_file": str(VIDEO), "sha256": sha256_file(project / VIDEO),
    }
    return {**row, **changes}


def save_receipt(project, row, *, segment="V02", path=RECEIPT, **extra):
    write_json(project / path, {"canvas_url": CANVAS, "segments": {segment: row}, **extra})
    return path


def test_finds_unregistered_output_and_repeat_does_not_duplicate(project):
    ref = save_receipt(project, result_row(project))
    before = {item: item.read_bytes() for item in project.rglob("*") if item.is_file()}

    first = reconcile(project, DELIVERY, [ref])
    assert reconcile(project, DELIVERY, [ref, ref]) == first
    output, = first["historical_outputs"]
    assert output["registration"] == "not_recorded"
    assert output["local_file"] == {"path": VIDEO.as_posix(), "status": "verified"}
    assert output["task_id"] == "task-2"
    assert first["segments_without_receipts"] == ["V01"]
    assert first["needs_review"] == []
    assert first["live_canvas_checked"] is False
    logged = reconcile(project, DELIVERY, [ref], recorded=[ref])
    logged_output, = logged["historical_outputs"]
    assert logged_output["registration"] == "recorded"
    assert {item: item.read_bytes() for item in before} == before


def test_uses_actual_bound_nodes_from_frozen_deliverable(project):
    write_json(project / DELIVERY, {
        "target_url": CANVAS, "target_project_id": PROJECT_ID,
        "drafts": [{"source_segment_id": "V02", "node_id": "node-2"}],
    })
    ref = save_receipt(project, result_row(project))
    assert main(["--project", str(project), "--delivery", str(DELIVERY), "--receipt", str(ref)]) == 0


def test_no_result_receipts_means_unknown_for_all_selected_segments(project):
    result = reconcile(project, DELIVERY)
    assert result["segments_without_receipts"] == ["V01", "V02"]
    assert result["historical_outputs"] == []


@pytest.mark.parametrize("row_change,reason", [
    ({"task_id": None}, "task_identity_unknown"),
    ({"previous_task_id": "task-2"}, "task_identity_unknown"),
    ({"phase": "platform_rejected"}, "completion_unverified"),
    ({"phase": "submission_unknown", "platform_state": "succeeded"}, "completion_unverified"),
    ({"platform_state": "running"}, "completion_unverified"),
    ({"node_id": "node-1"}, "segment_node_mismatch"),
    ({"video_output": {"url": "assets/old-node/2026-Q4/movie-2.mp4"}}, "output_node_mismatch"),
    ({"video_output": {"url": "assets/node-2/../other/result.mp4"}}, "output_node_mismatch"),
    ({"decode_passed": False}, "result_evidence_conflicts"),
])
def test_wrong_or_inconclusive_evidence_never_confirms_an_output(project, row_change, reason):
    ref = save_receipt(project, result_row(project, **row_change))
    result = reconcile(project, DELIVERY, [ref])
    assert result["historical_outputs"] == []
    issue, = result["needs_review"]
    assert issue["reason"] == reason


def test_single_unknown_submission_uses_scope_node_without_inferring_it_completed(project):
    write_json(project / RECEIPT, {
        "canvas_url": CANVAS, "node_id": "node-1", "status": "submission_unknown",
    })
    result = reconcile(project, DELIVERY, [RECEIPT])
    assert result["historical_outputs"] == []
    issue, = result["needs_review"]
    assert issue["segment_id"] == "V01"
    assert issue["reported_phase"] == "submission_unknown"


@pytest.mark.parametrize("field,wrong_value", [
    ("canvas_url", CANVAS.replace("canvas-A", "canvas-B")),
    ("target_project_id", "workspace-B"),
])
def test_refuses_mismatched_project_or_canvas_even_with_same_segment_and_node(project, field, wrong_value):
    ref = save_receipt(project, result_row(project), **{field: wrong_value})
    with pytest.raises(ValueError, match="目标画布或项目 ID 不符"):
        reconcile(project, DELIVERY, [ref])


def test_later_terminal_poll_can_resolve_an_initial_lookup_of_the_wrong_task(project):
    directory = ["/app/data/workspaces/" + PROJECT_ID]
    row = result_row(project, initial_poll={"query": {"taskId": ["task-other"], "directory": directory}})
    ref = save_receipt(project, row)
    assert reconcile(project, DELIVERY, [ref])["needs_review"][0]["reason"] == "result_evidence_conflicts"

    exact = {"query": {"taskId": ["task-2"], "directory": directory},
             "response": {"code": "200", "data": {"status": "succeeded"}}}
    wrong_project = {**exact, "query": {**exact["query"], "directory": ["/app/data/workspaces/other"]}}
    save_receipt(project, row, recent_task_polls=[wrong_project])
    assert reconcile(project, DELIVERY, [ref])["historical_outputs"] == []
    save_receipt(project, row, recent_task_polls=[None, wrong_project, exact])
    assert reconcile(project, DELIVERY, [ref])["historical_outputs"][0]["task_id"] == "task-2"


@pytest.mark.parametrize("failure", ["failed", "cancelled"])
def test_same_task_failure_poll_conflicts_with_claimed_success(project, failure):
    query = {"taskId": ["task-2"], "directory": ["/app/data/workspaces/" + PROJECT_ID]}
    failed_poll = {"query": query, "response": {"code": 200, "data": {"status": failure}}}
    running_poll = {"query": query, "response": {"code": 200, "data": {"status": "running"}}}
    other_task = {**failed_poll, "query": {**query, "taskId": ["different-task"]}}
    malformed = {"query": query, "response": None}

    ref = save_receipt(project, result_row(project, initial_poll=running_poll),
                       recent_task_polls=[other_task, malformed])
    assert len(reconcile(project, DELIVERY, [ref])["historical_outputs"]) == 1
    save_receipt(project, result_row(project, initial_poll=failed_poll))
    result = reconcile(project, DELIVERY, [ref])
    assert result["historical_outputs"] == []
    assert result["needs_review"][0]["reason"] == "conflicting_terminal_status"


def test_same_task_failure_from_another_receipt_conflicts_but_old_or_unknown_do_not(project):
    completed = save_receipt(project, result_row(project))
    failed = save_receipt(project, result_row(project, task_id="old-task", phase="cancelled"),
                          path=RECEIPT.parent / "旧失败.json")
    unfinished = save_receipt(project, result_row(project, phase="submission_unknown"),
                              path=RECEIPT.parent / "结果未知.json")
    assert len(reconcile(project, DELIVERY, [completed, failed, unfinished])["historical_outputs"]) == 1
    save_receipt(project, result_row(project, phase="failed"), path=failed)
    result = reconcile(project, DELIVERY, [completed, failed, unfinished])
    assert result["historical_outputs"] == []
    conflict, = [item for item in result["needs_review"] if item["reason"] == "conflicting_terminal_status"]
    assert conflict["task_id"] == "task-2"
    assert set(conflict["source_receipts"]) == {completed.as_posix(), failed.as_posix()}


def test_preserves_versions_without_using_old_task_as_new_result(project):
    ref1 = save_receipt(project, result_row(project))
    ref2 = save_receipt(project, result_row(
        project, task_id="new-task", previous_task_id="task-2",
        video_output={"url": "assets/node-2/2026-Q4/new-movie.mp4"},
    ), path=RECEIPT.parent / "修订生成回执.json")
    outputs = reconcile(project, DELIVERY, [ref1, ref2], recorded=[ref1])["historical_outputs"]
    assert {item["task_id"]: item["registration"] for item in outputs} == {
        "task-2": "recorded", "new-task": "not_recorded",
    }


def test_different_task_on_same_result_path_is_kept_unresolved(project):
    ref1 = save_receipt(project, result_row(project))
    ref2 = save_receipt(project, result_row(project, task_id="new-task"), path=RECEIPT.parent / "错配回执.json")
    result = reconcile(project, DELIVERY, [ref1, ref2])
    assert not result["historical_outputs"]
    assert {item["reason"] for item in result["needs_review"]} == {"conflicting_result_identity"}


def test_same_task_on_two_nodes_is_kept_unresolved(project):
    ref1 = save_receipt(project, result_row(project))
    ref2 = save_receipt(project, result_row(
        project, node_id="node-1", video_output={"url": "assets/node-1/2026-Q4/movie-1.mp4"},
    ), segment="V01", path=RECEIPT.parent / "错配段号.json")
    result = reconcile(project, DELIVERY, [ref1, ref2])
    assert not result["historical_outputs"]
    assert result["needs_review"][0]["reason"] == "conflicting_result_identity"


def test_archived_path_requires_exact_former_root_and_same_output_bytes(project):
    old = project.parent / "formerly-here"
    ref = save_receipt(project, result_row(project, video_file=str(old / VIDEO)))
    outside, = reconcile(project, DELIVERY, [ref])["historical_outputs"]
    assert outside["local_file"]["status"] == "outside_project"

    restored, = reconcile(project, DELIVERY, [ref], former_root=old)["historical_outputs"]
    assert restored["local_file"]["status"] == "verified"
    (project / VIDEO).write_bytes(b"replacement bytes")
    changed, = reconcile(project, DELIVERY, [ref], former_root=old)["historical_outputs"]
    assert changed["registration"] == "not_recorded"
    assert changed["local_file"]["status"] == "hash_mismatch"
    (project / VIDEO).unlink()
    missing, = reconcile(project, DELIVERY, [ref], former_root=old)["historical_outputs"]
    assert missing["local_file"]["status"] == "file_missing"
