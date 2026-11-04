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
        if value["source_storyboard"]["status"] == "agent_accepted":
            if project_root is None:
                raise SchemaContractError("agent_accepted 导演来源须提供准确项目并核验采用依据")
            from pathlib import Path
            from .artifacts import (load_manifest, project_path, digest,
                                    artifact_is_adopted, validate_current_dependencies)
            from .project_state import load_project_state
            from .agent_acceptance import validate_agent_acceptance
            root = Path(project_root).resolve()
            source = value["source_storyboard"]
            manifest, state = load_manifest(root), load_project_state(root)
            matches = [entry for entry in manifest["artifacts"]
                       if entry.get("logical_id") == "storyboard.package" and entry.get("status") == "current"]
            if len(matches) != 1 or matches[0].get("artifact_id") != source["artifact_id"]:
                raise SchemaContractError("agent_accepted 导演来源缺失、非当前准确版本或不唯一")
            entry = matches[0]
            path = project_path(root, entry.get("path_or_url", ""))
            if (entry.get("logical_id") != "storyboard.package"
                    or entry.get("status") != "current"
                    or entry["artifact_id"] not in state["current_artifacts"]
                    or entry.get("revision") != source["revision"]
                    or project_path(root, source["path"]) != path
                    or not path.is_file() or not entry.get("checksum")
                    or digest(path) != entry["checksum"]
                    or not entry.get("agent_acceptance")
                    or not artifact_is_adopted(entry, state, root)):
                raise SchemaContractError("agent_accepted 导演来源身份、修订、路径、字节或采用依据无效")
            validate_agent_acceptance(root, entry, state=state)
            validate_current_dependencies(entry, manifest, state, root)
    result = {"schema_valid": True, "business_status": value["status"], "decision": value["decision"]}
    if candidate_test:
        result.update(result_metadata())
    return result
