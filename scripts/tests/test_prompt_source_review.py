"""Offline source-segment ordering and consumer checks; no legacy v78 web adapter."""
import copy
import json

import pytest

from scripts.core.prompt_body import source_review_segments


def source_fixture():
    return {
        'revision': 'TEST-R12-SHAPE', 'status': 'candidate', 'platform': '小云雀网页全参考候选',
        'production_blocked': True, 'unknown_fact': {'preserved': 'untouched'},
        'generation_segments': [{
            'segment_id': 'P01', 'prompt': '实际正文。',
            'parameters': {'aspect_ratio': '9:16', 'model': 'SD 2.5', 'resolution': '720P', 'duration_seconds': 11},
            'generation_seconds': 11,
            'materials': [{'key': 'FACE', 'role': '只控制身份，不控制动作。',
                           'path': 'not-uploaded.png', 'sha256': 'a' * 64}],
        }],
    }


def mixed_scope_fixture():
    source = source_fixture()
    top = source['generation_segments'][0]
    top.update(edit_in_seconds=6, edit_out_seconds=10, source_shot_ids=['S2'])
    earlier = dict(top, segment_id='P00', edit_in_seconds=0, edit_out_seconds=6, source_shot_ids=['S1'])
    source['independent_candidate_ranges'] = [{'range_id': 'earlier', 'generation_segments': [earlier]}]
    source['scope'] = {
        'type': 'full_local_authored', 'authored_segment_count': 2, 'authored_shot_count': 2,
        'scope_seconds': 10,
        'independent_ranges': [
            {'segment_ids': ['P00'], 'shot_ids': ['S1'], 'edit_range_seconds': [0, 6]},
            {'segment_ids': ['P01'], 'shot_ids': ['S2'], 'edit_range_seconds': [6, 10]},
        ],
    }
    return source, earlier


def test_historical_mixed_source_review_and_consumers_cover_edit_order():
    from scripts.check_video_prompt_focal_intent import _json_segments
    from scripts.core.revision_control import _generation_segments

    source, _ = mixed_scope_fixture()
    raw = json.dumps(source, ensure_ascii=False)
    assert [row['segment_id'] for row in source_review_segments(source)] == ['P00', 'P01']
    assert [row['segment_id'] for row in _generation_segments(source)] == ['P00', 'P01']
    assert [row[0] for row in _json_segments(raw)] == ['P00', 'P01']
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
    with pytest.raises(ValueError, match=error):
        source_review_segments(source)


def test_source_review_rejects_frozen_drafts():
    with pytest.raises(ValueError, match='传输快照'):
        source_review_segments({'revision': 'frozen', 'drafts': []})


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
