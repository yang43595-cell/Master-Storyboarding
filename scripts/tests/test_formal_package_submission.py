"""Submission-path checks for formal manuscripts; all project data is synthetic."""
import copy
import json

import pytest

from scripts import stage_context_cli as cli
from scripts.core import artifacts, revision_control as rc
from scripts.core.io import read_json, write_json
from scripts.core.project_state import load_project_state, new_project_state, write_project_state
from test_director_compact import compact_package
from test_story_paragraph_sources import narrative as legacy_narrative


def narrative(root):
    """Fresh synthetic formal input; the shared historical fixture stays unchanged."""
    package = legacy_narrative(root)
    record = {"sources": [], "dialogues": []}
    for row in package["dialogues"]:
        record["dialogues"].append({
            "dialogue_id": row["dialogue_id"], "speaker_id": row["speaker_id"],
            "speaker_status": "verified", "text": row["text"], "source_ids": [],
            "origin": {"kind": "authored_addition", "reference": "synthetic original text request",
                       "scope": "synthetic formal adoption test only", "author": "synthetic user",
                       "purpose": "exercise manuscript submission", "revision": package["revision"]},
            "verification": {"scope": "dialogue", "dialogue_id": row["dialogue_id"],
                             "speaker_id": row["speaker_id"], "text": row["text"],
                             "method": "user_confirmation", "reference": "synthetic exact text and speaker approval",
                             "reviewer": "synthetic user"},
        })
    evidence = root / "synthetic-dialogue-review.json"
    write_json(evidence, record)
    package.update(dialogue_evidence_path=evidence.name,
                   dialogue_evidence_sha256=artifacts.digest(evidence))
    return package


def register(root, artifact_id, logical_id, payload, dependencies=()):
    path = root / f"{artifact_id}.json"
    write_json(path, payload)
    return artifacts.register_workspace_artifact(root, {
        "artifact_id": artifact_id, "logical_id": logical_id, "type": logical_id,
        "revision": payload.get("revision", "R01"), "path_or_url": path.name,
        "depends_on": list(dependencies),
    })


def confirmed_narrative(root):
    package = narrative(root)
    path = root / "N.json"
    write_json(path, package)
    entry = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
             "revision": package["revision"], "status": "current", "user_approved": True,
             "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": []}
    write_json(root / artifacts.MANIFEST_PATH, {"schema_version": "1.0", "artifacts": [entry]})
    state = new_project_state(current_stage="storyboard-planner")
    state.update(current_artifacts=["N"], user_confirmations=[{"artifact_ids": ["N"]}])
    write_project_state(root, state)
    return package, entry


def board_candidate(root, narrative_package, **overrides):
    board = compact_package()
    board.pop("human_readable_path", None)
    board["source_narrative"] = {
        "artifact_id": "N", "logical_id": "narrative.package",
        "revision": narrative_package["revision"], "path": "N.json",
    }
    board.update(overrides)
    return board


def test_formal_narrative_promotion_runs_existing_source_contract(tmp_path):
    package = narrative(tmp_path)
    write_json(tmp_path / "N-CAND.json", package)
    cli.main(["adopt-package", "--project", str(tmp_path), "--artifact-id", "N-CAND",
              "--logical-id", "narrative.package", "--path", "N-CAND.json",
              "--revision", package["revision"], "--approval-ref", "synthetic:approval"])

    adopted = next(a for a in artifacts.load_manifest(tmp_path)["artifacts"]
                   if a["artifact_id"] == "N-CAND")
    assert adopted["status"] == "current"
    assert adopted["checksum"] == artifacts.digest(tmp_path / "N-CAND.json")
    assert "target_url" not in package and "platform" not in package


def test_formal_narrative_promotion_blocks_bad_story_source_mapping(tmp_path):
    package = narrative(tmp_path)
    package["asset_requirements"][0]["source_paragraph_ids"] = ["STP-MISSING"]
    entry = register(tmp_path, "N-BAD", "narrative.package", package)

    with pytest.raises(ValueError, match="资产引用不存在"):
        artifacts.promote_candidate(tmp_path, "narrative.package", entry["artifact_id"])
    assert next(a for a in artifacts.load_manifest(tmp_path)["artifacts"]
                if a["artifact_id"] == "N-BAD")["status"] == "candidate"


