from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state


def test_new_project_routes_initial_creative_and_blocks_early_director(tmp_path):
    write_project_state(tmp_path, new_project_state(current_stage="creative-director"))
    write_json(tmp_path / cli.REQUEST_PATH, {
        "project_mode": "new",
        "kickoff": {"decision_ref": "fixture:kickoff", "product": {"mode": "none"}},
    })
    command = ["handoff", "--project", str(tmp_path)]
    first = cli.handoff(cli.parser().parse_args(command))
    action, = first["next_actions"]
    assert action["skill"] == "creative-director"
    handoff_path = Path(action["handoff_path"])
    before = handoff_path.stat().st_mtime_ns
    assert cli.handoff(cli.parser().parse_args(command)) == first
    assert handoff_path.stat().st_mtime_ns == before

    with pytest.raises(ValueError, match="director 当前未就绪"):
        cli.handoff(cli.parser().parse_args([*command, "--to-skill", "storyboard-planner"]))
    prepare = ["prepare", "--project", str(tmp_path), "--objective", "编导开工"]
    with pytest.raises(ValueError, match="director 当前未就绪"):
        cli.prepare(cli.parser().parse_args([*prepare, "--skill", "storyboard-planner"]))
    result = cli.prepare(cli.parser().parse_args([
        *prepare, "--skill", "creative-director", "--context-id", action["context_id"],
    ]))
    assert result["valid"]
    assert "程序交接范围" in Path(result["path"]).read_text(encoding="utf-8")


def test_routed_transactions_wait_on_reads_and_recover_before_dispatch(tmp_path):
    from scripts.core import revision_control
    from scripts.core.io import read_json
    from test_formal_package_submission import confirmed_narrative
    from test_production_handoff import authorize, kickoff

    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    authorize(tmp_path, "storyboard-planner")
    project_args = ["--project", str(tmp_path)]

    def command(name, *options):
        return cli.parser().parse_args([name, *project_args, *options])

    ready = cli.status(command("status"))
    assert {action["action"] for action in ready["next_actions"]} == {"assets", "director"}
    assert ready["production_allowed"] and ready["next_allowed_stage"] is None
    assert not cli._handoff_path(tmp_path).exists()

    request_path = tmp_path / cli.REQUEST_PATH
    request = read_json(request_path)
    request["recovery_fixture"] = "same project after transaction"
    with pytest.raises(RuntimeError, match="transaction interrupted"):
        revision_control._commit_json_transaction_unlocked(tmp_path, {
            request_path: request, tmp_path / "00_项目管理/second.json": {"recovered": True},
        }, reason="synthetic interrupted revision", transaction_id="interrupted", fail_after=1)

    journal = tmp_path / revision_control.TRANSACTION_ROOT / "interrupted"
    assert not (journal / "result.json").exists()
    assert not cli.status(command("status"))["next_actions"]
    with pytest.raises(cli.StageContextError, match="已有 commit intent 但无 result"):
        cli.prepare(command("prepare", "--skill", "storyboard-planner", "--objective", "合成导演准备"))
    assert not (journal / "result.json").exists()

    # A decided, recoverable transaction rolls forward before a writer computes the route.
    action = cli.handoff(command("handoff", "--to-skill", "storyboard-planner"))
    assert action["artifact_ids"] == ["N"]
    assert read_json(journal / "result.json")["state"] == "committed"
    assert read_json(tmp_path / "00_项目管理/second.json") == {"recovered": True}

    lost = tmp_path / revision_control.TRANSACTION_ROOT / "missing-original"
    write_json(lost / "commit_intent.json", {"files": []})
    assert cli.status(command("status"))["next_actions"] == []
    assert cli.handoff(command("handoff"))["next_actions"] == []
    with pytest.raises(cli.StageContextError, match="transaction.json 缺失"):
        cli.handoff(command("handoff", "--to-skill", "storyboard-planner"))
    with pytest.raises(cli.StageContextError, match="transaction.json 缺失"):
        cli.prepare(command("prepare", "--skill", "storyboard-planner", "--objective", "合成导演准备"))


