"""Synthetic project checks for localized depth-recreation writing inputs."""

import json

import pytest

from scripts import stage_context_cli as cli
from scripts.core.artifacts import MANIFEST_PATH, digest, load_manifest
from scripts.core.io import read_json, write_json
from scripts.core.production_handoff import resolve_production_handoff
from scripts.core.project_state import new_project_state, write_project_state
from scripts.core.recreation_segments import PLAN, PROMPTS
from test_formal_package_submission import narrative


@pytest.fixture
def recreation_project(tmp_path, capsys):
    project = tmp_path.resolve()
    write_project_state(project, new_project_state(current_stage="creative-director"))
    story = narrative(project)
    (project / story.pop("human_readable_path")).unlink()
    story["asset_requirements"][0].update(
        source_paragraph_ids=["STP-01", "STP-02", "STP-03", "STP-04", "STP-05"],
        image_decision="无需独立图", image_reason="测试场次直接沿用原表演",
    )
    dialogue_path = project / story["dialogue_evidence_path"]
    evidence = read_json(dialogue_path)
    dialogue_template = story["dialogues"].pop()
    evidence_template = evidence["dialogues"].pop()
    story["story_paragraphs"] = []
    references = {}
    segments = []
    for index in range(1, 6):
        suffix = f"{index:02d}"
        paragraph_id, dialogue_id = f"STP-{suffix}", f"DLG-{suffix}"
        dialogue = f"这句原话{suffix}。"
        story["story_paragraphs"].append({
            "paragraph_id": paragraph_id,
            "text": f"场景正文{suffix}。\n\n主角（{dialogue_id}）：\"{dialogue}\"",
        })
        story["dialogues"].append({
            **dialogue_template, "dialogue_id": dialogue_id,
            "text": dialogue, "source_paragraph_id": paragraph_id,
        })
        evidence["dialogues"].append({
            **evidence_template, "dialogue_id": dialogue_id, "text": dialogue,
            "verification": {**evidence_template["verification"], "dialogue_id": dialogue_id, "text": dialogue},
        })
        start = (index - 1) * 25
        reference_key = f"深度{suffix}"
        movie = project / f"depth-{suffix}.mp4"
        movie.write_bytes(f"fixture depth {suffix}".encode())
        references[reference_key] = {
            "path": movie.name, "sha256": digest(movie), "role": "depth",
            "source_start_frame": start, "frame_count": 25, "subtitle_check": "none",
        }
        segments.append({
            "id": f"SEG-{suffix}", "frames": [start, start + 25], "mode": "depth",
            "motion": reference_key, "reference_frames": [0, 25], "appearance": [],
            "paragraph_ids": [paragraph_id], "dialogue_ids": [dialogue_id],
            "visible_asset_ids": ["AST-CHAR-A"],
            "event": f"本段事件{suffix}", "entry_state": f"进入状态{suffix}", "exit_state": f"结束状态{suffix}",
        })

    write_json(dialogue_path, evidence)
    story["dialogue_evidence_sha256"] = digest(dialogue_path)
    story_path = project / "叙事.json"
    write_json(story_path, story)
    artifact_id = "ART-NARRATIVE-R01"
    assert cli.main([
        "adopt-package", "--project", str(project), "--artifact-id", artifact_id,
        "--logical-id", "narrative.package", "--path", story_path.name, "--revision", "R01",
        "--approval-ref", "synthetic:approved",
    ]) == 0
    capsys.readouterr()
    write_json(project / "00_项目管理/project_request.json", {
        "kickoff": {
            "decision_ref": "synthetic:depth-recreation",
            "creation": {"workflow": "depth-guided-recreation"},
            "product": {"mode": "none"},
        },
    })
    plan = {
        "narrative_id": artifact_id, "narrative_sha256": digest(story_path),
        "fps": 25, "first_frame": 0, "end_frame": 125, "product_visible_frames": [],
        "references": references, "segments": segments,
    }
    write_json(project / PLAN, plan)
    return project, story, plan


