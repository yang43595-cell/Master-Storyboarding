"""Synthetic candidate pipeline; no production examples, approvals or provider calls."""
import argparse
import copy
import json

import pytest
from scripts import stage_context_cli as cli
from scripts.canvas_readback import validate_submission
from scripts.core import artifacts, candidate_test as mode, revision_control, story_sources
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, new_project_state, write_project_state
from test_stage_context_cli import _method_vault, _record_methods
from test_story_paragraph_sources import narrative
from test_director_compact import compact_package


def setup(project):
    project.mkdir(exist_ok=True)
    state = new_project_state(current_stage='creative-director')
    write_project_state(project, state)
    mode.configure(project, 'synthetic-run', 'synthetic user: candidate text test only', [])
    return project


def add(project, ident, logical, payload, dependencies=()):
    path = project / (ident + '.json')
    write_json(path, payload)
    artifacts.register_workspace_artifact(project, dict(artifact_id=ident, logical_id=logical,
        type=logical, path_or_url=path.name, revision=payload.get('revision', 'R01'),
        depends_on=list(dependencies)))
    selected = mode.context(project)['input_artifact_ids'] + [ident]
    mode.configure(project, 'synthetic-run', 'synthetic user: candidate text test only', selected)
    return path


def sources(project):
    n = narrative(project)
    n.pop('human_readable_path')
    n['status'] = 'candidate'
    n['dialogues'][0]['status'] = 'draft'
    for item in n['asset_requirements']:
        item.update(image_decision='需要制作', image_reason='synthetic identity control')
    write_json(project/'raw.json', {'text': '不。'})
    record = {'sources': [{'source_id': 'ASR-1', 'kind': 'asr_hypothesis', 'text': '不。',
              'evidence_path': 'raw.json', 'evidence_sha256': artifacts.digest(project/'raw.json'),
              'text_pointer': '/text', 'locator': 'synthetic segment', 'producer': 'synthetic ASR'}],
              'dialogues': [{'dialogue_id': 'DLG-01', 'text': '不。', 'source_ids': ['ASR-1']}]}
    write_json(project/'review.json', record)
    n.update(dialogue_evidence_path='review.json', dialogue_evidence_sha256=artifacts.digest(project/'review.json'))
    b = compact_package()
    b.pop('human_readable_path')
    b['source_narrative'].update(artifact_id='N', path='N.json')
    return n, b


def prepared(project, vault, skill):
    args = argparse.Namespace(project=str(project), skill=skill, objective='synthetic candidate chain',
        request_json=None, vault=str(vault), actor_id='test-agent', task_id='test-method-binding', candidate_test=True)
    cli.prepare(args)
    _record_methods(project, skill, vault)
    result = cli.ensure_prepared(args)
    assert result['ok'] and result['candidate_checks_passed']
    assert result['review_status'] == 'unreviewed' and result['production_blocked'] is True
    return args


def test_candidate_text_chain_never_becomes_production(tmp_path):
    project = setup(tmp_path/'isolated')
    vault = _method_vault(tmp_path/'vault')
    prepared(project, vault, 'creative-director')
    n, b = sources(project)
    report = story_sources.validate_story_sources(n, project_root=project, candidate_test=True)
    assert report['unresolved_dialogue_ids'] == ['DLG-01']
    assert report['production_blocked'] and report['review_status'] == 'unreviewed'
    add(project, 'N', 'narrative.package', n)
    args = argparse.Namespace(project=str(project), to_skill='storyboard-planner', candidate_test=True)
    cli.handoff(args)
    card = (project/cli.CONTEXT_DIR/'next_stage_handoff.md').read_text()
    assert 'unreviewed' in card and '已获用户确认' not in card and '已确认产物' not in card
    prepared(project, vault, 'storyboard-planner')
    story_sources.validate_story_sources(n, b, project_root=project, candidate_test=True)
    add(project, 'B', 'storyboard.package', b, ['narrative.package'])
    # Asset planning can prepare from the same narrative without pretending images exist.
    prepared(project, vault, 'asset-image-assistant')
    cli.handoff(argparse.Namespace(project=str(project), to_skill='voiceover-to-video-script', candidate_test=True))
    # Video directly consumes sources, never revives its retired preparation workflow.
    add(project, 'P', 'video.prompt', {'status': 'candidate', 'revision': 'R01',
        'source_storyboard': {'revision': 'R02', 'path': 'B.json'},
        'text': 'synthetic prompt candidate', 'open_questions': ['no reviewed image; generation blocked']}, ['storyboard.package'])
    assert [a['artifact_id'] for a in mode.resolve_inputs(project)] == ['N', 'B', 'P']
    state = load_project_state(project)
    assert state['current_artifacts'] == state['user_confirmations'] == []
    for entry in artifacts.load_manifest(project)['artifacts']:
        assert entry['status'] == 'candidate' and entry['user_approved'] is False
        assert entry['review_status'] == 'unreviewed'
        assert not artifacts.artifact_is_confirmed(entry, state, project)
    assert 'verification' not in json.loads((project/'review.json').read_text())['dialogues'][0]


