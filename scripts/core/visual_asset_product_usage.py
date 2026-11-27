"""Project-local product usage reference reader."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .io import sha256_file
from .project_kickoff import load_kickoff

FILE = "04_资产/产品使用对接.json"


def story_hash(narrative: dict[str, Any]) -> str:
    fields = {k: narrative.get(k) for k in ("revision", "story_paragraphs", "events")}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _usage_problem(status: str, message: str) -> dict[str, Any]:
    return {"status": status, "message": message, "references": [], "effects": []}


def _same_file(a: Path, b: Path) -> bool:
    if a.resolve() == b.resolve():
        return True
    return a.stat().st_size == b.stat().st_size and sha256_file(a) == sha256_file(b)


def _read_reference(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("产品参考文件缺失或为空：" + str(path))


def _matched_references(usage: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(usage.get("references"), list) or not isinstance(usage.get("effects"), list):
        raise ValueError("产品动作匹配缺少完整动作或效果清单")
    if any(row.get("status") not in {"matched", "missing"} for row in usage["references"]):
        raise ValueError("产品动作匹配状态无效")
    return [row for row in usage["references"] if row["status"] == "matched"]


def _material_rows(rows: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("产品配套参考清单须为数组")
    indexed = {row["asset_id"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("产品配套参考清单含重复登记")
    return indexed


def _verify_cached_file(row: dict[str, Any]) -> None:
    original, copy_path = Path(row["canonical_path"]), Path(row["project_path"])
    for p in {original, copy_path}:
        _read_reference(p)
    if not _same_file(original, copy_path):
        raise ValueError("项目参考副本与登记原件不同：" + str(copy_path))
    if row.get("sha256") and sha256_file(original) != row["sha256"]:
        raise ValueError("产品参考字节与匹配记录不同：" + str(original))


def load_usage(root: str | Path, narrative: dict[str, Any]) -> dict[str, Any]:
    path = Path(root) / FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("产品动作匹配结果须为对象")
        if data.get("story_hash") != story_hash(narrative):
            return _usage_problem("stale", "剧情已变化，产品动作参考待重新核对")
        kickoff = load_kickoff(Path(root))
        product = kickoff["product"] if kickoff else {}
        selection = (
            product.get("reference_pack_sha256"),
            product.get("product_usage") if product.get("reference_pack_sha256") else None,
        )
        if selection != (data.get("reference_pack_sha256"), data.get("reference_pack_usage")):
            return _usage_problem("stale", "开工产品参考已变化，待重新核对")
        if data.get("status") == "error":
            return _usage_problem("error", data.get("message", "产品动作参考待恢复"))
        if data.get("status") == "matched":
            references = _matched_references(data)
            if "material_references" not in data:
                raise ValueError("产品配套参考清单待恢复")
            materials = _material_rows(data["material_references"])
            if "material_asset_ids" in data and materials.keys() != set(data["material_asset_ids"]):
                raise ValueError("产品配套参考清单不完整")
            for row in [*references, *materials.values()]:
                _verify_cached_file(row)
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _usage_problem("error", str(exc))
