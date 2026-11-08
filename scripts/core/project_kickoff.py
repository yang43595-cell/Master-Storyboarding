"""Read the current production decisions from the existing project request."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from .product_reference_pack import current_product_pack_revision, resolve_product_pack, validate_selection


REQUEST_PATH = Path("00_项目管理/project_request.json")
REDFRAME_MODEL = "Seedance 2.5"
PRODUCT_MODES = {"none", "retain", "replace", "insert"}
IMAGE_PROVIDERS = {"imagegen", "dreamina"}
IMAGE_SCOPES = ("required_asset_outputs", "required_assets_and_supplements")

_CREATOR_MASK_ID = "creator-polishing-mask-2"
_CREATOR_MASK_NAMES = {
    "造物者温泉酸面膜",
    "造物者温泉酸抛光面膜",
    "造物者温泉酸抛光面膜2.0",
    "造物者温泉酸抛光面膜 2.0",
}


def _normalize_product_id(record):
    value = record.get("product_id")
    if isinstance(value, str) and value.strip() in _CREATOR_MASK_NAMES:
        return {**record, "product_id": _CREATOR_MASK_ID}
    return record


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def load_kickoff(project):
    path = Path(project) / REQUEST_PATH
    try:
        request = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取项目需求：{path}") from exc
    if not isinstance(request, dict):
        raise ValueError(f"项目需求必须是 JSON 对象：{path}")
    if "kickoff" not in request:
        return None
    return validate_kickoff(request["kickoff"])


def validate_kickoff(kickoff, *, for_registration=False):
    if not isinstance(kickoff, dict) or not _text(kickoff.get("decision_ref")):
        raise ValueError("开工决定须登记 decision_ref")
    product = kickoff.get("product")
    if not isinstance(product, dict) or not _text(product.get("mode")) or product["mode"] not in PRODUCT_MODES:
        raise ValueError("开工产品须明确选择 none / retain / replace / insert")
    if product["mode"] == "none":
        if "product_id" in product or "product_usage" in product:
            raise ValueError("无广告项目不能同时选定广告产品或用法")
    elif not _text(product.get("product_id")) or product.get("product_usage") not in ("display", "real_use"):
        raise ValueError("有广告时须登记准确 product_id 和 product_usage")
    else:
        product = _normalize_product_id(product)
    if for_registration:
        if (product.get("product_id") == _CREATOR_MASK_ID
                and "reference_pack_revision" not in product and "reference_pack_sha256" not in product):
            product = {**product, "reference_pack_revision": current_product_pack_revision(_CREATOR_MASK_ID)}
        pack = resolve_product_pack(product, for_registration=True)
        if pack:
            product = {**product, "reference_pack_sha256": pack["manifest_sha256"]}
    else:
        validate_selection(product)
    kickoff = {**kickoff, "product": product}
    canvas = kickoff.get("canvas")
    if for_registration and isinstance(canvas, dict) and canvas.get("platform") == "redframe":
        canvas = {**canvas, "model": REDFRAME_MODEL}
        kickoff = {**kickoff, "canvas": canvas}
    if not isinstance(canvas, dict) or not all(_text(canvas.get(key)) for key in (
        "platform", "model", "aspect_ratio", "resolution"
    )):
        raise ValueError("开工画布须登记平台、模型、画幅和分辨率")
    if canvas.get("target_url") is not None:
        url = urlsplit(canvas["target_url"] if _text(canvas["target_url"]) else "")
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            raise ValueError("开工画布 target_url 必须是准确 HTTP(S) 地址")
    if canvas.get("target_project_id") is not None and not _text(canvas["target_project_id"]):
        raise ValueError("画布项目 ID 无效")
    if canvas.get("target_url") is None and canvas.get("create_new") is not True:
        raise ValueError("须登记准确画布地址或明确新建")
    if canvas["platform"] == "xiaoyunque" and canvas["resolution"].lower() != "480p":
        raise ValueError("小云雀画布须采用 480p")
    generation = kickoff.get("image_generation")
    if generation is not None:
        if not isinstance(generation, dict):
            raise ValueError("生图授权须为 JSON 对象")
        outputs = generation.get("outputs")
        if "outputs" in generation:
            scope_valid = ("scope" not in generation and isinstance(outputs, dict) and bool(outputs)
                           and all(_text(ast) and isinstance(keys, list) and keys
                                   and all(_text(key) for key in keys) for ast, keys in outputs.items()))
        else:
            scope_valid = generation.get("scope") in IMAGE_SCOPES
        if (not scope_valid
                or not _text(generation.get("provider")) or generation["provider"] not in IMAGE_PROVIDERS
                or not _text(generation.get("model"))
                or not _text(generation.get("authorization_ref"))
                or generation.get("reference_upload_authorized") is not True
                or generation.get("fee_scope") != "existing_quota"):
            raise ValueError("生图授权须登记工具、模型、参考上传、既有额度、获准范围与授权依据")
    return kickoff


def bind_stage_request(request, kickoff):
    if "kickoff" in request and request["kickoff"] != kickoff:
        raise ValueError("开工决定须来自项目需求记录")
    request = _normalize_product_id(request)
    if kickoff is None:
        return request
    product = kickoff["product"]
    for key in ("product_id", "product_usage"):
        if key in request and request[key] != product.get(key):
            raise ValueError(f"阶段请求的 {key} 与项目开工决定冲突")
    return {**request, "kickoff": kickoff,
            **{key: product[key] for key in ("product_id", "product_usage") if key in product}}


def grant_covers_asset(grant, ast_id, output_id):
    """Check scope after the caller validates the current AST and output."""
    return grant is not None and (
        grant.get("scope") in IMAGE_SCOPES or output_id in grant.get("outputs", {}).get(ast_id, [])
    )
