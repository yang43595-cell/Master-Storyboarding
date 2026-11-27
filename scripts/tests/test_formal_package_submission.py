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


@pytest.mark.parametrize("duplicate_minimum", [False, True])
def test_formal_storyboard_checks_required_visuals_at_adoption(tmp_path, duplicate_minimum):
    story, _ = confirmed_narrative(tmp_path)
    board = board_candidate(tmp_path, story)
    visuals = [{"prompt_clause": "她松开放在桌上的手。", "min_seconds": 1.0},
               {"prompt_clause": "物品留在桌面。"}]
    if duplicate_minimum:
        visuals[1]["min_seconds"] = 2.0
    board["scenes"][0]["shots"][0]["required_visuals"] = visuals
    write_json(tmp_path / "B-CAND.json", board)
    args = ["adopt-package", "--project", str(tmp_path), "--artifact-id", "B-CAND",
            "--logical-id", "storyboard.package", "--path", "B-CAND.json",
            "--revision", board["revision"], "--depends-on", "narrative.package",
            "--approval-ref", "synthetic:approval"]
    if duplicate_minimum:
        with pytest.raises(cli.StageContextError, match="required_visuals: S01-010"):
            cli.main(args)
        assert "B-CAND" not in load_project_state(tmp_path)["current_artifacts"]
        return
    cli.main(args)
    adopted = next(a for a in artifacts.load_manifest(tmp_path)["artifacts"] if a["artifact_id"] == "B-CAND")
    assert adopted["status"] == "current"
    assert "target_url" not in board and "platform" not in board


def historic_current(tmp_path):
    package = narrative(tmp_path)
    path = tmp_path / "N.json"
    write_json(path, package)
    current = {"artifact_id": "N", "logical_id": "narrative.package", "type": "narrative.package",
               "revision": package["revision"], "status": "current", "user_approved": True,
               "path_or_url": path.name, "checksum": artifacts.digest(path), "depends_on": []}
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": [current]})
    return current


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


