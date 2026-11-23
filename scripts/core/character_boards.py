"""Compose ordinary project character boards from their confirmed source slots."""

import hashlib
from io import BytesIO
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps

from . import artifacts, asset_refs
from .io import read_json, sha256_json, write_json

BOARD = "人物1x4"
HEAD = "大头照"
CLOTHES = {"服装", "服装三视图"}
ORDER = ["正面大头照", "服装正面", "服装左侧", "服装背面"]
PREVIEWS = Path("04_资产/母版/人物拼版")
PACKETS = Path("00_项目管理/资产交审/人物拼版")


def project_file(root, path):
    result = artifacts.project_path(root, path)
    if result == root or not result.is_relative_to(root):
        raise ValueError("拼版文件须位于项目内")
    return result


def _receipt(root, reference, cache):
    name, _, fragment = reference.partition("#")
    if name not in cache:
        cache[name] = read_json(project_file(root, name))
    return cache[name], fragment


def source_links(root, ast, output, cache):
    reference = output.get("user_confirmation_ref", "")
    if not reference:
        return []
    packet, fragment = _receipt(root, reference, cache)
    for row in packet.get("items", []):
        confirmation = row.get("confirmation", {})
        if row.get("ast_id") == ast and row.get("output_id") == output["output_id"] and confirmation.get("id") == fragment:
            return confirmation.get("sources", [])
    return []


def invalidate_changed_source(root, record, output_id, checksum, gaps, receipt, cache):
    """Retire only derived outputs using a superseded slot. Keep historical manifests and files."""
    keep = []
    for board in record["confirmed_outputs"]:
        if board.get("output_kind") != BOARD or not any(
            item["output_id"] == output_id and item["sha256"] != checksum
            for item in source_links(root, record["asset_id"], board, cache)
        ):
            keep.append(board)
            continue
        final = board["output_id"]
        gap = f"GAP-{record['asset_id']}-{final}"
        if not any(item["gap_id"] == gap for item in gaps):
            gaps.append(dict(gap_id=gap, requested_item=final, reason="已确认的拼版源图换版，须重新拼版",
                             source_ref=receipt, production_stopped=True))
    record["confirmed_outputs"] = keep


def _shown_finals(root, ast, output, cache):
    """Read pre-existing chat receipts that assigned a wardrobe to a specific board."""
    ref, _ = _receipt(root, output["confirmation_ref"], cache)
    target = ast + "@" + output["output_id"]
    result = set()
    for key in ref.get("shown", []):
        name = "00_项目管理/资产交审/" + key + ".json"
        shown, _ = _receipt(root, name, cache)
        if sha256_json(shown) != key:
            raise ValueError("原交审内容已变化：" + target)
        for item in shown["items"]:
            if item["target"] == target and item["sha256"] == output["sha256"] and item.get("final_output_id"):
                result.add(item["final_output_id"])
    return result


def round_board_source_gaps(root, assets, candidates):
    """Check that every outstanding outfit can be assembled after this single human review."""
    new = {}
    for item in candidates:
        ast, output = item["target"].split("@")
        new.setdefault(ast, {})[output] = item
    gaps = []
    cache = {}
    for ast, asset in assets.items():
        required = set(asset.get("required_board_ids", []))
        if not required:
            continue
        proposals = new.get(ast, {})
        confirmed = [o for o in asset["image"].get("outputs", []) if o["output_id"] not in proposals]
        remaining = required - {o["output_id"] for o in confirmed if o.get("output_kind") == BOARD}
        if not remaining:
            continue
        head = any(o.get("output_kind") == HEAD for o in (*confirmed, *proposals.values()))
        clothes = {o.get("final_output_id") for o in proposals.values() if o.get("output_kind") in CLOTHES}
        for o in confirmed:
            if o.get("output_kind") in CLOTHES:
                clothes.update(_shown_finals(root, ast, o, cache))
        for final in sorted(remaining):
            missing = ([] if head else ["同角色大头照"]) + ([] if final in clothes else ["绑定本造型的服装源图"])
            if missing:
                gaps.append(dict(target=ast + "@" + final, status="pending", issue="尚缺" + "、".join(missing)))
    return gaps


