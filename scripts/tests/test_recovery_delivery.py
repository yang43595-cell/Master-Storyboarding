"""Bounded synthetic recovery checks; never load real cases or modify approvals."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest
from scripts import stage_context_cli as cli
from scripts.core import artifacts, candidate_test as mode
from scripts.core.io import write_json
from test_candidate_test_mode import setup, add
from test_stage_context_cli import _project, _method_vault


def snapshot(project):
    return {p.relative_to(project).as_posix(): p.read_bytes()
            for p in project.rglob('*') if p.is_file()}


def chain(project):
    setup(project)
    add(project, 'N', 'narrative.package', {'status': 'candidate', 'revision': 'R01'})
    add(project, 'B', 'storyboard.package', {'status': 'candidate', 'revision': 'R02',
        'source_narrative': {'revision': 'R01', 'path': 'N.json'}}, ['narrative.package'])
    return project


def test_required_external_method_fails_before_startup(tmp_path):
    project = _project(tmp_path)
    request = tmp_path/'request.json'
    write_json(request, {'use_story_method': True})
    before = snapshot(project)
    args = cli.parser().parse_args(['prepare', '--project', str(project), '--skill',
        'creative-director', '--objective', 'synthetic recovery', '--vault', str(tmp_path/'missing-vault'),
        '--request-json', str(request)])
    with pytest.raises(ValueError, match='专业方法文件缺失'):
        cli.prepare(args)
    assert snapshot(project) == before


def test_optional_external_method_is_not_made_mandatory(tmp_path):
    project = _project(tmp_path)
    result = cli.prepare(cli.parser().parse_args(['prepare', '--project', str(project), '--skill',
        'creative-director', '--objective', 'synthetic optional method', '--vault', str(tmp_path/'absent')]))
    assert Path(result['path']).is_file()


def test_move_and_zip_restore_preserve_relative_refs_and_bytes(tmp_path):
    project = chain(tmp_path/'original')
    (project/'readme.md').write_text('[source](N.json)\n[director](B.json)\n')
    expected = snapshot(project)
    archive = tmp_path/'bundle.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for relative in expected:
            z.write(project/relative, relative)
    moved = tmp_path/'moved'
    shutil.move(project, moved)
    restored = tmp_path/'restored'
    # This archive is generated here, never an untrusted uploaded ZIP.
    with zipfile.ZipFile(archive) as z:
        z.extractall(restored)
    for root in (moved, restored):
        assert snapshot(root) == expected
        with pytest.raises(ValueError, match='不能复制授权运行身份'):
            mode.resolve_inputs(root)
        with pytest.raises(ValueError, match='不能复制授权运行身份'):
            mode.configure(root, 'synthetic-run', 'synthetic recovery', ['N', 'B'])
        for relative in ('N.json', 'B.json'):
            assert artifacts.project_path(root, relative).is_file()
            assert hashlib.sha256((root/relative).read_bytes()).digest() == hashlib.sha256(expected[relative]).digest()
        with pytest.raises(ValueError): artifacts.promote_candidate(root, 'narrative.package', 'N')
        assert snapshot(root) == expected


@pytest.mark.parametrize('fault', ['missing', 'traversal', 'symlink', 'revision', 'hash'])
def test_bad_candidate_recovery_blocks_without_changing_confirmation(tmp_path, fault):
    project = chain(tmp_path/'project')
    manifest = artifacts.load_manifest(project)
    source = manifest['artifacts'][0]
    if fault == 'missing': (project/'N.json').unlink()
    elif fault == 'traversal':
        outside = tmp_path/'outside.json'; outside.write_bytes((project/'N.json').read_bytes())
        source['path_or_url'] = '../outside.json'
    elif fault == 'symlink':
        outside = tmp_path/'outside.json'; outside.write_bytes((project/'N.json').read_bytes())
        (project/'link.json').symlink_to(outside)
        source['path_or_url'] = 'link.json'
    elif fault == 'revision':
        payload = json.loads((project/'B.json').read_text())
        payload['source_narrative']['revision'] = 'R00'
        write_json(project/'B.json', payload)
    else: source['checksum'] = '0'*64  # Deliberately corrupt fixture metadata; never an approval.
    artifacts.save_manifest(project, manifest)
    before = snapshot(project)
    with pytest.raises(ValueError): mode.resolve_inputs(project)
    assert snapshot(project) == before


def test_document_current_manual_preflight_gap(tmp_path):
    # The video stage deliberately has no prepare API. Product/action file
    # readiness therefore cannot be claimed from a successful stage prepare.
    project = chain(tmp_path/'project')
    for kind in ('product_photo', 'action_reference'):
        (project/f'{kind}.json').write_text(json.dumps({'path': f'missing-{kind}.png'}))
    assert mode.resolve_inputs(project)  # Unregistered manual inputs aren't checked.
    with pytest.raises(ValueError, match='准备包流程已退役'):
        cli.prepare(argparse.Namespace(skill='voiceover-to-video-script'))


def test_export_budget_can_be_measured_before_transfer(tmp_path):
    # Synthetic threshold, NOT an asserted platform upload limit.
    archive = tmp_path/'bundle.zip'
    payload = bytes(range(256))*32
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED) as z:
        z.writestr('synthetic.txt', payload)
    budget = 4096
    assert archive.stat().st_size > budget
    assert len(payload) > budget
    # No network transfer is attempted. There is no universal export-budget CLI.


@pytest.mark.parametrize('kind', ['knowledge.md', 'product.png', 'action.mp4'])
def test_explicit_readiness_detects_each_input_before_output(tmp_path, kind):
    from scripts.core.product_refs import validate_required_files
    project = chain(tmp_path/'project')
    dependency = tmp_path/kind
    dependency.write_bytes(b'synthetic input bytes, not real media')
    before = snapshot(project)
    assert validate_required_files(project, [str(dependency)], [tmp_path]) == []
    dependency.unlink()
    assert validate_required_files(project, [str(dependency)], [tmp_path])
    assert snapshot(project) == before


@pytest.mark.parametrize('raw', ['', 'empty.txt', 'directory', '../outside.txt', 'link.txt'])
def test_explicit_readiness_rejects_bad_or_escaping_inputs(tmp_path, raw):
    from scripts.core.product_refs import validate_required_files
    project = tmp_path/'project'; project.mkdir()
    (project/'empty.txt').touch(); (project/'directory').mkdir()
    outside = tmp_path/'outside.txt'; outside.write_text('synthetic')
    (project/'link.txt').symlink_to(outside)
    assert validate_required_files(project, [raw])


def test_explicit_readiness_cli_is_read_only_and_fails_closed(tmp_path):
    import subprocess, sys
    project = chain(tmp_path/'project')
    before = snapshot(project)
    command = [sys.executable, '-m', 'scripts.core.product_refs', '--project', str(project)]
    good = subprocess.run(command + ['--require-file', 'N.json'], capture_output=True)
    bad = subprocess.run(command + ['--require-file', 'missing.mp4'], capture_output=True)
    assert good.returncode == 0 and bad.returncode == 1
    assert snapshot(project) == before


def test_external_inputs_require_declared_root_and_resolved_boundary(tmp_path):
    from scripts.core.product_refs import validate_required_files
    project = tmp_path/'project'; project.mkdir()
    vault = tmp_path/'vault'; vault.mkdir()
    source = vault/'method.md'; source.write_text('synthetic method')
    assert validate_required_files(project, [str(source)])
    assert not validate_required_files(project, [str(source)], [vault])
    outside = tmp_path/'other.md'; outside.write_text('synthetic outside')
    (vault/'escape.md').symlink_to(outside)
    assert validate_required_files(project, [str(vault/'escape.md')], [vault])


def test_existing_review_links_are_absolute_and_need_reading_copy_after_move(tmp_path):
    import runpy
    builder = runpy.run_path(str(Path(__file__).parents[1]/'build-image-review-delivery.py'))
    folder = tmp_path/'original'; folder.mkdir()
    (folder/'synthetic.png').write_bytes(b'synthetic path-only fixture')
    output = builder['build_delivery'](folder, ['synthetic.png'])
    assert folder.as_posix() in output
    shutil.move(folder, tmp_path/'moved')
    assert not folder.exists()
    assert folder.as_posix() in output  # Documented limitation, not a portable-link pass.


@pytest.mark.parametrize('target', ['file', 'root', 'project'])
def test_symlink_loops_are_reported_as_readiness_errors(tmp_path, target):
    from scripts.core.product_refs import validate_required_files
    project = tmp_path/'project'; project.mkdir()
    loop = project/'loop'; loop.symlink_to(loop)
    assert validate_required_files(loop if target == 'project' else project,
        ['loop'] if target == 'file' else ['missing'], [loop] if target == 'root' else [])
