"""Small synthetic preview replays; no browser, uploads or provider calls."""
import copy
import json

import pytest

from scripts.canvas_readback import compare, digest, validate_submission, preview_scope
from scripts.core import artifacts, candidate_test
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state
from scripts.tests.test_delivery_preflight import delivery, bind_synthetic_quality


def seal(frozen):
    auth = frozen['preview_authorization']
    auth['drafts_sha256'] = digest(frozen['drafts'])
    auth['sources_sha256'] = digest(frozen['source_artifacts'])
    auth['cleanup_sha256'] = digest(frozen.get('cleanup', []))


def preview(root):
    frozen, _ = delivery(root)
    manifest = artifacts.load_manifest(root)
    for item in manifest['artifacts']:
        item.update(status='candidate', user_approved=False)
    write_json(root / artifacts.MANIFEST_PATH, manifest)
    write_project_state(root, new_project_state())
    candidate_test.configure(root, 'TEST', 'synthetic-only', ['FRAME', 'DIRECTOR'])
    for ref in frozen['source_artifacts'] + [r for d in frozen['drafts'] for r in d['reference_checks']]:
        item = next(a for a in manifest['artifacts'] if a['artifact_id'] == ref['artifact_id'])
        ref.update(project_path=str(root), path=str(root / item['path_or_url']), record_sha256=item['checksum'])
    frozen.update(target_project_id='exact', status='candidate', review_status='unreviewed', production_blocked=True,
                  operations=['upload_references', 'save_drafts'])
    for row in frozen['drafts']:
        for ref, media in zip(row['reference_checks'], row['ordered_media']):
            ref['limited_use'] = dict(conclusion='usable_for_private_preview', reviewer='fixture root',
                review_ref='synthetic-review', observation='Synthetic blue square only',
                draft_node_ids=['one','two'], entity_ids=['ACTOR'], roles=['start_frame','end_frame'], excluded_roles=['identity'])
            ref['upload_binding'] = dict(target_url=frozen['target_url'], target_project_id='exact',
                                         **media, revision=ref['revision'], sha256=ref['sha256'])
    frozen['preview_authorization'] = dict(mode='private_candidate_preview', project_path=str(root),
        visibility='private', target_url=frozen['target_url'], target_project_id='exact',
        source_projects=[str(root)], evidence=[dict(message_id='synthetic-only', quote='Synthetic private draft permission')],
        allowed_operations=frozen['operations'].copy())
    seal(frozen)
    return frozen


def observed(frozen):
    value = copy.deepcopy(frozen)
    value.update(phase='after_refresh', frozen_sha256=digest(frozen))
    for row in value['drafts']:
        row['native_mentions'] = [{**m, 'entity_type':'fixture-chip', 'native_noneditable_entity':True}
                                  for m in row['ordered_media']]
    return value


def test_preview_real_bytes_and_readback_never_mutate_or_promote(tmp_path):
    frozen = preview(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = compare(frozen, observed(frozen), tmp_path, private_preview=True)
    assert report['ok'] and report['submission_checks']['production_blocked'] is True
    assert all(p.read_bytes() == content for p, content in before.items())
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)
    frozen.pop('preview_authorization')
    with pytest.raises(ValueError):
        validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('mutation', ['no_scope','no_quote','wrong_url','wrong_project','wrong_source_root',
    'source_hash','media_hash','record_hash','stale_revision','missing_file','native_binding','role_scope',
    'generation','generation_field','generation_parameter','promoted','no_draft_hash','no_source_hash',
    'missing_native_type','missing_ref','duration','boundary','cleanup_used','cleanup_referenced'])
