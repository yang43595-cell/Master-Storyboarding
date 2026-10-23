"""Synthetic dependency coverage and actual transfer-byte boundary checks."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from scripts.core.delivery_files import discover_dependencies, check_package_files
from scripts.core.io import write_json
from scripts.core import artifacts
from scripts.canvas_readback import validate_submission
from test_delivery_preflight import delivery


def test_nested_unlisted_dependencies_and_cycles_are_read_only(tmp_path):
    write_json(tmp_path/'root.json', {'references': [{'path': 'nested.json'}],
                                    'description': 'missing-prose.mp4'})
    write_json(tmp_path/'nested.json', {'paths': ['root.json', 'action clip.mp4'],
                                      'method_path': 'method.md'})
    (tmp_path/'action clip.mp4').write_bytes(b'synthetic media')
    (tmp_path/'method.md').write_text('synthetic method')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = discover_dependencies(tmp_path, ['root.json'])
    assert result['ok'] and len(result['checked_files']) == 4
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    (tmp_path/'action clip.mp4').unlink()
    result = discover_dependencies(tmp_path, ['root.json'])
    assert not result['ok'] and 'action clip.mp4' in str(result['errors'])


@pytest.mark.parametrize('fault', ['missing', 'empty', 'directory', 'escape', 'symlink', 'loop', 'remote', 'invalid_json'])
def test_unlisted_dependency_faults_fail_closed(tmp_path, fault):
    project = tmp_path/'project'; project.mkdir()
    raw = 'missing.mp4'
    if fault == 'empty':
        (project/'empty.mp4').touch(); raw = 'empty.mp4'
    elif fault == 'directory':
        (project/'dir').mkdir(); raw = 'dir'
    elif fault in ('escape', 'symlink'):
        outside = tmp_path/'outside.mp4'; outside.write_bytes(b'fixture')
        raw = '../outside.mp4'
        if fault == 'symlink':
            (project/'link.mp4').symlink_to(outside); raw = 'link.mp4'
    elif fault == 'loop':
        (project/'loop.mp4').symlink_to(project/'loop.mp4'); raw = 'loop.mp4'
    elif fault == 'remote': raw = 'https://fixture.invalid/input.mp4'
    elif fault == 'invalid_json':
        (project/'broken.json').write_text('{'); raw = 'broken.json'
    write_json(project/'root.json', {'action_references': [{'path': raw}]})
    report = discover_dependencies(project, ['root.json'])
    assert not report['ok']
    assert report['uncheckable'] if fault == 'remote' else report['errors']


def test_external_roots_and_optional_empty_paths(tmp_path):
    project = tmp_path/'project'; project.mkdir()
    vault = tmp_path/'vault'; vault.mkdir()
    (vault/'method.md').write_text('method')
    write_json(project/'root.json', {'method_path': str(vault/'method.md'), 'audio_path': ''})
    assert not discover_dependencies(project, ['root.json'])['ok']
    assert discover_dependencies(project, ['root.json'], [vault])['ok']


def test_manifest_dependency_not_in_manual_list(tmp_path):
    write_json(tmp_path/'source.json', {'references': ['unlisted.png']})
    write_json(tmp_path/artifacts.MANIFEST_PATH, {'artifacts': [
        {'artifact_id': 'EXACT', 'logical_id': 'source', 'path_or_url': 'source.json', 'status': 'current'}]})
    result = discover_dependencies(tmp_path, payload={'source_artifacts': [{'artifact_id': 'EXACT'}]})
    assert not result['ok'] and 'unlisted.png' in str(result['errors'])
    (tmp_path/'unlisted.png').write_bytes(b'fixture')
    assert discover_dependencies(tmp_path, payload={'depends_on': ['source']})['ok']
    assert not discover_dependencies(tmp_path, payload={'depends_on': ['absent']})['ok']


def test_submission_calls_dependency_discovery(tmp_path):
    frozen, _ = delivery(tmp_path)
    assert validate_submission(frozen, tmp_path)['ok']
    frozen['references'] = [{'video_path': 'not-manually-listed.mp4'}]
    with pytest.raises(ValueError, match='Dependency readiness failed'):
        validate_submission(frozen, tmp_path)


@pytest.mark.parametrize('size,ok', [(18_999_999, True), (19_000_000, False), (19_000_001, False)])
def test_actual_archive_byte_boundary(tmp_path, size, ok):
    path = tmp_path/'bundle.zip'
    # Stored ZIP overhead is constant for the same member name.
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('payload.bin', b'')
    overhead = path.stat().st_size
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('payload.bin', b'x'*(size-overhead))
    assert path.stat().st_size == size
    if ok: assert check_package_files([path])[0]['bytes'] == size
    else:
        with pytest.raises(ValueError, match='Rebuild a smaller self-contained package'):
            check_package_files([path])


@pytest.mark.parametrize('budget', [0, -1, True, 1.5])
def test_bad_package_budgets(tmp_path, budget):
    with pytest.raises(ValueError): check_package_files([], budget)


def test_compression_actual_bytes_and_all_packages_checked(tmp_path):
    archive = tmp_path/'compressed.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('large.txt', 'x'*10000)
    assert check_package_files([archive], 1000)[0]['bytes'] < 1000
    (tmp_path/'large.zip').write_bytes(b'x'*1000)
    with pytest.raises(ValueError): check_package_files([archive, tmp_path/'large.zip'], 1000)
    with pytest.raises(ValueError): check_package_files([tmp_path/'absent.zip'])


def test_cli_dependency_and_package_checkpoint(tmp_path):
    write_json(tmp_path/'source.json', {'file': 'absent.mp4'})
    package = tmp_path/'bundle.zip'; package.write_bytes(b'1234')
    command = [sys.executable, '-m', 'scripts.core.product_refs', '--project', str(tmp_path)]
    assert subprocess.run(command+['--discover-dependencies', '--require-file', 'source.json'], capture_output=True).returncode == 1
    assert subprocess.run(command+['--package', str(package), '--max-package-bytes', '5'], capture_output=True).returncode == 0
    assert subprocess.run(command+['--package', str(package), '--max-package-bytes', '4'], capture_output=True).returncode == 1


def test_move_restore_keeps_project_relative_discovery(tmp_path):
    source = tmp_path/'original'; source.mkdir()
    write_json(source/'root.json', {'file': 'media/source.mp4'})
    (source/'media').mkdir(); (source/'media/source.mp4').write_bytes(b'fixture')
    archive = tmp_path/'bundle.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for path in source.rglob('*'):
            if path.is_file(): z.write(path, path.relative_to(source))
    check_package_files([archive])
    with zipfile.ZipFile(archive) as z: z.extractall(tmp_path/'restored')  # Own synthetic ZIP only.
    assert discover_dependencies(tmp_path/'restored', ['root.json'])['ok']


@pytest.mark.parametrize('field', ['source_image', 'source_video', 'source_audio', 'reference_images', 'reference_videos'])
def test_existing_explicit_media_fields(field, tmp_path):
    report = discover_dependencies(tmp_path, payload={field: ['missing.mp4'] if field.startswith('reference_') else 'missing.mp4'})
    assert not report['ok'] and 'missing.mp4' in str(report['errors'])


def test_selected_file_follows_manifest_dependencies(tmp_path):
    write_json(tmp_path/'B.json', {'body': 'no duplicate dependency list'})
    write_json(tmp_path/artifacts.MANIFEST_PATH, {'artifacts': [
        {'artifact_id': 'B', 'logical_id': 'downstream', 'path_or_url': 'B.json', 'status': 'current', 'depends_on': ['upstream']},
        {'artifact_id': 'N', 'logical_id': 'upstream', 'path_or_url': 'missing.json', 'status': 'current'}]})
    assert not discover_dependencies(tmp_path, ['B.json'])['ok']
    assert not discover_dependencies(tmp_path, payload={'source_artifact_id': 'N'})['ok']
    assert not discover_dependencies(tmp_path, payload={'artifact_ids': ['N']})['ok']


def test_selected_candidate_dependencies_stay_unreviewed(tmp_path):
    from test_recovery_delivery import chain, snapshot
    project = chain(tmp_path/'project')
    before = snapshot(project)
    result = discover_dependencies(project, ['B.json'])
    assert result['ok'] and result['production_blocked'] and result['review_status'] == 'unreviewed'
    assert snapshot(project) == before
    (project/'N.json').unlink()
    assert not discover_dependencies(project, ['B.json'])['ok']


def test_ordinary_web_provenance_is_not_a_local_file_requirement(tmp_path):
    assert discover_dependencies(tmp_path, payload={
        'target_url': 'https://fixture.invalid/canvas',
        'source_url': 'https://fixture.invalid/evidence',
        'references': ['https://fixture.invalid/article', 'AST-ONE']})['ok']


def test_actual_delivery_cli_checks_package(tmp_path):
    frozen, _ = delivery(tmp_path)
    write_json(tmp_path/'frozen.json', frozen)
    package = tmp_path/'bundle.zip'; package.write_bytes(b'1234')
    script = Path(__file__).parents[1]/'canvas_readback.py'
    command = [sys.executable, str(script), '--delivery', '--preflight', '--project', str(tmp_path),
               '--frozen', str(tmp_path/'frozen.json'), '--package', str(package)]
    good = subprocess.run(command+['--max-package-bytes', '5'], capture_output=True, text=True)
    bad = subprocess.run(command+['--max-package-bytes', '4'], capture_output=True, text=True)
    assert good.returncode == 0, good.stdout+good.stderr
    assert json.loads(good.stdout)['packages'][0]['bytes'] == 4
    assert bad.returncode != 0 and not json.loads(bad.stdout)['ok']


@pytest.mark.parametrize('raw', ['https://fixture.invalid/image.png?download=1', 'https://fixture.invalid/action.mp4#t=1'])
def test_remote_media_query_and_fragment_are_uncheckable(tmp_path, raw):
    result = discover_dependencies(tmp_path, payload={'images': [raw]})
    assert not result['ok'] and result['uncheckable']


def test_source_artifact_ids_are_followed(tmp_path):
    assert not discover_dependencies(tmp_path, payload={'source_artifact_ids': ['MISSING']})['ok']