def test_default_entrypoints_stay_strict(tmp_path):
    project = setup(tmp_path)
    n, _ = sources(project)
    add(project, 'N', 'narrative.package', n)
    with pytest.raises(ValueError, match='未核'):
        story_sources.validate_story_sources(n, project_root=project)
    with pytest.raises(ValueError, match='--candidate-test'):
        cli.handoff(argparse.Namespace(project=str(project), to_skill='storyboard-planner'))
    with pytest.raises(ValueError, match='--candidate-test'):
        cli.ensure_prepared(argparse.Namespace(project=str(project), skill='storyboard-planner'))
    with pytest.raises(ValueError, match='已确认 current'):
        cli._confirmed_project_inputs(project, {**load_project_state(project), 'current_artifacts': ['N']})


@pytest.mark.parametrize('mutation', ['raw_bytes', 'evidence_bytes', 'dialogue', 'missing_asset'])
def test_test_mode_does_not_hide_structural_or_evidence_errors(tmp_path, mutation):
    project = setup(tmp_path)
    n, b = sources(project)
    if mutation == 'raw_bytes': write_json(project/'raw.json', {'text': 'different'})
    if mutation == 'evidence_bytes': write_json(project/'review.json', {})
    if mutation == 'dialogue': b['scenes'][0]['shots'][0]['audio']['dialogue'][0]['text'] = '改写'
    if mutation == 'missing_asset': b['scenes'][0]['shots'][0]['source_asset_requirement_ids'] = ['AST-MISSING']
    with pytest.raises(ValueError):
        story_sources.validate_story_sources(n, b, project_root=project, candidate_test=True)


@pytest.mark.parametrize('mutation', ['revision', 'path', 'id', 'cycle', 'removed_mode'])
def test_test_selection_checks_exact_dependencies(tmp_path, mutation):
    project = setup(tmp_path)
    n, b = sources(project)
    add(project, 'N', 'narrative.package', n)
    add(project, 'B', 'storyboard.package', b, ['narrative.package'])
    if mutation == 'revision': b['source_narrative']['revision'] = 'R99'
    if mutation == 'path': b['source_narrative']['path'] = 'wrong.json'
    if mutation == 'id': b['source_narrative']['artifact_id'] = 'WRONG'
    if mutation == 'cycle':
        manifest = artifacts.load_manifest(project)
        manifest['artifacts'][0]['depends_on'] = ['storyboard.package']
        artifacts.save_manifest(project, manifest)
    if mutation == 'removed_mode':
        state = load_project_state(project); state.pop('candidate_test'); write_project_state(project, state)
    write_json(project/'B.json', b)
    with pytest.raises(ValueError): mode.resolve_inputs(project)


@pytest.mark.parametrize('action', ['promote', 'confirm', 'commit', 'paid', 'submit'])
def test_production_exits_reject_even_after_mode_removed(tmp_path, action):
    project = setup(tmp_path)
    add(project, 'N', 'narrative.package', {'status': 'candidate', 'revision': 'R01'})
    state = load_project_state(project); state.pop('candidate_test'); write_project_state(project, state)
    calls = {
        'promote': lambda: artifacts.promote_candidate(project, 'narrative.package', 'N'),
        'confirm': lambda: revision_control.confirm_candidate(project, change_id='missing', approval_ref='fake'),
        'commit': lambda: revision_control.commit_change(project, change_id='missing'),
        'paid': lambda: revision_control.record_paid_approval(project, change_id='missing', paid_action_id='P', approval_ref='fake', targets=['N'], quantity=1),
        'submit': lambda: validate_submission({}, project),
    }
    with pytest.raises(ValueError, match='unreviewed'): calls[action]()


