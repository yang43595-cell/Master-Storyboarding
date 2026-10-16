"""Adoption, invalidation and task selection must follow the same active branch."""
import json
import pytest
from scripts.core import artifacts, revision_control
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, new_project_state, write_project_state


def entry(artifact_id, logical_id, status="current", dependencies=()):
    return {"artifact_id": artifact_id, "logical_id": logical_id, "status": status,
            "depends_on": list(dependencies)}


def test_dependency_graph_ignores_history_and_prefers_current_over_stale():
    manifest = {"artifacts": [
        entry("B-OLD", "board", "superseded", ["source.old"]),
        entry("B-STALE", "board", "stale", ["source.old"]),
        entry("B-REJECTED", "board", "rejected", ["source.old"]),
        entry("B-CANDIDATE", "board", "candidate", ["source.old"]),
        entry("B-CURRENT", "board", dependencies=["source.new"]),
        entry("VIDEO", "video", dependencies=["board"]),
    ]}
    assert artifacts.dependency_closure(manifest, {"source.old"}) == {"source.old"}
    assert revision_control._dependency_closure(manifest, ["source.old"]) == ["source.old"]
    assert artifacts.mark_impacted_artifacts_stale(manifest, {"source.old"}, "old source", "R02").stale_ids == set()
    assert artifacts.dependency_closure(manifest, {"source.new"}) == {"source.new", "board", "video"}


def test_stale_adopted_version_still_connects_current_downstream():
    manifest = {"artifacts": [
        entry("BOARD", "board", "stale", ["source"]),
        entry("VIDEO", "video", dependencies=["board"]),
        entry("UNUSED", "unused", "candidate", ["source"]),
    ]}
    assert revision_control._dependency_closure(manifest, ["source"]) == ["board", "source", "video"]
    report = artifacts.mark_impacted_artifacts_stale(manifest, {"source"}, "changed", "R02")
    assert report.stale_ids == {"video"}
    assert manifest["artifacts"][-1]["status"] == "candidate"


def test_revision_inherits_its_baseline_and_replaces_only_selected_ids(tmp_path):
    entries = [
        entry("B-R01", "board", "superseded", ["source.old"]),
        entry("B-R02", "board", dependencies=["source.new"]),
        entry("VIDEO", "video", dependencies=["board"]),
        entry("UNSELECTED", "unrelated"),
    ]
    for item in entries:
        path = tmp_path / (item["artifact_id"] + ".md")
        path.write_text(item["artifact_id"], encoding="utf-8")
        item.update(path_or_url=path.name, type="markdown", revision="R02",
                    checksum=artifacts.digest(path), user_approved=True)
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": entries})
    state = new_project_state(current_stage="storyboard-planner")
    state.update(current_artifacts=["B-R02", "VIDEO"],
                 user_confirmations=[{"artifact_ids": ["B-R02", "VIDEO"]}])
    write_project_state(tmp_path, state)
    candidate = tmp_path / "R03.md"
    candidate.write_text("revised board", encoding="utf-8")
    revision_control.open_change(tmp_path, change_id="REV-3", tier="T1", logical_ids=["board"], reason="revise")
    revision_control.confirm_impact(tmp_path, change_id="REV-3", approval_ref="user:impact")
    revision_control.record_candidate(tmp_path, change_id="REV-3", artifact_id="B-R03", logical_id="board",
        artifact_type="markdown", candidate_path=candidate.name, revision="R03")
    recorded = artifacts.load_manifest(tmp_path)["artifacts"][-1]
    assert recorded["depends_on"] == ["source.new"]
    revision_control.record_review(tmp_path, change_id="REV-3", verdict="approved", report_ref="self-check")
    revision_control.confirm_candidate(tmp_path, change_id="REV-3", approval_ref="user:adopt")
    revision_control.commit_change(tmp_path, change_id="REV-3")
    current = load_project_state(tmp_path)
    assert current["current_artifacts"] == ["B-R03"]
    assert current["user_confirmations"][0] == state["user_confirmations"][0]
    markdown = (tmp_path / "00_项目管理/project_state.md").read_text(encoding="utf-8")
    assert "当前有效产物：1" in markdown
    assert f"用户确认：{len(current['user_confirmations'])}" in markdown


