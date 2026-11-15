"""Offline source-only review; no synthetic native receipts for real-source tests."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.core.web_content import compile_content, canonical, source_review_segments, WebContentError
from test_prompt_segment_web_adapter import fixture, segment


def compile_source(raw, **kwargs):
    # Explicit offline fixture head, NOT a claim about the site's current head.
    return compile_content(raw, stage='prompts', case_id='03',
        source_script_version=hashlib.sha256(b'OFFLINE-TEST-HEAD-NOT-PUBLISHED').hexdigest(),
        expected_version=None, scope='qa', prompt_mode='source_review', **kwargs)


def assert_source_view(raw, result, expected_ids=None):
    source = json.loads(raw)
    content = result['request']['content']; blocks = content['blocks']
    rows = source['generation_segments']
    if expected_ids is not None:
        by_id = {row['segment_id']: row for row in rows + [
            row for span in source['independent_candidate_ranges'] for row in span['generation_segments']]}
        rows = [by_id[ident] for ident in expected_ids]
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


def mixed_scope_fixture():
    source = source_fixture()
    top = source['generation_segments'][0]
    top.update(edit_in_seconds=6, edit_out_seconds=10, source_shot_ids=['S2'])
    earlier = dict(top, segment_id='P00', edit_in_seconds=0, edit_out_seconds=6, source_shot_ids=['S1'])
    source['independent_candidate_ranges'] = [{'range_id': 'earlier', 'generation_segments': [earlier]}]
    source['scope'] = {'type': 'full_local_authored', 'authored_segment_count': 2, 'authored_shot_count': 2,
                       'scope_seconds': 10,
                       'independent_ranges': [
                           {'segment_ids': ['P00'], 'shot_ids': ['S1'], 'edit_range_seconds': [0, 6]},
                           {'segment_ids': ['P01'], 'shot_ids': ['S2'], 'edit_range_seconds': [6, 10]},
                       ]}
    return source, earlier


def test_historical_mixed_source_review_and_consumers_cover_edit_order():
    from scripts.check_video_prompt_focal_intent import _json_segments
    from scripts.core.revision_control import _generation_segments

    source, _ = mixed_scope_fixture()
    raw = canonical(source).encode()
    assert_source_view(raw, compile_source(raw), ['P00', 'P01'])
    assert [row['segment_id'] for row in _generation_segments(source)] == ['P00', 'P01']
    assert [row[0] for row in _json_segments(raw.decode())] == ['P00', 'P01']
    assert source == json.loads(raw)


@pytest.mark.parametrize('change,error', [
    ('duplicate', 'segment_id 重复'), ('missing_count', '生成段总数'),
    ('missing_shot', '镜号总数'), ('wrong_order', '顺序或覆盖'),
    ('wrong_shot', '范围镜号'), ('wrong_range', '范围时码'),
    ('wrong_duration', '剪辑总时长'),
    ('missing_edit_window', '绝对剪辑起止时码'), ('overlap', '重叠或全片空档'),
    ('full_gap', '重叠或全片空档'),
])
def test_mixed_source_scope_rejects_ambiguity(change, error):
    source, earlier = mixed_scope_fixture()
    top = source['generation_segments'][0]
    scope = source['scope']
    if change == 'duplicate':
        earlier['segment_id'] = top['segment_id']
    elif change == 'missing_count':
        scope['authored_segment_count'] = 1
    elif change == 'missing_shot':
        scope['authored_shot_count'] = 1
    elif change == 'wrong_order':
        scope['independent_ranges'].reverse()
    elif change == 'wrong_shot':
        scope['independent_ranges'][0]['shot_ids'] = ['S2']
    elif change == 'wrong_range':
        scope['independent_ranges'][0]['edit_range_seconds'] = [0, 4]
    elif change == 'wrong_duration':
        scope['scope_seconds'] = 8
    elif change == 'missing_edit_window':
        earlier.pop('edit_in_seconds')
    elif change == 'overlap':
        earlier['edit_out_seconds'] = 7
    elif change == 'full_gap':
        earlier['edit_out_seconds'] = 5
    with pytest.raises(WebContentError, match=error):
        source_review_segments(source)


@pytest.mark.parametrize('sectioned', [False, True], ids=['flat', 'sectioned'])
@pytest.mark.parametrize('start', [3, 4, 5], ids=['overlap', 'contiguous', 'gap'])
def test_full_source_scope_checks_absolute_time_in_declared_order(sectioned, start):
    first = {'segment_id': 'P01', 'edit_in_seconds': 0, 'edit_out_seconds': 4}
    second = {'segment_id': 'P02', 'edit_in_seconds': start, 'edit_out_seconds': start + 4}
    source = {'scope': {'type': 'full_local_authored', 'scope_seconds': 8}}
    if sectioned:
        source['creative_sections'] = [{'generation_segments': [row]} for row in (first, second)]
    else:
        source['generation_segments'] = [first, second]

    if start == 4:
        assert source_review_segments(source) == [first, second]
    else:
        with pytest.raises(WebContentError, match='P01→P02.*重叠或全片空档'):
            source_review_segments(source)


def test_full_source_supports_duration_only_but_rejects_incomplete_absolute_timing():
    first = {'segment_id': 'P01', 'target_edit_duration_seconds': 4}
    second = {'segment_id': 'P02', 'target_edit_duration_seconds': 4}
    source = {'scope': {'type': 'full_local_authored'}, 'generation_segments': [first, second]}
    assert source_review_segments(source) == [first, second]

    first.update(edit_in_seconds=0, edit_out_seconds=4)
    with pytest.raises(WebContentError, match='绝对剪辑起止时码'):
        source_review_segments(source)


def test_partial_source_keeps_disjoint_requested_edit_windows():
    source, earlier = mixed_scope_fixture()
    source['scope'] = {'type': 'partial_local_authored'}
    earlier['edit_out_seconds'] = 5
    assert source_review_segments(source) == [earlier, source['generation_segments'][0]]


def test_generation_boundaries_checks_hidden_same_scene_seam_in_blank_task(tmp_path):
    from scripts.core.generation_boundaries import validate_generation_boundaries
    from scripts.init_desktop_project import initialize

    root = initialize(tmp_path / 'blank')
    source, _ = mixed_scope_fixture()
    board = {'scenes': [{'scene_id': 'S', 'shots': [
        {'shot_id': 'S1', 'take': {'action': '放稳书。'}}, {'shot_id': 'S2'},
    ]}]}
    with pytest.raises(ValueError, match='同场景默认合并'):
        validate_generation_boundaries(source, board, root)
    source['generation_segments'][0]['same_scene_split'] = {
        'from_segment_id': 'P00', 'evidence': '前一个独立动作已结束',
        'reason': 'event_complete',
        'source_boundary': {'shot_id': 'S1', 'field': 'take.action', 'quote': '放稳书。'},
    }
    report = validate_generation_boundaries(source, board, root)
    assert (report['segment_count'], report['same_scene_boundaries_checked']) == (2, 1)


def test_delivery_maps_historical_independent_segment_and_checks_native_claims(tmp_path):
    from scripts.core.quality_evidence import validate_native_binding_claims, validate_scene_source_mapping

    source, earlier = mixed_scope_fixture()
    source['source_storyboard'] = {'path': 'director.json'}
    board = {'scenes': [{'scene_id': 'A', 'shots': [{'shot_id': 'S1'}]},
                        {'scene_id': 'B', 'shots': [{'shot_id': 'S2'}]}]}
    refs = [({'artifact_id': 'VIDEO'}, tmp_path / 'video.json', source),
            ({'artifact_id': 'DIR'}, tmp_path / 'director.json', board)]
    draft = {'source_segment_id': 'P00', 'source_shot_ids': ['S1'],
             'prompt': earlier['prompt'], 'parameters': copy.deepcopy(earlier['parameters'])}
    assert validate_scene_source_mapping([draft], refs, tmp_path, check_submitted_body=True) == {}
    draft['prompt'] = '改过的正文。'
    with pytest.raises(ValueError, match='P00.*冻结正文'):
        validate_scene_source_mapping([draft], refs, tmp_path, check_submitted_body=True)
    earlier['native_entity_verified'] = True
    with pytest.raises(ValueError, match='旧native_binding_verified'):
        validate_native_binding_claims(source, tmp_path)


def test_source_review_preserves_original_and_requires_no_canvas():
    source = source_fixture(); raw = canonical(source).encode()
    assert_source_view(raw, compile_source(raw))
    assert source == json.loads(raw)
    with pytest.raises(WebContentError, match='transport_evidence'):
        compile_content(raw, stage='prompts', case_id='03', source_script_version='a'*64,
                        expected_version=None, scope='qa')


def test_source_review_preserves_inline_generation_segment_reconciliation():
    source = source_fixture()
    source['segment_reconciliation'] = {
        'baseline': {'artifact_id': 'OLD-PROMPT', 'sha256': 'a' * 64},
        'changes': [{'action': 'add', 'segment_id': 'P02', 'reason': 'Documented scene boundary.'}],
    }
    raw = canonical(source).encode()
    result = compile_source(raw)
    canonical_source = ''.join(b['text'] for b in result['request']['content']['blocks']
                               if b['id'] == 'canonical-source' or b['id'].startswith('canonical-source-part-'))
    assert json.loads(canonical_source) == source


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
