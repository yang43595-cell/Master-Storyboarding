from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core import revision_control
from scripts.core.io import write_json
from scripts.core.project_state import load_project_state, new_project_state, normalize_project_state, write_project_state


def _current_narrative_package(root: Path, revision: str = "R01") -> dict:
    from test_story_paragraph_sources import narrative
    package = narrative(root)
    package["revision"] = revision
    package["status"] = "confirmed"
    return package


def _current_storyboard_package(root: Path, narrative_entry: dict, revision: str = "R01") -> dict:
    from test_director_compact import compact_package
    package = compact_package()
    package["revision"] = revision
    package["source_narrative"] = {"artifact_id": narrative_entry["artifact_id"],
        "logical_id": "narrative.package", "revision": narrative_entry["revision"],
        "path": narrative_entry["path_or_url"]}
    package["human_readable_path"] = "director.md"
    (root / "director.md").write_text("synthetic reviewed director rendering", encoding="utf-8")
    return package


@pytest.mark.parametrize('command', ['video-reading-index', 'video-reading-scene',
                                   'record-video-scene-output', 'export-video-delivery'])
def test_retired_video_commands_are_not_executable(command):
    with pytest.raises(SystemExit) as error:
        cli.parser().parse_args([command])
    assert error.value.code == 2


@pytest.mark.parametrize("payload", [None, {}, {"workflow": "video-task"}, {"delivery_object": "prompt"}])
def test_retired_video_request_cannot_create_preparation_files(tmp_path, payload):
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(payload), encoding='utf-8')
    project = tmp_path / 'direct-video'
    project.mkdir()
    command = ['prepare', '--project', str(project),
        '--skill', 'voiceover-to-video-script', '--objective', 'retired workflow']
    if payload is not None:
        command += ['--request-json', str(request)]
    with pytest.raises(cli.StageContextError, match='准备包流程已退役'):
        cli.prepare(cli.parser().parse_args(command))
    assert not (project / cli.CONTEXT_DIR).exists()


def test_retired_video_saved_package_cannot_be_resumed(tmp_path):
    folder = tmp_path / cli.CONTEXT_DIR
    folder.mkdir(parents=True)
    startup = folder / 'stage_startup.md'
    startup.write_text('# 阶段启动包\n- Skill：`voiceover-to-video-script`\n', encoding='utf-8')
    before = startup.read_bytes()
    for skill in ('voiceover-to-video-script', None):
        with pytest.raises(cli.StageContextError, match='准备包流程已退役'):
            cli.ensure_prepared(argparse.Namespace(project=str(tmp_path), skill=skill))
    for operation in (cli.record_method_reading, cli.review_pack):
        with pytest.raises(cli.StageContextError, match='准备包流程已退役'):
            operation(argparse.Namespace(project=str(tmp_path), skill='voiceover-to-video-script'))
    assert startup.read_bytes() == before


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    manage = project / "00_项目管理"
    manage.mkdir(parents=True)
    (manage / "project_state.json").write_text(
        json.dumps({
            "current_stage": "creative-director",
            "current_artifacts": [],
            "user_confirmations": [],
            "review_report": None,
            "accepted_issues": [],
            "next_stage": "",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return project


def _method_vault(root: Path) -> Path:
    for skill, relative in (
        ("creative-director", "projects/Master-Storyboarding/应用知识/编导/编导方法.md"),
        ("storyboard-planner", "projects/Master-Storyboarding/应用知识/导演规划/导演规划方法.md"),
        ("图片连续性", "projects/Master-Storyboarding/应用知识/图片资产/图片连续性方法.md"),
        ("视觉补充", "projects/Master-Storyboarding/应用知识/图片资产/视觉补充方法.md"),
        ("人物资产", "projects/Master-Storyboarding/应用知识/图片资产/人物资产方法.md"),
        ("场景资产", "projects/Master-Storyboarding/应用知识/图片资产/场景资产方法.md"),
        ("风格资产", "projects/Master-Storyboarding/应用知识/图片资产/风格资产方法.md"),
        ("道具与产品资产", "projects/Master-Storyboarding/应用知识/图片资产/道具与产品资产方法.md"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"""---
title: {skill} 测试方法
version: 2026-09-11
status: enabled
lifecycle:
  state: current
---

# {skill} 测试方法

用于 stage_context_cli 的隔离方法绑定测试。
""", encoding="utf-8")
    return root



def _synthetic_method_profile(tmp_path, vault, monkeypatch):
    """Bind test-only method bytes to a test-only explicit promotion record."""
    import shutil
    repo = tmp_path / "synthetic-repo"
    shutil.copytree(cli.ROOT, repo, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '.git', 'regression_evidence'))
    monkeypatch.setattr(cli, 'ROOT', repo)
    _bind_synthetic_method_revision(vault)


def _bind_synthetic_method_revision(vault):
    profile_path = cli.ROOT / 'skills/creative-director/knowledge-profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    method = profile['professional_methods'][0]
    method['promotion_record'] = {
        'basis': 'user_directed', 'method_version': '2026-09-11',
        'method_sha256': cli.digest(vault / method['path']),
        'scope': 'synthetic isolated cache and reading test only',
        'source_ref': 'fixture:synthetic method document',
        'user_instruction_ref': 'fixture:explicit adoption of this synthetic revision',
        'conflict_resolution': 'fixture:no competing method',
        'rollback': 'fixture:discard temporary repository', 'supersedes': [],
    }
    write_json(profile_path, profile)


def test_state_summary_preserves_finalization_and_old_projects_remain_compatible() -> None:
    old = cli._state_summary({"current_stage": "creative-director"})
    assert old["project_status"] == "active"
    assert old["finalization"] is None

    finalization = {
        "record_path": "00_项目管理/项目定稿记录.md",
        "included_segments": ["S01", "S02", "S03"],
        "cancelled_generation_segments": ["S04"],
        "assembly_status": "not_performed",
        "replacement_segments": [{"replacement": "R02", "replaces_part_of": "07"}],
    }
    current = cli._state_summary({
        "current_stage": "voiceover-to-video-script",
        "project_status": "finalized",
        "finalization": finalization,
    })
    assert current["project_status"] == "finalized"
    assert current["finalization"] == finalization


def test_legacy_current_artifact_objects_read_as_ids_and_writes_are_canonical(tmp_path):
    project = _project(tmp_path)
    path = project / "00_项目管理/project_state.json"
    legacy = new_project_state(current_stage="creative-director")
    legacy["current_artifacts"] = [{"artifact_id": "ART-1", "path": "old-copy.md"}]
    write_json(path, legacy)
    before = path.read_bytes()
    state = load_project_state(project)
    assert state["current_artifacts"] == ["ART-1"]
    assert cli._state(project)["current_artifacts"] == ["ART-1"]
    assert path.read_bytes() == before
    write_project_state(project, state)
    assert json.loads(path.read_text(encoding="utf-8"))["current_artifacts"] == ["ART-1"]
    with pytest.raises(ValueError, match="current_artifacts"):
        write_project_state(project, legacy)
    # An explicitly empty current list must not revive old active IDs.
    assert normalize_project_state({"current_artifacts": [], "active_artifact_ids": ["OLD"]})["current_artifacts"] == []


@pytest.mark.parametrize("items", [None, "ART-1", [{}], [{"artifact_id": ""}], [""], [" ART-1"], ["ART-1", "ART-1"], [42]])
def test_invalid_current_artifact_ids_are_not_silently_dropped(items):
    with pytest.raises(ValueError, match="current_artifacts"):
        normalize_project_state({"current_artifacts": items})


def test_legacy_markdown_revision_cannot_be_confirmed_or_handed_off(tmp_path):
    project = _project(tmp_path)
    for name in ("old.md", "new.md"):
        (project / name).write_text(name, encoding="utf-8")
    write_json(project / "00_项目管理/artifact_manifest.json", {"artifacts": [{
        "artifact_id": "ART-OLD", "logical_id": "narrative.package", "type": "narrative-package",
        "revision": "R01", "status": "current", "user_approved": True,
        "path_or_url": "old.md", "depends_on": [],
        "checksum": revision_control.sha256_file(project / "old.md"),
    }]})
    state = load_project_state(project)
    state.update(current_artifacts=["ART-OLD"], next_stage="storyboard-planner",
                 user_confirmations=[{"artifact_ids": ["ART-OLD"]}])
    write_project_state(project, state)
    revision_control.open_change(project, change_id="REV-TEST", tier="T1",
                                 logical_ids=["narrative.package"], reason="test")
    revision_control.confirm_impact(project, change_id="REV-TEST", approval_ref="test:user-impact")
    revision_control.record_candidate(project, change_id="REV-TEST", artifact_id="ART-NEW",
        logical_id="narrative.package", artifact_type="narrative-package", candidate_path="new.md", revision="R02")
    revision_control.record_review(project, change_id="REV-TEST", verdict="approved", report_ref="test:review")
    with pytest.raises(revision_control.RevisionControlError, match="仅保留历史读取"):
        revision_control.confirm_candidate(project, change_id="REV-TEST", approval_ref="test:user-adopt")
    manifest = revision_control.load_manifest(project)
    assert next(a for a in manifest["artifacts"] if a["artifact_id"] == "ART-NEW")["status"] == "candidate"
    assert load_project_state(project)["current_artifacts"] == ["ART-OLD"]
    with pytest.raises(cli.StageContextError):
        cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))


def test_handoff_blocks_unknown_or_conflicting_next_stage(tmp_path):
    project = _project(tmp_path)
    state = load_project_state(project)
    state.update(current_stage="creative-director", next_stage=None, current_artifacts=["ART"])
    write_project_state(project, state)
    with pytest.raises(cli.StageContextError, match="next_stage 为空"):
        cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))
    state["next_stage"] = "creative-director"
    write_project_state(project, state)
    with pytest.raises(cli.StageContextError, match="不一致"):
        cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))