def test_legacy_multi_target_change_replaces_only_the_adopted_logical_id(tmp_path):
    entries = [entry("BOARD", "board"), entry("OTHER", "other")]
    for item in entries:
        path = tmp_path / (item["artifact_id"] + ".md")
        path.write_text(item["artifact_id"], encoding="utf-8")
        item.update(path_or_url=path.name, type="markdown", revision="R01",
                    checksum=artifacts.digest(path), user_approved=True)
    write_json(tmp_path / artifacts.MANIFEST_PATH, {"artifacts": entries})
    state = new_project_state()
    state["current_artifacts"] = ["BOARD", "OTHER"]
    write_project_state(tmp_path, state)
    revision_control.open_change(tmp_path, change_id="LEGACY", tier="T1", logical_ids=["board"], reason="legacy import")
    # New changes have one target. Older persisted records may contain several.
    control = revision_control.show_change(tmp_path)
    control["active_change"]["targets"].append({
        "logical_id": "other", "artifact_id": "OTHER", "revision": "R01", "checksum": entries[1]["checksum"],
    })
    write_json(tmp_path / revision_control.CONTROL_RELATIVE, control)
    revision_control.confirm_impact(tmp_path, change_id="LEGACY", approval_ref="user:legacy-impact")
    candidate = tmp_path / "BOARD-R02.md"
    candidate.write_text("revised board", encoding="utf-8")
    revision_control.record_candidate(tmp_path, change_id="LEGACY", artifact_id="BOARD-R02", logical_id="board",
        artifact_type="markdown", candidate_path=candidate.name, revision="R02")
    revision_control.record_review(tmp_path, change_id="LEGACY", verdict="approved", report_ref="self-check")
    revision_control.confirm_candidate(tmp_path, change_id="LEGACY", approval_ref="user:board-only")
    revision_control.commit_change(tmp_path, change_id="LEGACY")
    assert load_project_state(tmp_path)["current_artifacts"] == ["BOARD-R02", "OTHER"]
    other = next(item for item in artifacts.load_manifest(tmp_path)["artifacts"] if item["artifact_id"] == "OTHER")
    assert other["status"] == "current"


def test_manifest_validity_change_syncs_selection_without_creating_state(tmp_path):
    manifest = {"artifacts": [entry("OLD", "old"), entry("OTHER", "other")]}
    artifacts.save_manifest(tmp_path, manifest)
    assert not (tmp_path / "00_项目管理/project_state.json").exists()
    state = new_project_state()
    state.update(current_artifacts=["OLD"], user_confirmations=[{"artifact_ids": ["OLD"]}])
    write_project_state(tmp_path, state)
    path = tmp_path / "00_项目管理/project_state.json"
    write_json(path, {**state, "legacy_note": "preserve unrelated legacy facts"})
    manifest["artifacts"][0]["status"] = "stale"
    artifacts.save_manifest(tmp_path, manifest)
    assert load_project_state(tmp_path)["current_artifacts"] == []
    assert load_project_state(tmp_path)["user_confirmations"] == state["user_confirmations"]
    assert json.loads(path.read_text(encoding="utf-8"))["legacy_note"] == "preserve unrelated legacy facts"
    assert "当前有效产物：0" in (tmp_path / "00_项目管理/project_state.md").read_text(encoding="utf-8")


def test_interrupted_manifest_and_state_transaction_recovers_the_same_valid_selection(tmp_path):
    manifest = {"artifacts": [entry("OLD", "old")]}
    artifacts.save_manifest(tmp_path, manifest)
    state = new_project_state()
    state["current_artifacts"] = ["OLD"]
    write_project_state(tmp_path, state)
    manifest["artifacts"][0]["status"] = "stale"
    manifest["artifacts"].append(entry("UNSELECTED", "other"))
    with pytest.raises(RuntimeError, match="fault injection"):
        revision_control._commit_json_transaction_unlocked(tmp_path, {
            tmp_path / artifacts.MANIFEST_PATH: manifest,
            tmp_path / "00_项目管理/project_state.json": state,
        }, reason="invalidate", fail_after=1)
    assert load_project_state(tmp_path)["current_artifacts"] == ["OLD"]
    assert revision_control.recover_transactions(tmp_path)[0]["state"] == "committed"
    assert load_project_state(tmp_path)["current_artifacts"] == []
    assert "当前有效产物：0" in (tmp_path / "00_项目管理/project_state.md").read_text(encoding="utf-8")


def test_explicit_project_relocation_reuses_paths_without_editing_receipts(tmp_path, monkeypatch):
    from scripts.core import asset_refs
    source = r'C:\Users\Owner\Desktop\film'
    photo = tmp_path / '04_资产/master.png'
    receipt = tmp_path / '00_项目管理/confirmation.json'
    photo.parent.mkdir(); photo.write_bytes(b'path fixture, not a real asset')
    receipt.parent.mkdir(); receipt.write_text('{"existing_confirmation": true}', encoding='utf-8')
    original = receipt.read_bytes()
    raw_photo = source + r'\04_资产\master.png'
    assert artifacts.project_path(tmp_path, raw_photo) != photo
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({source: str(tmp_path)}))
    assert artifacts.project_path(tmp_path, raw_photo) == photo
    assert artifacts.project_path(tmp_path, '/' + raw_photo.replace('\\', '/')) == photo
    assert asset_refs._image_path(tmp_path, raw_photo) == photo
    assert asset_refs._confirmation_file(tmp_path, source + r'\00_项目管理\confirmation.json#existing') == (receipt, 'existing')
    assert receipt.read_bytes() == original
    # The same project name at an unconfigured location never acts as an alias.
    assert artifacts.project_path(tmp_path, r'D:\Other\film\04_资产\master.png') != photo
    # Explicit absolute references keep their destination even across callers.
    assert artifacts.project_path(tmp_path / 'another-project', raw_photo) == photo
    with pytest.raises(ValueError, match='escapes'):
        artifacts.project_path(tmp_path, source + r'\..\outside.png')