def plans(root, selection, specs, reviewed=None):
    raw_assets = {a["asset_id"]: a for a in selection["assets"]}
    explicit = {}
    for target, head, outfit in specs:
        ast, sep, final = target.partition("@")
        if not sep or not final or (ast, final) in explicit:
            raise ValueError("须指定唯一人物及最终输出 AST@OUTPUT")
        explicit[ast, final] = (head, outfit)
    scope = list(dict.fromkeys(ast for ast, _ in explicit)) if explicit else list(raw_assets)
    contract = asset_refs.character_output_contract(root, scope)
    requests = [ast + "@" + output for (ast, final), pair in explicit.items() for output in (*pair, final)]
    available = asset_refs.confirmed_images(root, requests=requests or list(contract),
                                            file_digest=artifacts.cache_file_digests(check_for_changes=True))
    decisions, issues = asset_refs._output_decisions(root)
    if any(issue.get("asset_id") in [None, *scope] for issue in issues):
        raise ValueError("资产适用决定存在问题，须先核对")
    if any(contract.get(ast, {}).get(final) != BOARD for ast, final in explicit):
        raise ValueError("目标不属于当前已登记的人物板")
    ready, reused, pending = [], [], []
    cache = {}
    for ast, finals in contract.items():
        raw_outputs = {o["output_id"]: o for o in raw_assets.get(ast, {}).get("confirmed_outputs", [])}
        outputs = {o["output_id"]: o for o in available.get(ast, {}).get("outputs", [])
                   if o["output_id"] in raw_outputs and
                   raw_outputs[o["output_id"]].get("user_confirmation_ref") == o["confirmation_ref"]}
        heads = [o for o in outputs.values() if o.get("output_kind") == HEAD]
        outfits = [o for o in outputs.values() if o.get("output_kind") in CLOTHES]
        for final, kind in finals.items():
            if kind != BOARD or (explicit and (ast, final) not in explicit):
                continue
            target = ast + "@" + final
            if decisions.get((ast, final), {}).get("status") == "rejected":
                pending.append(target)
                continue
            existing = outputs.get(final)
            previous = source_links(root, ast, raw_outputs[final], cache) if existing else []
            if existing and existing["output_kind"] == BOARD and not explicit:
                # Independent adopted boards remain reusable. For a derived board,
                # also check its dependencies when reading pre-upgrade project data.
                if all(outputs.get(s["output_id"], {}).get("sha256") == s["sha256"] for s in previous):
                    reused.append(target)
                    continue
            if explicit:
                head, outfit = (outputs.get(i, {}) for i in explicit[ast, final])
                if head.get("output_kind") != HEAD or outfit.get("output_kind") not in CLOTHES:
                    raise ValueError("两张来源须为同一角色当前已确认的头照和服装：" + target)
            else:
                pairs = [o for o in outfits if final in _shown_finals(root, ast, o, cache)]
                if len(heads) != 1 or len(pairs) != 1:
                    pending.append(target)
                    continue
                head, outfit = heads[0], pairs[0]
            linked = _shown_finals(root, ast, outfit, cache)
            if linked and final not in linked:
                raise ValueError("服装确认时登记的最终人物板与目标不同：" + target)
            sources = [dict(output_id=o["output_id"], path=project_file(root, o["path"]).relative_to(root).as_posix(),
                            sha256=o["sha256"], confirmation_ref=o["confirmation_ref"]) for o in (head, outfit)]
            if reviewed is not None:
                expected = reviewed[target]
                if expected["sources"] != sources or expected["state"] != outfit["state"]:
                    raise ValueError("实看后拼版源图或用途已变化：" + target)
            if existing:
                if [(s["output_id"], s["sha256"]) for s in previous] == [(s["output_id"], s["sha256"]) for s in sources]:
                    if reviewed is not None and existing["sha256"] != expected["image_sha256"]:
                        raise ValueError("已登记人物板与此次实看版本不同：" + target)
                    reused.append(target)
                    continue
            prior = {k: existing[k] for k in ("sha256", "confirmation_ref")} if existing else None
            plan = dict(ast_id=ast, output_id=final, sources=sources, state=outfit["state"])
            if prior:
                plan["prior_output"] = prior
            ready.append(plan)
    return ready, reused, pending


def render(root, spec):
    # Keep the reviewed pixels in place across adoption and retries. The master manifest
    # only links them once the producer has actually checked this specific preview.
    folder = project_file(root, PREVIEWS / sha256_json(spec["sources"]))
    filename = "人物1×4.png"
    if not folder.exists():
        folder.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=folder.parent) as tmp:
            staged = Path(tmp) / "board"
            staged.mkdir()
            images = []
            for source in spec["sources"]:
                data = project_file(root, source["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != source["sha256"]:
                    raise ValueError("取图期间拼版源文件发生变化")
                with Image.open(BytesIO(data)) as raw:
                    upright = ImageOps.exif_transpose(raw)
                    mode = "RGBA" if "A" in upright.getbands() or "transparency" in upright.info else "RGB"
                    images.append(upright.convert(mode))
            ratio = sum(im.width / im.height for im in images)
            height = min(max(301, min(im.height for im in images)), 5999, int(5997 / ratio))
            if height < 1:
                raise ValueError("源图尺寸无法完整等比拼版")
            widths = [max(1, round(im.width / im.height * height)) for im in images]
            width = sum(widths)
            canvas_height = max(height, math.ceil(width / 2.46)) if width / height >= 2.5 else height
            if not (300 < min(width, canvas_height) <= max(width, canvas_height) < 6000 and width > canvas_height):
                raise ValueError("源图比例不适合人物横向拼版")
            mode = "RGBA" if any(image.mode == "RGBA" for image in images) else "RGB"
            board = Image.new(mode, (width, canvas_height), images[0].getpixel((0, 0)))
            x = 0
            for image, w in zip(images, widths):
                board.paste(image.resize((w, height), Image.Resampling.LANCZOS), (x, (canvas_height-height)//2))
                x += w
            board.save(staged / filename)
            write_json(staged / "尺寸与散列.json", dict(sha256=artifacts.digest(staged / filename), pixel_size=list(board.size)))
            staged.rename(folder)
    info = read_json(project_file(root, folder / "尺寸与散列.json"))
    file = project_file(root, folder / filename)
    if artifacts.digest(file) != info["sha256"]:
        raise ValueError("实看拼版图的字节已变化")
    return dict(spec, path=file.relative_to(root).as_posix(), image_sha256=info["sha256"], pixel_size=info["pixel_size"])