def test_formal_package_body_revision_must_match_registered_revision(tmp_path):
    package = narrative(tmp_path)
    entry = register(tmp_path, "N-WRONG-REV", "narrative.package", package)
    manifest = artifacts.load_manifest(tmp_path)
    item = next(a for a in manifest["artifacts"] if a["artifact_id"] == entry["artifact_id"])
    item["revision"] = "R99"
    artifacts.save_manifest(tmp_path, manifest)

    with pytest.raises(ValueError, match="正文 revision 与登记版本不一致"):
        artifacts.promote_candidate(tmp_path, "narrative.package", entry["artifact_id"])


def test_normal_cli_adopts_confirmed_package_without_channel_configuration(tmp_path, capsys):
    project = tmp_path / "synthetic-project"
    project.mkdir()
    write_project_state(project, new_project_state(current_stage="creative-director"))
    package = narrative(project)
    package.pop("human_readable_path")
    package["asset_requirements"][0].update(image_decision="需要制作", image_reason="synthetic only",
                                            character_output_kind="人物单图")
    path = project / "02_编导/R01.json"
    write_json(path, package)

    result = cli.main(["adopt-package", "--project", str(project), "--artifact-id", "N-R01",
                       "--logical-id", "narrative.package", "--path", "02_编导/R01.json",
                       "--revision", "R01", "--approval-ref", "synthetic user confirmation"])

    output = json.loads(capsys.readouterr().out)
    assert result == 0 and output["ok"] is True
    assert output["artifact"]["status"] == "current"
    assert output["artifact"]["checksum"] == artifacts.digest(path)
    assert output["confirmation"]["artifact_ids"] == ["N-R01"]
    assert load_project_state(project)["current_stage"] == "creative-director"
    assert load_project_state(project)["next_stage"] is None
    assert "platform" not in package and "target_url" not in package


def test_normal_cli_keeps_invalid_submission_as_candidate_without_confirmation(tmp_path):
    project = tmp_path / "synthetic-project"
    project.mkdir()
    write_project_state(project, new_project_state(current_stage="creative-director"))
    package = narrative(project)
    package["asset_requirements"][0]["source_paragraph_ids"] = ["STP-MISSING"]
    path = project / "R01.json"
    write_json(path, package)

    with pytest.raises(cli.StageContextError, match="稿件保持候选"):
        cli.main(["adopt-package", "--project", str(project), "--artifact-id", "N-BAD",
                  "--logical-id", "narrative.package", "--path", "R01.json",
                  "--revision", "R01", "--approval-ref", "synthetic user confirmation"])
    entry = next(item for item in artifacts.load_manifest(project)["artifacts"]
                 if item["artifact_id"] == "N-BAD")
    assert entry["status"] == "candidate"
    assert entry["checksum"] == artifacts.digest(path)
    assert load_project_state(project)["current_artifacts"] == []
    assert load_project_state(project)["user_confirmations"] == []
    package["asset_requirements"][0]["source_paragraph_ids"] = ["STP-01"]
    write_json(path, package)
    with pytest.raises(cli.StageContextError, match="登记 hash"):
        cli.main(["adopt-package", "--project", str(project), "--artifact-id", "N-BAD",
                  "--logical-id", "narrative.package", "--path", "R01.json",
                  "--revision", "R01", "--approval-ref", "synthetic user confirmation"])
    assert load_project_state(project)["user_confirmations"] == []


def test_valid_formal_storyboard_requires_controlled_adoption_entry(tmp_path):
    story, _ = confirmed_narrative(tmp_path)
    board = board_candidate(tmp_path, story)
    write_json(tmp_path / "B-CAND.json", board)
    cli.main(["adopt-package", "--project", str(tmp_path), "--artifact-id", "B-CAND",
              "--logical-id", "storyboard.package", "--path", "B-CAND.json",
              "--revision", board["revision"], "--depends-on", "narrative.package",
              "--approval-ref", "synthetic:approval"])
    adopted = next(a for a in artifacts.load_manifest(tmp_path)["artifacts"] if a["artifact_id"] == "B-CAND")
    assert adopted["status"] == "current"
    assert "target_url" not in board and "platform" not in board


def test_manifest_save_cannot_bypass_formal_adoption_entry(tmp_path):
    story = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, story)
    with pytest.raises(ValueError, match="不能晋升或恢复正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [{
            "artifact_id": "N", "logical_id": "narrative.package", "type": "json",
            "revision": story["revision"], "status": "current", "user_approved": True,
            "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": [],
        }]})
    assert not artifacts.load_manifest(tmp_path)["artifacts"]


