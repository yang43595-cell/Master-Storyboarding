"""Synthetic evidence validation; the reports assert no actual creative quality."""
import copy
import json

import pytest

from scripts.core import agent_acceptance as aa, artifacts
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from test_formal_package_submission import board_candidate, confirmed_narrative, register


def fixture(root):
    narrative, _ = confirmed_narrative(root)
    aa.register_workflow_authorization(root, authorization_id="SYNTHETIC",
        authorization_ref="SYNTHETIC EXISTING USER AUTHORIZATION ONLY", skills=["storyboard-planner"])
    entry = register(root, "BOARD", "storyboard.package", board_candidate(root, narrative), ["narrative.package"])
    entry["checksum"] = artifacts.digest(root / entry["path_or_url"])
    profile_path = aa.ROOT / "skills/storyboard-planner/review-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    report = {"schema_version": "1.0", "kind": "agent-self-check", "authorization_id": "SYNTHETIC",
        "agent_id": "SYNTHETIC TEST ONLY", "checked_at": "2026-11-05T00:00:00Z",
        "scope": "artifact", "verdict": "pass", **aa.acceptance_binding(root, entry),
        "review_profile_sha256": artifacts.digest(profile_path),
        "checks": [{"id": check["id"], "result": "pass", "observation": "Synthetic assertion only",
                    "locator": "synthetic fixture"} for check in profile["production_artifact_checks"]]}
    return entry, report


def write_report(root, report):
    path = root / "self-check.json"
    write_json(path, report)
    return path


def test_evidence_pointer_rechecks_bytes_without_creating_human_facts(tmp_path):
    entry, report = fixture(tmp_path)
    before = copy.deepcopy(load_project_state(tmp_path))
    path = write_report(tmp_path, report)
    entry["agent_acceptance"] = aa.build_agent_acceptance(tmp_path, entry, path)
    assert aa.validate_agent_acceptance(tmp_path, entry) == report
    assert load_project_state(tmp_path) == before
    assert entry["user_approved"] is False
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="报告已改变"):
        aa.validate_agent_acceptance(tmp_path, entry)


@pytest.mark.parametrize("mutation", [
    "missing_check", "duplicate_check", "unknown_check", "malformed_check_id", "malformed_result",
    "failed_check", "missing_observation", "missing_locator", "core_na", "na_without_reason",
    "wrong_profile", "wrong_artifact_hash", "wrong_upstream_hash", "missing_upstream", "partial",
    "partial_ids", "unknown_verdict", "missing_agent", "missing_time", "image_usage_on_text",
    "unbound_group_member", "malformed_group_member",
])
def test_rejects_incomplete_or_unbound_self_check(tmp_path, mutation):
    entry, report = fixture(tmp_path)
    if mutation == "missing_check":
        report["checks"].pop()
    elif mutation == "duplicate_check":
        report["checks"][-1] = report["checks"][0]
    elif mutation == "unknown_check":
        report["checks"][0]["id"] = "INVENTED"
    elif mutation == "malformed_check_id":
        report["checks"][0]["id"] = []
    elif mutation == "malformed_result":
        report["checks"][0]["result"] = {}
    elif mutation == "failed_check":
        report["checks"][0]["result"] = "fail"
    elif mutation in {"missing_observation", "missing_locator"}:
        report["checks"][0].pop(mutation.removeprefix("missing_"))
    elif mutation in {"core_na", "na_without_reason"}:
        check = next(c for c in report["checks"] if (c["id"] in aa.CORE_CHECKS["storyboard-planner"])
                     == (mutation == "core_na"))
        check["result"] = "not_applicable"
        if mutation == "core_na":
            check["reason"] = "Synthetic waiver cannot waive core checks"
    elif mutation == "wrong_profile":
        report["review_profile_sha256"] = "0" * 64
    elif mutation == "wrong_artifact_hash":
        report["artifact"]["sha256"] = "0" * 64
    elif mutation == "wrong_upstream_hash":
        report["upstreams"][0]["sha256"] = "0" * 64
    elif mutation == "missing_upstream":
        report["upstreams"] = []
    elif mutation == "partial":
        report["scope"] = "segment"
    elif mutation == "partial_ids":
        report["shot_ids"] = ["S01-010"]
    elif mutation == "unknown_verdict":
        report["verdict"] = "unknown"
    elif mutation == "missing_agent":
        report["agent_id"] = " "
    elif mutation == "missing_time":
        report.pop("checked_at")
    elif mutation == "image_usage_on_text":
        report["usage"] = {"source_shot_ids": ["S01-010"]}
    else:
        member = {"artifact_id": "UNKNOWN", "revision": "R01", "sha256": "0" * 64}
        if mutation == "malformed_group_member":
            member["artifact_id"] = []
        report["reference_groups"] = [{"group_id": "G", "members": [member]}]
    before = copy.deepcopy(load_project_state(tmp_path))
    with pytest.raises(ValueError):
        aa.build_agent_acceptance(tmp_path, entry, write_report(tmp_path, report))
    assert load_project_state(tmp_path) == before


