"""Synthetic local acceptance transactions; no fixture is user or visual approval."""
import base64
import copy
import json
from pathlib import Path

import pytest

from scripts.core import agent_acceptance as aa, artifacts, revision_control as rc
from scripts.core import quality_evidence as quality
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, write_project_state
from test_formal_package_submission import board_candidate, confirmed_narrative, register


def report(root, entry, *, name="self-check.json", **overrides):
    skill = aa.skill_for_artifact(entry)
    profile = Path(aa.ROOT) / "skills" / skill / "review-profile.json"
    checks = json.loads(profile.read_text(encoding="utf-8"))["production_artifact_checks"]
    value = {"schema_version": "1.0", "kind": "agent-self-check", "authorization_id": "SYNTHETIC-FLOW",
             "agent_id": "SYNTHETIC TEST ONLY", "checked_at": "2026-11-05T00:00:00Z",
             "scope": "artifact", "verdict": "pass", **aa.acceptance_binding(root, entry),
             "review_profile_sha256": artifacts.digest(profile),
             "checks": [{"id": item["id"], "result": "pass", "observation": "Synthetic assertion only",
                         "locator": "synthetic fixture"} for item in checks]}
    value.update(overrides)
    path = root / name
    write_json(path, value)
    return path


def pending(root):
    narrative, _ = confirmed_narrative(root)
    aa.register_workflow_authorization(root, authorization_id="SYNTHETIC-FLOW",
        authorization_ref="SYNTHETIC USER FLOW AUTHORIZATION ONLY", skills=["storyboard-planner"])
    path = root / "B.json"
    write_json(path, board_candidate(root, narrative, revision="R01"))
    entry = artifacts.register_workspace_artifact(root, {"artifact_id": "B", "logical_id": "storyboard.package",
        "type": "storyboard.package", "revision": "R01", "path_or_url": path.name,
        "depends_on": ["narrative.package"], "checksum": artifacts.digest(path)})
    return narrative, entry


