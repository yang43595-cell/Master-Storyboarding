"""Small declaration/consumer replays, not generated media or visual validation.

All image bytes and approvals are inherited synthetic fixtures. No real project,
provider, native browser or generation job is touched by these tests.
"""
import copy

import pytest

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, quality_evidence as q
from scripts.core.io import read_json, write_json
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality as bind_image_and_draft_quality


def bind_synthetic_quality(root, frozen, *, assessment='changed'):
    """Explicit test assertion, never inferred story semantics or real review."""
    bind_image_and_draft_quality(root, frozen)
    for row in frozen['drafts']:
        check = row['quality_evidence']['checks']['persistent_objects']
        check.update(scene_state_assessment=assessment,
                     source_shot_ids=q.source_shot_ids(row))
        row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))


def state_record(shot_ids=None):
    return dict(state_id='TABLE-SERVED', entity_id='DINING-ROOM',
                previous_state_id='TABLE-EMPTY', change_event='Dishes are served.',
                description='The dishes remain on the table.',
                preserve=['Room architecture and table position.'],
                changes=['The previously empty table now has dishes.'],
                source_shot_ids=shot_ids or ['S01-002', 'S01-003'])


def source(root, frozen, states, *, assessment='changed'):
    payload = {'scenes': [{'scene_id': 'S01',
                          'shots': [{'shot_id': s} for s in ['S01-001', 'S01-002', 'S01-003']],
                          **({'scene_states': states} if states is not None else {})}]}
    write_json(root / 'director.json', payload)
    checksum = artifacts.digest(root / 'director.json')
    manifest = artifacts.load_manifest(root)
    next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')['checksum'] = checksum
    frozen['source_artifacts'][0]['sha256'] = checksum
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    bind_synthetic_quality(root, frozen, assessment=assessment)


def scene_delivery(root):
    frozen, _ = delivery(root)
    frozen['drafts'] = frozen['drafts'][:1]
    row = frozen['drafts'][0]
    row.update(prompt='Use the served-table state; the dishes remain visible.',
               source_shot_ids=['S01-002', 'S01-003'],
               required_entities=['DINING-ROOM'],
               required_reference_roles={'DINING-ROOM': ['state_reference']})
    row['reference_checks'][0].update(role='state_reference', state_id='TABLE-SERVED',
                                      entity_ids=['DINING-ROOM'])
    source(root, frozen, [state_record()])
    row.update(source_segment_id='G-SERVED', source_shots=['S01-002', 'S01-003'])
    manifest = artifacts.load_manifest(root)
    next(a for a in manifest['artifacts'] if a['artifact_id'] == 'DIRECTOR')['logical_id'] = 'storyboard.package'
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    payload = {'source_storyboard': {'artifact_id': 'DIRECTOR', 'revision': 'R01'},
               'generation_segments': [{'segment_id': 'G-SERVED',
                                        'source_shots': ['S01-002', 'S01-003'],
                                        'prompt': row['prompt']}]}
    write_synthetic_video_source(root, frozen, payload)
    return frozen


def observed(frozen):
    value = copy.deepcopy(frozen)
    value.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in value['drafts']:
        row['native_mentions'] = [dict(m, entity_type='fixture-chip', native_noneditable_entity=True)
                                  for m in row['ordered_media']]
    return value


def test_correct_state_reference_and_native_mapping_pass_only_consistency(tmp_path):
    frozen = scene_delivery(tmp_path)
    report = compare(frozen, observed(frozen), tmp_path)
    assert report['ok']
    submission = validate_submission(frozen, tmp_path)
    assert submission['visual_quality_verified'] is False
    assert submission['generation_input_provenance'] == 'unknown'


@pytest.mark.parametrize('mutation', ['old_state', 'missing_image', 'wrong_hash', 'wrong_scope',
                                     'failed_image', 'unknown_image', 'unreviewed_image'])
def test_changed_scene_rejects_unusable_reference(tmp_path, mutation):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    ref = row['reference_checks'][0]
    if mutation == 'old_state':
        ref['state_id'] = 'TABLE-EMPTY'
    elif mutation == 'missing_image':
        row['ordered_media'] = []
        row['reference_checks'] = []
        row['required_entities'] = []
        row['required_reference_roles'] = {}
    elif mutation == 'wrong_hash':
        ref['sha256'] = '0' * 64
    elif mutation == 'wrong_scope':
        ref['source_shot_ids'] = ['S01-001']
    bind_synthetic_quality(tmp_path, frozen)
    if mutation in {'failed_image', 'unknown_image', 'unreviewed_image'}:
        manifest = artifacts.load_manifest(tmp_path)
        item = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')
        if mutation == 'unreviewed_image':
            item.pop('quality_evidence')
        else:
            item['quality_evidence']['status'] = 'fail' if mutation == 'failed_image' else 'unknown'
        write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


def test_old_full_scene_image_cannot_remain_active_alongside_new_state(tmp_path):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    old = dict(row['reference_checks'][0], role='layout', state_id='TABLE-EMPTY')
    row['reference_checks'].append(old)
    row['ordered_media'].append(copy.deepcopy(row['ordered_media'][0]))
    bind_synthetic_quality(tmp_path, frozen)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


