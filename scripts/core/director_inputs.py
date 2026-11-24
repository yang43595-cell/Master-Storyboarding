"""Prepare director source material and bind local rewrite jobs to their inputs."""

from pathlib import Path

from .artifacts import digest, project_path
from .asset_refs import effective, read_choices
from .io import read_json, sha256_json, write_json

PLAN = Path("01_原始资料/复刻分段.json")
JOBS = Path("00_项目管理/执行上下文/批次/handoff-director/局部改戏")


def sources(project, entry, paragraph_ids=None, dialogue_ids=None):
    path = project_path(project, entry["path_or_url"])
    if digest(path) != entry["checksum"]:
        raise ValueError("编导源文件已变更")
    story = read_json(path)
    paragraphs = [
        p for p in story["story_paragraphs"] if paragraph_ids is None or p["paragraph_id"] in paragraph_ids
    ]
    assets = effective(story, read_choices(project))
    result = []
    for p in paragraphs:
        pid = p["paragraph_id"]
        result.append(
            dict(
                paragraph_id=pid,
                text=p["text"],
                dialogues=[
                    d
                    for d in story["dialogues"]
                    if d["source_paragraph_id"] == pid
                    and (dialogue_ids is None or d["dialogue_id"] in dialogue_ids)
                ],
                assets=[
                    {"asset_id": a["asset_id"], "name": a["name"]}
                    for a in assets
                    if pid in a["source_paragraph_ids"]
                ],
            )
        )
    packet = dict(
        source_narrative={k: entry[k] for k in ("artifact_id", "revision", "path_or_url", "checksum")},
        paragraphs=result,
    )
    if paragraph_ids is None:
        packet["package_fields"] = dict(
            source_narrative=dict(
                artifact_id=entry["artifact_id"],
                logical_id="narrative.package",
                revision=entry["revision"],
                path=entry["path_or_url"],
            ),
            source_paragraph_ids=[p["paragraph_id"] for p in paragraphs],
            source_dialogue_ids=[d["dialogue_id"] for d in story["dialogues"]],
            source_asset_requirement_ids=[a["asset_id"] for a in assets],
        )
    return packet


def job_path(project, sid):
    return project / JOBS / (sha256_json(sid) + ".json")


def job_input(project, plan, index, entry):
    rows = plan["segments"]
    row = rows[index]
    keys = [row["motion"]] if row.get("motion") else []
    keys.extend(row.get("appearance", []))
    neighbors = []
    for i in (index - 1, index + 1):
        if 0 <= i < len(rows):
            other = rows[i]
            neighbors.append(
                {
                    k: other[k]
                    for k in ("id", "frames", "event", "entry_state", "exit_state", "appearance")
                    if k in other
                }
            )
    return dict(
        segment=row,
        adjacent=neighbors,
        fps=plan["fps"],
        references={k: plan["references"][k] for k in keys},
        **sources(project, entry, row["paragraph_ids"], row["dialogue_ids"]),
    )


def dispatch(project, entry, segment_ids):
    plan = read_json(project / PLAN)
    paths = []
    for i, row in enumerate(plan["segments"]):
        if row["id"] in segment_ids:
            job = job_input(project, plan, i, entry)
            path = job_path(project, row["id"])
            write_json(path, {"job_sha256": sha256_json(job), "input": job})
            paths.append(str(path.relative_to(project)))
    return paths


def return_binding(row, refs, job_hash):
    fields = (
        "id",
        "frames",
        "paragraph_ids",
        "dialogue_ids",
        "change",
        "mode",
        "motion",
        "appearance",
        "reference_frames",
        "decision",
        "entry_state",
        "exit_state",
        "reference_use",
    )
    selected = [row.get(k) for k in fields]
    motion, appearance = row["motion"], row["appearance"]
    if not isinstance(appearance, list) or any(not isinstance(key, str) or not key for key in appearance):
        raise ValueError("局部回传的外观参考无效")
    if row["mode"] == "product-display":
        if motion is not None or row["reference_frames"] != [] or not appearance:
            raise ValueError("商品展示回传须有外观参考且不带动作或参考帧")
        keys = appearance
    else:
        if not isinstance(motion, str) or not motion:
            raise ValueError("局部回传缺准确动作参考")
        keys = [motion, *appearance]
    return sha256_json([job_hash, selected, [refs[key] for key in keys]])