@pytest.mark.parametrize("checksum", [None, "0" * 64])
def test_handoff_requires_exact_current_artifact_hash(tmp_path, checksum):
    project = _project(tmp_path)
    artifact = project / "current.json"
    artifact.write_text('{"revision":"R01"}', encoding="utf-8")
    write_json(project / "00_项目管理/artifact_manifest.json", {"artifacts": [{
        "artifact_id": "ART", "logical_id": "story.package", "type": "json", "revision": "R01",
        "status": "current", "user_approved": True, "path_or_url": artifact.name,
        "checksum": checksum, "depends_on": []}]})
    state = load_project_state(project)
    state.update(current_artifacts=["ART"], next_stage="storyboard-planner",
                 user_confirmations=[{"artifact_ids": ["ART"]}])
    write_project_state(project, state)
    with pytest.raises(cli.StageContextError, match="缺少有效确认 hash|与确认版本不一致"):
        cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))


def test_read_only_status_reports_unknown_and_does_not_recover_transactions(tmp_path):
    project = _project(tmp_path)
    tx = project / "00_项目管理/revision_transactions/REV-TX-fixture"
    tx.mkdir(parents=True)
    write_json(tx / "transaction.json", {"transaction_id": "REV-TX-fixture", "files": []})
    write_json(tx / "commit_intent.json", {"transaction_id": "REV-TX-fixture", "files": []})
    before = {p: p.read_bytes() for p in [*tx.iterdir(), *(project / "00_项目管理").glob("*.json")]}
    report = cli.status(argparse.Namespace(project=str(project)))
    assert report["read_only"] is True and report["ok"] is False
    assert report["next_allowed_stage"] is None
    assert report["transactions"][0]["state"] == "anomalous"
    assert any("需显式恢复" in message for message in report["blockers"])
    assert {p: p.read_bytes() for p in before} == before


def test_status_distinguishes_valid_candidate_test_from_production_blockers(tmp_path, monkeypatch):
    from scripts.core import candidate_test
    import subprocess
    def git_result(command, **kwargs):
        assert command[:3] == ['git', '-C', str(cli.ROOT)]
        value = {'branch': 'fixture-branch', 'rev-parse': 'a' * 40,
                 'merge-base': 'a' * 40, 'status': ''}[command[3]]
        return subprocess.CompletedProcess(command, 0, value, '')
    monkeypatch.setattr(cli.subprocess, 'run', git_result)
    project = tmp_path / "isolated-candidate"
    project.mkdir()
    write_project_state(project, new_project_state(current_stage="creative-director"))
    write_json(project / "00_项目管理/artifact_manifest.json", {"artifacts": []})
    candidate_test.configure(project, "RUN-SYNTHETIC", "fixture:authorized candidate test", [])
    before = {p: p.read_bytes() for p in (project / "00_项目管理").rglob("*") if p.is_file()}
    report = cli.status(argparse.Namespace(project=str(project)))
    assert report["mode"] == "candidate_test"
    assert report["production_allowed"] is False and report["production_ok"] is False
    assert report["candidate_test_allowed"] is True and report["ok"] is True
    assert report["next_allowed_stage"] is None
    assert "显式选择 --to-skill" in report["next_stage_note"]
    assert {p: p.read_bytes() for p in before} == before
    handoff = cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner",
        candidate_test=True))
    assert handoff["execution_mode"] == "candidate_test"
    assert handoff["production_blocked"] is True


def test_missing_project_state_is_unknown_but_does_not_lock_direct_initial_prepare(tmp_path):
    project = tmp_path / "new-project"
    (project / "00_项目管理").mkdir(parents=True)
    before = set((project / "00_项目管理").iterdir())
    report = cli.status(argparse.Namespace(project=str(project)))
    assert report["next_allowed_stage"] is None
    assert any("project_state.json 缺失" in message for message in report["blockers"])
    assert set((project / "00_项目管理").iterdir()) == before
    vault = _method_vault(tmp_path / "first-stage-vault")
    prepared = cli.prepare(argparse.Namespace(project=str(project), skill="creative-director",
        objective="first-stage fixture", request_json=None, vault=str(vault)))
    assert prepared["valid"] is True
    assert (project / cli.CONTEXT_DIR / "stage_startup.md").is_file()


def _record_methods(project: Path, skill: str, vault: Path) -> None:
    basis = json.loads((project / cli.CONTEXT_DIR / "task_basis.json").read_text(encoding="utf-8"))
    manifest = basis["reading_manifest"]
    methods = [item["id"] for item in manifest["methods"]]
    if methods:
        cli.record_method_reading(argparse.Namespace(
            project=str(project), skill=skill, vault=str(vault), actor_id="test-agent",
            task_id="test-method-binding", method=methods, candidate_rule=[],
        ))