def test_no_persistent_scene_change_requires_no_extra_reference(tmp_path):
    frozen, _ = delivery(tmp_path)
    source(tmp_path, frozen, None, assessment='unchanged')
    assert validate_submission(frozen, tmp_path)['ok']


def test_second_source_shot_requires_new_state_not_just_segment_head(tmp_path):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    row['source_shot_ids'] = ['S01-001', 'S01-002']
    row['source_shots'] = ['S01-001', 'S01-002']
    payload = read_json(tmp_path / 'video-source.json')
    payload['generation_segments'][0]['source_shots'] = row['source_shots']
    write_synthetic_video_source(tmp_path, frozen, payload)
    row['reference_checks'][0]['state_id'] = 'TABLE-EMPTY'
    bind_synthetic_quality(tmp_path, frozen)
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


def test_relabelled_prompt_and_correct_reference_cannot_hide_old_native_mentions(tmp_path):
    frozen = scene_delivery(tmp_path)
    value = observed(frozen)
    value['drafts'][0]['native_mentions'][0].update(node_id='old-empty-table-node', media_id='old-empty-table-media')
    report = compare(frozen, value, tmp_path)
    assert not report['ok']
    assert any(d.get('field') == 'native_mentions' for d in report['differences'])


def test_multiple_scene_states_in_one_segment_require_honest_native_control(tmp_path):
    frozen = scene_delivery(tmp_path)
    later = dict(state_record(['S01-003']), state_id='TABLE-CLEARED',
                 previous_state_id='TABLE-SERVED', change_event='The dishes are removed.',
                 description='The table is cleared.', changes=['Dishes are absent.'])
    source(tmp_path, frozen, [state_record(['S01-002']), later])
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


def test_source_state_range_must_name_actual_director_shots(tmp_path):
    frozen = scene_delivery(tmp_path)
    source(tmp_path, frozen, [state_record(['S99-999'])])
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


def test_partial_segment_state_scope_is_not_native_per_shot_control(tmp_path):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    row['source_shot_ids'] = ['S01-001', 'S01-002']
    row['source_shots'] = ['S01-001', 'S01-002']
    payload = read_json(tmp_path / 'video-source.json')
    payload['generation_segments'][0]['source_shots'] = row['source_shots']
    write_synthetic_video_source(tmp_path, frozen, payload)
    ref = row['reference_checks'][0]
    ref['source_shot_ids'] = ['S01-002']
    bind_synthetic_quality(tmp_path, frozen)
    manifest = artifacts.load_manifest(tmp_path)
    image = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'FRAME')
    image['quality_evidence']['uses'] = [q.image_use(ref, ['S01-002'])]
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    # The image/use is otherwise valid. This must fail at the state consumer,
    # not be treated as native per-shot control merely because scope text exists.
    with pytest.raises(ValueError, match='scene_state'):
        validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('mapping', [['S01-002', 'S01-003'],
                                   [{'shot_id': 'S01-002'}, {'shot_id': 'S01-003'}]])
def test_both_existing_source_shots_shapes_resolve_all_shots(tmp_path, mapping):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    row['source_shots'] = mapping
    bind_synthetic_quality(tmp_path, frozen)
    assert validate_submission(frozen, tmp_path)['ok']
    row['reference_checks'][0]['state_id'] = 'TABLE-EMPTY'
    bind_synthetic_quality(tmp_path, frozen)
    with pytest.raises(ValueError, match='scene_state'):
        validate_submission(frozen, tmp_path)


def test_source_shots_without_duplicate_direct_field_is_supported(tmp_path):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    row['source_shots'] = [{'shot_id': ident} for ident in row.pop('source_shot_ids')]
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    assert validate_submission(frozen, tmp_path)['ok']


def test_deleted_source_state_declaration_cannot_bypass_changed_assessment(tmp_path):
    frozen = scene_delivery(tmp_path)
    source(tmp_path, frozen, None, assessment='changed')
    with pytest.raises(ValueError, match='scene_state'):
        validate_submission(frozen, tmp_path)


def test_unchanged_environment_reference_needs_explicit_scope_assessment(tmp_path):
    frozen = scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    row['reference_checks'][0].update(role='layout', state_id='TABLE-EMPTY')
    row['required_reference_roles'] = {'DINING-ROOM': ['layout']}
    source(tmp_path, frozen, None, assessment='unchanged')
    assert validate_submission(frozen, tmp_path)['ok']
    check = row['quality_evidence']['checks']['persistent_objects']
    check.pop('scene_state_assessment')
    row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    with pytest.raises(ValueError, match='scene_state'):
        validate_submission(frozen, tmp_path)


def test_unchanged_assessment_cannot_erase_declared_new_state(tmp_path):
    frozen = scene_delivery(tmp_path)
    bind_synthetic_quality(tmp_path, frozen, assessment='unchanged')
    with pytest.raises(ValueError, match='scene_state'):
        validate_submission(frozen, tmp_path)