def test_initial_agent_adoption_remains_distinct_from_human_confirmation(tmp_path):
    _, entry = pending(tmp_path)
    before = copy.deepcopy(load_project_state(tmp_path)["user_confirmations"])
    adopted = artifacts.adopt_initial_agent_artifact(tmp_path, "B", str(report(tmp_path, entry)))["artifact"]
    state = load_project_state(tmp_path)
    assert adopted["status"] == "current" and adopted["user_approved"] is False
    assert state["user_confirmations"] == before
    assert artifacts.artifact_is_adopted(adopted, state, tmp_path)
    assert not artifacts.artifact_is_confirmed(adopted, state, tmp_path)
    manifest = artifacts.load_manifest(tmp_path)
    manifest["artifacts"][-1]["agent_acceptance"]["report_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="不能改写"):
        artifacts.save_manifest(tmp_path, manifest)


@pytest.mark.parametrize("field,value", [("checksum", "0" * 64), ("revision", "R99"),
    ("logical_id", "other"), ("type", "other"), ("path_or_url", "other.json"),
    ("depends_on", []), ("user_approved", True), ("agent_acceptance", None)])
def test_generic_save_cannot_rebind_agent_adopted_identity(tmp_path, field, value):
    _, entry = pending(tmp_path)
    artifacts.adopt_initial_agent_artifact(tmp_path, "B", str(report(tmp_path, entry)))
    manifest = artifacts.load_manifest(tmp_path)
    manifest["artifacts"][-1][field] = value
    with pytest.raises(ValueError, match="不能改写"):
        artifacts.save_manifest(tmp_path, manifest)
    assert artifacts.load_manifest(tmp_path)["artifacts"][-1]["status"] == "current"


@pytest.mark.parametrize("failure", ["report", "authorization", "upstream"])
def test_consumption_rechecks_own_acceptance_and_upstream_byte_binding(tmp_path, failure):
    _, entry = pending(tmp_path)
    checked = report(tmp_path, entry)
    adopted = artifacts.adopt_initial_agent_artifact(tmp_path, "B", str(checked))["artifact"]
    if failure == "authorization":
        state = load_project_state(tmp_path)
        state["workflow_authorizations"][0]["status"] = "revoked"
        write_project_state(tmp_path, state)
    else:
        path = checked if failure == "report" else tmp_path / "N.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not artifacts.artifact_is_adopted(adopted, load_project_state(tmp_path), tmp_path)


def test_old_human_initial_entry_cannot_replace_agent_current(tmp_path):
    narrative, entry = pending(tmp_path)
    artifacts.adopt_initial_agent_artifact(tmp_path, "B", str(report(tmp_path, entry)))
    register(tmp_path, "B-HUMAN", "storyboard.package", board_candidate(tmp_path, narrative, revision="R02"))
    before = copy.deepcopy(load_project_state(tmp_path))
    with pytest.raises(ValueError, match="已有 current/stale"):
        artifacts.adopt_initial_package(tmp_path, "B-HUMAN", "SYNTHETIC ONLY")
    assert load_project_state(tmp_path) == before
    assert next(item for item in artifacts.load_manifest(tmp_path)["artifacts"] if item["artifact_id"] == "B")["status"] == "current"


@pytest.mark.parametrize("failure", ["no_authorization", "partial", "wrong_hash", "failed", "unknown"])
def test_initial_rejected_agent_reports_do_not_change_adoption_facts(tmp_path, failure):
    _, entry = pending(tmp_path)
    path = report(tmp_path, entry)
    value = json.loads(path.read_text(encoding="utf-8"))
    if failure == "no_authorization":
        state = load_project_state(tmp_path)
        state["workflow_authorizations"] = []
        write_project_state(tmp_path, state)
    elif failure == "partial":
        value.update(scope="segment", paragraph_ids=["STP-001"])
    elif failure == "wrong_hash":
        value["artifact"]["sha256"] = "0" * 64
    else:
        value["checks"][0]["result"] = failure
    write_json(path, value)
    before = copy.deepcopy(load_project_state(tmp_path))
    with pytest.raises(ValueError):
        artifacts.adopt_initial_agent_artifact(tmp_path, "B", str(path))
    assert artifacts.load_manifest(tmp_path)["artifacts"][-1]["status"] == "candidate"
    assert load_project_state(tmp_path) == before


def ready_revision(root):
    narrative, entry = pending(root)
    artifacts.adopt_initial_agent_artifact(root, "B", str(report(root, entry)))
    human = copy.deepcopy(load_project_state(root)["user_confirmations"])
    rc.open_change(root, change_id="SYNTHETIC-REV", tier="T1", logical_ids=["storyboard.package"],
                   reason="synthetic revision", authorization_id="SYNTHETIC-FLOW")
    board = board_candidate(root, narrative, revision="R02")
    path = root / "B2.json"
    write_json(path, board)
    rc.record_candidate(root, change_id="SYNTHETIC-REV", artifact_id="B2", logical_id="storyboard.package",
                        artifact_type="storyboard.package", candidate_path=path.name, revision="R02")
    candidate = artifacts.load_manifest(root)["artifacts"][-1]
    self_check = report(root, candidate, name="self-check-R02.json")
    rc.confirm_agent_candidate(root, change_id="SYNTHETIC-REV", report_path=str(self_check))
    return self_check, human


def test_authorized_agent_revision_commits_without_human_receipts(tmp_path):
    _, before = ready_revision(tmp_path)
    assert rc.show_change(tmp_path)["active_change"]["impact_gate"] == "authorized"
    with pytest.raises(ValueError, match="自主变更"):
        rc.confirm_candidate(tmp_path, change_id="SYNTHETIC-REV", approval_ref="SYNTHETIC NOT ACTUAL APPROVAL")
    rc.commit_change(tmp_path, change_id="SYNTHETIC-REV")
    state = load_project_state(tmp_path)
    adopted = artifacts.load_manifest(tmp_path)["artifacts"][-1]
    assert adopted["user_approved"] is False and adopted["status"] == "current"
    assert state["user_confirmations"] == before and state["current_artifacts"] == ["N", "B2"]
    assert artifacts.artifact_is_adopted(adopted, state, tmp_path)
    assert "candidate_confirmation" not in rc.show_change(tmp_path)["history"][-1]


@pytest.mark.parametrize("failure", ["report_drift", "revoked_authorization", "candidate_drift"])
def test_agent_revision_rechecks_evidence_before_commit(tmp_path, failure):
    self_check, _ = ready_revision(tmp_path)
    if failure == "report_drift":
        self_check.write_text(self_check.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif failure == "candidate_drift":
        path = tmp_path / "B2.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    else:
        state = load_project_state(tmp_path)
        state["workflow_authorizations"][0]["status"] = "revoked"
        write_project_state(tmp_path, state)
    before = copy.deepcopy(load_project_state(tmp_path))
    with pytest.raises(ValueError):
        rc.commit_change(tmp_path, change_id="SYNTHETIC-REV")
    assert load_project_state(tmp_path) == before
    assert artifacts.load_manifest(tmp_path)["artifacts"][-1]["status"] == "candidate"


@pytest.mark.parametrize("shot", ["S01-010", "S99-999"])
def test_supplement_agent_adoption_requires_real_current_director_shot(tmp_path, shot):
    _, board = pending(tmp_path)
    artifacts.adopt_initial_agent_artifact(tmp_path, "B", str(report(tmp_path, board)))
    aa.register_workflow_authorization(tmp_path, authorization_id="SYNTHETIC-IMAGE",
        authorization_ref="SYNTHETIC IMAGE FLOW ONLY", skills=["storyboard-director"])
    image = tmp_path / "image.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    evidence = {"status": "pass", "observer": "SYNTHETIC ONLY", "checked_at": "2026-11-05T00:00:00Z",
                "image_sha256": artifacts.digest(image), "source_shot_ids": [shot],
                "source_files": [{"path": "B.json", "sha256": artifacts.digest(tmp_path / "B.json")}],
                "view": {"resolution": "original", "locator": "Synthetic pixel only"},
                "checks": {key: {"result": "pass", "observation": "Synthetic assertion only",
                                 "locator": "Synthetic fixture"} for key in quality.IMAGE_ASPECTS}}
    entry = artifacts.register_workspace_artifact(tmp_path, {"artifact_id": "IMG", "logical_id": "image.test",
        "type": "visual-supplement-image", "revision": "R01", "path_or_url": image.name,
        "depends_on": ["storyboard.package"], "quality_evidence": evidence, "checksum": artifacts.digest(image)})
    path = report(tmp_path, entry, name="image-self-check.json", authorization_id="SYNTHETIC-IMAGE",
                  usage={"source_shot_ids": [shot]})
    with pytest.raises(ValueError, match="不能自主采用"):
        artifacts.promote_candidate(tmp_path, "image.test", "IMG")
    before = copy.deepcopy(load_project_state(tmp_path)["user_confirmations"])
    if shot == "S99-999":
        with pytest.raises(ValueError, match="用途镜号"):
            artifacts.adopt_initial_agent_artifact(tmp_path, "IMG", str(path))
    else:
        adopted = artifacts.adopt_initial_agent_artifact(tmp_path, "IMG", str(path))["artifact"]
        assert artifacts.artifact_is_adopted(adopted, load_project_state(tmp_path), tmp_path)
        replacement = artifacts.register_workspace_artifact(tmp_path, {"artifact_id": "IMG2", "logical_id": "image.test",
            "type": "visual-supplement-image", "revision": "R02", "path_or_url": image.name, "user_approved": True})
        with pytest.raises(ValueError, match="不能替换已自主采用"):
            artifacts.promote_candidate(tmp_path, "image.test", replacement["artifact_id"])
        manifest = artifacts.load_manifest(tmp_path)
        manifest["artifacts"][-1]["status"] = "current"
        with pytest.raises(ValueError, match="不能晋升"):
            artifacts.save_manifest(tmp_path, manifest)
        manifest = artifacts.load_manifest(tmp_path)
        current = next(item for item in manifest["artifacts"] if item["artifact_id"] == "IMG")
        current["quality_evidence"]["observer"] = "Changed after acceptance"
        with pytest.raises(ValueError, match="不能改写"):
            artifacts.save_manifest(tmp_path, manifest)
    assert load_project_state(tmp_path)["user_confirmations"] == before


def test_narrative_cannot_enter_agent_adoption(tmp_path):
    narrative, _ = confirmed_narrative(tmp_path)
    register(tmp_path, "N2", "narrative.package", {**narrative, "revision": "R02"})
    before = copy.deepcopy(load_project_state(tmp_path))
    with pytest.raises(ValueError, match="不允许 agent"):
        artifacts.adopt_initial_agent_artifact(tmp_path, "N2", "does-not-exist.json")
    assert load_project_state(tmp_path) == before


def test_authorized_stale_image_revision_carries_evidence_in_atomic_candidate(tmp_path):
    from test_quality_evidence_consumers import agent_fixture
    _, base = agent_fixture(tmp_path)
    before = copy.deepcopy(load_project_state(tmp_path)['user_confirmations'])
    manifest = artifacts.load_manifest(tmp_path)
    next(a for a in manifest['artifacts'] if a['artifact_id'] == base['artifact_id'])['status'] = 'stale'
    artifacts.save_manifest(tmp_path, manifest)
    rc.open_change(tmp_path, change_id='REBIND', tier='T1', logical_ids=[base['logical_id']],
                   reason='Synthetic revalidation only', authorization_id='SYNTHETIC-WF')
    rc.record_candidate(tmp_path, change_id='REBIND', artifact_id='IMG-R02',
                        logical_id=base['logical_id'], artifact_type=base['type'],
                        candidate_path=base['path_or_url'], revision='R02',
                        artifact_metadata={'quality_evidence': copy.deepcopy(base['quality_evidence'])})
    candidate = next(a for a in artifacts.load_manifest(tmp_path)['artifacts'] if a['artifact_id'] == 'IMG-R02')
    check = report(tmp_path, candidate, name='rebind-check.json', authorization_id='SYNTHETIC-WF',
                   usage={'source_shot_ids': ['S01-010']})
    rc.confirm_agent_candidate(tmp_path, change_id='REBIND', report_path=str(check))
    rc.commit_change(tmp_path, change_id='REBIND')
    adopted = next(a for a in artifacts.load_manifest(tmp_path)['artifacts'] if a['artifact_id'] == 'IMG-R02')
    assert artifacts.artifact_is_adopted(adopted, load_project_state(tmp_path), tmp_path)
    assert adopted['user_approved'] is False
    assert load_project_state(tmp_path)['user_confirmations'] == before


@pytest.mark.parametrize('injected', ['status', 'user_approved', 'agent_acceptance', 'checksum'])
def test_revision_image_metadata_cannot_inject_adoption_fields(tmp_path, injected):
    with pytest.raises(ValueError, match='不能注入采用身份'):
        rc.record_candidate(tmp_path, change_id='UNUSED', artifact_id='IMG-R02',
                            logical_id='image.test', artifact_type='visual-supplement-image',
                            candidate_path='image.png', revision='R02',
                            artifact_metadata={injected: True})


@pytest.mark.parametrize('change_scope', ['later_scene', 'used_scene', 'global_rule', 'timing_source'])
def test_director_revision_preserves_only_unchanged_supplement_scope(tmp_path, change_scope):
    from scripts.core import asset_refs
    from test_quality_evidence_consumers import adopt_agent_fixture_entry, observations

    story, _ = confirmed_narrative(tmp_path)
    board = board_candidate(tmp_path, story)
    later = copy.deepcopy(board['scenes'][0])
    later.update(scene_id='S02', audiovisual_flow='S02-010 Synthetic subsequent action')
    later['shots'][0].update(shot_id='S02-010', source_dialogue_ids=[])
    later['shots'][0]['audio']['dialogue'] = []
    board['scenes'].append(later)
    base = adopt_agent_fixture_entry(tmp_path, register(tmp_path, 'DIR-R01', 'storyboard.package', board,
                                                       ['narrative.package']))
    evidence_file = {'path': base['path_or_url'], 'sha256': base['checksum']}
    for shot_id in ['S01-010', 'S02-010']:
        image = tmp_path / (shot_id + '.png')
        image.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
        evidence = observations(quality.IMAGE_ASPECTS)
        evidence.update(image_sha256=artifacts.digest(image), source_files=[evidence_file],
                        source_shot_ids=[shot_id], view={'resolution': 'original', 'locator': 'synthetic pixel'})
        entry = artifacts.register_workspace_artifact(tmp_path, {
            'artifact_id': shot_id, 'logical_id': f'image.{shot_id}', 'type': 'visual-supplement-image',
            'revision': 'R01', 'path_or_url': image.name, 'depends_on': ['storyboard.package'],
            'quality_evidence': evidence,
        })
        adopt_agent_fixture_entry(tmp_path, entry, usage={'source_shot_ids': [shot_id]})
        assert asset_refs.require_references(tmp_path, [shot_id], [shot_id]) == (True, [])

    before_state = load_project_state(tmp_path)
    old_receipt = (tmp_path / 'agent-S01-010.json').read_bytes()
    new_board = copy.deepcopy(board)
    new_board.update(revision='R02')
    new_board['scenes'][1]['shots'][0]['rhythm'] = 'Later shot revised'
    new_board['timing_basis']['source_type'] = 'Updated description of the non-evaluable later shot budget'
    if change_scope == 'used_scene':
        new_board['scenes'][0]['shots'][0]['rhythm'] = 'Earlier shot also revised'
    elif change_scope == 'global_rule':
        new_board['director_rules']['camera_principle'] = 'Different shared camera rule'
    elif change_scope == 'timing_source':
        new_board['timing_basis'].update(source_path='N.json', source_field='different source basis')

    path = tmp_path / 'DIR-R02.json'
    write_json(path, new_board)
    inspected = rc.inspect_change(tmp_path, ['storyboard.package'], path.name)
    rc.open_change(tmp_path, change_id='V04', tier='T1', logical_ids=['storyboard.package'],
                   reason='Synthetic scoped director revision', authorization_id='SYNTHETIC-WF')
    rc.record_candidate(tmp_path, change_id='V04', artifact_id='DIR-R02', logical_id='storyboard.package',
                        artifact_type='storyboard.package', candidate_path=path.name, revision='R02')
    frozen = rc.show_change(tmp_path)['active_change']['frozen_logical_ids']
    assert inspected['frozen_logical_ids'] == frozen
    assert 'image.S02-010' in frozen
    assert ('image.S01-010' in frozen) == (change_scope != 'later_scene')
    if change_scope == 'later_scene':
        assert asset_refs.require_references(tmp_path, ['S01-010'], ['S01-010']) == (True, [])

    candidate = artifacts.load_manifest(tmp_path)['artifacts'][-1]
    check = report(tmp_path, candidate, name='director-r02-check.json', authorization_id='SYNTHETIC-WF')
    rc.confirm_agent_candidate(tmp_path, change_id='V04', report_path=str(check))
    rc.commit_change(tmp_path, change_id='V04')
    adopted = {item['artifact_id']: item for item in artifacts.load_manifest(tmp_path)['artifacts']}
    assert adopted['DIR-R01']['status'] == 'superseded'
    assert adopted['S02-010']['status'] == 'stale'
    assert not asset_refs.require_references(tmp_path, ['S02-010'], ['S02-010'])[0]
    if change_scope == 'later_scene':
        assert adopted['S01-010']['status'] == 'current'
        assert asset_refs.require_references(tmp_path, ['S01-010'], ['S01-010']) == (True, [])
        assert not asset_refs.require_references(tmp_path, ['S01-010'], ['S02-010'])[0]
        old_board = tmp_path / base['path_or_url']
        old_board.write_bytes(old_board.read_bytes() + b'\n')
        assert not asset_refs.require_references(tmp_path, ['S01-010'], ['S01-010'])[0]
    else:
        assert adopted['S01-010']['status'] == 'stale'
        assert not asset_refs.require_references(tmp_path, ['S01-010'], ['S01-010'])[0]
    assert (tmp_path / 'agent-S01-010.json').read_bytes() == old_receipt
    assert load_project_state(tmp_path)['user_confirmations'] == before_state['user_confirmations']