def _prepared(project: Path, skill: str = "creative-director") -> None:
    vault = _method_vault(project / "method-vault")
    cli.prepare(argparse.Namespace(
        project=str(project), skill=skill, objective="审核测试任务",
        request_json=None, vault=str(vault), actor_id="test-agent", task_id="test-method-binding",
    ))
    _record_methods(project, skill, vault)


def test_identical_preparation_reuses_files_but_changed_objective_is_saved(tmp_path):
    import os
    project = _project(tmp_path)
    vault = _method_vault(tmp_path / 'vault')
    args = argparse.Namespace(project=str(project), skill='creative-director', objective='原目标',
                              request_json=None, vault=str(vault), actor_id='test-agent', task_id='test-method-binding')
    cli.prepare(args)
    _record_methods(project, args.skill, vault)
    paths = [p for p in (project / cli.CONTEXT_DIR).iterdir() if p.is_file()]
    for p in paths:
        os.utime(p, ns=(1_000_000_000, 1_000_000_000))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    cli.prepare(args)
    _record_methods(project, args.skill, vault)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths} == before
    args.objective = '新目标'
    cli.prepare(args)
    assert '新目标' in (project / cli.CONTEXT_DIR / 'stage_startup.md').read_text(encoding='utf-8')
    assert json.loads((project / cli.CONTEXT_DIR / 'task_basis.json').read_text(encoding='utf-8'))['objective'] == '新目标'


def test_opt_in_write_keeps_normal_writes_and_metadata_changes(tmp_path, monkeypatch):
    from scripts.core import io
    p = tmp_path / 'state.json'
    calls = []
    original = io._replace_with_windows_retry
    def track(source, dest):
        calls.append(dest)
        original(source, dest)
    monkeypatch.setattr(io, '_replace_with_windows_retry', track)
    assert io.write_text_if_changed(p, '{"at":1}\n')
    assert not io.write_text_if_changed(p, '{"at":1}\n')
    assert len(calls) == 1
    assert io.write_text_if_changed(p, '{"at":2}\n')
    io.write_text(p, '{"at":2}\n')
    assert len(calls) == 3
    assert not list(tmp_path.glob('.tmp-*'))


def test_state_reading_filter_keeps_current_unknown_and_accepted_issues():
    import copy
    receipts = [{'artifact_ids':['OLD'], 'scope':'artifact'},
                {'artifact_ids':['OLD','CURRENT'], 'scope':'dialogue', 'dialogue_ids':['DLG-01']},
                {'artifact_id':'CURRENT', 'approval_ref':'user:turn'},
                {'kind':'revision-impact', 'approval_ref':'user:other'},
                {'artifact_ids':[], 'note':'unresolved scope'}]
    summary = {'current_artifacts':['CURRENT'], 'user_confirmations':receipts,
               'accepted_issues':[{'id':'GAP-1','reason':'explicitly accepted'}]}
    before = copy.deepcopy(summary)
    result = cli._state_reading_view(summary)
    assert result['user_confirmations'] == receipts[1:]
    assert result['accepted_issues'] == summary['accepted_issues']
    assert summary == before
    assert cli._state_reading_view({**summary,'current_artifacts':[]})['user_confirmations'] == receipts


def test_task_view_preserves_frozen_checks_without_rewriting_basis(tmp_path, capsys):
    _prepared(project := _project(tmp_path))
    p = project / cli.CONTEXT_DIR / 'task_basis.json'
    before = p.read_bytes()
    basis = json.loads(before)
    assert cli.main(['ensure-prepared','--project',str(project),'--skill','creative-director',
                     '--actor-id','test-agent','--task-id','test-method-binding','--show-task-basis']) == 0
    result = json.loads(capsys.readouterr().out)['task_basis']
    assert result['review_acceptance'] == basis['review_acceptance']
    assert result['input_versions'] == basis['input_versions']
    assert result['revision'] == basis['revision']
    assert 'reading_manifest' not in result
    assert 'local_documents' not in result['standards']
    assert p.read_bytes() == before


@pytest.mark.parametrize('change', ['none', 'source', 'method', 'scope', 'actor', 'confirmation', 'corrupt'])
def test_stage_switch_restores_only_matching_reading_state(tmp_path, change, monkeypatch):
    project = _project(tmp_path)
    vault = _method_vault(tmp_path / 'vault')
    _synthetic_method_profile(tmp_path, vault, monkeypatch)
    source = project / 'source.md'; source.write_text('原始内容', encoding='utf-8')
    request = project / 'request.json'
    request.write_text(json.dumps({'inputs':[str(source)], 'use_story_method': True}), encoding='utf-8')
    def args(skill):
        return argparse.Namespace(project=str(project),skill=skill,objective='固定目标',
            request_json=str(request),vault=str(vault),actor_id='test-agent',task_id='test-method-binding')
    a = args('creative-director')
    cli.prepare(a); _record_methods(project, a.skill, vault)
    saved = (project / cli.CONTEXT_DIR / 'method_reading_packet.md').read_bytes()
    b = args('storyboard-planner')
    cli.prepare(b); _record_methods(project, b.skill, vault)
    if change == 'source': source.write_text('新的内容',encoding='utf-8')
    if change == 'method':
        p=vault/'projects/Master-Storyboarding/应用知识/编导/编导方法.md'
        p.write_text(p.read_text(encoding='utf-8')+'\n新规则。',encoding='utf-8')
        # A separately adopted synthetic revision, not unapproved byte drift.
        _bind_synthetic_method_revision(vault)
    if change == 'scope': a.objective = '改变范围'
    if change == 'actor': a.actor_id = 'another-agent'
    if change == 'confirmation':
        p=project/'00_项目管理/project_state.json';state=json.loads(p.read_text(encoding='utf-8'))
        state['user_confirmations'].append({'approval_ref':'new'});p.write_text(json.dumps(state),encoding='utf-8')
    if change == 'corrupt':
        cli._reading_cache_path(project,a.skill).write_text('{broken',encoding='utf-8')
    result = cli.prepare(a)
    assert result['stage_reading_restored'] == (change == 'none')
    assert bool(cli._evidence_current(project,a.skill)) == (change == 'none')
    if change == 'none':
        assert (project / cli.CONTEXT_DIR / 'method_reading_packet.md').read_bytes() == saved
        assert len(list((project / cli.CONTEXT_DIR / 'stage_reading_cache').glob('*.json'))) == 2


def test_prepare_is_bounded_and_fast_without_vault(tmp_path: Path) -> None:
    project = _project(tmp_path)
    started = time.perf_counter()
    report = cli.prepare(argparse.Namespace(
        project=str(project), skill="creative-director", objective="形成编导候选",
        request_json=None, vault=str(_method_vault(tmp_path / "method-vault")),
    ))
    assert report["bytes"] <= cli.LIMITS["startup"]
    assert time.perf_counter() - started < 2
    text = Path(report["path"]).read_text(encoding="utf-8")
    assert "形成编导候选" in text
    assert "完整对话" not in text


def test_preparation_contract_rejects_unknown_delivery_object() -> None:
    with pytest.raises(cli.StageContextError, match="只能是 prompt、plan 或 submission"):
        cli._delivery_contract({"delivery_object": "typo"})



