"""Materialize the final, silent motion reference per depth segment before writing drafts."""
import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(project, plan, output, ffmpeg="ffmpeg", ffprobe="ffprobe", short_edge=540, *, segment_ids=None):
    project = project.resolve()
    output = output.resolve()
    if not output.is_relative_to(project) or short_edge <= 0 or short_edge % 2:
        raise ValueError("输出须在项目内，短边须为正偶数")
    output.mkdir(parents=True, exist_ok=True)
    fps = Fraction(str(plan["fps"]))
    if not 0 < fps <= 240:
        raise ValueError("分段帧率无效")
    segments = [row for row in plan["segments"] if row["mode"] == "depth"]
    if segment_ids is not None:
        requested = set(segment_ids)
        unknown = requested - {row["id"] for row in segments}
        if not requested or unknown:
            raise ValueError("须指定计划中的深度段：" + "、".join(sorted(unknown)))
        segments = [row for row in segments if row["id"] in requested]
    hashes, clips, source_rates = {}, {}, {}
    with TemporaryDirectory(dir=output) as scratch:
        for row in segments:
            sid = row["id"]
            if not re.fullmatch(r"[\w-]+", sid) or sid in clips:
                raise ValueError("深度段 ID 无效或重复")
            ref = plan["references"][row["motion"]]
            src = (project / ref["path"]).resolve()
            if ref["role"] != "depth" or not src.is_relative_to(project):
                raise ValueError(f"{sid} 深度来源错误")
            if src not in hashes:
                hashes[src] = checksum(src)
                stream = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-select_streams",
                    "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "json", str(src)]))["streams"][0]
                source_rates[src] = (Fraction(stream["avg_frame_rate"]), Fraction(stream["r_frame_rate"]))
            if source_rates[src] != (fps, fps):
                raise ValueError(f"{sid} 母片需先按编导时基核对并规范化")
            a, b = row["reference_frames"]
            if (hashes[src] != ref["sha256"] or type(a) is not int or type(b) is not int
                    or not 0 <= a < b <= ref["frame_count"]
                    or [ref["source_start_frame"] + k for k in (a, b)] != row["frames"]):
                raise ValueError(f"{sid} 来源或帧窗不一致")
            spec = json.dumps([ref["sha256"], a, b, str(fps), short_edge])
            dest = output / f"{sid}_{hashlib.sha256(spec.encode()).hexdigest()[:20]}.mp4"
            tmp = Path(scratch) / dest.name
            stamp = dest.with_suffix(".sha256")
            cached = dest.exists() and stamp.is_file()
            if cached and checksum(dest) != stamp.read_text().strip():
                raise ValueError(f"{sid} 已交付文件发生变化")
            if not cached:
                scale = f"scale='if(lte(iw,ih),{short_edge},-2)':'if(lte(iw,ih),-2,{short_edge})'"
                subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(src), "-map", "0:v:0",
                                "-an", "-vf", f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,{scale}",
                                "-r", str(fps), "-fps_mode", "cfr", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                "-movflags", "+faststart", str(tmp)], check=True)
            media = tmp if tmp.exists() else dest
            info = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-count_frames",
                "-show_entries", "stream=codec_type,width,height,nb_read_frames,avg_frame_rate,start_time",
                "-of", "json", str(media)]))["streams"]
            video, = info
            if (video["codec_type"] != "video" or int(video["nb_read_frames"]) != b - a
                    or Fraction(video["avg_frame_rate"]) != fps or Fraction(video["start_time"]) != 0
                    or min(video["width"], video["height"]) != short_edge):
                raise ValueError(f"{sid} 实际尺寸、帧率、起点或帧数错误")
            sha = stamp.read_text().strip() if cached else checksum(media)
            if tmp.exists():
                try:
                    os.link(tmp, dest)
                except FileExistsError:
                    if checksum(dest) != sha:
                        raise ValueError(f"{sid} 并发输出不一致")
                meta = Path(scratch) / stamp.name
                meta.write_text(sha + "\n")
                os.replace(meta, stamp)
            clips[sid] = dict(path=str(dest.relative_to(project)), sha256=sha, source_sha256=ref["sha256"],
                              source_frames=row["frames"], local_frames=[0, b - a], size=[video["width"], video["height"]])
    return clips


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "plan", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--short-edge", type=int, default=540)
    parser.add_argument("--segment-id", action="append", help="局部导出指定深度段，可重复")
    args = parser.parse_args()
    print(json.dumps(export(args.project, json.loads(args.plan.read_text()), args.output,
                            args.ffmpeg, args.ffprobe, args.short_edge, segment_ids=args.segment_id), ensure_ascii=False, indent=2))
