"""Independent audit reproductions: mapping, reuse and event-chain boundaries."""
import copy
import pytest
from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core.io import read_json, write_json
from scripts.core import artifacts
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality


def test_unapproved_native_media_cannot_inherit_approved_local_reference(tmp_path):
    frozen, observed = delivery(tmp_path)
    for doc in (frozen, observed):
        for row in doc['drafts']:
            row['ordered_media'][0]['media_id'] = 'UNAPPROVED-media'
    for row in observed['drafts']:
        row['native_mentions'][0]['media_id'] = 'UNAPPROVED-media'
    bind_synthetic_quality(tmp_path, frozen)
    observed['drafts'] = [{**row, 'quality_evidence': frozen['drafts'][i]['quality_evidence']} for i, row in enumerate(observed['drafts'])]
    observed['frozen_sha256'] = digest(frozen)
    with pytest.raises(ValueError, match='upload'):
        compare(frozen, observed, tmp_path)


def test_one_upload_can_have_identity_and_boundary_roles(tmp_path):
    frozen, _ = delivery(tmp_path)
    row = frozen['drafts'][0]
    row['reference_checks'][0]['roles'] = ['identity', 'end_frame']
    row['reference_checks'][0].pop('role')
    row['required_reference_roles']['ACTOR'] = ['identity', 'end_frame']
    bind_synthetic_quality(tmp_path, frozen)
    assert validate_submission(frozen, tmp_path)['ok']


def interleaved(root):
    frozen, _ = delivery(root)
    insert = copy.deepcopy(frozen['drafts'][0]); insert['node_id'] = 'cutaway'
    insert['continuity'] = {'event_id': 'other-event'}
    frozen['drafts'].insert(1, insert)
    bind_synthetic_quality(root, frozen)
    return frozen


def test_event_chain_survives_cutaway(tmp_path):
    assert validate_submission(interleaved(tmp_path), tmp_path)['ok']


@pytest.mark.parametrize('mutation', ['missing', 'state', 'cycle', 'cross_event', 'unknown'])
def test_event_chain_missing_wrong_or_cyclic_predecessor_rejected(tmp_path, mutation):
    frozen = interleaved(tmp_path)
    row = frozen['drafts'][2]
    if mutation == 'missing': row['continuity'].pop('previous_node_id'); row['reference_checks'][0]['state_id'] = 'WRONG'
    elif mutation == 'state': row['reference_checks'][0]['state_id'] = 'WRONG'
    elif mutation == 'cycle': frozen['drafts'][0]['continuity']['previous_node_id'] = 'two'
    elif mutation == 'cross_event': row['continuity']['previous_node_id'] = 'cutaway'
    else: row['continuity']['previous_node_id'] = 'missing'
    with pytest.raises(ValueError): validate_submission(frozen, tmp_path)


def group_fixture(root):
    frozen, _ = delivery(root)
    original = artifacts.load_manifest(root)['artifacts'][0]
    image = root / 'other-angle.png'
    from PIL import Image
    Image.new('RGB', (8, 8), 'red').save(image)
    other = {**original, 'artifact_id': 'OTHER-ANGLE', 'logical_id': 'OTHER-ANGLE',
             'path_or_url': image.name, 'checksum': artifacts.digest(image),
             'upload_bindings': [{'target_url': frozen['target_url'], 'node_id': 'other-node',
                                 'media_id': 'other-media', 'revision': 'R01', 'sha256': artifacts.digest(image)}]}
    manifest = artifacts.load_manifest(root); manifest['artifacts'].append(other)
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    members = []
    for item, role, draft in [(original, 'end_frame', 'one'), (other, 'start_frame', 'two')]:
        members.append({'artifact_id': item['artifact_id'], 'revision': item['revision'], 'sha256': item['checksum'],
                        'roles': [role], 'draft_node_ids': [draft]})
    state = read_json(root / '00_项目管理/project_state.json')
    state['current_artifacts'].append(other['artifact_id'])
    state['user_confirmations'].append({'scope': 'artifact', 'artifact_ids': ['FRAME', 'OTHER-ANGLE'],
        'approval_ref': 'fixture:actual-group-approval', 'reference_groups': [{
            'group_id': 'BOUNDARY', 'event_id': 'one-event', 'state_id': 'handoff',
            'entity_ids': ['ACTOR'], 'members': members}]})
    write_json(root / '00_项目管理/project_state.json', state)
    head = frozen['drafts'][1]['reference_checks'][0]
    head.update(artifact_id=other['artifact_id'], sha256=other['checksum'])
    frozen['drafts'][1]['ordered_media'] = [{'node_id': 'other-node', 'media_id': 'other-media'}]
    for row in frozen['drafts']: row['reference_checks'][0]['reference_group_id'] = 'BOUNDARY'
    bind_synthetic_quality(root, frozen)
    return frozen