@pytest.mark.parametrize("field,value", [
    ("revision", "R99"), ("path_or_url", "forged.json"), ("checksum", "0" * 64),
    ("depends_on", ["unconfirmed.upstream"]), ("user_approved", False),
])
def test_manifest_save_cannot_rebind_existing_formal_current_id(tmp_path, field, value):
    package = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, package)
    current = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
               "revision": package["revision"], "status": "current", "user_approved": True,
               "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": []}
    # Seed a synthetic historic record; test the supported manifest writer's attempt to rewrite it.
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [current]})
    changed = copy.deepcopy(current)
    changed[field] = value
    with pytest.raises(ValueError, match="不能改写 current 正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [changed]})
    assert artifacts.load_manifest(tmp_path)["artifacts"] == [current]


def test_manifest_save_cannot_rebind_formal_current_id_to_unrelated_logical_item(tmp_path):
    package = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, package)
    current = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
               "revision": package["revision"], "status": "current", "user_approved": True,
               "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": []}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [current]})
    shadow = {**current, "logical_id": "unrelated.file", "type": "fixture", "path_or_url": "shadow.json",
              "checksum": "f" * 64}
    with pytest.raises(ValueError, match="不能改写 current 正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [shadow]})


def test_manifest_save_cannot_reactivate_formal_current_after_staling(tmp_path):
    package = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, package)
    current = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
               "revision": package["revision"], "status": "current", "user_approved": True,
               "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": []}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [current]})
    stale = {**current, "status": "stale"}
    artifacts.save_manifest(tmp_path, {"artifacts": [stale]})
    with pytest.raises(ValueError, match="不能晋升或恢复正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [current]})


def test_manifest_save_cannot_remove_then_readd_formal_current(tmp_path):
    package = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, package)
    current = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
               "revision": package["revision"], "status": "current", "user_approved": True,
               "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": []}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [current]})
    with pytest.raises(ValueError, match="不能删除 current 正式稿件历史记录"):
        artifacts.save_manifest(tmp_path, {"artifacts": []})
    # If an external/manual edit removes it, a later supported API still cannot
    # reinterpret the old confirmation as permission to make it current again.
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": []})
    with pytest.raises(ValueError, match="不能晋升或恢复正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [current]})


def test_duplicate_artifact_id_cannot_shadow_confirmed_package_at_handoff(tmp_path, capsys):
    state = new_project_state(current_stage="creative-director")
    state["next_stage"] = "storyboard-planner"
    write_project_state(tmp_path, state)
    package = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, package)
    cli.main(["adopt-package", "--project", str(tmp_path), "--artifact-id", "N",
              "--logical-id", "narrative.package", "--path", path.name,
              "--revision", package["revision"], "--approval-ref", "synthetic:user"])
    capsys.readouterr()
    original = artifacts.load_manifest(tmp_path)["artifacts"][0]
    shadow = {**original, "logical_id": "unrelated.file", "type": "fixture",
              "path_or_url": "shadow.json", "checksum": "f" * 64}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [original, shadow]})
    with pytest.raises(cli.StageContextError, match="重复 artifact_id"):
        cli.handoff(type("Args", (), {"project": str(tmp_path), "to_skill": "storyboard-planner"})())


@pytest.mark.parametrize("fault, message", [
    ("missing", "唯一 current"),
    ("unconfirmed", "缺少有效确认"),
    ("expired_hash", "偏离登记字节"),
    ("wrong_revision", "修订与 current 上游不一致"),
])
def test_formal_storyboard_promotion_blocks_bad_upstream(tmp_path, fault, message):
    story, _ = confirmed_narrative(tmp_path)
    board = board_candidate(tmp_path, story)
    if fault == "missing":
        write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": []})
    elif fault == "unconfirmed":
        state = new_project_state()
        write_project_state(tmp_path, state)
    elif fault == "expired_hash":
        (tmp_path / "N.json").write_text("{}", encoding="utf-8")
    elif fault == "wrong_revision":
        board["source_narrative"]["revision"] = "R99"
    entry = register(tmp_path, "B-BAD", "storyboard.package", board, ["narrative.package"])

    with pytest.raises(ValueError, match=message):
        artifacts.promote_candidate(tmp_path, "storyboard.package", entry["artifact_id"])
    assert next(a for a in artifacts.load_manifest(tmp_path)["artifacts"]
                if a["artifact_id"] == "B-BAD")["status"] == "candidate"


