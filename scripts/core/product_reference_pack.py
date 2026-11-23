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


def _named_packs():
    """Read the identities of packages whose folder uses a display name."""
    for path in sorted(PACK_ROOT.glob("*/manifest.json")):
        if not path.resolve().is_relative_to(PACK_ROOT.resolve()):
            raise ValueError(f"产品参考包目录越界：{path}")
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError(f"产品参考包清单须为对象：{path}")
        product_id, revision = manifest.get("product_id"), manifest.get("revision")
        if not all(_matches(_COMPONENT, value) for value in (product_id, revision)):
            raise ValueError(f"产品参考包须登记稳定产品 ID 和版本：{path}")
        yield path, manifest


def product_catalog():
    """List installed choices without selecting a product or loading media."""
    products = {}
    superseded = set()
    manifests = list(_named_packs())
    for path in sorted(PACK_ROOT.glob("*/*/manifest.json")):
        product_id, revision = path.parent.parent.name, path.parent.name
        if not all(_matches(_COMPONENT, value) for value in (product_id, revision)):
            continue
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        if (manifest["product_id"], manifest["revision"]) != (product_id, revision):
            raise ValueError(f"商品目录与参考包身份不符：{path}")
        manifests.append((path, manifest))
    seen = set()
    for path, manifest in manifests:
        product_id, revision = manifest["product_id"], manifest["revision"]
        if (product_id, revision) in seen:
            raise ValueError(f"产品参考包有重复版本：{path}")
        seen.add((product_id, revision))
        product = products.setdefault(product_id, {"product_id": product_id, "versions": []})
        product["versions"].append({"revision": revision, "product_name": manifest["product_name"]})
        previous = manifest.get("supersedes_for_new_selection")
        if _matches(_COMPONENT, previous) and previous != revision:
            superseded.add((product_id, previous))
    for product_id, product in products.items():
        current = [row for row in product["versions"] if (product_id, row["revision"]) not in superseded]
        if not current:
            raise ValueError(f"产品参考包版本存在循环替代：{product_id}")
        product["versions"] = current
    return list(products.values())


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


def _local_file(root, relative, *, require_file=True):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("产品参考包文件须使用包内相对路径")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"产品参考包文件路径越界：{relative}")
    resolved = root.joinpath(*path.parts).resolve()
    if (not resolved.is_relative_to(root)
            or require_file and (not resolved.is_file() or resolved.stat().st_size == 0)):
        raise ValueError(f"产品参考包文件缺失、为空或越界：{relative}（{root}）")
    return resolved


def resolve_product_pack(product, *, for_registration=False, include_evidence=False):
    """Verify the pinned manifest and the files allowed for the selected usage."""
    if not product:
        return None
    validate_selection(product, for_registration=for_registration)
    if "reference_pack_revision" not in product:
        return None
    product_id, revision, usage = (product[key] for key in ("product_id", "reference_pack_revision", "product_usage"))
    packs = PACK_ROOT.resolve()
    named = [path.parent if manifest["revision"] == revision else path.parent / "历史" / "锁定版本" / revision
             for path, manifest in _named_packs() if manifest["product_id"] == product_id]
    named = [directory for directory in named if (directory / "manifest.json").is_file()]
    if len(named) > 1:
        raise ValueError(f"产品参考包有重复版本：{product_id}/{revision}")
    root = (named[0] if named else packs / product_id / revision).resolve()
    if not root.is_relative_to(packs) or root == packs:
        raise ValueError(f"产品参考包目录越界：{product_id}/{revision}")
    path = _local_file(root, "manifest.json")
    current_path = path
    raw = path.read_bytes()
    manifest_hash = hashlib.sha256(raw).hexdigest()
    expected_hash = product.get("reference_pack_sha256", manifest_hash)
    if expected_hash != manifest_hash:
        # A metadata correction can keep the release name. Explicit old
        # pins still use their original bytes and the same verified media.
        archived = _local_file(root, f"history/{expected_hash}/manifest.json", require_file=False)
        raw = archived.read_bytes() if archived.is_file() else b""
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError(f"产品参考包清单与开工锁定的版本不符：{path}")
        path = archived
        manifest_hash = expected_hash
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
    result = {"product_id": product_id, "product_name": name, "aliases": aliases, "revision": revision, "usage": usage,
              "manifest_path": str(path), "manifest_sha256": manifest_hash, "references": selected, "gaps": gaps}
    if path != current_path:
        result["current_manifest_path"] = str(current_path)
    if include_evidence:
        evidence = manifest.get("evidence_files", [])
        if not isinstance(evidence, list):
            raise ValueError(f"产品参考包证据须为列表：{path}")
        result["evidence_files"] = {}
        for item in evidence:
            if not isinstance(item, dict) or not _matches(_SHA256, item.get("sha256")):
                raise ValueError(f"产品参考包证据须登记文件和 SHA-256：{path}")
            file = _local_file(root, item.get("path"), require_file=False)
            if path != current_path:
                archived_file = _local_file(path.parent, item["path"], require_file=False)
                if archived_file.is_file():
                    file = archived_file
            result["evidence_files"][file] = item["sha256"]
    return result


def _same_file(path, expected):
    return path == expected or (path.is_file() and expected.is_file() and path.samefile(expected))


def resolve_pack_media(project, pack, reference, asset_requirements):
    """Resolve an exact medium from the project-selected pack and approved product requirement."""
    identity = reference.get("product_reference_id")
    entry = next((item for item in (pack or {}).get("references", []) if item["id"] == identity), None)
    if not entry or reference.get("sha256") != entry["sha256"] or not isinstance(reference.get("path"), str):
        raise ValueError("参考素材须匹配已锁产品包的条目和文件字节")
    canonical = Path(entry["path"])
    path = (project / reference["path"]).resolve()
    source = reference.get("source_pack_path")
    same_source = isinstance(source, str) and _same_file(Path(source), canonical)
    if not _same_file(path, canonical) and (not same_source
                              or not path.is_relative_to(project) or not path.is_file()
                              or sha256_file(path) != entry["sha256"]):
        raise ValueError("产品参考须为已锁产品包原件或来源和字节相同的项目副本")
    selected_paths = {Path(pack["manifest_path"]), canonical}
    if pack.get("current_manifest_path"):
        selected_paths.add(Path(pack["current_manifest_path"]))
    if not any(asset.get("asset_id") == reference.get("asset_id") and asset.get("category") == "REAL_PRODUCT"
               and any(_same_file((project / raw).resolve(), selected) for selected in selected_paths
                       for raw in asset.get("reference_paths", []) if isinstance(raw, str))
               for asset in asset_requirements):
        raise ValueError("产品参考须对应已采用编导稿中选用本包的真实商品")
    roles = reference.get("roles", [reference.get("role")])
    permitted = {
        "action": {"action"}, "technique": {"action"}, "packaging": {"appearance", "scale"},
        "material": {"appearance", "scale"}, "state": {"appearance", "state_reference"},
    }.get(entry["role"], set())
    if (not isinstance(roles, list) or not roles or any(not isinstance(role, str) or role not in permitted for role in roles)
            or path.suffix.lower() not in MEDIA_EXTENSIONS.get(reference.get("kind"), set())):
        raise ValueError("产品参考的媒体类型或实际用途与已锁产品包不符")
    if entry["role"] in {"action", "technique"}:
        actions = reference.get("action_references")
        if not isinstance(actions, list) or not actions or any(
                not isinstance(action, dict) or action.get("action_key") != entry["action_key"] for action in actions):
            raise ValueError("产品动作参考须匹配已锁产品包的准确动作")
    return path
