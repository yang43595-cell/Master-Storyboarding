from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIVE_PROJECT = Path("/Users/liuyang/Desktop/公主护肤日_流程实测_20261126")


def test_video_prompt_cli_help_and_redframe_write_creative_help() -> None:
    r1 = subprocess.run(
        [sys.executable, str(REPO / "scripts/video_prompt_package.py"), "--help"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    assert "put-segment" in r1.stdout
    assert "self-check-template" in r1.stdout

    r2 = subprocess.run(
        [sys.executable, str(REPO / "scripts/redframe_draft.py"), "write-creative", "--help"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    assert "--preflight" in r2.stdout


def test_v2_video_prompt_skips_director_coverage_requirement(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from core.generation_boundaries import validate_generation_boundaries

    import hashlib
    import pytest

    cap_rel = "06_视频/cap.json"
    cap_file = tmp_path / cap_rel
    cap_file.parent.mkdir(parents=True, exist_ok=True)
    cap_bytes = json.dumps({
        "captured_at": "2026-11-27T00:00:00+08:00",
        "model": {
            "type": "video",
            "providerId": "volcengineArkVideo",
            "modelId": "doubao-seedance-2-5-260628",
            "label": "Doubao Seedance 2.5",
            "mode": [["imageReference:30", "videoReference:10"]],
            "durationResolutionMap": [{"duration": list(range(4, 31)), "resolution": ["480p"]}],
        },
    }).encode("utf-8")
    cap_file.write_bytes(cap_bytes)

    storyboard = {
        "schema_version": "5.0",
        "project_id": "test-proj",
        "revision": "R01",
        "scenes": [{
            "scene_id": "S01",
            "shots": [{
                "shot_id": "S01-001",
                "duration_seconds": 5,
                "action": "公主转身看向梳妆镜。",
                "cut": {"exit": "动作落定", "handoff": "切下一镜"},
                "audio": {"dialogue": []},
            }],
        }],
    }
    payload_v2 = {
        "schema_version": "2.0",
        "kind": "video.prompt",
        "project_id": "test-proj",
        "revision": "R01",
        "source_storyboard": {"artifact_id": "DIR-R01", "revision": "R01", "path": "03_导演/storyboard-package.json"},
        "selected_video_model": {
            "id": "Seedance 2.5", "platform": "redframe", "entry_point": "redframe_canvas",
            "reference_mode": "reference", "confirmed_by_user": True,
            "min_duration_seconds": 4, "max_duration_seconds": 30,
            "capability_evidence": {
                "path": cap_rel,
                "sha256": hashlib.sha256(cap_bytes).hexdigest(),
                "locator": "model.durationResolutionMap",
                "checked_at": "2026-11-27T00:00:00+08:00",
            },
        },
        "scope": {
            "type": "full_local_authored",
            "authored_segment_count": 1,
            "authored_shot_count": 1,
            "scope_seconds": 5,
            "unwritten_shot_ids": [],
        },
        "generation_segments": [{
            "segment_id": "SEG-01",
            "title": "开场",
            "source_shots": ["S01-001"],
            "duration_seconds": 5,
            "shot_durations": [5],
            "target_edit_duration_seconds": 5,
            "edit_in_seconds": 0,
            "edit_out_seconds": 5,
            "parameters": {"model": "Seedance 2.5", "aspect_ratio": "9:16", "resolution": "480p", "duration_seconds": 5, "reference_mode": "reference"},
            "source_audio": [{"shot_id": "S01-001", "dialogue": []}],
            "materials": [],
            "prompt": "竖屏近景，王室梳妆室内公主转身看向镜面，晨光柔和洒在浅粉睡袍肩头。",
        }],
    }
    res_v2 = validate_generation_boundaries(payload_v2, storyboard, tmp_path, prompt_content_segment_ids=["SEG-01"])
    assert isinstance(res_v2, dict)

    payload_v1 = {**payload_v2, "schema_version": "1.0"}
    with pytest.raises(ValueError, match="director_coverage"):
        validate_generation_boundaries(payload_v1, storyboard, tmp_path, prompt_content_segment_ids=["SEG-01"])


def test_live_project_outline_if_present() -> None:
    if not LIVE_PROJECT.is_dir():
        return
    env = {"PYTHONPATH": str(REPO / "scripts")}
    res = subprocess.run(
        [sys.executable, str(REPO / "scripts/video_prompt_package.py"), "outline", "--project", str(LIVE_PROJECT)],
        cwd=REPO, env={**dict(__import__("os").environ), **env}, capture_output=True, text=True, check=True,
    )
    data = json.loads(res.stdout)
    assert data["storyboard_revision"].startswith("R")
    assert data["total_shots"] == 47
    assert "AST-CH-01" in data["ready_assets"]
    assert data["scenes"][0]["total_seconds"] > 0


def test_put_segment_validates_audio_durations_and_required_visuals(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import argparse
    import video_prompt_package as vpp

    proj = tmp_path / "proj"
    cap_rel = "06_视频/cap.json"
    cap_file = proj / cap_rel
    cap_file.parent.mkdir(parents=True, exist_ok=True)
    cap_file.write_text(json.dumps({
        "captured_at": "2026-11-27T00:00:00+08:00",
        "model": {
            "type": "video",
            "providerId": "volcengineArkVideo",
            "modelId": "doubao-seedance-2-5-260628",
            "label": "Doubao Seedance 2.5",
            "mode": [["imageReference:30", "videoReference:10"]],
            "durationResolutionMap": [{"duration": list(range(4, 31)), "resolution": ["480p"]}],
        },
    }), encoding="utf-8")
    shot = {
        "shot_id": "S01-001",
        "rhythm": "约6秒｜开场平稳",
        "action": "公主接过手镜看向镜面。",
        "cut": {"exit": "镜面落稳", "handoff": "切下一镜"},
        "audio": {"dialogue": [{"dialogue_id": "DLG-001", "speaker_id": "PRINCESS", "text": "早上好呀。"}]},
        "required_visuals": [{"prompt_clause": "粉金手镜镜面清晰映出公主正脸"}],
    }
    scene = {"scene_id": "S01", "shots": [shot]}
    storyboard = {"schema_version": "5.0", "project_id": "test-proj", "revision": "R07", "scenes": [scene]}
    fake_ctx = {
        "project": proj,
        "manifest": {"project_id": "test-proj", "artifacts": []},
        "kickoff": {"canvas": {"model": "Seedance 2.5", "platform": "redframe", "aspect_ratio": "9:16", "resolution": "480p"}},
        "dir_entry": {"artifact_id": "DIR-R07", "revision": "R07", "path_or_url": "03_导演/R07.json", "checksum": "a" * 64},
        "storyboard": storyboard,
        "narrative": {"asset_requirements": []},
        "ready_assets": {},
        "supplements": {},
        "product_pack": None,
        "real_product_asset_id": "AST-RP-01",
        "shots_ordered": [shot],
        "shot_by_id": {"S01-001": shot},
        "shot_scene": {"S01-001": scene},
    }
    monkeypatch.setattr(vpp, "_context", lambda _p: fake_ctx)
    vpp.cmd_init(argparse.Namespace(
        project=str(proj), revision="R07", output="06_视频/R07.json",
        capability_evidence=cap_rel, capability_locator="model.durationResolutionMap",
        min_duration=4, max_duration=30,
    ))
    seg_in = proj / "seg01.json"
    seg_in.write_text(json.dumps({
        "segment_id": "SEG-01",
        "title": "开场",
        "source_shots": ["S01-001"],
        "duration_seconds": 6,
        "materials": [],
        "prompt": "竖屏近景，公主接过手镜。\n粉金手镜镜面清晰映出公主正脸\n无背景音乐，不叠加任何字幕。",
    }, ensure_ascii=False), encoding="utf-8")
    assert vpp.cmd_put_segment(argparse.Namespace(
        project=str(proj), package="06_视频/R07.json", input=str(seg_in),
    )) == 0
    saved = json.loads((proj / "06_视频/R07.json").read_text(encoding="utf-8"))
    assert saved["scope"]["type"] == "full_local_authored"
    assert saved["generation_segments"][0]["source_audio"][0]["dialogue"][0]["text"] == "早上好呀。"


def test_creative_linked_v2_readback_marks_canvas_complete(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from core.draft_delivery import _binding, read_draft_delivery
    from core.io import sha256_json, write_json

    canvas = {
        "target_url": "https://redframe-ai.xmpaohong.com:28888/#/workspace?canvasId=6047af24-d2d1-4640-95cb-cd3ba2e0aaf3",
        "target_project_id": "newProjectb5f36d91fa2c493c87164bfc935b9a33",
    }
    entry = {"artifact_id": "VID-R07", "revision": "R07", "checksum": "4" * 64}
    rows = [{"source_segment_id": "SEG-01", "node_id": "67e7d057-13ec-493a-8f8e-b6f6d5108f02"}]
    frozen = {
        "mode": "creative_linked_v2",
        "revision": "R07",
        **canvas,
        "source_artifacts": [{"artifact_id": "VID-R07", "logical_id": "video.prompt", "revision": "R07", "checksum": "4" * 64}],
        "drafts": rows,
    }
    frozen_sha = sha256_json(frozen)
    observed = {"mode": "creative_linked_v2", "phase": "after_refresh", "revision": "R07", **canvas, "frozen_sha256": frozen_sha, "drafts": rows}
    observed_sha = sha256_json(observed)
    report = {"ok": True, "mode": "creative_linked_v2", "native_refresh_binding": "selected_fields_match", "revision": "R07", "draft_count": 1, "frozen_sha256": frozen_sha, "observed_sha256": observed_sha}
    rb_rel = "06_视频/草稿回读/creative-test"
    for name, doc in (("frozen", frozen), ("observed", observed), ("formal_readback", report)):
        write_json(tmp_path / rb_rel / f"{name}.json", doc)
    binding = _binding(canvas, entry, "SEG-01")
    write_json(tmp_path / "06_视频/画布交付回执.json", {
        **canvas,
        "draft_receipts": {
            sha256_json(binding): {
                "binding": binding,
                "node_id": rows[0]["node_id"],
                "phase": "readback_complete",
                "plan_sha256": frozen_sha,
                "readback_path": rb_rel,
            }
        },
    })
    res = read_draft_delivery(tmp_path, canvas, entry, ["SEG-01"])
    assert res["SEG-01"]["status"] == "complete"




def test_visual_stage_cli_help_and_live_outline() -> None:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/visual_stage_cli.py"), "--help"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    assert "init-dialogue-evidence" in r.stdout
    assert "capture-redframe-capability" in r.stdout
    if LIVE_PROJECT.is_dir():
        env = {**dict(__import__("os").environ), "PYTHONPATH": str(REPO / "scripts")}
        res = subprocess.run(
            [sys.executable, str(REPO / "scripts/visual_stage_cli.py"), "outline", "--project", str(LIVE_PROJECT)],
            cwd=REPO, env=env, capture_output=True, text=True, check=True,
        )
        data = json.loads(res.stdout)
        assert data["storyboard"].startswith("DIR-R")
        assert len(data["scenes"]) == 11


def test_video_prompt_reconciliation_and_offer_typo_gate(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import argparse
    import hashlib
    import pytest
    import video_prompt_package as vpp
    from core import revision_control as rc

    proj = tmp_path / "proj"
    (proj / "06_视频").mkdir(parents=True)
    old_pkg = {
        "schema_version": "2.0", "kind": "video.prompt", "revision": "R01",
        "generation_segments": [{
            "segment_id": "SEG-01", "source_shots": ["S01-001"], "duration_seconds": 5,
            "prompt": "竖屏近景，公主端详手镜。",
        }],
    }
    old_raw = (json.dumps(old_pkg, ensure_ascii=False) + "\n").encode("utf-8")
    (proj / "06_视频/R01.json").write_bytes(old_raw)
    old_sha = hashlib.sha256(old_raw).hexdigest()
    manifest = {"artifacts": [{
        "artifact_id": "VID-R01", "logical_id": "video.prompt", "type": "video.prompt",
        "status": "stale", "path_or_url": "06_视频/R01.json", "checksum": old_sha,
    }]}
    new_pkg = {
        "schema_version": "2.0", "kind": "video.prompt", "revision": "R02",
        "generation_segments": [{
            "segment_id": "SEG-01", "source_shots": ["S01-001"], "duration_seconds": 5,
            "prompt": "竖屏近景，公主昂首下达皇家抛光护理指令。",
        }],
    }
    vpp._sync_reconciliation(proj, manifest, new_pkg, "R02 台词微调")
    assert new_pkg["segment_reconciliation"]["baseline"] == {"artifact_id": "VID-R01", "sha256": old_sha}
    rc._validate_segment_reconciliation(proj, manifest["artifacts"][0], new_pkg)

    shot = {"shot_id": "S01-001", "duration_seconds": 5, "action": "收口。", "cut": {"exit": "收", "handoff": "完"}, "audio": {"dialogue": []}}
    monkeypatch.setattr(vpp, "_context", lambda _p: {
        "project": proj, "manifest": manifest,
        "kickoff": {"product": {"product_id": "creator-polishing-mask-2"}, "canvas": {"model": "Seedance 2.5", "platform": "redframe"}},
        "dir_entry": {"artifact_id": "DIR-R02", "revision": "R02", "path_or_url": "03_导演/R02.json", "checksum": "b" * 64},
        "storyboard": {"scenes": [{"scene_id": "S01", "shots": [shot]}]}, "narrative": {"asset_requirements": []},
        "ready_assets": {}, "supplements": {}, "product_pack": None, "real_product_asset_id": "AST-RP-01",
        "shots_ordered": [shot], "shot_by_id": {"S01-001": shot}, "shot_scene": {"S01-001": {"scene_id": "S01"}},
    })
    (proj / "06_视频/R02.json").write_text(json.dumps(new_pkg, ensure_ascii=False), encoding="utf-8")
    bad_in = proj / "bad_seg.json"
    bad_in.write_text(json.dumps({"segment_id": "SEG-01", "source_shots": ["S01-001"], "duration_seconds": 5, "materials": [], "prompt": "贴身女仆说：“还送高定调膜碗刷！”"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="还送高定条膜碗刷"):
        vpp.cmd_put_segment(argparse.Namespace(project=str(proj), package="06_视频/R02.json", input=str(bad_in)))