@pytest.mark.parametrize("field,value", [
    ("status", "revoked"), ("project_path", "/different/project"), ("workflow", "other"),
    ("skills", ["storyboard-director"]), ("skills", [{}]), ("authorization_ref", ""), ("created_at", ""),
])
def test_invalid_authorizations_cannot_support_acceptance(tmp_path, field, value):
    entry, report = fixture(tmp_path)
    state = load_project_state(tmp_path)
    state["workflow_authorizations"][0][field] = value
    with pytest.raises(ValueError):
        aa.build_agent_acceptance(tmp_path, entry, write_report(tmp_path, report), state=state)


def test_changed_authorization_invalidates_an_existing_pointer(tmp_path):
    entry, report = fixture(tmp_path)
    entry["agent_acceptance"] = aa.build_agent_acceptance(tmp_path, entry, write_report(tmp_path, report))
    state = load_project_state(tmp_path)
    state["workflow_authorizations"][0]["authorization_ref"] += " revised"
    write_project_state(tmp_path, state)
    with pytest.raises(ValueError, match="授权已改变"):
        aa.validate_agent_acceptance(tmp_path, entry)


@pytest.mark.parametrize("skills", [[], ["unknown"], [{}], ["storyboard-planner", "storyboard-planner"]])
def test_malformed_registration_has_no_side_effects(tmp_path, skills):
    confirmed_narrative(tmp_path)
    before = copy.deepcopy(load_project_state(tmp_path))
    with pytest.raises(ValueError):
        aa.register_workflow_authorization(tmp_path, authorization_id="SYNTHETIC",
            authorization_ref="SYNTHETIC ONLY", skills=skills)
    assert load_project_state(tmp_path) == before


@pytest.mark.parametrize("logical,kind", [
    ("narrative.package", "narrative.package"), ("visual-asset-manifest", "visual-asset-manifest"),
    ("previs.test", "blender-previs"), ("voice.test", "voice-reference"),
    ("narrative.package", "visual-supplement-image"), ([], "visual-supplement-image"),
])
def test_human_stages_cannot_use_agent_acceptance(logical, kind):
    with pytest.raises(ValueError, match="不允许 agent"):
        aa.skill_for_artifact({"logical_id": logical, "type": kind})


@pytest.mark.parametrize("field,value", [("candidate_test", True), ("production_blocked", True), ("user_approved", True)])
def test_candidate_test_or_human_approval_cannot_be_relabelled(tmp_path, field, value):
    entry, report = fixture(tmp_path)
    entry[field] = value
    with pytest.raises(ValueError):
        aa.build_agent_acceptance(tmp_path, entry, write_report(tmp_path, report))


def test_report_must_be_a_project_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    entry, report = fixture(project)
    outside = write_report(tmp_path, report)
    with pytest.raises(ValueError, match="越出项目"):
        aa.build_agent_acceptance(project, entry, outside)


@pytest.mark.parametrize("failure", ["missing_checksum", "changed_bytes", "malformed_dependencies"])
def test_bindings_require_registered_actual_bytes_and_named_dependencies(tmp_path, failure):
    entry, _ = fixture(tmp_path)
    if failure == "missing_checksum":
        entry.pop("checksum", None)
        entry["status"] = "current"
    elif failure == "malformed_dependencies":
        entry["depends_on"] = [{}]
    else:
        path = tmp_path / entry["path_or_url"]
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        aa.acceptance_binding(tmp_path, entry)


