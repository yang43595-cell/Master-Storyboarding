"""The normal video and delivery entry points enforce required handoff inputs."""
import json

import pytest

from scripts import canvas_readback, stage_context_cli
from scripts.core import artifacts, generation_boundaries
from scripts.core.io import read_json, write_json
from scripts.core.project_kickoff import REQUEST_PATH
from scripts.core.visual_readiness import require_visual_readiness
from scripts.visual_asset_choices import RELATIVE as ASSET_CHOICES
from test_formal_package_submission import register, video_project
from test_production_handoff import authorize, kickoff
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


@pytest.mark.parametrize('problem', ['pending_supplement', 'invalid_request', 'unreadable_asset_choices'])
def test_partial_video_handoff_agrees_with_shot_preflight(tmp_path, capsys, problem):
    video_project(tmp_path)
    kickoff(tmp_path)
    authorize(tmp_path, 'voiceover-to-video-script')
    manifest = artifacts.load_manifest(tmp_path)
    director = next(entry for entry in manifest['artifacts'] if entry['artifact_id'] == 'B')
    prompt = next(entry for entry in manifest['artifacts'] if entry['artifact_id'] == 'P-OLD')
    director_path, prompt_path = (tmp_path / entry['path_or_url'] for entry in (director, prompt))
    storyboard, partial = read_json(director_path), read_json(prompt_path)
    storyboard['scenes'][0]['shots'][0]['source_asset_requirement_ids'] = []
    if problem != 'unreadable_asset_choices':
        storyboard['scenes'][1]['shots'][0]['source_asset_requirement_ids'] = []
    partial['generation_segments'] = partial['generation_segments'][:1]
    write_json(director_path, storyboard)
    write_json(prompt_path, partial)
    director['checksum'] = artifacts.digest(director_path)
    prompt['checksum'] = artifacts.digest(prompt_path)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    write_request(tmp_path, director, ['S01-010', 'S02-010'], decision='no_supplement')

    if problem == 'unreadable_asset_choices':
        choices = tmp_path / ASSET_CHOICES
        choices.parent.mkdir(exist_ok=True)
        choices.write_text('{', encoding='utf-8')
    else:
        path = write_request(tmp_path, director, ['S02-010'], request_id='VS-B')
        if problem == 'invalid_request':
            request = read_json(path)
            request['max_images'] = 'invalid'
            write_json(path, request)

    command = ['--project', str(tmp_path)]
    assert stage_context_cli.main(['handoff', *command]) == 0
    report = json.loads(capsys.readouterr().out)
    assert any(row['action'] == 'canvas' and row['segment_ids'] == ['G-01'] for row in report['next_actions'])
    assert not any(row['action'] == 'video' for row in report['next_actions'])
    waiting = next(row for row in report['steps'] if row['action'] == 'video' and row['status'] == 'waiting')
    assert list(waiting['blockers']) == ['S02-010']

    preflight = [*command, '--storyboard', director['path_or_url'], '--shot-id']
    assert generation_boundaries.main([*preflight, 'S01-010']) == 0
    capsys.readouterr()
    assert generation_boundaries.main([*preflight, 'S02-010']) == 1
    error = json.loads(capsys.readouterr().out)['error']
    assert ('Expecting property name' if problem == 'unreadable_asset_choices' else 'VS-B') in error
