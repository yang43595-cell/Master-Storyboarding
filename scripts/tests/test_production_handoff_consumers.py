"""The normal video and delivery entry points enforce required handoff inputs."""
import json

import pytest

from scripts import canvas_readback
from scripts.core import artifacts, generation_boundaries
from scripts.core.io import read_json, write_json
from scripts.core.project_kickoff import REQUEST_PATH
from scripts.core.visual_readiness import require_visual_readiness
from test_formal_package_submission import register, video_project
from test_quality_evidence_consumers import agent_fixture
from test_visual_readiness import write_request


def prepare_unapproved_asset_fixture(project):
    director = next(entry for entry in artifacts.load_manifest(project)['artifacts']
                    if entry['logical_id'] == 'storyboard.package')
    write_json(project / REQUEST_PATH, {'kickoff': {
        'decision_ref': 'Synthetic fixture only', 'product': {'mode': 'none'},
    }})
    write_request(project, director, ['S01-010'], decision='no_supplement')
    return director


def test_missing_required_asset_blocks_shot_start_and_video_adoption(tmp_path, capsys):
    video = video_project(tmp_path, current_prompt=False)
    director = prepare_unapproved_asset_fixture(tmp_path)
    assert generation_boundaries.main([
        '--project', str(tmp_path), '--storyboard', director['path_or_url'], '--shot-id', 'S01-010',
    ]) == 1
    assert 'AST-CHAR-A' in json.loads(capsys.readouterr().out)['error']

    candidate = register(tmp_path, 'VIDEO-WAIT', 'video.prompt', video, ['storyboard.package'])
    with pytest.raises(ValueError, match='AST-CHAR-A'):
        artifacts.validate_candidate_submission(tmp_path, candidate)


def test_canvas_preflight_rechecks_required_assets_omitted_by_the_draft(tmp_path):
    frozen, _ = agent_fixture(tmp_path)
    prepare_unapproved_asset_fixture(tmp_path)
    with pytest.raises(ValueError, match='AST-CHAR-A'):
        canvas_readback.validate_submission(frozen, tmp_path, preflight=True)


def test_visual_handoff_reports_only_affected_shots_in_one_check(tmp_path):
    video_project(tmp_path, current_prompt=False)
    director = prepare_unapproved_asset_fixture(tmp_path)
    write_request(tmp_path, director, ['S02-010'], request_id='VS-PENDING')
    storyboard = read_json(tmp_path / director['path_or_url'])

    report = require_visual_readiness(tmp_path, storyboard, director, ['S01-010', 'S02-010'],
                                      require_review=True, collect_blockers=True)

    assert report['reviewed_shot_ids'] == ['S01-010']
    assert list(report['blocked_shot_ids']) == ['S02-010']
    assert any('VS-PENDING' in issue for issue in report['blocked_shot_ids']['S02-010'])




def test_additional_planned_scene_output_survives_one_approved_view(tmp_path):
    from scripts.core.production_handoff import _asset_steps

    ast = 'AST-ROOM'
    current = dict(output_id='MASTER', sha256='a' * 64, path_exists=True)
    assets = {ast: {'state': 'needs_image', 'image': {'outputs': [current]}}}
    steps = _asset_steps(tmp_path, {}, {'image_generation': {'scope': 'required_asset_outputs'}},
                         dict(artifact_id='R03', checksum='b' * 64, revision='R03'), assets, {ast: [ast + '@MASTER']}, {},
                         review_targets=[ast + '@MASTER', ast + '@REVERSE'])
    assert len(steps) == 1 and steps[0]['status'] == 'ready' and steps[0]['output_ids'] == [ast + '@REVERSE']




def test_partial_batch_rechecks_only_outstanding_images(tmp_path, monkeypatch):
    from scripts.core import asset_batch_receipt as receipts, production_handoff as handoff
    from test_asset_batch_receipt import _passed_row

    story = dict(artifact_id='N1', checksum='b' * 64, revision='R01')
    fingerprint = 'a' * 64
    targets = ['AST-ROOM@WIDE', 'AST-ROOM@REVERSE']
    rows = [dict(_passed_row(tmp_path, target, f'C{i}'), purpose='场景') for i, target in enumerate(targets)]
    name = fingerprint + '.json'
    write_json(tmp_path / receipts.DIRECT_REQUESTS / name, dict(
        skill='asset-image-assistant', request_sha256=fingerprint, narrative_id='N1',
        narrative_sha256=story['checksum'], targets=targets, image_output_kinds={}))
    write_json(tmp_path / receipts.DIRECT_RECEIPTS / name, dict(request_sha256=fingerprint, items=rows))
    reads = []
    original = receipts.sha256_file
    def count(path):
        reads.append(path.name)
        return original(path)
    monkeypatch.setattr(receipts, 'sha256_file', count)
    def pending():
        return handoff._pending_asset_batches(tmp_path, story, {'AST-ROOM'}, {targets[0]})
    assert pending()[targets[1]]['statuses'] == {'ready_for_review'}
    assert reads == ['C1.png']
    (tmp_path / 'C1.png').write_bytes(b'changed')
    assert pending()[targets[1]]['statuses'] == {'pending'}
