"""Synthetic declarations only; no real model capability or project approval."""
import copy
import hashlib
import json

import pytest

from scripts.core.generation_boundaries import validate_generation_boundaries, main
from scripts.core.quality_evidence import validate_scene_source_mapping


def fixture(tmp_path, maximum=18):
    evidence = tmp_path / "synthetic-capability.txt"
    evidence.write_text("Synthetic test observation, not a real platform capability.")
    proof = dict(path=evidence.name, sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                 locator="synthetic fixture", checked_at="2026-01-01T00:00:00Z")
    board = {"scenes": [{"scene_id": "ROOM", "shots": [
        {"shot_id": f"S{i}", "cut": {"exit": f"Synthetic boundary {i}"}} for i in range(1, 4)]}]}
    rows = [dict(segment_id=f"G{i}", prompt=f"Synthetic prompt {i}", source_shot_ids=[f"S{i}"],
                 duration_seconds=9, materials=[]) for i in range(1, 4)]
    for index, row in enumerate(rows[1:], 1):
        state, sha = f"STATE{index}", str(index) * 64
        ref = dict(sha256=sha, state_id=state, role="state_reference")
        rows[index - 1]["materials"].append(copy.deepcopy(ref))
        row["materials"].append(copy.deepcopy(ref))
        row["same_scene_split"] = dict(from_segment_id=f"G{index}", reason="model_duration_exceeded",
            evidence="Synthetic content exceeds this test model; use the director's existing boundary.",
            estimated_scene_duration_seconds=27, timing_basis="Synthetic natural dialogue/action estimate.",
            source_boundary=dict(shot_id=f"S{index}", field="cut.exit", quote=f"Synthetic boundary {index}"))
        row["continuity"] = dict(previous_segment_id=f"G{index}", boundary=dict(state_id=state,
            description="Synthetic same actor and prop state, next action has not happened.",
            previous_sha256s=[sha], current_sha256s=[sha]))
    payload = dict(generation_segments=rows, source_storyboard=dict(path="board.json"),
        platform=dict(model="synthetic-model", reference_mode="synthetic-multi-reference"),
        selected_video_model=dict(id="synthetic-model", platform="synthetic-platform", entry_point="synthetic-ui",
            reference_mode="synthetic-multi-reference", confirmed_by_user=True,
            max_duration_seconds=maximum, capability_evidence=proof))
    return payload, board


