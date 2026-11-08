"""Synthetic legacy-state compatibility probes; no historical source data."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core.project_state import load_project_state, validate_project_state


STRING_IDS = [
    'SYNTH-STORY-TEXT', 'SYNTH-STORY-DATA',
    'SYNTH-DIRECTOR-TEXT', 'SYNTH-DIRECTOR-DATA',
]
OBJECTS = [
    {'artifact_id': 'SYNTH-STORY-TEXT', 'logical_id': 'narrative.script',
     'path': 'fixtures/story.txt', 'revision': 'R01'},
    {'artifact_id': 'SYNTH-STORY-DATA', 'logical_id': 'narrative.package',
     'path': 'fixtures/story.json', 'revision': 'R01'},
    {'artifact_id': 'SYNTH-DIRECTOR-TEXT', 'logical_id': 'storyboard.plan',
     'path': 'fixtures/director.txt', 'revision': 'R02'},
    {'artifact_id': 'SYNTH-DIRECTOR-DATA', 'logical_id': 'storyboard.package',
     'path': 'fixtures/director.json', 'revision': 'R02'},
    {'artifact_id': 'SYNTH-ASSET-DATA', 'logical_id': 'visual.asset.manifest',
     'path': 'fixtures/assets.json', 'revision': 'R03'},
]


def write_state(project, selection):
    manage = project / '00_项目管理'
    manage.mkdir(parents=True)
    path = manage / 'project_state.json'
    path.write_text(json.dumps({'schema_version': 'stage-1.0',
                               'current_artifacts': selection}, ensure_ascii=False))
    return path


@pytest.mark.parametrize('selection', [STRING_IDS, OBJECTS], ids=['strings', 'objects'])
def test_legacy_selections_load_into_real_stage_reader_without_rewriting(tmp_path, selection):
    path = write_state(tmp_path, selection)
    before = path.read_bytes()
    expected = [x['artifact_id'] if isinstance(x, dict) else x for x in selection]
    state = load_project_state(tmp_path)
    validate_project_state(state)
    assert state['current_artifacts'] == expected
    summary = cli._state_reading_view(cli._state_summary(cli._state(tmp_path)))
    assert summary['current_artifacts'] == expected
    # Missing evidence is rejected by the real stage source reader, without
    # inventing paths/approvals from the legacy object's descriptive fields.
    manifest = tmp_path / '00_项目管理/artifact_manifest.json'
    manifest.write_text(json.dumps({'artifacts': []}))
    with pytest.raises(cli.StageContextError, match=expected[0]):
        cli._input_snapshot(tmp_path, {}, cli._state(tmp_path))
    assert path.read_bytes() == before
    assert not (tmp_path / cli.CONTEXT_DIR).exists()


def test_same_artifact_id_stays_project_local(tmp_path):
    left, right = tmp_path / 'left', tmp_path / 'right'
    shared = 'SYNTH-SHARED-ID'
    for project in (left, right):
        write_state(project, [shared])
        # Different projects may have the same ID. Neither is an approval.
        manifest = {'artifacts': [{'artifact_id': shared, 'status': 'candidate',
                                  'path_or_url': str(project / 'different-source.json')}]}
        (project / '00_项目管理/artifact_manifest.json').write_text(json.dumps(manifest))
        with pytest.raises(cli.StageContextError, match=shared):
            cli._confirmed_project_inputs(project, cli._state(project))


def test_selected_baseline_does_not_fall_forward_to_unconfirmed_r13(tmp_path):
    # Synthetic baseline/candidate control; no production approval is created.
    old, candidate = 'SYNTH-BASELINE', 'SYNTH-CANDIDATE'
    path = write_state(tmp_path, [old])
    before = path.read_bytes()
    (tmp_path / '00_项目管理/artifact_manifest.json').write_text(json.dumps({
        'artifacts': [{'artifact_id': old, 'status': 'current', 'user_approved': False},
                      {'artifact_id': candidate, 'status': 'candidate', 'user_approved': False}]}))
    with pytest.raises(cli.StageContextError, match=old):
        cli._input_snapshot(tmp_path, {}, cli._state(tmp_path))
    assert cli._state(tmp_path)['current_artifacts'] == [old]
    assert path.read_bytes() == before


def test_missing_explicit_request_has_path_diagnostic_and_cannot_create_preparation(tmp_path):
    write_state(tmp_path, [])
    manage = tmp_path / '00_项目管理'
    (manage / 'project-entry.md').write_text('Synthetic baseline: R12\nSynthetic candidate: R13, unconfirmed\n')
    request = manage / 'project_request.json'
    before = {p.name: p.read_bytes() for p in manage.iterdir()}
    result = subprocess.run([sys.executable, '-m', 'scripts.stage_context_cli', 'prepare',
        '--project', str(tmp_path), '--skill', 'storyboard-planner',
        '--objective', 'synthetic compatibility probe', '--request-json', str(request)],
        text=True, capture_output=True)
    assert result.returncode == 1
    assert 'project_request.json' in result.stderr
    assert '不存在' in result.stderr
    assert {p.name: p.read_bytes() for p in manage.iterdir()} == before
    assert not (tmp_path / cli.CONTEXT_DIR).exists()


def test_permission_denial_is_read_error_not_missing_file(tmp_path, monkeypatch):
    write_state(tmp_path, [])
    request = tmp_path / '00_项目管理/project_request.json'
    request.write_text('{}')
    original_read = Path.read_text

    def denied_read(path, *args, **kwargs):
        if path == request:
            raise PermissionError(13, 'synthetic permission denied', str(path))
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', denied_read)
    args = cli.parser().parse_args(['prepare', '--project', str(tmp_path),
        '--skill', 'storyboard-planner', '--objective', 'synthetic read error',
        '--request-json', str(request)])
    with pytest.raises(cli.StageContextError) as error:
        cli.prepare(args)
    message = str(error.value)
    assert '无法读取请求 JSON' in message and 'permission denied' in message
    assert '不存在' not in message
    assert not (tmp_path / cli.CONTEXT_DIR).exists()


def test_omitted_request_option_keeps_explicit_objective_without_inference(tmp_path):
    project = tmp_path / 'project'
    write_state(project, [])
    manage = project / '00_项目管理'
    (manage / 'project-entry.md').write_text('Synthetic candidate R13 is unconfirmed')
    args = cli.parser().parse_args(['prepare', '--project', str(project),
        '--skill', 'storyboard-planner', '--objective', 'synthetic explicit objective'])
    cli.prepare(args)
    basis = json.loads((project / cli.CONTEXT_DIR / 'task_basis.json').read_text())
    assert basis['request_path'] is None
    assert basis['task_request'] == {}
    assert basis['input_snapshot']['selected_artifact_ids'] == []
    assert cli._state(project)['current_artifacts'] == []
    assert not (manage / 'project_request.json').exists()
