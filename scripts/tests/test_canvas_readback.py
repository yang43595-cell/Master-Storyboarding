import copy
import pytest
from scripts.canvas_readback import compare, digest


def sample():
    frozen = {'target_url': 'https://fixture.invalid/canvas/one', 'drafts': [{
        'node_id': 'draft-13', 'prompt': 'body with native chip labels',
        'parameters': {'duration': 16, 'aspect': '9:16', 'model': 'fixture'},
        'ordered_media': [{'node_id': 'image-1', 'media_id': 'm1'},
                          {'node_id': 'image-2', 'media_id': 'm2'},
                          {'node_id': 'image-1', 'media_id': 'm1'}]}]}
    observed = {**copy.deepcopy(frozen), 'phase': 'after_refresh', 'frozen_sha256': digest(frozen)}
    return frozen, observed


def test_full_fields_and_repeated_order_pass():
    frozen, observed = sample()
    assert compare(frozen, observed)['ok']


def test_partial_dom_node_identity_never_substitutes_for_media_identity():
    frozen, observed = sample()
    frozen['drafts'][0]['native_entity_type'] = 'image'
    observed['frozen_sha256'] = digest(frozen)
    # Shape emitted by readRichEditor. Even if all other fields were obtained,
    # public node/type attributes do not prove the actual media version/bytes.
    observed['drafts'][0]['native_mentions'] = [
        {'node_id': ref['node_id'], 'entity_type': 'image', 'native_noneditable_entity': True}
        for ref in frozen['drafts'][0]['ordered_media']]
    result = compare(frozen, observed)
    assert not result['ok']
    assert result['native_reference_checks']['draft-13'] == 'failed'


@pytest.mark.parametrize('field', ['prompt', 'parameters', 'ordered_media', 'missing_fields', 'node_ids'])
def test_graph_equivalent_editor_mutations_fail(field):
    frozen, observed = sample()
    row = observed['drafts'][0]
    if field == 'prompt': row['prompt'] += 'wrong'
    elif field == 'parameters': row['parameters']['duration'] = 99
    elif field == 'ordered_media': row['ordered_media'][0]['media_id'] = 'wrong-media-same-label'
    elif field == 'missing_fields': row.pop('prompt')
    else: row['node_id'] = 'another-draft'
    assert not compare(frozen, observed)['ok']


def test_order_is_not_deduplicated_to_edges():
    frozen, observed = sample()
    observed['drafts'][0]['ordered_media'][0:2] = reversed(observed['drafts'][0]['ordered_media'][0:2])
    assert not compare(frozen, observed)['ok']


@pytest.mark.parametrize('field,value', [('target_url', 'https://fixture.invalid/canvas/other'),
    ('phase', 'before_refresh'), ('frozen_sha256', 'stale')])
def test_wrong_evidence_identity_rejected(field, value):
    frozen, observed = sample(); observed[field] = value
    with pytest.raises(ValueError): compare(frozen, observed)


def test_self_reported_refresh_without_native_readback_does_not_pass():
    frozen, observed = sample()
    frozen['drafts'][0]['native_entity_type'] = 'fixture-chip'
    observed['frozen_sha256'] = digest(frozen)
    # A phase label and matching write payload are claims, not native editor evidence.
    assert not compare(frozen, observed)['ok']
    observed['drafts'][0]['native_mentions'] = [{'entity_type': 'fixture-chip',
        'native_noneditable_entity': True, 'node_id': 'image-1', 'media_id': 'm1'},
        {'entity_type': 'fixture-chip', 'native_noneditable_entity': True,
         'node_id': 'image-2', 'media_id': 'm2'},
        {'entity_type': 'fixture-chip', 'native_noneditable_entity': True,
         'node_id': 'image-1', 'media_id': 'm1'}]
    report = compare(frozen, observed)
    assert report['ok'] and report['native_reference_checks']['draft-13'] == 'passed'
    assert 'cannot establish remote provenance' in report['limitations']


def test_duplicate_drafts_rejected():
    frozen, observed = sample(); observed['drafts'] *= 2
    with pytest.raises(ValueError): compare(frozen, observed)


def test_native_parameter_type_change_is_not_equal():
    frozen, observed = sample()
    frozen['drafts'][0]['parameters']['enabled'] = True
    observed['frozen_sha256'] = digest(frozen)
    observed['drafts'][0]['parameters']['enabled'] = 1
    assert not compare(frozen, observed)['ok']
