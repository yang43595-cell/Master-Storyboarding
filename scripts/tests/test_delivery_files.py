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


def _image_qa_sources(path):
    import hashlib
    from scripts.core.quality_evidence import IMAGE_ASPECTS
    return {'status': 'pass', 'observer': 'synthetic-fixture',
            'checked_at': '2026-11-06T00:00:00+08:00', 'image_sha256': '1'*64,
            'view': {'resolution': 'original'}, 'uses': [{'roles': ['layout']}],
            'checks': {aspect: {} for aspect in IMAGE_ASPECTS},
            'source_files': [{'path': str(path),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}


def test_image_qa_checks_plan_bytes_without_requiring_historical_outputs(tmp_path):
    plan = tmp_path/'original-plan.json'
    write_json(plan, {'output_path': 'failed-v001.png', 'source_image': 'historical.jpg'})
    image = tmp_path/'selected-v002.png'; image.write_bytes(b'current image')
    payload = {'image_path': image.name, 'quality_evidence': _image_qa_sources(plan)}
    result = discover_dependencies(tmp_path, payload=payload, verify_hashes=True)
    assert result['ok'] and set(result['checked_files']) == {str(plan), str(image)}
    image.unlink()
    assert not discover_dependencies(tmp_path, payload=payload)['ok']


def test_historical_prompt_observation_does_not_select_old_media(tmp_path):
    prompt = tmp_path/'historical.json'
    write_json(prompt, {'image_path': 'old-missing.png'})
    evidence = {'quality_evidence': {'source_prompt': {
        'path': prompt.name, 'sha256': artifacts.digest(prompt), 'segment_id': 'V01', 'material_order': 2}}}
    assert discover_dependencies(tmp_path, payload=evidence)['ok']
    assert not discover_dependencies(tmp_path, files=[prompt], payload=evidence)['ok']
    prompt.write_text('{}')
    assert not discover_dependencies(tmp_path, payload=evidence)['ok']


def test_method_versions_resolve_only_within_recorded_and_explicit_roots(tmp_path):
    project = tmp_path/'project'; project.mkdir()
    methods = tmp_path/'methods'; methods.mkdir()
    method = methods/'method.md'; method.write_text('recorded method')
    record = {'path': 'method.md', 'version': '2026-11-06', 'sha256': artifacts.digest(method)}
    payload = {'method_basis': {'professional_methods_root': str(methods),
                                'professional_method_versions': [record]}}
    assert not discover_dependencies(project, payload=payload)['ok']
    assert discover_dependencies(project, input_roots=[methods], payload=payload, verify_hashes=True)['ok']
    (tmp_path/'outside.md').write_text('existing file outside the recorded method root')
    record['path'] = '../outside.md'
    assert not discover_dependencies(project, input_roots=[tmp_path], payload=payload)['ok']


def test_candidate_logical_dependency_uses_declared_exact_source(tmp_path):
    from scripts.core.candidate_test import configure
    source_kind = 'narrative.package'
    for i in (1, 2):
        name = f'narrative-{i}.json'
        write_json(tmp_path/name, {'status': 'candidate', 'revision': f'R0{i}'})
        artifacts.register_workspace_artifact(tmp_path, {
            'artifact_id': f'N{i}', 'logical_id': source_kind, 'type': source_kind,
            'revision': f'R0{i}', 'path_or_url': name})
    path = tmp_path/'director.json'
    source = {'artifact_id': 'N2', 'revision': 'R02', 'path': 'narrative-2.json'}
    write_json(path, {'status': 'candidate', 'revision': 'R01', 'source_narrative': source})
    artifacts.register_workspace_artifact(tmp_path, {
        'artifact_id': 'D1', 'logical_id': 'storyboard.package', 'type': 'storyboard.package',
        'revision': 'R01', 'path_or_url': path.name, 'depends_on': [source_kind]})
    configure(tmp_path, 'test-run', 'synthetic authorization', ['N1', 'N2', 'D1'])
    result = discover_dependencies(tmp_path, files=[path])
    assert result['ok'] and str(tmp_path/'narrative-1.json') not in result['checked_files']
    source['revision'] = 'R01'
    write_json(path, {'status': 'candidate', 'revision': 'R01', 'source_narrative': source})
    assert not discover_dependencies(tmp_path, files=[path])['ok']


def test_image_qa_still_rejects_missing_or_changed_source_record(tmp_path):
    plan = tmp_path/'original-plan.json'; write_json(plan, {'output_path': 'future.png'})
    payload = {'quality_evidence': _image_qa_sources(plan)}
    plan.write_text('{}')
    assert not discover_dependencies(tmp_path, payload=payload, verify_hashes=True)['ok']
    plan.unlink()
    assert not discover_dependencies(tmp_path, payload=payload)['ok']


@pytest.mark.parametrize('evidence_first', [True, False])
def test_qa_byte_source_cannot_hide_same_file_selected_as_active_input(tmp_path, evidence_first):
    plan = tmp_path/'plan.json'; write_json(plan, {'file': 'required-but-missing.png'})
    qa = {'quality_evidence': _image_qa_sources(plan)}
    active = {'file': plan.name}
    payload = [qa, active] if evidence_first else [active, qa]
    assert not discover_dependencies(tmp_path, payload=payload)['ok']
    assert not discover_dependencies(tmp_path, payload={'source_files': qa['quality_evidence']['source_files']})['ok']


def test_image_qa_source_json_is_still_parsed_for_corruption(tmp_path):
    plan = tmp_path/'broken.json'; plan.write_text('{')
    assert not discover_dependencies(tmp_path, payload={'quality_evidence': _image_qa_sources(plan)})['ok']


def test_superseded_qa_does_not_override_current_source_bytes(tmp_path):
    plan = tmp_path/'source.json'; write_json(plan, {'output_path': 'old-failed.png'})
    previous = _image_qa_sources(plan)
    plan.write_text('{}')
    current = _image_qa_sources(plan)
    payload = {'quality_evidence': current,
               'metadata': {'previous_quality_evidence_records': [{'quality_evidence': previous}]}}
    assert discover_dependencies(tmp_path, payload=payload, verify_hashes=True)['ok']
    plan.write_text('{"changed": true}')
    assert not discover_dependencies(tmp_path, payload=payload, verify_hashes=True)['ok']


def test_structured_diff_json_pointer_is_not_a_file(tmp_path):
    payload = {'diff': [{'path': '/scenes/0/shots/0/take/end', 'before': 'old', 'after': 'new'},
                        {'path': '/revision', 'before': 'R01', 'after': 'R02'}]}
    result = discover_dependencies(tmp_path, payload=payload, verify_hashes=True)
    assert result['ok'] and not result['checked_files']
    payload['diff'][0]['after'] = {'file_path': 'missing.png'}
    assert not discover_dependencies(tmp_path, payload=payload)['ok']
    assert not discover_dependencies(tmp_path, payload={'path': '/revision'})['ok']


def test_pdf_page_locator_checks_actual_file_and_hash(tmp_path):
    import hashlib
    pdf = tmp_path/'product manual.pdf'
    pdf.write_bytes(b'%PDF synthetic source')
    payload = {'reference': {'path': 'product manual.pdf#page=2',
                             'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest()}}
    result = discover_dependencies(tmp_path, payload=payload, verify_hashes=True)
    assert result['ok'] and result['checked_files'] == [str(pdf)]
    payload['reference']['sha256'] = '0'*64
    assert not discover_dependencies(tmp_path, payload=payload, verify_hashes=True)['ok']
    pdf.unlink()
    assert not discover_dependencies(tmp_path, payload=payload)['ok']


def test_pdf_page_locator_keeps_root_boundary(tmp_path):
    project = tmp_path/'project'; project.mkdir()
    (tmp_path/'outside.pdf').write_bytes(b'%PDF fixture')
    result = discover_dependencies(project, payload={'path': '../outside.pdf#page=1'})
    assert not result['ok'] and result['errors']


def test_existing_filename_with_page_suffix_is_not_reinterpreted(tmp_path):
    literal = tmp_path/'source.pdf#page=1'
    literal.write_bytes(b'literal name')
    result = discover_dependencies(tmp_path, payload={'path': literal.name})
    assert result['ok'] and result['checked_files'] == [str(literal)]


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


def test_reused_file_requires_exact_identity_and_digest(tmp_path):
    import hashlib
    image = tmp_path / 'reused.png'
    image.write_bytes(b'synthetic unchanged image')
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    write_json(tmp_path / artifacts.MANIFEST_PATH, {'artifacts': [
        {'artifact_id': 'OLD', 'logical_id': 'image', 'path_or_url': image.name,
         'status': 'superseded', 'checksum': digest},
        {'artifact_id': 'NEW', 'logical_id': 'image', 'path_or_url': image.name,
         'status': 'current', 'checksum': digest}]})
    payload = {'artifact_id': 'NEW', 'path': image.name, 'sha256': digest}
    assert discover_dependencies(tmp_path, payload=payload, verify_hashes=True)['ok']
    old_binding = {**payload, 'artifact_id': 'OLD'}
    assert discover_dependencies(tmp_path, payload=[payload, old_binding])['ok']
    assert discover_dependencies(tmp_path, payload=[old_binding, payload])['ok']
    for unbound in [{'path': image.name}, {**old_binding, 'sha256': '0' * 64}]:
        for references in ([payload, unbound], [unbound, payload]):
            report = discover_dependencies(tmp_path, payload={'references': references})
            assert not report['ok'] and 'ambiguous manifest entries' in str(report['errors'])
            assert report['checked_files'] == [str(image)]
    for invalid in [{'path': image.name}, {**payload, 'artifact_id': 'MISSING'},
                    {**payload, 'sha256': '0' * 64}]:
        assert not discover_dependencies(tmp_path, payload=invalid, verify_hashes=True)['ok']
    image.write_bytes(b'changed image')
    assert not discover_dependencies(tmp_path, payload=payload, verify_hashes=True)['ok']


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
