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
from scripts.core.character_layout import character_layout_reference, character_prompt_templates
from test_stage_context_cli import _method_vault, _project, isolated_stage_knowledge


pytestmark = pytest.mark.usefixtures("isolated_stage_knowledge")


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


def test_parallel_ordinary_tasks_follow_their_bound_handoffs_and_reuse_reading(tmp_path):
    project = _project(tmp_path)
    manage = project / "00_项目管理"
    state_path = manage / "project_state.json"
    manifest_path = manage / "artifact_manifest.json"
    base_context = project / cli.CONTEXT_DIR
    source = project / "verified-source.txt"
    source.write_text("Synthetic verified source", encoding="utf-8")
    request = project / "creative-method.json"
    request.write_text(json.dumps({"use_story_method": True}), encoding="utf-8")
    manifest_path.write_text(json.dumps({"artifacts": [{"artifact_id": "REF", "logical_id": "reference.test",
        "type": "reference", "revision": "R01", "path_or_url": source.name, "status": "current",
        "user_approved": True, "checksum": cli.digest(source), "depends_on": []}]}), encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(current_artifacts=["REF"], user_confirmations=[{"scope": "artifact", "artifact_ids": ["REF"],
        "approval_ref": "SYNTHETIC ONLY"}])
    project_args = ["--project", str(project)]
    stage_args = []
    handoffs = []
    for skill, actor, task in (("creative-director", "/root/creative", "draft-a"),
                               ("storyboard-planner", "/root/planning", "board-a")):
        # Shared authorization chooses each next stage, while its handoff does not mutate project state.
        state_path.write_text(json.dumps({**state, "next_stage": skill}), encoding="utf-8")
        identity = ["--actor-id", actor, "--task-id", task]
        handoff_args = cli.parser().parse_args(["handoff", *project_args, "--to-skill", skill, *identity])
        handoffs.append(cli.handoff(handoff_args))
        stage_args.append([*project_args, "--skill", skill, *identity])
    shared_before = (state_path.read_bytes(), manifest_path.read_bytes())
    assert handoffs[0]["context_id"] != handoffs[1]["context_id"]
    barrier = Barrier(2)

    def run(commands):
        stage, handoff = commands
        prepare_args = cli.parser().parse_args(["prepare", *stage, "--objective", "Synthetic ordinary task",
                                                "--request-json", str(request)])
        barrier.wait(timeout=15)
        prepared = cli.prepare(prepare_args)
        barrier.wait(timeout=15)
        folder = Path(prepared["path"]).parent
        assert prepared["context_id"] == handoff["context_id"]
        assert folder == Path(handoff["path"]).parent
        assert "上一阶段交接事实" in Path(prepared["path"]).read_text(encoding="utf-8")
        basis = json.loads((folder / "task_basis.json").read_text(encoding="utf-8"))
        method_args = [arg for item in basis["reading_manifest"]["methods"] for arg in ("--method", item["id"])]
        reading = cli.record_method_reading(cli.parser().parse_args(["record-method-reading", *stage, *method_args]))
        ensure_args = cli.parser().parse_args(["ensure-prepared", *stage, "--show-task-basis"])
        assert cli.ensure_prepared(ensure_args)["ok"]
        assert Path(reading["path"]).parent == folder
        return prepare_args, ensure_args, Path(reading["path"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(run, zip(stage_args, handoffs))
    prepare_args, ensure_args, receipt_path = first
    receipt = receipt_path.read_bytes()
    cli.prepare(prepare_args)
    assert receipt_path.read_bytes() == receipt
    for _, ensure, _ in (first, second):
        assert cli.ensure_prepared(ensure)["ok"]
    explicit = ["handoff", *project_args, "--to-skill", "storyboard-planner", "--actor-id", "/root/planning",
                "--task-id", "board-a", "--context-id", "explicit-context"]
    assert cli.handoff(cli.parser().parse_args(explicit))["context_id"] == "explicit-context"
    assert cli.ensure_prepared(second[1])["context_id"] == handoffs[1]["context_id"]
    assert not (base_context / "stage_startup.md").exists()
    assert not (base_context / "next_stage_handoff.md").exists()
    assert (state_path.read_bytes(), manifest_path.read_bytes()) == shared_before


def test_scoped_handoff_change_requires_preparation_and_drives_current_goal(tmp_path):
    project = _project(tmp_path)
    manage = project / "00_项目管理"
    source = project / "verified-source.txt"
    source.write_text("Verified source", encoding="utf-8")
    state_path = manage / "project_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(next_stage="creative-director", current_artifacts=["REF"],
                 user_confirmations=[{"scope": "artifact", "artifact_ids": ["REF"], "approval_ref": "SYNTHETIC"}])
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (manage / "artifact_manifest.json").write_text(json.dumps({"artifacts": [{"artifact_id": "REF", "logical_id": "reference.test",
        "type": "reference", "revision": "R01", "path_or_url": source.name, "status": "current",
        "user_approved": True, "checksum": cli.digest(source), "depends_on": []}]}), encoding="utf-8")
    request = project / "request.json"
    request.write_text('{"use_story_method": true}', encoding="utf-8")
    identity = ["--project", str(project), "--actor-id", "worker", "--task-id", "draft"]
    stage = [*identity, "--skill", "creative-director"]
    handoff = ["handoff", *identity, "--to-skill", "creative-director"]
    parse = cli.parser().parse_args

    cli.handoff(parse([*handoff, "--objective", "Scope A", "--stop-at", "Stop A"]))
    prepare = parse(["prepare", *stage, "--request-json", str(request), "--objective", "Scope A"])
    folder = Path(cli.prepare(prepare)["path"]).parent
    basis_path = folder / "task_basis.json"
    basis = json.loads(basis_path.read_text(encoding="utf-8"))
    methods = [arg for item in basis["reading_manifest"]["methods"] for arg in ("--method", item["id"])]
    assert methods
    evidence = Path(cli.record_method_reading(parse(["record-method-reading", *stage, *methods]))["path"])
    attestation = evidence.read_bytes()
    ensure = parse(["ensure-prepared", *stage, "--show-task-basis"])
    assert cli.ensure_prepared(ensure)["ok"]

    # Existing preparations stored the original card in startup before a separate scope field existed.
    del basis["handoff_scope"]
    basis_path.write_text(json.dumps(basis), encoding="utf-8")
    assert cli.ensure_prepared(ensure)["ok"]
    cli.handoff(parse([*handoff, "--objective", "Scope B", "--stop-at", "Stop B"]))
    with pytest.raises(cli.StageContextError, match="交接目标、停止点.*已改变"):
        cli.ensure_prepared(ensure)
    cli.prepare(prepare)  # Even retrying an old command must expose the new authoritative goal.
    current = cli.ensure_prepared(ensure)
    assert current["ok"] and current["task_basis"]["objective"] == "Scope B"
    assert current["task_basis"]["handoff_scope"] == {"objective": "Scope B", "stop_at": "Stop B"}
    assert evidence.read_bytes() == attestation


@pytest.mark.parametrize("targets,skill", [
    ([], "storyboard-director"),
    ([f"VS-OUT-{number}" for number in range(9)], "storyboard-director"),
    (["VS-SAME", "VS-SAME"], "storyboard-director"),
    ([None], "storyboard-director"),
    ([""], "storyboard-director"),
    (["../output.png"], "storyboard-director"),
    (["AST-CHAR"], "asset-image-assistant"),
    (["AST-CHAR@"], "asset-image-assistant"),
    (["AST-CHAR@view@extra"], "asset-image-assistant"),
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
    if skill == "asset-image-assistant":
        templates = basis["character_layout_references"]
        assert set(templates) == {"大头照", "服装", "人物1x4"}
        assert templates["服装"] == character_layout_reference("服装三视图")
        assert all(row["layout_only"] and Path(row["original"]).is_file() for row in templates.values())
        record_batch_methods(args)
        reading = cli.ensure_prepared(argparse.Namespace(**vars(args), show_task_basis=True))
        assert reading["task_basis"]["character_layout_references"] == templates
        assert reading["task_basis"]["character_prompt_templates"] == character_prompt_templates()
    else:
        assert "character_layout_references" not in basis


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
    with pytest.raises(cli.StageContextError, match="开工决定或阶段请求已改变"):
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


@pytest.mark.parametrize("entry", ["prepare", "handoff"])
def test_asset_worker_receipt_is_bound_and_received_without_changing_project_stage(tmp_path, entry):
    target = "AST-SYNTHETIC@view-a"
    args, request = make_batch_context(tmp_path, skill="asset-image-assistant", targets=[target])
    project = Path(args.project)
    state_path = project / "00_项目管理/project_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(current_artifacts=["REF"], next_stage=args.skill,
        user_confirmations=[{"scope": "artifact", "artifact_ids": ["REF"],
                             "approval_ref": "SYNTHETIC ONLY"}])
    state_path.write_text(json.dumps(state), encoding="utf-8")
    source = project / "source.txt"
    source.write_text("Synthetic source only", encoding="utf-8")
    manifest_path = project / "00_项目管理/artifact_manifest.json"
    manifest_path.write_text(json.dumps({"artifacts": [{"artifact_id": "REF", "logical_id": "reference.test",
        "type": "reference", "revision": "R01", "path_or_url": source.name, "status": "current",
        "user_approved": True, "checksum": cli.digest(source), "depends_on": []}]}), encoding="utf-8")
    state_before, manifest_before = state_path.read_bytes(), manifest_path.read_bytes()
    receipt = request.with_name("visual-self-check.json")
    batch_args = ["handoff", "--project", args.project, "--to-skill", args.skill,
                  "--context-id", args.context_id]
    if entry == "prepare":
        args.vault = None
        args.batch_receipt = str(receipt)
        result = cli.prepare(args)
    else:
        result = cli.handoff(cli.parser().parse_args(batch_args + ["--request-json", str(request),
                                                                 "--batch-receipt", str(receipt)]))
        args.vault = None
        assert cli.prepare(args)["valid"]
        startup = (batch_folder(args) / "stage_startup.md").read_text(encoding="utf-8")
        assert startup.count("- 图片批次依据 `") == 1
        args.batch_receipt = str(receipt.with_name("unbound-receipt.json"))
        with pytest.raises(cli.StageContextError, match="已有图片批次交接不一致"):
            cli.prepare(args)
    assert result["asset_batch"]["targets"] == [target]
    receive = cli.parser().parse_args(batch_args + ["--receive-batch"])
    assert cli.handoff(receive)["status"] == "pending"

    image = project / "candidate.png"
    image.write_bytes(b"Synthetic receipt fixture")
    receipt.write_text(json.dumps({"context_id": args.context_id, "request_sha256": result["asset_batch"]["request_sha256"],
        "full_log": "PRIVATE LOG", "items": [{"target": target,
        "candidate_id": "SYNTHETIC-C1", "output_path": image.name, "sha256": cli.digest(image),
        "review_actor": "worker-a", "review_result": "pass", "observations": "SYNTHETIC OBSERVATION"}]}),
        encoding="utf-8")
    received = cli.handoff(receive)
    assert received["context_id"] == args.context_id
    assert received["status"] == "ready_for_review"
    assert received["items"][0]["path"] == str(image)
    assert set(received["items"][0]) == {"target", "status", "candidate_id", "path", "sha256", "observer", "visual_status"}
    assert state_path.read_bytes() == state_before
    assert manifest_path.read_bytes() == manifest_before
    assert "PRIVATE LOG" not in json.dumps(received)
    with pytest.raises(cli.StageContextError, match="沿用本批交接卡"):
        cli.handoff(cli.parser().parse_args(batch_args + ["--receive-batch", "--batch-receipt", str(receipt)]))
    request.write_text('{"image_targets": ["AST-SYNTHETIC@different"]}', encoding="utf-8")
    with pytest.raises(cli.StageContextError, match="原请求内容已改变"):
        cli.handoff(receive)


def test_reprepared_batch_rejects_old_receipt_of_unchanged_target(tmp_path):
    target = "AST-SYNTHETIC@same-output"
    args, request = make_batch_context(tmp_path, skill="asset-image-assistant", targets=[target])
    project = Path(args.project)
    args.vault = None
    receipt = request.with_name("visual-self-check.json")
    args.batch_receipt = str(receipt)
    first = cli.prepare(args)["asset_batch"]
    image = project / "candidate.png"
    image.write_bytes(b"Synthetic receipt fixture")
    result = {"context_id": args.context_id, "request_sha256": first["request_sha256"], "items": [{"target": target,
        "candidate_id": "C1", "path": image.name, "sha256": cli.digest(image),
        "observer": "worker-a", "visual_status": "passed", "observed": "Synthetic observation"}]}
    receipt.write_text(json.dumps(result), encoding="utf-8")
    receive = cli.parser().parse_args(["handoff", "--project", args.project, "--to-skill", args.skill,
                                      "--context-id", args.context_id, "--receive-batch"])
    assert cli.handoff(receive)["status"] == "ready_for_review"
    request.write_text(json.dumps({"image_targets": [target], "constraint": "changed reference"}), encoding="utf-8")
    second = cli.prepare(args)["asset_batch"]
    assert second["request_sha256"] != first["request_sha256"]
    assert cli.handoff(receive)["status"] == "pending"
    result["request_sha256"] = second["request_sha256"]
    receipt.write_text(json.dumps(result), encoding="utf-8")
    assert cli.handoff(receive)["status"] == "ready_for_review"


def test_asset_request_targets_and_sha_come_from_one_read(tmp_path, monkeypatch):
    target = "AST-SYNTHETIC@view-a"
    args, request = make_batch_context(tmp_path, skill="asset-image-assistant", targets=[target])
    args.vault = None
    args.batch_receipt = str(request.with_name("visual-self-check.json"))
    request = request.resolve()
    expected = cli.digest(request)
    read_bytes = Path.read_bytes
    swapped = False

    def swap_after_read(path):
        nonlocal swapped
        raw = read_bytes(path)
        if path == request and not swapped:
            replacement = path.with_suffix(".new")
            replacement.write_text('{"image_targets": ["AST-SYNTHETIC@view-b"]}', encoding="utf-8")
            replacement.replace(path)
            swapped = True
        return raw

    monkeypatch.setattr(Path, "read_bytes", swap_after_read)
    batch = cli.prepare(args)["asset_batch"]
    assert swapped and batch["targets"] == [target] and batch["request_sha256"] == expected
    receive = cli.parser().parse_args(["handoff", "--project", args.project, "--to-skill", args.skill,
                                      "--context-id", args.context_id, "--receive-batch"])
    with pytest.raises(cli.StageContextError, match="原请求内容已改变"):
        cli.handoff(receive)


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