def write_synthetic_video_source(root, frozen, payload):
    """Register only an isolated test source, never a real approval."""
    from scripts.core.project_state import load_project_state, write_project_state
    write_json(root / 'video-source.json', payload)
    checksum = artifacts.digest(root / 'video-source.json')
    manifest = artifacts.load_manifest(root)
    manifest['artifacts'] = [a for a in manifest['artifacts'] if a['artifact_id'] != 'VIDEO-SOURCE']
    manifest['artifacts'].append(dict(artifact_id='VIDEO-SOURCE', logical_id='video.synthetic.source',
        revision='R01', path_or_url='video-source.json', checksum=checksum,
        status='current', user_approved=True))
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    state = load_project_state(root)
    if 'VIDEO-SOURCE' not in state['current_artifacts']:
        state['current_artifacts'].append('VIDEO-SOURCE')
        state['user_confirmations'].append({'artifact_ids': ['VIDEO-SOURCE']})
    write_project_state(root, state)
    frozen['source_artifacts'] = [r for r in frozen['source_artifacts'] if r['artifact_id'] != 'VIDEO-SOURCE']
    frozen['source_artifacts'].append(dict(artifact_id='VIDEO-SOURCE', revision='R01', sha256=checksum))
    bind_synthetic_quality(root, frozen)


def mapped_scene_delivery(root):
    frozen = scene_delivery(root)
    return frozen, read_json(root / 'video-source.json')


def test_draft_source_mapping_binds_exact_video_segment_and_director(tmp_path):
    frozen, _ = mapped_scene_delivery(tmp_path)
    assert validate_submission(frozen, tmp_path)['ok']


@pytest.mark.parametrize('mutation', ['earlier_shot', 'unknown_shot', 'unknown_segment',
                                     'missing_director', 'missing_video', 'director_revision', 'director_hash'])
def test_source_mapping_cannot_be_changed_or_omitted_to_skip_state_gate(tmp_path, mutation):
    frozen, payload = mapped_scene_delivery(tmp_path)
    row = frozen['drafts'][0]
    if mutation in {'earlier_shot', 'unknown_shot'}:
        ids = ['S01-001'] if mutation == 'earlier_shot' else ['S99-999']
        row['source_shot_ids'] = ids
        row['source_shots'] = ids
        # Even fresh draft/image declarations must not authorize a remapping.
        bind_synthetic_quality(tmp_path, frozen, assessment='unchanged')
    elif mutation == 'unknown_segment':
        row['source_segment_id'] = 'G-NOT-IN-SOURCE'
        bind_synthetic_quality(tmp_path, frozen)
    elif mutation in {'missing_director', 'missing_video'}:
        removed = 'DIRECTOR' if mutation == 'missing_director' else 'VIDEO-SOURCE'
        frozen['source_artifacts'] = [r for r in frozen['source_artifacts'] if r['artifact_id'] != removed]
    else:
        key, value = ('revision', 'R00') if mutation == 'director_revision' else ('sha256', '0' * 64)
        payload['source_storyboard'][key] = value
        write_synthetic_video_source(tmp_path, frozen, payload)
    expected = '来源修订' if mutation == 'director_revision' else 'scene_state'
    with pytest.raises(ValueError, match=expected):
        validate_submission(frozen, tmp_path)


def test_established_entry_state_with_no_predecessor_keeps_source_shots_unchanged(tmp_path):
    frozen = scene_delivery(tmp_path)
    original_director_shots = copy.deepcopy(read_json(tmp_path / 'director.json')['scenes'][0]['shots'])
    original_video = (tmp_path / 'video-source.json').read_bytes()
    entry = dict(state_record(), previous_state_id=None,
                 change_event='Synthetic source S01-002 opens with dishes already on the table.',
                 changes=['Entry fact: dishes are already present; no serving action is specified.'])
    source(tmp_path, frozen, [entry])
    submitted = copy.deepcopy(frozen)
    director_bytes = (tmp_path / 'director.json').read_bytes()
    assert validate_submission(frozen, tmp_path)['ok']
    assert frozen == submitted
    assert (tmp_path / 'director.json').read_bytes() == director_bytes
    assert read_json(tmp_path / 'director.json')['scenes'][0]['shots'] == original_director_shots
    assert (tmp_path / 'video-source.json').read_bytes() == original_video
    # This checks declared null-entry support and non-mutation only. It does not
    # claim NLP detection of invented serving/picking-up actions in free text.


def test_null_predecessor_cannot_reset_an_existing_entity_state_chain(tmp_path):
    frozen = scene_delivery(tmp_path)
    first = dict(state_record(['S01-002']), previous_state_id=None,
                 change_event='Synthetic source starts with dishes already present.')
    reset = dict(state_record(['S01-003']), state_id='TABLE-CLEARED', previous_state_id=None,
                 change_event='Synthetic later state incorrectly resets its predecessor.',
                 description='The table is clear.', changes=['Dishes are absent.'])
    source(tmp_path, frozen, [first, reset])
    with pytest.raises(ValueError, match='状态链前驱'):
        validate_submission(frozen, tmp_path)