def test_project_relative_windows_separators_are_portable(tmp_path):
    assert artifacts.project_path(tmp_path, r'04_资产\master.png') == tmp_path / '04_资产/master.png'


def test_image_receipts_bind_new_bytes_without_inventing_legacy_evidence(tmp_path):
    path = tmp_path / 'image.png'
    path.write_bytes(b'original image fixture')
    legacy = {'id': 'older-approval'}
    receipt = {'image_sha256': artifacts.digest(path)}
    artifacts.verify_image_confirmation(path, receipt)
    path.write_bytes(b'changed image fixture')
    with pytest.raises(ValueError, match='图片内容已变化'):
        artifacts.verify_image_confirmation(path, receipt)
    artifacts.verify_image_confirmation(path, legacy)
    assert legacy == {'id': 'older-approval'}
    for invalid in (None, '', 'not-a-hash'):
        with pytest.raises(ValueError, match='图片内容已变化'):
            artifacts.verify_image_confirmation(path, {'image_sha256': invalid})


def test_revision_inspection_and_candidates_use_shared_relocated_root(tmp_path, monkeypatch):
    old_root = r'C:\Users\owner\Desktop\film'
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({old_root: str(tmp_path)}))
    source = tmp_path / '03_导演/R01.md'
    source.parent.mkdir()
    source.write_text('原有镜头\n', encoding='utf-8')
    candidate = tmp_path / '03_导演/R02.md'
    candidate.write_bytes(source.read_bytes())
    item = entry('B1', 'storyboard.package')
    item['path_or_url'] = old_root + r'\03_导演\R01.md'
    write_json(tmp_path / '00_项目管理/artifact_manifest.json', {'artifacts': [item]})
    result = revision_control.inspect_change(tmp_path, ['storyboard.package'], candidate=r'03_导演\R02.md')
    assert result['tier'] == 'T0'
    assert result['frozen_logical_ids'] == []
    assert revision_control._within_project(tmp_path, old_root + r'\03_导演\R02.md')[0] == candidate
    outside = tmp_path.parent / 'outside.md'
    with pytest.raises(revision_control.RevisionControlError, match='位于项目内'):
        revision_control._destination_within_project(tmp_path, outside)


def test_promotion_refuses_legacy_shared_logical_id_without_mutating(tmp_path):
    entries = [entry('DOOR', 'visual-supplement.image'), entry('CAT', 'visual-supplement.image'),
               entry('NEW-CAT', 'visual-supplement.image', 'candidate')]
    path = tmp_path / '00_项目管理/artifact_manifest.json'
    write_json(path, {'artifacts': entries})
    before = path.read_bytes()
    with pytest.raises(ValueError, match='多个'):
        artifacts.promote_candidate(tmp_path, 'visual-supplement.image', 'NEW-CAT')
    assert path.read_bytes() == before


def test_exact_supplement_reference_checks_receipt_and_freeze(tmp_path):
    from scripts.core.asset_refs import require_references
    from PIL import Image
    image = tmp_path / 'frame.png'
    Image.new('RGB', (16, 16), 'blue').save(image)
    digest = artifacts.digest(image)
    item = entry('FRAME-R01', 'visual-supplement.image.bridge')
    item.update(type='visual-supplement-image', path_or_url=image.name, checksum=digest,
                revision='R01', user_approved=True, confirmation_ref='receipt.json')
    receipt = {'scope': 'artifact', 'artifact_ids': ['FRAME-R01'],
               'images': [{'artifact_id': 'FRAME-R01', 'path': image.name, 'sha256': digest}]}
    write_json(tmp_path / 'receipt.json', receipt)
    write_json(tmp_path / '00_项目管理/artifact_manifest.json', {'artifacts': [item]})
    state = new_project_state()
    state.update(current_artifacts=['FRAME-R01'], user_confirmations=[receipt])
    write_project_state(tmp_path, state)
    assert require_references(tmp_path, ['FRAME-R01'])[0]
    assert not require_references(tmp_path, [])[0]
    receipt['images'][0]['usage'] = {'source_shot_ids': ['S04-010']}
    write_json(tmp_path / 'receipt.json', receipt)
    assert require_references(tmp_path, ['FRAME-R01'], ['S04-010'])[0]
    assert not require_references(tmp_path, ['FRAME-R01'], ['S06-010'])[0]
    receipt['images'][0]['sha256'] = '0' * 64
    write_json(tmp_path / 'receipt.json', receipt)
    assert not require_references(tmp_path, ['FRAME-R01'])[0]
    receipt['images'][0]['sha256'] = digest
    write_json(tmp_path / 'receipt.json', receipt)
    revision_control.open_change(tmp_path, change_id='REWORK', tier='T1',
                                logical_ids=[item['logical_id']], reason='fixture rework')
    assert not require_references(tmp_path, ['FRAME-R01'])[0]
    revision_control.cancel_change(tmp_path, change_id='REWORK', reason='fixture cancelled')
    assert require_references(tmp_path, ['FRAME-R01'])[0]
