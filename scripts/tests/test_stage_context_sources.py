"""Synthetic local files using the real request.sources shape; no live QA claim."""
import argparse
import json

import pytest

from scripts import stage_context_cli as cli
from scripts.core.artifacts import load_manifest, save_manifest
from scripts.core.project_state import load_project_state, write_project_state
from test_stage_context_cli import _project


def prepare_sources(tmp_path, alias="sources"):
    project = _project(tmp_path)
    raw = project / "01_原始资料"
    raw.mkdir()
    names = ["source.mp4", "user-requested-changes.json", "brief.json",
             "source-provenance.json", "user-face-reference.png"]
    paths = []
    for name in names:
        path = raw / name
        path.write_bytes(b"synthetic source bytes: " + name.encode())
        paths.append(str(path))
    request = project / "request.json"
    request.write_text(json.dumps({"task": "synthetic source snapshot", alias: paths}))
    args = argparse.Namespace(project=str(project), skill="creative-director",
        objective="synthetic source snapshot", request_json=str(request), vault=None,
        actor_id="test-agent", task_id="test-sources")
    cli.prepare(args)
    basis = json.loads((project / cli.CONTEXT_DIR / "task_basis.json").read_text())
    return project, paths, basis


@pytest.mark.parametrize("alias", ["sources", "source_files"])
def test_prepare_persists_exact_declared_files_and_rejects_drift(tmp_path, alias):
    project, paths, basis = prepare_sources(tmp_path, alias)
    assert basis["input_versions"] == {"source_files": paths}
    assert basis["task_request"][alias] == paths  # Raw request identity is not rewritten.
    assert basis["input_snapshot"]["sources"] == [cli.fingerprint(cli.Path(p)) for p in sorted(paths)]
    cli._validate_current_inputs(project, basis)
    cli.Path(paths[0]).write_bytes(b"changed source bytes")
    with pytest.raises(cli.StageContextError, match="当前来源"):
        cli.ensure_prepared(argparse.Namespace(project=str(project), skill="creative-director"))


@pytest.mark.parametrize("value", [None, [], "file.png", {}, {"path": "file.png"},
                                    [None], [7], [""], [" "], [["file.png"]]])
def test_invalid_sources_is_not_silently_ignored(tmp_path, value):
    with pytest.raises(cli.StageContextError, match="sources"):
        cli._input_snapshot(tmp_path, {"sources": value}, {})


@pytest.mark.parametrize("source", ["missing.png", "https://example.invalid/image.png"])
def test_declared_source_must_be_local_existing_file(tmp_path, source):
    with pytest.raises(cli.StageContextError, match="来源"):
        cli._input_snapshot(tmp_path, {"sources": [source]}, {})


def test_alias_equivalence_conflict_and_no_unrelated_scan(tmp_path):
    source = tmp_path / "declared.txt"
    source.write_text("synthetic declared bytes")
    other = tmp_path / "unrelated.txt"
    other.write_text("not declared")
    declared = [str(source)]
    expected = cli._input_snapshot(tmp_path, {"source_files": declared}, {})
    for inputs in ({"sources": declared}, {"sources": declared, "source_files": declared}):
        assert cli._input_snapshot(tmp_path, inputs, {}) == expected
    other.write_text("unrelated drift is not a task dependency")
    assert cli._input_snapshot(tmp_path, {"sources": declared}, {}) == expected
    with pytest.raises(cli.StageContextError, match="不一致"):
        cli._input_snapshot(tmp_path, {"sources": declared, "source_files": [str(other)]}, {})
    assert cli._explicit_source_paths(tmp_path, {"metadata": {"sources": ["narrative claim"]}}) == []


