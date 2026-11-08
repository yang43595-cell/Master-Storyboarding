"""Twelve synthetic regression groups: creation provenance is not approval."""
import copy

import pytest
from scripts.core import artifacts, story_sources
from scripts.core.io import write_json
from test_candidate_test_mode import setup, add
from test_story_paragraph_sources import narrative


def origin(kind='authored_addition', **kw):
    return dict(kind=kind, reference='synthetic-message-42', scope='add an advertising transition only',
                author='synthetic writer', purpose='transition', revision='R01', **kw)


def fixture(root, *, isolated=True):
    if isolated:
        setup(root)
    sources, rows = [], []
    for i, text in [(1, '甲句。'), (2, '乙句。')]:
        path = root / f'raw{i}.json'
        write_json(path, {'text': text})
        sources.append(dict(source_id=f'S{i}', kind='subtitle_verbatim', text=text,
            evidence_path=path.name, evidence_sha256=artifacts.digest(path), text_pointer='/text',
            locator='synthetic frame', producer='synthetic reader'))
        rows.append(dict(dialogue_id=f'DLG-0{i}', speaker_id='CHR-A', speaker_status='inferred_unverified', text=text, source_ids=[f'S{i}']))
    baseline = dict(sources=sources, dialogues=rows)
    write_json(root/'extracted.json', baseline)
    record = copy.deepcopy(baseline)
    for row in record['dialogues']:
        row['origin'] = {'kind': 'source_reproduction'}
    record['dialogues'].insert(1, dict(dialogue_id='DLG-NEW', speaker_id='CHR-A', speaker_status='inferred_unverified', text='新增衔接。', source_ids=[], origin=origin()))
    record['source_baseline'] = dict(path='extracted.json', sha256=artifacts.digest(root/'extracted.json'))
    return record


def bind(root, record):
    n = narrative(root)
    n.pop('human_readable_path')
    n['status'] = 'candidate'
    n['asset_requirements'][0].update(image_decision='需要制作', image_reason='synthetic',
                                      character_output_kind='人物单图')
    n['dialogues'] = [dict(dialogue_id=r['dialogue_id'], text=r['text'], speaker_id='CHR-A',
        source_paragraph_id='STP-01', status='draft', source_ref='review.json') for r in record['dialogues']]
    n['story_paragraphs'][0]['text'] = '\n'.join(f'主角（{r["dialogue_id"]}）："{r["text"]}"' for r in record['dialogues'])
    write_json(root/'review.json', record)
    n.update(dialogue_evidence_path='review.json', dialogue_evidence_sha256=artifacts.digest(root/'review.json'))
    return n


def check(root, record):
    return story_sources.validate_dialogue_review(record, root)


# 1: Insert between originals; full text/table still agrees; never approved.
def test_mixed_origin_candidate_chain(tmp_path):
    record = fixture(tmp_path)
    before = copy.deepcopy(record)
    assert check(tmp_path, record)['source_fidelity_checked']
    n = bind(tmp_path, record)
    result = story_sources.validate_story_sources(n, project_root=tmp_path, candidate_test=True)
    assert result['unresolved_dialogue_ids'] == ['DLG-01', 'DLG-NEW', 'DLG-02']
    assert result['production_blocked'] and result['review_status'] == 'unreviewed'
    assert record == before
    assert result['source_fidelity_checked']
    with pytest.raises(ValueError, match='未核'):
        story_sources.validate_story_sources(n, project_root=tmp_path)


# 2: Preserve raw evidence while explicitly mapping the authorized revision.
def test_authorized_revision_preserves_raw_sources(tmp_path):
    record = fixture(tmp_path)
    original = copy.deepcopy(record['sources'])
    row = record['dialogues'][0]
    row.update(text='修订句。', origin=origin('authorized_revision'),
        editorial_proposal=dict(original_text='甲句。', proposed_text='修订句。', reason='synthetic adaptation', source_ids=['S1']))
    assert check(tmp_path, record)['unresolved_ids'] == ['DLG-01', 'DLG-NEW', 'DLG-02']
    assert record['sources'] == original
    row['origin']['reference'] = ''
    with pytest.raises(ValueError, match='授权引用'): check(tmp_path, record)