def test_register_cannot_directly_create_current_formal_package(tmp_path):
    path = tmp_path / "old.md"
    path.write_text("old event package")
    with pytest.raises(ValueError, match="只能先登记为 candidate"):
        artifacts.register_workspace_artifact(tmp_path, {
            "artifact_id": "LEGACY", "logical_id": "storyboard.package", "type": "markdown",
            "revision": "R01", "path_or_url": path.name, "status": "current", "user_approved": True,
        })
    assert not artifacts.load_manifest(tmp_path)["artifacts"]


@pytest.mark.parametrize("path_name,payload", [
    ("old.md", "old director markdown"),
    ("old-event.json", '{"schema_version":"legacy-event-package","revision":"R02"}'),
])
def test_legacy_import_candidate_cannot_promote_to_current(tmp_path, path_name, payload):
    path = tmp_path / path_name
    path.write_text(payload)
    entry = artifacts.register_workspace_artifact(tmp_path, {
        "artifact_id": "LEGACY", "logical_id": "storyboard.package", "type": "legacy-import",
        "revision": "R02", "path_or_url": path.name,
    })
    with pytest.raises(ValueError, match="仅保留历史读取"):
        artifacts.promote_candidate(tmp_path, "storyboard.package", entry["artifact_id"])
    assert artifacts.load_manifest(tmp_path)["artifacts"][0]["status"] == "candidate"


def test_web_review_approval_does_not_let_legacy_candidate_confirm_or_commit(tmp_path):
    old = tmp_path / "old.md"
    old.write_text("old director text")
    old_entry = {"artifact_id": "OLD", "logical_id": "storyboard.package", "type": "markdown",
                 "revision": "R01", "status": "current", "user_approved": True,
                 "path_or_url": old.name, "checksum": artifacts.digest(old), "depends_on": []}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [old_entry]})
    state = new_project_state(current_stage="storyboard-planner")
    state.update(current_artifacts=["OLD"], user_confirmations=[{"artifact_ids": ["OLD"]}])
    write_project_state(tmp_path, state)
    rc.open_change(tmp_path, change_id="LEGACY-REV", tier="T1",
                   logical_ids=["storyboard.package"], reason="synthetic legacy update")
    rc.confirm_impact(tmp_path, change_id="LEGACY-REV", approval_ref="synthetic:impact")
    new = tmp_path / "new.md"
    new.write_text("changed old format")
    rc.record_candidate(tmp_path, change_id="LEGACY-REV", artifact_id="NEW", logical_id="storyboard.package",
                        artifact_type="markdown", candidate_path=new.name, revision="R02")
    # Exercise the supported web-review result and then the actual confirmation entry.
    rc.record_review(tmp_path, change_id="LEGACY-REV", verdict="approved", report_ref="synthetic:web-review")
    with pytest.raises(rc.RevisionControlError, match="仅保留历史读取"):
        rc.confirm_candidate(tmp_path, change_id="LEGACY-REV", approval_ref="synthetic:user")
    control = rc.show_change(tmp_path)
    assert control["active_change"]["status"] == "awaiting_candidate_confirmation"
    assert control["active_change"].get("candidate_confirmation") is None
    assert artifacts.load_manifest(tmp_path)["artifacts"][-1]["status"] == "candidate"