def test_confirmed_same_state_different_angle_group_passes(tmp_path):
    assert validate_submission(group_fixture(tmp_path), tmp_path)['ok']


@pytest.mark.parametrize('field', ['revision', 'sha256', 'roles', 'draft_node_ids', 'state_id', 'event_id', 'entity_ids', 'scope'])
def test_group_authority_is_exact_and_limited_to_approved_usage(tmp_path, field):
    frozen = group_fixture(tmp_path)
    path = tmp_path / '00_项目管理/project_state.json'; state = read_json(path)
    receipt = state['user_confirmations'][-1]; group = receipt['reference_groups'][0]
    if field in ['revision','sha256']: group['members'][1][field] = 'wrong'
    elif field in ['roles','draft_node_ids']: group['members'][1][field] = ['wrong']
    elif field == 'scope': receipt['scope'] = 'segment'; receipt['shot_ids'] = ['S01-001']
    elif field == 'entity_ids': group[field] = ['OTHER']
    else: group[field] = 'wrong'
    write_json(path, state)
    with pytest.raises(ValueError): validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('segments,valid', [(['one','two'], True), (['one'], False), (['two'], False), (['other'], False)])
def test_existing_segment_approval_can_cover_exact_boundary_usage(tmp_path, segments, valid):
    frozen = group_fixture(tmp_path)
    path = tmp_path / '00_项目管理/project_state.json'; state = read_json(path)
    group_receipt = state['user_confirmations'][-1]
    # The images have their own existing whole-image confirmation; only group
    # usage is scoped to the two segments. Do not escalate this to whole-film approval.
    state['user_confirmations'].append({'scope': 'artifact', 'artifact_ids': ['OTHER-ANGLE'],
                                        'approval_ref': 'fixture:whole-image'})
    group_receipt.update(scope='segment', segment_ids=segments)
    write_json(path, state)
    if valid: assert validate_submission(frozen, tmp_path)['ok']
    else:
        with pytest.raises(ValueError): validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('different_bytes,selected,selected_alias', [
    (True,'FRAME',True), (True,'ALIAS',True), (False,'FRAME',True), (False,'ALIAS',True),
    (True,'FRAME',False), (False,'FRAME',False)])
def test_reverse_upload_mapping_conflict_but_same_bytes_reuse_allowed(tmp_path, different_bytes, selected, selected_alias):
    frozen, _ = delivery(tmp_path)
    manifest = artifacts.load_manifest(tmp_path)
    original = manifest['artifacts'][0]
    alias = copy.deepcopy(original); alias.update(artifact_id='ALIAS', logical_id='ALIAS')
    if different_bytes:
        from PIL import Image
        image = tmp_path / 'other.png'; Image.new('RGB', (8,8), 'red').save(image)
        alias.update(path_or_url=image.name, checksum=artifacts.digest(image))
        alias['upload_bindings'][0]['sha256'] = alias['checksum']
    manifest['artifacts'].append(alias); write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    path = tmp_path / '00_项目管理/project_state.json'; state = read_json(path)
    if selected_alias: state['current_artifacts'].append('ALIAS')
    state['user_confirmations'].append({'artifact_ids': ['ALIAS'], 'approval_ref': 'fixture:alias'})
    write_json(path, state)
    if selected == 'ALIAS':
        for row in frozen['drafts']:
            row['reference_checks'][0].update(artifact_id='ALIAS', sha256=alias['checksum'])
    bind_synthetic_quality(tmp_path, frozen)
    if different_bytes:
        with pytest.raises(ValueError, match='conflict'): validate_submission(frozen, tmp_path)
    else: assert validate_submission(frozen, tmp_path)['ok']


