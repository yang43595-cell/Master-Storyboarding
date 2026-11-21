"""Extract requested local video frames and return an ordered contact sheet and manifest."""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__:
    from .core.io import file_lock, read_json, sha256_file, sha256_json, write_json
else:
    from core.io import file_lock, read_json, sha256_file, sha256_json, write_json


MANIFEST = "manifest.json"
SHEET = "contact-sheet.png"
FRAME_PTS = re.compile(r"Parsed_showinfo_\d+[^\n]*?\bn:\s*0\s+pts:\s*(-?\d+)")
TIME_BASE = re.compile(r"Parsed_showinfo_\d+[^\n]*?config in time_base:\s*(\d+/\d+)")
CELL_SIZE = 240


def _frame_name(index):
    return f"frame-{index:04d}.png"


def _file_stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _run(tool, args, *, cwd=None):
    try:
        result = subprocess.run(
            [tool, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=120,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"当前 PATH 找不到 {tool}，请沿用已核实的本机媒体工具环境") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{tool} 本地处理超时") from exc
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        summary = "\n".join(detail[-3:]) if detail else "无诊断文本"
        raise ValueError(f"{tool} 失败（退出码 {result.returncode}）：{summary}")
    return result


def _video_end(video):
    result = _run("ffprobe", [
        "-v", "error", "-protocol_whitelist", "file", "-select_streams", "V:0",
        "-show_entries", "stream=index,start_time,duration:format=start_time", "-of", "json", str(video),
    ])
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams", [])
    if not streams:
        raise ValueError("输入没有可抽帧的视频流")
    value = streams[0].get("duration")
    if value in (None, "N/A"):
        return None
    start = streams[0].get("start_time")
    playback_start = metadata.get("format", {}).get("start_time")
    if start in (None, "N/A") or playback_start in (None, "N/A"):
        return None
    duration = float(value)
    end = float(start) - float(playback_start) + duration
    return end if math.isfinite(end) and duration > 0 and end > 0 else None


def _extract_frame(video, time_seconds, target):
    result = _run("ffmpeg", [
        "-hide_banner", "-nostdin", "-loglevel", "info", "-xerror", "-protocol_whitelist", "file",
        "-ss", f"{time_seconds:.6f}", "-copyts", "-start_at_zero", "-i", str(video),
        "-map", "0:V:0", "-vf", "showinfo", "-frames:v", "1",
        "-c:v", "png", "-pix_fmt", "rgb24", "-threads", "1", "-update", "1", target.name,
    ], cwd=target.parent)
    if not target.is_file() or not target.stat().st_size:
        raise ValueError(f"{time_seconds:g} 秒及之后未解码到画面，请选择片尾实际有帧的时间")
    pts = FRAME_PTS.search(result.stderr)
    time_base = TIME_BASE.search(result.stderr)
    if not pts or not time_base:
        raise ValueError(f"{time_seconds:g} 秒附近已解码到画面，未能核实实际帧时间")
    return float(int(pts[1]) * Fraction(time_base[1]))


def _make_sheet(directory, count):
    columns = min(count, 3)
    rows = (count + columns - 1) // columns
    size = CELL_SIZE
    filters = (
        f"scale={size}:{size}:force_original_aspect_ratio=decrease,"
        f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"tile={columns}x{rows}:nb_frames={count}:padding=4:margin=4"
    )
    _run("ffmpeg", [
        "-hide_banner", "-nostdin", "-loglevel", "error", "-xerror",
        "-f", "image2", "-start_number", "1", "-i", "frame-%04d.png", "-vf", filters,
        "-frames:v", "1", "-c:v", "png", "-threads", "1", "-update", "1", SHEET,
    ], cwd=directory)
    sheet = directory / SHEET
    if not sheet.is_file() or not sheet.stat().st_size:
        raise ValueError("抽出的画面没有形成可读取的联系表")


def _matches_file(directory, receipt, name):
    path = directory / name
    return (
        receipt["path"] == name and not path.is_symlink() and path.is_file()
        and path.stat().st_size > 0 and sha256_file(path) == receipt["sha256"]
    )


def _read_complete(directory, request):
    try:
        path = directory / MANIFEST
        if directory.is_symlink() or path.is_symlink():
            return None
        receipt = read_json(path)
        frames = receipt["frames"]
        times = request["requested_seconds"]
        if (receipt["request"] != request or len(frames) != len(times)
                or not _matches_file(directory, receipt["contact_sheet"], SHEET)):
            return None
        for index, (frame, time_seconds) in enumerate(zip(frames, times), 1):
            if (frame["requested_seconds"] != time_seconds
                    or not math.isfinite(frame["actual_seconds"])
                    or not _matches_file(directory, frame, _frame_name(index))):
                return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _result(directory, receipt, *, reused, preserved):
    frames = [{**frame, "path": str(directory / frame["path"])} for frame in receipt["frames"]]
    sheet = receipt["contact_sheet"]
    return {
        "ok": True,
        "reused": reused,
        "source": receipt["request"]["source"],
        "manifest": str(directory / MANIFEST),
        "contact_sheet": {**sheet, "path": str(directory / sheet["path"])},
        "frames": frames,
        "preserved_invalid_outputs": preserved,
    }


def extract_video_frames(video, out_dir, times):
    if not times or any(not math.isfinite(time) or time < 0 for time in times):
        raise ValueError("时间点须为非负且有限的秒数")
    times = sorted({round(time, 6) for time in times})
    video = video.resolve()
    if not video.is_file():
        raise ValueError(f"原片不可读：{video}")
    out_dir = out_dir.resolve()
    stamp = _file_stamp(video)
    request = {
        "schema_version": "1.0",
        "source": {"path": str(video), "sha256": sha256_file(video)},
        "requested_seconds": times,
    }
    base = "frames-" + sha256_json(request)[:20]
    preserved = []
    with file_lock(out_dir / f".{base}.lock", timeout_seconds=60):
        directory = out_dir / base
        while directory.exists() or directory.is_symlink():
            receipt = _read_complete(directory, request)
            if receipt:
                if _file_stamp(video) != stamp:
                    raise ValueError("处理期间原片有变动，请核准原件后重试")
                return _result(directory, receipt, reused=True, preserved=preserved)
            preserved.append(str(directory))
            directory = out_dir / f"{base}-{len(preserved) + 1}"

        end = _video_end(video)
        if end is not None and times[-1] > end:
            raise ValueError(f"{times[-1]:g} 秒超出视频画面结束时间 {end:g} 秒，请填写片尾前的时间")
        with TemporaryDirectory(prefix=".video-frames-", dir=out_dir) as staging_path:
            staging = Path(staging_path)
            frames = []
            for index, time_seconds in enumerate(times, 1):
                image = staging / _frame_name(index)
                actual_seconds = _extract_frame(video, time_seconds, image)
                frames.append({
                    "path": image.name,
                    "requested_seconds": time_seconds,
                    "actual_seconds": actual_seconds,
                    "sha256": sha256_file(image),
                })
            _make_sheet(staging, len(frames))
            if _file_stamp(video) != stamp:
                raise ValueError("处理期间原片有变动，请核准原件后重试")
            receipt = {
                "request": request,
                "frames": frames,
                "contact_sheet": {"path": SHEET, "sha256": sha256_file(staging / SHEET)},
            }
            write_json(staging / MANIFEST, receipt)
            directory.mkdir()
            # Publish the manifest last. Exclusive creation preserves any
            # preexisting file and an interrupted batch cannot count as complete.
            for name in [*(frame["path"] for frame in frames), SHEET, MANIFEST]:
                with (staging / name).open("rb") as source, (directory / name).open("xb") as target:
                    shutil.copyfileobj(source, target)
        return _result(directory, receipt, reused=False, preserved=preserved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="准确的本地视频文件")
    parser.add_argument("--out-dir", type=Path, required=True, help="本轮取证输出目录")
    parser.add_argument("--at", type=float, action="append", required=True, metavar="SECONDS",
                        help="求取该时间或之后的首个可解码帧。可重复传入多个时间点")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        result = extract_video_frames(args.video, args.out_dir, args.at)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
