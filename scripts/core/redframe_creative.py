"""Deliver adopted creative video.prompt segments to Redframe canvas and publish verified readback."""
from __future__ import annotations

import math
import re
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import artifact_is_adopted, digest, load_manifest, project_path
from .draft_delivery import DELIVERY_RECEIPT, _binding, draft_delivery_lock, load_draft_receipt
from .io import read_json, sha256_json, write_json
from .media_types import MEDIA_EXTENSIONS
from .native_references import redframe_reference_labels
from .project_kickoff import load_kickoff
from .project_state import load_project_state
from .redframe_image_ui import _valid_face_certificate
from .redframe_recreation import (
    _SEEDANCE_MODEL,
    _SEEDANCE_REFERENCE_MODES,
    _check_layout,
    _collect,
    _fit,
    _insert_references,
    _label,
    _media_kind,
    _native_tokens,
    _nodes,
    _position,
    _read_images,
    _reload,
    _reuse_or_create,
    _set_duration,
    _upload_video,
    _verify_parameters,
    _video_data,
    _wire_image_slots,
)
from .redframe_workspace import _image_output_path


def _aliases(names):
    ordered = sorted(names, key=len, reverse=True)
    return re.compile(r'@(?:' + '|'.join(re.escape(n) for n in ordered) + r')(?![A-Za-z0-9_])')


def _material_key(material: dict[str, Any]) -> str:
    if material.get("product_reference_id"):
        return f"PROD-{material['product_reference_id']}"
    if material.get("artifact_id"):
        return str(material["artifact_id"])
    if material.get("asset_id") and material.get("output_id"):
        return f"{material['asset_id']}@{material['output_id']}"
    return f"MAT-{material['sha256'][:12]}"


def _read_images_batched(ui, image_nodes: list[dict[str, Any]], batch_size: int = 12) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(image_nodes), batch_size):
        for row in ui.workspace.digest_image_outputs(image_nodes[i:i + batch_size]):
            if row.get("status") == 200 and row.get("sha256"):
                out[row["node_id"]] = row["sha256"]
    return out


def _digest_slots_batched(workspace, draft_nodes: list[dict[str, Any]], batch_size: int = 6) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(draft_nodes), batch_size):
        out.update(workspace.digest_slots(draft_nodes[i:i + batch_size]))
    return out


def _resolve_file(project: Path, raw_path: str) -> Path:
    p = Path(raw_path)
    return p.resolve() if p.is_absolute() else (project / p).resolve()


