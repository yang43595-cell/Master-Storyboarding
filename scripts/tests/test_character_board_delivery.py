from pathlib import Path

import pytest
from PIL import Image

from scripts import chat_asset_review as review
from scripts.core import artifacts
from scripts.core.io import read_json, sha256_json, write_json
from test_review_text_cli import narrative


def test_approved_outfit_replacement_only_recomposes_its_board(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    path, story = narrative(root)
    req = story["asset_requirements"][0]
    ast = "AST-CHAR"
    req.update(asset_id=ast, category="CHARACTER", character_type="HUMAN", character_output_kind="人物1x4",
               image_decision="需要制作", wardrobe_states=[
                   dict(name=key, evidence="synthetic", output_id=key) for key in ("DAY", "NIGHT")])
    write_json(path, story)
    story_entry = dict(artifact_id="story", logical_id="narrative.package", type="narrative.package",
                       checksum=artifacts.digest(path), revision="R01", path_or_url=path.name,
                       user_approved=True, status="current")
    write_json(root / artifacts.MANIFEST_PATH, {"schema_version": "1.0", "artifacts": [story_entry]})
    state = artifacts.load_project_state(root)
    state.update(current_artifacts=["story"], user_confirmations=[dict(artifact_ids=["story"], at="synthetic")])
    write_json(root / "00_项目管理/project_state.json", state)

    def entries():
        return artifacts.load_manifest(root)["artifacts"]

    def confirm(outputs, note):
        rows = []
        for key, kind, final, color in outputs:
            image = root / (key + note + ".png")
            Image.new("RGB", (360, 480) if kind == "大头照" else (640, 480), color).save(image)
            rows.append(dict(target=ast + "@" + key, path=image.name, sha256=artifacts.digest(image),
                             purpose=key, output_kind=kind, candidate_id=key, visual_status="passed",
                             final_output_id=final, requirement_sha256=sha256_json(req)))
        packet = dict(narrative=story_entry["checksum"], narrative_id="story", items=rows)
        key = sha256_json(packet)
        write_json(root / review.SHOWN / (key + ".json"), packet)
        review.confirm(root, [key], dict(at="2026-11-22T12:00:00+08:00", quote="synthetic", scope="all_shown",
                                         review_ref=note, shown=[key]), story_entry, entries())

    def boards(checked=None, specs=()):
        if checked:
            return review.confirm(root, [checked], dict(at="2026-11-22T13:00:00+08:00", quote="拼版通过",
                                  scope="all_shown", review_ref=checked, shown=[checked]), story_entry, entries())
        return review.compose_boards(root, specs, story_entry, entries())

    confirm([("FACE", "大头照", None, "red"), ("JACKET", "服装", "DAY", "blue"),
             ("COAT", "服装三视图", "NIGHT", "green")], "first")
    preview = boards()
    assert preview["count"] == 2 and not preview["pending"]
    day = next(o for o in preview["items"] if o["target"] == ast + "@DAY")
    with Image.open(root / day["path"]) as im:
        assert im.size == (1000, 480)
        assert im.getpixel((359, 250)) == (255, 0, 0) and im.getpixel((360, 250)) == (0, 0, 255)
    assert not any(e["artifact_id"].startswith("visual-boards-") for e in entries())
    with monkeypatch.context() as patch:
        def fail_after_promotion(*args):
            raise OSError("synthetic interruption before project state update")
        patch.setattr(review, "select_export", fail_after_promotion)
        with pytest.raises(OSError, match="interruption"):
            boards(preview["shown"])
    adopted = boards(preview["shown"])
    assert boards(preview["shown"])["artifact_id"] == adopted["artifact_id"]
    assert set(boards()["reused"]) == {ast + "@DAY", ast + "@NIGHT"}
    confirm([("JACKET", "服装", "DAY", "yellow")], "changed")
    active = read_json(root / review.current(entries(), review.LOGICAL)["path_or_url"])
    outputs = {o["output_id"] for o in active["assets"][0]["confirmed_outputs"]}
    assert "DAY" not in outputs and "NIGHT" in outputs
    assert [g["requested_item"] for g in active["scope_gaps"]] == ["DAY"]
    assert boards(preview["shown"])["artifact_id"] == adopted["artifact_id"]
    with pytest.raises(ValueError, match="服装确认"):
        boards(specs=[(ast + "@DAY", "FACE", "COAT")])
    new = boards()
    assert [r["target"].split("@")[1] for r in new["items"]] == ["DAY"] and new["reused"] == [ast + "@NIGHT"]
    picture = root / new["items"][0]["path"]
    raw = picture.read_bytes()
    picture.write_bytes(raw + b"changed")
    with pytest.raises(ValueError, match="字节已变化"):
        boards(new["shown"])
    picture.write_bytes(raw)
    last = boards(new["shown"])
    assert not read_json(Path(last["manifest"]))["scope_gaps"]
    confirm([("FACE", "大头照", None, "black")], "new identity photo")
    assert {r["target"].split("@")[1] for r in boards()["items"]} == {"DAY", "NIGHT"}


def test_matted_headshot_and_wide_outfit_keep_headshot_full_bleed_left(tmp_path):
    root = tmp_path.resolve()
    path, story = narrative(root)
    req = story["asset_requirements"][0]
    ast = "AST-CHAR"
    req.update(asset_id=ast, category="CHARACTER", character_type="HUMAN", character_output_kind="人物1x4",
               image_decision="需要制作", wardrobe_states=[dict(name="UNIFORM", evidence="synthetic", output_id="UNIFORM")])
    write_json(path, story)
    story_entry = dict(artifact_id="story", logical_id="narrative.package", type="narrative.package",
                       checksum=artifacts.digest(path), revision="R01", path_or_url=path.name,
                       user_approved=True, status="current")
    write_json(root / artifacts.MANIFEST_PATH, {"schema_version": "1.0", "artifacts": [story_entry]})
    state = artifacts.load_project_state(root)
    state.update(current_artifacts=["story"], user_confirmations=[dict(artifact_ids=["story"], at="synthetic")])
    write_json(root / "00_项目管理/project_state.json", state)

    head = Image.new("RGB", (1200, 1600), (250, 250, 250))
    head.paste(Image.new("RGB", (864, 1152), (180, 140, 130)), (168, 224))
    head_path = root / "head.png"
    head.save(head_path)
    outfit_path = root / "outfit.png"
    Image.new("RGB", (1672, 941), (255, 255, 255)).save(outfit_path)

    rows = [
        dict(target=f"{ast}@HEAD", path=head_path.name, sha256=artifacts.digest(head_path),
             purpose="head", output_kind="大头照", candidate_id="H1", visual_status="passed",
             requirement_sha256=sha256_json(req)),
        dict(target=f"{ast}@CLOTHES", path=outfit_path.name, sha256=artifacts.digest(outfit_path),
             purpose="clothes", output_kind="服装三视图", candidate_id="C1", visual_status="passed",
             final_output_id="UNIFORM", requirement_sha256=sha256_json(req)),
    ]
    packet = dict(narrative=story_entry["checksum"], narrative_id="story", items=rows)
    key = sha256_json(packet)
    write_json(root / review.SHOWN / (key + ".json"), packet)
    entries = lambda: artifacts.load_manifest(root)["artifacts"]
    review.confirm(root, [key], dict(at="2026-11-26T22:25:00+08:00", quote="synthetic", scope="all_shown",
                                     review_ref="matted", shown=[key]), story_entry, entries())

    preview = review.compose_boards(root, (), story_entry, entries())
    with Image.open(root / preview["items"][0]["path"]) as board:
        assert board.width / board.height < 2.5
        assert board.getpixel((0, 0)) == (180, 140, 130)
        assert board.getpixel((0, board.height - 1)) == (180, 140, 130)