def test_preview_fail_closed(tmp_path, mutation):
    frozen = preview(tmp_path)
    auth = frozen['preview_authorization']; ref = frozen['drafts'][0]['reference_checks'][0]
    if mutation == 'no_scope': frozen.pop('preview_authorization')
    elif mutation == 'no_quote': auth['evidence'][0]['quote'] = ''
    elif mutation == 'wrong_url': frozen['target_url'] += '-other'
    elif mutation == 'wrong_project': frozen['target_project_id'] = 'other'
    elif mutation == 'wrong_source_root': ref['project_path'] = str(tmp_path.parent)
    elif mutation == 'source_hash': frozen['source_artifacts'][0]['sha256'] = '0'*64
    elif mutation == 'media_hash': ref['sha256'] = '0'*64
    elif mutation == 'record_hash': ref['record_sha256'] = '0'*64
    elif mutation == 'stale_revision': ref['revision'] = 'wrong'
    elif mutation == 'missing_file': (tmp_path/'frame.png').unlink()
    elif mutation == 'native_binding': ref['upload_binding']['media_id'] = 'other'
    elif mutation == 'role_scope': ref['limited_use']['roles'] = ['identity']
    elif mutation == 'generation': frozen['operations'].append('generate_video'); auth['allowed_operations'].append('generate_video')
    elif mutation == 'generation_field': frozen['generate'] = True
    elif mutation == 'generation_parameter': frozen['drafts'][0]['parameters']['auto_generate'] = True
    elif mutation == 'promoted': frozen['status'] = 'confirmed'
    elif mutation == 'no_draft_hash': auth['drafts_sha256'] = 'wrong'
    elif mutation == 'no_source_hash': auth['sources_sha256'] = 'wrong'
    elif mutation == 'missing_native_type': frozen['drafts'][0].pop('native_entity_type')
    elif mutation == 'missing_ref': frozen['drafts'][0]['reference_checks'] = []
    elif mutation == 'duration': frozen['drafts'][0]['parameters']['duration'] = 100
    elif mutation == 'boundary': frozen['drafts'][1]['reference_checks'][0]['state_id'] = 'wrong'
    elif mutation.startswith('cleanup_'):
        frozen['operations'].append('remove_unreferenced_candidates'); auth['allowed_operations'].append('remove_unreferenced_candidates')
        frozen['cleanup'] = [dict(node_id='frame-node' if mutation == 'cleanup_used' else 'old',
            target_url=frozen['target_url'], target_project_id='exact', status='candidate',
            reference_count=0 if mutation == 'cleanup_used' else 1, phase='after_refresh',
            readback_ref='synthetic', authorization_ref='synthetic')]
    if mutation not in {'no_scope','no_draft_hash','no_source_hash'}: seal(frozen)
    with pytest.raises((ValueError, KeyError)):
        validate_submission(frozen, tmp_path, private_preview=True)


@pytest.mark.parametrize('mutation', ['refresh','native_entity','project','hash'])
def test_preview_requires_actual_refresh_native_evidence(tmp_path, mutation):
    frozen = preview(tmp_path); got = observed(frozen)
    if mutation == 'refresh': got['phase'] = 'before_refresh'
    elif mutation == 'native_entity': got['drafts'][0]['native_mentions'][0]['native_noneditable_entity'] = False
    elif mutation == 'project': got['target_project_id'] = 'wrong'
    else: got['frozen_sha256'] = 'wrong'
    if mutation == 'native_entity':
        assert not compare(frozen, got, tmp_path, private_preview=True)['ok']
    else:
        with pytest.raises(ValueError): compare(frozen, got, tmp_path, private_preview=True)


