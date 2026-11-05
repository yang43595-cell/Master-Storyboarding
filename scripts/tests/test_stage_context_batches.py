"""Synthetic batch-context checks; reading receipts assert no actual production."""
from __future__ import annotations

import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from scripts import stage_context_cli as cli
from test_stage_context_cli import _method_vault, _project


def make_batch_context(tmp_path: Path, *, context_id="batch-a", actor_id="worker-a",
                       task_id="task-a", targets=None, skill="storyboard-director",
                       project=None, vault=None):
    """Reusable isolated-input builder for larger synthetic handoff tests."""
    project = project or _project(tmp_path)
    vault = vault or _method_vault(tmp_path / "method-vault")
    requests = project / "requests"
    requests.mkdir(exist_ok=True)
    request = requests / (context_id + ".json")
    request.write_text(json.dumps({"image_targets": targets if targets is not None else ["VS-S01-OPEN"],
                                   "source_shot_ids": ["S01-001"]}), encoding="utf-8")
    args = cli.parser().parse_args(["prepare", "--project", str(project), "--skill", skill,
        "--objective", "synthetic image batch only", "--request-json", str(request),
        "--vault", str(vault), "--actor-id", actor_id, "--task-id", task_id,
        "--context-id", context_id])
    return args, request


def batch_folder(args):
    return Path(args.project) / cli.CONTEXT_DIR / "批次" / args.context_id


def record_batch_methods(args):
    """Declare the selected synthetic methods using this worker's real identity."""
    basis = json.loads((batch_folder(args) / "task_basis.json").read_text(encoding="utf-8"))
    return cli.record_method_reading(argparse.Namespace(**vars(args),
        method=[item["id"] for item in basis["reading_manifest"]["methods"]],
        candidate_scope=[]))


def test_two_concurrent_batches_keep_worker_inputs_receipts_and_cache_separate(tmp_path):
    first, _ = make_batch_context(tmp_path)
    second, _ = make_batch_context(tmp_path, project=Path(first.project), vault=Path(first.vault),
        context_id="batch-b", actor_id="worker-b", task_id="task-b", targets=["VS-S02-CLOSE"])
    state = Path(first.project) / "00_项目管理/project_state.json"
    before = state.read_bytes()
    barrier = Barrier(2)

    def run(args):
        barrier.wait(timeout=15)
        cli.prepare(args)
        barrier.wait(timeout=15)
        record_batch_methods(args)
        return cli.ensure_prepared(argparse.Namespace(**vars(args), show_task_basis=True))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (first, second)))
    for args, result in zip((first, second), results):
        assert result["ok"] and result["task_basis"]["context_id"] == args.context_id
        folder = batch_folder(args)
        basis = json.loads((folder / "task_basis.json").read_text(encoding="utf-8"))
        evidence = json.loads((folder / "method_reading_evidence.json").read_text(encoding="utf-8"))
        assert evidence["actor_id"] == args.actor_id
        assert evidence["task_id"] == args.task_id
        assert evidence["context_id"] == args.context_id
        assert basis["scope"]["image_targets"] == json.loads(Path(args.request_json).read_text())["image_targets"]
        assert (folder / "stage_reading_cache" / (args.skill + ".json")).is_file()
    assert state.read_bytes() == before
    assert not (Path(first.project) / cli.CONTEXT_DIR / "stage_startup.md").exists()
    assert cli._context_dir(Path(first.project)) == Path(first.project) / cli.CONTEXT_DIR


@pytest.mark.parametrize("targets,skill", [
    ([], "storyboard-director"),
    ([f"VS-OUT-{number}" for number in range(9)], "storyboard-director"),
    (["VS-SAME", "VS-SAME"], "storyboard-director"),
    ([None], "storyboard-director"),
    ([""], "storyboard-director"),
    (["../output.png"], "storyboard-director"),
    (["AST-CHAR"], "asset-image-assistant"),
    (["AST-CHAR@"], "asset-image-assistant"),
])
def test_invalid_image_targets_fail_before_any_context_write(tmp_path, targets, skill):
    args, _ = make_batch_context(tmp_path, targets=targets, skill=skill)
    with pytest.raises(cli.StageContextError, match="image_targets"):
        cli.prepare(args)
    assert not (Path(args.project) / cli.CONTEXT_DIR).exists()