def test_review_pack_accepts_only_real_project_artifacts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _prepared(project)
    manifest = project / "00_项目管理" / "artifact_manifest.json"
    manifest.write_text('{"schema_version":"1.0","artifacts":[]}', encoding="utf-8")
    artifact = project / "candidate.md"
    artifact.write_text("candidate", encoding="utf-8")
    report = cli.review_pack(argparse.Namespace(
        project=str(project), skill="creative-director",
        artifact=[str(artifact)], check=[],
    ))
    assert report["bytes"] <= cli.LIMITS["review"]
    assert report["required_check_count"] > 0
    assert report["manual_check_count"] == 0
    text = Path(report["path"]).read_text(encoding="utf-8")
    assert "Profile 版本：`1.0`" in text
    assert "## 产物登记证据" in text
    assert f"`{manifest}`" in text
    assert report["manifest_evidence_exists"] is True
    assert "## 项目状态与用户确认事实" in text
    assert f"`{project / '00_项目管理' / 'project_state.json'}`" in text
    assert report["project_state_evidence_exists"] is True
    assert report["review_mode"] == "initial"
    with pytest.raises(cli.StageContextError, match="项目内"):
        cli.review_pack(argparse.Namespace(
            project=str(project), skill="creative-director",
            artifact=[str(tmp_path / "outside.md")], check=["检查"],
        ))


def test_review_pack_auto_loads_required_checks_without_manual_check(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _prepared(project, "stage-reviewer")
    artifact = project / "candidate.md"
    artifact.write_text("candidate", encoding="utf-8")

    report = cli.review_pack(argparse.Namespace(
        project=str(project), skill="stage-reviewer", artifact=[str(artifact)], check=[],
    ))

    text = Path(report["path"]).read_text(encoding="utf-8")
    assert report["required_check_count"] == 4
    assert report["manual_check_count"] == 0
    assert "`REVIEW-EVIDENCE`" in text
    assert "自动必检项不可由命令行删除" in text


def test_review_pack_marks_same_candidate_recheck_as_revision_mode(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _prepared(project)
    artifact = project / "candidate.md"
    artifact.write_text("candidate revised", encoding="utf-8")

    args = argparse.Namespace(
        project=str(project), skill="creative-director", artifact=[str(artifact)], check=[],
        review_mode="revision",
    )
    with pytest.raises(cli.StageContextError, match="--previous-review"):
        cli.review_pack(args)
    initial = cli.review_pack(argparse.Namespace(
        project=str(project), skill="creative-director", artifact=[str(artifact)], check=[],
    ))
    previous = project / "review.json"
    previous.write_text(json.dumps({
        "scope": json.loads(Path(initial["review_scope_path"]).read_text(encoding="utf-8")),
        "reviewer_id": "original-reviewer", "issues": [],
    }), encoding="utf-8")
    changes = project / "producer-response.json"
    changes.write_text('{"responses": []}', encoding="utf-8")
    args.previous_review = str(previous)
    args.producer_response = str(changes)
    args.baseline = [str(artifact)]
    report = cli.review_pack(args)

    text = Path(report["path"]).read_text(encoding="utf-8")
    assert report["review_mode"] == "revision"
    assert "- 审核模式：`revision`" in text
    assert "必须回原审核子 agent" in text
    assert "original-reviewer" in text
    assert "artifact_manifest.json" not in text
    assert "project_state.json" not in text
    assert "method_reading_evidence.json" not in text


@pytest.mark.parametrize("semantic_review", [None, {"verdict": "fail", "revision": "R01"}, {"verdict": "pass", "revision": "R00"}, {"verdict": "pass", "revision": "R01"}])
def test_handoff_uses_only_confirmed_current_artifacts_and_prepare_embeds_it(tmp_path: Path, semantic_review) -> None:
    project = _project(tmp_path)
    artifact = project / "R01_叙事包.json"
    write_json(artifact, _current_narrative_package(project))
    (project / "00_项目管理" / "project_state.json").write_text(json.dumps({
        "current_stage": "creative-director", "current_artifacts": ["ART-NARRATIVE-R01"],
        "user_confirmations": [{"artifact_ids": ["ART-NARRATIVE-R01"]}], "review_report": {"verdict": "approved"},
        "accepted_issues": [], "next_stage": "storyboard-planner",
    }, ensure_ascii=False), encoding="utf-8")
    (project / "00_项目管理" / "artifact_manifest.json").write_text(json.dumps({
        "schema_version": "1.0", "artifacts": [{
            "artifact_id": "ART-NARRATIVE-R01", "logical_id": "narrative.package", "type": "narrative-package",
            "revision": "R01", "status": "current", "path_or_url": str(artifact), "user_approved": True,
                "checksum": revision_control.sha256_file(artifact),
        }],
    }, ensure_ascii=False), encoding="utf-8")

    state_path = project / "00_项目管理" / "project_state.json"
    state = json.loads(state_path.read_text())
    if semantic_review is None:
        state.pop("review_report", None)
    else:
        state["review_report"] = semantic_review
    state_path.write_text(json.dumps(state), encoding="utf-8")
    report = cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))
    handoff = Path(report["path"])
    text = handoff.read_text(encoding="utf-8")
    assert "上游候选已审核通过" not in text
    assert "语义自查：本卡未读取结论" in text
    assert "用户确认与依赖已核对" in text
    assert report["artifact_ids"] == ["ART-NARRATIVE-R01"]
    assert "`ART-NARRATIVE-R01`" in handoff.read_text(encoding="utf-8")

    startup = cli.prepare(argparse.Namespace(
        project=str(project), skill="storyboard-planner", objective="形成导演镜头候选",
        request_json=None, vault=str(_method_vault(tmp_path / "method-vault")),
    ))
    assert "## 上一阶段交接事实" in Path(startup["path"]).read_text(encoding="utf-8")


def test_handoff_keeps_video_skill_direct_read_exception(tmp_path: Path) -> None:
    project = _project(tmp_path)
    narrative_artifact = project / "R01_叙事包.json"
    write_json(narrative_artifact, _current_narrative_package(project))
    narrative_entry = {"artifact_id": "ART-NARRATIVE-R01", "logical_id": "narrative.package",
        "type": "narrative-package", "revision": "R01", "status": "current", "user_approved": True,
        "path_or_url": narrative_artifact.name, "checksum": cli.digest(narrative_artifact), "depends_on": []}
    artifact = project / "R01_导演包.json"
    write_json(artifact, _current_storyboard_package(project, narrative_entry))
    board_entry = {"artifact_id": "ART-STORYBOARD-R01", "logical_id": "storyboard.package",
        "type": "storyboard-package", "revision": "R01", "status": "current", "user_approved": True,
        "path_or_url": artifact.name, "checksum": cli.digest(artifact), "depends_on": ["narrative.package"]}
    (project / "00_项目管理" / "project_state.json").write_text(json.dumps({
        "current_stage": "storyboard-director", "current_artifacts": ["ART-NARRATIVE-R01", "ART-STORYBOARD-R01"],
        "user_confirmations": [{"artifact_ids": ["ART-NARRATIVE-R01", "ART-STORYBOARD-R01"]}], "next_stage": "voiceover-to-video-script",
    }, ensure_ascii=False), encoding="utf-8")
    (project / "00_项目管理" / "artifact_manifest.json").write_text(json.dumps({"artifacts": [narrative_entry, board_entry]}), encoding="utf-8")

    report = cli.handoff(argparse.Namespace(project=str(project), to_skill="voiceover-to-video-script"))

    content = Path(report["path"]).read_text(encoding="utf-8")
    assert "不生成或校验 stage_startup.md" in content


