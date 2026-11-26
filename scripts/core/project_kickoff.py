"""Read the current production decisions from the existing project request."""

import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from .product_reference_pack import product_catalog, resolve_product_pack, validate_selection


REQUEST_PATH = Path("00_项目管理/project_request.json")
REDFRAME_MODEL = "Seedance 2.5"
CREATIVE_WORKFLOW = "creative"
RECREATION_WORKFLOW = "depth-guided-recreation"
PENDING_WORKFLOW = "pending"
RECREATION_ENTRY = f"skills/{RECREATION_WORKFLOW}/SKILL.md"
RECREATION_METHOD = f"skills/{RECREATION_WORKFLOW}/modules/recreation-method.md"
PRODUCT_MODES = {"none", "retain", "replace", "insert"}
IMAGE_PROVIDERS = {"imagegen", "dreamina", "chatgpt", "redframe"}
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


def production_workflow(kickoff):
    creation = (kickoff or {}).get("creation")
    workflow = creation.get("workflow", CREATIVE_WORKFLOW) if isinstance(creation, dict) else CREATIVE_WORKFLOW
    if workflow not in (CREATIVE_WORKFLOW, RECREATION_WORKFLOW, PENDING_WORKFLOW):
        raise ValueError("creation.workflow 须选 creative、depth-guided-recreation 或 pending")
    return workflow


def _positive_seconds(value):
    if type(value) not in (int, float) or value <= 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def product_demo_timing_advisory(kickoff):
    """Compare a confirmed target window with a referenced estimate."""
    if not kickoff or kickoff["product"].get("product_usage") != "real_use":
        return None
    creation = kickoff.get("creation")
    if not isinstance(creation, dict):
        return None
    timing = creation.get("product_demo_timing")
    if not isinstance(timing, dict):
        return None

    window = _positive_seconds(timing.get("target_seconds"))
    if window is None or not _text(timing.get("decision_ref")):
        return None

    report = {"window_seconds": window, "window_source": "confirmed"}
    estimate = _positive_seconds(timing.get("estimated_minimum_seconds"))
    basis = timing.get("estimate_basis")
    if estimate is None or not _text(basis):
        return {**report, "status": "unchecked", "reason": "missing_estimate_or_basis"}

    conflict = estimate > window and not math.isclose(estimate, window, rel_tol=0, abs_tol=1e-6)
    return {**report, "status": "conflict" if conflict else "fits",
            "estimated_minimum_seconds": estimate, "estimate_basis": basis.strip(),
            "shortfall_seconds": round(estimate - window, 6) if conflict else 0}


def load_project_request(project):
    path = Path(project) / REQUEST_PATH
    try:
        request = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取项目需求：{path}") from exc
    if not isinstance(request, dict):
        raise ValueError(f"项目需求必须是 JSON 对象：{path}")
    return request


def load_kickoff(project):
    request = load_project_request(project)
    if "kickoff" not in request:
        return None
    return validate_kickoff(request["kickoff"])