@pytest.mark.parametrize("skill", ["asset-image-assistant", "storyboard-director"])
def test_eight_exact_targets_are_allowed_and_frozen(tmp_path, skill):
    targets = ([f"AST-SYNTHETIC@view-{number}" for number in range(8)]
               if skill == "asset-image-assistant" else
               [f"VS-SYNTHETIC-{number}" for number in range(8)])
    args, _ = make_batch_context(tmp_path, skill=skill, targets=targets)
    args.vault = None  # Use the shipped method bundle for either image Skill.
    assert cli.prepare(args)["valid"]
    basis = json.loads((batch_folder(args) / "task_basis.json").read_text(encoding="utf-8"))
    assert basis["scope"]["image_targets"] == targets


@pytest.mark.parametrize("context_id", ["", "../escape", "/absolute", "a/b", "a\\b", ".", "a b", "BATCH-A", "batch-A"])
def test_context_id_rejects_unsafe_paths_without_writes(tmp_path, context_id):
    args, _ = make_batch_context(tmp_path)
    args.context_id = context_id
    with pytest.raises(cli.StageContextError, match="context-id"):
        cli.prepare(args)
    assert not (Path(args.project) / cli.CONTEXT_DIR).exists()


def test_case_alias_cannot_overwrite_an_existing_batch(tmp_path):
    args, _ = make_batch_context(tmp_path)
    cli.prepare(args)
    record_batch_methods(args)
    folder = batch_folder(args)
    before = {path.relative_to(folder): path.read_bytes()
              for path in folder.rglob("*") if path.is_file()}
    alias = argparse.Namespace(**vars(args))
    alias.context_id = args.context_id.upper()
    alias.actor_id, alias.task_id = "other-worker", "other-task"
    with pytest.raises(cli.StageContextError, match="规范小写"):
        cli.prepare(alias)
    assert before == {path.relative_to(folder): path.read_bytes()
                      for path in folder.rglob("*") if path.is_file()}
    assert cli.ensure_prepared(args)["ok"]


@pytest.mark.parametrize("component", ["执行上下文", "批次", "batch-a"])
def test_context_path_rejects_symlink_at_any_selected_component(tmp_path, component):
    args, _ = make_batch_context(tmp_path)
    base = Path(args.project) / cli.CONTEXT_DIR
    link = {"执行上下文": base, "批次": base / "批次", "batch-a": batch_folder(args)}[component]
    link.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This platform cannot create the test directory symlink")
    with pytest.raises(cli.StageContextError, match="符号链接"):
        cli.prepare(args)
    assert not list(outside.iterdir())


def test_cross_batch_receipts_and_copied_task_basis_cannot_authorize_reading(tmp_path):
    first, _ = make_batch_context(tmp_path)
    second, _ = make_batch_context(tmp_path, project=Path(first.project), vault=Path(first.vault),
        context_id="batch-b", actor_id=first.actor_id, task_id=first.task_id)
    for args in (first, second):
        cli.prepare(args)
        record_batch_methods(args)
        assert cli.ensure_prepared(args)["ok"]
    shutil.copyfile(batch_folder(first) / "method_reading_evidence.json",
                    batch_folder(second) / "method_reading_evidence.json")
    assert cli.ensure_prepared(second)["ok"] is False
    shutil.copyfile(batch_folder(first) / "task_basis.json", batch_folder(second) / "task_basis.json")
    with pytest.raises(cli.StageContextError, match="任务清单缺失|另一批次"):
        cli.ensure_prepared(second)


def test_same_batch_target_change_invalidates_old_reading_receipt(tmp_path):
    args, request = make_batch_context(tmp_path)
    cli.prepare(args)
    record_batch_methods(args)
    assert cli.ensure_prepared(args)["ok"]
    payload = json.loads(request.read_text())
    payload["image_targets"] = ["VS-NEW-TARGET"]
    request.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(cli.StageContextError, match="实质范围已改变"):
        cli.ensure_prepared(args)
    result = cli.prepare(args)
    assert result["stage_reading_restored"] is False
    assert cli.ensure_prepared(args)["ok"] is False


def test_context_cli_show_basis_and_default_context_compatibility(tmp_path, capsys):
    args, _ = make_batch_context(tmp_path)
    cli.prepare(args)
    record_batch_methods(args)
    assert cli.main(["ensure-prepared", "--project", args.project, "--skill", args.skill,
        "--context-id", args.context_id, "--actor-id", args.actor_id, "--task-id", args.task_id,
        "--show-task-basis"]) == 0
    assert json.loads(capsys.readouterr().out)["task_basis"]["context_id"] == args.context_id
    default = argparse.Namespace(**vars(args))
    default.context_id = None
    default.request_json = None
    cli.prepare(default)  # Existing non-batch image preparation remains compatible.
    assert (Path(args.project) / cli.CONTEXT_DIR / "task_basis.json").is_file()
    assert "context_id" not in json.loads((Path(args.project) / cli.CONTEXT_DIR / "task_basis.json").read_text())
    assert cli.ensure_prepared(args)["ok"]