def test_revision_confirmation_checks_manuscript_before_recording_approval(tmp_path):
    original = narrative(tmp_path)
    original_path = tmp_path / "N.json"
    write_json(original_path, original)
    current = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
               "revision": original["revision"], "status": "current", "user_approved": True,
               "path_or_url": original_path.name, "checksum": artifacts.digest(original_path), "depends_on": []}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [current]})
    state = new_project_state(current_stage="creative-director")
    state.update(current_artifacts=["N"], user_confirmations=[{"artifact_ids": ["N"]}])
    write_project_state(tmp_path, state)

    rc.open_change(tmp_path, change_id="SYNTH-REV", tier="T1", logical_ids=["narrative.package"], reason="synthetic")
    rc.confirm_impact(tmp_path, change_id="SYNTH-REV", approval_ref="synthetic impact receipt")
    bad = copy.deepcopy(original)
    bad["revision"] = "R02"
    bad["dialogues"][0]["text"] = "改写但未同步正文。"
    write_json(tmp_path / "N-R02.json", bad)
    rc.record_candidate(tmp_path, change_id="SYNTH-REV", artifact_id="N-R02",
                        logical_id="narrative.package", artifact_type="narrative.package",
                        candidate_path="N-R02.json", revision="R02")
    rc.record_review(tmp_path, change_id="SYNTH-REV", verdict="approved", report_ref="synthetic review")

    with pytest.raises(rc.RevisionControlError, match="正式稿件提交前校验失败"):
        rc.confirm_candidate(tmp_path, change_id="SYNTH-REV", approval_ref="synthetic user approval")
    control = rc.show_change(tmp_path)
    assert control["active_change"]["status"] == "awaiting_candidate_confirmation"
    assert not any(r.get("artifact_id") == "N-R02" for r in load_project_state(tmp_path)["user_confirmations"])


def test_commit_rechecks_confirmed_candidate_bytes(tmp_path):
    original, _ = confirmed_narrative(tmp_path)
    candidate = copy.deepcopy(original)
    candidate["revision"] = "R02"
    write_json(tmp_path / "N-R02.json", candidate)
    rc.open_change(tmp_path, change_id="BYTE-DRIFT", tier="T1",
                   logical_ids=["narrative.package"], reason="synthetic tested revision")
    rc.confirm_impact(tmp_path, change_id="BYTE-DRIFT", approval_ref="synthetic:impact")
    rc.record_candidate(tmp_path, change_id="BYTE-DRIFT", artifact_id="N-R02", logical_id="narrative.package",
                        artifact_type="narrative.package", candidate_path="N-R02.json", revision="R02")
    rc.record_review(tmp_path, change_id="BYTE-DRIFT", verdict="approved", report_ref="synthetic:review")
    rc.confirm_candidate(tmp_path, change_id="BYTE-DRIFT", approval_ref="synthetic:user")
    changed = read_json(tmp_path / "N-R02.json")
    changed["change_summary"] = ["changed after exact user confirmation"]
    write_json(tmp_path / "N-R02.json", changed)
    with pytest.raises(rc.RevisionControlError, match="提交前发生漂移"):
        rc.commit_change(tmp_path, change_id="BYTE-DRIFT")
    assert next(a for a in artifacts.load_manifest(tmp_path)["artifacts"] if a["artifact_id"] == "N-R02")["status"] == "candidate"
    assert rc.show_change(tmp_path)["active_change"]["status"] == "commit_ready"


def test_whole_revision_approval_covers_new_creative_dialogue_without_editing_evidence(tmp_path):
    from scripts.core.story_sources import validate_story_sources

    original, _ = confirmed_narrative(tmp_path)
    candidate = copy.deepcopy(original)
    candidate["revision"] = "R02"
    original_evidence = tmp_path / original["dialogue_evidence_path"]
    evidence = read_json(original_evidence)
    row = evidence["dialogues"][0]
    row["origin"]["revision"] = "R02"
    row.pop("verification")
    path = tmp_path / "revision-dialogues.json"
    write_json(path, evidence)
    candidate.update(dialogue_evidence_path=path.name, dialogue_evidence_sha256=artifacts.digest(path))
    source = tmp_path / "N-R02.json"
    write_json(source, candidate)
    before = source.read_bytes(), path.read_bytes()

    rc.open_change(tmp_path, change_id="SCRIPT-REV", tier="T1",
                   logical_ids=["narrative.package"], reason="synthetic revised narrative")
    rc.confirm_impact(tmp_path, change_id="SCRIPT-REV", approval_ref="synthetic:impact")
    rc.record_candidate(tmp_path, change_id="SCRIPT-REV", artifact_id="N-R02", logical_id="narrative.package",
                        artifact_type="narrative.package", candidate_path=source.name, revision="R02")
    rc.record_review(tmp_path, change_id="SCRIPT-REV", verdict="approved", report_ref="synthetic:review")
    with pytest.raises(ValueError, match="未核对白证据"):
        validate_story_sources(candidate, project_root=tmp_path)
    rc.confirm_candidate(tmp_path, change_id="SCRIPT-REV", approval_ref="synthetic:whole-script-human-approval")
    rc.commit_change(tmp_path, change_id="SCRIPT-REV")
    validate_story_sources(candidate, project_root=tmp_path, narrative_path=source, require_dialogue_evidence=True)
    assert before == (source.read_bytes(), path.read_bytes())