def _read_scope(project, capsys, *segment_ids):
    args = ["recreation-prompt-input", "--project", str(project)]
    for segment_id in segment_ids:
        args.extend(["--segment-id", segment_id])
    assert cli.main(args) == 0
    return json.loads(capsys.readouterr().out)


def _seam_pairs(payload):
    return [(seam["from"]["id"], seam["to"]["id"], seam["frame"]) for seam in payload["seams"]]


def test_single_segment_reads_canonical_prose_once_and_leaves_project_unchanged(recreation_project, capsys):
    project, story, plan = recreation_project
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    payload = _read_scope(project, capsys, "SEG-03")

    assert payload["narrative_id"] == plan["narrative_id"]
    assert payload["narrative_sha256"] == plan["narrative_sha256"]
    assert payload["plan_sha256"] == digest(project / PLAN)
    assert payload["fps"] == 25
    assert [row["id"] for row in payload["segments"]] == ["SEG-03"]
    assert payload["story_paragraphs"] == [story["story_paragraphs"][2]]
    original = story["dialogues"][2]
    assert payload["dialogues"] == [{
        **{key: value for key, value in original.items() if key != "text"},
        "speaker_name": "主角", "text_in_paragraph": True,
    }]
    assert payload["references"] == {"深度03": plan["references"]["深度03"]}
    assert _seam_pairs(payload) == [("SEG-02", "SEG-03", 50), ("SEG-03", "SEG-04", 75)]
    assert payload["seams"][0]["from"]["exit_state"] == "结束状态02"
    assert payload["seams"][1]["to"]["entry_state"] == "进入状态04"
    rendered = json.dumps(payload, ensure_ascii=False)
    for suffix in ("01", "02", "04", "05"):
        assert f"场景正文{suffix}。" not in rendered
        assert f"这句原话{suffix}。" not in rendered
    assert "本段事件01" not in rendered and "本段事件05" not in rendered
    assert {path: path.read_bytes() for path in project.rglob("*") if path.is_file()} == before


def test_multiple_segments_keep_exact_dialogue_when_a_shared_paragraph_is_omitted(recreation_project, capsys):
    project, story, plan = recreation_project
    plan["segments"][1]["dialogue_ids"] = []
    plan["segments"][2]["paragraph_ids"].insert(0, "STP-02")
    plan["segments"][2]["dialogue_ids"].insert(0, "DLG-02")
    appearance = project / "room.png"
    appearance.write_bytes(b"fixture appearance")
    plan["references"]["室内外观"] = {
        "path": appearance.name, "sha256": digest(appearance), "role": "appearance",
        "asset_id": "AST-CHAR-A", "output_id": "ROOM-MASTER",
    }
    for row in plan["segments"][2:4]:
        row["appearance"] = ["室内外观"]
    (project / "depth-01.mp4").unlink()
    write_json(project / PLAN, plan)

    payload = _read_scope(project, capsys, "SEG-04", "SEG-03")

    assert [row["id"] for row in payload["segments"]] == ["SEG-03", "SEG-04"]
    assert payload["segments"][0]["event"] == "本段事件03"
    assert payload["story_paragraphs"] == story["story_paragraphs"][2:4]
    assert [row["dialogue_id"] for row in payload["dialogues"]] == ["DLG-02", "DLG-03", "DLG-04"]
    assert payload["dialogues"][0] == {**story["dialogues"][1], "speaker_name": "主角"}
    assert set(payload["references"]) == {"深度03", "深度04", "室内外观"}
    assert payload["references"]["室内外观"] == plan["references"]["室内外观"]
    assert _seam_pairs(payload) == [("SEG-02", "SEG-03", 50), ("SEG-04", "SEG-05", 100)]


