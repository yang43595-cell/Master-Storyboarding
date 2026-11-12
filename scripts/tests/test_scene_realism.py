"""Synthetic declared facts only: no video generation, visual QA or approval."""
import copy
import json

import pytest

from scripts.core.scene_realism import inspect_scene_realism, prompt_digest
from scripts.check_video_prompt_focal_intent import inspect_document, main

BODY = '杯子碰响，近处女孩抬头，远处男孩继续看书；女孩从桌上拿起杯子。保留室内声。'


def fixture():
    record = {
        'segment_id': 'G1', 'prompt_sha256': prompt_digest(BODY),
        'events': [{'event_id': 'clink', 'at': 1, 'description': '杯子碰响', 'audible_source': '杯子碰桌'},
                   {'event_id': 'pickup', 'at': 2, 'description': '女孩拿起桌上的杯子'}],
        'reactions': [
            {'person_id': 'near', 'event_id': 'clink', 'prior_activity': '低头整理桌面',
             'perception': 'heard', 'perception_basis': '近处无遮挡，杯响可听',
             'perceived_at': 1.1, 'response_at': 1.3, 'response': '抬头看杯子'},
            {'person_id': 'far', 'event_id': 'clink', 'prior_activity': '隔窗看书',
             'perception': 'not_perceived', 'perception_basis': '隔声玻璃外，背向室内',
             'perceived_at': None, 'response_at': None, 'response': None}],
        'objects': [{'object_id': 'cup', 'before': {'count': 1, 'place': 'table', 'holder': None},
                     'after': {'count': 1, 'place': 'near_hand', 'holder': 'near'},
                     'change': {'event_id': 'pickup', 'count_delta': 0, 'path': '手伸到桌面杯把，接触后抬起'}}],
        'environment_sound': {'mode': 'present', 'sources': [{'source': '室内空调', 'behavior': '杯响前后持续低鸣'}]},
        'boundary': {'kind': 'start'},
        'entry_state': {'near': 'standing', 'hat': 'on_head', 'left_wrist': 'bracelet'},
        'exit_state': {'near': 'standing', 'hat': 'on_head', 'left_wrist': 'bracelet'},
    }
    return [('G1', BODY, '')], [record]


def codes(result):
    return {f['code'] for f in result['findings']}


def test_near_reacts_far_unaware_is_consistent_and_never_visual_proof():
    segments, records = fixture()
    original = copy.deepcopy(records)
    result = inspect_scene_realism(segments, records)
    assert result == {'verification': 'declared_plan_only', 'visual_quality_verified': False,
                      'audio_quality_verified': False, 'status': 'consistent', 'findings': []}
    assert records == original


@pytest.mark.parametrize('change,expected', [
    (lambda r: r['reactions'][0].update(response=None, response_at=None), 'missing_response'),
    (lambda r: r['reactions'][0].update(response_at=.5), 'early_response'),
    (lambda r: r['reactions'][0].update(perceived_at=.5), 'early_perception'),
    (lambda r: r['reactions'][1].update(response='转头', response_at=1.2), 'unperceived_response'),
    (lambda r: r['events'][0].pop('audible_source'), 'inaudible_event'),
    (lambda r: r['objects'][0]['after'].update(count=3), 'object_count'),
    (lambda r: r['objects'][0]['change'].pop('path'), 'object_path'),
    (lambda r: r['objects'][0].pop('change'), 'object_cause'),
    (lambda r: r['objects'][0]['before'].update(count=0, holder='someone'), 'empty_holder'),
    (lambda r: r.pop('environment_sound'), 'environment_sound'),
    (lambda r: r['environment_sound'].update(sources=[]), 'sound_source'),
    (lambda r: r.update(environment_sound={'mode': 'intentional_silence'}), 'silence_reason'),
    (lambda r: r['objects'][0]['before'].update(holder='giver'), 'handover_contact'),
])
def test_known_negative_declarations_are_flagged(change, expected):
    segments, records = fixture()
    change(records[0])
    assert expected in codes(inspect_scene_realism(segments, records))


def test_frozen_or_deliberately_restrained_response_needs_specific_basis():
    segments, records = fixture()
    records[0]['reactions'][0].update(response=None, response_at=None, no_visible_response_reason='保持克制以免惊动对方，原拍法明确')
    assert inspect_scene_realism(segments, records)['status'] == 'consistent'


