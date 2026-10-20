from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .runtime_deps import RuntimeDepsError, require_runtime_deps


SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


class SchemaContractError(ValueError):
    pass


@lru_cache(maxsize=8)
def _validator(schema_name: str):
    try:
        require_runtime_deps(SCHEMA_ROOT.parent)
        import jsonschema
        from referencing import Registry, Resource
    except (ImportError, RuntimeDepsError) as exc:
        raise SchemaContractError(
            "运行结构化合同校验需要 jsonschema 和 referencing；"
            "请用当前解释器运行 python scripts/install_runtime_deps.py 安装到隔离目录"
        ) from exc
    schema_path = SCHEMA_ROOT / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaContractError(f"无法读取结构化合同 schema：{schema_path}: {exc}") from exc
    jsonschema.Draft202012Validator.check_schema(schema)
    registry = Registry()
    try:
        for candidate in SCHEMA_ROOT.glob("*.json"):
            contents = json.loads(candidate.read_text(encoding="utf-8"))
            resource = Resource.from_contents(contents)
            resource_id = contents.get("$id") or candidate.as_uri()
            registry = registry.with_resource(resource_id, resource)
    except (OSError, ValueError) as exc:
        raise SchemaContractError(f"无法建立结构化合同 schema registry：{exc}") from exc
    return jsonschema.Draft202012Validator(schema, registry=registry)


def schema_errors(value: Any, schema_name: str) -> list[str]:
    legacy = {
        ("storyboard-package.schema.json", "3.0"): "storyboard-package-v3.schema.json",
        ("narrative-package.schema.json", "1.0"): "narrative-package-v1.schema.json",
        ("storyboard-package.schema.json", "2.0"): "storyboard-package-v2.schema.json",
        ("storyboard-package.schema.json", "1.0"): "storyboard-package-v1.schema.json",
        ("production-plan.schema.json", "3.2"): "production-plan-v3.2.schema.json",
        ("visual-asset-manifest.schema.json", "1.0"): "visual-asset-manifest-v1.schema.json",
        ("visual-asset-manifest.schema.json", "1.1"): "visual-asset-manifest-v1.schema.json",
    }
    version = str(value.get("schema_version", "")) if isinstance(value, dict) else ""
    validator = _validator(legacy.get((schema_name, version), schema_name))
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    rendered: list[str] = []
    for error in errors:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        rendered.append(f"{path}: {error.message}")
    return rendered


def require_schema(value: Any, schema_name: str, *, label: str) -> None:
    errors = schema_errors(value, schema_name)
    if errors:
        raise SchemaContractError(f"{label} 不符合 {schema_name}：{'；'.join(errors)}")


def validate_prompt_intent(value: Any) -> None:
    require_schema(value, "prompt-intent.schema.json", label="prompt_intent")


def validate_revision_evidence(value: Any) -> None:
    require_schema(value, "revision-evidence.schema.json", label="revision evidence")


def validate_visual_supplement_request(value: Any, *, project_root=None, candidate_test=False) -> dict:
    """One contract-specific test exception; the cached production schema stays strict."""
    from .candidate_test import context, require_production, result_metadata
    name = "visual-supplement-request.schema.json"
    if candidate_test:
        if project_root is None or not context(project_root):
            raise SchemaContractError("补图候选校验需要显式隔离测试项目")
        from copy import deepcopy
        validator = _validator(name)
        schema = deepcopy(validator.schema)
        schema["$defs"]["storyboardRef"]["properties"]["status"] = {"const": "candidate"}
        # These are business outcomes, independent of manifest adoption identity.
        schema["properties"]["status"] = {"enum": ["candidate", "blocked", "not_needed"]}
        errors = list(type(validator)(schema).iter_errors(value))
        if errors:
            raise SchemaContractError("补图候选不符合合同：" + "; ".join(
                f"{'.'.join(str(p) for p in error.absolute_path) or '$'}: {error.message}" for error in errors))
    else:
        if project_root is not None:
            require_production(project_root)
        require_schema(value, name, label="视觉补充请求")
    result = {"schema_valid": True, "business_status": value["status"], "decision": value["decision"]}
    if candidate_test:
        result.update(result_metadata())
    return result


