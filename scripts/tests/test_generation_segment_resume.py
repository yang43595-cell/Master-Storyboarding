"""Existing prompt revisions must reconcile generation IDs against their frozen source."""
import json
import hashlib

import pytest

from scripts.core import revision_control as rc
from scripts.core.io import write_json


def baseline(tmp_path, payload=None):
    payload = payload or {"revision": "R01", "generation_segments": [
        {"segment_id": "G-01", "prompt": "First actual generation prompt."},
        {"segment_id": "G-02", "prompt": "Second actual generation prompt."},
    ]}
    path = tmp_path / "baseline.json"
    write_json(path, payload)
    record = {"artifact_id": "OLD", "logical_id": "video.prompt", "revision": "R01",
        "path_or_url": path.name, "checksum": rc.sha256_file(path)}
    return path, record


def test_existing_segments_continue_without_replacing_ids(tmp_path):
    _, old = baseline(tmp_path)
    candidate = {"generation_segments": [
        {"segment_id": "G-01", "prompt": "First actual\n   generation prompt."},
        {"segment_id": "G-02", "prompt": "Second actual generation prompt."},
    ]}
    rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_same_id_cannot_hide_material_prompt_replacement_without_exact_reason(tmp_path):
    _, old = baseline(tmp_path, {"revision": "R01", "generation_segments": [
        {"segment_id": "G-01", "prompt": "Alice close-up: stop at the door."}]})
    body = "Bob wide shot: go through the door."
    candidate = {"generation_segments": [{"segment_id": "G-01", "prompt": body}]}
    with pytest.raises(rc.RevisionControlError, match="segment_reconciliation"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)
    baseline_prompt = "Alice close-up: stop at the door."
    candidate["segment_reconciliation"] = {
        "baseline": {"artifact_id": "OLD", "sha256": old["checksum"]},
        "changes": [{"action": "revise", "segment_id": "G-01",
            "before_sha256": hashlib.sha256(baseline_prompt.encode()).hexdigest(),
            "after_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "reason": "The approved shot changed to the corrected blocking."}],
    }
    rc._validate_segment_reconciliation(tmp_path, old, candidate)


@pytest.mark.parametrize(("before", "after"), [
    ("1.5 seconds, move forward -3 steps.", "15 seconds, move forward 3 steps."),
    ("不，移动到门口。", "不移动到门口。"),
])
def test_semantic_punctuation_changes_are_not_normalized_away(tmp_path, before, after):
    _, old = baseline(tmp_path, {"revision": "R01", "generation_segments": [
        {"segment_id": "G-01", "prompt": before}]})
    candidate = {"generation_segments": [{"segment_id": "G-01", "prompt": after}]}
    with pytest.raises(rc.RevisionControlError, match="segment_reconciliation"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)
    candidate["segment_reconciliation"] = {
        "baseline": {"artifact_id": "OLD", "sha256": old["checksum"]},
        "changes": [{"action": "revise", "segment_id": "G-01",
            "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
            "after_sha256": hashlib.sha256(after.encode()).hexdigest(),
            "reason": "The punctuation edit is intended to change the spoken duration/direction."}],
    }
    rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_unrecognized_source_extension_object_does_not_trigger_known_id_shape_rules(tmp_path):
    _, old = baseline(tmp_path, {"revision": "R01", "generation_segments": [
        {"segment_id": "G-01", "prompt": "Keep the same action.",
         "source_note": {"confidence": 0.7, "freeform": ["extension", {"x": 1}]}}]})
    candidate = {"generation_segments": [{"segment_id": "G-01", "prompt": "Keep the same action.",
        "source_note": {"confidence": 0.8, "freeform": ["new extension object"]}}]}
    rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_reordering_existing_ids_requires_exact_order_record(tmp_path):
    _, old = baseline(tmp_path)
    candidate = {"generation_segments": [
        {"segment_id": "G-02", "prompt": "Second actual generation prompt."},
        {"segment_id": "G-01", "prompt": "First actual generation prompt."},
    ]}
    with pytest.raises(rc.RevisionControlError, match="segment_reconciliation"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)
    candidate["segment_reconciliation"] = {
        "baseline": {"artifact_id": "OLD", "sha256": old["checksum"]},
        "changes": [{"action": "reorder", "before": ["G-01", "G-02"],
            "after": ["G-02", "G-01"], "reason": "The second shot now precedes the opening shot."}],
    }
    rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_unexplained_segment_replacement_and_material_group_are_blocked(tmp_path):
    _, old = baseline(tmp_path)
    candidate = {"generation_segments": [{"segment_id": "G-NEW", "prompt": "Replacement."}]}
    with pytest.raises(rc.RevisionControlError, match="segment_reconciliation"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)
    with pytest.raises(rc.RevisionControlError, match="不可被材料组"):
        rc._validate_segment_reconciliation(tmp_path, old, {"materials_groups": [{"items": ["a", "b"]}]})
    with pytest.raises(rc.RevisionControlError, match="真实提示词正文"):
        rc._validate_segment_reconciliation(tmp_path, old, {
            "generation_segments": [{"segment_id": "G-01", "materials": ["a", "b"]}]})


def test_legal_segment_adjustment_requires_exact_baseline_and_reasons(tmp_path):
    _, old = baseline(tmp_path)
    candidate = {"generation_segments": [
        {"segment_id": "G-01", "prompt": "First actual generation prompt."},
        {"segment_id": "G-03", "prompt": "New split prompt."},
    ], "segment_reconciliation": {
        "baseline": {"artifact_id": "OLD", "sha256": old["checksum"]},
        "changes": [
            {"action": "remove", "segment_id": "G-02", "reason": "Split at documented scene transition."},
            {"action": "add", "segment_id": "G-03", "reason": "Continue the same action in the new segment."},
        ],
    }}
    rc._validate_segment_reconciliation(tmp_path, old, candidate)
    candidate["segment_reconciliation"]["baseline"]["sha256"] = "0" * 64
    with pytest.raises(rc.RevisionControlError, match="准确旧 artifact/hash"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_changed_baseline_bytes_are_blocked_even_when_segment_ids_match(tmp_path):
    path, old = baseline(tmp_path)
    path.write_text(json.dumps({"revision": "R01", "generation_segments": [
        {"segment_id": "G-01", "prompt": "Changed without manifest update."},
        {"segment_id": "G-02", "prompt": "Second actual generation prompt."},
    ]}), encoding="utf-8")
    candidate = {"generation_segments": [
        {"segment_id": "G-01", "prompt": "New current prompt."},
        {"segment_id": "G-02", "prompt": "Second actual generation prompt."},
    ]}
    with pytest.raises(rc.RevisionControlError, match="hash 不一致"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_same_segment_id_cannot_change_shot_or_dialogue_coverage_silently(tmp_path):
    _, old = baseline(tmp_path, {"revision": "R01", "generation_segments": [
        {"segment_id": "G-01", "prompt": "Before.", "source_shot_ids": ["S01", "S02"],
         "source_dialogue_ids": ["DLG-01", "DLG-02"]}]})
    candidate = {"generation_segments": [
        {"segment_id": "G-01", "prompt": "Before.", "source_shot_ids": ["S01"],
         "source_dialogue_ids": ["DLG-01"]}]}
    with pytest.raises(rc.RevisionControlError, match="segment_reconciliation"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)
    candidate["segment_reconciliation"] = {
        "baseline": {"artifact_id": "OLD", "sha256": old["checksum"]},
        "changes": [
            {"action": "coverage", "segment_id": "G-01", "field": "source_shot_ids",
             "before": ["S01", "S02"], "after": ["S01"], "reason": "S02 moved to the following segment."},
            {"action": "coverage", "segment_id": "G-01", "field": "source_dialogue_ids",
             "before": ["DLG-01", "DLG-02"], "after": ["DLG-01"], "reason": "DLG-02 follows the same split."},
        ]}
    rc._validate_segment_reconciliation(tmp_path, old, candidate)
    candidate["segment_reconciliation"]["changes"][0]["after"] = ["S02"]
    with pytest.raises(rc.RevisionControlError, match="覆盖对账"):
        rc._validate_segment_reconciliation(tmp_path, old, candidate)


def test_new_project_without_prior_segments_can_start_its_segment_list(tmp_path):
    _, old = baseline(tmp_path, {"revision": "R01", "prompt": "unsegmented prior draft"})
    candidate = {"generation_segments": [{"segment_id": "FIRST", "prompt": "First real prompt."}]}
    rc._validate_segment_reconciliation(tmp_path, old, candidate)