def test_non_image_context_does_not_require_image_targets(tmp_path):
    args, _ = make_batch_context(tmp_path, skill="creative-director")
    args.request_json = None
    cli.prepare(args)
    basis = json.loads((batch_folder(args) / "task_basis.json").read_text(encoding="utf-8"))
    assert basis["context_id"] == args.context_id
    assert "image_targets" not in basis["task_request"]


def test_review_and_handoff_use_batch_context_without_changing_project_stage(tmp_path):
    args, _ = make_batch_context(tmp_path)
    project = Path(args.project)
    source = project / "source.txt"
    source.write_text("Synthetic source only", encoding="utf-8")
    state_path = project / "00_项目管理/project_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(current_artifacts=["REF"], next_stage="storyboard-planner",
        user_confirmations=[{"scope": "artifact", "artifact_ids": ["REF"],
                             "approval_ref": "SYNTHETIC ONLY"}])
    state_path.write_text(json.dumps(state), encoding="utf-8")
    manifest_path = project / "00_项目管理/artifact_manifest.json"
    manifest_path.write_text(json.dumps({"artifacts": [{"artifact_id": "REF", "logical_id": "reference.test",
        "type": "reference", "revision": "R01", "path_or_url": source.name, "status": "current",
        "user_approved": True, "checksum": cli.digest(source), "depends_on": []}]}), encoding="utf-8")
    state_before, manifest_before = state_path.read_bytes(), manifest_path.read_bytes()
    cli.prepare(args)
    record_batch_methods(args)
    reviewed = cli.review_pack(cli.parser().parse_args(["review-pack", "--project", args.project,
        "--skill", args.skill, "--artifact", str(source), "--context-id", args.context_id]))
    handed = cli.handoff(cli.parser().parse_args(["handoff", "--project", args.project,
        "--to-skill", "storyboard-planner", "--context-id", args.context_id]))
    assert Path(reviewed["path"]).parent == batch_folder(args)
    assert Path(reviewed["review_scope_path"]).parent == batch_folder(args)
    assert Path(handed["path"]).parent == batch_folder(args)
    assert state_path.read_bytes() == state_before
    assert manifest_path.read_bytes() == manifest_before
    assert cli._context_dir(project) == project / cli.CONTEXT_DIR


def test_symlinked_batch_cache_cannot_receive_reading_writes(tmp_path):
    args, _ = make_batch_context(tmp_path)
    cli.prepare(args)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    try:
        (batch_folder(args) / "stage_reading_cache").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This platform cannot create the test directory symlink")
    with pytest.raises(cli.StageContextError, match="符号链接"):
        record_batch_methods(args)
    assert not list(outside.iterdir())
    assert cli._context_dir(Path(args.project)) == Path(args.project) / cli.CONTEXT_DIR


def test_nested_prepare_restores_outer_context_and_failed_call_resets_scope(tmp_path, monkeypatch):
    outer, _ = make_batch_context(tmp_path)
    inner, _ = make_batch_context(tmp_path, project=Path(outer.project), vault=Path(outer.vault),
        context_id="batch-inner", actor_id="worker-inner", task_id="task-inner", targets=["VS-INNER"])
    original = cli._load_review_profile
    nested = False

    def read_profile(skill):
        nonlocal nested
        if not nested:
            nested = True
            cli.prepare(inner)
        return original(skill)

    monkeypatch.setattr(cli, "_load_review_profile", read_profile)
    result = cli.prepare(outer)
    assert Path(result["path"]).parent == batch_folder(outer)
    for args in (outer, inner):
        basis = json.loads((batch_folder(args) / "task_basis.json").read_text(encoding="utf-8"))
        assert basis["reading_manifest"]["actor_id"] == args.actor_id
        assert basis["context_id"] == args.context_id
    outer.skill = "voiceover-to-video-script"
    with pytest.raises(cli.StageContextError, match="准备包流程已退役"):
        cli.prepare(outer)
    assert cli._context_dir(Path(outer.project)) == Path(outer.project) / cli.CONTEXT_DIR