# 3: A user directly supplies new text; that message is its origin, not audio.
def test_user_direct_addition_and_fully_original_work(tmp_path):
    row = dict(dialogue_id='DLG-USER', text='用户新增句。', source_ids=[], origin=origin())
    row['origin'].update(author='synthetic user', reference='synthetic-message-with-exact-new-text')
    report = check(tmp_path, dict(sources=[], dialogues=[row]))
    assert report['unresolved_ids'] == ['DLG-USER']
    assert not report['source_fidelity_checked']
    row.update(speaker_id='CHR-A', speaker_status='verified')
    row['verification'] = dict(scope='dialogue', dialogue_id='DLG-USER', speaker_id='CHR-A', text=row['text'],
        method='user_confirmation', reference='synthetic-exact-text-confirmation', reviewer='synthetic user')
    checked = check(tmp_path, dict(sources=[], dialogues=[row]))
    assert checked['unresolved_ids'] == [] and checked['verified_timing_ids'] == []


# 4: Evidence location/unknown identity does not silently turn into verified timing.
def test_unknown_source_information_is_preserved(tmp_path):
    record = fixture(tmp_path)
    record['dialogues'][0].update(speaker_id='unknown', source_locator={'start': 1, 'end': 2})
    report = check(tmp_path, record)
    assert report['verified_timing_ids'] == []
    assert 'DLG-01' in report['unresolved_ids']
    assert 'source_locator' not in record['dialogues'][1]


# 5: No new compulsory origin or baseline for historical packages.
def test_legacy_editorial_proposal_still_works(tmp_path):
    from test_source_lineage_guards import evidence
    record = evidence(tmp_path)
    row = record['dialogues'][0]
    row.update(text='新解释', editorial_proposal=dict(original_text='字甲', proposed_text='新解释', reason='synthetic', source_ids=['S']))
    assert check(tmp_path, record)['unresolved_ids'] == ['DLG-01']


# 6: Added/rewritten lines must never impersonate source audio.
@pytest.mark.parametrize('mutation', ['source', 'kind', 'listening', 'timing', 'locator', 'proposal', 'revision_listening'])
def test_fabricated_audio_provenance_is_rejected(tmp_path, mutation):
    record = fixture(tmp_path)
    row = record['dialogues'][1]
    if mutation == 'source': row['source_ids'] = ['S1']
    elif mutation == 'kind': row['origin']['kind'] = 'subtitle_verbatim'
    elif mutation in {'listening', 'revision_listening'}:
        row['verification'] = dict(scope='dialogue', dialogue_id=row['dialogue_id'], text=row['text'],
            method='human_listening', reference='synthetic', reviewer='synthetic')
        if mutation == 'revision_listening':
            row['origin']['kind'] = 'authorized_revision'; row['source_ids'] = ['S1']
            row['editorial_proposal'] = dict(original_text='甲句。', proposed_text=row['text'], reason='synthetic', source_ids=['S1'])
    elif mutation == 'timing': row['verified_timing'] = {'start': 1, 'end': 2}
    elif mutation == 'locator': row['source_locator'] = {'start': 1, 'end': 2}
    else: row['editorial_proposal'] = {}
    with pytest.raises(ValueError): check(tmp_path, record)


# 7: Not merely an empty or boolean "authorized" flag.
@pytest.mark.parametrize('key', ['reference', 'scope', 'author', 'purpose', 'revision'])
@pytest.mark.parametrize('bad', ['', '  ', True, None])
def test_missing_creation_basis_is_rejected(tmp_path, key, bad):
    record = fixture(tmp_path); record['dialogues'][1]['origin'][key] = bad
    with pytest.raises(ValueError, match='创作来源'): check(tmp_path, record)