def test_prepare_rejects_replaced_handoff_input_and_accepts_regenerated_card(tmp_path):
    project = _project(tmp_path)
    vault = _method_vault(tmp_path / "method-vault")
    args = argparse.Namespace(project=str(project), skill="storyboard-planner", objective="导演修订",
                              request_json=None, vault=str(vault))
    manage = project / "00_项目管理"
    entries = []
    def adopt(revision):
        for item in entries:
            item["status"] = "superseded"
        path = project / (revision + ".json")
        write_json(path, _current_narrative_package(project, revision))
        artifact_id = "NARR-" + revision
        entries.append({"artifact_id": artifact_id, "logical_id": "narrative.package",
                        "type": "narrative-package", "revision": revision, "status": "current",
                        "path_or_url": path.name, "user_approved": True, "checksum": cli.digest(path), "depends_on": []})
        write_json(manage / "artifact_manifest.json", {"artifacts": entries})
        state = load_project_state(project)
        state.update(current_artifacts=[artifact_id], next_stage="storyboard-planner",
                     user_confirmations=[{"artifact_ids": [artifact_id], "confirmation_ref": "user:" + revision}])
        write_project_state(project, state)
    adopt("R01")
    cli.handoff(argparse.Namespace(project=str(project), to_skill=args.skill))
    first = cli.prepare(args)
    before = Path(first["path"]).read_bytes()
    adopt("R02")
    with pytest.raises(cli.StageContextError, match="交接卡.*过期"):
        cli.prepare(args)
    assert Path(first["path"]).read_bytes() == before
    cli.handoff(argparse.Namespace(project=str(project), to_skill=args.skill))
    result = cli.prepare(args)
    text = Path(result["path"]).read_text(encoding="utf-8")
    card = text.split("## 上一阶段交接事实", 1)[1].split("## 必读专业方法", 1)[0]
    assert "NARR-R02" in card and "NARR-R01" not in card


def test_prepare_does_not_consume_another_skills_handoff(tmp_path):
    project = _project(tmp_path)
    path = project / cli.CONTEXT_DIR / "next_stage_handoff.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 下一阶段交接卡\n- 目标 Skill：`storyboard-planner`\nOLD-STAGE-INPUT\n", encoding="utf-8")
    result = cli.prepare(argparse.Namespace(project=str(project), skill="creative-director", objective="编导修订",
        request_json=None, vault=str(_method_vault(tmp_path / "method-vault"))))
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "上一阶段交接事实" not in text and "OLD-STAGE-INPUT" not in text


def test_manifest_invalidation_preserves_planner_repair_handoff(tmp_path):
    from scripts.core.artifacts import mark_impacted_artifacts_stale, save_manifest
    project = _project(tmp_path)
    manifest = {"artifacts": []}
    narrative_path = project / "NARR.json"
    write_json(narrative_path, _current_narrative_package(project))
    entries = [("NARR", "narrative.package", [], narrative_path)]
    assets_path = project / "ASSETS.json"
    write_json(assets_path, {"asset_manifest": []})
    entries.append(("ASSETS", "visual.assets", ["narrative.package"], assets_path))
    narrative_entry = {"artifact_id": "NARR", "revision": "R01", "path_or_url": "NARR.json"}
    board_path = project / "BOARD.json"
    write_json(board_path, _current_storyboard_package(project, narrative_entry))
    entries.append(("BOARD", "storyboard.package", ["narrative.package", "visual.assets"], board_path))
    for artifact_id, logical_id, dependencies, path in entries:
        manifest["artifacts"].append({"artifact_id": artifact_id, "logical_id": logical_id,
            "status": "current", "user_approved": True, "path_or_url": path.name,
            "revision": "R01", "type": "json", "depends_on": dependencies, "checksum": cli.digest(path)})
    write_json(project / "00_项目管理" / "artifact_manifest.json", manifest)
    state = load_project_state(project)
    state.update(current_artifacts=["NARR", "ASSETS", "BOARD"], next_stage="storyboard-planner",
                 user_confirmations=[{"artifact_ids": ["NARR", "ASSETS", "BOARD"]}])
    write_project_state(project, state)
    mark_impacted_artifacts_stale(manifest, {"visual.assets"}, "asset export", "R02")
    # The new asset export replaces its prior manifest before handing back to planning.
    manifest["artifacts"][1]["status"] = "current"
    save_manifest(project, manifest)
    assert load_project_state(project)["current_artifacts"] == ["NARR", "ASSETS"]
    report = cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))
    assert report["artifact_ids"] == ["NARR", "ASSETS"]


