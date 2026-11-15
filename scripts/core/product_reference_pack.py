"""Resolve the locally installed product references selected at project kickoff."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from .io import sha256_file
from .media_types import MEDIA_EXTENSIONS


PACK_ROOT = Path(__file__).resolve().parents[2] / "assets" / "product-references"
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_USAGES = {"display", "real_use"}
_VISUAL_FORMATS = MEDIA_EXTENSIONS["image"] | MEDIA_EXTENSIONS["video"]
_ROLE_FORMATS = {
    "brief": {".json", ".md", ".txt", ".pdf"},
    "packaging": _VISUAL_FORMATS,
    "material": _VISUAL_FORMATS,
    "state": _VISUAL_FORMATS,
    "action": MEDIA_EXTENSIONS["video"],
    "technique": MEDIA_EXTENSIONS["video"],
}


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def current_product_pack_revision(product_id):
    """Find the sole installed version for a product with a kickoff default."""
    product_root = PACK_ROOT / product_id
    revisions = [path.parent.name for path in product_root.glob("*/manifest.json")
                 if _matches(_COMPONENT, path.parent.name) and path.is_file()]
    if not revisions:
        raise ValueError(f"未安装默认产品参考包：{product_root}")
    if len(revisions) != 1:
        raise ValueError(f"默认产品参考包存在多个版本，请指定准确版本：{product_root}")
    return revisions[0]


def validate_selection(product, *, for_registration=False):
    if "reference_pack_revision" not in product:
        if "reference_pack_sha256" in product:
            raise ValueError("须先选择产品参考包版本，再登记清单 SHA-256")
        return
    if product["mode"] == "none" or not _matches(_COMPONENT, product.get("product_id")):
        raise ValueError("选择产品参考包须登记准确产品 ID")
    if not _matches(_COMPONENT, product["reference_pack_revision"]):
        raise ValueError("产品参考包版本须为目录名，例如 R01")
    if for_registration and "reference_pack_sha256" not in product:
        return
    if not _matches(_SHA256, product.get("reference_pack_sha256")):
        raise ValueError("所选产品参考包须锁定清单 SHA-256；请通过 kickoff 登记")


def _local_file(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("产品参考包文件须使用包内相对路径")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"产品参考包文件路径越界：{relative}")
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"产品参考包文件缺失、为空或越界：{relative}（{root}）")
    return resolved


def resolve_product_pack(product, *, for_registration=False):
    """Verify the pinned manifest and the files allowed for the selected usage."""
    if not product:
        return None
    validate_selection(product, for_registration=for_registration)
    if "reference_pack_revision" not in product:
        return None
    product_id, revision, usage = (product[key] for key in ("product_id", "reference_pack_revision", "product_usage"))
    packs = PACK_ROOT.resolve()
    root = (packs / product_id / revision).resolve()
    if not root.is_relative_to(packs) or root == packs:
        raise ValueError(f"产品参考包目录越界：{product_id}/{revision}")
    path = _local_file(root, "manifest.json")
    raw = path.read_bytes()
    manifest_hash = hashlib.sha256(raw).hexdigest()
    if product.get("reference_pack_sha256", manifest_hash) != manifest_hash:
        raise ValueError(f"产品参考包清单与开工锁定的版本不符：{path}")
    manifest = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(manifest, dict) or (manifest.get("product_id"), manifest.get("revision")) != (product_id, revision):
        raise ValueError(f"产品参考包清单的产品或版本不符：{path}")
    name, entries, gaps = manifest.get("product_name"), manifest.get("references"), manifest.get("gaps", [])
    aliases = manifest.get("aliases", [])
    if (not isinstance(name, str) or not name.strip() or not isinstance(entries, list) or not entries
            or not isinstance(gaps, list) or not isinstance(aliases, list)
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)):
        raise ValueError(f"产品参考包清单须登记产品名称、参考列表和缺口：{path}")
    selected, ids = [], set()
    for item in entries:
        if not isinstance(item, dict) or not _matches(_COMPONENT, item.get("id")) or item["id"] in ids:
            raise ValueError(f"产品参考包条目须有不重复的 ID：{path}")
        ids.add(item["id"])
        usages, role = item.get("usages"), item.get("role")
        if (not isinstance(role, str) or role not in _ROLE_FORMATS or not isinstance(usages, list) or not usages
                or any(not isinstance(value, str) or value not in _USAGES for value in usages)
                or not _matches(_SHA256, item.get("sha256"))
                or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("path", "source", "coverage"))
                or (role in {"action", "technique"} and (usages != ["real_use"]
                    or not isinstance(item.get("action_key"), str) or not item["action_key"].strip()))):
            raise ValueError(f"产品参考包条目的用途、来源或 SHA-256 无效：{item['id']}")
        if usage not in usages:
            continue
        file = _local_file(root, item["path"])
        if file.suffix.lower() not in _ROLE_FORMATS[role]:
            raise ValueError(f"产品参考包文件格式与参考职责不符：{file}")
        if sha256_file(file) != item["sha256"]:
            raise ValueError(f"产品参考包文件 SHA-256 不符：{file}")
        selected.append({**item, "path": str(file)})
    if not selected:
        raise ValueError(f"产品参考包没有适用 {usage} 的参考：{path}")
    return {"product_id": product_id, "product_name": name, "aliases": aliases, "revision": revision, "usage": usage,
            "manifest_path": str(path), "manifest_sha256": manifest_hash, "references": selected, "gaps": gaps}