def video_project(root, *, current_prompt=True):
    story, _ = confirmed_narrative(root)
    board = board_candidate(root, story)
    # Synthetic second scene: the reconciliation test must also retain real
    # source mappings rather than treating two unmapped prompts as a full film.
    second = copy.deepcopy(board["scenes"][0])
    second["scene_id"] = "S02"
    second["shots"][0].update(shot_id="S02-010", source_dialogue_ids=[])
    second["shots"][0]["audio"]["dialogue"] = []
    board["scenes"].append(second)
    board_path = root / "B.json"
    write_json(board_path, board)
    board_entry = {"artifact_id": "B", "logical_id": "storyboard.package", "type": "storyboard.package",
                   "revision": board["revision"], "status": "current", "user_approved": True,
                   "path_or_url": board_path.name, "checksum": artifacts.digest(board_path),
                   "depends_on": ["narrative.package"]}
    manifest = artifacts.load_manifest(root)
    manifest["artifacts"].append(board_entry)
    source_board = {"artifact_id": "B", "logical_id": "storyboard.package",
                    "revision": board["revision"], "path": board_path.name}
    old_prompt = {"revision": "R01", "source_storyboard": source_board,
                  "generation_segments": [{"segment_id": "G-01", "prompt": "Alice close-up: stop at the door.", "source_shot_ids": ["S01-010"]},
                                          {"segment_id": "G-02", "prompt": "She turns back.", "source_shot_ids": ["S02-010"]}]}
    for scene, segment in zip(board["scenes"], old_prompt["generation_segments"]):
        shot = scene["shots"][0]
        segment["source_audio"] = [
            dict(fragment, source_shot_id=shot["shot_id"]) for fragment in shot["audio"]["dialogue"]
        ]
    # The synthetic shot mapping deliberately crosses scene IDs while keeping
    # the same actor, doorway and visible state across the cut.
    old_prompt["generation_segments"][1]["physical_continuity"] = {
        "from_segment_id": "G-01",
        "permitted_change": "Alice turns her head without stepping away.",
        "positions": {"status": "no_visual_needed", "previous_state": "Alice stops at the door.",
                      "current_state": "Alice stands at the same door.",
                      "basis": "The cloned scene and its continuation keep Alice at one marked position."},
        "props": {"status": "no_visual_needed", "previous_state": "Alice carries no object.",
                  "current_state": "Alice carries no object.",
                  "basis": "Neither source shot gives Alice an object or a handoff."},
        "visibility": {"status": "no_visual_needed", "previous_state": "Alice is visible in close-up.",
                        "current_state": "Alice remains visible while looking back.",
                        "basis": "The turn does not introduce a new visible character or object."},
    }
    old_path = root / "P-OLD.json"
    write_json(old_path, old_prompt)
    if current_prompt:
        manifest["artifacts"].append({"artifact_id": "P-OLD", "logical_id": "video.prompt", "type": "video.prompt",
                                      "revision": "R01", "status": "current", "user_approved": True,
                                      "path_or_url": old_path.name, "checksum": artifacts.digest(old_path),
                                      "depends_on": ["storyboard.package"]})
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    state = new_project_state(current_stage="voiceover-to-video-script")
    current_ids = ["N", "B"] + (["P-OLD"] if current_prompt else [])
    state.update(current_artifacts=current_ids,
                 user_confirmations=[{"artifact_ids": current_ids}])
    write_project_state(root, state)
    return old_prompt


@pytest.mark.parametrize("mutation", ["reorder", "rewrite", "materials_group", "section_links"])
def test_formal_video_prompt_promotion_runs_generation_segment_reconciliation(tmp_path, mutation):
    baseline = video_project(tmp_path)
    payload = copy.deepcopy(baseline)
    payload["revision"] = "R02"
    if mutation == "reorder":
        payload["generation_segments"].reverse()
    elif mutation == "rewrite":
        payload["generation_segments"][0]["prompt"] = "Bob wide shot: go through the door."
    elif mutation == "section_links":
        payload["creative_sections"] = [{"scene_id": "SYNTHETIC", "generation_segments": ["OTHER-01", "OTHER-02"]}]
    else:
        payload.pop("generation_segments")
        payload["materials_groups"] = [{"name": "materials only"}]
    entry = register(tmp_path, "P-BAD", "video.prompt", payload, ["storyboard.package"])

    with pytest.raises(ValueError, match="segment_reconciliation|generation_segments|材料组|创作段引用不一致"):
        artifacts.promote_candidate(tmp_path, "video.prompt", entry["artifact_id"])
    assert next(a for a in artifacts.load_manifest(tmp_path)["artifacts"]
                if a["artifact_id"] == "P-BAD")["status"] == "candidate"


