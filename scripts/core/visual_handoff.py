"""Lower-L2 visual and shot-scoped asset readiness gates for storyboard handoffs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import asset_refs
from .acceptance_rules import artifact_is_adopted, validate_agent_acceptance, validate_current_dependencies
from .character_assets import final_output_problems, scoped_output_requirements
from .manifest_store import cache_file_digests, load_manifest, project_path
from .project_kickoff import load_kickoff, load_project_request
from .project_state import load_project_state
from .visual_readiness import require_visual_readiness

NARRATIVE = "narrative.package"
LEGACY_RULE = "docs/rules/legacy-review.md"
EVIDENCE_ERRORS = (OSError, ValueError, KeyError, TypeError)


def read_adopted_package(project, manifest, state, logical_id):
    matches = [entry for entry in manifest["artifacts"] if entry.get("logical_id") == logical_id
               and entry.get("status") == "current"]
    if not matches:
        return None, None
    if len(matches) != 1:
        raise ValueError(f"{logical_id} 缺唯一当前版本")
    entry = matches[0]
    path = project_path(project, entry["path_or_url"])
    if (entry["artifact_id"] not in state["current_artifacts"] or not path.is_relative_to(project)
            or not path.is_file()):
        raise ValueError(f'{entry["artifact_id"]} 缺当前产物登记或准确文件字节')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.get("checksum"):
        raise ValueError(f'{entry["artifact_id"]} 缺当前产物登记或准确文件字节')
    if not artifact_is_adopted(entry, state, project):
        if entry.get("agent_acceptance"):
            try:
                validate_agent_acceptance(project, entry, state=state)
            except EVIDENCE_ERRORS as exc:
                raise ValueError(f'{entry["artifact_id"]} 自主采用依据无效：{exc}') from exc
        raise ValueError(f'{entry["artifact_id"]} 缺有效采用或确认依据')
    validate_current_dependencies(entry, manifest, state, project)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f'{entry["artifact_id"]} 正文不是 JSON 对象')
    return entry, payload


def asset_readiness(project, narrative, asset_ids=None, *, file_digest=None):
    choices = asset_refs.read_choices(project)
    assets = asset_refs.asset_index(
        asset_refs.load_asset_list(project, narrative, choices, image_requests=asset_ids, file_digest=file_digest)
    )
    requested = set(assets) if asset_ids is None else set(asset_ids)
    requirements = scoped_output_requirements(narrative, choices, requested)
    ready, waiting = {}, {}
    for asset_id in sorted(requested):
        item = assets.get(asset_id)
        if item is None or item["state"] in {"split_parent", "pending"}:
            waiting[asset_id] = [f"{asset_id} 缺当前可用的图像需求决定"]
            continue
        if item["state"] != "needs_image":
            continue
        image = item["image"]
        outputs = [output for output in image.get("outputs", [])
                   if output.get("sha256") and output.get("path_exists")]
        if asset_id in requirements:
            item["required_board_ids"] = list(requirements[asset_id])
            problems = final_output_problems(requirements, [asset_id], {asset_id: outputs})
        else:
            problems = []
        if image["status"] != "confirmed" or not outputs or problems:
            waiting[asset_id] = problems or [f"{asset_id} 缺有文件字节依据的已确认输出"]
        else:
            ready[asset_id] = [f'{asset_id}@{output["output_id"]}' for output in outputs
                               if asset_id not in requirements or output["output_id"] in requirements[asset_id]]
    return assets, ready, waiting


def shot_assets(storyboard):
    return {shot["shot_id"]: shot["source_asset_requirement_ids"]
            for scene in storyboard["scenes"] for shot in scene["shots"]}


def shot_blockers(shots, waiting):
    return {shot: [issue for asset in assets for issue in waiting.get(asset, [])]
            for shot, assets in shots.items() if any(asset in waiting for asset in assets)}


def require_handoff_visual_inputs(project, storyboard, director, shot_ids, *, rows=None,
                                  manifest=None, state=None, require_review=False, file_digest=None,
                                  package_fn=None):
    """Use the regular visual gate plus current shot-scoped assets on the default workflow."""
    project = Path(project).resolve()
    scope = list(shot_ids)
    file_digest = cache_file_digests(check_for_changes=True) if file_digest is None else file_digest
    read_package = read_adopted_package if package_fn is None else package_fn
    assets = None
    modern = (storyboard.get("schema_version") in ("4.0", "5.0") and bool(storyboard.get("project_id"))
              and load_kickoff(project) is not None
              and LEGACY_RULE not in load_project_request(project).get("local_references", []))
    if modern:
        manifest = load_manifest(project) if manifest is None else manifest
        state = load_project_state(project) if state is None else state
        shots = {shot: reqs for shot, reqs in shot_assets(storyboard).items() if shot in scope}
        required = {asset for reqs in shots.values() for asset in reqs}
        if required:
            _, narrative = read_package(project, manifest, state, NARRATIVE)
            if narrative is None:
                raise ValueError("缺当前已采用编导，无法核对镜头的必要资产")
            assets, _, waiting = asset_readiness(project, narrative, required, file_digest=file_digest)
            if problems := shot_blockers(shots, waiting):
                raise ValueError("镜头必要资产未就绪：" + "；".join(
                    f'{shot}：{"、".join(issues)}' for shot, issues in problems.items()))
    return require_visual_readiness(project, storyboard, director, scope, rows=rows,
                                    manifest=manifest, require_review=modern or require_review,
                                    assets=assets, file_digest=file_digest)
