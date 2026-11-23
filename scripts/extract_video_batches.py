"""Publish sampled frames and audio from a local reference as each time window completes."""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

if __package__:
    from .extract_video_frames import _file_stamp, _video_end
else:
    from extract_video_frames import _file_stamp, _video_end


def _subtitle_changes(batch, start, fps, previous):
    """Collapse consecutive identical OCR readings, retaining a locator into the raw evidence."""
    changes = []
    with (batch / "subtitles.jsonl").open(encoding="utf-8") as observations:
        for line in observations:
            row = json.loads(line)
            texts = [item["text"] for item in row["v"]]
            if texts and texts != previous:
                frame = row["i"]
                changes.append({"sample_seconds": round(start + (frame - 1) / fps, 6),
                                "frame": f"{batch.name}/{frame:04d}.jpg", "texts": texts})
            previous = texts
    return changes, previous


def batches(video, output, start, end, seconds, fps, ocr_region=None):
    video = video.resolve(strict=True)
    if not (math.isfinite(end) and 0 <= start < end and seconds > 0 and fps > 0):
        raise ValueError("取材范围、批次秒数或采样频率无效")
    stamp = _file_stamp(video)
    duration = _video_end(video)
    if duration is not None and end > duration + 0.001:
        raise ValueError("取材终点超出原片")
    probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                     "-show_entries", "stream=index", "-of", "csv=p=0", str(video)])
    output.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix="video-batches-", dir=output)).resolve()
    with TemporaryDirectory(dir=run) as work:
        recognizer = Path(work) / "recognize"
        if ocr_region:
            subprocess.run(["swiftc", str(Path(__file__).with_name("recognize_subtitles.swift")),
                            "-o", str(recognizer)], check=True)
        previous_texts = []
        index = 0
        while start < end:
            stop = min(start + seconds, end)
            index += 1
            target = run / f"batch-{index:04d}"
            stage = Path(work) / target.name
            stage.mkdir()
            command = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-ss", str(start),
                       "-i", str(video), "-map", "0:V:0", "-t", str(stop-start),
                       "-vf", f"fps={fps},scale=540:-2", "-q:v", "3", str(stage / "%04d.jpg")]
            if probe.strip():
                command += ["-map", "0:a:0", "-t", str(stop-start), "-ac", "1", "-ar", "16000",
                            str(stage / "audio.wav")]
            subprocess.run(command, check=True)
            frames = sorted(stage.glob("*.jpg"))
            if not frames:
                raise ValueError("本批未解码到画面")
            if ocr_region:
                with (stage / "subtitles.jsonl").open("w") as stream:
                    subprocess.run([str(recognizer), str(stage), "1", str(len(frames)), ocr_region],
                                   stdout=stream, check=True)
            if _file_stamp(video) != stamp:
                raise ValueError("取证期间原片发生变化")
            record = dict(source=str(video), source_stamp=stamp, start=start, end=stop,
                          samples_per_second=fps, frames=len(frames), directory=str(target),
                          audio="audio.wav" if probe.strip() else None,
                          subtitles="subtitles.jsonl" if ocr_region else None)
            if ocr_region:
                subtitle_index = run / "subtitle-index.jsonl"
                changes, previous_texts = _subtitle_changes(stage, start, fps, previous_texts)
                record["subtitle_index"] = str(subtitle_index)
            (stage / "manifest.json").write_text(json.dumps(record, ensure_ascii=False))
            stage.rename(target)
            if ocr_region:
                with subtitle_index.open("a", encoding="utf-8") as stream:
                    stream.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in changes)
            yield record
            start = stop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--batch-seconds", type=int, default=10)
    parser.add_argument("--fps", type=int, default=4)
    parser.add_argument("--ocr-region", help="macOS Vision 字幕区，归一化 x,y,width,height，左下为原点")
    args = parser.parse_args()
    if args.ocr_region:
        try:
            x, y, w, h = map(float, args.ocr_region.split(","))
            if not (0 <= x < x+w <= 1 and 0 <= y < y+h <= 1):
                raise ValueError()
        except ValueError:
            parser.error("OCR 区域须为画面内的归一化矩形")
    try:
        for record in batches(args.video, args.out_dir, args.start, args.end, args.batch_seconds,
                              args.fps, args.ocr_region):
            print(json.dumps(record, ensure_ascii=False), flush=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
