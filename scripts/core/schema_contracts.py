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
