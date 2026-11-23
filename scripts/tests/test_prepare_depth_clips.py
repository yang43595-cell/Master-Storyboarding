import json
from pathlib import Path

import pytest

from scripts import prepare_depth_clips as depth


def test_final_depth_cache_checks_window_and_bytes(tmp_path, monkeypatch):
    src = tmp_path / "master.mp4"
    src.write_bytes(b"source")
    ref = dict(role="depth", sha256=depth.checksum(src), path=src.name,
               frame_count=10, source_start_frame=100)
    segment = dict(id="SEG-02", mode="depth", motion="origin",
                   reference_frames=[2, 5], frames=[102, 105])
    plan = dict(fps=30, references={"origin": ref}, segments=[segment, {"mode": "product"}])
    encodes = []

    def encode(args, check):
        encodes.append(args)
        Path(args[-1]).write_bytes(b"encoded")

    def probe(args):
        info = dict(avg_frame_rate="30", r_frame_rate="30", codec_type="video",
                    width=540, height=960, nb_read_frames="3", start_time="0")
        return json.dumps({"streams": [info]}).encode()

    monkeypatch.setattr(depth.subprocess, "run", encode)
    monkeypatch.setattr(depth.subprocess, "check_output", probe)
    output = tmp_path / "clips"
    rows = depth.export(tmp_path, plan, output)
    assert rows["SEG-02"]["local_frames"] == [0, 3]
    assert rows == depth.export(tmp_path, plan, output)
    assert len(encodes) == 1
    assert "trim=start_frame=2:end_frame=5" in encodes[0][encodes[0].index("-vf") + 1]
    (tmp_path / rows["SEG-02"]["path"]).write_bytes(b"replaced")
    with pytest.raises(ValueError, match="发生变化"):
        depth.export(tmp_path, plan, output)
    segment["frames"] = [102, 106]
    with pytest.raises(ValueError, match="帧窗"):
        depth.export(tmp_path, plan, output)