def agent_group_fixture(root, *, mutation=None, conflicting_upload=False):
    """Different real fixture images share a state through an agent report only."""
    from scripts.tests.test_quality_evidence_consumers import agent_fixture, adopt_agent_fixture_entry
    from scripts.core import quality_evidence as q
    from PIL import Image
    def observe_first_boundary(frozen, image):
        first = frozen['drafts'][0]
        first['reference_checks'][0].update(roles=['end_frame'], state_id='handoff',
                                           reference_group_id='AGENT-BOUNDARY')
        first['required_reference_roles'] = {'E1': ['end_frame']}
        image['quality_evidence']['uses'].append(q.image_use(first['reference_checks'][0], first['source_shot_ids']))
    frozen, _ = agent_fixture(root, image_setup=observe_first_boundary)
    first = frozen['drafts'][0]
    second = copy.deepcopy(first)
    second.update(node_id='D2', source_shot_ids=['S01-020'])
    second['continuity']['previous_node_id'] = first['node_id']
    second['reference_checks'][0].update(artifact_id='OTHER-AGENT', roles=['start_frame'])
    second['required_reference_roles'] = {'E1': ['start_frame']}
    manifest = artifacts.load_manifest(root)
    original = next(item for item in manifest['artifacts'] if item['artifact_id'] == 'IMG')
    image = root / 'other-agent.png'
    Image.new('RGB', (8, 8), 'red').save(image)
    other = copy.deepcopy(original)
    other.update(artifact_id='OTHER-AGENT', logical_id='image.other-agent', path_or_url=image.name,
                 checksum=artifacts.digest(image), status='candidate', user_approved=False,
                 depends_on=['storyboard.package', original['logical_id']])
    other.pop('agent_acceptance')
    if not conflicting_upload:
        other['upload_bindings'][0].update(node_id='OTHER-NODE', media_id='OTHER-MEDIA')
    other['upload_bindings'][0]['sha256'] = other['checksum']
    second['reference_checks'][0]['sha256'] = other['checksum']
    second['ordered_media'] = [{key: other['upload_bindings'][0][key] for key in ('node_id', 'media_id')}]
    evidence = other['quality_evidence']
    evidence.update(image_sha256=other['checksum'], source_shot_ids=['S01-020'],
                    uses=[q.image_use(second['reference_checks'][0], second['source_shot_ids'])])
    group = {'group_id': 'AGENT-BOUNDARY', 'event_id': 'E', 'state_id': 'handoff',
             'entity_ids': ['E1'], 'members': [
                 {'artifact_id': item['artifact_id'], 'revision': item['revision'], 'sha256': item['checksum'],
                  'roles': [role], 'draft_node_ids': [row['node_id']]}
                 for item, role, row in [(original, 'end_frame', first), (other, 'start_frame', second)]]}
    if mutation in {'state_id', 'event_id'}: group[mutation] = 'WRONG'
    elif mutation == 'entity_ids': group[mutation] = ['WRONG']
    elif mutation in {'roles', 'draft_node_ids'}: group['members'][1][mutation] = ['WRONG']
    artifacts.register_workspace_artifact(root, other)
    other = adopt_agent_fixture_entry(root, other, usage={'source_shot_ids': ['S01-020']},
                                     reference_groups=[] if mutation == 'missing' else [group])
    frozen['drafts'].append(second)
    # The group-bearing report is an explicit source, not an arbitrary report scan.
    frozen['source_artifacts'].append({'artifact_id': other['artifact_id'], 'revision': other['revision'],
                                       'sha256': other['checksum']})
    for row in frozen['drafts']:
        row['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(row))
    return frozen


def test_agent_same_state_different_angle_group_uses_exact_report_without_human_receipt(tmp_path):
    frozen = agent_group_fixture(tmp_path)
    assert validate_submission(frozen, tmp_path)['ok']
    state = read_json(tmp_path / '00_项目管理/project_state.json')
    assert state['user_confirmations'] == [{'artifact_ids': ['N']}]
    assert all('reference_groups' not in receipt for receipt in state['user_confirmations'])


@pytest.mark.parametrize('mutation', ['missing', 'state_id', 'event_id', 'entity_ids', 'roles', 'draft_node_ids'])
def test_agent_group_still_requires_exact_boundary_usage(tmp_path, mutation):
    frozen = agent_group_fixture(tmp_path, mutation=mutation)
    with pytest.raises(ValueError, match='boundary'):
        validate_submission(frozen, tmp_path)


def test_reverse_upload_conflict_includes_agent_adopted_reference(tmp_path):
    frozen = agent_group_fixture(tmp_path, conflicting_upload=True)
    with pytest.raises(ValueError, match='conflicting upload'):
        validate_submission(frozen, tmp_path)