# 8: Complete creative table and body remain one exact ordered set.
@pytest.mark.parametrize('mutation', ['body_missing', 'table_missing', 'review_missing', 'duplicate', 'text', 'order', 'speaker', 'paragraph'])
def test_complete_creative_table_remains_strict(tmp_path, mutation):
    record = fixture(tmp_path); n = bind(tmp_path, record)
    if mutation == 'body_missing': n['story_paragraphs'][0]['text'] = n['story_paragraphs'][0]['text'].split('\n')[0]
    elif mutation == 'table_missing': n['dialogues'].pop(1)
    elif mutation == 'review_missing':
        record['dialogues'].pop(1); write_json(tmp_path/'review.json', record)
        n['dialogue_evidence_sha256'] = artifacts.digest(tmp_path/'review.json')
    elif mutation == 'duplicate': n['dialogues'][1]['dialogue_id'] = 'DLG-01'
    elif mutation == 'text': n['dialogues'][1]['text'] = 'different'
    elif mutation == 'order': n['dialogues'].reverse()
    elif mutation == 'speaker': n['dialogues'][1]['speaker_id'] = 'MISSING'
    else: n['dialogues'][1]['source_paragraph_id'] = 'STP-MISSING'
    with pytest.raises(ValueError): story_sources.validate_story_sources(n, project_root=tmp_path, candidate_test=True)


# 9: Projection catches deletion/reordering even with authored rows between.
@pytest.mark.parametrize('mutation', ['delete', 'reorder', 'rewrite', 'relabel', 'raw_changed', 'baseline_bytes', 'wrong_original'])
def test_source_projection_cannot_hide_changes(tmp_path, mutation):
    record = fixture(tmp_path)
    if mutation == 'delete': record['dialogues'].pop(0)
    elif mutation == 'reorder': record['dialogues'].reverse()
    elif mutation == 'rewrite': record['dialogues'][0]['text'] = 'unauthorized'
    elif mutation == 'relabel': record['dialogues'][0].update(origin=origin(), source_ids=[])
    elif mutation == 'raw_changed': record['sources'][0]['producer'] = 'altered'
    elif mutation == 'baseline_bytes': write_json(tmp_path/'extracted.json', {})
    else:
        row = record['dialogues'][0]
        row.update(origin=origin('authorized_revision'), text='rewrite',
            editorial_proposal=dict(original_text='乙句。', proposed_text='rewrite', reason='synthetic', source_ids=['S1']))
    with pytest.raises(ValueError): check(tmp_path, record)


def test_explicit_deletion_and_reorder_preserve_sources(tmp_path):
    record = fixture(tmp_path); sources = copy.deepcopy(record['sources'])
    record['dialogues'].reverse()
    record['source_changes'] = [dict(action='reorder', dialogue_ids=['DLG-02', 'DLG-01'],
        reference='synthetic-user-message', scope='these two original lines only', reason='synthetic')]
    assert check(tmp_path, record)['source_fidelity_checked']
    record['dialogues'].pop(0)
    record['source_changes'] = [dict(action='delete', dialogue_id='DLG-02',
        reference='synthetic-user-message', scope='DLG-02 only', reason='synthetic')]
    assert check(tmp_path, record)['source_fidelity_checked']
    assert record['sources'] == sources
    record['source_changes'][0]['reference'] = ''
    with pytest.raises(ValueError): check(tmp_path, record)


# 10: Permission to create is not exact text confirmation nor production adoption.
def test_creation_permission_is_not_confirmation_or_adoption(tmp_path):
    record = fixture(tmp_path); row = record['dialogues'][1]
    row['verification'] = dict(scope='adaptation', dialogue_id='DLG-NEW', text=row['text'],
        method='user_confirmation', reference='synthetic general permission', reviewer='synthetic user')
    with pytest.raises(ValueError, match='核对记录'): check(tmp_path, record)
    row.pop('verification'); n = bind(tmp_path, record)
    add(tmp_path, 'N', 'narrative.package', n)
    with pytest.raises(ValueError, match='unreviewed'):
        artifacts.promote_candidate(tmp_path, 'narrative.package', 'N')


# 11: Partial removal fails in every mode; total removal cannot pass production.
@pytest.mark.parametrize('key', ['dialogue_evidence_path', 'dialogue_evidence_sha256'])
def test_partial_evidence_unbinding_is_rejected(tmp_path, key):
    n = bind(tmp_path, fixture(tmp_path)); n.pop(key)
    with pytest.raises(ValueError): story_sources.validate_story_sources(n, project_root=tmp_path, candidate_test=True)


