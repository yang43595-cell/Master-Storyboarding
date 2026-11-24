"""End-to-end synthetic fixtures for mechanically derived character boards."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

from scripts import chat_asset_review as review
from scripts.core import artifacts
from scripts.core.io import read_json, sha256_json, write_json
from test_review_text_cli import narrative


AST = "AST-TEST"
FINALS = ["OUTFIT-A-FINAL", "OUTFIT-B-FINAL"]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def approved(tmp_path):
    project = tmp_path
    path, story = narrative(project)
    req = story["asset_requirements"][0]
    req.update(asset_id=AST, category="CHARACTER", image_decision="需要制作",
               name="合成人物", character_type="HUMAN", character_output_kind="人物1x4",
               wardrobe_states=[dict(name=f"合成服装 {idx}", output_id=final, evidence="合成测试")
                                for idx, final in enumerate(FINALS)])
    write_json(path, story)
    upstream = dict(artifact_id="story", logical_id="narrative.package", type="narrative.package",
                    checksum=artifacts.digest(path), revision="R01", path_or_url=path.name,
                    status="current", user_approved=True)
    write_json(project / artifacts.MANIFEST_PATH, dict(schema_version="1.0", artifacts=[upstream]))
    state = artifacts.load_project_state(project)
    state["current_artifacts"] = ["story"]
    state["user_confirmations"] = [dict(artifact_ids=["story"], scope="artifact")]
    review.write_project_state(project, state)

    head = project / "head.png"
    Image.new("RGB", (6, 8), "cyan").save(head)
    specs = [("HEAD", head, "大头照", None)]
    for idx, color in enumerate(("red", "blue")):
        clothes = project / f"clothing-{idx}.png"
        Image.new("RGB", (16, 12), color).save(clothes)
        specs.append((f"CLOTHING-{idx}", clothes, "服装三视图", FINALS[idx]))
    rows = [dict(target=f"{AST}@{oid}", path=p.name, sha256=artifacts.digest(p),
                 purpose="头照或服装用于机械拼版", output_kind=kind, candidate_id=oid,
                 visual_status="passed", final_output_id=final,
                 requirement_sha256=sha256_json(review.effective_requirements(project, upstream)[AST]))
            for oid, p, kind, final in specs]
    packet = dict(narrative=upstream["checksum"], narrative_id="story", items=rows)
    key = sha256_json(packet)
    write_json(project / review.SHOWN / (key + ".json"), packet)
    before = review.confirm(project, [key],
        dict(at="2026-11-22T12:00:00+08:00", quote="合成示例通过",
             scope="all_shown", review_ref="synthetic-board-approval"),
        upstream, artifacts.load_manifest(project)["artifacts"])
    selected = {o["output_id"]: o for o in read_json(Path(before["manifest"]))["assets"][0]["confirmed_outputs"]}
    items = []
    for idx, final in enumerate(FINALS):
        board = project / (final + ".png")
        with Image.open(head) as portrait, Image.open(specs[idx+1][1]) as outfit:
            result = Image.new("RGB", (25, 12))
            result.paste(portrait.resize((9, 12)), (0, 0))
            result.paste(outfit, (9, 0))
            result.save(board)
        copies = [selected["HEAD"], selected[f"CLOTHING-{idx}"]]
        items.append(dict(target=f"{AST}@{final}", path=board.name, sha256=artifacts.digest(board),
            sources=[dict(output_id=out["output_id"], path=out["path"],
                          sha256=artifacts.digest(project / out["path"]),
                          confirmation_ref=out["user_confirmation_ref"]) for out in copies]))
    return project, dict(narrative="story", items=items)


def cli(project, plan):
    write_json(project / "board-receipt.json", plan)
    return subprocess.run([sys.executable, "-m", "scripts.chat_asset_review", "--project", str(project),
                           "register-derived-boards", "--board-receipt", "board-receipt.json"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")


def board_gate(project, finals=FINALS):
    return subprocess.run([sys.executable, "-m", "scripts.core.asset_refs", "--project", str(project),
                           "--require-final-image", *(f"{AST}@{out}" for out in finals)], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")


def test_register_two_boards_through_cli_and_verify_consumption(approved):
    project, plan = approved
    assert board_gate(project).returncode != 0
    before = artifacts.load_project_state(project)["user_confirmations"]
    outcome = cli(project, plan)
    assert outcome.returncode == 0, outcome.stderr
    result = json.loads(outcome.stdout)
    assert result["count"] == 2
    gate = board_gate(project)
    assert gate.returncode == 0, (gate.stdout, gate.stderr)
    manifest = read_json(Path(result["manifest"]))
    assert not manifest["scope_gaps"]
    current = review.current(artifacts.load_manifest(project)["artifacts"], review.LOGICAL)
    assert current["artifact_id"] == result["artifact_id"] and current["user_approved"] is False
    assert artifacts.load_project_state(project)["user_confirmations"] == before
    derivations = read_json(project / current["confirmation_ref"])
    assert [len(record["confirmation"]["derivation"]["sources"]) for record in derivations["items"]] == [2, 2]
    assert json.loads(cli(project, plan).stdout)["count"] == 0
    # An edited approved copy is not valid for dependent boards, even when the board bytes
    # and its own receipt are unchanged. The independent outfit stays usable.
    clothing = project / plan["items"][0]["sources"][1]["path"]
    clothing.write_bytes(clothing.read_bytes() + b"edited")
    assert board_gate(project, FINALS[:1]).returncode != 0
    assert board_gate(project, FINALS[1:]).returncode == 0


@pytest.mark.parametrize("defect", ["discarded-pixel", "swapped-outfit", "tampered-receipt", "stale-narrative"])
def test_wrong_supporting_data_cannot_partially_register(approved, defect):
    project, plan = approved
    asset_entries = artifacts.load_manifest(project)
    item = plan["items"][1]  # First board is valid; neither may be registered.
    if defect == "discarded-pixel":
        p = project / item["path"]
        with Image.open(p) as im:
            altered = im.copy()
        altered.putpixel((2, 1), (255, 0, 127))
        altered.save(p)
        item["sha256"] = artifacts.digest(p)
    elif defect == "swapped-outfit":
        item["sources"][1] = plan["items"][0]["sources"][1]
        item["path"] = plan["items"][0]["path"]
        item["sha256"] = plan["items"][0]["sha256"]
    elif defect == "tampered-receipt":
        item["sources"][1]["confirmation_ref"] = item["sources"][0]["confirmation_ref"]
    else:
        plan["narrative"] = "older"
    outcome = cli(project, plan)
    assert outcome.returncode != 0, outcome.stdout
    assert artifacts.load_manifest(project) == asset_entries
    assert board_gate(project, FINALS[:1]).returncode != 0


def test_carried_forward_board_remains_bound_to_original_receipt_hash(approved):
    project, plan = approved
    first = dict(plan, items=plan["items"][:1])
    second = dict(plan, items=plan["items"][1:])
    result = cli(project, first)
    assert result.returncode == 0, result.stderr
    first_entry = review.current(artifacts.load_manifest(project)["artifacts"], review.LOGICAL)
    result = cli(project, second)
    assert result.returncode == 0, result.stderr
    assert board_gate(project).returncode == 0
    old_receipt_path = project / first_entry["confirmation_ref"]
    old_receipt = read_json(old_receipt_path)
    old_board = old_receipt["items"][0]["confirmation"]
    board_path = project / old_board["path"]
    Image.new("RGB", (25, 12), "green").save(board_path)
    old_board["image_sha256"] = artifacts.digest(board_path)
    write_json(old_receipt_path, old_receipt)
    assert board_gate(project, FINALS[:1]).returncode != 0
    assert board_gate(project, FINALS[1:]).returncode == 0