def test_prepared_visual_subset_survives_the_unselected_shot_progress(tmp_path):
    from scripts.core import artifacts
    from scripts.core.io import read_json
    from test_formal_package_submission import video_project
    from test_production_handoff import kickoff
    from test_visual_readiness import write_request

    kickoff(tmp_path)
    video_project(tmp_path, current_prompt=False)
    board_path = tmp_path / "B.json"
    board = read_json(board_path)
    for scene in board["scenes"]:
        for shot in scene["shots"]:
            shot["source_asset_requirement_ids"] = []
    write_json(board_path, board)
    manifest = artifacts.load_manifest(tmp_path)
    director = next(row for row in manifest["artifacts"] if row["artifact_id"] == "B")
    director["checksum"] = artifacts.digest(board_path)
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)

    command = ["handoff", "--project", str(tmp_path)]
    first = cli.handoff(cli.parser().parse_args(command))
    visual = next(action for action in first["next_actions"] if action["action"] == "visual")
    assert set(visual["shot_ids"]) == {"S01-010", "S02-010"}
    request = tmp_path / "one-shot.json"
    write_json(request, {"source_shot_ids": ["S02-010"]})
    task_args = ["--project", str(tmp_path), "--skill", "storyboard-director",
                 "--context-id", visual["context_id"], "--actor-id", "worker-a", "--task-id", "task-a",
                 "--request-json", str(request)]
    prepared = cli.prepare(cli.parser().parse_args(["prepare", *task_args, "--objective", "检查第二镜"]))
    basis = read_json(Path(prepared["path"]).with_name("task_basis.json"))
    assert basis["handoff_scope"]["routed"]["shot_ids"] == ["S02-010"]

    write_request(tmp_path, director, ["S01-010"], decision="no_supplement")
    refreshed = cli.handoff(cli.parser().parse_args(command))
    assert next(action for action in refreshed["next_actions"] if action["action"] == "visual")["shot_ids"] == ["S02-010"]
    methods = [piece for row in basis["reading_manifest"]["methods"] for piece in ("--method", row["id"])]
    ensure = task_args[:-2]
    assert cli.ensure_prepared(cli.parser().parse_args(["ensure-prepared", *ensure, "--mark-read", *methods]))["ok"]

    write_request(tmp_path, director, ["S02-010"], request_id="VS-SECOND-DECISION", decision="no_supplement")
    with pytest.raises(cli.StageContextError, match="visual 当前未就绪"):
        cli.ensure_prepared(cli.parser().parse_args(["ensure-prepared", *ensure]))


def test_routed_preparation_binds_declared_project_source(tmp_path):
    from scripts.core.io import read_json
    from test_formal_package_submission import confirmed_narrative
    from test_production_handoff import authorize, kickoff

    kickoff(tmp_path)
    confirmed_narrative(tmp_path)
    authorize(tmp_path, "storyboard-planner")
    path = tmp_path / cli.REQUEST_PATH
    request = read_json(path)
    request["kickoff"]["creation"] = {"workflow": "depth-guided-recreation"}
    request["source_files"] = ["source.mp4"]
    write_json(path, request)
    route_command = cli.parser().parse_args(["handoff", "--project", str(tmp_path)])
    missing = cli.handoff(route_command)
    assert all(item["action"] != "director" for item in missing["next_actions"])
    assert any(item["action"] == "director" and "source.mp4" in item["reason"] for item in missing["steps"])
    (tmp_path / "source.mp4").write_bytes(b"synthetic original bytes")
    handoff = cli.handoff(route_command)
    action = next(item for item in handoff["next_actions"] if item["action"] == "director")
    command = ["prepare", "--project", str(tmp_path), "--skill", "storyboard-planner",
               "--objective", "复刻导演准备", "--context-id", action["context_id"]]
    result = cli.prepare(cli.parser().parse_args(command))
    basis = read_json(Path(result["path"]).with_name("task_basis.json"))
    assert basis["input_versions"]["source_files"] == ["source.mp4"]