@pytest.mark.parametrize('failure', ['none','original','later'])
def test_real_trial_receipt_and_limited_failed_purpose(tmp_path, failure):
    from scripts.tests.test_asset_generation_trial import TrialTests
    from scripts import asset_generation_trial as trial
    fixture = TrialTests(); fixture.setUp()
    try:
        trial.preflight(fixture.root, fixture.request)
        receipt = trial.finish(fixture.root, 'A01', fixture.output(),
            {'passed': failure != 'original', 'notes':'Synthetic identity purpose fails; spatial blue-square purpose separate'})
        record = fixture.root/'attempts/A01/result.json'
        if failure == 'later': fixture.reject(receipt)
        original = record.read_bytes()
        frozen = preview(tmp_path)
        frozen['preview_authorization']['source_projects'].append(str(fixture.root))
        frozen['preview_authorization']['input_roots'] = [str(fixture.base)]
        for row in frozen['drafts']:
            ref = row['reference_checks'][0]
            ref.update(project_path=str(fixture.root), artifact_id='A01', revision='R01',
                record_sha256=artifacts.digest(record), path=receipt['image']['path'], sha256=receipt['image']['sha256'],
                trial_provenance=[trial.snap(record.parent/'request.json'), trial.snap(fixture.root/'asset_generation_trial.json')],
                review_evidence=[trial.snap(p) for p in sorted((record.parent/'review-overrides').glob('*.json'))])
            ref['upload_binding']['sha256'] = ref['sha256']
        # Qualified synthetic pixels need exact image/source/use evidence too.
        from scripts.core import quality_evidence as q
        from scripts.tests.test_quality_evidence_consumers import observations
        manifest = artifacts.load_manifest(fixture.root)
        item = next(a for a in manifest['artifacts'] if a['artifact_id'] == 'A01')
        evidence = observations(q.IMAGE_ASPECTS)
        evidence.update(image_sha256=receipt['image']['sha256'],
            source_files=[{'path': str(fixture.source), 'sha256': artifacts.digest(fixture.source)}],
            source_shot_ids=['SYNTHETIC-SHOT'], view={'resolution':'original','locator':'synthetic generated pixels'},
            uses=[q.image_use(row['reference_checks'][0], row['source_shot_ids']) for row in frozen['drafts']])
        item['quality_evidence'] = evidence
        write_json(fixture.root / artifacts.MANIFEST_PATH, manifest)
        seal(frozen)
        if failure != 'none':
            with pytest.raises(ValueError, match='failed pixels'):
                validate_submission(frozen, tmp_path, private_preview=True)
            for row in frozen['drafts']:
                row['reference_checks'][0]['limited_use']['failure_scope_note'] = 'Identity remains failed; root inspected spatial end/start state only.'
            seal(frozen)
        # New authorized contract: a purpose note cannot waive failed active pixels.
        if failure == 'none':
            assert compare(frozen, observed(frozen), tmp_path, private_preview=True)['ok']
        else:
            with pytest.raises(ValueError, match='failed pixels'):
                compare(frozen, observed(frozen), tmp_path, private_preview=True)
        assert record.read_bytes() == original
        # The preview declaration never repairs the actual generation baseline.
        if failure != 'none':
            with pytest.raises(ValueError): trial.check_reference_reviews(fixture.root, [receipt['image']])
        (record.parent/'request.json').write_text('{}')
        with pytest.raises(ValueError, match='provenance'):
            validate_submission(frozen, tmp_path, private_preview=True)
    finally:
        fixture.tearDown()


def test_cleanup_is_exact_recoverable_zero_reference_and_read_back(tmp_path):
    frozen = preview(tmp_path)
    frozen['operations'].append('remove_unreferenced_candidates')
    frozen['preview_authorization']['allowed_operations'].append('remove_unreferenced_candidates')
    frozen['cleanup'] = [dict(node_id='old-unused', target_url=frozen['target_url'], target_project_id='exact',
        status='candidate', reference_count=0, reference_scan='whole_canvas', recoverable=True,
        phase='after_refresh', readback_ref='synthetic', authorization_ref='synthetic-only')]
    seal(frozen)
    got = observed(frozen)
    assert not compare(frozen, got, tmp_path, private_preview=True)['ok']
    got['cleanup_results'] = [{'node_id':'old-unused','absent':True}]
    assert compare(frozen, got, tmp_path, private_preview=True)['ok']
    frozen['cleanup'][0]['recoverable'] = False; seal(frozen)
    with pytest.raises(ValueError): validate_submission(frozen, tmp_path, private_preview=True)