def validate_video_prompt_identity(value, project_root, *, candidate_mode=False, artifact=None):
    """Validate the existing business JSON against this project's exact sources.

    This trusts the existing local manifest/confirmation authority, not filenames
    or caller-supplied labels. It is not a signature or semantic translation test.
    Legacy documents remain readable but cannot pass this new-delivery checkpoint.
    """
    from .artifacts import (load_manifest, project_path, digest, artifact_is_confirmed,
                            validate_current_dependencies)
    from .candidate_test import context, require_production, result_metadata
    from .project_state import load_project_state
    project = Path(project_root).resolve()
    testing = context(project) if candidate_mode else None
    if candidate_mode and testing is None:
        raise SchemaContractError("video prompt candidate check requires explicit candidate_test context")
    if not candidate_mode:
        require_production(project)
    require_schema(value, "video-prompt-identity.schema.json", label="video prompt identity (legacy metadata must be reviewed, not inferred)")
    manifest, state = load_manifest(project), load_project_state(project)
    # Existing narrative.package project_id is the project key; do not invent a
    # new ID or require a parallel identity registry. Honor a manifest key if present.
    if "project_id" in manifest and value["project_id"] != manifest["project_id"]:
        raise SchemaContractError("video prompt project_id differs from the established project manifest identity")
    if artifact is not None and (artifact.get("logical_id") != "video.prompt"
            or artifact.get("type") not in {"video.prompt", "video-prompt", "json"}
            or artifact.get("revision") != value["revision"]):
        raise SchemaContractError("video prompt document identity/revision differs from its artifact registration")

    verified_sources = []

    def source(ref, logical, schema):
        from hashlib import sha256
        matches = [a for a in manifest["artifacts"] if a.get("artifact_id") == ref["artifact_id"]]
        if len(matches) != 1:
            raise SchemaContractError("video prompt source artifact is missing or ambiguous: " + logical)
        item = matches[0]
        if (item.get("logical_id") != logical or item.get("type") not in {logical, logical.replace(".", "-")}
                or item.get("revision") != ref["revision"]):
            raise SchemaContractError("video prompt source registration type/revision mismatch: " + logical)
        path = project_path(project, item.get("path_or_url", ""))
        if (not path.is_relative_to(project) or not path.is_file()
                or project_path(project, ref["path"]) != path):
            raise SchemaContractError("video prompt source path/hash mismatch: " + logical)
        # Hash and parse one byte snapshot. A second read for JSON parsing can
        # otherwise validate a payload different from the registered bytes.
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != ref["sha256"]:
            raise SchemaContractError("video prompt source path/hash mismatch: " + logical)
        if testing:
            if (item["artifact_id"] not in testing["input_artifact_ids"] or item.get("status") != "candidate"
                    or item.get("user_approved", False) is not False
                    or item.get("candidate_test") != testing["run_id"]
                    or item.get("review_status") != "unreviewed" or item.get("production_blocked") is not True):
                raise SchemaContractError("video prompt source is not this run's selected unreviewed candidate")
        else:
            if (item["artifact_id"] not in state["current_artifacts"]
                    or not artifact_is_confirmed(item, state, project) or item.get("checksum") != ref["sha256"]):
                raise SchemaContractError("video prompt source is not the exact confirmed current artifact")
            validate_current_dependencies(item, manifest, state, project)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise SchemaContractError("video prompt source is not readable JSON") from exc
        require_schema(payload, schema, label=logical)
        if payload.get("project_id") != value["project_id"] or payload.get("revision") != ref["revision"]:
            raise SchemaContractError("video prompt upstream body project/revision mismatch: " + logical)
        verified_sources.append((path, ref["sha256"]))
        return payload

    board = source(value["source_storyboard"], "storyboard.package", "storyboard-package.schema.json")
    source(value["source_narrative"], "narrative.package", "narrative-package.schema.json")
    upstream = board.get("source_narrative", {})
    declared = value["source_narrative"]
    if (any(upstream.get(k) != declared[k] for k in ("artifact_id", "logical_id", "revision"))
            or project_path(project, upstream.get("path", "")) != project_path(project, declared["path"])
            or ("sha256" in upstream and upstream["sha256"] != declared["sha256"])):
        raise SchemaContractError("video prompt narrative is not the director's exact registered source")
    source_refs = value.get("source_artifacts")
    if source_refs is not None:
        for ref in (value["source_storyboard"], value["source_narrative"]):
            matches = [r for r in source_refs if isinstance(r, dict) and r.get("artifact_id") == ref["artifact_id"]]
            if len(matches) != 1 or any(matches[0].get(k) != ref[k] for k in ("revision", "sha256")):
                raise SchemaContractError("delivery source_artifacts differs from exact video prompt sources")
            repeated = matches[0]
            if (("logical_id" in repeated and repeated["logical_id"] != ref["logical_id"])
                    or ("type" in repeated and repeated["type"] not in {ref["logical_id"], ref["logical_id"].replace(".", "-")})
                    or ("project_id" in repeated and repeated["project_id"] != value["project_id"])
                    or any(key in repeated and project_path(project, repeated[key]) != project_path(project, ref["path"])
                           for key in ("path", "path_or_url"))):
                raise SchemaContractError("delivery source_artifacts has contradictory source identity/path")
    shot_ids = [s["shot_id"] for scene in board["scenes"] for s in scene["shots"]]
    node_ids = [d["node_id"] for d in value["drafts"]]
    if len(set(node_ids)) != len(node_ids):
        raise SchemaContractError("video prompt duplicate draft node_id")
    for draft in value["drafts"]:
        ids = draft["source_shot_ids"]
        if any(ident not in shot_ids for ident in ids) or [shot_ids.index(i) for i in ids] != sorted(shot_ids.index(i) for i in ids):
            raise SchemaContractError("video prompt draft source shots are absent or out of director order")
    if any(not path.is_file() or digest(path) != expected for path, expected in verified_sources):
        raise SchemaContractError("video prompt source changed during identity validation")
    return {"ok": True, "document_type": value["document_type"], "project_id": value["project_id"],
            "revision": value["revision"], "identity_checks": "schema_and_exact_registered_upstream_bytes",
            "semantic_translation": "requires_manual_review", "generation_authorization": "not_granted",
            **(result_metadata() if testing else {})}