@pytest.mark.parametrize("status,questions", [
    ("blocked", []), ("rejected", []), ("superseded", []), ("candidate", ["Synthetic unresolved decision"]),
])
def test_passing_report_cannot_hide_actual_source_blockers(tmp_path, status, questions):
    entry, report = fixture(tmp_path)
    path = tmp_path / entry["path_or_url"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(status=status, open_questions=questions)
    write_json(path, payload)
    entry["checksum"] = artifacts.digest(path)
    report.update(aa.acceptance_binding(tmp_path, entry))
    with pytest.raises(ValueError, match="源稿仍有阻塞"):
        aa.build_agent_acceptance(tmp_path, entry, write_report(tmp_path, report))


@pytest.mark.parametrize("field", ["quality_evidence", "product_reference"])
def test_image_evidence_cannot_change_under_an_existing_report(tmp_path, field):
    from test_quality_evidence_consumers import agent_fixture
    _, entry = agent_fixture(tmp_path)
    assert aa.validate_agent_acceptance(tmp_path, entry)
    if field == "quality_evidence":
        entry[field]["observer"] = "Different synthetic observation"
    else:
        entry[field] = {"different": "Synthetic packaging reference"}
    with pytest.raises(ValueError, match="自查未绑定"):
        aa.validate_agent_acceptance(tmp_path, entry)


@pytest.mark.parametrize("reconcile", [True, False])
def test_agent_video_adoption_and_revision_keep_generation_reconciliation(tmp_path, reconcile):
    from scripts import stage_context_cli as cli
    from scripts.core import revision_control as rc
    from test_agent_artifact_adoption import report as revision_report
    from test_formal_package_submission import video_project
    from test_quality_evidence_consumers import adopt_agent_fixture_entry
    payload = video_project(tmp_path, current_prompt=False)
    # Replace only synthetic fixture setup with an unapproved candidate, then
    # exercise actual director and video adoption instead of adding receipts.
    manifest = artifacts.load_manifest(tmp_path)
    board = next(item for item in manifest["artifacts"] if item["artifact_id"] == "B")
    board.update(status="candidate", user_approved=False)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    state = load_project_state(tmp_path)
    state.update(current_artifacts=["N"], user_confirmations=[{"artifact_ids": ["N"]}])
    write_project_state(tmp_path, state)
    adopt_agent_fixture_entry(tmp_path, board)
    entry = register(tmp_path, "P", "video.prompt", payload, ["storyboard.package"])
    adopted = adopt_agent_fixture_entry(tmp_path, entry)
    before = copy.deepcopy(load_project_state(tmp_path)["user_confirmations"])
    _, selected = cli._confirmed_project_inputs(tmp_path, load_project_state(tmp_path))
    assert "P" in {item["artifact_id"] for item in selected}
    assert adopted["user_approved"] is False

    rc.open_change(tmp_path, change_id="VIDEO-REV", tier="T1", logical_ids=["video.prompt"],
                   reason="Synthetic generation-segment revision", authorization_id="SYNTHETIC-WF")
    revised = copy.deepcopy(payload)
    revised["revision"] = "R02"
    revised["generation_segments"][1]["segment_id"] = "G-03"
    if reconcile:
        revised["segment_reconciliation"] = {
            "baseline": {"artifact_id": "P", "sha256": adopted["checksum"]},
            "changes": [{"action": "remove", "segment_id": "G-02", "reason": "Synthetic replacement"},
                        {"action": "add", "segment_id": "G-03", "reason": "Synthetic new segment"}]}
    write_json(tmp_path / "P2.json", revised)
    if not reconcile:
        with pytest.raises(ValueError, match="segment_reconciliation"):
            rc.record_candidate(tmp_path, change_id="VIDEO-REV", artifact_id="P2", logical_id="video.prompt",
                                artifact_type="video.prompt", candidate_path="P2.json", revision="R02")
        assert rc.show_change(tmp_path)["active_change"]["status"] == "awaiting_candidate"
        assert load_project_state(tmp_path)["user_confirmations"] == before
        return
    rc.record_candidate(tmp_path, change_id="VIDEO-REV", artifact_id="P2", logical_id="video.prompt",
                        artifact_type="video.prompt", candidate_path="P2.json", revision="R02")
    candidate = next(item for item in artifacts.load_manifest(tmp_path)["artifacts"] if item["artifact_id"] == "P2")
    check = revision_report(tmp_path, candidate, name="video-R02-check.json", authorization_id="SYNTHETIC-WF")
    rc.confirm_agent_candidate(tmp_path, change_id="VIDEO-REV", report_path=str(check))
    rc.commit_change(tmp_path, change_id="VIDEO-REV")
    current = next(item for item in artifacts.load_manifest(tmp_path)["artifacts"] if item["artifact_id"] == "P2")
    assert artifacts.artifact_is_adopted(current, load_project_state(tmp_path), tmp_path)
    assert current["user_approved"] is False
    assert load_project_state(tmp_path)["user_confirmations"] == before