def _prepare_local_sources(tmp_path, *, by_path=False):
    project = _project(tmp_path)
    state = load_project_state(project)
    state.update(next_stage='creative-director', current_artifacts=['OTHER', 'LEAF', 'ROOT', 'PARENT'])
    artifacts = []
    for ident, upstream in (('OTHER', []), ('LEAF', []), ('ROOT', ['reference.parent']), ('PARENT', ['reference.leaf'])):
        path = project / f'{ident}.txt'
        path.write_text(f'synthetic bytes for {ident}', encoding='utf-8')
        artifacts.append(dict(artifact_id=ident, logical_id=f'reference.{ident.lower()}', type='reference',
                              path_or_url=path.name, revision='R01', status='current', user_approved=True,
                              checksum=cli.digest(path), depends_on=upstream))
    state['user_confirmations'] = [dict(scope='artifact', artifact_ids=[row['artifact_id']], approval_ref='synthetic approval')
                                   for row in artifacts]
    save_manifest(project, {'artifacts': artifacts})
    write_project_state(project, state)
    identity = ['--project', str(project), '--actor-id', 'worker', '--task-id', 'local-source-test']
    stage = [*identity, '--skill', 'creative-director']
    handoff = ['handoff', *identity, '--to-skill', 'creative-director', '--objective', 'Local scope', '--stop-at', 'Stop A']
    card = cli.handoff(cli.parser().parse_args(handoff))
    request = project / 'request.json'
    selection = {'source_paths': ['ROOT.txt'], 'source_shot_ids': ['S01-001']} if by_path else {'source_artifact_ids': ['ROOT']}
    request.write_text(json.dumps(selection), encoding='utf-8')
    prepared = cli.prepare(cli.parser().parse_args(['prepare', *stage, '--objective', 'Local scope', '--request-json', str(request)]))
    startup = cli.Path(prepared['path'])
    basis = json.loads((startup.parent / 'task_basis.json').read_text(encoding='utf-8'))
    ensure = cli.parser().parse_args(['ensure-prepared', *stage])
    return project, startup, basis, ensure, cli.Path(card['path']), handoff


def _replace_local_test_artifact(project, ident):
    """Change one current identity while keeping its logical role and file bytes."""
    manifest = load_manifest(project)
    old = next(row for row in manifest['artifacts'] if row['artifact_id'] == ident)
    new_id = ident + '-R02'
    new_path = project / f'{new_id}.txt'
    new_path.write_bytes((project / old['path_or_url']).read_bytes())
    manifest['artifacts'].append({**old, 'artifact_id': new_id, 'revision': 'R02', 'path_or_url': new_path.name})
    old.update(status='stale')
    save_manifest(project, manifest)
    state = load_project_state(project)
    state['current_artifacts'] = [value if value != ident else new_id for value in state['current_artifacts']]
    state['user_confirmations'] = [{**receipt, 'artifact_ids': [new_id]}
                                   if receipt['artifact_ids'] == [ident] else receipt
                                   for receipt in state['user_confirmations']]
    write_project_state(project, state)


@pytest.mark.parametrize('by_path', [False, True])
def test_new_local_preparation_ignores_unrelated_revision_in_input_and_handoff(tmp_path, monkeypatch, by_path):
    project, startup, basis, ensure, card, handoff = _prepare_local_sources(tmp_path, by_path=by_path)
    snapshot = basis['input_snapshot']
    assert snapshot['artifact_scope'] == 'declared'
    assert set(snapshot['selected_artifact_ids']) == {'ROOT', 'PARENT', 'LEAF'}
    assert {fact['record']['artifact_id'] for fact in snapshot['artifacts']} == {'ROOT', 'PARENT', 'LEAF'}
    assert '- artifact_id：`OTHER`' not in startup.read_text(encoding='utf-8')
    assert '- artifact_id：`OTHER`' in card.read_text(encoding='utf-8')
    assert cli.ensure_prepared(ensure)['ok']
    original_basis = (startup.parent / 'task_basis.json').read_bytes()
    original_digest = cli.digest

    _replace_local_test_artifact(project, 'OTHER')
    assert cli.handoff(cli.parser().parse_args(handoff))['valid']  # The new card lists OTHER-R02.

    def only_related(path):
        assert not (path.parent == project and path.name.startswith('OTHER'))
        return original_digest(path)

    monkeypatch.setattr(cli, 'digest', only_related)
    assert cli.ensure_prepared(ensure)['ok']
    assert (startup.parent / 'task_basis.json').read_bytes() == original_basis


@pytest.mark.parametrize('by_path', [False, True])
def test_declared_handoff_reuses_task_scope_and_leaves_unrelated_files_unread(tmp_path, monkeypatch, by_path):
    project, startup, basis, ensure, card, handoff = _prepare_local_sources(tmp_path, by_path=by_path)
    (project / 'OTHER.txt').write_text('unrelated drift', encoding='utf-8')
    original_digest = cli.digest

    def only_related(path):
        assert path != project / 'OTHER.txt'
        return original_digest(path)

    monkeypatch.setattr(cli, 'digest', only_related)
    args = cli.parser().parse_args([*handoff, '--request-json', str(project / 'request.json')])
    report = cli.handoff(args)
    assert set(report['artifact_ids']) == {'ROOT', 'PARENT', 'LEAF'}
    assert '- artifact_id：`OTHER`' not in card.read_text(encoding='utf-8')
    assert cli.ensure_prepared(ensure)['ok']
    assert json.loads((startup.parent / 'task_basis.json').read_text(encoding='utf-8')) == basis

    (project / 'LEAF.txt').write_text('changed transitive dependency', encoding='utf-8')
    with pytest.raises(cli.StageContextError, match='上游文件缺失或已偏离'):
        cli.handoff(args)


