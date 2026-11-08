"""Synthetic regression boundaries only; no production approval or provider use."""
import argparse
import copy
import subprocess

import pytest
from scripts import canvas_readback, stage_context_cli as cli
from scripts.core import artifacts, story_sources
from scripts.core.io import write_json
from test_candidate_test_mode import setup, add
from test_story_paragraph_sources import narrative
from test_stage_context_cli import _method_vault, _synthetic_method_profile, isolated_stage_knowledge


pytestmark = pytest.mark.usefixtures("isolated_stage_knowledge")


def dialogue_record(root, speaker=None):
    write_json(root / 'raw.json', {'text': '不。'})
    row = dict(dialogue_id='DLG-01', text='不。', source_ids=['S'],
        verification=dict(scope='dialogue', dialogue_id='DLG-01', text='不。',
                          method='human_listening', reference='synthetic receipt', reviewer='synthetic reader'))
    if speaker is not None:
        row.update(speaker_id=speaker, speaker_status='verified')
        row['verification']['speaker_id'] = speaker
    return dict(sources=[dict(source_id='S', kind='subtitle_verbatim', text='不。',
        evidence_path='raw.json', evidence_sha256=artifacts.digest(root / 'raw.json'),
        text_pointer='/text', locator='synthetic frame', producer='synthetic reader')], dialogues=[row])


def bind_package(root, record, speaker):
    package = narrative(root)
    package.pop('human_readable_path')
    package['status'] = 'candidate'
    package['asset_requirements'] = []
    package['dialogues'][0].update(speaker_id=speaker, status='draft')
    package['story_paragraphs'][0]['text'] = speaker + '（DLG-01）："不。"'
    write_json(root / 'review.json', record)
    package.update(dialogue_evidence_path='review.json', dialogue_evidence_sha256=artifacts.digest(root / 'review.json'))
    return package


@pytest.mark.parametrize('speaker', ['UNKNOWN', ' unknown ', 'unknown_speaker', 'UNK', '未知', '待确认'])
def test_unknown_identity_cannot_self_attest_as_verified(tmp_path, speaker):
    setup(tmp_path)
    record = dialogue_record(tmp_path, speaker)
    before = copy.deepcopy(record)
    report = story_sources.validate_dialogue_review(record, tmp_path)
    assert report['unresolved_ids'] == ['DLG-01']
    package = bind_package(tmp_path, record, speaker)
    if speaker not in {'UNKNOWN', 'UNK'}:
        with pytest.raises(ValueError, match='schema'):
            story_sources.validate_story_sources(package, project_root=tmp_path)
        assert record == before
        return
    with pytest.raises(ValueError, match='未核'):
        story_sources.validate_story_sources(package, project_root=tmp_path)
    candidate = story_sources.validate_story_sources(package, project_root=tmp_path, candidate_test=True)
    assert candidate['unresolved_dialogue_ids'] == ['DLG-01'] and candidate['production_blocked']
    assert record == before


def test_legacy_timing_is_read_only_declaration_not_verified_authority(tmp_path):
    record = dialogue_record(tmp_path)
    record['dialogues'][0]['verified_timing'] = dict(start=1, end=2, verification_ref='synthetic alignment', reviewer='synthetic reader')
    before = copy.deepcopy(record)
    report = story_sources.validate_dialogue_review(record, tmp_path)
    assert report['unresolved_ids'] == ['DLG-01'] and report['verified_timing_ids'] == []
    assert record == before
    package = bind_package(tmp_path, record, 'CHR-A')
    with pytest.raises(ValueError, match='未核'):
        story_sources.validate_story_sources(package, project_root=tmp_path)
    record['dialogues'][0]['speaker_status'] = 'unresolved'
    with pytest.raises(ValueError, match='时码'):
        story_sources.validate_dialogue_review(record, tmp_path)


def test_verified_named_identity_and_timing_still_work(tmp_path):
    record = dialogue_record(tmp_path, 'CHR-A')
    record['dialogues'][0]['verified_timing'] = dict(start=1, end=2, verification_ref='synthetic alignment', reviewer='synthetic reader')
    report = story_sources.validate_dialogue_review(record, tmp_path)
    assert report['unresolved_ids'] == [] and report['verified_timing_ids'] == ['DLG-01']


def test_unadopted_method_byte_drift_remains_blocked(tmp_path, monkeypatch):
    vault = _method_vault(tmp_path / 'vault')
    _synthetic_method_profile(tmp_path, vault, monkeypatch)
    project = tmp_path / 'project'; project.mkdir()
    request = project / 'request.json'; write_json(request, {'use_story_method': True})
    args = argparse.Namespace(project=str(project), skill='creative-director', objective='synthetic',
        request_json=str(request), vault=str(vault))
    cli.prepare(args)
    method = vault / 'projects/Master-Storyboarding/应用知识/编导/编导方法.md'
    method.write_text(method.read_text() + '\nUnadopted synthetic change.\n')
    with pytest.raises(cli.StageContextError, match='hash'):
        cli.prepare(args)


def test_unreadable_git_remains_a_status_blocker(tmp_path, monkeypatch):
    setup(tmp_path)
    write_json(tmp_path / artifacts.MANIFEST_PATH, {'artifacts': []})
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(128, command)
    monkeypatch.setattr(cli.subprocess, 'run', fail)
    report = cli.status(argparse.Namespace(project=str(tmp_path)))
    assert not report['ok'] and not report['candidate_test_allowed']
    assert any('revision' in value for value in report['blockers'])


def test_submission_rejects_unreviewed_before_payload_processing(tmp_path, monkeypatch):
    setup(tmp_path)
    add(tmp_path, 'N', 'narrative.package', {'status': 'candidate', 'revision': 'R01'})
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError('unreviewed project must fail before payload processing')
    monkeypatch.setattr(canvas_readback, 'target_url', forbidden)
    with pytest.raises(ValueError, match='候选|unreviewed'):
        canvas_readback.validate_submission({}, tmp_path)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
