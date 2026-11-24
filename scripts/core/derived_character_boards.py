"""Register lossless character boards using existing exact source approvals."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops

from . import artifacts, asset_refs
from .character_assets import scoped_output_requirements
from .io import read_json, sha256_json, write_json
from .project_state import write_project_state
from .schema_contracts import require_schema


def _local(root, path):
    if not isinstance(path, (str, Path)):
        raise ValueError("拼版路径无效")
    target = artifacts.project_path(root, path)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("拼版须引用项目内的已有文件")
    return target


def _source(root, ast, output, expected, **kwargs):
    if output.get("derived_confirmation_ref") or output.get("output_kind") != expected:
        raise ValueError(f"{ast}：拼版来源类型与已确认头照／服装不符")
    selected = asset_refs._confirmed_output(root, ast, output, **kwargs)
    if not selected or not selected.get("sha256"):
        raise ValueError(f"{ast}：拼版缺少可用的准确来源确认")
    ref = selected["confirmation_ref"]
    receipt = _local(root, ref.partition("#")[0])
    path = _local(root, selected["path"])
    return dict(output_id=selected["output_id"], path=path.relative_to(root).as_posix(),
                sha256=selected["sha256"], confirmation_ref=ref,
                receipt_sha256=artifacts.digest(receipt))


def _check_source_decision(root, ast, output_id):
    decisions, _ = asset_refs._output_decisions(root)
    request = f"{ast}@{output_id}"
    if (asset_refs._decision_issues_for_request(root, request)
            or decisions.get((ast, output_id), {}).get("status") == "rejected"):
        raise ValueError(f"{request}：来源当前不可用")


def validate_consumption(root, ast, output, receipt, asset_outputs, **kwargs):
    """Recheck the exact source outputs when this derived board is consumed."""
    derivation = receipt.get("derivation")
    if (receipt.get("path") != output.get("path") or output.get("output_kind") != "人物1x4"
            or not isinstance(derivation, dict) or derivation.get("kind") != "character-board"
            or not isinstance(asset_outputs, list)):
        raise ValueError("人物拼版确认凭据不完整")
    sources = derivation.get("sources", [])
    if not isinstance(sources, list) or len(sources) != 2 or any(not isinstance(s, dict) for s in sources):
        raise ValueError("人物拼版缺两个来源")
    for source, kind in zip(sources, ("大头照", "服装三视图")):
        matches = [item for item in asset_outputs if isinstance(item, dict)
                   and item.get("output_id") == source.get("output_id")]
        if len(matches) != 1 or _source(root, ast, matches[0], kind, **kwargs) != source:
            raise ValueError("人物拼版原确认来源已变更")
        _check_source_decision(root, ast, source["output_id"])


def _check_chat_approval(root, narrative, ast, source, final_id=None):
    """Check the exact original shown batch; clothing also fixes the final slot."""
    SHOWN = Path("00_项目管理/资产交审")

    path = _local(root, source["confirmation_ref"].partition("#")[0])
    receipt = read_json(path)
    if not isinstance(receipt.get("shown"), list):
        raise ValueError("源图缺普通对话交审批次，无法确认其目标拼版")
    approved = [row["confirmation"] for row in receipt.get("items", [])
                if row.get("ast_id") == ast and row.get("output_id") == source["output_id"]]
    if (len(approved) != 1 or approved[0].get("scope") != "all_shown"
            or not all(approved[0].get(k) for k in ("at", "quote", "review_ref"))):
        raise ValueError("源图缺原始普通对话通过凭据")
    item = approved[0]
    matches = []
    for key in receipt["shown"]:
        packet = read_json(_local(root, SHOWN / (str(key) + ".json")))
        if (sha256_json(packet) != key or packet.get("narrative") != narrative["checksum"]
                or packet.get("narrative_id") != narrative["artifact_id"]):
            raise ValueError("源图原交审批次或编导版本不符")
        matches.extend(row for row in packet["items"]
                       if row.get("target") == f"{ast}@{source['output_id']}"
                       and row.get("sha256") == source["sha256"]
                       and row.get("path") == item.get("source_candidate"))
    if not matches:
        raise ValueError("源图未在当前编导的原确认批次中展示")
    if final_id and any(row.get("final_output_id") != final_id for row in matches):
        raise ValueError(f"{ast}@{final_id}：已确认的服装不是该拼版的原交审来源")


def _verify_pixels(board_path, head_path, clothing_path):
    with Image.open(board_path) as canvas, Image.open(head_path) as head, Image.open(clothing_path) as clothing:
        if canvas.format != "PNG" or canvas.width <= canvas.height:
            raise ValueError("机械拼版交付须为横向无损 PNG")
        dims = [(round(im.width * canvas.height / im.height), canvas.height) for im in (head, clothing)]
        if canvas.width != sum(w for w, _ in dims):
            raise ValueError("人物拼版尺寸不符合两张原图等比缩放到同高")
        # Require exact pixels for a standard resampling of each complete panel.
        # This rejects look-alike, retouched, cropped, padded or swapped boards.
        left = 0
        for image, size in zip((head, clothing), dims):
            panel = canvas.convert("RGBA").crop((left, 0, left + size[0], canvas.height))
            image = image.convert("RGBA")
            if not any(not any(band.getbbox() for band in ImageChops.difference(
                        panel, image.resize(size, sample)).split()) for sample in Image.Resampling):
                raise ValueError("人物拼版像素不是已确认的两张原图按原样并排")
            left += size[0]
        return canvas.size


def register(root, plan, narrative, entries, base):
    LOGICAL = "visual.asset.manifest"

    if not base or not artifacts.artifact_is_adopted(base, artifacts.load_project_state(root), root):
        raise ValueError("缺当前已采用资产清单")
    manifest_path = _local(root, base["path_or_url"])
    if artifacts.digest(manifest_path) != base["checksum"]:
        raise ValueError("当前资产清单字节已变化")
    manifest = read_json(manifest_path)
    if (manifest.get("schema_version") != "2.0"
            or manifest["source_narrative"].get("artifact_id") != narrative["artifact_id"]
            or plan.get("narrative") != narrative["artifact_id"]):
        raise ValueError("拼版和资产清单须绑定当前采用编导")
    asset_refs.without_rejected_outputs(root, manifest)
    items = plan.get("items")
    if not isinstance(items, list) or not items or any(not isinstance(i, dict) for i in items):
        raise ValueError("缺本批准确人物拼版")
    targets = [i.get("target") for i in items]
    if any(not isinstance(t, str) or t.count("@") != 1 or not all(t.split("@")) for t in targets):
        raise ValueError("每张拼版须登记 AST@最终输出 ID")
    if len(set(targets)) != len(targets):
        raise ValueError("拼版目标重复")
    story = read_json(_local(root, narrative["path_or_url"]))
    requirements = scoped_output_requirements(story, asset_refs.read_choices(root), targets)
    accepted = {}
    for item in items:
        ast, final_id = item["target"].split("@")
        if requirements.get(ast, {}).get(final_id) != "人物1x4":
            raise ValueError(f"{item['target']}：不是本版已登记的最终人物拼版")
        matches = [asset for asset in manifest["assets"] if asset["asset_id"] == ast]
        if len(matches) != 1 or matches[0]["status"] != "confirmed":
            raise ValueError(f"{ast}：缺已确认人物素材")
        asset = matches[0]
        outputs = {row["output_id"]: row for row in asset["confirmed_outputs"]}
        if len(outputs) != len(asset["confirmed_outputs"]):
            raise ValueError(f"{ast}：资产输出有重复")
        inputs = item.get("sources")
        if not isinstance(inputs, list) or len(inputs) != 2 or any(not isinstance(i, dict) for i in inputs):
            raise ValueError("每张拼版须有头照、三视图两项已确认来源")
        sources = []
        for spec, kind in zip(inputs, ("大头照", "服装三视图")):
            output_id = spec.get("output_id")
            _check_source_decision(root, ast, output_id)
            if output_id not in outputs:
                raise ValueError(f"{ast}：拼版来源不是当前清单的已确认输出")
            source = _source(root, ast, outputs[output_id], kind)
            if any(spec.get(key) != source[key] for key in ("sha256", "confirmation_ref")):
                raise ValueError(f"{ast}@{output_id}：拼版记录与当前源图确认不符")
            sources.append(source)
        _check_chat_approval(root, narrative, ast, sources[0])
        _check_chat_approval(root, narrative, ast, sources[1], final_id)
        board = _local(root, item.get("path"))
        checksum = artifacts.digest(board)
        if checksum != item.get("sha256"):
            raise ValueError(f"{item['target']}：成品字节与回执不符")
        size = _verify_pixels(board, *(_local(root, source["path"]) for source in sources))
        _check_source_decision(root, ast, final_id)
        existing = outputs.get(final_id)
        if existing:
            selected = asset_refs._confirmed_output(root, ast, existing, asset_outputs=asset["confirmed_outputs"])
            if selected and selected.get("sha256") == checksum and existing.get("derived_confirmation_ref"):
                # Same bytes can have a different ancestry after an official source revision.
                # The consumption check above must still pass for a no-op.
                continue
            raise ValueError(f"{item['target']}：已有登记；更换最终成品须走正式修订")
        accepted[item["target"]] = (asset, board, checksum, size, sources)
    if not accepted:
        _select_current(root, entries, base["artifact_id"])
        return dict(artifact_id=base["artifact_id"], count=0, manifest=str(manifest_path))

    ident = "visual-assets-derived-boards-" + sha256_json([base["artifact_id"], sorted(
        (key, value[2], value[4]) for key, value in accepted.items())])
    rel = Path(asset_refs.MASTER_DIR) / ident
    folder = root / rel
    receipt_rel = rel / "机械拼版来源记录.json"
    manifest_rel = rel / "visual-asset-manifest.json"
    revisions = [int(e["revision"][1:]) for e in entries if e["logical_id"] == LOGICAL
                 and e.get("revision", "").startswith("R") and e["revision"][1:].isdigit()]
    old = next((e for e in entries if e["artifact_id"] == ident), None)
    if any(e["logical_id"] == LOGICAL and e["status"] == "candidate" and e is not old for e in entries):
        raise ValueError("另一次资产登记待恢复")
    manifest["revision"] = old["revision"] if old else f"R{max(revisions, default=0) + 1:02}"
    receipts = []
    derived_outputs = []
    with TemporaryDirectory(dir=root) as tmp:
        staging = Path(tmp)
        for key, (asset, board, checksum, size, sources) in sorted(accepted.items()):
            ast, final_id = key.split("@")
            name = sha256_json(key)[:12] + ".png"
            data = board.read_bytes()
            if hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError(f"{key}：成品在登记期间发生变化")
            (staging / name).write_bytes(data)
            path = (rel / name).as_posix()
            used = {r["candidate_id"] for r in asset["candidates"]}
            serial = next(n for n in range(1, len(used) + 2) if f"{ast}-C{n:02}" not in used)
            candidate = f"{ast}-C{serial:02}"
            asset["candidates"].append(dict(candidate_id=candidate, path=path, status="candidate",
                source_references=[v["path"] for v in sources], technical_qc=dict(path_exists=True,
                    format="png", width=size[0], height=size[1], naming_valid=True, reference_traceable=True)))
            state = next((w["name"] for req in story["asset_requirements"] if req["asset_id"] == ast
                          for w in req.get("wardrobe_states", []) if w["output_id"] == final_id), final_id)
            output = dict(output_id=final_id, candidate_id=candidate,
                path=path, output_kind="人物1x4", purpose="沿已确认头照及对应服装的角色外观参考",
                view="头照／服装正面／左侧／背面", state=state,
                derived_confirmation_ref=receipt_rel.as_posix() + "#" + final_id)
            asset["confirmed_outputs"].append(output)
            derived_outputs.append(output)
            manifest["scope_gaps"] = [g for g in manifest.get("scope_gaps", [])
                                      if g["gap_id"] != f"GAP-{ast}-{final_id}"]
            receipts.append(dict(ast_id=ast, output_id=final_id, confirmation=dict(id=final_id, path=path,
                image_sha256=checksum, derivation=dict(kind="character-board", sources=sources))))
        write_json(staging / receipt_rel.name, dict(kind="derived-character-boards", base=base["artifact_id"],
                                                   narrative=narrative["artifact_id"], items=receipts))
        receipt_sha256 = artifacts.digest(staging / receipt_rel.name)
        for output in derived_outputs:
            output["derived_confirmation_sha256"] = receipt_sha256
        require_schema(manifest, "visual-asset-manifest.schema.json", label="机械拼版导出")
        write_json(staging / manifest_rel.name, manifest)
        record = dict(artifact_id=ident, logical_id=LOGICAL, type="visual-asset-manifest",
            revision=manifest["revision"], path_or_url=manifest_rel.as_posix(), depends_on=["narrative.package"],
            user_approved=False, checksum=artifacts.digest(staging / manifest_rel.name),
            confirmation_ref=receipt_rel.as_posix(),
            derived_character_boards_sha256=receipt_sha256)
        if old and (old["status"] != "candidate" or any(old.get(k) != v for k, v in record.items())):
            raise ValueError("待恢复机械拼版登记与本批不一致")
        if not old:
            artifacts.register_workspace_artifact(root, record)
        if folder.exists():
            if any(not (folder / f.name).is_file() or artifacts.digest(folder / f.name) != artifacts.digest(f)
                   for f in staging.iterdir()):
                raise ValueError("已有机械拼版资产目录与本批不一致")
        else:
            folder.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(folder)
        artifacts.promote_candidate(root, LOGICAL, ident)
    _select_current(root, entries, ident)
    return dict(artifact_id=ident, count=len(accepted), manifest=str(root / manifest_rel))


def _select_current(root, entries, ident):
    state = artifacts.load_project_state(root)
    asset_ids = {e["artifact_id"] for e in entries if e["logical_id"] == "visual.asset.manifest"} | {ident}
    selected = [v for v in state["current_artifacts"] if v not in asset_ids] + [ident]
    if state["current_artifacts"] != selected:
        state["current_artifacts"] = selected
        write_project_state(root, state)