def test_missing_selected_event_and_neighbor_state_use_scoped_fallbacks(recreation_project, capsys):
    project, story, plan = recreation_project
    plan["segments"][1]["paragraph_ids"].append("STP-03")
    del plan["segments"][1]["event"], plan["segments"][2]["entry_state"]
    write_json(project / PLAN, plan)

    payload = _read_scope(project, capsys, "SEG-02")

    assert payload["story_paragraphs"] == [story["story_paragraphs"][1]]
    assert payload["story_needs_source_review"] == {"SEG-02": ["STP-03"]}
    assert [row["dialogue_id"] for row in payload["dialogues"]] == ["DLG-02"]
    assert payload["seams"][1]["to"] == {"id": "SEG-03", "event": "本段事件03"}


def test_normal_handoff_points_to_read_scope_and_completed_prompt_can_still_be_revised(recreation_project, capsys):
    project, _, plan = recreation_project
    assert cli.main(["handoff", "--project", str(project), "--to-skill", "voiceover-to-video-script"]) == 0
    report = json.loads(capsys.readouterr().out)
    card = (project / report["path"]).read_text(encoding="utf-8")
    assert "recreation-prompt-input --project <业务项目> --segment-id" in card
    assert "recreation-method.md#原片产品核对与制作分流" in card
    assert "画布转换的深度沿复刻 Skill 核输入窗及引用" in card

    draft = {
        "narrative_id": plan["narrative_id"], "plan_sha256": digest(project / PLAN),
        "segments": [{"id": "SEG-03", "prompt": "用 @深度03。主角说‘这句原话03。’"}],
    }
    for source_fields, issue in (
        ({}, "缺少 narrative_sha256"),
        ({"narrative_sha256": "older-draft"}, "narrative_sha256 与当前采用编导稿不符"),
    ):
        write_json(project / PROMPTS, {**draft, **source_fields})
        assert cli.main(["handoff", "--project", str(project)]) == 0
        handoff = json.loads(capsys.readouterr().out)
        action = next(step for step in handoff["next_actions"] if step["action"] == "video")
        assert "SEG-03" in action["segment_ids"] and issue in action["reason"]
        assert not any(step["action"] == "video" and step["status"] == "complete" for step in handoff["steps"])

    write_json(project / PROMPTS, {**draft, "narrative_sha256": plan["narrative_sha256"]})
    steps = resolve_production_handoff(project, actions={"video"})["steps"]
    assert any(step["action"] == "video" and step["status"] == "complete"
               and step["segment_ids"] == ["SEG-03"] for step in steps)
    assert [row["id"] for row in _read_scope(project, capsys, "SEG-03")["segments"]] == ["SEG-03"]


@pytest.mark.parametrize("segment_ids", [("SEG-99",), ("SEG-03", "SEG-03")])
def test_scope_rejects_unknown_or_repeated_segments(recreation_project, capsys, segment_ids):
    project, _, _ = recreation_project
    with pytest.raises(ValueError, match="SEG-99|SEG-03|重复"):
        _read_scope(project, capsys, *segment_ids)


@pytest.mark.parametrize("stale", ["adoption", "narrative", "plan_link", "media"])
def test_scope_rejects_stale_adoption_and_sources(recreation_project, capsys, stale):
    project, story, plan = recreation_project
    if stale == "adoption":
        manifest = load_manifest(project)
        manifest["artifacts"][0]["status"] = "superseded"
        write_json(project / MANIFEST_PATH, manifest)
    elif stale == "narrative":
        story["objective"] += "未确认变动"
        write_json(project / "叙事.json", story)
    elif stale == "plan_link":
        plan["narrative_sha256"] = "0" * 64
        write_json(project / PLAN, plan)
    else:
        (project / "depth-03.mp4").write_bytes(b"new media bytes")
    with pytest.raises(ValueError, match="编导|叙事|当前|参考|换版|复刻分段"):
        _read_scope(project, capsys, "SEG-03")