def test_copied_reactions_and_justified_synchrony_are_distinguished():
    segments, records = fixture()
    a = records[0]['reactions'][0]
    b = copy.deepcopy(a); b['person_id'] = 'other'
    records[0]['reactions'].append(b)
    assert 'copied_reactions' in codes(inspect_scene_realism(segments, records))
    for r in (a, b): r['coordination_reason'] = '已排练的齐声回应同一口令'
    assert inspect_scene_realism(segments, records)['status'] == 'consistent'
    for r in (a, b): r.pop('coordination_reason')
    b.update(response_at=1.7, response='停下手里的笔')
    assert inspect_scene_realism(segments, records)['status'] == 'consistent'


def two_segments():
    segments, records = fixture()
    right = copy.deepcopy(records[0]); right['segment_id'] = 'G2'
    right['boundary'] = {'kind': 'continuous', 'from_segment_id': 'G1'}
    right['objects'][0]['before'] = copy.deepcopy(records[0]['objects'][0]['after'])
    right['objects'][0].pop('change')
    records.append(right); segments.append(('G2', BODY, ''))
    return segments, records


def test_continuous_state_drift_and_meaningful_time_jump():
    segments, records = two_segments()
    assert inspect_scene_realism(segments, records)['status'] == 'consistent'
    records[1]['entry_state']['left_wrist'] = 'bare'
    records[1]['objects'][0]['before']['place'] = 'locked_cabinet'
    result = inspect_scene_realism(segments, records)
    assert {'seam_state', 'seam_objects'} <= codes(result)
    # A justified time jump can change boundary state, but cannot excuse an
    # impossible in-segment transfer. Repair that independently.
    records[1]['objects'][0]['before'] = copy.deepcopy(records[1]['objects'][0]['after'])
    records[1]['boundary'].update(kind='time_jump', basis='来源明确十分钟后，已交代取下饰件')
    assert inspect_scene_realism(segments, records)['status'] == 'consistent'


@pytest.mark.parametrize('change,expected', [
    (lambda r: r[1]['boundary'].update(from_segment_id='wrong'), 'missing_predecessor'),
    (lambda r: r[1].update(boundary={'kind': 'start'}), 'reset_boundary'),
    (lambda r: r[1]['boundary'].update(kind='new_scene'), 'boundary_basis'),
    (lambda r: r[1]['entry_state'].pop('hat'), 'seam_state'),
    (lambda r: r[1].update(objects=[], objects_not_applicable='杯子出画'), 'seam_objects'),
])
def test_boundary_omissions_do_not_reset_state(change, expected):
    segments, records = two_segments(); change(records)
    assert expected in codes(inspect_scene_realism(segments, records))


@pytest.mark.parametrize('value', [None, [], {}, 'unchecked', False])
def test_missing_records_are_unverified(value):
    segments, _ = fixture()
    assert inspect_scene_realism(segments, value)['status'] == 'unverified'


@pytest.mark.parametrize('value', [None, {}, False, 'bad', [None], [{'person_id': []}]])
def test_malformed_groups_are_unverified_not_crashes(value):
    segments, records = fixture(); records[0]['reactions'] = value
    assert inspect_scene_realism(segments, records)['status'] == 'unverified'


@pytest.mark.parametrize('value', [None, {}, [], False])
def test_malformed_event_refs_do_not_crash(value):
    segments, records = fixture(); records[0]['reactions'][0]['event_id'] = value
    assert 'reaction_event' in codes(inspect_scene_realism(segments, records))
    records[0]['objects'][0]['change']['event_id'] = value
    assert 'object_cause' in codes(inspect_scene_realism(segments, records))


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '1'])
def test_invalid_planned_times_are_not_measurements(value):
    segments, records = fixture(); records[0]['events'][0]['at'] = value
    assert 'event' in codes(inspect_scene_realism(segments, records))


def test_changed_body_stales_annotations_and_does_not_mutate_them():
    segments, records = fixture(); segments[0] = ('G1', BODY + '修改', '')
    assert inspect_scene_realism(segments, records)['status'] == 'unverified'


