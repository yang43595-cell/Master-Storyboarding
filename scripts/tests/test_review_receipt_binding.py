"""Old passing evidence must never approve a newer registered candidate."""
import pytest

from scripts.core import artifacts, revision_control as rc
from scripts.core.io import write_json, read_json
from scripts.core.project_state import new_project_state, write_project_state


def pending(root):
    source = root / 'old.json'
    source.write_text('{"revision":"R01"}')
    write_json(root / artifacts.MANIFEST_PATH, {'artifacts': [{
        'artifact_id': 'OLD', 'logical_id': 'fixture.review-target', 'revision': 'R01',
        'status': 'current', 'user_approved': True, 'path_or_url': source.name,
        'checksum': artifacts.digest(source), 'depends_on': []}]})
    state = new_project_state()
    state.update(current_artifacts=['OLD'], user_confirmations=[{'artifact_ids': ['OLD']}])
    write_project_state(root, state)
    rc.open_change(root, change_id='FIX', tier='T1', logical_ids=['fixture.review-target'], reason='repair')
    rc.confirm_impact(root, change_id='FIX', approval_ref='fixture:impact')
    candidate = root / 'new.json'
    candidate.write_text('{"revision":"R02","prompt":"current body"}')
    rc.record_candidate(root, change_id='FIX', artifact_id='NEW', logical_id='fixture.review-target',
                        artifact_type='json', candidate_path=candidate.name, revision='R02')
    return candidate


def test_mutation_before_review_cannot_receive_pass(tmp_path):
    candidate = pending(tmp_path)
    candidate.write_text('{"revision":"R02","prompt":"changed"}')
    with pytest.raises(rc.RevisionControlError, match='漂移'):
        rc.record_review(tmp_path, change_id='FIX', verdict='approved', report_ref='fixture:review')
    assert read_json(tmp_path / rc.CONTROL_RELATIVE)['active_change']['status'] == 'awaiting_review'


def test_manifest_candidate_drift_cannot_receive_confirmation(tmp_path):
    pending(tmp_path)
    rc.record_review(tmp_path, change_id='FIX', verdict='approved', report_ref='fixture:review')
    manifest = artifacts.load_manifest(tmp_path)
    candidate = next(item for item in manifest['artifacts'] if item['artifact_id'] == 'NEW')
    candidate['path_or_url'] = 'old.json'
    artifacts.save_manifest(tmp_path, manifest)

    with pytest.raises(rc.RevisionControlError, match='产物清单已变化'):
        rc.confirm_candidate(tmp_path, change_id='FIX', approval_ref='fixture:user')
    state = read_json(tmp_path / '00_项目管理/project_state.json')
    assert not any(item.get('artifact_id') == 'NEW' for item in state['user_confirmations'])


@pytest.mark.parametrize('field', ['artifact_id', 'revision', 'sha256'])
def test_replayed_pass_rejected_at_confirmation_and_adoption(tmp_path, field):
    pending(tmp_path)
    rc.record_review(tmp_path, change_id='FIX', verdict='approved', report_ref='fixture:review')
    path = tmp_path / rc.CONTROL_RELATIVE
    valid = read_json(path)
    stale = read_json(path)
    stale['active_change']['review'][field] = 'old-pass'
    write_json(path, stale)
    with pytest.raises(rc.RevisionControlError, match='旧 PASS'):
        rc.confirm_candidate(tmp_path, change_id='FIX', approval_ref='fixture:user')
    write_json(path, valid)
    rc.confirm_candidate(tmp_path, change_id='FIX', approval_ref='fixture:user')
    stale = read_json(path)
    stale['active_change']['review'][field] = 'old-pass'
    write_json(path, stale)
    with pytest.raises(rc.RevisionControlError, match='旧 PASS'):
        rc.commit_change(tmp_path, change_id='FIX')
