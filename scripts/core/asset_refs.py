"""当前资产清单、资产变更影响扫描与素材图片门禁（只读报告，不改稿）。

权威来源是项目 `00_项目管理/artifact_manifest.json` 与已确认叙事包，派生规则见本模块
`build_asset_list()`——该函数是唯一权威，别处不再复述口径：
- 资产网页的每次改动（取消／恢复／新增）落盘后调用 `scan_and_write()`，产出受影响位置清单；
- `当前资产清单.json` 只是可重建快照，供人查看；工具读取与门禁一律走 `load_asset_list()` 现场派生；
- 视频提交前用 `--require-image AST-ID@OUTPUT-ID` 核对具体确认输出，无图、输出不存在或已被
  项目明确拒用都直接阻塞。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from .character_assets import character_type, uses_wardrobe

if __package__ == "scripts.core":
    from ..visual_asset_choices import effective, read_choices  # type: ignore
    from ..visual_asset_product_usage import load_usage
    from .artifacts import artifact_is_confirmed, digest, load_manifest, project_path, verify_image_confirmation  # type: ignore
    from .project_state import load_project_state  # type: ignore
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from visual_asset_choices import effective, read_choices  # type: ignore
    from visual_asset_product_usage import load_usage
    from core.artifacts import artifact_is_confirmed, digest, load_manifest, project_path, verify_image_confirmation  # type: ignore
    from core.project_state import load_project_state  # type: ignore

ASSET_LIST = "04_资产/当前资产清单.json"
IMPACT_DIR = "04_资产/变更影响"
ASSET_TABLE = "04_资产/当前资产需求表.md"
MASTER_DIR = "04_资产/母版"
OUTPUT_DECISIONS = "00_项目管理/资产输出适用决定.json"
SCAN_GLOBS = ("03_导演/*.md", "03_导演/*.json", "05_分镜/*.md", "05_分镜/**/*.json", "06_视频/*.md", "06_视频/**/*.json")

# 图片之外的别名，只用于“恢复／新增资产后应补引用”的建议检索。
NAME_ALIASES = {
    "AST-PROP-LAPTOP": ["电脑", "笔记本"],
    "AST-PROP-BATTERY": ["电池"],
    "AST-PROP-MIC": ["麦克"],
    "AST-PROP-BEAUTY-CARD": ["美容卡"],
    "AST-PROP-MAOTAI": ["茅台"],
    "AST-PROP-MASK-AB": ["双剂", "A剂", "B剂", "白A", "金B"],
    "AST-PROP-MASK-BOX": ["礼盒", "盒子", "盒盖", "合盖"],
}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _confirmation_file(root: Path, ref: str) -> tuple[Path | None, str]:
    """Resolve a manifest confirmation reference without treating its text as a guess."""
    path_text, marker, fragment = ref.partition("#")
    path = project_path(root, path_text)
    return (path if path.is_file() else None), (fragment if marker else "")


def _image_path(root: Path, value: str) -> Path:
    return project_path(root, value)


def _output_decisions(root: Path) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    """Read project-local decisions that supersede output applicability, not history.

    The file is optional for old projects.  A decision must identify one exact
    ``asset_id`` + ``output_id`` pair.  Malformed or ambiguous records are
    surfaced as issues and never widened to the whole asset or silently ignored.
    Later valid records for the same pair supersede earlier ones, which permits
    an explicit user-approved re-adoption without rewriting the old rejection.
    """
    path = root / OUTPUT_DECISIONS
    if not path.is_file():
        return {}, []
    try:
        value = _read(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, [{"message": f"资产输出适用决定文件无法读取：{exc}"}]
    if not isinstance(value, dict) or value.get("schema_version") != "asset-output-applicability-1.0":
        return {}, [{"message": "资产输出适用决定文件结构或版本无效"}]
    records = value.get("decisions")
    if not isinstance(records, list):
        return {}, [{"message": "资产输出适用决定缺少 decisions 数组"}]
    decisions: dict[tuple[str, str], dict] = {}
    issues: list[dict] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            issues.append({"message": f"资产输出适用决定第 {index} 条不是对象"})
            continue
        asset_id = record.get("asset_id")
        output_id = record.get("output_id")
        decision_id = record.get("decision_id")
        status = record.get("status")
        evidence_refs = record.get("evidence_refs")
        valid = (
            isinstance(asset_id, str) and asset_id.startswith("AST-")
            and isinstance(output_id, str) and bool(output_id)
            and isinstance(decision_id, str) and bool(decision_id) and decision_id not in seen_ids
            and status in {"rejected", "adopted"}
            and record.get("scope") == "project"
            and isinstance(record.get("reason"), str) and bool(record["reason"].strip())
            and isinstance(record.get("user_instruction"), str) and bool(record["user_instruction"].strip())
            and isinstance(record.get("decided_at"), str) and bool(record["decided_at"].strip())
            and isinstance(evidence_refs, list) and bool(evidence_refs)
            and all(isinstance(item, str) and item for item in evidence_refs)
        )
        if not valid:
            issues.append({
                "asset_id": asset_id if isinstance(asset_id, str) else None,
                "output_id": output_id if isinstance(output_id, str) else None,
                "message": f"资产输出适用决定第 {index} 条字段无效或范围不明确",
            })
            continue
        seen_ids.add(decision_id)
        decisions[(asset_id, output_id)] = dict(record)
    return decisions, issues


def _decision_issues_for_request(root: Path, request: str) -> list[str]:
    _, issues = _output_decisions(root)
    asset_id, separator, output_id = request.partition("@")
    messages = []
    for issue in issues:
        issue_asset = issue.get("asset_id")
        issue_output = issue.get("output_id")
        if issue_asset not in (None, asset_id):
            continue
        if separator and issue_output not in (None, "", output_id):
            continue
        messages.append(str(issue["message"]))
    return messages


def _apply_output_decisions(root: Path, images: dict[str, dict]) -> None:
    decisions, _ = _output_decisions(root)
    for asset_id, image in images.items():
        available: list[dict] = []
        unavailable: list[dict] = []
        for original in image.get("outputs", []):
            output = dict(original)
            decision = decisions.get((asset_id, str(output.get("output_id") or "")))
            if decision is None:
                output["applicability"] = {"status": "available", "source": "confirmation"}
                available.append(output)
                continue
            output["applicability"] = {
                "status": "unavailable" if decision["status"] == "rejected" else "available",
                "source": OUTPUT_DECISIONS,
                "decision_id": decision["decision_id"],
                "reason": decision["reason"],
                "user_instruction": decision["user_instruction"],
                "decided_at": decision["decided_at"],
                "evidence_refs": decision["evidence_refs"],
            }
            (unavailable if decision["status"] == "rejected" else available).append(output)
        image["outputs"] = available
        image["unavailable_outputs"] = unavailable
        image["files"] = [item["path"] for item in available]
        if image.get("selected_path") and not any(
            item.get("path") == image["selected_path"] for item in available
        ):
            image["selected_path"] = None
        if not available and unavailable:
            # Confirmation history remains attached to the unavailable outputs,
            # but this asset has no output that the current project may use.
            image["status"] = "unavailable"
            image["availability_reason"] = "all_confirmed_outputs_rejected"


def _confirmed_output(root: Path, asset_id: str, output: dict) -> dict | None:
    """Return one explicitly confirmed output, or None when its evidence is incomplete.

    Candidates are deliberately absent here.  A confirmation record can be concise, but
    it must at least be the file named by the manifest and either identify the approved
    item or be the replacement record for this exact file.
    """
    path_text = output.get("path")
    reference = output.get("user_confirmation_ref")
    if not isinstance(path_text, str) or not isinstance(reference, str) or not reference:
        return None
    image_path = _image_path(root, path_text)
    if not image_path.is_file():
        return None
    receipt_path, fragment = _confirmation_file(root, reference)
    if receipt_path is None:
        return None
    try:
        receipt = _read(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(receipt, dict):
        return None
    items = receipt.get("items")
    if isinstance(items, list) and items:
        matched = [item for item in items if isinstance(item, dict)
            and item.get("ast_id") == asset_id
            and item.get("output_id") == output.get("output_id")
            and isinstance(item.get("confirmation"), dict)
            and item["confirmation"].get("id") == fragment
        ]
        if len(matched) != 1:
            return None
        image_receipt = matched[0]["confirmation"]
    elif isinstance(receipt.get("images"), list):
        # Legacy scene selection receipts identify every selected image directly.
        # The fragment is the image/output ID in that record, so require all
        # three facts rather than accepting any fragment in the same folder.
        matched = [item for item in receipt["images"] if isinstance(item, dict)
            and item.get("output_id") == output.get("output_id") == fragment
            and _image_path(root, str(item.get("path", ""))) == image_path
        ]
        if len(matched) != 1:
            return None
        image_receipt = matched[0]
    elif not fragment:
        # A replacement record has no per-output receipt ID.  It only proves the
        # one file it names, never every asset in the manifest.
        frame = receipt.get("frame_path") if isinstance(receipt, dict) else None
        if not isinstance(frame, str) or _image_path(root, frame) != image_path:
            return None
        image_receipt = receipt
    else:
        return None
    try:
        verify_image_confirmation(image_path, image_receipt)
    except (ValueError, OSError):
        return None
    return {
        "output_id": output.get("output_id"),
        "purpose": output.get("purpose"),
        "view": output.get("view"),
        "state": output.get("state"),
        "candidate_id": output.get("candidate_id"),
        "path": str(image_path),
        "confirmation_ref": reference,
        "confirmed_at": str(receipt.get("at") or ""),
        "path_exists": True,
    }


def _legacy_confirmed_output(root: Path, asset: dict) -> dict | None:
    """Project a trustworthy pre-2.0 confirmed_asset into one main output."""
    asset_id = asset.get("asset_id")
    selected = asset.get("confirmed_asset")
    if not isinstance(asset_id, str) or not isinstance(selected, dict):
        return None
    path_text = selected.get("path")
    candidate_id = selected.get("candidate_id")
    reference = selected.get("user_confirmation_ref")
    if not all(isinstance(value, str) and value for value in (path_text, candidate_id, reference)):
        return None
    image_path = _image_path(root, path_text)
    if not image_path.is_file():
        return None
    candidate_matches = any(
        isinstance(candidate, dict)
        and candidate.get("candidate_id") == candidate_id
        and isinstance(candidate.get("path"), str)
        and _image_path(root, candidate["path"]) == image_path
        for candidate in asset.get("candidates", [])
    )
    receipt_path, fragment = _confirmation_file(root, reference)
    if not candidate_matches or receipt_path is None or not fragment:
        return None
    try:
        receipt = _read(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    items = receipt.get("items") if isinstance(receipt, dict) else None
    matched = [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)
        and item.get("ast_id") == asset_id
        and isinstance(item.get("confirmation"), dict)
        and item["confirmation"].get("id") == fragment
    ]
    if len(matched) != 1:
        return None
    try:
        verify_image_confirmation(image_path, matched[0]["confirmation"])
    except (ValueError, OSError):
        return None
    return {
        "output_id": "legacy-main", "purpose": "legacy-main", "view": "legacy-main", "state": "",
        "candidate_id": candidate_id, "path": str(image_path), "confirmation_ref": reference,
        "confirmed_at": str(receipt.get("at") or ""), "path_exists": True,
    }


def confirmed_images(root: Path) -> dict[str, dict]:
    """Read current, explicitly confirmed outputs from the newest asset manifest.

    ``candidates`` are production history, not approved material.  Likewise a
    replacement frame is scoped by the manifest output that names it; it must not
    be appended to unrelated assets merely because the record shares a folder.
    Registered manifests must still be current and confirmed.  Unregistered
    legacy manifests retain their per-output confirmation compatibility.
    """
    master = root / MASTER_DIR
    result: dict[str, dict] = {}
    if not master.is_dir():
        return result
    registrations: dict[Path, list[dict]] = {}
    for entry in load_manifest(root).get("artifacts", []):
        path_text = entry.get("path_or_url")
        if isinstance(path_text, str) and path_text:
            path = project_path(root, path_text)
            registrations.setdefault(path, []).append(entry)
            if (entry.get("type") == "visual-asset-manifest" and entry.get("status") == "current"
                    and entry.get("checksum")
                    and (not path.is_file() or digest(path) != entry["checksum"])):
                # Its changed contents cannot tell us which old ASTs to erase.
                # Stop before merging, rather than reviving an earlier legacy image.
                raise ValueError("已登记资产导出清单内容已变化或缺失，请重新核对：" + str(path))
    state = load_project_state(root)
    for folder in sorted(p for p in master.iterdir() if p.is_dir()):
        manifest_path = folder / "visual-asset-manifest.json"
        if not manifest_path.is_file():
            continue
        entries = registrations.get(manifest_path.resolve())
        # Unadopted/replaced revisions are history, not evidence that the
        # currently confirmed image has disappeared. Inspect status before
        # reading their files; even an unfinished candidate must not block it.
        if entries and all(entry.get("status") in {"candidate", "rejected", "superseded"}
                           for entry in entries):
            continue
        manifest = _read(manifest_path)
        current_manifest = entries is None or any(
            entry.get("artifact_id") in state["current_artifacts"]
            and artifact_is_confirmed(entry, state, root)
            and (not entry.get("checksum") or digest(manifest_path) == entry["checksum"])
            for entry in entries
        )
        for asset in manifest.get("assets", []):
            asset_id = asset.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            if entries is None and asset.get("status") in {"candidate", "awaiting_user_review", "rejected"}:
                continue  # Legacy candidates have not replaced the adopted output either.
            # An adopted but stale/invalid output is a current gap, not
            # permission to revive an older master.
            result.pop(asset_id, None)
            if not current_manifest or asset.get("status") != "confirmed":
                continue
            outputs = [item for item in (
                _confirmed_output(root, asset_id, output)
                for output in asset.get("confirmed_outputs", [])
                if isinstance(output, dict)
            ) if item is not None]
            selected = asset.get("confirmed_asset")
            if "confirmed_outputs" not in asset:
                legacy = _legacy_confirmed_output(root, asset)
                outputs = [legacy] if legacy else []
            if not outputs:
                continue
            # 确认时间随投影一起返回，不再为每个输出重读一次确认记录。
            confirmed_at = max((str(item.get("confirmed_at") or "") for item in outputs), default="")
            selected_path = selected.get("path") if isinstance(selected, dict) else None
            if isinstance(selected_path, str):
                selected_path = str(_image_path(root, selected_path))
            if selected_path and not any(item["path"] == selected_path for item in outputs):
                selected_path = None
            result[asset_id] = {
                "status": "confirmed",
                "manifest": str(manifest_path.relative_to(root)),
                "confirmed_at": confirmed_at,
                "selected_path": selected_path,
                # Retain the old lightweight interface for consumers that only
                # need paths.  Detailed selection must use outputs.
                "files": [item["path"] for item in outputs],
                "outputs": outputs,
            }
    _apply_output_decisions(root, result)
    return result


def build_asset_list(root: Path, narrative: dict, choices: dict | None = None) -> dict:
    """当前资产清单：state + 在场区间 + 图片状态。"""
    choices = choices if choices is not None else read_choices(root)
    images = confirmed_images(root)
    usage = load_usage(root, narrative)
    items = []
    for requirement in effective(narrative, choices):
        asset_id = requirement["asset_id"]
        items.append({
            "asset_id": asset_id,
            "name": requirement["name"],
            "category": requirement["category"],
            **({'character_type': character_type(requirement), 'uses_wardrobe': uses_wardrobe(requirement)}
               if requirement['category'] == 'CHARACTER' else {}),
            "state": {"无需独立图": "no_image", "待定": "pending"}.get(requirement.get("image_decision"), "needs_image"),
            "image_decision": requirement.get("image_decision", "旧表未记录判断"),
            "image_reason": requirement.get("image_reason", "沿用历史确认；实质修订时补齐"),
            "appearance": requirement.get("source_paragraph_ids", []),
            "image": images.get(asset_id, {"status": "missing"}),
            "action_references": [r for r in usage.get('references', []) if r['product_ast_id'] == asset_id],
        })
    for asset_id, item in choices.get("excluded", {}).items():
        items.append({"asset_id": asset_id, "name": item.get("name", ""), "category": item.get("category", ""),
                      "state": "excluded", "appearance": [],
                      "image": images.get(asset_id, {"status": "missing"}),
                      "note": item.get("message", "用户明确不需要该资产图")})
    for asset_id, item in choices.get("replaced", {}).items():
        items.append({"asset_id": asset_id, "name": item.get("name", ""), "category": item.get("category", ""),
                      "state": "split_parent", "appearance": [], "image": {"status": "missing"},
                      "children": item.get("children", []),
                      "note": "已拆分，引用请用子项"})
    return {"schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(),
            "authoritative": False,
            "authority": "00_项目管理/artifact_manifest.json + 已确认叙事包",
            "note": "派生快照，仅供查看；读取与门禁以 core.asset_refs.load_asset_list() 现场派生为准",
            "adjustment_revision": choices.get("revision", 0),
            "source_narrative_revision": narrative.get("revision"),
            "items": items,
            "product_usage": usage}


def write_asset_list(root: Path, narrative: dict, choices: dict) -> Path:
    path = root / ASSET_LIST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_asset_list(root, narrative, choices), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def load_asset_list(root: Path, narrative: dict | None = None, choices: dict | None = None) -> dict:
    if narrative is None:
        narrative = current_narrative(root)
    # The JSON file is a convenience snapshot, never an authority for image
    # confirmation.  Rebuild in memory so an old candidate-polluted snapshot
    # cannot be re-exported by a later tool invocation.
    return build_asset_list(root, narrative, choices)


def current_narrative(root: Path) -> dict:
    """读取权威：唯一已确认的 narrative.package，绝不按文件名猜最新稿。"""
    manifest = load_manifest(root)
    state = load_project_state(root)
    entries = [entry for entry in manifest.get("artifacts", [])
               if entry.get("logical_id") == "narrative.package" and entry.get("status") == "current"]
    if (len(entries) != 1 or entries[0].get("artifact_id") not in state["current_artifacts"]
            or not artifact_is_confirmed(entries[0], state, root)):
        raise ValueError(
            "当前资产清单只认唯一且已确认的 narrative.package，"
            f"现在清单里符合的条目为 {len(entries)} 条；不按文件名猜最新稿。"
            "请先确认编导稿并登记该产物，再核对 "
            "00_项目管理/artifact_manifest.json 与 00_项目管理/project_state.json 的确认记录。"
        )
    path = project_path(root, entries[0].get("path_or_url", ""))
    if not path.is_file():
        raise FileNotFoundError(
            f"narrative.package 已登记但文件不存在：{path}；"
            "请恢复该文件或重新登记当前版本，工具不会回退到其他修订。"
        )
    checksum = entries[0].get("checksum")
    if checksum and digest(path) != checksum:
        raise ValueError("已确认叙事包内容与登记版本不一致，请回上游核对：" + str(path))
    return _read(path)


def asset_index(asset_list: dict) -> dict[str, dict]:
    return {item["asset_id"]: item for item in asset_list["items"]}


def _aliases(item: dict) -> list[str]:
    names = [item["name"]] + NAME_ALIASES.get(item["asset_id"], [])
    return [n for n in dict.fromkeys(names) if n]


MANIFEST = "00_项目管理/artifact_manifest.json"


def current_files(root: Path, include: list[str] | None = None) -> list[Path]:
    """只扫当前候选：登记为 current 的产物 + 未带修订号的活跃文档 + 同目录上不低于当前修订号的候选。"""
    registered: set[str] = set()
    floor: dict[str, int] = {}
    manifest_path = root / MANIFEST
    if manifest_path.is_file():
        for item in _read(manifest_path).get("artifacts", []):
            if item.get("status") != "current" or not item.get("path_or_url"):
                continue
            path = project_path(root, item["path_or_url"])
            try:
                rel = str(path.relative_to(root.resolve()))
            except ValueError:
                continue
            registered.add(rel)
            match = re.match(r"^R(\d+)_", Path(rel).name)
            if match:
                folder = str(Path(rel).parent)
                floor[folder] = max(floor.get(folder, 0), int(match.group(1)))
    out: set[Path] = set()
    for pattern in SCAN_GLOBS:
        for path in root.glob(pattern):
            rel = str(path.relative_to(root))
            if rel.startswith("99_历史与废弃"):
                continue
            if rel in registered or (include and rel in include):
                out.add(path)
                continue
            match = re.match(r"^R(\d+)_", path.name)
            if match is None:
                out.add(path)
                continue
            folder = str(path.parent.relative_to(root))
            if folder in floor and int(match.group(1)) >= floor[folder]:
                out.add(path)
    return sorted(out)


def _document_fragments(path: Path):
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() != '.json':
        return [(number, line, None) for number, line in enumerate(text.splitlines(), 1)]
    def walk(value, pointer='$'):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from walk(item, pointer + '/' + key.replace('~', '~0').replace('/', '~1'))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                yield from walk(item, pointer + '/' + str(i))
        elif isinstance(value, str):
            yield None, pointer + ': ' + value, pointer
    return list(walk(json.loads(text)))


def scan_references(root: Path, asset_ids: list[str], asset_list: dict | None = None,
                    name_hit_limit: int = 8) -> dict:
    """扫描当前候选里对指定资产的引用位命中与名字命中。"""
    asset_list = asset_list or load_asset_list(root)
    index = asset_index(asset_list)
    scanned = current_files(root)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "adjustment_revision": asset_list.get("adjustment_revision"),
              "source_narrative_revision": asset_list.get("source_narrative_revision"),
              "scanned_files": [str(p.relative_to(root)) for p in scanned],
              "assets": []}
    for asset_id in asset_ids:
        item = index.get(asset_id, {"asset_id": asset_id, "name": "", "state": "unknown"})
        references: list[dict] = []
        names: list[dict] = []
        name_total = 0
        for path in scanned:
            try:
                lines = _document_fragments(path)
            except (UnicodeDecodeError, OSError):
                continue
            for number, line, pointer in lines:
                if asset_id in line:
                    is_reference = (line.lstrip().startswith("| 来源") or "资产：" in line
                                    or "source_asset_requirement_ids" in line
                                    or (pointer is not None and any(key in pointer.split("/") for key in ("asset_id", "asset_ids", "materials", "references", "reference_checks")))
                                    or re.match(r"^\s*[-|]\s*素材", line) is not None)
                    references.append({"file": str(path.relative_to(root)), "line": number,
                                       "kind": "reference" if is_reference else "mention",
                                       **({"json_path": pointer} if pointer else {}),
                                       "text": line.strip()[:200]})
                    continue
                if item.get("name") and any(alias in line for alias in _aliases(item)):
                    name_total += 1
                    if len(names) < name_hit_limit:
                        names.append({"file": str(path.relative_to(root)), "line": number,
                                      "kind": "name_hit", **({"json_path": pointer} if pointer else {}), "text": line.strip()[:200]})
        action = {"needs_image": "keep", "excluded": "remove_reference",
                  "split_parent": "replace_with_child", "unknown": "register_first"}.get(item["state"], "review")
        report["assets"].append({"asset_id": asset_id, "name": item.get("name", ""), "state": item["state"],
                                 "children": item.get("children", []), "action": action,
                                 "image": item.get("image", {"status": "missing"}),
                                 "reference_hits": references, "name_hit_total": name_total,
                                 "name_hits": names})
    return report


def scan_and_write(root: Path, narrative: dict, asset_ids: list[str], choices: dict | None = None) -> Path:
    report = scan_references(root, [a for a in asset_ids if a], None)
    folder = root / IMPACT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    revision = report.get("adjustment_revision") or 0
    path = folder / f"R{revision:03d}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _annotate_table(root, report)
    return path


def _annotate_table(root: Path, report: dict) -> None:
    table = root / ASSET_TABLE
    if not table.is_file():
        return
    text = table.read_text(encoding="utf-8")
    text = re.sub(r"\n> 资产变更影响：.*?(?=\n[^>]|\Z)", "", text, flags=re.S)
    lines = [f"- {a['asset_id']}（{a['state']}）：{a['action']}，引用位命中 {len(a['reference_hits'])} 处、文字命中 {a['name_hit_total']} 处"
             for a in report["assets"] if a["reference_hits"] or a["name_hit_total"]]
    if not lines:
        return
    note = "\n> 资产变更影响：" + "；".join(lines) + f"；明细见 {IMPACT_DIR}/R{report.get('adjustment_revision') or 0:03d}.json"
    head, _, body = text.partition("\n|")
    table.write_text(head + note + "\n|" + body, encoding="utf-8")


def _requested_output(item: dict, request: str) -> tuple[dict | None, str | None]:
    """Resolve ``AST-ID`` or ``AST-ID@output_id`` without guessing a version."""
    asset_id, separator, output_id = request.partition("@")
    if not separator:
        selected = item.get("image", {}).get("selected_path")
        if selected:
            return next((output for output in item["image"].get("outputs", []) if output.get("path") == selected), None), None
        return None, "没有明确选中的确认图片；请指定 @output_id"
    image = item.get("image", {})
    matches = [output for output in (
        list(image.get("outputs", [])) + list(image.get("unavailable_outputs", []))
    ) if output.get("output_id") == output_id]
    if not matches:
        return None, f"未找到已确认输出 {output_id}"
    output = matches[0]
    applicability = output.get("applicability") or {}
    if applicability.get("status") == "unavailable":
        return output, (
            f"输出 {output_id} 已被当前项目明确拒用：{applicability.get('reason')}"
            f"（决定 {applicability.get('decision_id')}）"
        )
    return output, None


def scope_assets(root: Path, requests: list[str]) -> tuple[list[dict], list[str]]:
    """Return only requested AST/output facts for a production segment."""
    index = asset_index(load_asset_list(root))
    result: list[dict] = []
    problems: list[str] = []
    for request in requests:
        asset_id = request.partition("@")[0]
        item = index.get(asset_id)
        if item is None:
            problems.append(f"{asset_id}：不在当前资产清单中")
            continue
        image = item.get("image", {})
        entry = {"asset_id": asset_id, "name": item.get("name"), "state": item.get("state"),
                 "appearance": item.get("appearance", []), "image_status": image.get("status", "missing"),
                 "manifest": image.get("manifest"), "selected_path": image.get("selected_path"),
                 "availability_reason": image.get("availability_reason")}
        entry['action_references'] = item.get('action_references', [])
        if item.get('category') == 'CHARACTER':
            entry['character_type'] = item.get('character_type', 'HUMAN')
            entry['uses_wardrobe'] = item.get('uses_wardrobe', True)
        for decision_issue in _decision_issues_for_request(root, request):
            problems.append(f"{asset_id}：{decision_issue}")
        output, issue = _requested_output(item, request)
        if request.partition("@")[1]:
            if issue:
                problems.append(f"{asset_id}：{issue}")
            if output is not None:
                entry["outputs"] = [output]
        else:
            entry["outputs"] = image.get("outputs", [])
            entry["unavailable_outputs"] = image.get("unavailable_outputs", [])
        result.append(entry)
    return result, problems


def require_images(root: Path, asset_ids: list[str]) -> tuple[bool, list[str]]:
    from .revision_control import guard_action
    try:
        guard_action(root, logical_ids=['narrative.package', 'visual.asset.manifest'])
    except ValueError as exc:
        return False, [str(exc)]
    asset_list = load_asset_list(root)
    index = asset_index(asset_list)
    problems = []
    for request in asset_ids:
        asset_id = request.partition("@")[0]
        item = index.get(asset_id)
        for decision_issue in _decision_issues_for_request(root, request):
            problems.append(f"{asset_id}：{decision_issue}")
        if item is None:
            problems.append(f"{asset_id}：不在当前资产清单中，先登记需求")
        elif item["state"] != "needs_image":
            problems.append(f"{asset_id}：状态 {item['state']}，不得放进素材清单")
        elif "@" in request:
            output, issue = _requested_output(item, request)
            if issue:
                problems.append(f"{asset_id}：{issue}")
            elif not output.get("path_exists"):
                problems.append(f"{asset_id}：选中的确认图片不存在：{output.get('path')}")
        elif item.get("image", {}).get("status") == "unavailable":
            rejected_count = len(item.get("image", {}).get("unavailable_outputs", []))
            problems.append(
                f"{asset_id}：没有当前项目可用的确认输出"
                f"（{rejected_count} 个已确认输出均被明确拒用）"
            )
        elif item.get("image", {}).get("status") != "confirmed":
            problems.append(f"{asset_id}：没有已确认母版图片，禁止放进素材清单（引用空白图）")
    return not problems, problems



def require_references(root: Path, artifact_ids: list[str], shot_ids: list[str] | None = None) -> tuple[bool, list[str]]:
    """Check exact registered supplemental images/previs, without adopting anything.

    This proves file/version availability only; the caller must still inspect the
    selected frame's action phase, shared state and permitted visual role.
    """
    if not artifact_ids:
        return False, ["未提供具体确认参考；身份/服装板不能替代场内比例或切点参考"]
    manifest = load_manifest(root)
    state = load_project_state(root)
    problems = []
    for ident in dict.fromkeys(artifact_ids):
        try:
            found = [a for a in manifest['artifacts'] if a.get('artifact_id') == ident]
            if len(found) != 1:
                raise ValueError('参考产物登记缺失或不唯一')
            entry = found[0]
            permitted_shots = set()
            from .revision_control import guard_action
            guard_action(root, logical_ids=[entry['logical_id']])
            if (ident not in state['current_artifacts']
                    or not artifact_is_confirmed(entry, state, root)):
                raise ValueError('参考尚未确认、已返工失效或被新版替代')
            path = project_path(root, entry['path_or_url'])
            if not entry.get('checksum') or not path.is_file() or digest(path) != entry['checksum']:
                raise ValueError('参考缺少可核对的登记指纹、文件丢失或内容已变化')
            if entry.get('type') == 'previs-reference':
                if __package__ == 'scripts.core':
                    from ..blender_previs.project import confirmed_reference_data
                else:
                    from blender_previs.project import confirmed_reference_data
                _, previs = confirmed_reference_data(root, ident)
                permitted_shots = {previs["shot_id"]}
            elif entry.get('type') != 'visual-supplement-image' or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('请引用具体确认分镜图或白模参考；AST母图仍用 --require-image')
            else:
                # Existing image receipts are either a single hash-bound image,
                # a named member of a batch, or the revision-control adoption.
                from .confirmation_scope import is_whole_artifact
                from .artifacts import formal_adoption
                confirmation = entry.get('confirmation_ref')
                if not confirmation:
                    exact = any(r.get('kind') == 'revision-candidate'
                                and r.get('artifact_id') == ident and is_whole_artifact(r)
                                and r.get('sha256') == entry['checksum'] and r.get('approval_ref')
                                for r in state['user_confirmations'])
                    if not exact and not formal_adoption(entry, root):
                        raise ValueError('分镜图缺少绑定该版本的具体确认依据')
                else:
                    receipt_path, _ = _confirmation_file(root, confirmation)
                    receipt = _read(receipt_path) if receipt_path else {}
                    if not isinstance(receipt, dict) or not is_whole_artifact(receipt) or ident not in receipt.get('artifact_ids', []):
                        raise ValueError('确认记录未覆盖这张分镜图')
                    if 'images' in receipt:
                        if not isinstance(receipt['images'], list):
                            raise ValueError('批量确认图片明细无效')
                        selected = [r for r in receipt['images'] if isinstance(r, dict) and r.get('artifact_id') == ident]
                        if len(selected) != 1:
                            raise ValueError('批量确认中缺少这张图的唯一明细')
                        image = selected[0]
                    else:
                        image = {'path': receipt.get('image_path'), 'sha256': receipt.get('image_sha256')}
                    if (not image.get('path') or project_path(root, image['path']) != path
                            or image.get('sha256') != entry['checksum']):
                        raise ValueError('确认记录的图片路径/指纹与当前版本不一致')
                    permitted_shots = set((image.get('usage') or receipt.get('usage') or {}).get('source_shot_ids', []))
                    white = image.get('white_reference')
                    if white:
                        white_path = project_path(root, white['path'])
                        if not white_path.is_file() or digest(white_path) != white.get('sha256'):
                            raise ValueError('本图共同使用的确认白模图已变化或缺失')

            if entry.get('type') == 'visual-supplement-image':
                from .quality_evidence import validate_image_evidence
                validate_image_evidence(root, path, entry.get('quality_evidence'), shot_ids)

            if shot_ids and not set(shot_ids) <= permitted_shots:
                raise ValueError('本图确认用途未覆盖这些镜头，不能自行扩用：' + ', '.join(sorted(set(shot_ids) - permitted_shots)))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            problems.append(f'{ident}：{exc}')
    return not problems, problems

def main() -> int:
    parser = argparse.ArgumentParser(description="当前资产清单、变更影响扫描与素材图片门禁（只读）")
    parser.add_argument("--project", required=True)
    parser.add_argument("--list", action="store_true", help="打印当前资产清单摘要")
    parser.add_argument("--scope", nargs="+", metavar="AST-ID[@OUTPUT-ID]", help="只输出本段关联资产及明确确认输出")
    parser.add_argument("--scan", nargs="+", metavar="AST-ID", help="扫描这些资产的引用影响")
    parser.add_argument("--write", action="store_true", help="把扫描结果写入变更影响目录并标注资产表")
    parser.add_argument("--require-image", nargs="+", metavar="AST-ID[@OUTPUT-ID]", help="图片可用性门禁；上传所用版本必须指定 @OUTPUT-ID")
    parser.add_argument("--require-reference", nargs="+", metavar="ART-ID", help="核对明确的分镜图/白模参考版本；不证明切点状态或视觉用途正确")
    parser.add_argument("--for-shots", nargs="+", metavar="SHOT-ID", help="限定本次非AST参考的获准镜号范围；不判定视觉状态/比例是否正确")
    args = parser.parse_args()
    if args.for_shots and not args.require_reference:
        parser.error('--for-shots 必须与 --require-reference 一起使用')
    root = Path(args.project).expanduser().resolve()
    if args.list:
        asset_list = load_asset_list(root)
        print(f"叙事修订 {asset_list.get('source_narrative_revision')}｜资产调整修订 {asset_list.get('adjustment_revision')}")
        for item in asset_list["items"]:
            image = item.get("image", {}).get("status", "missing")
            print(f"  {item['asset_id']:<26} {item['state']:<12} 图片={image:<9} {item['name']}")
        return 0
    if args.scope:
        entries, problems = scope_assets(root, args.scope)
        print(json.dumps({"assets": entries, "problems": problems}, ensure_ascii=False, indent=2))
        return 0 if not problems else 2
    if args.scan:
        if args.write:
            narrative = current_narrative(root)
            path = scan_and_write(root, narrative, args.scan)
            print(f"已写入 {path.relative_to(root)}")
        report = scan_references(root, args.scan)
        for asset in report["assets"]:
            print(f"{asset['asset_id']}（{asset['state']}／建议 {asset['action']}/"
                  f"图片={asset['image'].get('status')}）：引用位命中 {len(asset['reference_hits'])} 处、"
                  f"文字命中 {asset['name_hit_total']} 处")
            for hit in asset["reference_hits"]:
                print(f"    引用位 {hit['file']}:{hit.get('json_path') or hit['line']} [{hit['kind']}] {hit['text'][:90]}")
            for hit in asset["name_hits"]:
                print(f"    文字   {hit['file']}:{hit.get('json_path') or hit['line']} {hit['text'][:90]}")
            if asset["name_hit_total"] > len(asset["name_hits"]):
                print(f"    （另有 {asset['name_hit_total'] - len(asset['name_hits'])} 处文字命中，见影响清单文件）")
        return 0
    if args.require_image or args.require_reference:
        ok, problems = require_images(root, args.require_image) if args.require_image else (True, [])
        if args.require_reference:
            refs_ok, refs_problems = require_references(root, args.require_reference, args.for_shots)
            ok = ok and refs_ok
            problems += refs_problems
        for problem in problems:
            print("阻塞：" + problem)
        print("素材图片门禁：" + ("通过" if ok else "不通过"))
        return 0 if ok else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