def test_prepared_input_bytes_and_mode_are_rechecked(tmp_path):
    project = setup(tmp_path/'isolated'); vault = _method_vault(tmp_path/'vault')
    add(project, 'N', 'narrative.package', {'status': 'candidate', 'revision': 'R01'})
    args = prepared(project, vault, 'storyboard-planner')
    write_json(project/'N.json', {'status': 'candidate', 'revision': 'R01', 'changed': True})
    with pytest.raises(ValueError, match='来源'): cli.ensure_prepared(args)


def test_cannot_configure_production_project_or_copy_test_authority(tmp_path):
    project = tmp_path/'production'; project.mkdir()
    state = new_project_state(); state['user_confirmations'] = [{'artifact_ids': ['A']}]
    write_project_state(project, state)
    with pytest.raises(ValueError): mode.configure(project, 'run', 'batch request', [])
    assert 'candidate_test' not in load_project_state(project)
    other = setup(tmp_path/'isolated')
    copied = load_project_state(other)
    copied['candidate_test']['project_path'] = str(project)
    write_json(other/'00_项目管理/project_state.json', copied)
    with pytest.raises(ValueError, match='另一项目'): mode.context(other)


@pytest.mark.parametrize('method', ['submit', 'submit_imagegen'])
def test_project_image_submission_rejected_before_provider_or_job_mutation(tmp_path, method):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from scripts.visual_asset_library import Library
    project = setup(tmp_path)
    job = {'project_id': 'synthetic-project', 'steps': [{'status': 'waiting'}]}
    lib = Library.__new__(Library)
    lib.lock = nullcontext()
    lib.operation_job = lambda *args: job
    lib.projects = SimpleNamespace(get=lambda ident: {'path': str(project)})
    before = copy.deepcopy(job)
    with pytest.raises(ValueError, match='unreviewed'):
        getattr(lib, method)({'id': 'job', 'index': 0, 'authorize': True})
    assert job == before


def test_candidate_paths_cannot_escape_isolation(tmp_path):
    project = setup(tmp_path/'isolated')
    write_json(tmp_path/'outside.json', {'status': 'candidate', 'revision': 'R01'})
    # Even forged manifest paths must be rejected at consumption time.
    item = dict(artifact_id='N', logical_id='narrative.package', path_or_url='../outside.json',
                type='narrative.package', status='candidate', revision='R01', user_approved=False,
                candidate_test='synthetic-run', production_blocked=True, review_status='unreviewed')
    artifacts.save_manifest(project, {'artifacts': [item]})
    state = load_project_state(project); state['candidate_test']['input_artifact_ids'] = ['N']
    write_project_state(project, state)
    with pytest.raises(ValueError, match='越出'): mode.resolve_inputs(project)


def test_method_reading_is_still_required(tmp_path):
    project = setup(tmp_path/'isolated'); vault = _method_vault(tmp_path/'vault')
    args = argparse.Namespace(project=str(project), skill='storyboard-planner', objective='synthetic',
        request_json=None, vault=str(vault), actor_id='test-agent', task_id='test-method-binding', candidate_test=True)
    cli.prepare(args)
    result = cli.ensure_prepared(args)
    assert result['ok'] is False and result['candidate_checks_passed'] is False
    assert result['production_blocked'] is True


def test_handoff_candidate_bytes_are_bound_without_confirmation_checksum(tmp_path):
    project = setup(tmp_path)
    add(project, 'N', 'narrative.package', {'status': 'candidate', 'revision': 'R01'})
    cli.handoff(argparse.Namespace(project=str(project), to_skill='storyboard-planner', candidate_test=True))
    assert 'checksum' not in artifacts.load_manifest(project)['artifacts'][0]
    write_json(project/'N.json', {'status': 'candidate', 'revision': 'R01', 'text': 'changed'})
    with pytest.raises(ValueError, match='字节'):
        cli._current_handoff_text(project, 'storyboard-planner')