def test_formal_video_adoption_rejects_rewritten_source_audio(tmp_path):
    payload = video_project(tmp_path, current_prompt=False)
    payload["generation_segments"][0]["source_audio"][0]["text"] = "Changed dialogue."
    entry = register(tmp_path, "P-DRIFT", "video.prompt", payload, ["storyboard.package"])
    with pytest.raises(ValueError, match="source_audio"):
        artifacts.promote_candidate(tmp_path, "video.prompt", entry["artifact_id"])
    assert next(a for a in artifacts.load_manifest(tmp_path)["artifacts"]
                if a["artifact_id"] == entry["artifact_id"])["status"] == "candidate"


@pytest.mark.parametrize("declared_total", [None, 3, 2], ids=["legacy", "matching", "impossible"])
def test_adopt_package_checks_video_shot_schedule_against_declared_total(tmp_path, declared_total):
    payload = video_project(tmp_path, current_prompt=False)
    segment = payload["generation_segments"][0]
    segment["shot_durations"] = [3]
    if declared_total is not None:
        segment["duration_seconds"] = declared_total
    path = tmp_path / "P-NEW.json"
    write_json(path, payload)
    before = load_project_state(tmp_path)
    arguments = ["adopt-package", "--project", str(tmp_path), "--artifact-id", "P-NEW",
                 "--logical-id", "video.prompt", "--path", path.name, "--revision", "R01",
                 "--approval-ref", "synthetic exact manuscript approval", "--depends-on", "storyboard.package"]

    if declared_total == 2:
        with pytest.raises(cli.StageContextError, match=r"G-01.*(?:片段总长|逐镜合计)"):
            cli.main(arguments)
        assert load_project_state(tmp_path) == before
    else:
        assert cli.main(arguments) == 0
        assert "P-NEW" in load_project_state(tmp_path)["current_artifacts"]
    entry = next(a for a in artifacts.load_manifest(tmp_path)["artifacts"] if a["artifact_id"] == "P-NEW")
    assert entry["status"] == ("candidate" if declared_total == 2 else "current")


def test_formal_video_prompt_accepts_reasoned_generation_segment_change(tmp_path):
    baseline = video_project(tmp_path)
    payload = copy.deepcopy(baseline)
    payload["revision"] = "R02"
    payload["generation_segments"][1].update(segment_id="G-03", prompt="Continue after the turn.")
    payload["segment_reconciliation"] = {
        "baseline": {"artifact_id": "P-OLD", "sha256": artifacts.digest(tmp_path / "P-OLD.json")},
        "changes": [{"action": "remove", "segment_id": "G-02", "reason": "Move the return glance to a later continuation."},
                    {"action": "add", "segment_id": "G-03", "reason": "Keep the continuation as its own generation segment."}],
    }
    write_json(tmp_path / "P-NEW.json", payload)
    rc.open_change(tmp_path, change_id="PROMPT-REV", tier="T1",
                   logical_ids=["video.prompt"], reason="synthetic segment adjustment")
    rc.confirm_impact(tmp_path, change_id="PROMPT-REV", approval_ref="synthetic:impact")
    rc.record_candidate(tmp_path, change_id="PROMPT-REV", artifact_id="P-NEW", logical_id="video.prompt",
                        artifact_type="video.prompt", candidate_path="P-NEW.json", revision="R02")
    rc.record_review(tmp_path, change_id="PROMPT-REV", verdict="approved", report_ref="synthetic:review")
    rc.confirm_candidate(tmp_path, change_id="PROMPT-REV", approval_ref="synthetic:user")
    rc.commit_change(tmp_path, change_id="PROMPT-REV")

    adopted = next(a for a in artifacts.load_manifest(tmp_path)["artifacts"]
                   if a["artifact_id"] == "P-NEW")
    assert adopted["status"] == "current"
    assert "target_url" not in payload and "platform" not in payload


