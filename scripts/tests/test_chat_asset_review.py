from pathlib import Path

import pytest
from PIL import Image

from scripts import chat_asset_review as review
from scripts.core import artifacts
from scripts.core.io import read_json, sha256_json, write_json
from test_review_text_cli import narrative


def test_incremental_confirmation_checks_bytes_and_does_not_reactivate_earlier_batch(tmp_path):
    project = tmp_path.resolve()
    path, story = narrative(project)
    requirement = story["asset_requirements"][0]
    requirement["image_decision"] = "需要制作"
    write_json(path, story)
    upstream = dict(
        artifact_id="story", checksum=artifacts.digest(path), revision="R01", path_or_url=path.name
    )
    keys = []
    for output in ("PAPER-A", "PAPER-B"):
        image = project / (output + ".png")
        Image.new("RGB", (5, 5)).save(image)
        item = dict(
            target="AST-PROP-PAPER@" + output,
            path=image.name,
            sha256=artifacts.digest(image),
            purpose="纸张外观",
            output_kind="道具",
            candidate_id=output,
            visual_status="failed",
            issue="边角裁切",
            requirement_sha256=sha256_json(
                review.effective_requirements(project, upstream)["AST-PROP-PAPER"]
            ),
        )
        packet = dict(narrative=upstream["checksum"], narrative_id="story", items=[item])
        keys.append(sha256_json(packet))
        write_json(project / review.SHOWN / (keys[-1] + ".json"), packet)

    def confirm(key, ref):
        entries = artifacts.load_manifest(project)["artifacts"]
        message = dict(at="2026-11-22T12:00:00+08:00", quote="全部通过", scope="all_shown", review_ref=ref)
        return review.confirm(project, [key], message, upstream, entries)

    first = confirm(keys[0], "synthetic-1")
    with pytest.raises(ValueError, match="同一条用户消息"):
        confirm(keys[1], "synthetic-1")
    original = image.read_bytes()
    image.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="字节已变化"):
        confirm(keys[1], "synthetic-2")
    image.write_bytes(original)
    second = confirm(keys[1], "synthetic-2")
    assert confirm(keys[0], "synthetic-1") == first
    assert artifacts.load_project_state(project)["current_artifacts"] == [second["artifact_id"]]
    asset = read_json(Path(second["manifest"]))["assets"][0]
    assert [o["output_id"] for o in asset["confirmed_outputs"]] == ["PAPER-A", "PAPER-B"]
    assert all(
        review.asset_refs._confirmed_output(project, asset["asset_id"], o) for o in asset["confirmed_outputs"]
    )
