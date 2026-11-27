"""Tests for automatic must_read guidance and concurrent multi-act parallel writing."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIVE_PROJECT = Path("/Users/liuyang/Desktop/公主护肤日_流程实测_20261126")


def test_stage_guidance_surfaces_core_method_and_product_docs() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from core.stage_guidance import build_stage_guidance

    target = LIVE_PROJECT if LIVE_PROJECT.is_dir() else REPO
    for skill in (
        "creative-director",
        "asset-image-assistant",
        "storyboard-planner",
        "storyboard-director",
        "voiceover-to-video-script",
    ):
        g = build_stage_guidance(target, skill)
        assert g["stage_skill"] == skill
        assert "PROJECT_RULES.md" in g["core_docs"]
        assert len(g["key_reminders"]) >= 2
        if LIVE_PROJECT.is_dir():
            assert any("PRODUCT_BRIEF.md" in p for p in g["product_docs"])


def _run_put_segment(proj_str: str, pkg_rel: str, seg_file_str: str) -> None:
    env = {**dict(__import__("os").environ), "PYTHONPATH": str(REPO / "scripts")}
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/video_prompt_package.py"),
            "put-segment",
            "--project",
            proj_str,
            "--package",
            pkg_rel,
            "--input",
            seg_file_str,
        ],
        cwd=REPO,
        env=env,
        check=True,
    )


def test_video_prompt_concurrent_put_segment_preserves_all_and_sorts_chronologically(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import video_prompt_package as vpp

    proj = tmp_path / "proj"
    cap_rel = "06_视频/cap.json"
    cap_file = proj / cap_rel
    cap_file.parent.mkdir(parents=True, exist_ok=True)
    cap_file.write_text(json.dumps({
        "captured_at": "2026-11-27T00:00:00+08:00",
        "model": {"type": "video", "modelId": "doubao-seedance-2-5-260628"},
    }), encoding="utf-8")
    shots = [
        {"shot_id": f"S0{i}-00{i}", "rhythm": "约5秒｜平稳", "action": f"动作{i}。", "cut": {"exit": f"尾态{i}", "handoff": "切"}, "audio": {"dialogue": []}}
        for i in range(1, 5)
    ]
    scenes = [{"scene_id": f"S0{i}", "shots": [shots[i - 1]]} for i in range(1, 5)]
    storyboard = {"schema_version": "5.0", "project_id": "p", "revision": "R01", "scenes": scenes}
    fake_ctx = {
        "project": proj,
        "manifest": {"project_id": "p", "artifacts": []},
        "kickoff": {"canvas": {"model": "Seedance 2.5", "platform": "redframe", "aspect_ratio": "9:16", "resolution": "480p"}},
        "dir_entry": {"artifact_id": "DIR-R01", "revision": "R01", "path_or_url": "03_导演/R01.json", "checksum": "a" * 64},
        "storyboard": storyboard,
        "narrative": {"asset_requirements": []},
        "ready_assets": {},
        "supplements": {},
        "product_pack": None,
        "real_product_asset_id": "AST-RP-01",
        "shots_ordered": shots,
        "shot_by_id": {s["shot_id"]: s for s in shots},
        "shot_scene": {shots[i - 1]["shot_id"]: scenes[i - 1] for i in range(1, 5)},
    }
    monkeypatch.setattr(vpp, "_context", lambda _p: fake_ctx)
    vpp.cmd_init(argparse.Namespace(
        project=str(proj), revision="R01", output="06_视频/R01.json",
        from_package=None, capability_evidence=cap_rel, capability_locator="model", min_duration=4, max_duration=30,
    ))
    vpp.cmd_plan_acts(argparse.Namespace(
        project=str(proj), max_segments_per_act=2, target_duration=8.0, output="06_视频/分幕接口卡.json",
    ))
    plan = json.loads((proj / "06_视频/分幕接口卡.json").read_text(encoding="utf-8"))
    assert len(plan["acts"]) == 2

    # Write segments out of order concurrently across threads (SEG-04, SEG-02, SEG-03, SEG-01)
    seg_files = []
    for i in (4, 2, 3, 1):
        sf = proj / f"seg_{i}.json"
        sf.write_text(json.dumps({
            "segment_id": f"SEG-0{i}",
            "title": f"段{i}",
            "source_shots": [f"S0{i}-00{i}"],
            "duration_seconds": 5,
            "materials": [],
            "prompt": f"竖屏中景，执行动作{i}。无背景音乐，不叠加任何字幕。",
        }, ensure_ascii=False), encoding="utf-8")
        seg_files.append(sf)

    def _put_one(sf: Path) -> None:
        vpp.cmd_put_segment(argparse.Namespace(
            project=str(proj), package="06_视频/R01.json", input=str(sf), reconcile_reason=None,
        ))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_put_one, seg_files))

    saved = json.loads((proj / "06_视频/R01.json").read_text(encoding="utf-8"))
    assert [s["segment_id"] for s in saved["generation_segments"]] == ["SEG-01", "SEG-02", "SEG-03", "SEG-04"]
    assert saved["scope"]["type"] == "full_local_authored"


def test_director_cli_flag_order_and_visual_batch_commands(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import director_package as dp
    import visual_stage_cli as vs

    # Verify --project / --package work AFTER subcommand as well as before
    monkeypatch.setattr(dp, "outline", lambda root: {"ok": True, "root": str(root)})
    rc = dp.main(["outline", "--project", str(tmp_path), "--package", "03_导演/R01.json"])
    assert rc == 0

    # Verify batch-save-checks writes multiple validated supplement requests in one call
    proj = tmp_path / "vproj"
    dir_rel = "03_导演/R01.json"
    (proj / "03_导演").mkdir(parents=True, exist_ok=True)
    (proj / dir_rel).write_text('{"schema_version":"5.0"}', encoding="utf-8")
    ast_rel = "04_资产/AST-001.png"
    (proj / "04_资产").mkdir(parents=True, exist_ok=True)
    (proj / ast_rel).write_bytes(b"png")
    fake_vctx = {
        "project": proj,
        "dir_entry": {"artifact_id": "DIR-R01", "revision": "R01", "path_or_url": dir_rel, "user_approved": True},
        "dir_path": proj / dir_rel,
        "dir_sha256": "b" * 64,
        "storyboard": {"project_id": "vp", "scenes": []},
        "chosen_by_ast": {"AST-001": "AST-001@1x4"},
        "resolved_map": {"AST-001@1x4": {"path": str(proj / ast_rel)}},
        "waiting_assets": [],
        "kickoff": {},
        "product_pack": None,
    }
    monkeypatch.setattr(vs, "_ctx", lambda _p: fake_vctx)
    monkeypatch.setattr(vs, "validate_visual_supplement_request", lambda doc, project_root=None: None)
    batch_in = tmp_path / "batch_vs.json"
    batch_in.write_text(json.dumps([
        {"request_id": "VS-01", "source_shot_ids": ["S01-001"], "assets": ["AST-001"], "decision": "reuse_existing"},
        {"request_id": "VS-02", "source_shot_ids": ["S01-002"], "assets": ["AST-001"], "decision": "request_supplement"},
    ], ensure_ascii=False), encoding="utf-8")
    assert vs.main(["batch-save-checks", "--project", str(proj), "--input", str(batch_in)]) == 0
    assert (proj / "05_分镜/VS-01_视觉补充请求.json").is_file()
    assert (proj / "05_分镜/VS-02_视觉补充请求.json").is_file()