def test_review_pack_rejects_manual_check_not_frozen_in_task_basis(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _prepared(project, "stage-reviewer")
    artifact = project / "candidate.md"
    artifact.write_text("candidate", encoding="utf-8")
    required_text = cli._load_review_profile("stage-reviewer")[1]["production_artifact_checks"][0]["text"]

    with pytest.raises(cli.StageContextError, match="不能在审核时另选标准"):
        cli.review_pack(argparse.Namespace(
            project=str(project), skill="stage-reviewer", artifact=[str(artifact)],
            check=[required_text, "项目特有检查"],
        ))


def test_review_kind_contract_uses_same_task_basis_without_project_artifact_facts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _prepared(project, "creative-director")
    contract = cli.ROOT / "skills" / "creative-director" / "SKILL.md"
    report = cli.review_pack(argparse.Namespace(
        project=str(project), skill="creative-director", artifact=[str(contract)], check=[],
        review_kind="skill_contract",
    ))
    text = Path(report["path"]).read_text(encoding="utf-8")
    assert report["review_kind"] == "skill_contract"
    assert "`CONTRACT-TRIGGER`" in text
    assert "## 合同审核边界" in text
    assert "## 产物登记证据" not in text
    assert "task_basis.json" in text


def test_revision_rejects_changed_task_basis(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _prepared(project)
    artifact = project / "candidate.md"
    artifact.write_text("before", encoding="utf-8")
    initial = cli.review_pack(argparse.Namespace(project=str(project), skill="creative-director", artifact=[str(artifact)], check=[]))
    previous = project / "review.json"
    previous.write_text(json.dumps({
        "scope": json.loads(Path(initial["review_scope_path"]).read_text(encoding="utf-8")),
        "reviewer_id": "reviewer", "reviewer_lineage": ["reviewer"], "issues": [],
    }), encoding="utf-8")
    baseline = project / "baseline.md"; baseline.write_text("before", encoding="utf-8")
    artifact.write_text("after", encoding="utf-8")
    changes = project / "producer-response.json"; changes.write_text('{"responses": []}', encoding="utf-8")
    basis = project / cli.CONTEXT_DIR / "task_basis.json"
    current = json.loads(basis.read_text(encoding="utf-8")); current["non_goals"] = ["changed"]
    basis.write_text(json.dumps(current), encoding="utf-8")
    with pytest.raises(cli.StageContextError, match="任务依据"):
        cli.review_pack(argparse.Namespace(
            project=str(project), skill="creative-director", artifact=[str(artifact)], check=[], review_mode="revision",
            previous_review=str(previous), baseline=[str(baseline)], producer_response=str(changes),
        ))


@pytest.mark.parametrize("payload", [
    "data:image/png;base64,AAAA",
    "# 完整对话\nuser: x",
    "required_context_" + "manifest",
    "内部推理",
])
def test_validate_rejects_forbidden_payloads(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "packet.md"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(cli.StageContextError):
        cli.validate_text("startup", path)


def test_validate_fails_without_silent_truncation(tmp_path: Path) -> None:
    path = tmp_path / "handoff.md"
    content = "字" * (cli.LIMITS["handoff"] + 1)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(cli.StageContextError, match="超出"):
        cli.validate_text("handoff", path)
    assert path.read_text(encoding="utf-8") == content


def test_three_consecutive_stage_cycles_use_only_fixed_packets(tmp_path: Path) -> None:
    project = _project(tmp_path)
    context_dir = project / cli.CONTEXT_DIR
    vault = _method_vault(tmp_path / "method-vault")
    for index, skill in enumerate((
        "creative-director", "storyboard-planner", "stage-reviewer"
    ), start=1):
        cli.prepare(argparse.Namespace(
            project=str(project), skill=skill, objective=f"阶段 {index}",
            request_json=None, vault=str(vault),
        ))
        artifact = project / f"candidate-{index}.md"
        artifact.write_text(f"candidate {index}", encoding="utf-8")
        cli.review_pack(argparse.Namespace(
            project=str(project), skill=skill, artifact=[str(artifact)],
            check=[],
        ))
        handoff = context_dir / "next_stage_handoff.md"
        write_json(project / "00_项目管理/artifact_manifest.json", {"artifacts": [{
            "artifact_id": f"ART-{index}", "logical_id": "test.package", "type": "markdown",
            "revision": f"R{index:02d}", "status": "current", "user_approved": True,
            "path_or_url": str(artifact), "checksum": cli.digest(artifact),
        }]})
        state = load_project_state(project)
        state.update(current_stage=skill, current_artifacts=[f"ART-{index}"], next_stage=skill,
                     user_confirmations=[{"artifact_ids": [f"ART-{index}"]}])
        write_project_state(project, state)
        cli.handoff(argparse.Namespace(project=str(project), to_skill=skill))
        assert cli.validate_text("handoff", handoff)["valid"] is True
    assert {path.name for path in context_dir.iterdir()} == {
        "stage_startup.md", "task_basis.json", "review_packet.md", "next_stage_handoff.md", "review_scope.json"
    }
    assert not (context_dir / "review_rounds").exists()
    assert json.loads((context_dir / "review_scope.json").read_text(encoding="utf-8"))["skill"] == "stage-reviewer"


def test_prepare_embeds_active_revision_only_in_existing_startup_packet(tmp_path: Path) -> None:
    project = _project(tmp_path)
    artifact = project / "candidate.json"
    artifact.write_text('{"title":"before"}', encoding="utf-8")
    write_json(project / "00_项目管理" / "artifact_manifest.json", {
        "schema_version": "1.0",
        "artifacts": [{
            "artifact_id": "ART-1", "logical_id": "story.main", "type": "storyboard",
            "revision": "R01", "status": "current", "path_or_url": "candidate.json",
            "depends_on": [], "checksum": revision_control.sha256_file(artifact), "user_approved": True,
        }],
    })
    revision_control.open_change(
        project, change_id="REV-CONTEXT", tier="T1", logical_ids=["story.main"], reason="修订事实"
    )
    revision_control.confirm_impact(project, change_id="REV-CONTEXT", approval_ref="user:impact")

    report = cli.prepare(argparse.Namespace(
        project=str(project), skill="creative-director", objective="局部修订",
        request_json=None, vault=str(_method_vault(tmp_path / "method-vault")),
        change_id="REV-CONTEXT", target_logical_id=["story.main"],
    ))

    content = Path(report["path"]).read_text(encoding="utf-8")
    assert "前序修订约束" in content
    assert "REV-CONTEXT" in content
    assert {path.name for path in (project / cli.CONTEXT_DIR).iterdir()} == {"stage_startup.md", "task_basis.json"}


def test_startup_validation_rejects_skill_boundary_residue(tmp_path: Path) -> None:
    path = tmp_path / "stage_startup.md"
    text = "\n".join([
        "# 阶段启动包",
        "",
        "- Skill：`creative-director`",
        "",
        "# creative-director 当前知识包",
        "",
        "知识要求说明机位如何处理。",
        "",
        "## 选择记录",
    ])

    with pytest.raises(cli.StageContextError, match="Skill 边界残留：机位"):
        cli.validate_text("startup", path, text)


def test_startup_boundary_check_ignores_objective_that_states_prohibition(tmp_path: Path) -> None:
    path = tmp_path / "stage_startup.md"
    text = "\n".join([
        "# 阶段启动包",
        "",
        "- Skill：`creative-director`",
        "- 目标：不得设计机位",
        "",
        "# creative-director 当前知识包",
        "",
        "只处理故事因果。",
        "",
        "## 选择记录",
    ])

    assert cli.validate_text("startup", path, text)["valid"] is True


def test_ensure_prepared_distinguishes_empty_selection_and_missing_evidence(tmp_path: Path) -> None:
    project = _project(tmp_path)
    report = cli.prepare(argparse.Namespace(
        project=str(project), skill="creative-director", objective="编导候选",
        request_json=None, vault=str(_method_vault(tmp_path / "method-vault")),
    ))
    args = argparse.Namespace(project=str(project), skill="creative-director")
    assert cli.ensure_prepared(args)["knowledge_compliance"] == "requires_agent_review"
    args.required_rule = ["MSB-KB-REQUIRED-001"]
    with pytest.raises(cli.StageContextError, match="必需动态规则未命中"):
        cli.ensure_prepared(args)
    args.required_rule = []
    args.required_reference = ["skills/creative-director/modules/story-method.md"]
    gate = cli.ensure_prepared(args)
    assert gate["ok"] is True and "method_reading_evidence" not in gate
    args.required_reference = ["missing-reference.md"]
    with pytest.raises(cli.StageContextError, match="必需本地规范"):
        cli.ensure_prepared(args)
    args.required_reference = []
    path = Path(report["path"])
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("- 已选知识块：无", "- 已选知识块：MSB-KB-REQUIRED-001"), encoding="utf-8")
    with pytest.raises(cli.StageContextError, match="正文不一致"):
        cli.ensure_prepared(args)
    path.write_text(original.split("## 选择记录")[0], encoding="utf-8")
    with pytest.raises(cli.StageContextError, match="缺少已选知识记录"):
        cli.ensure_prepared(args)


@pytest.mark.parametrize("skill", ["blender-previs", "voice-reference-manager", "stage-reviewer", "knowledge-maintainer"])
def test_three_layer_prepare_keeps_unmigrated_skill_compatible(tmp_path: Path, skill: str) -> None:
    project = _project(tmp_path)
    original = (project / "00_项目管理/project_state.json").read_bytes()
    args = argparse.Namespace(project=str(project), skill=skill, objective="隔离兼容性检查", request_json=None, vault=str(tmp_path / "missing-vault"))
    report = cli.prepare(args)
    assert report["required_method_count"] == 0
    assert cli.ensure_prepared(args)["ok"] is True
    assert {item.name for item in (project / cli.CONTEXT_DIR).iterdir()} == {"stage_startup.md", "task_basis.json"}
    assert (project / "00_项目管理/project_state.json").read_bytes() == original


@pytest.mark.parametrize("skill,count", [("asset-image-assistant", 5), ("storyboard-director", 2)])
def test_third_batch_stage_requires_method_files_and_reading_before_production(tmp_path: Path, skill: str, count: int) -> None:
    project = _project(tmp_path)
    request = project / 'request.json'
    request.write_text(json.dumps({'asset_method_scopes': dict.fromkeys(['style', 'character', 'scene', 'object'], True)}), encoding='utf-8')
    args = argparse.Namespace(project=str(project), skill=skill, objective='隔离第三批读取测试',
        request_json=str(request), vault=str(tmp_path/'missing-vault'), actor_id='test-agent', task_id='test-method-binding')
    with pytest.raises(cli.StageContextError, match='专业方法文件缺失'):
        cli.prepare(args)
    vault = _method_vault(tmp_path/'method-vault')
    args.vault = str(vault)
    report = cli.prepare(args)
    assert report['required_method_count'] == count
    assert cli.ensure_prepared(args)['ok'] is False
    # Unit-test agent simulation only; does not confirm any project/production artifact.
    _record_methods(project, skill, vault)
    assert cli.ensure_prepared(args)['ok'] is True


def test_visual_supplement_cannot_continue_with_only_shared_method(tmp_path: Path) -> None:
    project = _project(tmp_path)
    state_path = project / '00_项目管理/project_state.json'
    original_state = state_path.read_bytes()
    vault = _method_vault(tmp_path/'method-vault')
    source = vault/'projects/Master-Storyboarding/应用知识/图片资产/视觉补充方法.md'
    content = source.read_text(encoding='utf-8')
    source.unlink()  # Deliberately missing in an isolated fixture; shared method still exists.
    args = argparse.Namespace(project=str(project), skill='storyboard-director',
        objective='隔离方法依赖测试', request_json=None, vault=str(vault),
        actor_id='test-agent', task_id='test-method-binding')
    with pytest.raises(cli.StageContextError, match='专业方法文件缺失：VISUAL-SUPPLEMENT-METHOD'):
        cli.prepare(args)
    source.write_text(content, encoding='utf-8')
    cli.prepare(args)
    for only_method in ['IMAGE-CONTINUITY-METHOD', 'VISUAL-SUPPLEMENT-METHOD']:
        with pytest.raises(cli.StageContextError, match='全部必读专业方法'):
            cli.record_method_reading(argparse.Namespace(**vars(args), method=[only_method], candidate_rule=[]))
        assert cli.ensure_prepared(args)['ok'] is False
    _record_methods(project, args.skill, vault)  # Synthetic test receipt, never production approval.
    assert cli.ensure_prepared(args)['ok'] is True
    assert state_path.read_bytes() == original_state


def test_optional_windows_relative_path_opens_only_inside_selected_vault(tmp_path: Path) -> None:
    vault = tmp_path / "测试 Vault 空格"
    relative = "wiki\\项目 经验\\可选 中文.md"
    source = vault / relative.replace("\\", "/")
    source.parent.mkdir(parents=True)
    source.write_text("该经验只对另一类任务适用，当前可跳过。", encoding="utf-8")
    paths = cli._optional_experience_paths(vault, {"optional_experience_scopes": [relative]})
    assert paths == [("wiki/项目 经验/可选 中文.md", True)]
    assert (vault / paths[0][0]).read_text(encoding="utf-8").startswith("该经验")


@pytest.mark.parametrize("relative", ["../outside.md", "wiki/../../outside.md", "C:/outside.md", "C:outside.md", "\\\\server\\share\\outside.md", "wiki\\..\\outside.md", "wiki/目录"])
def test_optional_experience_rejects_outside_or_non_file_scope(tmp_path: Path, relative: str) -> None:
    with pytest.raises(cli.StageContextError):
        cli._optional_experience_paths(tmp_path / "vault", {"optional_experience_scopes": [relative]})


def test_cli_required_arguments_repeat_and_keep_confirmation_authorization_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = (project / "00_项目管理/project_state.json").read_bytes()
    args = argparse.Namespace(project=str(project), skill="creative-director", objective="隔离参数测试", request_json=None, vault=str(_method_vault(tmp_path / "method-vault")))
    cli.prepare(args)
    gate = cli.parser().parse_args(["ensure-prepared", "--project", str(project), "--skill", "creative-director", "--required-rule", "MSB-KB-TEST-A", "--required-rule", "MSB-KB-TEST-B", "--required-reference", "PROJECT_RULES.md", "--required-reference", "skills/creative-director/modules/story-method.md"])
    assert gate.required_rule == ["MSB-KB-TEST-A", "MSB-KB-TEST-B"]
    assert len(gate.required_reference) == 2
    with pytest.raises(cli.StageContextError, match="必需动态规则未命中"):
        cli.ensure_prepared(gate)
    gate.required_rule = []
    result = cli.ensure_prepared(gate)
    assert result["ok"] is True and "method_reading_evidence" not in result
    assert (project / "00_项目管理/project_state.json").read_bytes() == original
    with pytest.raises(cli.StageContextError):
        cli.handoff(argparse.Namespace(project=str(project), to_skill="storyboard-planner"))
    assert not (project / "00_项目管理/artifact_manifest.json").exists()


@pytest.mark.parametrize("reference", ["../outside.md", "C:/outside.md", "missing-reference.md"])
def test_required_reference_rejects_outside_or_missing(tmp_path: Path, reference: str) -> None:
    project = _project(tmp_path)
    args = argparse.Namespace(project=str(project), skill="creative-director", objective="隔离本地规范路径测试", request_json=None, vault=str(_method_vault(tmp_path / "method-vault")))
    cli.prepare(args)
    args.required_reference = [reference]
    with pytest.raises(cli.StageContextError, match="必需本地规范"):
        cli.ensure_prepared(args)


def test_ensure_prepared_rejects_empty_rule_body(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = project / cli.CONTEXT_DIR / "stage_startup.md"
    path.parent.mkdir(parents=True)
    text = "# 阶段启动包\n- Skill：`creative-director`\n# creative-director 当前知识包\n## MSB-KB-TEST-001 [enabled]\n适用条件：测试\n\n## 选择记录\n- 已选知识块：MSB-KB-TEST-001\n"
    path.write_text(text, encoding="utf-8")
    args = argparse.Namespace(project=str(project), skill="creative-director")
    with pytest.raises(cli.StageContextError, match="正文为空"):
        cli.ensure_prepared(args)
    path.write_text(text.replace("适用条件：测试", "适用条件：测试\n\n保留故事事实。"), encoding="utf-8")
    args.required_rule = ["MSB-KB-TEST-001"]
    assert cli.ensure_prepared(args)["ok"]


def test_startup_boundary_scan_does_not_treat_rule_body_heading_as_candidate_partition(tmp_path: Path) -> None:
    profile = cli.ROOT / "skills" / "creative-director" / "knowledge-profile.json"
    forbidden = json.loads(profile.read_text(encoding="utf-8"))["forbidden_knowledge_terms"][0]
    startup = "\n".join(["# 阶段启动包", "", "- Skill：`creative-director`", "", "# creative-director 当前知识包", "", "## MSB-KB-X [enabled]", "", "规则正文", "## 按需知识候选", forbidden, "", "## 选择记录", "", "- 已选知识块：MSB-KB-X", ""])
    with pytest.raises(cli.StageContextError, match="Skill 边界残留"):
        cli.validate_text("startup", tmp_path / "stage_startup.md", startup)


def test_other_skills_keep_isolated_nonempty_profile_rule_compatible(tmp_path: Path) -> None:
    skills = ("asset-image-assistant", "blender-previs", "creative-director", "knowledge-maintainer", "storyboard-director", "storyboard-planner", "stage-reviewer", "voice-reference-manager")
    vault = _method_vault(tmp_path / "vault")
    profiles = {skill: json.loads((cli.ROOT / "skills" / skill / "knowledge-profile.json").read_text(encoding="utf-8")) for skill in skills}
    topics = sorted({topic for profile in profiles.values() for topic in profile.get("topics", [])})
    metadata = {"id":"MSB-KB-COMPAT-REAL", "canonical_key":"compat.real", "kind":"rule", "status":"enabled", "authority":"wiki", "projects":["Master-Storyboarding"], "stages":["*"], "topics":topics, "triggers":[], "conflicts_with":[], "priority":1, "sources":[], "applies_when":"兼容测试"}
    page = vault / "wiki" / "concepts" / "compat.md"; page.parent.mkdir(parents=True)
    page.write_text("<!-- msb-knowledge\n" + json.dumps(metadata, ensure_ascii=False) + "\n-->\n兼容规则正文。\n^msb-kb-compat-real\n<!-- /msb-knowledge -->", encoding="utf-8")
    for skill in skills:
        project = _project(tmp_path / skill)
        args = argparse.Namespace(project=str(project), skill=skill, objective="隔离合成非空知识兼容", request_json=None, vault=str(vault))
        report = cli.prepare(args)
        text = Path(report["path"]).read_text(encoding="utf-8")
        if profiles[skill].get("topics", []):
            assert "兼容规则正文。" in text
        else:
            assert "兼容规则正文。" not in text and "已选知识块：无" in text
        gate = cli.ensure_prepared(args)
        if skill in {"storyboard-planner", "asset-image-assistant", "storyboard-director"}:
            assert gate["ok"] is False and gate["method_reading_evidence"] == "required"
        else:
            assert gate["ok"] is True


@pytest.mark.parametrize("invalid", [None, "receipt_hash", "file_hash", "approval"])
def test_handoff_revision_receipt_relative_path_and_hash(tmp_path, invalid):
    project = _project(tmp_path)
    narrative_path = project / "R03_narrative.json"
    write_json(narrative_path, _current_narrative_package(project, "R03"))
    narrative_entry = {"artifact_id": "ART-NARRATIVE-R03", "logical_id": "narrative.package",
        "type": "narrative-package", "revision": "R03", "status": "current", "user_approved": True,
        "path_or_url": narrative_path.name, "checksum": cli.digest(narrative_path), "depends_on": []}
    artifact = project / "03_director" / "R03.json"
    artifact.parent.mkdir()
    board = _current_storyboard_package(project, narrative_entry, "R03")
    write_json(artifact, board)
    checksum = cli.digest(artifact)
    entry = {"artifact_id": "ART-R03", "logical_id": "storyboard.package",
             "type": "storyboard.package", "revision": "R03", "status": "current",
             "user_approved": True, "path_or_url": "03_director/R03.json", "checksum": checksum,
             "depends_on": ["narrative.package"]}
    receipt = {"kind": "revision-candidate", "artifact_id": "ART-R03",
               "sha256": checksum, "approval_ref": "approval.json"}
    if invalid == "receipt_hash": receipt["sha256"] = "wrong"
    if invalid == "approval": receipt.pop("approval_ref")
    if invalid == "file_hash": artifact.write_text("changed", encoding="utf-8")
    manage = project / "00_项目管理"
    (manage / "project_state.json").write_text(json.dumps({
        "current_stage": "storyboard-planner", "next_stage": "storyboard-director",
        "current_artifacts": ["ART-NARRATIVE-R03", "ART-R03"],
        "user_confirmations": [{"artifact_ids": ["ART-NARRATIVE-R03"]}, receipt]}), encoding="utf-8")
    (manage / "artifact_manifest.json").write_text(json.dumps({"artifacts": [narrative_entry, entry]}), encoding="utf-8")
    args = argparse.Namespace(project=str(project), to_skill="storyboard-director")
    if invalid:
        with pytest.raises(cli.StageContextError): cli.handoff(args)
    else:
        report = cli.handoff(args)
        assert str(artifact.resolve()) in Path(report["path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("requested", [False, True])
def test_creative_story_method_is_optional_but_selected_method_keeps_reading_gate(tmp_path, requested, monkeypatch):
    project = _project(tmp_path)
    vault = _method_vault(tmp_path / "vault")
    _synthetic_method_profile(tmp_path, vault, monkeypatch)
    request = project / "request.json"
    request.write_text(json.dumps({"use_story_method": requested}), encoding="utf-8")
    args = argparse.Namespace(project=str(project), skill="creative-director", objective="局部故事修订",
        request_json=str(request), vault=str(vault), actor_id="test-agent", task_id="test-method-binding")
    original = (project / "00_项目管理/project_state.json").read_bytes()
    report = cli.prepare(args)
    assert report["required_method_count"] == int(requested)
    assert cli.ensure_prepared(args)["ok"] is (not requested)
    if requested:
        _record_methods(project, args.skill, vault)
        assert cli.ensure_prepared(args)["ok"]
        args.actor_id = "not-the-reader"
        assert cli.ensure_prepared(args)["ok"] is False
    assert (project / "00_项目管理/project_state.json").read_bytes() == original
    assert not (project / "00_项目管理/artifact_manifest.json").exists()


def test_creative_selected_method_missing_is_not_silently_skipped(tmp_path):
    project = _project(tmp_path)
    vault = tmp_path / "empty-vault"
    vault.mkdir()
    request = project / "request.json"
    request.write_text('{"use_story_method": true}', encoding="utf-8")
    args = argparse.Namespace(project=str(project), skill="creative-director", objective="需要故事诊断",
        request_json=str(request), vault=str(vault), actor_id="test-agent", task_id="test-method-binding")
    with pytest.raises(cli.StageContextError):
        cli.prepare(args)


def test_explicit_template_dependency_is_frozen_and_missing_is_reported(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    skill = root / "skills" / "storyboard-planner"
    (skill / "templates").mkdir(parents=True)
    (root / "PROJECT_RULES.md").write_text("# Rules", encoding="utf-8")
    (skill / "SKILL.md").write_text("[模板](templates/current.md)", encoding="utf-8")
    template = skill / "templates/current.md"
    template.write_text("first", encoding="utf-8")
    unrelated = skill / "templates/historical.md"
    unrelated.write_text("historical", encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", root)
    paths = cli._local_standard_documents(skill)
    assert template.resolve() in paths
    assert unrelated.resolve() not in paths
    before = cli.fingerprint(template)
    template.write_text("changed", encoding="utf-8")
    assert cli.fingerprint(template) != before
    template.unlink()
    with pytest.raises(cli.StageContextError, match="本地规范缺失"):
        cli._local_standard_documents(skill)


def test_current_director_template_is_in_local_contracts():
    skill = cli.ROOT / "skills/storyboard-planner"
    assert (skill / "templates/专业影视分镜输出模板.md").resolve() in cli._local_standard_documents(skill)
