"""Storyboard 5.0 compact contract preserves action states and exact cross-shot speech."""
import copy
import json
import pytest
from test_storyboard_planner_contract import sample_package
from test_story_paragraph_sources import narrative
from core.schema_contracts import schema_errors
from core.story_sources import validate_story_sources


def compact_package():
    return sample_package()


def test_compact_v5_sources_read(tmp_path):
    n = narrative(tmp_path)
    validate_story_sources(n, compact_package(), project_root=tmp_path)


def test_missing_action_or_handoff_rejected():
    p = compact_package()
    p["scenes"][0]["shots"][0].pop("action")
    assert schema_errors(p, "storyboard-package.schema.json")
    p2 = compact_package()
    p2["scenes"][0]["shots"][0]["cut"].pop("handoff")
    assert schema_errors(p2, "storyboard-package.schema.json")


def test_cross_shot_fragments_and_reaction(tmp_path):
    p = compact_package()
    s = p["scenes"][0]["shots"][0]
    s["audio"]["dialogue"][0]["text"] = "不"
    reaction = copy.deepcopy(s)
    reaction["shot_id"] = "S01-020"
    reaction["audio"]["dialogue"][0].update(text="。", presentation="OFFSCREEN")
    p["scenes"][0]["shots"].append(reaction)
    validate_story_sources(narrative(tmp_path), p, project_root=tmp_path)
    reaction["audio"]["dialogue"][0]["text"] = "不。"
    with pytest.raises(ValueError, match="遗漏、重复或改写"):
        validate_story_sources(narrative(tmp_path), p, project_root=tmp_path)


@pytest.mark.parametrize("mutation", ["speaker", "missing", "reference"])
def test_bad_speech_rejected(tmp_path, mutation):
    p = compact_package()
    s = p["scenes"][0]["shots"][0]
    if mutation == "speaker":
        s["audio"]["dialogue"][0]["speaker_id"] = "OTHER"
    if mutation == "missing":
        s["audio"]["dialogue"][0]["text"] = "不"
    if mutation == "reference":
        s["source_dialogue_ids"] = []
    with pytest.raises(ValueError):
        validate_story_sources(narrative(tmp_path), p, project_root=tmp_path)


def test_motion_handoff_time_jump_and_explicit_silence(tmp_path):
    p = compact_package()
    scene = p["scenes"][0]
    s = scene["shots"][0]
    s["camera"]["movement"] = "连续右移至桌边，跟随手中物，保持同一侧"
    s["action"] = "甲持物递出，乙接稳后甲松手，物留在乙手中"
    s["overrides"] = {"music": []}
    scene["defaults"]["music"] = ["低音乐"]
    second = copy.deepcopy(scene)
    second["scene_id"] = "S02"
    second["start_state"] = "稍后，同房间，乙已站着持物"
    second["shots"][0]["shot_id"] = "S02-010"
    second["shots"][0]["source_dialogue_ids"] = []
    second["shots"][0]["audio"]["dialogue"] = []
    second["shots"][0].pop("overrides")
    p["scenes"].append(second)
    validate_story_sources(narrative(tmp_path), p, project_root=tmp_path)
    assert s["overrides"]["music"] == [] and "overrides" not in second["shots"][0]


def test_delivery_survives_cross_shot_roundtrip_without_changing_text(tmp_path):
    p = compact_package()
    shot = p["scenes"][0]["shots"][0]
    shot["audio"]["dialogue"][0].update(text="不", delivery="直接拒绝，坚定")
    listener = copy.deepcopy(shot)
    listener["shot_id"] = "S01-020"
    listener["audio"]["dialogue"][0].update(text="。", presentation="OFFSCREEN", delivery="承接前镜坚定语气，收句")
    p["scenes"][0]["shots"].append(listener)
    saved = json.loads(json.dumps(p, ensure_ascii=False))
    validate_story_sources(narrative(tmp_path), saved, project_root=tmp_path)
    assert saved["scenes"][0]["shots"][1]["audio"]["dialogue"][0]["delivery"] == "承接前镜坚定语气，收句"
