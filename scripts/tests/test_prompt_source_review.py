"""Offline source-only review; no synthetic native receipts for real-source tests."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.core.web_content import compile_content, canonical, WebContentError
from test_prompt_segment_web_adapter import fixture, segment


def compile_source(raw, **kwargs):
    # Explicit offline fixture head, NOT a claim about the site's current head.
    return compile_content(raw, stage='prompts', case_id='03',
        source_script_version=hashlib.sha256(b'OFFLINE-TEST-HEAD-NOT-PUBLISHED').hexdigest(),
        expected_version=None, scope='qa', prompt_mode='source_review', **kwargs)


def assert_source_view(raw, result):
    source = json.loads(raw)
    content = result['request']['content']; blocks = content['blocks']
    rows = source['generation_segments']
    assert result['sourceReview']['segmentCount'] == len(rows)
    assert content['reviewable'] is False
    assert result['published'] is False and result['remoteReadbackPerformed'] is False
    assert 'transportEvidence' not in result
    assert result['sourceReview']['canvasBindingVerified'] is False
    assert result['sourceReview']['generationVerified'] is False
    assert json.loads(''.join(b['text'] for b in blocks if b['id'] == 'canonical-source' or b['id'].startswith('canonical-source-part-'))) == source
    binding = json.loads(next(b['text'] for b in blocks if b['id'] == 'source-binding'))
    assert binding['source_sha256'] == hashlib.sha256(raw).hexdigest()
    for row in rows:
        block = next(b for b in blocks if b['id'] == row['segment_id'])
        assert block['text'].startswith(row.get('prompt', row.get('prompt_text')) + '\n\n')
        assert canonical(row['parameters']) in block['text']
        assert canonical(row['materials']) in block['text']
        assert '未挂图' in block['text'] and '不构成用户确认' in block['text']
        assert not block['media']
    assert not result['mediaMap']
    assert all(not b['media'] for b in blocks)
    assert '/api/content-media/' not in canonical(result)


def source_fixture():
    return {'revision': 'TEST-R12-SHAPE', 'status': 'candidate', 'platform': '小云雀网页全参考候选',
        'production_blocked': True, 'unknown_fact': {'preserved': 'untouched'},
        'generation_segments': [{'segment_id': 'P01', 'prompt': '实际正文。',
            'parameters': {'aspect_ratio': '9:16', 'model': 'SD 2.5', 'resolution': '720P', 'duration_seconds': 11},
            'generation_seconds': 11, 'materials': [{'key': 'FACE', 'role': '只控制身份，不控制动作。',
                'path': 'not-uploaded.png', 'sha256': 'a'*64}]}]}


def test_source_review_preserves_original_and_requires_no_canvas():
    source = source_fixture(); raw = canonical(source).encode()
    assert_source_view(raw, compile_source(raw))
    assert source == json.loads(raw)
    with pytest.raises(WebContentError, match='transport_evidence'):
        compile_content(raw, stage='prompts', case_id='03', source_script_version='a'*64,
                        expected_version=None, scope='qa')


@pytest.mark.parametrize('fields', [{}, {'prompt': ''}, {'prompt': 42}, {'prompt_text': None},
    {'prompt': 'one', 'prompt_text': 'two'}, {'prompt': 'one', 'prompt_text': ''}])
def test_source_review_missing_invalid_or_conflicting_body_fails(fields):
    source = source_fixture(); row = source['generation_segments'][0]
    row.pop('prompt'); row.update(fields); row['metadata'] = {'prompt': 'not a body'}
    with pytest.raises(WebContentError): compile_source(canonical(source).encode())


@pytest.mark.parametrize('extra', [{'reviewable': True}, {'transport_evidence': {}},
    {'transport_evidence': {'canvasBindingVerified': True, 'generationVerified': True}}])
def test_source_review_cannot_claim_execution_or_approval(extra):
    with pytest.raises(WebContentError, match='未确认只读审阅'):
        compile_source(canonical(source_fixture()).encode(), **extra)


def test_source_review_rejects_wrong_stage_and_frozen_drafts():
    with pytest.raises(WebContentError):
        compile_content(b'{}', stage='director', case_id='03', source_script_version='a'*64,
                        expected_version=None, scope='qa', prompt_mode='source_review')
    with pytest.raises(WebContentError, match='传输快照'):
        compile_source(canonical({'revision': 'frozen', 'drafts': []}).encode())


def test_source_review_registry_uses_existing_byte_and_receipt_guards(tmp_path):
    source, media, _ = fixture(tmp_path, 'top')
    raw = canonical(source).encode(); before = copy.deepcopy(source)
    result = compile_source(raw, media_registry=media)
    assert len(result['request']['content']['blocks'][1]['media']) == 1
    assert result['sourceReview']['canvasBindingVerified'] is False
    assert source == before and 'transportEvidence' not in result
    key = next(k for k in media if k.startswith('SYNTHETIC-FACE@'))
    media[key]['readback'] = {}
    with pytest.raises(WebContentError, match='回读标识'):
        compile_source(raw, media_registry=media)


def test_cli_requires_explicit_source_review(tmp_path):
    path = tmp_path/'source.json'; path.write_text(canonical(source_fixture()))
    job = dict(sourcePath=str(path), stage='prompts', caseId='03', sourceScriptVersion='a'*64,
               expectedVersion=None, scope='qa', promptMode='source_review')
    result = subprocess.run([sys.executable, '-m', 'scripts.core.web_content'], input=canonical(job),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert_source_view(path.read_bytes(), json.loads(result.stdout))
    job.pop('promptMode')
    result = subprocess.run([sys.executable, '-m', 'scripts.core.web_content'], input=canonical(job),
                            text=True, capture_output=True)
    assert result.returncode == 2


@pytest.mark.parametrize('variable, count', [('MSB_TEST_CASE2_R12', 19), ('MSB_TEST_CASE3_R04', 16)])
def test_real_sources_read_only(variable, count):
    """Paths explicitly provided by operator; private sources stay out of repo."""
    path = os.environ.get(variable)
    if not path: pytest.skip('Provide ' + variable + ' for private-source read-only regression')
    path = Path(path); raw = path.read_bytes()
    assert len(json.loads(raw)['generation_segments']) == count
    assert_source_view(raw, compile_source(raw))
    assert path.read_bytes() == raw
