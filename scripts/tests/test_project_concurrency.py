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
from scripts.core.project_state import new_project_state, write_project_state

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
            # Leave startup/exit overhead beyond the production lock's 10s timeout,
            # so contention reports its real error instead of racing the watchdog.
            out, err = proc.communicate(timeout=30)
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