def creative_packet(project: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = project.resolve()
    kickoff = load_kickoff(project)
    if not kickoff or not isinstance(kickoff.get("canvas"), dict):
        raise ValueError("缺少开工画布配置")
    canvas = kickoff["canvas"]
    if canvas.get("platform") != "redframe":
        raise ValueError("当前项目画布不是红帧")
    state = load_project_state(project)
    manifest = load_manifest(project)
    entries = [
        row for row in manifest.get("artifacts", [])
        if row.get("logical_id") == "video.prompt"
        and row.get("status") == "current"
        and row.get("artifact_id") in state.get("current_artifacts", [])
    ]
    if len(entries) != 1 or not artifact_is_adopted(entries[0], state, project):
        raise ValueError("写红帧草稿前须先正式采用唯一当前版 video.prompt")
    entry = entries[0]
    prompt_path = project_path(project, entry["path_or_url"])
    if not prompt_path.is_file() or digest(prompt_path) != entry["checksum"]:
        raise ValueError("已采用视频提示词文件缺失或字节变化")
    payload = read_json(prompt_path)
    if payload.get("generation_authorized") is True:
        raise ValueError("默认草稿写入禁止开启 generation_authorized")
    groups: list[dict[str, Any]] = []
    for seg in payload.get("generation_segments", []):
        seg_id = str(seg["segment_id"])
        raw_mats = list(seg.get("materials") or [])
        norm_mats: list[dict[str, Any]] = []
        ref_map: dict[str, str] = {}
        ref_keys: list[str] = []
        for m in raw_mats:
            key = _material_key(m)
            orig_ref = str(m["reference"])
            ref_map[orig_ref] = "@" + key
            ref_keys.append(key)
            fpath = _resolve_file(project, str(m["path"]))
            if not fpath.is_file() or digest(fpath) != m["sha256"]:
                raise ValueError(f"{seg_id} 素材文件缺失或字节不符：{m['path']}")
            norm_mats.append({
                **m,
                "orig_reference": orig_ref,
                "reference": "@" + key,
                "abs_path": str(fpath),
            })
        prompt_text = str(seg["prompt"])
        if ref_map:
            pat = re.compile("|".join(re.escape(k) for k in sorted(ref_map, key=len, reverse=True)))
            prompt_text = pat.sub(lambda match: ref_map[match[0]], prompt_text)
        groups.append({
            "id": seg_id,
            "node_name": f"{entry['revision']}｜{seg_id}｜{seg.get('title', seg_id)}",
            "generate_duration_seconds": int(seg["duration_seconds"]),
            "materials": norm_mats,
            "reference_keys": ref_keys,
            "prompt": prompt_text,
        })
    if not groups:
        raise ValueError("已采用视频提示词中无 generation_segments")
    packet = {
        "artifact_id": entry["artifact_id"],
        "revision": entry["revision"],
        "prompt_path": entry["path_or_url"],
        "prompt_sha256": entry["checksum"],
        "groups": groups,
    }
    return packet, canvas, entry


def _serialize_creative(group: dict[str, Any], identities: list[tuple[str, str]], tokens: dict[tuple[str, str], tuple[str, dict[str, Any]]]) -> tuple[str, list[list[dict[str, Any]]]]:
    names = group["reference_keys"]
    pattern = _aliases(names)
    lookup = {"@" + key: tokens[ident] for key, ident in zip(names, identities, strict=True)}
    rich = []
    for line in group["prompt"].split("\n"):
        parts, pos = [], 0
        for match in pattern.finditer(line):
            if pos < match.start():
                parts.append({"type": "Write", "text": line[pos:match.start()]})
            parts.append(deepcopy(lookup[match[0]][1]))
            pos = match.end()
        if pos < len(line) or not parts:
            parts.append({"type": "Write", "text": line[pos:]})
        rich.append(parts)
    return pattern.sub(lambda m: lookup[m[0]][0], group["prompt"]), rich


def _configure_creative(ui, group: dict[str, Any], ident: str, image_ids: dict[str, str], canvas: dict[str, Any]):
    graph = ui.workspace.read_canvas()
    draft = _nodes(graph)[ident]
    data = _video_data(draft)
    _verify_parameters(data, data["duration"], canvas)
    duration = group["generate_duration_seconds"]
    if data["duration"] != duration:
        _set_duration(ui, ident, duration)
        draft = _nodes(ui.workspace.read_canvas())[ident]
    videos = [m for m in group["materials"] if _media_kind(m) == "video"]
    slots = ui.workspace.digest_slots([draft])[ident]
    if [m["sha256"] for m in videos[:len(slots)]] != [slot["sha256"] for slot in slots] or len(slots) > len(videos):
        raise ValueError(f"{ident} 已有视频素材与目标顺序不符")
    for slot_count, material in enumerate(videos[len(slots):], len(slots) + 1):
        _upload_video(ui, ident, Path(material["abs_path"]), slot_count)
    labels = [label[1:] for label in redframe_reference_labels(map(_media_kind, group["materials"]))]
    data = _video_data(_nodes(ui.workspace.read_canvas())[ident])
    known = _native_tokens(data)
    direct_slots = iter(data.get("localReferences", {}).get("in", []))
    missing = []
    for label, material in zip(labels, group["materials"], strict=True):
        if _media_kind(material) == "image":
            source_id = image_ids[material["reference"][1:]]
            exists = any(source_id == node_id for node_id, _ in known)
        else:
            exists = (ident, next(direct_slots)["sourceHandle"]) in known
        if not exists:
            missing.append(label)
    if missing:
        _insert_references(ui, ident, missing)
    return ui._wait_canvas(
        lambda g: len(_native_tokens(_video_data(_nodes(g)[ident]))) == len(labels) or None,
        "保存原生引用", timeout=35,
    )


def _layout_module_rows(
    nodes: dict[str, dict[str, Any]],
    rows_of_ids: list[list[str]],
    *,
    start_x: float = 720.0,
    start_y: float = 50.0,
    min_w: float = 360.0,
    min_h: float = 480.0,
    gap_x: float = 90.0,
    gap_y: float = 110.0,
) -> tuple[dict[str, tuple[float, float]], float]:
    positions: dict[str, tuple[float, float]] = {}
    cur_y = start_y
    for row_ids in rows_of_ids:
        if not row_ids:
            continue
        cur_x = start_x
        row_h = min_h
        for nid in row_ids:
            dims = (nodes.get(nid) or {}).get("dimensions") or {}
            w = max(min_w, float(dims.get("width") or min_w))
            h = max(min_h, float(dims.get("height") or min_h))
            positions[nid] = (round(cur_x, 1), round(cur_y, 1))
            cur_x += w + gap_x
            row_h = max(row_h, h)
        cur_y += row_h + gap_y
    return positions, cur_y


def _group_asset_rows(images: dict[str, dict[str, Any]], asset_id_map: dict[str, str]) -> list[list[str]]:
    buckets: list[tuple[int, list[str]]] = [
        (4, [asset_id_map[k] for k in images if k.startswith("AST-CH-")]),
        (8, [asset_id_map[k] for k in images if k.startswith("SUP-")]),
        (4, [asset_id_map[k] for k in images if k.startswith("AST-ENV-")]),
        (3, [asset_id_map[k] for k in images if k.startswith("AST-PR-")]),
        (3, [asset_id_map[k] for k in images if not k.startswith(("AST-CH-", "SUP-", "AST-ENV-", "AST-PR-"))]),
    ]
    rows: list[list[str]] = []
    for cols, ids in buckets:
        for i in range(0, len(ids), cols):
            rows.append(ids[i:i + cols])
    return rows


def write_creative(project: Path, *, host_browser) -> dict[str, Any]:
    from ..redframe_draft import RedframeVideoDraftUI, _native_content, open_workspace

    project = project.resolve()
    with draft_delivery_lock(project), ExitStack() as session:
        packet, canvas, entry = creative_packet(project)
        url, project_id = canvas["target_url"], canvas["target_project_id"]
        groups = packet["groups"]
        source = {
            "artifact_id": packet["artifact_id"],
            "revision": packet["revision"],
            "prompt_path": packet["prompt_path"],
            "prompt_sha256": packet["prompt_sha256"],
        }
        receipt = load_draft_receipt(project)
        receipt.update(target_url=url, target_project_id=project_id)
        state_key = f"creative_{packet['revision']}_{packet['prompt_sha256'][:8]}"
        state = receipt.setdefault(state_key, {**source, "nodes": {}, "assets": {}})

        def save():
            write_json(project / DELIVERY_RECEIPT, receipt)

        materials = {m["reference"][1:]: m for g in groups for m in g["materials"]}
        images = {k: m for k, m in materials.items() if _media_kind(m) == "image"}
        required_faces = {
            k for k, m in images.items()
            if m.get("requires_face_certification") and "无头" not in Path(m["abs_path"]).name
        }

        workspace = session.enter_context(open_workspace(host_browser, url, project_id))
        page = workspace.page
        page.set_viewport_size({"width": 2480, "height": 1660})
        page.get_by_role("button", name="适应视图", exact=True).wait_for(timeout=60000)
        ui = RedframeVideoDraftUI(page, project_id, url, workspace=workspace)
        ui.close_dialog()

        prior_asset_ids = {
            aid
            for k, v in receipt.items()
            if k != state_key and isinstance(v, dict) and isinstance(v.get("assets"), dict)
            for aid in v["assets"].values()
        }
        init_nodes = _nodes(workspace.read_canvas())
        cand_img_nodes = [n for n in init_nodes.values() if n.get("type") == "remote-imageNode"]
        init_hashes = _read_images_batched(ui, cand_img_nodes) if cand_img_nodes else {}
        by_hash: dict[str, str] = {}
        for n in sorted(cand_img_nodes, key=lambda x: (x["id"] not in prior_asset_ids, x["id"])):
            h = init_hashes.get(n["id"])
            if h and h not in by_hash:
                by_hash[h] = n["id"]

        created_any_asset = False
        for key, mat in images.items():
            if key in state["assets"] and state["assets"][key] in init_nodes and init_hashes.get(state["assets"][key]) == mat["sha256"]:
                print("已绑定源卡", key, state["assets"][key], flush=True)
                continue
            if key not in state["assets"] and mat["sha256"] in by_hash:
                state["assets"][key] = by_hash[mat["sha256"]]
                save()
                print("复用同哈希源卡", key, state["assets"][key], flush=True)
                continue
            was_known = key in state["assets"]
            name = f"1:1参考｜{packet['revision']}｜{key}"
            ident = _reuse_or_create(
                ui, state, "assets", key, name,
                lambda x, y, p=Path(mat["abs_path"]): ui.upload_source(p, x, y),
                save, expected_hash=mat["sha256"], reload_after_rename=False,
            )
            if not was_known:
                created_any_asset = True
            print("素材卡", key, ident, flush=True)
        if created_any_asset:
            _reload(ui)

        graph = workspace.read_canvas()
        nodes = _nodes(graph)
        image_checks = _read_images_batched(ui, [nodes[state["assets"][k]] for k in images])
        if any(image_checks.get(state["assets"][k]) != m["sha256"] for k, m in images.items()):
            raise ValueError("源素材卡的实际字节与视频提示词不符")

        new_images = {k: m for k, m in images.items() if state["assets"][k] not in prior_asset_ids}
        own_ids = {state["assets"][k] for k in new_images} | set(state["nodes"].values())
        other_nodes = [n for n in nodes.values() if n["id"] not in own_ids]
        asset_rows = _group_asset_rows(new_images, state["assets"]) if new_images else []
        est_total_h = (
            len(asset_rows) * 610.0
            + (180.0 if asset_rows else 0.0)
            + math.ceil(len(groups) / 4) * 810.0
        )
        if other_nodes:
            other_min_y = min(float(n["position"]["y"]) for n in other_nodes)
            other_max_y = max(
                float(n["position"]["y"]) + float(n.get("dimensions", {}).get("height") or 480)
                for n in other_nodes
            )
            other_max_x = max(
                float(n["position"]["x"]) + float(n.get("dimensions", {}).get("width") or 440)
                for n in other_nodes
            )
            if (other_max_y + 220.0 + est_total_h - other_min_y) > 7200.0:
                module_x = round(other_max_x + 420.0, 1)
                mid_y = (other_min_y + other_max_y) / 2.0
                asset_y = round(max(other_min_y, mid_y - est_total_h / 2.0), 1)
            else:
                module_x = 720.0
                asset_y = other_max_y + 220.0
        else:
            module_x = 720.0
            asset_y = 50.0
        if new_images:
            asset_positions, after_assets_y = _layout_module_rows(
                nodes,
                asset_rows,
                start_x=module_x,
                start_y=asset_y,
                min_w=380.0,
                min_h=500.0,
                gap_x=90.0,
                gap_y=110.0,
            )
            _position(ui, asset_positions)
            _fit(ui)
        else:
            after_assets_y = asset_y - 180.0

        attempts = receipt.setdefault("face_certification_attempts", {})
        for key in sorted(required_faces):
            print("人脸认证", key, state["assets"][key], flush=True)
            ui.ensure_face_certification(state["assets"][key], images[key]["sha256"], attempts, save)

        created_any_node = False
        for group in groups:
            was_known = group["id"] in state["nodes"]
            ident = _reuse_or_create(
                ui, state, "nodes", group["id"], group["node_name"],
                lambda x, y: ui._new_node(x, y, menu_name="视频", generation_kind="remote-videoGenerationNode"),
                save, reload_after_rename=False,
            )
            if not was_known:
                created_any_node = True
            print("草稿节点", group["id"], ident, flush=True)
        if created_any_node:
            _reload(ui)

        nodes_now = _nodes(workspace.read_canvas())
        draft_id_list = [state["nodes"][g["id"]] for g in groups]
        draft_rows = [draft_id_list[i:i + 4] for i in range(0, len(draft_id_list), 4)]
        base_y = after_assets_y + 180.0
        draft_positions, _ = _layout_module_rows(
            nodes_now,
            draft_rows,
            start_x=module_x,
            start_y=base_y,
            min_w=440.0,
            min_h=680.0,
            gap_x=110.0,
            gap_y=130.0,
        )
        _position(ui, draft_positions)
        _wire_image_slots(ui, groups, state)
        for group in groups:
            _configure_creative(ui, group, state["nodes"][group["id"]], state["assets"], canvas)
            print("已配置草稿素材与时长", group["id"], flush=True)

        graph = workspace.read_canvas()
        staged = deepcopy(graph)
        obs_nodes = _nodes(graph)
        next_nodes = _nodes(staged)
        slots = _digest_slots_batched(workspace, [obs_nodes[i] for i in state["nodes"].values()])
        for group in groups:
            ident = state["nodes"][group["id"]]
            _, identities, tokens, _ = _collect(group, ident, graph, state["assets"], slots[ident])
            prompt, rich = _serialize_creative(group, identities, tokens)
            next_nodes[ident]["data"].update(prompt=prompt, promptModel=rich)
        if graph != staged:
            workspace.write_canvas(graph, staged)
        _reload(ui)

        post_nodes = _nodes(workspace.read_canvas())
        final_draft_positions, _ = _layout_module_rows(
            post_nodes,
            draft_rows,
            start_x=module_x,
            start_y=base_y,
            min_w=440.0,
            min_h=680.0,
            gap_x=110.0,
            gap_y=130.0,
        )
        _position(ui, final_draft_positions)

        graph = workspace.read_canvas()
        obs_nodes = _nodes(graph)
        slots = _digest_slots_batched(workspace, [obs_nodes[i] for i in state["nodes"].values()])
        checked = _read_images_batched(ui, [obs_nodes[state["assets"][k]] for k in images])
        if any(checked.get(state["assets"][k]) != m["sha256"] for k, m in images.items()):
            raise ValueError("刷新后图片源卡字节变化")
        for key in sorted(required_faces):
            node = obs_nodes[state["assets"][key]]
            if not _valid_face_certificate(node["data"].get("faceCertification"), _image_output_path(node), images[key]["sha256"]):
                raise ValueError(f"刷新后人物源卡缺本图认证：{key}")

        models = {"reference": ("Seedance 2.5", {"providerId": _SEEDANCE_MODEL[0], "modelId": _SEEDANCE_MODEL[1], "mode": _SEEDANCE_REFERENCE_MODES})}
        rows = []
        for group in groups:
            ident = state["nodes"][group["id"]]
            data, _, _, ordered = _collect(group, ident, graph, state["assets"], slots[ident])
            _verify_parameters(data, group["generate_duration_seconds"], canvas, populated=True)
            if (data["prompt"], data["promptModel"]) != (next_nodes[ident]["data"]["prompt"], next_nodes[ident]["data"]["promptModel"]):
                raise ValueError(f"刷新后正文或引用结构发生变化：{ident}")
            prompt, parameters, mentions = _native_content(ident, data, ordered, list(map(_media_kind, group["materials"])), models, linked=True)
            labels = redframe_reference_labels(map(_media_kind, group["materials"]))
            label_by_key = dict(zip(group["reference_keys"], labels, strict=True))
            expected = _aliases(label_by_key).sub(lambda m: label_by_key[m[0][1:]], group["prompt"])
            if prompt != expected or len(mentions) < len(ordered):
                raise ValueError(f"刷新后的原生正文、引用或素材身份核对失败：{ident}")
            rows.append({
                "source_segment_id": group["id"],
                "node_id": ident,
                "parameters": parameters,
                "ordered_media": ordered,
                "native_mentions": mentions,
                "prompt": prompt,
                "position": obs_nodes[ident]["position"],
            })
        _fit(ui)
        _check_layout(page, state["nodes"].values())

        frozen = {
            "mode": "creative_linked_v2",
            "revision": entry["revision"],
            "target_url": url,
            "target_project_id": project_id,
            "source_artifacts": [{
                "artifact_id": entry["artifact_id"],
                "logical_id": "video.prompt",
                "revision": entry["revision"],
                "path_or_url": entry["path_or_url"],
                "checksum": entry["checksum"],
                "sha256": entry["checksum"],
            }],
            "drafts": rows,
        }
        frozen_sha = sha256_json(frozen)
        observed_doc = {
            "mode": "creative_linked_v2",
            "phase": "after_refresh",
            "revision": entry["revision"],
            "target_url": url,
            "target_project_id": project_id,
            "frozen_sha256": frozen_sha,
            "drafts": rows,
        }
        observed_sha = sha256_json(observed_doc)
        formal_report = {
            "ok": True,
            "mode": "creative_linked_v2",
            "native_refresh_binding": "selected_fields_match",
            "revision": entry["revision"],
            "draft_count": len(rows),
            "frozen_sha256": frozen_sha,
            "observed_sha256": observed_sha,
        }
        rb_dir = DELIVERY_RECEIPT.parent / "草稿回读" / f"creative-{frozen_sha[:16]}"
        for fname, doc in (("frozen", frozen), ("observed", observed_doc), ("formal_readback", formal_report)):
            write_json(project / rb_dir / f"{fname}.json", doc)

        records = receipt.setdefault("draft_receipts", {})
        for row in rows:
            binding = _binding(canvas, entry, row["source_segment_id"])
            records[sha256_json(binding)] = {
                "binding": binding,
                "node_id": row["node_id"],
                "phase": "readback_complete",
                "plan_sha256": frozen_sha,
                "readback_path": rb_dir.as_posix(),
            }
        state.update(phase="after_refresh", rows=rows, readback_path=rb_dir.as_posix())
        save()
        return {
            "ok": True,
            "draft_count": len(rows),
            "readback_path": rb_dir.as_posix(),
            "receipt_path": DELIVERY_RECEIPT.as_posix(),
        }