def remove_dialogue_binding(package):
    package.pop("dialogue_evidence_path")
    package.pop("dialogue_evidence_sha256")


def unbound_current_narrative(root):
    """Seed a synthetic historical source, never rewrite a real approval."""
    source = read_json(root / "N.json")
    remove_dialogue_binding(source)
    write_json(root / "N.json", source)
    manifest = artifacts.load_manifest(root)
    next(item for item in manifest["artifacts"] if item["artifact_id"] == "N")["checksum"] = artifacts.digest(root / "N.json")
    write_json(root / artifacts.MANIFEST_PATH, manifest)


@pytest.mark.parametrize("logical_id", ["narrative.package", "storyboard.package", "video.prompt"])
def test_adopt_package_cannot_use_unbound_dialogue_as_legacy_compatibility(tmp_path, logical_id):
    dependencies = []
    if logical_id == "narrative.package":
        package = narrative(tmp_path)
        remove_dialogue_binding(package)
        package.pop("human_readable_path")
        package["asset_requirements"][0].update(image_decision="需要制作", image_reason="synthetic only")
    elif logical_id == "storyboard.package":
        story, _ = confirmed_narrative(tmp_path)
        package = board_candidate(tmp_path, story)
        unbound_current_narrative(tmp_path)
        dependencies = ["narrative.package"]
    else:
        package = video_project(tmp_path, current_prompt=False)
        unbound_current_narrative(tmp_path)
        dependencies = ["storyboard.package"]
    write_json(tmp_path / "NEW.json", package)
    before = copy.deepcopy(load_project_state(tmp_path))
    arguments = ["adopt-package", "--project", str(tmp_path), "--artifact-id", "NEW",
                 "--logical-id", logical_id, "--path", "NEW.json",
                 "--revision", package["revision"], "--approval-ref", "synthetic exact manuscript approval"]
    for dependency in dependencies:
        arguments.extend(["--depends-on", dependency])
    with pytest.raises(cli.StageContextError, match="必须绑定 dialogue_evidence"):
        cli.main(arguments)
    entry = next(item for item in artifacts.load_manifest(tmp_path)["artifacts"] if item["artifact_id"] == "NEW")
    assert entry["status"] == "candidate" and not entry.get("user_approved")
    state = load_project_state(tmp_path)
    assert state["user_confirmations"] == before["user_confirmations"]
    assert state["current_artifacts"] == before["current_artifacts"]


@pytest.mark.parametrize("logical_id", ["narrative.package", "storyboard.package", "video.prompt"])
def test_revision_confirmation_cannot_erase_dialogue_binding(tmp_path, logical_id):
    if logical_id == "narrative.package":
        package, _ = confirmed_narrative(tmp_path)
        package = copy.deepcopy(package)
        remove_dialogue_binding(package)
    else:
        prompt = video_project(tmp_path)
        package = read_json(tmp_path / "B.json") if logical_id == "storyboard.package" else prompt
        unbound_current_narrative(tmp_path)
    package["revision"] = "R03"
    write_json(tmp_path / "REV.json", package)
    rc.open_change(tmp_path, change_id="UNBOUND-REV", tier="T1", logical_ids=[logical_id],
                   reason="synthetic missing source evidence negative test")
    rc.confirm_impact(tmp_path, change_id="UNBOUND-REV", approval_ref="synthetic impact approval")
    rc.record_candidate(tmp_path, change_id="UNBOUND-REV", artifact_id="REV", logical_id=logical_id,
                        artifact_type=logical_id, candidate_path="REV.json", revision="R03")
    rc.record_review(tmp_path, change_id="UNBOUND-REV", verdict="approved", report_ref="synthetic review")
    before = copy.deepcopy(load_project_state(tmp_path)["user_confirmations"])
    with pytest.raises(rc.RevisionControlError, match="必须绑定 dialogue_evidence"):
        rc.confirm_candidate(tmp_path, change_id="UNBOUND-REV", approval_ref="synthetic exact revision approval")
    change = rc.show_change(tmp_path)["active_change"]
    assert change["status"] == "awaiting_candidate_confirmation" and not change.get("candidate_confirmation")
    assert load_project_state(tmp_path)["user_confirmations"] == before
    assert next(item for item in artifacts.load_manifest(tmp_path)["artifacts"] if item["artifact_id"] == "REV")["status"] == "candidate"