@pytest.mark.parametrize(('change', 'error'), [
    ('source_bytes', '内容与确认版本'),
    ('dependency_bytes', '上游文件缺失或已偏离'),
    ('dependency_revision', '当前来源、选定产物或确认依据已改变'),
    ('source_approval', '缺少用户确认'),
    ('dependency_approval', '上游 current 缺少有效确认'),
    ('handoff_stop', '交接目标、停止点.*已改变'),
])
def test_local_preparation_still_blocks_changes_to_its_authority(tmp_path, change, error):
    project, _, _, ensure, card, _ = _prepare_local_sources(tmp_path)
    assert cli.ensure_prepared(ensure)['ok']
    if change == 'source_bytes':
        (project / 'ROOT.txt').write_bytes(b'changed selected artifact')
    elif change == 'dependency_bytes':
        (project / 'LEAF.txt').write_bytes(b'changed transitive dependency')
    elif change == 'dependency_revision':
        _replace_local_test_artifact(project, 'LEAF')
    elif change in {'source_approval', 'dependency_approval'}:
        revoked = 'ROOT' if change == 'source_approval' else 'LEAF'
        state = load_project_state(project)
        state['user_confirmations'] = [receipt for receipt in state['user_confirmations']
                                       if receipt['artifact_ids'] != [revoked]]
        write_project_state(project, state)
    else:
        card.write_text(card.read_text(encoding='utf-8').replace('Stop A', 'Stop B'), encoding='utf-8')
    with pytest.raises(cli.StageContextError, match=error):
        cli.ensure_prepared(ensure)


def test_old_persisted_preparation_missing_sources_snapshot_requires_reprepare(tmp_path):
    project, paths, basis = prepare_sources(tmp_path)
    basis["input_versions"] = {}
    basis["input_snapshot"]["sources"] = []
    with pytest.raises(cli.StageContextError, match="当前来源"):
        cli._validate_current_inputs(project, basis)


def test_old_external_knowledge_basis_requires_reprepare_before_reading_methods(tmp_path, monkeypatch):
    project, _, basis = prepare_sources(tmp_path)
    external = tmp_path / "old-vault"
    external.mkdir()
    basis["reading_manifest"]["vault_root"] = str(external)
    (project / cli.CONTEXT_DIR / "task_basis.json").write_text(json.dumps(basis), encoding="utf-8")

    def forbidden(*args):
        raise AssertionError("knowledge bytes must not be read from an old external root")

    monkeypatch.setattr(cli, "verify_selected_files", forbidden)
    args = argparse.Namespace(project=str(project), skill="creative-director")
    with pytest.raises(cli.StageContextError, match="仓库内置知识重新 prepare"):
        cli.ensure_prepared(args)
    with pytest.raises(cli.StageContextError, match="仓库内置知识重新 prepare"):
        cli.record_method_reading(args)


def test_explicit_external_knowledge_cannot_override_method_receipt(tmp_path):
    project, _, _ = prepare_sources(tmp_path)
    external = tmp_path / "external-vault"
    external.mkdir()
    args = argparse.Namespace(project=str(project), skill="creative-director",
        actor_id="test-agent", task_id="test-sources", vault=str(external), method=[])
    with pytest.raises(cli.StageContextError, match="knowledge/runtime"):
        cli.record_method_reading(args)
    assert not (project / cli.CONTEXT_DIR / "method_reading_evidence.json").exists()


@pytest.mark.parametrize("field", ["knowledge_profile", "local_documents"])
def test_saved_local_knowledge_paths_cannot_point_outside_repository(tmp_path, monkeypatch, field):
    project, _, basis = prepare_sources(tmp_path)
    external = tmp_path / "external-knowledge.json"
    external.write_text("{}", encoding="utf-8")
    record = basis[field] if field == "knowledge_profile" else basis["standards"][field][0]
    record.update(path=str(external), sha256=cli.digest(external))
    read_json = cli._read_json
    digest = cli.digest

    def guarded_read(path, *args, **kwargs):
        assert path != external, "external knowledge file must not be opened"
        return read_json(path, *args, **kwargs)

    def guarded_digest(path):
        assert path != external, "external knowledge file must not be hashed"
        return digest(path)

    monkeypatch.setattr(cli, "_read_json", guarded_read)
    monkeypatch.setattr(cli, "digest", guarded_digest)
    with pytest.raises(cli.StageContextError, match="规范路径越出仓库"):
        cli._validate_current_inputs(project, basis)