def test_existing_cli_optional_branch_and_exit_codes(tmp_path, capsys):
    segments, records = fixture()
    payload = {'generation_segments': [{'segment_id': 'G1', 'prompt': BODY}], 'scene_realism': records}
    path = tmp_path / 'candidate.json'; path.write_text(json.dumps(payload, ensure_ascii=False))
    assert inspect_document(path, check_scene_realism=True) == (1, [])
    assert main([str(path), '--check-scene-realism']) == 0
    output = capsys.readouterr().out
    assert 'DECLARATIONS CONSISTENT' in output and '真实视频/声音仍未验证' in output
    payload['scene_realism'][0]['reactions'][0].update(response=None, response_at=None)
    path.write_text(json.dumps(payload, ensure_ascii=False))
    assert main([str(path), '--check-scene-realism']) == 1
    assert inspect_document(path) == (1, [])  # Legacy scope is unchanged, not a realism pass.
    payload.pop('scene_realism'); path.write_text(json.dumps(payload))
    assert main([str(path), '--check-scene-realism']) == 2
    assert 'UNCHECKED' in capsys.readouterr().out


def test_non_json_does_not_acquire_realism_pass(tmp_path):
    path = tmp_path / 'body.txt'; path.write_text(BODY)
    assert main([str(path), '--check-scene-realism']) == 2


@pytest.mark.parametrize('field', ['perception', 'sound_mode', 'boundary_kind'])
@pytest.mark.parametrize('value', [[], {}, True, None])
def test_malformed_enums_return_unverified(field, value):
    segments, records = fixture()
    if field == 'perception': records[0]['reactions'][0]['perception'] = value
    if field == 'sound_mode': records[0]['environment_sound']['mode'] = value
    if field == 'boundary_kind': records[0]['boundary']['kind'] = value
    assert inspect_scene_realism(segments, records)['status'] == 'unverified'


def test_huge_integer_time_is_unverified_without_overflow():
    segments, records = fixture(); records[0]['events'][0]['at'] = 10 ** 1000
    assert 'event' in codes(inspect_scene_realism(segments, records))


def test_missing_holder_is_not_assumed_unheld():
    segments, records = fixture(); records[0]['objects'][0]['before'].pop('holder')
    assert 'object_state' in codes(inspect_scene_realism(segments, records))


def test_declared_count_change_cannot_hide_behind_unchanged_snapshots():
    segments, records = fixture(); item = records[0]['objects'][0]
    item['before'] = copy.deepcopy(item['after']); item['change']['count_delta'] = 1
    assert 'object_count' in codes(inspect_scene_realism(segments, records))


def test_nearby_brawl_nonresponse_negative_and_far_unaware_positive():
    segments, records = fixture()
    body = '近处两人发生冲突，面对他们的路人已看到却继续看报；隔街背向的行人没有察觉，继续行走。'
    segments[0] = ('G1', body, ''); record = records[0]
    record['prompt_sha256'] = prompt_digest(body)
    record['events'][0].update(description='近处两人发生冲突', audible_source='争执及碰撞')
    record['reactions'][0].update(perception='seen', prior_activity='近处看报', perception_basis='面向两人无遮挡', response=None, response_at=None)
    record['reactions'][1].update(prior_activity='隔街行走', perception_basis='背向冲突且道路噪声盖过争执')
    result = inspect_scene_realism(segments, records)
    assert 'missing_response' in codes(result)
    assert 'unperceived_response' not in codes(result)


def test_consistent_annotations_cannot_certify_false_or_omitted_prose_facts():
    segments, records = fixture()
    # Deliberately contradictory prose with fresh annotation hash. This is a
    # scope regression: the tool must never claim it understood the sentence.
    body = '所有人同时转头。杯子凭空出现在手里。'
    segments[0] = ('G1', body, ''); records[0]['prompt_sha256'] = prompt_digest(body)
    result = inspect_scene_realism(segments, records)
    assert result['status'] == 'consistent'
    assert result['verification'] == 'declared_plan_only'
    assert result['visual_quality_verified'] is False
    assert result['audio_quality_verified'] is False


@pytest.mark.parametrize('field', ['perceived_at', 'response', 'response_at'])
def test_missing_reaction_is_not_assumed_explicit_null(field):
    segments, records = fixture(); records[0]['reactions'][1].pop(field)
    assert 'reaction_declaration' in codes(inspect_scene_realism(segments, records))
