#!/usr/bin/env python3
"""Headless Blender director-previsualization entry point.

The outer process validates recipes, manages licensed external assets and runs
Blender in background mode. Blender-specific scene construction lives in
``scripts/blender_previs/runner.py`` so this command remains testable without a
GUI or a Blender Python import in the host interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

if __package__:
    from .core.schema_contracts import SchemaContractError, require_schema
    from .blender_previs.output_contract import (OutputContractError, build_output_plan, build_video_frame_range,
                                                json_fingerprint, require_empty_output)
    from .blender_previs import project as project_previs
else:
    from core.schema_contracts import SchemaContractError, require_schema
    from blender_previs.output_contract import (OutputContractError, build_output_plan, build_video_frame_range,
                                               json_fingerprint, require_empty_output)
    from blender_previs import project as project_previs


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "assets" / "blender-previs" / "asset-registry.json"
RUNNER_PATH = REPO_ROOT / "scripts" / "blender_previs" / "runner.py"
VERIFY_PATH = REPO_ROOT / "scripts" / "blender_previs" / "verify.py"
SHOOT_PATH = REPO_ROOT / "scripts" / "blender_previs" / "shoot.py"
MPFB_BUILDER_PATH = REPO_ROOT / "scripts" / "blender_previs" / "build_mpfb_human.py"
MPFB_OUTPUT_DIR = REPO_ROOT / ".master-storyboarding" / "blender-assets" / "generated" / "mpfb-neutral-human"
MPFB_GLB_PATH = MPFB_OUTPUT_DIR / "mpfb-neutral-human.glb"
DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")


class PrevisError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PrevisError(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise PrevisError(f"JSON 顶层必须是对象：{path}")
    return value


def find_blender(explicit: str | None = None) -> Path:
    candidates = [explicit, os.environ.get("BLENDER_EXE"), shutil.which("blender"), str(DEFAULT_BLENDER)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    # 兜底：扫描官方安装目录里的任意版本（含 5.x），避免只认固定版本。
    for base in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation",):
        for exe in sorted(base.glob("Blender */blender.exe"), reverse=True):
            if exe.is_file():
                return exe.resolve()
    raise PrevisError("找不到 Blender。请安装 Blender 或设置 BLENDER_EXE（例如 "
                      r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe）。")


def blender_config_root(blender: Path | None = None) -> Path:
    """与指定 Blender 版本对应的用户配置目录（版本号取 exe 所在目录名）。"""
    appdata = Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"
    if blender is not None:
        version = blender.parent.name.removeprefix("Blender ").strip()
        if version:
            return appdata / version
    return appdata


def mpfb_installed(blender: Path | None = None) -> bool:
    """只认与当前 Blender 版本匹配的 MPFB，避免扫描到其它版本造成误判。"""
    root = blender_config_root(blender)
    if (root / "extensions/blender_org/mpfb/__init__.py").is_file():
        return True
    if blender is None:
        return bool(list(root.glob("*/extensions/blender_org/mpfb/__init__.py")))
    return False


def validate_recipe(recipe: dict[str, Any]) -> None:
    try:
        require_schema(recipe, "blender-previs-recipe.schema.json", label="预演配方")
    except SchemaContractError as exc:
        raise PrevisError(f"配方校验失败：{exc}") from exc

    duration = float(recipe.get("project", {}).get("duration_seconds", 0))
    actor_ids: set[str] = set()
    for actor in recipe.get("actors", []):
        actor_id = str(actor.get("id", ""))
        if actor_id in actor_ids:
            raise PrevisError(f"人物 id 重复：{actor_id}")
        actor_ids.add(actor_id)
    camera = recipe.get("camera", {})
    for index, item in enumerate(camera.get("keyframes", [])):
        if float(item.get("time", -1)) > duration:
            raise PrevisError(f"camera.keyframes[{index}].time 超出项目时长")

    tracking_actor = camera.get("tracking_actor")
    if camera.get("position_space") == "tracking_actor_relative" and not tracking_actor:
        raise PrevisError("camera.position_space 为 tracking_actor_relative 时必须设置 tracking_actor")
    if tracking_actor and str(tracking_actor) not in actor_ids:
        raise PrevisError(f"camera.tracking_actor 不存在：{tracking_actor}")

    shots = camera.get("shots", [])
    shot_ids: set[str] = set()
    expected_start = 0.0
    fps = int(recipe.get("project", {}).get("fps", 0))
    for index, shot in enumerate(shots):
        shot_id = str(shot.get("id", ""))
        if shot_id in shot_ids:
            raise PrevisError(f"镜头 id 重复：{shot_id}")
        shot_ids.add(shot_id)
        start = float(shot.get("start", -1))
        end = float(shot.get("end", -1))
        if end <= start:
            raise PrevisError(f"camera.shots[{index}] 的 end 必须晚于 start")
        if round(end * fps) <= round(start * fps):
            raise PrevisError(f"camera.shots[{index}] 的时长不足一帧")
        if abs(start - expected_start) > 1e-6:
            raise PrevisError(f"camera.shots[{index}] 与上一镜头不连续，期望从 {expected_start:g} 秒开始")
        if end > duration + 1e-6:
            raise PrevisError(f"camera.shots[{index}].end 超出项目时长")
        shot_tracking_actor = shot.get("tracking_actor")
        if shot.get("position_space", "world") == "tracking_actor_relative" and not shot_tracking_actor:
            raise PrevisError(f"camera.shots[{index}] 使用人物相对坐标时必须设置 tracking_actor")
        if shot_tracking_actor and str(shot_tracking_actor) not in actor_ids:
            raise PrevisError(f"camera.shots[{index}].tracking_actor 不存在：{shot_tracking_actor}")
        shot_duration = end - start
        previous_keyframe_time = -1.0
        for keyframe_index, item in enumerate(shot.get("keyframes", [])):
            keyframe_time = float(item.get("time", -1))
            if keyframe_time > shot_duration + 1e-6:
                raise PrevisError(
                    f"camera.shots[{index}].keyframes[{keyframe_index}].time 超出镜头时长"
                )
            if keyframe_time <= previous_keyframe_time:
                raise PrevisError(f"camera.shots[{index}] 的关键帧时间必须严格递增")
            previous_keyframe_time = keyframe_time
        first_keyframe_time = float(shot.get("keyframes", [{}])[0].get("time", -1))
        if abs(first_keyframe_time) > 1e-6:
            raise PrevisError(f"camera.shots[{index}] 的第一个关键帧必须从镜头内 0 秒开始")
        expected_start = end
    if shots and abs(expected_start - duration) > 1e-6:
        raise PrevisError(f"正式镜头时间轴必须覆盖完整项目时长，当前结束于 {expected_start:g} 秒")

    shot_by_id = {str(shot["id"]): shot for shot in shots}
    reference_ids: set[str] = set()
    for index, view in enumerate(recipe.get("outputs", {}).get("reference_views", [])):
        reference_id = str(view.get("id", ""))
        if reference_id in reference_ids:
            raise PrevisError(f"参考视图 id 重复：{reference_id}")
        reference_ids.add(reference_id)
        time_value = float(view.get("time", -1))
        if time_value > duration + 1e-6:
            raise PrevisError(f"outputs.reference_views[{index}].time 超出项目时长")
        for subject_id in view.get("subject_ids", []):
            if str(subject_id) not in actor_ids:
                raise PrevisError(f"outputs.reference_views[{index}] 引用了不存在的人物：{subject_id}")
        orientation_actor = view.get("orientation_actor")
        if orientation_actor and str(orientation_actor) not in actor_ids:
            raise PrevisError(
                f"outputs.reference_views[{index}].orientation_actor 不存在：{orientation_actor}"
            )
        shot_id = view.get("shot_id")
        if shot_id:
            shot = shot_by_id.get(str(shot_id))
            if shot is None:
                raise PrevisError(f"outputs.reference_views[{index}].shot_id 不存在：{shot_id}")
            is_last_shot = shots and shot is shots[-1]
            within = float(shot["start"]) <= time_value < float(shot["end"])
            if is_last_shot and abs(time_value - float(shot["end"])) <= 1e-6:
                within = True
            if not within:
                raise PrevisError(f"outputs.reference_views[{index}] 的 time 不在镜头 {shot_id} 范围内")
    object_ids: set[str] = set()
    for item in recipe.get("environment", {}).get("objects", []):
        object_id = str(item.get("id", ""))
        if object_id in object_ids:
            raise PrevisError(f"场景物体 id 重复：{object_id}")
        object_ids.add(object_id)
    outputs = recipe.get("outputs", {})
    if outputs.get("preview_video", True):
        build_video_frame_range(recipe, fps)
    topdown = outputs.get("topdown") or {}
    if topdown and topdown.get("enabled", True) and topdown.get("video", False):
        build_video_frame_range(recipe, fps, topdown=True)
    build_output_plan(recipe)


def registry() -> tuple[Path, list[dict[str, Any]]]:
    data = read_json(REGISTRY_PATH)
    cache_root = REPO_ROOT / str(data["cache_root"])
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise PrevisError("资产注册表 assets 必须是数组")
    return cache_root, assets


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_assets(only: set[str] | None = None) -> list[dict[str, Any]]:
    cache_root, assets = registry()
    unknown = (only or set()) - {str(item["id"]) for item in assets}
    if unknown:
        raise PrevisError("未知资产 ID：" + ", ".join(sorted(unknown)))
    records: list[dict[str, Any]] = []
    for item in assets:
        asset_id = str(item["id"])
        if only and asset_id not in only:
            continue
        target = cache_root / str(item["filename"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.stat().st_size == 0:
            if item.get("download_mode") == "generated_locally":
                raise PrevisError(
                    f"资产 {asset_id} 须在本机生成，不能下载；请先检查 doctor，按 bootstrap-human 流程操作；安装和生成仍需授权"
                )
            if item.get("download_mode") == "manual_archive":
                raise PrevisError(
                    f"资产 {asset_id} 由发布页压缩包提供，尚未安装；请从 {item['url']} 下载免费标准包并按注册路径解压"
                )
            request = urllib.request.Request(str(item["url"]), headers={"User-Agent": "Master-Storyboarding-Blender-Previs/1.0"})
            with urllib.request.urlopen(request, timeout=180) as response, tempfile.NamedTemporaryFile(
                dir=target.parent, delete=False
            ) as temporary:
                shutil.copyfileobj(response, temporary)
                temp_path = Path(temporary.name)
            temp_path.replace(target)
        record = dict(item)
        record.update({"path": str(target.resolve()), "bytes": target.stat().st_size, "sha256": sha256(target)})
        records.append(record)
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / "installed-assets.json").write_text(
        json.dumps({"schema_version": "1.0", "assets": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return records


def run_blender(recipe_path: Path, output_dir: Path, blender: Path, *, prepared_files=()) -> dict[str, Any]:
    require_empty_output(output_dir, prepared_files)
    recipe = read_json(recipe_path)
    source_recipe_sha256 = json_fingerprint(recipe)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_root, assets = registry()
    asset_specs = {str(item["id"]): item for item in assets}
    asset_paths = {asset_id: cache_root / str(item["filename"]) for asset_id, item in asset_specs.items()}
    used_assets: set[str] = set()
    used_generated: set[str] = set()
    for actor in recipe.get("actors", []):
        if actor.get("human_template") == "mpfb_neutral":
            if not MPFB_GLB_PATH.is_file():
                build_mpfb_human(blender)
            actor["asset_path"] = str(MPFB_GLB_PATH.resolve())
            used_generated.add("mpfb_neutral")
            continue
        asset_id = actor.get("asset")
        if not asset_id:
            continue
        asset_path = asset_paths.get(str(asset_id))
        if asset_path is None:
            raise PrevisError(f"外部人物资产未登记：{asset_id}")
        if not asset_path.is_file():
            raise PrevisError(f"外部人物资产尚未下载：{asset_id}；请先运行 bootstrap-assets")
        actor["asset_path"] = str(asset_path.resolve())
        used_assets.add(str(asset_id))
    lighting = recipe.get("lighting", {})
    lighting_asset = lighting.get("asset")
    if lighting_asset:
        asset_path = asset_paths.get(str(lighting_asset))
        if asset_path is None:
            raise PrevisError(f"灯光资产未登记：{lighting_asset}")
        if not asset_path.is_file():
            raise PrevisError(f"灯光资产尚未下载：{lighting_asset}；请先运行 bootstrap-assets")
        lighting["asset_path"] = str(asset_path.resolve())
        used_assets.add(str(lighting_asset))
    resolved_recipe = output_dir / "resolved.recipe.json"
    resolved_recipe.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "9",
        "--python",
        str(RUNNER_PATH),
        "--",
        "--recipe",
        str(resolved_recipe.resolve()),
        "--output-dir",
        str(output_dir.resolve()),
    ]
    for path in prepared_files:
        command.extend(["--prepared-file", str(Path(path).resolve())])
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    log_path = output_dir / "blender-previs.log"
    log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise PrevisError(f"Blender 后台执行失败（退出码 {completed.returncode}），日志：{log_path}")
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        raise PrevisError(f"Blender 未生成产物清单：{manifest_path}")
    manifest = read_json(manifest_path)
    manifest["source_recipe_sha256"] = source_recipe_sha256
    manifest["external_assets"] = [
        {
            "id": asset_id,
            "license": asset_specs[asset_id]["license"],
            "attribution": asset_specs[asset_id]["attribution"],
            "source": asset_specs[asset_id]["source"],
            "path": str(asset_paths[asset_id].resolve()),
            "sha256": sha256(asset_paths[asset_id]),
        }
        for asset_id in sorted(used_assets)
    ]
    if "mpfb_neutral" in used_generated:
        manifest["external_assets"].append({
            "id": "mpfb_neutral",
            "license": "CC0-1.0",
            "attribution": "Generated locally with MPFB / MakeHuman Community",
            "source": "https://extensions.blender.org/add-ons/mpfb/",
            "path": str(MPFB_GLB_PATH.resolve()),
            "sha256": sha256(MPFB_GLB_PATH),
        })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_mpfb_human(blender: Path, keep_helpers: bool = False) -> dict[str, Any]:
    MPFB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not mpfb_installed(blender):
        install = subprocess.run(
            [str(blender), "--command", "extension", "install", "--sync", "--enable", "mpfb"],
            cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True,
        )
        (MPFB_OUTPUT_DIR / "install.log").write_text(install.stdout + "\n" + install.stderr, encoding="utf-8")
        if install.returncode:
            raise PrevisError("无法从 Blender 官方扩展库安装 MPFB；详见人体模板 install.log")
    command = [
        str(blender), "--background", "--python-exit-code", "9", "--python", str(MPFB_BUILDER_PATH),
        "--", "--output-dir", str(MPFB_OUTPUT_DIR.resolve()),
    ]
    if keep_helpers:
        command.append("--keep-helpers")
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    log_path = MPFB_OUTPUT_DIR / "build.log"
    log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise PrevisError(f"MPFB 人体模板生成失败（退出码 {completed.returncode}），日志：{log_path}")
    report_path = MPFB_OUTPUT_DIR / "mpfb-neutral-human.json"
    if not report_path.is_file() or not MPFB_GLB_PATH.is_file():
        raise PrevisError(f"MPFB 未生成预期人体模板：{report_path}")
    return read_json(report_path)


def generate_depth(image_path: Path, output_path: Path, model_path: Path) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import onnxruntime as ort  # type: ignore
    except ImportError as exc:
        raise PrevisError("单图深度需要 opencv-python、numpy 和 onnxruntime") from exc

    # OpenCV's Windows file APIs do not reliably accept non-ASCII paths.
    image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise PrevisError(f"无法读取参考图片：{image_path}")
    original_h, original_w = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    tensor = ((resized - 0.5) / 0.5).transpose(2, 0, 1)[None, ...].astype(np.float32)
    available = set(ort.get_available_providers())
    providers = [name for name in ("CUDAExecutionProvider", "CPUExecutionProvider") if name in available]
    session = ort.InferenceSession(str(model_path), providers=providers)
    input_name = session.get_inputs()[0].name
    result = session.run(None, {input_name: tensor})[0]
    depth = np.squeeze(result).astype(np.float32)
    depth = cv2.resize(depth, (original_w, original_h), interpolation=cv2.INTER_CUBIC)
    low, high = np.percentile(depth, (2, 98))
    depth = np.clip((depth - low) / max(float(high - low), 1e-6), 0, 1)
    depth_u16 = (depth * 65535.0).astype(np.uint16)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded, buffer = cv2.imencode(".png", depth_u16)
    if not encoded:
        raise PrevisError(f"无法写入深度图：{output_path}")
    buffer.tofile(str(output_path))
    return {"width": original_w, "height": original_h, "min_percentile": float(low), "max_percentile": float(high)}


def image_environment_recipe(image_path: Path, depth_path: Path, name: str, resolution: tuple[int, int]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project": {"name": name, "fps": 24, "duration_seconds": 4, "resolution": list(resolution)},
        "environment": {
            "preset": "image_relief",
            "reference_image": str(image_path.resolve()),
            "depth_map": str(depth_path.resolve()),
            "relief_depth": 2.0,
        },
        "actors": [],
        "camera": {
            "lens_mm": 38,
            "keyframes": [
                {"time": 0, "position": [0, -3.2, 2.4], "look_at": [0, 2.0, 2.25]},
                {"time": 4, "position": [0.35, -2.9, 2.45], "look_at": [0, 2.0, 2.25]},
            ],
        },
        "lighting": {"preset": "soft_daylight"},
        "outputs": {"blend": True, "preview_video": True, "still_frames": [0, 2, 4], "render_style": "textured"},
    }


def command_doctor(args: argparse.Namespace) -> int:
    blender = find_blender(args.blender)
    completed = subprocess.run(
        [str(blender), "--version"], text=True, encoding="utf-8", errors="replace", capture_output=True, check=True
    )
    cache_root, assets = registry()
    print(json.dumps({
        "ok": True,
        "blender": str(blender),
        "version": completed.stdout.splitlines()[0],
        "runner": str(RUNNER_PATH),
        "cache_root": str(cache_root.resolve()),
        "registered_assets": [item["id"] for item in assets],
        "mpfb_extension": mpfb_installed(blender),
        "mpfb_template": str(MPFB_GLB_PATH.resolve()) if MPFB_GLB_PATH.is_file() else None,
    }, ensure_ascii=False, indent=2))
    return 0


def command_assets(args: argparse.Namespace) -> int:
    records = download_assets(set(args.only) if args.only else None)
    print(json.dumps({"ok": True, "assets": records}, ensure_ascii=False, indent=2))
    return 0


def command_human_template(args: argparse.Namespace) -> int:
    report = build_mpfb_human(find_blender(args.blender), keep_helpers=bool(getattr(args, "keep_helpers", False)))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_shoot(args: argparse.Namespace) -> int:
    """在已建好的场次白模上运镜出图：不重建场景，只加相机与按镜头挪道具。"""
    output_dir = Path(args.output_dir).resolve()
    require_empty_output(output_dir)
    command = [
        str(find_blender(args.blender)), "--background", "--python-exit-code", "9",
        "--python", str(SHOOT_PATH), "--",
        "--scene", str(Path(args.scene).resolve()),
        "--shots", str(Path(args.shots).resolve()),
        "--output-dir", str(output_dir),
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    print((completed.stdout or "").strip() or (completed.stderr or "").strip())
    return completed.returncode


def command_verify(args: argparse.Namespace) -> int:
    """按配方自动验收已构建产物：接触、比例、机位与产物齐全度。"""
    command = [
        str(find_blender(args.blender)), "--background", "--python-exit-code", "9",
        "--python", str(VERIFY_PATH), "--",
        "--recipe", str(Path(args.recipe).resolve()),
        "--output-dir", str(Path(args.output_dir).resolve()),
    ]
    if getattr(args, "scene", None):
        command += ["--scene", str(Path(args.scene).resolve())]
    if getattr(args, "shots", None):
        command += ["--shots", str(Path(args.shots).resolve())]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    print((completed.stdout or "").strip() or (completed.stderr or "").strip())
    return completed.returncode


def command_build(args: argparse.Namespace) -> int:
    recipe_path = Path(args.recipe).resolve()
    recipe = read_json(recipe_path)
    validate_recipe(recipe)
    result = run_blender(recipe_path, Path(args.output_dir).resolve(), find_blender(args.blender))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_reconstruct_scene(args: argparse.Namespace) -> int:
    recipe_path = Path(args.recipe).resolve()
    recipe = read_json(recipe_path)
    validate_recipe(recipe)
    environment = recipe.get("environment", {})
    if environment.get("preset") != "structured_scene":
        raise PrevisError("reconstruct-scene 只接受 environment.preset=structured_scene 的结构化重建配方")
    source_image = Path(str(environment.get("source_image", "")))
    if not source_image.is_file():
        raise PrevisError(f"结构化重建的源图片不存在：{source_image}")
    result = run_blender(recipe_path, Path(args.output_dir).resolve(), find_blender(args.blender))
    result["reconstruction"] = {
        "source_image": str(source_image.resolve()),
        "visible_objects": sum(item.get("provenance") == "visible" for item in environment.get("objects", [])),
        "partially_visible_objects": sum(item.get("provenance") == "partially_visible" for item in environment.get("objects", [])),
        "inferred_objects": sum(item.get("provenance") == "inferred" for item in environment.get("objects", [])),
        "editable_objects": len(environment.get("objects", [])),
        "actors": len(recipe.get("actors", [])),
    }
    manifest_path = Path(args.output_dir).resolve() / "manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_image_environment(args: argparse.Namespace) -> int:
    image_path = Path(args.image).resolve()
    if not image_path.is_file():
        raise PrevisError(f"参考图片不存在：{image_path}")
    output_dir = Path(args.output_dir).resolve()
    require_empty_output(output_dir)
    records = download_assets({"midas.v21-small-onnx"})
    model_path = Path(records[0]["path"])
    depth_path = output_dir / "depth" / f"{image_path.stem}_midas.png"
    depth_info = generate_depth(image_path, depth_path, model_path)
    recipe = image_environment_recipe(image_path, depth_path, args.name or image_path.stem, (args.width, args.height))
    recipe_path = output_dir / "image-environment.recipe.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    validate_recipe(recipe)
    result = run_blender(recipe_path, output_dir, find_blender(args.blender),
                         prepared_files=[depth_path, recipe_path])
    result["depth_estimation"] = depth_info
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_project(args: argparse.Namespace) -> int:
    """Opt-in only: register/confirm/handoff without launching a renderer."""
    root = Path(args.project).resolve()
    if args.command == 'register-scene':
        result = project_previs.register_scene(root, args.scene_id, args.revision,
            Path(args.scene).resolve(), Path(args.evidence).resolve(), args.reference,
            Path(args.recipe).resolve() if args.recipe else None)
    elif args.command == 'prepare-shot':
        result = {'input': str(project_previs.prepare_shot(root, args.shot_id, args.revision, args.scene_artifact))}
    elif args.command == 'register-reference':
        result = project_previs.register_reference(root, Path(args.input).resolve(), Path(args.spec).resolve())
    elif args.command == 'confirm-reference':
        result = project_previs.confirm_reference(root, args.artifact_id, args.approval_ref, args.user_quote)
    else:
        result = {'handoff': str(project_previs.handoff(root, args.artifact_id, Path(args.output_dir), args.media_role))}
    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, indent=2))
    return 0


def command_stage_shot(args: argparse.Namespace) -> int:
    """Add actors/cameras to a copied room using existing runner; no rendering here."""
    root = Path(args.project).resolve()
    binding = read_json(Path(args.input).resolve())
    project_previs.check_binding(root, binding)
    output = project_previs.project_file(root, args.output_dir)
    project_previs.empty_directory(output)
    recipe = read_json(Path(args.recipe).resolve())
    validate_recipe(recipe)
    shots = recipe.get('camera', {}).get('shots', [])
    if len(shots) != 1 or shots[0]['id'] != binding['shot']['shot_id']:
        raise PrevisError('逐镜配方必须且只能覆盖已准备的导演镜号')
    for actor in recipe['actors']:
        if actor.get('human_template') == 'mpfb_neutral':
            if not MPFB_GLB_PATH.is_file():
                raise PrevisError('本机标准人物模块缺失；请按需 bootstrap-human，不自动联网安装')
            actor['asset_path'] = str(MPFB_GLB_PATH.resolve())
    base_path = project_previs.verify_file(root, binding['scene_base'])
    scene_data = read_json(base_path)
    scene = project_previs.verify_file(root, next(f for f in scene_data['files'] if f['role'] == 'scene'))
    blender = find_blender(args.blender)
    output.mkdir(parents=True, exist_ok=True)
    resolved = output / 'resolved.recipe.json'
    resolved.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(args.input, output / 'shot-input.json')
    completed = subprocess.run([str(blender), '--background', '--python-exit-code', '9', '--python',
        str(REPO_ROOT / 'scripts/blender_previs/stage_shot.py'), '--', '--scene', str(scene),
        '--recipe', str(resolved), '--output-dir', str(output)], cwd=REPO_ROOT,
        text=True, encoding='utf-8', errors='replace', capture_output=True)
    (output / 'stage.log').write_text(completed.stdout + '\n' + completed.stderr, encoding='utf-8')
    if completed.returncode:
        raise PrevisError(f'逐镜布置失败；日志：{output / "stage.log"}')
    print(json.dumps({'ok': True, 'scene': str(output / 'previs.blend'), 'rendered': False}, ensure_ascii=False))
    return 0


def command_export_preview(args: argparse.Namespace) -> int:
    if __package__:
        from .blender_previs import preview
    else:
        from blender_previs import preview
    root = Path(args.project).resolve()
    output = preview.output_path(root, args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    binding = {}
    try:
        binding, recipe, sources, scene = preview.stage_inputs(root, args.stage_dir)
        validate_recipe(recipe)
        stage_dir = project_previs.project_file(root, args.stage_dir)
        command = [str(find_blender(args.blender)), '--background', '--python-exit-code', '9', '--python',
                   str(REPO_ROOT / 'scripts/blender_previs/export_preview.py'), '--', '--scene', str(scene),
                   '--recipe', str(stage_dir / 'resolved.recipe.json'), '--output-dir', str(output)]
        completed = subprocess.run(command, cwd=REPO_ROOT, text=True, encoding='utf-8',
                                   errors='replace', capture_output=True)
        (output / 'export.log').write_text(completed.stdout + '\n' + completed.stderr, encoding='utf-8')
        if completed.returncode:
            raise PrevisError('浏览器预览导出失败，请查看 ' + str(output / 'export.log') + '\n' +
                             (completed.stderr or completed.stdout)[-1200:])
        # Inputs can change while Blender runs; do not label an old export current.
        project_previs.check_binding(root, binding)
        for source in sources:
            if sha256(project_previs.project_file(root, source['path'])) != source['sha256']:
                raise PrevisError('导出过程中输入发生变化，请用新的逐镜布置与预览目录')
        manifest_path = output / 'preview_manifest.json'
        manifest = read_json(manifest_path)
        manifest.update(sources=sources, revision=binding['reference_revision'])
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'ok': True, 'shot_id': manifest['shot_id'], 'output_dir': str(output),
                          'rendered': False, 'user_confirmed': False}, ensure_ascii=False))
        return 0
    except (PrevisError, project_previs.ProjectPrevisError, OSError, ValueError, KeyError,
            StopIteration, subprocess.SubprocessError) as exc:
        (output / 'export_error.json').write_text(json.dumps({
            'error': str(exc), 'shot_id': binding.get('shot', {}).get('shot_id', output.name),
            'revision': binding.get('reference_revision', '')}, ensure_ascii=False), encoding='utf-8')
        raise PrevisError(str(exc)) from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Master-Storyboarding Blender 预演台/导演台 CLI")
    root.add_argument("--blender", help="blender.exe 路径；默认自动发现")
    sub = root.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查 Blender CLI 和资产注册表")
    doctor.set_defaults(func=command_doctor)
    assets = sub.add_parser("bootstrap-assets", help="下载并登记合规外部模板和模型")
    assets.add_argument("--only", action="append", help="只下载指定资产 id，可重复")
    assets.set_defaults(func=command_assets)
    human = sub.add_parser("bootstrap-human", help="用官方 MPFB 生成带蒙皮和53骨骼的中性人体模板")
    human.add_argument(
        "--keep-helpers",
        action="store_true",
        help="保留头发/裙子/睫毛/牙齿/JointCubes 与手指脚趾（默认删除，避免误读成服装或诱导精细手脚动作）",
    )
    human.set_defaults(func=command_human_template)
    build = sub.add_parser("build", help="根据导演配方生成 Blender 工程和预演")
    build.add_argument("--recipe", required=True)
    build.add_argument("--output-dir", required=True)
    build.set_defaults(func=command_build)
    verify = sub.add_parser("verify", help="按配方自动验收已构建产物（接触/比例/机位/景别/产物齐全）")
    verify.add_argument("--recipe", required=True)
    verify.add_argument("--output-dir", required=True)
    verify.add_argument("--scene", help="直接指定要验收的 previs.blend（shoot 产物没有自己的 blend）")
    verify.add_argument("--shots", help="镜头清单 JSON；按本镜有效配方检查人物、入画、景别和 shoot 产物")
    verify.set_defaults(func=command_verify)
    shoot = sub.add_parser("shoot", help="在已建好的场次白模上按镜头运镜出图（不重建场景）")
    shoot.add_argument("--scene", required=True, help="场次白模的 previs.blend")
    shoot.add_argument("--shots", required=True, help="镜头清单 JSON")
    shoot.add_argument("--output-dir", required=True)
    shoot.set_defaults(func=command_shoot)
    preview = sub.add_parser('export-preview', help='导出本镜同源 Three.js 预览，不渲染、不确认')
    preview.add_argument('--project', required=True)
    preview.add_argument('--stage-dir', required=True, help='stage-shot 产出的逐镜布置目录')
    preview.add_argument('--output-dir', required=True, help='05_分镜/预演/预览 下的新目录')
    preview.set_defaults(func=command_export_preview)
    reconstruction = sub.add_parser("reconstruct-scene", help="从已分析的单图结构配方生成完整可编辑三维场景")
    reconstruction.add_argument("--recipe", required=True)
    reconstruction.add_argument("--output-dir", required=True)
    reconstruction.set_defaults(func=command_reconstruct_scene)
    image_env = sub.add_parser("image-environment", help="从单张图片生成深度驱动的 2.5D 环境")
    image_env.add_argument("--image", required=True)
    image_env.add_argument("--output-dir", required=True)
    image_env.add_argument("--name")
    image_env.add_argument("--width", type=int, default=540)
    image_env.add_argument("--height", type=int, default=960)
    image_env.set_defaults(func=command_image_environment)
    for name, description in (
        ('register-scene', '登记可复用的已有本机空间基座，不重渲'),
        ('prepare-shot', '按当前确认镜号提取白模输入，显式选择本镜白模步骤'),
        ('stage-shot', '在房间基座副本上按现有配方加人物与摄影机，不重搭房间、不渲染'),
        ('register-reference', '登记本机白模图和检查证据为候选'),
        ('confirm-reference', '记录实际用户采用的逐镜白模，绑定文件哈希'),
        ('handoff-reference', '交接确认白模和有效母图，停止在生图前'),
    ):
        cmd = sub.add_parser(name, help=description)
        cmd.add_argument('--project', required=True)
        cmd.set_defaults(func=command_stage_shot if name == 'stage-shot' else command_project)
        if name == 'register-scene':
            cmd.add_argument('--scene-id', required=True)
            cmd.add_argument('--revision', required=True)
            cmd.add_argument('--scene', required=True)
            cmd.add_argument('--evidence', required=True)
            cmd.add_argument('--reference', action='append', required=True, help='确认场景 AST-ID@OUTPUT-ID，可重复')
            cmd.add_argument('--recipe')
        elif name == 'prepare-shot':
            cmd.add_argument('--shot-id', required=True)
            cmd.add_argument('--revision', required=True, help='本镜白模修订，独立于导演包 revision')
            cmd.add_argument('--scene-artifact', required=True)
        elif name in ('register-reference', 'stage-shot'):
            cmd.add_argument('--input', required=True, help='prepare-shot 产出的 shot-input.json')
            if name == 'register-reference':
                cmd.add_argument('--spec', required=True)
            else:
                cmd.add_argument('--recipe', required=True)
                cmd.add_argument('--output-dir', required=True)
        else:
            cmd.add_argument('--artifact-id', required=True)
            if name == 'confirm-reference':
                cmd.add_argument('--approval-ref', required=True)
                cmd.add_argument('--user-quote', required=True)
            else:
                cmd.add_argument('--output-dir', required=True)
                cmd.add_argument('--media-role', action='append',
                                 help='选择已登记 files 的图片/视频 role，可重复；默认带全部已登记媒体')
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (PrevisError, OutputContractError, project_previs.ProjectPrevisError,
            subprocess.SubprocessError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
