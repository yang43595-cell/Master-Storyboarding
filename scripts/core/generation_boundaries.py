"""Read-only checks for current generation_segments, not an automatic splitter.

The author still judges rhythm, actual model support and image contents. This
checks declared reasons against exact source fields/bytes and never invents a
duration ceiling, a scene boundary, a reference or an approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from .quality_evidence import source_shot_ids, reference_shot_ids


def _need(value, message):
    if not value:
        raise ValueError("generation_boundary: " + message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _evidence_file(proof, root):
    _need(isinstance(proof, dict) and all(_text(proof.get(k)) for k in
          ("path", "sha256", "locator", "checked_at")), "缺少本轮限制核验的原始证据定位与字节")
    path = (root / proof["path"]).resolve()
    _need(path.is_relative_to(root) and path.is_file() and path.stat().st_size > 0,
          "限制证据必须是项目内可读的真实文件")
    _need(hashlib.sha256(path.read_bytes()).hexdigest() == proof["sha256"], "限制证据 hash 已过期")


def _rows(payload):
    # Composite source snapshots may carry shot mappings without prompt prose;
    # formal adoption checks the complete submitted body independently.
    from .web_content import source_review_segments
    _need(isinstance(payload, dict) and ("generation_segments" in payload or "creative_sections" in payload),
          "须使用现行 generation_segments，不使用历史专用分段格式")
    rows = source_review_segments(payload)
    return rows


def _source_boundary(record, left, right, shots, reason):
    """Bind the rationale to an existing director field, never inferred prose."""
    _need(isinstance(record, dict), "拆段须引用准确导演边界")
    shot_id, field, quote = (record.get(k) for k in ("shot_id", "field", "quote"))
    _need(shot_id in (left[-1], right[0]) and _text(field) and _text(quote),
          "拆段依据须定位两侧准确镜号、字段及原文")
    # Only existing action/rhythm/cut fields can substantiate a boundary.
    _need(field.split(".")[0] in {"take", "cut", "rhythm", "continuity", "audio"},
          "导演边界须引用动作、节奏、完整对白或剪辑字段")
    if field.startswith("audio."):
        _need(reason in {"model_duration_exceeded", "execution_limit"}
              and re.fullmatch(r"audio\.dialogue\.\d+\.text", field),
              "完整句末只作承载限制下的连续切点，不自动证明新事件或时间省略")
    value = shots[shot_id][1]
    for key in field.split("."):
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            value = None
    _need(isinstance(value, str) and quote in value, "拆段依据不在准确导演字段原文中")


def _model_limit(payload, left, right, root):
    model = payload.get("selected_video_model")
    _need(isinstance(model, dict), "时长拆段缺少本轮已核模型能力")
    _need(all(_text(model.get(k)) for k in ("id", "platform", "entry_point", "reference_mode"))
          and model.get("confirmed_by_user") is True and _seconds(model.get("max_duration_seconds")),
          "模型、入口、参考模式、确认选择与真实时长上限不完整")
    _evidence_file(model.get("capability_evidence"), root)
    for row in (left, right):
        parameters = row.get("parameters", payload.get("platform", {}))
        _need(isinstance(parameters, dict) and parameters.get("model") == model["id"]
              and parameters.get("reference_mode", parameters.get("mode")) == model["reference_mode"],
              "拆段能力与实际段落模型/参考方式不一致")
        values = [row[k] for k in ("duration_seconds", "generation_seconds") if k in row]
        values += [parameters[k] for k in ("duration_seconds", "duration") if k in parameters]
        _need(values and all(_seconds(v) and v <= model["max_duration_seconds"] for v in values)
              and all(v == values[0] for v in values), "生成时长缺失、冲突或超过本轮已核上限")
    return model["max_duration_seconds"]


def _cutpoint(left, right):
    continuity = right.get("continuity")
    _need(isinstance(continuity, dict) and continuity.get("previous_segment_id") == left["segment_id"],
          "连续事件拆段缺少准确前段及共用切点参考")
    boundary = continuity.get("boundary")
    _need(isinstance(boundary, dict) and _text(boundary.get("state_id"))
          and _text(boundary.get("description")), "连续事件拆段缺少切点状态和具体描述")
    for side, row in (("previous", left), ("current", right)):
        boundary_shot = source_shot_ids(row)[-1 if side == "previous" else 0]
        hashes = boundary.get(side + "_sha256s")
        _need(isinstance(hashes, list) and hashes and all(_text(h) for h in hashes)
              and len(set(hashes)) == len(hashes), "切点须列两侧实际输入的准确参考 hash")
        refs = row.get("materials", row.get("candidate_upload_order", []))
        _need(isinstance(refs, list), "缺少本段实际素材清单")
        matched = set()
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            roles = ref.get("roles", [ref.get("role")])
            expected = {"state_reference", "end_frame" if side == "previous" else "start_frame"}
            if (isinstance(roles, list) and all(_text(r) for r in roles) and set(roles) & expected
                    and ref.get("state_id") == boundary["state_id"] and _text(ref.get("sha256"))
                    and boundary_shot in reference_shot_ids(ref, row)):
                matched.add(ref["sha256"])
        _need(set(hashes) <= matched, "基础环境/身份图不能冒充准确切点状态参考；须在两段实际输入中绑定")
    # Different views of the same state are allowed; the existing image-use and
    # canvas gates check their confirmed group/visual evidence and native bytes.


def validate_generation_boundaries(payload, storyboard, project):
    """Validate a full or partial current draft without writing/approving it.

    Missing facts may remain in a candidate, but this strict check fails until
    supplied. It does not auto-merge/split, demand an image for every shot, or
    impose model settings on an unsplit platform-neutral manuscript.
    """
    try:
        return _validate(payload, storyboard, Path(project).resolve())
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("generation_boundary: 分段依据或来源结构无效") from exc


def _validate(payload, storyboard, root):
    rows = _rows(payload)
    shots = {}
    for scene in storyboard.get("scenes", []):
        for shot in scene.get("shots", []):
            key = shot.get("shot_id")
            _need(_text(key) and key not in shots and _text(scene.get("scene_id")), "导演场景/镜号缺失或重复")
            shots[key] = (scene["scene_id"], shot)
    mapped = []
    order = {key: index for index, key in enumerate(shots)}
    previous = -1
    for row in rows:
        ids = source_shot_ids(row)
        _need(ids and all(key in shots for key in ids), "每个生成段必须映射准确导演镜号，不能省略映射绕过分段检查")
        for key in ids:
            _need(order[key] >= previous, "生成段来源顺序与导演顺序冲突")
            previous = order[key]
        mapped.append(ids)
    checked = 0
    for index, (left, right) in enumerate(zip(rows, rows[1:])):
        before, after = mapped[index:index + 2]
        if shots[before[-1]][0] != shots[after[0]][0]:
            continue
        split = right.get("same_scene_split")
        _need(isinstance(split, dict) and split.get("from_segment_id") == left["segment_id"]
              and _text(split.get("evidence")), "同场景默认合并；拆段须说明具体依据，不能按固定秒数、镜头数或图片数拆分")
        reason = split.get("reason")
        _need(reason in {"model_duration_exceeded", "execution_limit", "event_complete", "time_ellipsis"},
              "缺少真实承载限制或导演已有事件/时间边界的拆段理由")
        _source_boundary(split.get("source_boundary"), before, after, shots, reason)
        if reason == "model_duration_exceeded":
            limit = _model_limit(payload, left, right, root)
            estimate = split.get("estimated_scene_duration_seconds")
            _need(_seconds(estimate) and estimate > limit and _text(split.get("timing_basis")),
                  "待承载连续内容未证明超出模型上限，不能机械拆段")
        if reason == "execution_limit":
            _evidence_file(split.get("limitation_evidence"), root)
        if reason in {"model_duration_exceeded", "execution_limit"}:
            _cutpoint(left, right)
        checked += 1
    return {"segment_count": len(rows), "same_scene_boundaries_checked": checked,
            "scope": "declared source boundaries, capability evidence bytes and cutpoint input roles; not semantic/visual approval"}


def validate_delivery_cutpoints(rows, segments):
    """Require source cutpoint references in the actual submitted draft inputs."""
    by_id = {s["segment_id"]: s for s in segments}
    _need(len(by_id) == len(segments), "交付源生成段 ID 重复，不能猜来源")
    delivered = {row.get("source_segment_id"): row for row in rows}
    _need(len(delivered) == len(rows), "同一源生成段不能对应多个本次交付节点")
    required = {}
    for right in segments:
        split = right.get("same_scene_split", {})
        if not isinstance(split, dict) or split.get("reason") not in {"model_duration_exceeded", "execution_limit"}:
            continue
        boundary = right["continuity"]["boundary"]  # strict source check ran first
        left_id, right_id = split["from_segment_id"], right["segment_id"]
        if left_id in delivered and right_id in delivered:
            previous, current = delivered[left_id], delivered[right_id]
            before, after = previous.get("continuity"), current.get("continuity")
            _need(isinstance(before, dict) and isinstance(after, dict)
                  and _text(before.get("event_id")) and before["event_id"] == after.get("event_id")
                  and _text(previous.get("node_id")) and after.get("previous_node_id") == previous["node_id"]
                  and rows.index(previous) < rows.index(current),
                  "实际交付前驱/事件与源连续切点不一致，不能伪造新事件跳过切点核验")
        for ident, side in ((split["from_segment_id"], "previous"), (right["segment_id"], "current")):
            required.setdefault(ident, []).append((boundary["state_id"], set(boundary[side + "_sha256s"]), side))
    for row in rows:
        ident = row.get("source_segment_id")
        _need(ident in by_id, "scene_state: 交付必须绑定准确源生成段，不能换号绕过分段依据")
        for state, hashes, side in required.get(ident, []):
            boundary_shot = source_shot_ids(by_id[ident])[-1 if side == "previous" else 0]
            refs = row.get("reference_checks", [])
            actual = {r.get("sha256") for r in refs if isinstance(r, dict)
                      and r.get("state_id") == state and _text(r.get("sha256"))
                      and isinstance(r.get("roles", [r.get("role")]), list)
                      and all(_text(role) for role in r.get("roles", [r.get("role")]))
                      and set(r.get("roles", [r.get("role")])) &
                      {"state_reference", "end_frame" if side == "previous" else "start_frame"}
                      and boundary_shot in reference_shot_ids(r, row)}
            _need(hashes <= actual, "源稿切点参考未进入本次实际输入，不能仅在文字中声明")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--storyboard", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_generation_boundaries(json.loads(args.prompt.read_text(encoding="utf-8-sig")),
            json.loads(args.storyboard.read_text(encoding="utf-8-sig")), args.project)
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
