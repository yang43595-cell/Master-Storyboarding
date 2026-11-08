"""Repair assertions for the 2026-10-07 review/cache root-cause CLI cases.

The original repro and its failing-before evidence remain unchanged. These
fixtures assert positive repairs plus source, authority and archive boundaries.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import stage_context_cli as cli
from scripts.tests.test_stage_context_cli import _project, _method_vault, _synthetic_method_profile
from scripts.tests.source_fixture import copy_source_fixture


@pytest.fixture(autouse=True)
def synthetic_adopted_method(tmp_path, monkeypatch):
    vault = _method_vault(tmp_path / 'vault')
    _synthetic_method_profile(tmp_path, vault, monkeypatch)
    bundle = cli.ROOT / "knowledge"
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = "projects/Master-Storyboarding/应用知识/编导/编导方法.md"
    source = bundle / "runtime" / relative
    shutil.copyfile(vault / relative, source)
    entry = next(row for row in manifest["files"] if row["path"] == relative)
    entry.update(sha256=cli.digest(source), size_bytes=source.stat().st_size)
    write(manifest_path, manifest)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture(tmp_path):
    project = _project(tmp_path)
    vault = cli.ROOT / "knowledge" / "runtime"
    source = project / "source.md"
    source.write_text("已知原始证据。", encoding="utf-8")
    candidate = project / "candidate.md"
    candidate.write_text("## STP-001\n保持原始动作。\n", encoding="utf-8")
    request = project / "request.json"
    write(request, {"source_markdown": "source.md", "scope": "STP-001", "history": ["旧讨论"], "use_story_method": True})
    args = argparse.Namespace(project=str(project), skill="creative-director", objective="修订当前段",
                              request_json=str(request), vault=str(vault), actor_id="actor", task_id="stable-task")
    cli.prepare(args)
    reading = argparse.Namespace(project=str(project), skill=args.skill, actor_id=args.actor_id,
                                 task_id=args.task_id, method=["CREATIVE-DIRECTION-METHOD"])
    cli.record_method_reading(reading)
    return project, vault, source, candidate, request, args, reading


def basis(project):
    return json.loads((project / cli.CONTEXT_DIR / "task_basis.json").read_text(encoding="utf-8"))


def review_args(project, candidate, **extra):
    return argparse.Namespace(project=str(project), skill="creative-director", artifact=[str(candidate)], check=[], **extra)


def invoke(*arguments):
    return subprocess.run([sys.executable, str(cli.ROOT / "scripts/stage_context_cli.py"), *map(str, arguments)],
                          cwd=cli.ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("metadata", ["history", "superseded", "timestamp", "unrelated_current", "unrelated_confirmation"])
def test_irrelevant_changes_preserve_reading_and_substantive_review(tmp_path, metadata):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    first = cli.review_pack(review_args(project, candidate))
    previous = project / "previous.json"
    check = basis(project)["review_acceptance"]["production"]["required_checks"][0]["id"]
    write(previous, {"conclusion": "不通过", "issues": [{"check_id": check, "target": "STP-001",
                                                        "evidence": "candidate.md:2", "impact": "动作需核对"}]})
    before = basis(project)
    if metadata == "history":
        value = json.loads(request.read_text()); value["history"].append("仅历史变化"); write(request, value)
    if metadata in {"superseded", "timestamp", "unrelated_current"}:
        manifest = project / "00_项目管理/artifact_manifest.json"
        value = {"artifacts": [], "updated_at": "new"}
        if metadata != "timestamp":
            value["artifacts"] = [{"artifact_id": "UNRELATED", "status": "current" if metadata == "unrelated_current" else "superseded",
                                   "path_or_url": "missing-unrelated.md"}]
        write(manifest, value)
    if metadata == "unrelated_confirmation":
        path = project / "00_项目管理/project_state.json"
        value = json.loads(path.read_text()); value["user_confirmations"].append({"artifact_ids": ["OTHER"], "approval_ref": "test"}); write(path, value)
    cli.prepare(args)
    assert cli._evidence_current(project)
    assert before["reading_reuse_key"] == basis(project)["reading_reuse_key"]
    assert cli._review_identity(before) == cli._review_identity(basis(project))
    report = cli.review_pack(review_args(project, candidate, review_mode="revision", previous_review=str(previous)))
    delta = json.loads(Path(report["review_delta_path"]).read_text())
    assert delta["unresolved_findings"][0]["check_id"] == check
    assert not any(key in delta for key in ("issue_id_high_watermark", "reviewer_lineage", "wording_only_streak", "actual_diff"))
    assert not report["formal_archive"]
    assert not (project / cli.CONTEXT_DIR / "review_rounds").exists()


def test_original_history_only_formal_cli_repro_now_succeeds(tmp_path):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    first = invoke("review-pack", "--project", project, "--skill", args.skill, "--artifact", candidate)
    assert first.returncode == 0, first.stderr
    scope = json.loads(Path(json.loads(first.stdout)["review_scope_path"]).read_text())
    previous = project / "previous.json"; write(previous, {"scope": scope, "reviewer_id": "original-reviewer", "issues": []})
    baseline = project / "baseline.md"; baseline.write_bytes(candidate.read_bytes())
    value = json.loads(request.read_text()); value["history"].append("纯历史，无当前适用变化"); write(request, value)
    prepare = invoke("prepare", "--project", project, "--skill", args.skill, "--objective", args.objective,
                     "--vault", vault, "--request-json", request, "--actor-id", "actor", "--task-id", "stable-task")
    assert prepare.returncode == 0, prepare.stderr
    result = invoke("review-pack", "--project", project, "--skill", args.skill, "--artifact", candidate,
                    "--review-mode", "revision", "--previous-review", previous, "--baseline", baseline,
                    "--producer-response", '{"responses":[]}')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["formal_archive"]


@pytest.mark.parametrize("change", ["source_bytes", "source_missing", "method_bytes", "request_scope", "unknown_authority"])
def test_current_consumption_rejects_stale_required_inputs(tmp_path, change):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    if change == "source_bytes": source.write_text("另一版本", encoding="utf-8")
    if change == "source_missing": source.unlink()
    if change == "method_bytes":
        path = vault / "projects/Master-Storyboarding/应用知识/编导/编导方法.md"
        path.write_text(path.read_text() + "\n新方法。", encoding="utf-8")
    if change == "request_scope":
        value = json.loads(request.read_text()); value["scope"] = "STP-002"; write(request, value)
    if change == "unknown_authority":
        path = project / "00_项目管理/project_state.json"
        value = json.loads(path.read_text()); value["user_confirmations"].append({"approval_ref": "unknown-scope"}); write(path, value)
    for consume in (lambda: cli.ensure_prepared(reading), lambda: cli.record_method_reading(reading),
                    lambda: cli.review_pack(review_args(project, candidate))):
        with pytest.raises(cli.StageContextError): consume()
    if change == "source_missing":
        with pytest.raises(cli.StageContextError, match="显式来源文件缺失"): cli.prepare(args)


@pytest.mark.parametrize("revoke", ["confirmation", "status", "selection", "explicit_reference"])
def test_current_authority_is_revalidated_without_handoff(tmp_path, revoke):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    state_path = project / "00_项目管理/project_state.json"
    manifest_path = project / "00_项目管理/artifact_manifest.json"
    entry = {"artifact_id": "SOURCE", "logical_id": "upstream", "revision": "R1", "status": "current",
             "user_approved": True, "path_or_url": "source.md", "checksum": cli.digest(source)}
    write(manifest_path, {"artifacts": [entry]})
    state = json.loads(state_path.read_text()); state.update(current_artifacts=["SOURCE"], user_confirmations=[{"artifact_ids": ["SOURCE"]}]); write(state_path, state)
    cli.prepare(args); cli.record_method_reading(reading)
    if revoke == "confirmation": state["user_confirmations"] = []
    if revoke == "status": entry["status"] = "stale"; write(manifest_path, {"artifacts": [entry]})
    if revoke == "selection": state["current_artifacts"] = []
    if revoke == "explicit_reference": write(request, {"source_artifact_id": "REMOVED"})
    write(state_path, state)
    with pytest.raises(cli.StageContextError): cli.ensure_prepared(reading)
    if revoke != "selection":
        with pytest.raises(cli.StageContextError): cli.prepare(args)


def test_light_revision_requires_real_change_description_and_keeps_scope(tmp_path):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    cli.review_pack(review_args(project, candidate))
    previous = project / "previous.json"; write(previous, {"issues": []})
    candidate.write_text("## STP-001\n已修订动作。\n", encoding="utf-8")
    revision = review_args(project, candidate, review_mode="revision", previous_review=str(previous))
    with pytest.raises(cli.StageContextError, match="change-summary"): cli.review_pack(revision)
    revision.change_summary = ["STP-001第2行：把保持动作改为已修订动作；核对直接承接关系"]
    result = cli.review_pack(revision)
    delta = json.loads(Path(result["review_delta_path"]).read_text())
    assert delta["change_evidence"] == "author_declared_not_computed_diff"
    assert "actual_diff" not in delta
    value = json.loads(request.read_text()); value["scope"] = "STP-002"; write(request, value)
    cli.prepare(args)
    with pytest.raises(cli.StageContextError, match="任务依据"): cli.review_pack(revision)


def test_legacy_byte_hash_scope_remains_strict_and_cannot_downgrade(tmp_path):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    first = cli.review_pack(review_args(project, candidate, formal_archive=True))
    scope = json.loads(Path(first["review_scope_path"]).read_text()); scope.pop("task_basis_identity")
    previous = project / "previous.json"; write(previous, {"scope": scope, "reviewer_id": "original", "issues": []})
    revision = review_args(project, candidate, review_mode="revision", previous_review=str(previous))
    with pytest.raises(cli.StageContextError, match="正式留档"): cli.review_pack(revision)
    revision.baseline = [str(candidate)]; revision.producer_response = '{"responses":[]}'
    assert cli.review_pack(revision)["formal_archive"]
    # Preserve old evidence exactly; do not manufacture a semantic identity for it.
    assert "task_basis_identity" not in json.loads(previous.read_text())["scope"]


def test_corrupt_active_reading_is_recoverable_and_moved_project_rejected(tmp_path):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    (project / cli.CONTEXT_DIR / "method_reading_evidence.json").write_text("{broken")
    assert cli.prepare(args)["stage_reading_restored"]
    assert cli.ensure_prepared(reading)["execution_authorization"] == "not_granted"
    snapshot = basis(project); snapshot["reading_manifest"]["project_path"] = "/other-project"
    write(project / cli.CONTEXT_DIR / "task_basis.json", snapshot)
    with pytest.raises(cli.StageContextError, match="另一项目"):
        cli.review_pack(review_args(project, candidate))


def test_light_revision_does_not_forget_unresolved_findings(tmp_path):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    cli.review_pack(review_args(project, candidate))
    check = basis(project)["review_acceptance"]["production"]["required_checks"][0]["id"]
    previous = project / "previous.json"
    issue = {"check_id": check, "target": "STP-001", "evidence": "candidate.md:2", "impact": "动作需核对"}
    write(previous, {"issues": [issue]})
    revision = review_args(project, candidate, review_mode="revision", previous_review=str(previous))
    cli.review_pack(revision)
    write(previous, {"issues": []})
    with pytest.raises(cli.StageContextError, match="漏答"): cli.review_pack(revision)
    write(previous, {"issues": [{**issue, "status": "resolved"}]})
    result = cli.review_pack(revision)
    assert json.loads(Path(result["review_delta_path"]).read_text())["unresolved_findings"] == []


def test_input_metadata_url_is_not_mistaken_for_a_local_source(tmp_path):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    write(request, {"inputs": [{"path": "source.md", "metadata_url": "https://example.invalid/provenance", "description": "https://example.invalid/info"}]})
    cli.prepare(args)
    assert len(basis(project)["input_snapshot"]["sources"]) == 1
    write(request, {"source_markdown": "https://example.invalid/source.md"})
    with pytest.raises(cli.StageContextError, match="本地文件"): cli.prepare(args)


@pytest.mark.parametrize("replay", ["empty_old_scope", "resolved_old_scope", "old_candidate_scope"])
def test_light_old_embedded_report_cannot_replace_current_round(tmp_path, replay):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    initial = cli.review_pack(review_args(project, candidate))
    old_scope = json.loads(Path(initial["review_scope_path"]).read_text())
    check = basis(project)["review_acceptance"]["production"]["required_checks"][0]["id"]
    issue = {"check_id": check, "target": "STP-001", "evidence": "candidate.md:2", "impact": "仍须复核"}
    previous = project / "previous.json"; write(previous, {"issues": [issue]})
    revision = review_args(project, candidate, review_mode="revision", previous_review=str(previous))
    if replay == "old_candidate_scope":
        candidate.write_text("当前新版本", encoding="utf-8")
        revision.change_summary = ["candidate.md 全文：新版本；核对来源承接"]
    cli.review_pack(revision)
    current_path = project / cli.CONTEXT_DIR / "review_scope.json"
    current_bytes = current_path.read_bytes()
    write(previous, {"scope": old_scope, "issues": [] if replay == "empty_old_scope" else [{**issue, "status": "resolved"}]})
    with pytest.raises(cli.StageContextError, match="过期"): cli.review_pack(revision)
    assert current_path.read_bytes() == current_bytes
    # The current matching scope and an explicit closure are valid.
    write(previous, {"scope": json.loads(current_bytes), "issues": [{**issue, "status": "resolved"}]})
    report = cli.review_pack(revision)
    assert json.loads(Path(report["review_delta_path"]).read_text())["unresolved_findings"] == []


@pytest.mark.parametrize("bad_closure", ["other_check", "other_target", "duplicate", "suggestion"])
def test_light_closures_cannot_escape_current_pending_scope(tmp_path, bad_closure):
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    cli.review_pack(review_args(project, candidate))
    check = basis(project)["review_acceptance"]["production"]["required_checks"][0]["id"]
    issue = {"check_id": check, "target": "STP-001", "evidence": "candidate.md:2", "impact": "仍须复核"}
    previous = project / "previous.json"; write(previous, {"issues": [issue]})
    revision = review_args(project, candidate, review_mode="revision", previous_review=str(previous))
    cli.review_pack(revision)
    closure = {**issue, "status": "resolved"}
    if bad_closure == "other_check": closure["check_id"] = "OUTSIDE-CHECK"
    if bad_closure == "other_target": closure["target"] = "OTHER-TARGET"
    if bad_closure == "suggestion": closure["status"] = "suggestion"
    write(previous, {"issues": [closure, closure] if bad_closure == "duplicate" else [closure]})
    with pytest.raises(cli.StageContextError): cli.review_pack(revision)


@pytest.mark.parametrize("shared_name", ["draft-lifecycle.md", "knowledge-access.md", "product-usage-contract.md"])
@pytest.mark.parametrize("change", ["changed", "missing"])
def test_declared_shared_contracts_remain_live_dependencies(tmp_path, monkeypatch, shared_name, change):
    copied = tmp_path / "repo"
    copy_source_fixture(cli.ROOT, copied)
    monkeypatch.setattr(cli, "ROOT", copied)
    project, vault, source, candidate, request, args, reading = fixture(tmp_path)
    before = basis(project)
    shared = copied / "skills/_shared" / shared_name
    assert str(shared) in {record["path"] for record in before["standards"]["local_documents"]}
    if change == "missing": shared.unlink()
    else: shared.write_text(shared.read_text() + "\n实质必读规则变化。", encoding="utf-8")
    with pytest.raises(cli.StageContextError): cli.ensure_prepared(reading)
    if change == "missing":
        with pytest.raises(cli.StageContextError, match="共享规范缺失"): cli.prepare(args)
    else:
        cli.prepare(args)
        assert before["reading_reuse_key"] != basis(project)["reading_reuse_key"]
        assert cli._review_identity(before) != cli._review_identity(basis(project))
        assert cli._evidence_current(project) is None
