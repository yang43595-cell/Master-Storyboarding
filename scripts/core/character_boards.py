"""Compose ordinary project character boards from their confirmed source slots."""

import hashlib
from io import BytesIO
import math
from pathlib import Path
import re
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


_GAP_SAFE = re.compile(r"[A-Z0-9-]+")


def scope_gap_id(ast, output):
    """Build a schema-legal gap ID even when the output ID contains Chinese or underscores.

    Already-legal output IDs keep their historical form, so existing manifests still match.
    """
    if _GAP_SAFE.fullmatch(output or ""):
        return f"GAP-{ast}-{output}"
    slug = re.sub(r"[^A-Z0-9]+", "-", (output or "").upper()).strip("-")
    digest = hashlib.sha256((output or "").encode("utf-8")).hexdigest()[:8].upper()
    return f"GAP-{ast}-{slug + '-' if slug else ''}{digest}"


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
        gap = scope_gap_id(record['asset_id'], final)
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


def plans(root, selection, specs):
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
            if existing:
                if [(s["output_id"], s["sha256"]) for s in previous] == [(s["output_id"], s["sha256"]) for s in sources]:
                    reused.append(target)
                    continue
            plan = dict(ast_id=ast, output_id=final, sources=sources, state=outfit["state"])
            ready.append(plan)
    return ready, reused, pending


BOARD_LAYOUT = "full_left_head_v2"


def _trim_headshot_border(image: Image.Image) -> Image.Image:
    """Strip uniform outer near-white mat borders so the headshot fills the left slot."""
    w, h = image.size
    if w < 16 or h < 16:
        return image
    rgb = image.convert("RGB")
    corner = rgb.getpixel((0, 0))
    if min(corner) < 240 or max(corner) - min(corner) > 10:
        return image
    px = rgb.load()

    def _is_border(x, y):
        p = px[x, y]
        return abs(p[0] - corner[0]) <= 3 and abs(p[1] - corner[1]) <= 3 and abs(p[2] - corner[2]) <= 3

    top = 0
    while top < h and all(_is_border(x, top) for x in range(w)):
        top += 1
    if top >= h:
        return image
    bottom = h - 1
    while bottom > top and all(_is_border(x, bottom) for x in range(w)):
        bottom -= 1
    left = 0
    while left < w and all(_is_border(left, y) for y in range(top, bottom + 1)):
        left += 1
    right = w - 1
    while right > left and all(_is_border(right, y) for y in range(top, bottom + 1)):
        right -= 1
    has_outer_mat = bottom < h - 1 or (left > 0 and right < w - 1)
    cw, ch = right + 1 - left, bottom + 1 - top
    if not has_outer_mat or cw < w * 0.4 or ch < h * 0.4:
        return image
    return image.crop((left, top, right + 1, bottom + 1))


def render(root, spec):
    # Keep the reviewed pixels in place across adoption and retries. The master manifest
    # only links them once the producer has actually checked this specific preview.
    folder = project_file(root, PREVIEWS / sha256_json({"layout": BOARD_LAYOUT, "sources": spec["sources"]}))
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
            head = _trim_headshot_border(images[0])
            outfit = images[1]
            r_head = head.width / head.height
            r_outfit = outfit.width / outfit.height
            ratio = r_head + r_outfit
            outfit_h = min(max(301, min(head.height, outfit.height)), 5999, int(5997 / ratio))
            if outfit_h < 1 or r_head >= 2.46:
                raise ValueError("源图尺寸无法完整等比拼版")
            outfit_w = max(1, round(r_outfit * outfit_h))
            canvas_height = outfit_h
            if (max(1, round(r_head * canvas_height)) + outfit_w) / canvas_height >= 2.5:
                canvas_height = max(outfit_h, math.ceil(outfit_w / (2.46 - r_head)))
                if canvas_height >= 6000 or max(1, round(r_head * canvas_height)) + outfit_w >= 6000:
                    scale = 5997 / max(canvas_height, max(1, round(r_head * canvas_height)) + outfit_w)
                    outfit_h = max(1, int(outfit_h * scale))
                    outfit_w = max(1, round(r_outfit * outfit_h))
                    canvas_height = max(outfit_h, math.ceil(outfit_w / (2.46 - r_head)))
            head_w = max(1, round(r_head * canvas_height))
            width = head_w + outfit_w
            if not (300 < min(width, canvas_height) <= max(width, canvas_height) < 6000 and width > canvas_height):
                raise ValueError("源图比例不适合人物横向拼版")
            mode = "RGBA" if any(image.mode == "RGBA" for image in (head, outfit)) else "RGB"
            board = Image.new(mode, (width, canvas_height), outfit.getpixel((0, 0)))
            board.paste(head.resize((head_w, canvas_height), Image.Resampling.LANCZOS), (0, 0))
            board.paste(outfit.resize((outfit_w, outfit_h), Image.Resampling.LANCZOS),
                        (head_w, (canvas_height - outfit_h) // 2))
            board.save(staged / filename)
            write_json(staged / "尺寸与散列.json", dict(sha256=artifacts.digest(staged / filename), pixel_size=list(board.size)))
            staged.rename(folder)
    info = read_json(project_file(root, folder / "尺寸与散列.json"))
    file = project_file(root, folder / filename)
    if artifacts.digest(file) != info["sha256"]:
        raise ValueError("实看拼版图的字节已变化")
    return dict(spec, path=file.relative_to(root).as_posix(), image_sha256=info["sha256"], pixel_size=info["pixel_size"])