def test_cli_preview_and_formal_modes_are_separate(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    frozen = preview(tmp_path)
    path = tmp_path/'frozen.json'; path.write_text(json.dumps(frozen))
    command = [sys.executable, str(Path(__file__).parents[1]/'canvas_readback.py'),
               '--project',str(tmp_path),'--frozen',str(path),'--preflight']
    accepted = subprocess.run(command + ['--private-preview'], capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert json.loads(accepted.stdout)['production_blocked'] is True
    for mode in ([], ['--delivery'], ['--delivery','--private-preview']):
        rejected = subprocess.run(command + mode, capture_output=True, text=True)
        assert rejected.returncode == 2


def test_preserved_display_provenance_does_not_replay_old_methods(tmp_path):
    frozen = preview(tmp_path)
    frame = frozen['drafts'][0]['reference_checks'][0]
    request = tmp_path/'request.json'; request.write_text(json.dumps({'methods':[{'path':'/old/method.md','sha256':'old'}]}))
    result = tmp_path/'result.json'; result.write_text(json.dumps({'image':{'path':'/old/image.png','sha256':frame['sha256']},
        'outcome':'failed_candidate','assessment':{'passed':False,'notes':'identity failed'}}))
    review = tmp_path/'root-review.json'; review.write_text(json.dumps({'observation':'Synthetic state only; identity still fails'}))
    snap = lambda p: {'location':str(p),'sha256':artifacts.digest(p)}
    manifest = artifacts.load_manifest(tmp_path)
    entry = next(a for a in manifest['artifacts'] if a['artifact_id']=='FRAME')
    entry.update(type='preview-reference', preview_origin={
        'purpose':'private_preview_only_not_generation_baseline', 'source_sha256':frame['sha256'],
        'history_gaps':['Original method file unavailable; historical generator not revalidated'],
        'historical_evidence':[snap(request),snap(result),snap(review)]})
    write_json(tmp_path/artifacts.MANIFEST_PATH, manifest)
    for row in frozen['drafts']:
        row['reference_checks'][0]['limited_use']['review_evidence'] = snap(review)
    seal(frozen)
    with pytest.raises(ValueError, match='failed pixels'):
        validate_submission(frozen,tmp_path,private_preview=True)
    for row in frozen['drafts']:
        row['reference_checks'][0]['limited_use']['failure_scope_note']='Identity remains failed; state-only preview independently inspected.'
    seal(frozen)
    # Preserved history is readable; adding it to active references remains forbidden.
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    history_reader, _, reports = preview_scope(frozen, tmp_path)
    _, historical = history_reader(frozen['drafts'][0]['reference_checks'][0])
    assert historical['_has_later_failure'] is True
    assert any(x.get('historical_generation_revalidated') is False for x in reports)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='failed pixels'):
        validate_submission(frozen,tmp_path,private_preview=True)
    review.write_text('{}')
    with pytest.raises(ValueError, match='provenance evidence file/hash'):
        validate_submission(frozen,tmp_path,private_preview=True)


def test_candidate_state_reference_is_not_a_confirmed_hard_frame(tmp_path):
    frozen=preview(tmp_path)
    for row in frozen['drafts']:
        ref=row['reference_checks'][0]
        ref['role']='state_reference'; ref['limited_use']['roles']=['state_reference']
        row['required_reference_roles']={'ACTOR':['state_reference']}
    continuity=frozen['drafts'][1]['continuity']
    sha=frozen['drafts'][0]['reference_checks'][0]['sha256']
    continuity.update(mode='state_reference',boundary={'claim':'shared_state_only_not_hard_frames',
        'state_id':'handoff','description':'Candidate shared state; no strict first/last frame guarantee',
        'previous_node_id':'one','current_node_id':'two','previous_sha256s':[sha],'current_sha256s':[sha]})
    bind_synthetic_quality(tmp_path, frozen)
    seal(frozen)
    assert validate_submission(frozen,tmp_path,private_preview=True)['ok']
    with pytest.raises(ValueError): validate_submission(frozen,tmp_path)
    continuity['boundary']['current_sha256s']=['wrong'];seal(frozen)
    with pytest.raises(ValueError,match='state boundary'):
        validate_submission(frozen,tmp_path,private_preview=True)
