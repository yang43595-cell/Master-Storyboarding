"""Return one dispatched director change to the recreation plan."""

import argparse
from copy import deepcopy
from pathlib import Path

if __package__:
    from .core import director_inputs as di, production_handoff as handoff
    from .core.recreation_segments import segment_steps
    from .core.io import project_lock, read_json, sha256_json, write_json
else:
    from core import director_inputs as di, production_handoff as handoff
    from core.recreation_segments import segment_steps
    from core.io import project_lock, read_json, sha256_json, write_json


def submit(root, result):
    sid = result["segment_id"]
    with project_lock(root):
        handoff.require_production_manifest(root)
        plan = read_json(root / di.PLAN)
        index = next(i for i, s in enumerate(plan["segments"]) if s["id"] == sid)
        row = plan["segments"][index]
        job = read_json(di.job_path(root, sid))
        job_hash = result["job_sha256"]
        if job_hash != job["job_sha256"] or job_hash != sha256_json(job["input"]):
            raise ValueError("回传的任务版本不符")
        entry, story = handoff._package(
            root, handoff.load_manifest(root), handoff.load_project_state(root), handoff.NARRATIVE
        )
        candidate = deepcopy(plan)
        candidate["segments"][index] = job["input"]["segment"]
        if sha256_json(di.job_input(root, candidate, index, entry)) != job_hash:
            raise ValueError("编导、原参考或相邻接缝已变化，须重新派单")
        updated = dict(candidate["segments"][index])
        for key in ("decision", "entry_state", "exit_state", "reference_use"):
            if not isinstance(result.get(key), str) or not result[key].strip():
                raise ValueError("局部回传缺 " + key)
        for key in (
            "decision",
            "entry_state",
            "exit_state",
            "reference_use",
            "mode",
            "motion",
            "reference_frames",
            "appearance",
        ):
            updated[key] = result[key]
        if updated.get("requires_new_motion"):
            old_motion = job["input"]["references"].get(job["input"]["segment"].get("motion"))
            if old_motion and plan["references"][updated["motion"]]["sha256"] == old_motion["sha256"]:
                raise ValueError("本段工序变更要求新的动作参考")
        updated["director_return"] = di.return_binding(updated, plan["references"], job_hash)
        retry = updated == row
        if not retry and row != job["input"]["segment"]:
            raise ValueError("段落已变化，请从当前版本重新派单")
        if not retry:
            handoff.require_production_handoff(root, "director", segment_ids=[sid])
        candidate["segments"][index] = updated
        if not any(
            s["action"] == "video" and s["status"] == "ready" and sid in s["segment_ids"]
            for s in segment_steps(root, story, entry, plan=candidate)
        ):
            raise ValueError("回传的参考或时间窗未通过视频入口")
        if not retry:
            write_json(root / di.PLAN, candidate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    submit(args.project.resolve(), read_json(args.result))