def test_same_scene_without_reason_is_rejected_even_with_three_short_clips(tmp_path):
    payload, board = fixture(tmp_path)
    for row in payload["generation_segments"]:
        row.pop("same_scene_split", None)
    with pytest.raises(ValueError, match="同场景默认合并"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_merged_scene_has_no_fixed_minimum_maximum_or_model_guess(tmp_path):
    payload, board = fixture(tmp_path)
    payload = {"generation_segments": [dict(segment_id="ONE", prompt="Complete continuous scene.",
                duration_seconds=27, source_shot_ids=["S1", "S2", "S3"])]}
    before = copy.deepcopy(payload)
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 0
    assert payload == before and "selected_video_model" not in payload


def test_verified_limit_and_shared_state_inputs_pass_without_one_image_per_shot(tmp_path):
    payload, board = fixture(tmp_path)
    before = copy.deepcopy((payload, board))
    assert validate_generation_boundaries(payload, board, tmp_path)["same_scene_boundaries_checked"] == 2
    assert (payload, board) == before


def test_correct_endpoint_roles_pass_but_reversed_roles_do_not(tmp_path):
    payload, board = fixture(tmp_path)
    rows = payload["generation_segments"]
    for index in range(1, 3):
        state = f"STATE{index}"
        for row, role in ((rows[index - 1], "end_frame"), (rows[index], "start_frame")):
            next(r for r in row["materials"] if r["state_id"] == state)["role"] = role
    validate_generation_boundaries(payload, board, tmp_path)
    rows[0]["materials"][0]["role"] = "start_frame"
    with pytest.raises(ValueError, match="切点状态参考"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_same_shot_continuous_subranges_can_use_exact_complete_sentence(tmp_path):
    payload, board = fixture(tmp_path)
    payload["generation_segments"] = payload["generation_segments"][:2]
    payload["generation_segments"][1]["source_shot_ids"] = ["S1"]
    board["scenes"][0]["shots"][0]["audio"] = {"dialogue": [{"text": "First complete sentence."}]}
    payload["generation_segments"][1]["same_scene_split"]["source_boundary"] = dict(
        shot_id="S1", field="audio.dialogue.0.text", quote="First complete sentence.")
    validate_generation_boundaries(payload, board, tmp_path)


def test_composite_source_snapshot_keeps_existing_single_artifact_route(tmp_path):
    payload, board = fixture(tmp_path)
    payload.pop("source_storyboard")
    payload["scenes"] = board["scenes"]
    for row in payload["generation_segments"]:
        row.pop("prompt")  # Source mapping snapshot, not the formal manuscript.
    rows = [dict(node_id="node-" + s["segment_id"], source_segment_id=s["segment_id"],
                 source_shot_ids=s["source_shot_ids"], reference_checks=copy.deepcopy(s["materials"]),
                 continuity=dict(event_id="synthetic-continuous-event", **(
                     {"previous_node_id": "node-" + payload["generation_segments"][index - 1]["segment_id"]}
                     if index else {}))) for index, s in enumerate(payload["generation_segments"])]
    validate_scene_source_mapping(rows, [({}, tmp_path / "composite.json", payload)], tmp_path)


@pytest.mark.parametrize("side", ["source", "delivery"])
def test_cutpoint_reference_cannot_be_scoped_away_from_boundary_shot(tmp_path, side):
    payload, sources, rows = delivery_fixture(tmp_path)
    payload["generation_segments"] = payload["generation_segments"][:2]
    first, second = payload["generation_segments"]
    first["source_shot_ids"] = ["S1", "S2"]
    second["source_shot_ids"] = ["S3"]
    second["same_scene_split"]["source_boundary"] = dict(shot_id="S2", field="cut.exit", quote="Synthetic boundary 2")
    rows = rows[:2]
    rows[0]["source_shot_ids"], rows[1]["source_shot_ids"] = ["S1", "S2"], ["S3"]
    validate_scene_source_mapping(rows, sources, tmp_path)
    target = first["materials"][0] if side == "source" else rows[0]["reference_checks"][0]
    target["source_shot_ids"] = ["S1"]  # Actual exit is S2, not the earlier S1.
    with pytest.raises(ValueError, match="切点"):
        validate_scene_source_mapping(rows, sources, tmp_path)


@pytest.mark.parametrize("mutation", ["unknown_model", "changed_mode", "stale_evidence", "missing_evidence",
    "not_exceeded", "negative", "nan", "bool", "oversized_clip", "bad_boundary", "missing_mapping", "wrong_order"])
def test_no_assumed_capability_or_source_boundary(tmp_path, mutation):
    payload, board = fixture(tmp_path)
    model = payload["selected_video_model"]
    rows = payload["generation_segments"]
    if mutation == "unknown_model": payload.pop("selected_video_model")
    elif mutation == "changed_mode": payload["platform"]["reference_mode"] = "other"
    elif mutation == "stale_evidence": (tmp_path / model["capability_evidence"]["path"]).write_text("changed")
    elif mutation == "missing_evidence": model.pop("capability_evidence")
    elif mutation == "not_exceeded": model["max_duration_seconds"] = 60
    elif mutation == "negative": model["max_duration_seconds"] = -1
    elif mutation == "nan": model["max_duration_seconds"] = float("nan")
    elif mutation == "bool": model["max_duration_seconds"] = True
    elif mutation == "oversized_clip": rows[0]["duration_seconds"] = 19
    elif mutation == "bad_boundary": rows[1]["same_scene_split"]["source_boundary"]["quote"] = "Invented event"
    elif mutation == "missing_mapping": rows[1].pop("source_shot_ids")
    elif mutation == "wrong_order": rows.reverse()
    with pytest.raises(ValueError):
        validate_generation_boundaries(payload, board, tmp_path)


@pytest.mark.parametrize("mutation", ["environment_only", "wrong_state", "different_image", "one_side_missing"])
def test_reused_room_picture_is_not_cutpoint_evidence(tmp_path, mutation):
    payload, board = fixture(tmp_path)
    ref = payload["generation_segments"][0]["materials"][0]
    if mutation == "environment_only": ref["role"] = "layout"
    elif mutation == "wrong_state": ref["state_id"] = "BASE-ROOM"
    elif mutation == "different_image": ref["sha256"] = "f" * 64
    elif mutation == "one_side_missing": payload["generation_segments"][1]["materials"] = []
    with pytest.raises(ValueError, match="切点状态参考"):
        validate_generation_boundaries(payload, board, tmp_path)


def test_true_source_event_boundary_needs_no_model_limit_or_cutpoint_image(tmp_path):
    payload, board = fixture(tmp_path)
    payload.pop("selected_video_model")
    for row in payload["generation_segments"][1:]:
        row["same_scene_split"]["reason"] = "event_complete"
        row.pop("continuity")
        row["materials"] = []
    assert validate_generation_boundaries(payload, board, tmp_path)["segment_count"] == 3


def test_execution_limit_requires_actual_evidence_not_vague_safer_claim(tmp_path):
    payload, board = fixture(tmp_path)
    for row in payload["generation_segments"][1:]:
        row["same_scene_split"]["reason"] = "execution_limit"
    with pytest.raises(ValueError, match="限制核验"):
        validate_generation_boundaries(payload, board, tmp_path)


def delivery_fixture(tmp_path):
    payload, board = fixture(tmp_path)
    sources = [(dict(artifact_id="P"), tmp_path / "prompt.json", payload),
               (dict(artifact_id="B"), tmp_path / "board.json", board)]
    rows = [dict(node_id="node-" + s["segment_id"], source_segment_id=s["segment_id"],
                 source_shot_ids=s["source_shot_ids"], reference_checks=copy.deepcopy(s["materials"]),
                 continuity=dict(event_id="synthetic-continuous-event", **(
                     {"previous_node_id": "node-" + payload["generation_segments"][index - 1]["segment_id"]}
                     if index else {}))) for index, s in enumerate(payload["generation_segments"])]
    return payload, sources, rows


@pytest.mark.parametrize("mutation", ["invent_new_event", "wrong_previous_node", "reverse_order"])
def test_delivery_cannot_relabel_source_continuation_to_skip_native_boundary_gate(tmp_path, mutation):
    payload, sources, rows = delivery_fixture(tmp_path)
    if mutation == "invent_new_event": rows[1]["continuity"] = {"event_id": "unrelated-event"}
    elif mutation == "wrong_previous_node": rows[1]["continuity"]["previous_node_id"] = "OTHER"
    else: rows.reverse()
    with pytest.raises(ValueError, match="实际交付前驱"):
        validate_scene_source_mapping(rows, sources, tmp_path)


def test_partial_delivery_does_not_require_reuploading_omitted_predecessor(tmp_path):
    payload, sources, rows = delivery_fixture(tmp_path)
    validate_scene_source_mapping([rows[1]], sources, tmp_path)


@pytest.mark.parametrize("mutation", [None, "remove_reason", "omit_director", "wrong_source", "omit_actual_cutpoint"])
def test_canvas_source_gate_cannot_skip_boundary_or_actual_inputs(tmp_path, mutation):
    payload, sources, rows = delivery_fixture(tmp_path)
    if mutation == "remove_reason": payload["generation_segments"][1].pop("same_scene_split")
    elif mutation == "omit_director": payload.pop("source_storyboard")
    elif mutation == "wrong_source": rows[1]["source_segment_id"] = "OTHER"
    elif mutation == "omit_actual_cutpoint": rows[1]["reference_checks"] = []
    if mutation is None:
        validate_scene_source_mapping(rows, sources, tmp_path)
    else:
        with pytest.raises(ValueError):
            validate_scene_source_mapping(rows, sources, tmp_path)


def test_command_rejects_historical_ad_hoc_format_without_rewriting_files(tmp_path, capsys):
    prompt, board = tmp_path / "prompt.json", tmp_path / "board.json"
    prompt.write_text(json.dumps({"video_segments": [{"video_id": "V01", "duration_seconds": 11}]}))
    board.write_text(json.dumps(fixture(tmp_path)[1]))
    before = prompt.read_bytes()
    assert main(["--project", str(tmp_path), "--prompt", str(prompt), "--storyboard", str(board)]) == 1
    assert "历史专用分段格式" in capsys.readouterr().out
    assert prompt.read_bytes() == before
