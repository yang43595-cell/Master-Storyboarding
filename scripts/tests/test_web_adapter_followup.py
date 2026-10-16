"""Minimal adapter regressions from independent review; no remote writes."""
import copy
import hashlib
import json
import subprocess
from pathlib import Path
import pytest
from scripts.core.web_content import WebContentError, CONTRACT, compile_content, canonical
from test_web_content_adapter import registry, sources, compile_fixture, PNG


def test_asset_registry_cannot_replace_canonical_output_bytes(tmp_path):
    media=registry(tmp_path); source=sources(tmp_path)['assets']
    original=tmp_path/'fixture/face.png';original.parent.mkdir(exist_ok=True);original.write_bytes(b'other actual original')
    with pytest.raises(WebContentError): compile_fixture('assets',source,media)


def test_empty_native_identity_is_rejected(tmp_path):
    media=registry(tmp_path); source=sources(tmp_path)['prompts']
    source['drafts'][0]['ordered_media']=[{}]
    with pytest.raises(WebContentError):compile_fixture('prompts',source,media)


def two_references(tmp_path):
    media=registry(tmp_path);source=sources(tmp_path)['prompts'];media['ACTION@R01']=copy.deepcopy(media['video'])
    row=source['drafts'][0]
    row['ordered_media'].append({'node_id':'video-node','media_id':'video-media'})
    row['reference_checks'].append({'artifact_id':'ACTION','revision':'R01','sha256':media['ACTION@R01']['sha256'],'roles':['action']})
    return source,media


def test_reordering_preserves_media_anchor_source_identity(tmp_path):
    source,media=two_references(tmp_path)
    first=compile_fixture('prompts',source,media)['request']['content']['blocks'][0]['media']
    source['drafts'][0]['ordered_media'].reverse();source['drafts'][0]['reference_checks'].reverse()
    second=compile_fixture('prompts',source,media)['request']['content']['blocks'][0]['media']
    assert {m['id']:m['sha256'] for m in first}=={m['id']:m['sha256'] for m in second}
    assert [m['id'] for m in second]==list(reversed([m['id'] for m in first]))


def test_body_missing_registered_dialogue_is_rejected(tmp_path):
    media=registry(tmp_path);source=sources(tmp_path)['script']
    source['story_paragraphs'][0]['text']='人物拒绝的概述，没有逐句对白。'
    with pytest.raises(ValueError,match='对白'):compile_fixture('script',source,media)


def test_missing_record_and_readback_ids_cannot_match_none(tmp_path):
    media=registry(tmp_path);source=sources(tmp_path)['script']
    media['poster'].pop('id');media['poster']['readback'].pop('mediaVerified')
    with pytest.raises(WebContentError):compile_fixture('script',source,media)


def test_legacy_v1_returns_explained_error_not_keyerror(tmp_path):
    media=registry(tmp_path)
    from test_visual_asset_projects import fixture
    # A real existing legacy fixture has valid v1 fields, not an invented invalid object.
    _,project,_=fixture(tmp_path/'legacy')
    source=json.loads((Path(project['path'])/'narrative.json').read_text())
    with pytest.raises(WebContentError,match='2.0|旧版|legacy'):
        compile_fixture('script',source,media)


@pytest.mark.parametrize("mixed_ids", [False, True])
def test_actual_script_presentation_main_body_retains_dialogue(tmp_path, mixed_ids):
    media=registry(tmp_path);source=sources(tmp_path)['script']
    if mixed_ids:
        text=source['story_paragraphs'][0]['text']
        source['story_paragraphs'][0]['text']='无对白的第一段。'
        source['story_paragraphs'].append({'paragraph_id':'STP-EXTRA','text':text})
        source['dialogues'][0]['source_paragraph_id']='STP-EXTRA'
    content=compile_fixture('script',source,media)['request']['content']
    assert all(any(b['id']==p['paragraph_id'] for b in content['blocks']) for p in source['story_paragraphs'])
    script=CONTRACT/'review-static/script-template.js'
    code="const fs=require('node:fs'),vm=require('node:vm');const c={module:{exports:{}}};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),c);const p=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(c.module.exports.fromPackage(p)));"
    result=subprocess.run(['node','-e',code,str(script)],input=json.dumps(content),text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    view=json.loads(result.stdout)
    body='\n'.join('\n'.join(s['lines']) for s in view['scenes'])
    assert source['dialogues'][0]['text'] in body
    for paragraph in source['story_paragraphs']:
        assert paragraph['text'] in body


def test_old_windows_asset_path_uses_explicit_mapping_and_same_source_bytes(tmp_path, monkeypatch):
    media=registry(tmp_path); source=sources(tmp_path)['assets']
    old=r'C:\Synthetic\old-film'
    source['assets'][0]['confirmed_outputs'][0]['path']=old+r'\fixture\face.png'
    monkeypatch.setenv('MSB_PROJECT_PATH_MAP',json.dumps({old:str(tmp_path)}))
    assert compile_fixture('assets',source,media)['published'] is False
    # An explicit absolute Windows alias already supplies the location; no redundant root is needed.
    assert compile_content(canonical(source).encode(),stage='assets',case_id='01',
        source_script_version=hashlib.sha256(b'synthetic-basis').hexdigest(), expected_version=None,
        scope='qa',media_registry=media)['published'] is False


def test_duplicate_native_reference_reuse_remains_unique_and_stable(tmp_path):
    source,media=two_references(tmp_path);row=source['drafts'][0]
    row['ordered_media'].append(copy.deepcopy(row['ordered_media'][0]))
    row['reference_checks'].append(copy.deepcopy(row['reference_checks'][0]))
    first=compile_fixture('prompts',source,media)['request']['content']['blocks'][0]['media']
    assert len({m['id'] for m in first})==3
    row['ordered_media']=row['ordered_media'][1:]+row['ordered_media'][:1]
    row['reference_checks']=row['reference_checks'][1:]+row['reference_checks'][:1]
    second=compile_fixture('prompts',source,media)['request']['content']['blocks'][0]['media']
    assert {m['id']:m['sha256'] for m in first}=={m['id']:m['sha256'] for m in second}
