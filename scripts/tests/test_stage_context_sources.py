"""Synthetic local files using the real request.sources shape; no live QA claim."""
import argparse
import json

import pytest

from scripts import stage_context_cli as cli
from test_stage_context_cli import _method_vault, _project


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
    vault = _method_vault(tmp_path / "vault")
    args = argparse.Namespace(project=str(project), skill="creative-director",
        objective="synthetic source snapshot", request_json=str(request), vault=str(vault),
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


def test_old_persisted_preparation_missing_sources_snapshot_requires_reprepare(tmp_path):
    project, paths, basis = prepare_sources(tmp_path)
    basis["input_versions"] = {}
    basis["input_snapshot"]["sources"] = []
    with pytest.raises(cli.StageContextError, match="当前来源"):
        cli._validate_current_inputs(project, basis)
