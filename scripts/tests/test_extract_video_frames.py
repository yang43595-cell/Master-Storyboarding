from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image
import pytest

from scripts import extract_video_frames as frames
from scripts.core.io import read_json, sha256_file


def _make_clip(path, source="testsrc2=size=96x64:rate=4:duration=2"):
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", source,
        "-c:v", "mpeg4", "-threads", "1", str(path),
    ], check=True, capture_output=True)


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("本地媒体实测需要 ffmpeg 和 ffprobe")
    path = tmp_path / "短片 素材%.mp4"
    _make_clip(path)
    return path


def test_exact_outputs_and_verified_reuse(video, tmp_path, monkeypatch, capsys):
    out = tmp_path / "取证 输出%"
    args = ["--video", str(video), "--out-dir", str(out), "--at", "1.75", "--at", "0.1", "--at", "1.75"]
    assert frames.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["reused"] is False
    assert [row["requested_seconds"] for row in first["frames"]] == [0.1, 1.75]
    assert [row["actual_seconds"] for row in first["frames"]] == [0.25, 1.75]
    assert Path(first["manifest"]).is_file()
    assert read_json(Path(first["manifest"]))["frames"][0]["path"] == "frame-0001.png"
    with Image.open(first["contact_sheet"]["path"]) as sheet:
        assert sheet.size == (492, 248)
        sheet.verify()
    assert all(row["sha256"] == sha256_file(Path(row["path"])) for row in first["frames"])

    def unexpected_decode(*args, **kwargs):
        pytest.fail("同输入有效文件应直接复用")

    monkeypatch.setattr(frames, "_run", unexpected_decode)
    assert frames.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {**first, "reused": True}


@pytest.mark.parametrize("damage", ["removed_manifest", "removed_frame", "altered_sheet"])
def test_incomplete_or_changed_batch_is_preserved(video, tmp_path, damage):
    out = tmp_path / "frames"
    first = frames.extract_video_frames(video, out, [0])
    old_batch = Path(first["manifest"]).parent
    if damage == "removed_manifest":
        Path(first["manifest"]).unlink()
    elif damage == "removed_frame":
        Path(first["frames"][0]["path"]).unlink()
    else:
        Path(first["contact_sheet"]["path"]).write_bytes(b"changed")
    old_bytes = {path.name: path.read_bytes() for path in old_batch.iterdir()}

    repaired = frames.extract_video_frames(video, out, [0])
    assert repaired["manifest"] != first["manifest"]
    assert repaired["preserved_invalid_outputs"] == [str(old_batch)]
    assert old_bytes == {path.name: path.read_bytes() for path in old_batch.iterdir()}
    assert frames.extract_video_frames(video, out, [0]) == {**repaired, "reused": True}


def test_same_source_path_with_new_video_uses_new_batch(video, tmp_path):
    out = tmp_path / "frames"
    first = frames.extract_video_frames(video, out, [0])
    previous_manifest = Path(first["manifest"]).read_bytes()
    replacement = tmp_path / "revised.mp4"
    _make_clip(replacement, "color=red:size=96x64:rate=4:duration=2")
    replacement.replace(video)
    second = frames.extract_video_frames(video, out, [0])
    assert first["source"]["sha256"] != second["source"]["sha256"]
    assert first["manifest"] != second["manifest"]
    assert Path(first["manifest"]).read_bytes() == previous_manifest


def test_variable_rate_and_delayed_video_report_real_timestamps(video, tmp_path):
    delayed = tmp_path / "变速画面晚于音轨.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(video),
        "-f", "lavfi", "-i", "anullsrc=r=44100:d=5", "-map", "0:v:0", "-map", "1:a:0",
        "-vf", "select='eq(n,0)+eq(n,1)+eq(n,4)+eq(n,7)',setpts=PTS+3/TB",
        "-fps_mode", "vfr", "-c:v", "ffv1", "-c:a", "pcm_s16le", "-threads", "1", str(delayed),
    ], check=True, capture_output=True)
    report = frames.extract_video_frames(delayed, tmp_path / "frames", [0, 3.6, 4.75])
    assert [frame["actual_seconds"] for frame in report["frames"]] == [3.0, 4.0, 4.75]


@pytest.mark.parametrize(
    ("time_seconds", "error"),
    [(1.99, "未解码到画面"), (2, "未解码到画面"), (2.1, "超出视频画面结束时间"),
     (float("nan"), "非负且有限"), (-0.1, "非负且有限")],
)
def test_out_of_range_or_invalid_times_never_publish(video, tmp_path, time_seconds, error):
    out = tmp_path / "frames"
    with pytest.raises(ValueError, match=error):
        frames.extract_video_frames(video, out, [0, time_seconds])
    assert not any(out.glob("frames-*"))
    assert not any(out.glob(".video-frames-*"))


def test_bad_input_is_an_explicit_error(tmp_path, capsys):
    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(b"invalid media")
    if not shutil.which("ffprobe"):
        pytest.skip("本地媒体实测需要 ffprobe")
    out = tmp_path / "frames"
    code = frames.main(["--video", str(damaged), "--out-dir", str(out), "--at", "0"])
    assert code == 1 and "ffprobe 失败" in capsys.readouterr().err
    assert not any(out.glob("frames-*"))


def test_interrupted_sheet_creation_is_retryable(video, tmp_path, monkeypatch):
    out = tmp_path / "frames"

    def fail_sheet(*args):
        raise ValueError("合成过程中断")

    with monkeypatch.context() as patch:
        patch.setattr(frames, "_make_sheet", fail_sheet)
        with pytest.raises(ValueError, match="合成过程中断"):
            frames.extract_video_frames(video, out, [0])
    assert not any(out.glob("frames-*"))
    assert not any(out.glob(".video-frames-*"))
    assert frames.extract_video_frames(video, out, [0])["ok"]


def test_concurrent_identical_requests_share_one_batch(video, tmp_path):
    out = tmp_path / "frames"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result() for future in (
            pool.submit(frames.extract_video_frames, video, out, [0, 1]),
            pool.submit(frames.extract_video_frames, video, out, [1, 0]),
        )]
    assert first["manifest"] == second["manifest"]
    assert sorted([first["reused"], second["reused"]]) == [False, True]


def test_interval_batches_return_before_next_window_and_preserve_completed_window(video, tmp_path):
    from scripts.extract_video_batches import batches

    result = batches(video, tmp_path / "批次", 0, 1.5, 1, 4)
    first = next(result)
    directory = Path(first["directory"])
    assert first["frames"] == 4 and first["end"] == 1 and first["audio"] is None
    assert not (directory.parent / "batch-0002").exists()
    second = next(result)
    assert (second["start"], second["end"], second["frames"]) == (1, 1.5, 2)
    assert read_json(directory / "manifest.json")["frames"] == 4
    assert list(result) == []
