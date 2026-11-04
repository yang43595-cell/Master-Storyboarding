"""Synthetic regressions for lock ownership and shared project metadata writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from scripts.core import artifacts, io, revision_control
from scripts.core.project_state import new_project_state, write_project_state, load_project_state

REPO = Path(io.__file__).resolve().parents[2]


def child(code, *args):
    return subprocess.Popen(
        [sys.executable, '-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]);\n' + code,
         str(REPO), *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_live_lock_cannot_be_stolen_by_age(tmp_path):
    lock = tmp_path / 'lock'
    code = '''from pathlib import Path
from scripts.core.io import file_lock
try:
    with file_lock(Path(sys.argv[2]), timeout_seconds=0.15):
        pass
except TimeoutError:
    sys.exit(42)
'''
    with io.file_lock(lock):
        os.utime(lock, (time.time() - 121, time.time() - 121))
        proc = child(code, lock)
        out, err = proc.communicate(timeout=5)
        assert proc.returncode == 42, (out, err)
    # A later owner uses the same lock file; no unlink/recreate race on release.
    inode = lock.stat().st_ino
    with io.file_lock(lock):
        assert lock.stat().st_ino == inode


def test_owner_exit_releases_lock_without_waiting_for_file_age(tmp_path):
    lock = tmp_path / 'lock'
    proc = child('''import os
from pathlib import Path
from scripts.core.io import file_lock
with file_lock(Path(sys.argv[2])):
    os._exit(0)
''', lock)
    proc.communicate(timeout=5)
    assert proc.returncode == 0
    with io.file_lock(lock, timeout_seconds=0.15):
        pass


def test_nested_import_aliases_release_only_at_outer_exit(tmp_path):
    # The CLI and the Web can import these two module names in one interpreter.
    from core.io import file_lock as cli_lock
    lock = tmp_path / 'lock'
    entered = threading.Event()
    failure = []

    def other_thread():
        try:
            with cli_lock(lock, timeout_seconds=2):
                entered.set()
        except Exception as exc:
            failure.append(exc)

    with io.file_lock(lock):
        with pytest.raises(ValueError, match='synthetic'):
            with cli_lock(lock, timeout_seconds=0.15):
                raise ValueError('synthetic nested failure')
        worker = threading.Thread(target=other_thread)
        worker.start()
        assert not entered.wait(0.1)
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert not failure
    assert entered.is_set()


def test_voice_lock_timeout_keeps_the_cli_error_contract(tmp_path, monkeypatch):
    from scripts import voice_reference_cli as voice

    def unavailable(project):
        raise TimeoutError('synthetic contention')

    monkeypatch.setattr(voice, 'shared_project_lock', unavailable)
    with pytest.raises(voice.VoiceReferenceError, match='无法获取项目声音锁'):
        with voice.project_lock(tmp_path):
            pytest.fail('a timed-out writer must not enter')


def test_parallel_registration_preserves_both_records_and_existing_state(tmp_path):
    initial = {'artifact_id': 'KEPT', 'logical_id': 'kept', 'status': 'current',
               'user_approved': True, 'depends_on': []}
    io.write_json(tmp_path / artifacts.MANIFEST_PATH, {'artifacts': [initial]})
    state = new_project_state(current_stage='storyboard-planner')
    state.update(current_artifacts=['KEPT'], user_confirmations=[{'artifact_ids': ['KEPT']}])
    write_project_state(tmp_path, state)
    state_path = tmp_path / '00_项目管理/project_state.json'
    state_bytes = state_path.read_bytes()
    first_read, second_read, resume = (threading.Event() for _ in range(3))
    real_load = artifacts.load_manifest
    failures = []

    def controlled_load(project):
        result = real_load(project)
        if threading.current_thread().name == 'first' and not first_read.is_set():
            first_read.set()
            assert resume.wait(3)
        elif threading.current_thread().name == 'second':
            second_read.set()
        return result

    def register(ident):
        try:
            artifacts.register_workspace_artifact(tmp_path, {
                'artifact_id': ident, 'logical_id': 'asset.' + ident,
                'type': 'image', 'path_or_url': ident + '.png'})
        except Exception as exc:
            failures.append(exc)

    with patch.object(artifacts, 'load_manifest', controlled_load):
        first = threading.Thread(name='first', target=register, args=('A',))
        second = threading.Thread(name='second', target=register, args=('B',))
        first.start()
        assert first_read.wait(3)
        second.start()
        try:
            assert not second_read.wait(0.1), 'second registration read stale metadata'
        finally:
            resume.set()
            first.join(timeout=4)
            second.join(timeout=4)
    assert not first.is_alive() and not second.is_alive()
    assert not failures
    rows = {row['artifact_id']: row for row in real_load(tmp_path)['artifacts']}
    assert set(rows) == {'KEPT', 'A', 'B'}
    assert rows['KEPT'] == initial
    assert rows['A']['status'] == rows['B']['status'] == 'candidate'
    assert state_path.read_bytes() == state_bytes


def test_separate_processes_register_without_lost_records(tmp_path):
    start = tmp_path / 'start'
    code = '''import time
from pathlib import Path
from scripts.core.artifacts import register_workspace_artifact
root, start, prefix = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
print('ready', flush=True)
while not start.exists():
    time.sleep(0.01)
for number in range(8):
    ident = prefix + str(number)
    register_workspace_artifact(root, dict(artifact_id=ident, logical_id='asset.'+ident,
                                        type='image', path_or_url=ident+'.png'))
'''
    processes = [child(code, tmp_path, start, prefix) for prefix in ('A', 'B', 'C')]
    try:
        for proc in processes:
            assert proc.stdout.readline().strip() == 'ready'
        start.touch()
        for proc in processes:
            out, err = proc.communicate(timeout=10)
            assert proc.returncode == 0, (out, err)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
    rows = artifacts.load_manifest(tmp_path)['artifacts']
    assert len(rows) == 24
    assert len({row['artifact_id'] for row in rows}) == 24


@pytest.mark.parametrize('import_alias', [False, True])
def test_stale_manifest_cannot_erase_a_new_registration(tmp_path, import_alias):
    from core import artifacts as cli_artifacts
    writer = cli_artifacts if import_alias else artifacts
    stale = artifacts.load_manifest(tmp_path)
    artifacts.register_workspace_artifact(tmp_path, dict(
        artifact_id='NEW', logical_id='asset.NEW', type='image', path_or_url='new.png'))
    before = (tmp_path / artifacts.MANIFEST_PATH).read_bytes()
    with pytest.raises(ValueError, match='其他任务更新'):
        writer.save_manifest(tmp_path, stale)
    assert (tmp_path / artifacts.MANIFEST_PATH).read_bytes() == before
    current = artifacts.load_manifest(tmp_path)
    current['note'] = 'ordinary edit'
    artifacts.save_manifest(tmp_path, current)
    current['note'] = 'second ordinary edit'
    artifacts.save_manifest(tmp_path, current)
    assert json.loads((tmp_path / artifacts.MANIFEST_PATH).read_bytes())['note'] == current['note']
    assert not any('digest' in key for key in current)


def test_voice_and_revision_share_the_same_project_lock(tmp_path):
    from scripts.voice_reference_cli import project_lock as voice_lock
    entered = threading.Event()
    failure = []

    def voice_writer():
        try:
            with voice_lock(tmp_path):
                entered.set()
        except Exception as exc:
            failure.append(exc)

    with io.file_lock(tmp_path / revision_control.LOCK_RELATIVE):
        worker = threading.Thread(target=voice_writer)
        worker.start()
        blocked = not entered.wait(0.1)
    worker.join(timeout=3)
    assert blocked, 'voice binding bypassed the project revision lock'
    assert not worker.is_alive() and not failure
    assert entered.is_set()

def _candidate_record(ident):
    return dict(artifact_id=ident, logical_id='asset.' + ident,
                type='image', path_or_url=ident + '.png', status='candidate')


@pytest.mark.parametrize('reader_name,writer_name', [
    ('scripts.core.artifacts', 'core.artifacts'),
    ('core.artifacts', 'scripts.core.artifacts'),
])
def test_manifest_snapshot_conflict_survives_import_aliases(tmp_path, reader_name, writer_name):
    import importlib
    reader, writer = map(importlib.import_module, (reader_name, writer_name))
    reader.register_workspace_artifact(tmp_path, _candidate_record('A'))
    stale = reader.load_manifest(tmp_path)
    writer.register_workspace_artifact(tmp_path, _candidate_record('B'))
    stale['note'] = 'an older task is still editing'
    manifest_path = tmp_path / artifacts.MANIFEST_PATH
    before = manifest_path.read_bytes()

    with pytest.raises(ValueError, match='其他任务更新'):
        writer.save_manifest(tmp_path, stale)

    assert manifest_path.read_bytes() == before
    assert {a['artifact_id'] for a in reader.load_manifest(tmp_path)['artifacts']} == {'A', 'B'}
    fresh = reader.load_manifest(tmp_path)
    for note in ('first cross-alias edit', 'second cross-alias edit'):
        fresh['note'] = note
        writer.save_manifest(tmp_path, fresh)
        assert reader.load_manifest(tmp_path)['note'] == note


@pytest.mark.parametrize('reader_name,writer_name', [
    ('scripts.core.project_state', 'core.project_state'),
    ('core.project_state', 'scripts.core.project_state'),
])
def test_stale_project_state_cannot_remove_another_tasks_confirmation(tmp_path, reader_name, writer_name):
    import importlib
    reader, writer = map(importlib.import_module, (reader_name, writer_name))
    writer.write_project_state(tmp_path, writer.new_project_state(current_stage='creative-director'))
    stale = reader.load_project_state(tmp_path)
    current = writer.load_project_state(tmp_path)
    confirmation = {'kind': 'synthetic-approval', 'approval_ref': 'synthetic:other-task'}
    current['user_confirmations'].append(confirmation)
    writer.write_project_state(tmp_path, current)
    state_path = tmp_path / '00_项目管理/project_state.json'
    view_path = state_path.with_suffix('.md')
    before = state_path.read_bytes(), view_path.read_bytes()
    stale['current_stage'] = 'storyboard-planner'

    with pytest.raises(ValueError, match='其他任务更新'):
        writer.write_project_state(tmp_path, stale)

    assert (state_path.read_bytes(), view_path.read_bytes()) == before
    assert reader.load_project_state(tmp_path)['user_confirmations'] == [confirmation]


def test_reconciled_state_keeps_its_read_version(tmp_path):
    from scripts.core.project_state import reconcile_current_artifacts
    io.write_json(tmp_path / artifacts.MANIFEST_PATH, {'artifacts': [
        dict(_candidate_record('KEPT'), status='current', user_approved=True)]})
    initial = new_project_state(current_stage='creative-director')
    initial['current_artifacts'] = ['KEPT']
    write_project_state(tmp_path, initial)
    stale = reconcile_current_artifacts(load_project_state(tmp_path), artifacts.load_manifest(tmp_path))
    current = load_project_state(tmp_path)
    current['user_confirmations'].append({'approval_ref': 'synthetic:new-confirmation'})
    write_project_state(tmp_path, current)
    before = (tmp_path / '00_项目管理/project_state.json').read_bytes()
    stale['next_stage'] = 'storyboard-planner'

    with pytest.raises(ValueError, match='其他任务更新'):
        write_project_state(tmp_path, stale)

    assert (tmp_path / '00_项目管理/project_state.json').read_bytes() == before


def test_fresh_state_snapshot_supports_repeated_saves_and_plain_initialization(tmp_path):
    initial = new_project_state(current_stage='creative-director')
    write_project_state(tmp_path, initial)
    # Existing callers use ordinary dictionaries when initializing or replacing
    # state explicitly; optimistic checks apply to versioned loaded snapshots.
    initial['next_stage'] = 'storyboard-planner'
    write_project_state(tmp_path, initial)
    loaded = load_project_state(tmp_path)
    for stage in ('storyboard-planner', 'asset-image-assistant'):
        loaded['current_stage'] = stage
        write_project_state(tmp_path, loaded)
        assert load_project_state(tmp_path)['current_stage'] == stage
    raw = json.loads((tmp_path / '00_项目管理/project_state.json').read_text(encoding='utf-8'))
    assert set(raw) == set(initial)


def test_voice_projection_change_invalidates_an_older_normalized_state(tmp_path):
    from scripts.voice_reference_cli import next_project_state, project_lock as voice_lock
    write_project_state(tmp_path, new_project_state(current_stage='storyboard-planner'))
    stale = load_project_state(tmp_path)
    voice_manifest = {'assignments': [
        {'character_id': 'CHR-A', 'voice_id': 'VOICE-A', 'status': 'current'}]}
    with voice_lock(tmp_path):
        state_path, projected = next_project_state(tmp_path, voice_manifest, audio_policy='dialogue')
        revision_control._commit_json_transaction_unlocked(tmp_path, {state_path: projected},
            reason='synthetic voice projection', preserve_state_extensions=True)
    before = state_path.read_bytes()
    stale['next_stage'] = 'asset-image-assistant'

    with pytest.raises(ValueError, match='其他任务更新'):
        write_project_state(tmp_path, stale)

    assert state_path.read_bytes() == before
    raw = json.loads(before)
    assert raw['voice_assignments'] == {'CHR-A': 'VOICE-A'}
    assert raw['audio_policy'] == 'dialogue'


def _interrupt_project_revision(project):
    io.write_json(project / artifacts.MANIFEST_PATH, {'artifacts': [
        dict(_candidate_record('A'), status='current', user_approved=True)]})
    initial = new_project_state(current_stage='creative-director')
    initial['current_artifacts'] = ['A']
    write_project_state(project, initial)
    pending_manifest = artifacts.load_manifest(project)
    pending_manifest['artifacts'].append(_candidate_record('B'))
    pending_state = load_project_state(project)
    pending_state['next_stage'] = 'storyboard-planner'
    pending_state['user_confirmations'].append({'approval_ref': 'synthetic:pending-revision'})
    with io.project_lock(project):
        with pytest.raises(RuntimeError, match='fault injection'):
            revision_control._commit_json_transaction_unlocked(project, {
                project / artifacts.MANIFEST_PATH: pending_manifest,
                project / '00_项目管理/project_state.json': pending_state,
            }, reason='synthetic interrupted revision', fail_after=1)


def test_pending_revision_recovery_cannot_erase_a_successful_registration(tmp_path):
    _interrupt_project_revision(tmp_path)
    artifacts.register_workspace_artifact(tmp_path, _candidate_record('C'))
    revision_control.recover_transactions(tmp_path)

    assert {a['artifact_id'] for a in artifacts.load_manifest(tmp_path)['artifacts']} == {'A', 'B', 'C'}
    state = load_project_state(tmp_path)
    assert state['next_stage'] == 'storyboard-planner'
    assert state['user_confirmations'] == [{'approval_ref': 'synthetic:pending-revision'}]


def test_pending_revision_recovery_cannot_erase_a_later_state_confirmation(tmp_path):
    _interrupt_project_revision(tmp_path)
    receipt = {'approval_ref': 'synthetic:after-interruption'}
    state = load_project_state(tmp_path)
    state['user_confirmations'].append(receipt)
    try:
        write_project_state(tmp_path, state)
    except ValueError as exc:
        # Recovery may correctly invalidate the snapshot read before the write
        # acquired its lock. The caller must then reload rather than overwrite.
        assert '其他任务更新' in str(exc)
        state = load_project_state(tmp_path)
        state['user_confirmations'].append(receipt)
        write_project_state(tmp_path, state)
    revision_control.recover_transactions(tmp_path)

    state = load_project_state(tmp_path)
    assert state['next_stage'] == 'storyboard-planner'
    assert state['user_confirmations'] == [
        {'approval_ref': 'synthetic:pending-revision'}, receipt]
    assert {a['artifact_id'] for a in artifacts.load_manifest(tmp_path)['artifacts']} == {'A', 'B'}


@pytest.mark.parametrize('drift_target', ['artifact_manifest.json', 'project_state.json'])
@pytest.mark.parametrize('entrypoint', ['explicit_recovery', 'automatic_recovery'])
def test_recovery_rejects_third_party_changes_before_writing_any_target(tmp_path, drift_target, entrypoint):
    _interrupt_project_revision(tmp_path)
    transaction_path, = (tmp_path / revision_control.TRANSACTION_ROOT).glob('*/transaction.json')
    transaction = io.read_json(transaction_path)
    # Model the valid crash window after durable intent, before any replacement.
    # A later conflicting state file must not let recovery first rewrite manifest.
    for record in transaction['files']:
        relative = Path(record['path'])
        before_path = transaction_path.parent / 'before' / relative
        io.write_bytes(tmp_path / relative, before_path.read_bytes())
    drift_path = tmp_path / '00_项目管理' / drift_target
    foreign = io.read_json(drift_path)
    if drift_target == 'project_state.json':
        foreign['user_confirmations'].append({'approval_ref': 'synthetic:third-party-write'})
    else:
        foreign['note'] = 'synthetic third-party write after interrupted transaction'
    io.write_json(drift_path, foreign)
    original = {record['path']: (tmp_path / record['path']).read_bytes()
                for record in transaction['files']}

    with pytest.raises(revision_control.RevisionControlError):
        if entrypoint == 'explicit_recovery':
            revision_control.recover_transactions(tmp_path)
        else:
            artifacts.register_workspace_artifact(tmp_path, _candidate_record('C'))

    assert {relative: (tmp_path / relative).read_bytes() for relative in original} == original