def validate_kickoff(kickoff, *, for_registration=False):
    if not isinstance(kickoff, dict) or not _text(kickoff.get("decision_ref")):
        raise ValueError("开工决定须登记 decision_ref")
    production_workflow(kickoff)
    product = kickoff.get("product")
    if not isinstance(product, dict) or not _text(product.get("mode")) or product["mode"] not in PRODUCT_MODES:
        raise ValueError("开工产品须明确选择 none / retain / replace / insert")
    promotion_fields = {"promotion_copy", "promotion_decision_ref", "promotion_dialogue"}
    if promotion_fields & product.keys():
        if product["mode"] == "none" or not all(
            _text(product.get(key)) for key in ("promotion_copy", "promotion_decision_ref")
        ):
            raise ValueError("活动口径须登记对应产品、promotion_copy 和 promotion_decision_ref")
        if "promotion_dialogue" in product and not _text(product["promotion_dialogue"]):
            raise ValueError("promotion_dialogue 须为用户确认的完整口播原文")
    if product["mode"] == "none":
        if "product_id" in product or "product_usage" in product:
            raise ValueError("无广告项目不能同时选定广告产品或用法")
    elif not _text(product.get("product_id")) or product.get("product_usage") not in ("display", "real_use"):
        raise ValueError("有广告时须登记准确 product_id 和 product_usage")
    else:
        product = _normalize_product_id(product)
    if for_registration:
        if product["mode"] != "none" and not {"reference_pack_revision", "reference_pack_sha256"} & product.keys():
            selected = next((item for item in product_catalog() if item["product_id"] == product["product_id"]), None)
            if selected:
                versions = selected["versions"]
                if len(versions) != 1:
                    raise ValueError("该商品有多个参考包版本，须指定准确 reference_pack_revision")
                product = {**product, "reference_pack_revision": versions[0]["revision"]}
        pack = resolve_product_pack(product, for_registration=True)
        if pack:
            product = {**product, "reference_pack_sha256": pack["manifest_sha256"]}
    else:
        validate_selection(product)
    kickoff = {**kickoff, "product": product}
    canvas = kickoff.get("canvas")
    if for_registration and isinstance(canvas, dict) and canvas.get("platform") == "redframe":
        canvas = {"aspect_ratio": "9:16", "resolution": "480p", **canvas, "model": REDFRAME_MODEL}
        kickoff = {**kickoff, "canvas": canvas}
    if "canvas" in kickoff:
        if not isinstance(canvas, dict) or not all(_text(canvas.get(key)) for key in (
            "platform", "model", "aspect_ratio", "resolution"
        )):
            raise ValueError("画布须登记平台、模型、画幅和分辨率")
        if canvas.get("target_url") is not None:
            url = urlsplit(canvas["target_url"] if _text(canvas["target_url"]) else "")
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
                raise ValueError("画布 target_url 必须是准确 HTTP(S) 地址")
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


def kickoff_project(project, answers=None):
    from .candidate_test import require_production
    from .errors import StageContextError
    from .io import project_lock, read_stage_json, write_json
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise StageContextError("开工决定必须从业务项目目录读取")
    if answers is not None:
        answers = validate_kickoff(answers, for_registration=True)
        with project_lock(project):
            require_production(project)
            path = project / REQUEST_PATH
            request = read_stage_json(path, default={})
            if not isinstance(request, dict):
                raise StageContextError("项目需求必须是 JSON 对象")
            previous = request.get("kickoff")
            existing = previous.get("product") if isinstance(previous, dict) else None
            selected = answers["product"]
            if "reference_pack_revision" in selected:
                choice = next((item for item in product_catalog()
                               if item["product_id"] == selected["product_id"]), None)
                current = {item["revision"] for item in (choice or {}).get("versions", [])}
                lock_fields = ("product_id", "reference_pack_revision", "reference_pack_sha256")
                if (choice and selected["reference_pack_revision"] not in current
                        and any((existing or {}).get(key) != selected[key] for key in lock_fields)):
                    raise StageContextError("历史产品包仅供已有同版本锁定的项目续用；请从当前版本选择")
            if (isinstance(existing, dict) and existing.get("reference_pack_sha256")
                    and selected.get("reference_pack_sha256")
                    and (existing.get("product_id"), existing.get("reference_pack_revision"))
                    == (selected["product_id"], selected["reference_pack_revision"])
                    and existing["reference_pack_sha256"] != selected["reference_pack_sha256"]):
                raise StageContextError("已有项目锁定的同一参考包版本字节已变化，请还原文件或选择新版本")
            write_json(path, {**request, "kickoff": answers})
    kickoff = load_kickoff(project)
    result = {"ok": kickoff is not None, "path": str(project / REQUEST_PATH), "kickoff": kickoff,
              "workflow": production_workflow(kickoff)}
    if kickoff is None:
        result["product_choices"] = product_catalog()
        result["product_choice_prompt"] = "请用户选择本项目使用哪款产品、提供其他准确商品，或明确无产品"
    if (timing := product_demo_timing_advisory(kickoff)) is not None:
        result["product_demo_timing"] = timing
    if kickoff and (pack := resolve_product_pack(kickoff["product"])):
        result["product_reference_pack"] = pack
    return result