@pytest.mark.parametrize('legacy_layout', [False, True])
def test_production_requires_binding_even_when_package_claims_legacy_layout(tmp_path, legacy_layout):
    n = narrative(tmp_path)
    if not legacy_layout:
        n.pop('human_readable_path')
        n['asset_requirements'][0].update(image_decision='需要制作', image_reason='synthetic')
    before = copy.deepcopy(n)
    # A consistency-only read preserves historical bytes, without certifying production.
    report = story_sources.validate_story_sources(n, project_root=tmp_path)
    assert report['dialogue_evidence_status'] == 'not_bound_unverified'
    assert report['unresolved_dialogue_ids'] == ['DLG-01']
    assert not report['formal_adoption_allowed'] and not report['source_fidelity_checked']
    with pytest.raises(ValueError, match='必须绑定 dialogue_evidence'):
        story_sources.validate_story_sources(n, project_root=tmp_path, require_dialogue_evidence=True)
    assert n == before


def verified_package(root, *, isolated=True):
    """A new synthetic record, not an upgrade of any historical confirmation."""
    record = fixture(root, isolated=isolated)
    for row in record['dialogues']:
        row['speaker_status'] = 'verified'
        row['verification'] = dict(scope='dialogue', dialogue_id=row['dialogue_id'],
            speaker_id=row['speaker_id'], text=row['text'], method='user_confirmation',
            reference='synthetic exact-text-and-speaker confirmation', reviewer='synthetic user')
    return bind(root, record), record


def test_valid_new_dialogue_binding_passes_production_source_validation(tmp_path):
    n, _ = verified_package(tmp_path)
    before = copy.deepcopy(n)
    story_sources.validate_story_sources(n, project_root=tmp_path, require_dialogue_evidence=True)
    assert n == before


@pytest.mark.parametrize('fault', ['both_removed', 'bytes', 'review_text', 'unverified'])
def test_new_bound_package_cannot_erase_or_replace_evidence_at_production_boundary(tmp_path, fault):
    n, record = verified_package(tmp_path)
    if fault == 'both_removed':
        n.pop('dialogue_evidence_path')
        n.pop('dialogue_evidence_sha256')
    else:
        if fault in {'bytes', 'review_text'}:
            record['dialogues'][1]['text'] = '其他新增句。'
            record['dialogues'][1]['verification']['text'] = '其他新增句。'
        else:
            record['dialogues'][0].pop('verification')
        write_json(tmp_path/'review.json', record)
        if fault != 'bytes':
            n['dialogue_evidence_sha256'] = artifacts.digest(tmp_path/'review.json')
    with pytest.raises(ValueError):
        story_sources.validate_story_sources(n, project_root=tmp_path, require_dialogue_evidence=True)


def test_silent_new_package_does_not_require_dialogue_record(tmp_path):
    n = narrative(tmp_path)
    n.pop('human_readable_path')
    n['dialogues'] = []
    n['story_paragraphs'][0]['text'] = '人物沉默。'
    n['asset_requirements'][0].update(image_decision='需要制作', image_reason='synthetic',
                                      character_output_kind='人物单图')
    story_sources.validate_story_sources(n, project_root=tmp_path, require_dialogue_evidence=True)


def test_explicit_isolated_unbound_candidate_still_reports_missing_evidence(tmp_path):
    n = bind(tmp_path, fixture(tmp_path))
    n.pop('dialogue_evidence_path')
    n.pop('dialogue_evidence_sha256')
    report = story_sources.validate_story_sources(n, project_root=tmp_path,
        candidate_test=True, require_dialogue_evidence=True)
    assert report['production_blocked'] and report['review_status'] == 'unreviewed'
    assert report['dialogue_evidence_status'] == 'not_bound_unverified'
    assert report['unresolved_dialogue_ids'] == [r['dialogue_id'] for r in n['dialogues']]


# 12: Provenance checks do not claim product/content truth or authentic approval.
def test_authorship_is_not_product_fact_verification(tmp_path):
    record = fixture(tmp_path)
    record['dialogues'][1]['text'] = '虚构示例：产品保证神奇功效。'
    report = check(tmp_path, record)
    assert 'DLG-NEW' in report['unresolved_ids']
    assert report['verification'] == 'source_consistency_only'
    assert 'product claims' in report['limits']
