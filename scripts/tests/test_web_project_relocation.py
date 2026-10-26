"""Synthetic project-copy relocation through existing readers and web contract.

No historical case bytes, network, generation, publication or new registry.
"""
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts import stage_context_cli as stage_reader
from scripts.core import web_content as web
from scripts.core.artifacts import load_manifest, project_path
from scripts.core.project_state import load_project_state, new_project_state
from test_web_content_adapter import BENCHMARK, registry, sources


OLD_ROOT = r'D:\Synthetic\RelocatedFilm'


def source_path(relative, absolute):
    return OLD_ROOT + '\\' + relative.replace('/', '\\') if absolute else relative


def compile_all(root, media):
    outputs = {}
    for stage in ('script', 'director', 'assets', 'prompts', 'white_model', 'storyboard'):
        outputs[stage] = web.compile_content(
            (root / (stage + '.json')).read_bytes(), stage=stage, case_id='01',
            source_script_version=hashlib.sha256(b'synthetic-source-head').hexdigest(),
            expected_version=None, scope='qa', media_registry=media,
            benchmark=BENCHMARK if stage in {'script', 'assets'} else None,
            project_root=root, contract_root=root / 'web-contract')
    return outputs


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('absolute', [False, True], ids=['relative', 'explicit-windows-alias'])
def test_project_copy_keeps_json_confirmations_and_web_request_version(tmp_path, monkeypatch, absolute):
    before = tmp_path / 'before'
    before.mkdir()
    media = registry(before)
    values = sources(before)
    for output in values['assets']['assets'][0]['confirmed_outputs']:
        output['path'] = source_path(output['path'], absolute)
    preview = source_path('fixture/preview.png', absolute)
    media[preview] = media.pop('fixture/preview.png')
    for stage in ('white_model', 'storyboard'):
        values[stage] = {'project_id': 'SYNTHETIC', 'revision': 'R01', 'files': [
            {'path': preview, 'sha256': media[preview]['sha256']} ]}
    for stage in ('director', 'assets'):
        values[stage]['source_narrative'].update(artifact_id='SYNTH-SCRIPT', logical_id='narrative.package', path=source_path('script.json', absolute))
    entries = []
    for stage, value in values.items():
        path = before / (stage + '.json')
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        entries.append({'artifact_id': 'SYNTH-' + stage.upper(), 'logical_id': {'script': 'narrative.package', 'director': 'storyboard.package'}.get(stage, 'synthetic.' + stage),
                        'revision': value['revision'], 'status': 'current', 'user_approved': True,
                        'path_or_url': source_path(path.name, absolute),
                        'checksum': hashlib.sha256(path.read_bytes()).hexdigest()})
    manage = before / '00_项目管理'
    manage.mkdir()
    state = new_project_state()
    state.update(current_artifacts=[a['artifact_id'] for a in entries], user_confirmations=[{
        'scope': 'artifact', 'artifact_ids': [a['artifact_id'] for a in entries],
        'approval_ref': 'synthetic-test-only'}])
    (manage / 'project_state.json').write_text(json.dumps(state), encoding='utf-8')
    (manage / 'artifact_manifest.json').write_text(json.dumps({'artifacts': entries}), encoding='utf-8')
    shutil.copytree(web.CONTRACT, before / 'web-contract')
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({OLD_ROOT: str(before)}))
    snapshot = tree_bytes(before)
    _, original_inputs = stage_reader._confirmed_project_inputs(before, stage_reader._state(before))
    original = compile_all(before, media)
    assert tree_bytes(before) == snapshot

    after = tmp_path / 'after with spaces'
    shutil.copytree(before, after)
    # Preserve the original while making its former location unavailable.
    before.rename(tmp_path / 'retained-original')
    moved_media = copy.deepcopy(media)
    for record in moved_media.values():
        record['localPath'] = str(after / Path(record['localPath']).relative_to(before))
    if absolute:
        with pytest.raises(stage_reader.StageContextError, match='文件不存在'):
            stage_reader._confirmed_project_inputs(after, stage_reader._state(after))
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP', json.dumps({OLD_ROOT: str(after)}))
    _, relocated_inputs = stage_reader._confirmed_project_inputs(after, stage_reader._state(after))
    assert relocated_inputs == original_inputs
    assert load_manifest(after)['artifacts'] == entries
    assert load_project_state(after)['user_confirmations'] == state['user_confirmations']
    for entry in entries:
        assert project_path(after, entry['path_or_url']).is_relative_to(after)
    relocated = compile_all(after, moved_media)
    for stage, result in relocated.items():
        for field in ('request', 'version', 'canonical', 'contractHashes'):
            assert result[field] == original[stage][field]
        assert result['published'] is False and result['remoteReadbackPerformed'] is False
        for item in result['mediaMap']:
            assert Path(item['localPath']).is_relative_to(after)
            assert hashlib.sha256(Path(item['localPath']).read_bytes()).hexdigest() == item['sha256']
    assert tree_bytes(after) == snapshot


@pytest.mark.parametrize('missing', ['source', 'transport'])
def test_missing_media_fails_without_replacement_or_state_writes(tmp_path, missing):
    media = registry(tmp_path)
    value = sources(tmp_path)['assets']
    path = (tmp_path / 'fixture/face.png' if missing == 'source'
            else Path(media['AST-CHAR-A@face']['localPath']))
    path.unlink()  # Only this test's disposable synthetic bytes.
    before = tree_bytes(tmp_path)
    with pytest.raises(web.WebContentError, match='来源不存在|本地字节不存在'):
        web.compile_content(web.canonical(value).encode(), stage='assets', case_id='01',
            source_script_version='a' * 64, expected_version=None, scope='qa',
            media_registry=media, project_root=tmp_path)
    assert tree_bytes(tmp_path) == before


def test_missing_explicit_contract_fails_instead_of_using_default(tmp_path):
    with pytest.raises(web.WebContentError) as error:
        web.validate_web_request({}, contract_root=tmp_path / 'absent-contract')
    assert 'absent-contract' in str(error.value) and 'content-contract.ts' in str(error.value)


def test_missing_node_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-path'))
    with pytest.raises(FileNotFoundError, match='node'):
        web.validate_web_request({})


def test_missing_zod_fails_explicitly_in_isolated_bridge(tmp_path, monkeypatch):
    # Use the real bridge away from any repository Node dependency tree.
    root = tmp_path / 'isolated-validator'
    (root / 'scripts').mkdir(parents=True)
    shutil.copyfile(web.ROOT / 'scripts/validate_web_content.mjs', root / 'scripts/validate_web_content.mjs')
    monkeypatch.setattr(web, 'ROOT', root)
    monkeypatch.setenv('NODE_PATH', str(root / 'absent-node-dependencies'))
    with pytest.raises(web.WebContentError, match='zod'):
        web.validate_web_request({})
