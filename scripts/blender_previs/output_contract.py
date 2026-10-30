"""预演写入和验收共用的文件、时间与输入对应关系；不依赖 Blender。"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
from fractions import Fraction
from pathlib import Path


class OutputContractError(RuntimeError):
    pass


def require_empty_output(output_dir: Path, prepared_files=()) -> None:
    allowed = {Path(path).resolve() for path in prepared_files}
    if output_dir.exists() and (not output_dir.is_dir() or any(
        path.resolve() not in allowed for path in output_dir.rglob("*") if not path.is_dir()
    ) or any(not any(file.is_relative_to(path.resolve()) for file in allowed)
             for path in output_dir.rglob("*") if path.is_dir())):
        raise OutputContractError(f"输出目录必须为空且不能是文件；请使用新目录：{output_dir}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def shot_still_times(shot: dict) -> list[float]:
    start, end = float(shot["start"]), float(shot["end"])
    return [float(time) for time in (shot.get("still_times") or [min(start + 0.5, (start + end) / 2)])]


def reference_name(shot: dict, index: int, time: float) -> str:
    suffix = "START" if float(time) == float(shot["start"]) else f"T{time:05.2f}"
    return f"REF-{shot['id']}_{suffix}.png"


def shot_frame_range(shot: dict, fps: int) -> tuple[int, int]:
    # Blender 渲染包含 frame_end；关键帧在 end 时刻，视频结束在其前一帧。
    return round(float(shot["start"]) * fps) + 1, round(float(shot["end"]) * fps)


def build_video_frame_range(recipe: dict, fps: int, *, topdown=False) -> tuple[int, int]:
    duration = float(recipe["project"]["duration_seconds"])
    start, end = 0.0, duration
    outputs = recipe.get("outputs", {})
    if not topdown and outputs.get("preview_video_shots"):
        wanted = set(outputs["preview_video_shots"])
        picked = [shot for shot in recipe.get("camera", {}).get("shots", []) if shot["id"] in wanted]
        if {shot["id"] for shot in picked} != wanted:
            raise OutputContractError("预演视频选择了不存在的镜号")
        picked.sort(key=lambda shot: float(shot["start"]))
        if any(abs(float(a["end"]) - float(b["start"])) > 1e-6 for a, b in zip(picked, picked[1:])):
            raise OutputContractError("build 视频选镜必须相邻；不相邻的运镜镜请用 shoot 逐镜输出")
        start, end = float(picked[0]["start"]), float(picked[-1]["end"])
    elif not topdown and outputs.get("preview_video_range"):
        start, end = map(float, outputs["preview_video_range"])
    if not 0 <= start < end <= duration:
        raise OutputContractError("预演视频时间范围必须在项目时长内")
    first, last = shot_frame_range({"start": start, "end": end}, fps)
    if last < first:
        raise OutputContractError("预演视频时间范围不足一帧")
    return first, last


def build_output_plan(recipe: dict) -> dict[str, str]:
    """构建与验收共用准确文件计划，先阻止本轮内部的命名覆盖。"""
    wanted = {}
    def add(name, kind, time=None):
        if name in wanted:
            raise OutputContractError(f"预演输出文件名冲突：{name}")
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise OutputContractError(f"预演输出文件名无效：{name}")
        if time is not None and not 0 <= float(time) <= float(recipe["project"]["duration_seconds"]):
            raise OutputContractError(f"预演静帧时刻超出项目时长：{name}")
        wanted[name] = kind
    outputs = recipe.get("outputs", {})
    if outputs.get("blend", True):
        add("previs.blend", "blend")
    if outputs.get("preview_video", True):
        add("previs.mp4", "preview_video")
    for time in outputs.get("still_frames", []):
        add(f"stills/frame-{float(time):05.2f}.png", "still", time)
    for view in outputs.get("reference_views", []):
        add(f"reference-frames/{view['id']}.png", "reference_frame", view['time'])
    topdown = outputs.get("topdown") or {}
    if topdown and topdown.get("enabled", True):
        for time in topdown.get("still_times", []):
            add(f"topdown/topdown-{float(time):05.2f}.png", "topdown_still", time)
        if topdown.get("video", False):
            add("topdown/topdown.mp4", "topdown_video")
    return wanted


def render_settings(scene) -> dict:
    render = scene.render
    return {"fps": int(render.fps), "fps_base": float(render.fps_base),
            "width": int(render.resolution_x * render.resolution_percentage / 100),
            "height": int(render.resolution_y * render.resolution_percentage / 100)}


def validate_shots(spec: dict, fps: int) -> None:
    shots = spec.get("shots")
    if not isinstance(shots, list) or not shots:
        raise OutputContractError("镜头清单必须含非空 shots 数组")
    ids = set()
    for shot in shots:
        ident = shot.get("id")
        if not isinstance(ident, str) or not ident or Path(ident).name != ident or ident in ids:
            raise OutputContractError(f"镜号无效或重复：{ident}")
        ids.add(ident)
        start, end = float(shot["start"]), float(shot["end"])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise OutputContractError(f"镜头时间范围无效：{ident}")
        first, last = shot_frame_range(shot, fps)
        if last < first:
            raise OutputContractError(f"镜头时长不足一帧：{ident}")
        keys = shot.get("keyframes") or []
        times = [float(key["time"]) for key in keys]
        if not times or times[0] != 0 or any(not math.isfinite(t) or t < 0 or t > end - start
                                           for t in times) or any(a >= b for a, b in zip(times, times[1:])):
            raise OutputContractError(f"镜头关键帧时间无效：{ident}")
        stills = shot_still_times(shot)
        if any(not math.isfinite(t) or not start <= t <= end for t in stills):
            raise OutputContractError(f"静帧时刻超出镜头范围：{ident}")
        names = [reference_name(shot, i, t) for i, t in enumerate(stills)]
        if len(names) != len(set(names)):
            raise OutputContractError(f"静帧文件名冲突：{ident}")


def output_record(path: Path, **fields) -> dict:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": file_sha256(path), **fields}


def _file_matches(path: Path, record: dict | None) -> bool:
    return bool(record and path.is_file() and path.stat().st_size > 0
                and Path(str(record.get("path", ""))).resolve() == path.resolve()
                and record.get("bytes") == path.stat().st_size
                and record.get("sha256") == file_sha256(path))


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise OutputContractError(f"PNG 无法读取：{path}")
    return struct.unpack(">II", header[16:24])


def probe_video(path: Path) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=width,height,avg_frame_rate,nb_read_frames,duration", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
        )
        stream = json.loads(result.stdout)["streams"][0]
        return {"width": int(stream["width"]), "height": int(stream["height"]),
                "fps": float(Fraction(stream["avg_frame_rate"])), "frames": int(stream["nb_read_frames"]),
                "duration": float(stream["duration"])}
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        raise OutputContractError(f"视频无法核验（需要 ffprobe）：{path}：{exc}") from exc


def check_shoot_outputs(spec: dict, scene_path: Path, output_dir: Path, settings: dict) -> list[dict]:
    checks = []
    def add(ident, ok, detail):
        checks.append({"id": ident, "ok": bool(ok), "detail": detail})
    try:
        manifest = json.loads((output_dir / "shoot_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        add("file:shoot-manifest", False, str(exc))
        return checks
    add("inputs:scene", scene_path.is_file() and manifest.get("scene_sha256") == file_sha256(scene_path),
        "场次白模必须与本轮输出相同")
    add("inputs:shots", manifest.get("shots_sha256") == json_fingerprint(spec), "镜头参数必须与本轮输出相同")
    add("inputs:render", manifest.get("render_settings") == settings, "帧率与分辨率必须与白模相同")
    records = manifest.get("shots") or []
    add("file:shot-list", [r.get("id") for r in records] == [s["id"] for s in spec["shots"]], "逐镜清单及顺序一致")
    by_id = {r.get("id"): r for r in records}
    wanted_pngs, wanted_videos = set(), set()
    fps = settings["fps"]
    for shot in spec["shots"]:
        ident = shot["id"]
        record = by_id.get(ident, {})
        times = shot_still_times(shot)
        stills = record.get("stills") or []
        add(f"file:{ident}:stills", len(stills) == len(times), "静帧数必须与指定时刻一致")
        for index, time in enumerate(times):
            name = reference_name(shot, index, time)
            wanted_pngs.add(name)
            path = output_dir / "reference-frames" / name
            saved = stills[index] if index < len(stills) else None
            ok = _file_matches(path, saved)
            if ok:
                try:
                    ok = (saved.get("time") == time and saved.get("frame") == round(time * fps) + 1
                          and saved.get("camera") == f"ShotCamera.{ident}"
                          and png_size(path) == (settings["width"], settings["height"]))
                except OutputContractError:
                    ok = False
            add(f"file:{ident}:{name}", ok, "准确文件、时刻、相机、尺寸与内容哈希一致")
        video = record.get("video")
        if shot.get("video"):
            name = f"{ident}_previs.mp4"
            wanted_videos.add(name)
            path = output_dir / name
            ok, detail = _file_matches(path, video), "准确视频文件与内容哈希一致"
            if ok:
                try:
                    info = probe_video(path)
                    first, last = shot_frame_range(shot, fps)
                    count = last - first + 1
                    ok = (info["frames"] == count and abs(info["fps"] - fps) < 1e-6
                          and abs(info["duration"] - count / fps) < 1e-4
                          and (info["width"], info["height"]) == (settings["width"], settings["height"]))
                    detail = f"视频探测：{info}；期望 {count} 帧"
                except OutputContractError as exc:
                    ok, detail = False, str(exc)
            add(f"file:{ident}:video", ok, detail)
        else:
            add(f"file:{ident}:video", video is None, "静态镜不得登记视频")
    add("file:reference-frames", {p.name for p in (output_dir / "reference-frames").glob("*.png")} == wanted_pngs,
        "只包含本轮要求的参考帧")
    add("file:videos", {p.name for p in output_dir.glob("*.mp4")} == wanted_videos, "只包含本轮要求的视频")
    return checks


def check_build_outputs(recipe: dict, output_dir: Path, settings: dict) -> list[dict]:
    checks = []
    def add(ident, ok, detail):
        checks.append({"id": ident, "ok": bool(ok), "detail": detail})
    try:
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        add("file:manifest", False, str(exc))
        return checks
    fingerprints = {manifest.get("source_recipe_sha256"), manifest.get("recipe_sha256")}
    add("inputs:recipe", json_fingerprint(recipe) in fingerprints,
        "配方必须与本轮构建一致；旧清单缺少对应证据时不能验收")
    add("inputs:render", manifest.get("render_settings") == settings, "帧率与尺寸必须与场景相同")
    records = {Path(record["path"]).resolve(): record for record in manifest.get("artifacts", []) if record.get("path")}
    try:
        wanted = build_output_plan(recipe)
    except OutputContractError as exc:
        add("inputs:outputs", False, str(exc))
        return checks
    outputs = recipe.get("outputs", {})
    fps = settings["fps"]
    views = {view["id"]: view for view in outputs.get("reference_views", [])}
    emitted_views = {view["reference_id"]: view for view in manifest.get("reference_views", [])}
    add("file:reference-list", set(views) == set(emitted_views), "参考视图编号与本轮配方一致")
    for ident, view in views.items():
        saved = emitted_views.get(ident, {})
        add(f"reference:{ident}", saved.get("time") == float(view["time"])
            and saved.get("frame") == round(float(view["time"]) * fps) + 1
            and saved.get("shot_id") == view.get("shot_id"), "参考图对应准确镜号与时刻")
    for relative, kind in wanted.items():
        path = output_dir / relative
        saved = records.get(path.resolve())
        ok, detail = _file_matches(path, saved), "准确文件、类型与内容哈希一致"
        ok = ok and saved.get("type") == kind
        if ok and path.suffix == ".png":
            try:
                ok = png_size(path) == (settings["width"], settings["height"])
            except OutputContractError as exc:
                ok, detail = False, str(exc)
        if ok and path.suffix == ".mp4":
            try:
                info = probe_video(path)
                first, last = build_video_frame_range(recipe, fps, topdown=kind == "topdown_video")
                count = last - first + 1
                ok = (info["frames"] == count and abs(info["fps"] - fps) < 1e-6
                      and abs(info["duration"] - count / fps) < 1e-4
                      and (info["width"], info["height"]) == (settings["width"], settings["height"]))
                detail = f"视频探测：{info}；期望 {count} 帧"
            except OutputContractError as exc:
                ok, detail = False, str(exc)
        add(f"file:{relative}", ok, detail)
    actual = {path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*")
              if path.suffix.lower() in {".png", ".mp4", ".blend"} and path.is_file()
              and path.parent.name != "depth"}
    add("file:output-list", actual == set(wanted), "输出只包含本轮指定预演文件（深度输入除外）")
    add("file:manifest-list", {path for path in records} == { (output_dir / path).resolve() for path in wanted },
        "文件登记与本轮指定输出完整对应")
    return checks