@pytest.mark.parametrize("mutation", [
    {"revision": "R99"}, {"path_or_url": "forged.json"}, {"checksum": "0" * 64},
    {"depends_on": ["unconfirmed.upstream"]}, {"user_approved": False},
    {"logical_id": "unrelated.file", "type": "fixture", "path_or_url": "shadow.json", "checksum": "f" * 64},
])
def test_manifest_save_cannot_rebind_existing_formal_current_id(tmp_path, mutation):
    current = historic_current(tmp_path)
    with pytest.raises(ValueError, match="不能改写 current 正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [{**current, **mutation}]})
    assert artifacts.load_manifest(tmp_path)["artifacts"] == [current]


def test_manifest_save_cannot_reactivate_formal_current_after_staling(tmp_path):
    current = historic_current(tmp_path)
    stale = {**current, "status": "stale"}
    artifacts.save_manifest(tmp_path, {"artifacts": [stale]})
    with pytest.raises(ValueError, match="不能晋升或恢复正式稿件"):
        artifacts.save_manifest(tmp_path, {"artifacts": [current]})


def test_manifest_save_cannot_remove_then_readd_formal_current(tmp_path):
    current = historic_current(tmp_path)
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
    # Preserve scene IDs to exercise the existing cross-scene handoff checks.
    second = copy.deepcopy(board["scenes"][0])
    second.update(scene_id="S02", audiovisual_flow="对方先看桌上的物品，再抬眼看向主角。")
    second_shot = second["shots"][0]
    second_shot.update(
        shot_id="S02-010",
        source_dialogue_ids=[],
        purpose="接拒绝后的对方反应",
        action="对方先看桌上的物品，再抬眼看向主角。",
        cut={"exit": "两人对视时切出",
             "handoff": "保留双方重新谈判前的停顿"},
        rhythm="对方抬眼后停半拍",
        audio={"dialogue": [], "sfx": []},
    )
    second_shot["camera"]["framing"] = "中近景，眼高，对方偏右，主角肩膀留在左前景"
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
    old_prompt = {
        "revision": "R01", "source_storyboard": source_board,
        "generation_segments": [
            {
                "segment_id": "G-01", "source_shot_ids": ["S01-010"],
                "prompt": (
                    "眼高固定中近景，主角偏左，焦点在她的眼睛。左侧窗光照到桌边。"
                    "主角拿着物品看向对方，说：“不。”随后把物品放在桌面，发出轻响，手随即松开。"
                    "拒绝后停半拍，等物品落稳桌面、主角目光稳定后切出。"
                ),
                "director_coverage": [{
                    "shot_id": "S01-010",
                    "action": {"field": "action", "quote": "坚定说出拒绝后将物品轻放在桌面",
                               "prompt_quote": "随后把物品放在桌面"},
                    "reaction": None,
                    "movement": {"field": "camera.movement", "quote": "固定",
                                 "prompt_quote": "眼高固定中近景"},
                    "cut": {"field": "cut.exit", "quote": "物品落稳桌面、主角目光稳定后切出",
                            "prompt_quote": "等物品落稳桌面、主角目光稳定后切出"},
                }],
            },
            {
                "segment_id": "G-02", "source_shot_ids": ["S02-010"],
                "prompt": (
                    "眼高固定中近景，对方偏右，左前景保留主角肩膀，物品留在桌面。"
                    "对方先看桌上的物品，再抬眼看向主角。停半拍，两人对视时切出。"
                ),
                "director_coverage": [{
                    "shot_id": "S02-010",
                    "action": {"field": "action", "quote": "对方先看桌上的物品",
                               "prompt_quote": "对方先看桌上的物品"},
                    "reaction": {"field": "action", "quote": "再抬眼看向主角",
                                 "prompt_quote": "再抬眼看向主角"},
                    "movement": {"field": "camera.movement", "quote": "固定",
                                 "prompt_quote": "眼高固定中近景"},
                    "cut": {"field": "cut.exit", "quote": "两人对视时切出",
                            "prompt_quote": "两人对视时切出"},
                }],
            },
        ],
    }
    for scene, segment in zip(board["scenes"], old_prompt["generation_segments"]):
        shot = scene["shots"][0]
        segment["source_audio"] = [
            dict(fragment, source_shot_id=shot["shot_id"]) for fragment in shot["audio"]["dialogue"]
        ]
    old_prompt["generation_segments"][1]["physical_continuity"] = {
        "from_segment_id": "G-01",
        "permitted_change": "保持对话轴，切至右侧对方的反应。",
        "positions": {"status": "no_visual_needed", "previous_state": "主角在左，对方在右。",
                      "current_state": "两人仍在左右原位。", "basis": "两镜沿用同一组空间规则和对话轴。"},
        "props": {"status": "no_visual_needed", "previous_state": "物品已在桌上，主角松手。",
                  "current_state": "物品留在桌上。", "basis": "后镜起始沿用前镜收尾的物品位置。"},
        "visibility": {"status": "no_visual_needed", "previous_state": "主角中近景。",
                       "current_state": "对方中近景，主角肩膀在左前景。",
                       "basis": "两镜只更换取景，仍是同一对话双方。"},
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


@pytest.mark.parametrize("reason", ["内部对白", "视频段未就绪", "视频稿输入不可用",
                                    "独立泛词", "action 的落点", "director_coverage"])
def test_formal_video_adoption_catches_known_delivery_failures(tmp_path, reason):
    payload = video_project(tmp_path, current_prompt=False)
    row = payload["generation_segments"][0]
    if reason == "内部对白":
        row["prompt"] += " 继续拍 S05-008。"
    elif reason == "视频段未就绪":
        row["submit_ready"] = False
    elif reason == "独立泛词":
        row["prompt"] = "动作自然，画质真实。"
    elif reason == "action 的落点":
        # The declared director action survives in metadata, but the model won't receive it.
        row["prompt"] = row["prompt"].replace("随后把物品放在桌面", "随后抬起手")
    elif reason == "director_coverage":
        del row["director_coverage"]
    else:
        row["materials"] = [{"path": "missing.png"}]
    entry = register(tmp_path, "P-BAD", "video.prompt", payload, ["storyboard.package"])
    with pytest.raises(ValueError, match=reason):
        artifacts.promote_candidate(tmp_path, "video.prompt", entry["artifact_id"])


@pytest.mark.parametrize("declare_root", [False, True])
def test_formal_video_adoption_checks_declared_external_input_roots(tmp_path, declare_root):
    root, external = tmp_path / "film", tmp_path / "selected-inputs"
    root.mkdir()
    payload = video_project(root, current_prompt=False)
    external.mkdir()
    evidence = external / "source.txt"; evidence.write_text("selected source")
    payload["sources"] = [{"path": str(evidence), "sha256": artifacts.digest(evidence)}]
    if declare_root:
        payload["input_roots"] = [str(external)]
    entry = register(root, "P-EXTERNAL", "video.prompt", payload, ["storyboard.package"])
    if not declare_root:
        with pytest.raises(ValueError, match="越出项目及显式 input-root"):
            artifacts.adopt_initial_package(root, entry["artifact_id"], "synthetic approval")
    else:
        artifacts.adopt_initial_package(root, entry["artifact_id"], "synthetic approval")
        adopted = next(item for item in artifacts.load_manifest(root)["artifacts"]
                       if item["artifact_id"] == entry["artifact_id"])
        assert adopted["status"] == "current"


@pytest.mark.parametrize("roots", ["not-an-array", ["relative"], ["/"]])
def test_formal_video_adoption_rejects_unbounded_input_roots(tmp_path, roots):
    payload = video_project(tmp_path, current_prompt=False)
    payload["input_roots"] = roots
    entry = register(tmp_path, "P-ROOTS", "video.prompt", payload, ["storyboard.package"])
    with pytest.raises(ValueError, match="input_roots"):
        artifacts.adopt_initial_package(tmp_path, entry["artifact_id"], "synthetic approval")


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


def test_video_revision_rechecks_only_rows_with_changed_canonical_content(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, 'artifact_is_adopted', lambda *_: True)
    source = tmp_path / 'prior.json'
    rows = [
        {'segment_id': ident, 'prompt': 'synthetic prompt', 'source_shot_ids': [shot],
         'settings': {'enabled': True, 'ordered': [1, 2, 2]}}
        for ident, shot in [('V01', 'S01-010'), ('V02', 'S01-020')]
    ]
    director = {'checksum': 'director-version'}
    payload = {'revision': 'R01', 'generation_segments': rows,
               'source_storyboard': {'sha256': director['checksum']}}
    write_json(source, payload)
    prior = {'artifact_id': 'PRIOR', 'path_or_url': source.name, 'checksum': artifacts.digest(source)}
    revised = copy.deepcopy(payload)
    revised.update(revision='R02', self_check={'observed': 'revised'})
    first, second = revised['generation_segments']
    first['settings'] = {'ordered': [1, 2, 2], 'enabled': True}
    second['settings']['enabled'] = 1

    def rows_to_review():
        return artifacts._video_revision_visual_rows(
            tmp_path, revised, revised['generation_segments'], prior, director,
            {'S01-010', 'S01-020'}, {'current_artifacts': ['PRIOR']})

    assert rows_to_review() == [second]
    first['settings']['ordered'] = [2, 1, 2]
    assert rows_to_review() == [first, second]
    second['settings']['enabled'] = True
    assert rows_to_review() == [first]
    revised['shared_rule'] = 'changed'
    assert rows_to_review() == [first, second]


def test_formal_video_prompt_accepts_reasoned_generation_segment_change(tmp_path):
    baseline = video_project(tmp_path)
    payload = copy.deepcopy(baseline)
    payload["revision"] = "R02"
    payload["generation_segments"][1]["segment_id"] = "G-03"
    payload["segment_reconciliation"] = {
        "baseline": {"artifact_id": "P-OLD", "sha256": artifacts.digest(tmp_path / "P-OLD.json")},
        "changes": [{"action": "remove", "segment_id": "G-02", "reason": "Retire the replaced segment ID."},
                    {"action": "add", "segment_id": "G-03", "reason": "Give the existing reaction shot a new segment ID."}],
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
