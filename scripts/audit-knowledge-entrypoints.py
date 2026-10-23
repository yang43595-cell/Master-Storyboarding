from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

try:
    from .core.knowledge_blocks import (
        KnowledgeBlock,
        KnowledgeIndexError,
        RULE_BLOCK_RE,
        load_policy,
        parse_knowledge_blocks,
    )
except ImportError:  # 直接执行时 scripts/ 在 sys.path 上，回退到核心导入
    from core.knowledge_blocks import (
        KnowledgeBlock,
        KnowledgeIndexError,
        RULE_BLOCK_RE,
        load_policy,
        parse_knowledge_blocks,
    )

REGISTRY_PATH = Path("references/workflow/knowledge-entrypoints.json")
LEGACY_ROUTES_PATH = Path("references/workflow") / ("knowledge-" + "routes.json")


class EntrypointAuditError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EntrypointAuditError(f"cannot load JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EntrypointAuditError(f"JSON root must be an object: {path}")
    return value


def _is_excluded(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative.casefold(), pattern.casefold()) for pattern in patterns)


def _discovered_files(vault: Path, policy: dict[str, Any]) -> list[Path]:
    files: set[Path] = set()
    for raw_root in policy["discovery"]["roots"]:
        root = (vault / raw_root).resolve()
        try:
            root.relative_to(vault)
        except ValueError as exc:
            raise EntrypointAuditError(f"discovery root escapes vault: {raw_root}") from exc
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            relative = path.resolve().relative_to(vault).as_posix()
            if not _is_excluded(relative, policy["discovery"]["excluded_globs"]):
                files.add(path.resolve())
    return sorted(files, key=lambda path: path.relative_to(vault).as_posix())


def _load_profiles(repo: Path, errors: list[str]) -> dict[str, set[str]]:
    profiles: dict[str, set[str]] = {}
    for skill_dir in sorted(path for path in (repo / "skills").iterdir() if path.is_dir() and path.name != "_shared"):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        profile_path = skill_dir / "knowledge-profile.json"
        if not profile_path.is_file():
            errors.append(f"skill profile missing: {skill_dir.name}")
            continue
        try:
            profile = _load_json(profile_path)
        except EntrypointAuditError as exc:
            errors.append(str(exc))
            continue
        topics = profile.get("topics")
        if profile.get("schema_version") != "1.0":
            errors.append(f"invalid profile schema_version: {skill_dir.name}")
        if profile.get("skill") != skill_dir.name:
            errors.append(f"profile skill mismatch: {skill_dir.name}")
        if not isinstance(topics, list) or any(not isinstance(topic, str) or not topic for topic in topics):
            errors.append(f"profile topics must be strings: {skill_dir.name}")
            continue
        if len(topics) != len(set(topics)):
            errors.append(f"profile topics contain duplicates: {skill_dir.name}")
        profiles[skill_dir.name] = set(topics)
    return profiles


def _validate_carrier(
    carrier: dict[str, Any], *, repo: Path, known_skills: set[str], errors: list[str], label: str
) -> set[str]:
    path_value = carrier.get("path")
    skills = carrier.get("skills")
    terms = carrier.get("required_terms")
    if not isinstance(path_value, str) or not path_value or Path(path_value).is_absolute() or ".." in Path(path_value).parts:
        errors.append(f"{label}: invalid local carrier path")
        return set()
    if not isinstance(skills, list) or not skills or any(not isinstance(skill, str) or skill not in known_skills for skill in skills):
        errors.append(f"{label}: invalid carrier skills")
        skills = []
    if len(skills) != len(set(skills)):
        errors.append(f"{label}: carrier skills contain duplicates")
    if not isinstance(terms, list) or not terms or any(not isinstance(term, str) or not term for term in terms):
        errors.append(f"{label}: required_terms must be non-empty strings")
        terms = []

    path = (repo / path_value).resolve()
    try:
        path.relative_to(repo)
    except ValueError:
        errors.append(f"{label}: carrier escapes repository: {path_value}")
        return set(skills)
    if not path.is_file():
        errors.append(f"{label}: carrier missing: {path_value}")
        return set(skills)
    text = path.read_text(encoding="utf-8-sig")
    for term in terms:
        if term not in text:
            errors.append(f"{label}: carrier term missing in {path_value}: {term}")
    for skill in skills:
        skill_path = repo / "skills" / skill / "SKILL.md"
        direct = Path(os.path.relpath(path, skill_path.parent)).as_posix()
        if direct not in skill_path.read_text(encoding="utf-8-sig"):
            errors.append(f"{label}: {skill}/SKILL.md does not directly reference {direct}")
    return set(skills)


def _non_rule_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    text = RULE_BLOCK_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_unique_and_conflicts(blocks: list[KnowledgeBlock], errors: list[str]) -> None:
    for field in ("id", "canonical_key"):
        seen: dict[str, KnowledgeBlock] = {}
        for block in blocks:
            value = getattr(block, field)
            if value in seen:
                errors.append(f"duplicate {field} {value}: {seen[value].id}, {block.id}")
            else:
                seen[value] = block
    confirmed = [block for block in blocks if block.status == "confirmed"]
    for index, left in enumerate(confirmed):
        for right in confirmed[index + 1 :]:
            if right.canonical_key in left.conflicts_with or left.canonical_key in right.conflicts_with:
                errors.append(f"confirmed rules conflict: {left.id}, {right.id}")


def _legacy_route_references(repo: Path) -> list[str]:
    hits: set[str] = set()
    roots = [repo / "skills", repo / "scripts", repo / "references", repo / "README.md", repo / "skill-config.md"]
    suffixes = {".md", ".json", ".py", ".ps1", ".yaml", ".yml"}
    needle = "knowledge-" + "routes.json"
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*") if root.exists() else []
        for path in paths:
            if not path.is_file() or path.suffix.casefold() not in suffixes or path.resolve() == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
            if needle in text:
                hits.add(path.resolve().relative_to(repo).as_posix())
    return sorted(hits)


def audit(repo: Path, vault: Path) -> dict[str, Any]:
    repo, vault = repo.resolve(), vault.resolve()
    policy = load_policy(repo)
    registry = _load_json(repo / REGISTRY_PATH)
    errors: list[str] = []
    warnings: list[str] = []
    profiles = _load_profiles(repo, errors)
    known_skills = set(profiles)

    if (repo / LEGACY_ROUTES_PATH).exists():
        errors.append("legacy central route file remains")
    for relative in _legacy_route_references(repo):
        errors.append(f"legacy central route reference remains: {relative}")
    if registry.get("schema_version") != "2.0":
        errors.append("knowledge-entrypoints schema_version must be 2.0")
    repo_registry = registry.get("repo_rules")
    reviewed_registry = registry.get("reviewed_rule_pages")
    if not isinstance(repo_registry, dict):
        errors.append("repo_rules must be an object")
        repo_registry = {}
    if not isinstance(reviewed_registry, dict):
        errors.append("reviewed_rule_pages must be an object")
        reviewed_registry = {}
    if "vault_execution_pages" in registry:
        errors.append("legacy vault_execution_pages registry remains")

    discovered = _discovered_files(vault, policy)
    discovered_set = set(discovered)
    blocks: list[KnowledgeBlock] = []
    try:
        for path in discovered:
            blocks.extend(parse_knowledge_blocks(path, vault))
    except KnowledgeIndexError as exc:
        errors.append(str(exc))
    for path in sorted((vault / "wiki").rglob("*.md")):
        text = path.read_text(encoding="utf-8-sig")
        if "<!-- msb-knowledge" in text and path.resolve() not in discovered_set:
            errors.append(f"knowledge block is outside dynamic discovery roots: {path.resolve().relative_to(vault).as_posix()}")

    project_blocks = [block for block in blocks if "Master-Storyboarding" in block.projects or "*" in block.projects]
    _validate_unique_and_conflicts(project_blocks, errors)
    wiki_rules = [block for block in project_blocks if block.authority == "wiki"]
    repo_rules = [block for block in project_blocks if block.authority == "repo"]
    for block in wiki_rules:
        unknown_consumers = sorted(set(block.stages) - known_skills - {"*"})
        if unknown_consumers:
            errors.append(f"dynamic rule uses unknown consuming Skills: {block.id}: {unknown_consumers}")
        consumers = known_skills if "*" in block.stages else set(block.stages).intersection(known_skills)
        if not consumers:
            errors.append(f"dynamic rule has no real consuming Skill: {block.id} ({block.canonical_key})")
        elif not any(profiles[skill].intersection(block.topics) for skill in consumers) and not block.triggers:
            errors.append(f"dynamic rule has neither a reachable topic nor an explicit trigger: {block.id} ({block.canonical_key})")
        if block.kind == "reference" and not block.triggers:
            errors.append(f"reference rule has no trigger: {block.id}")
        for source in block.sources:
            if not (vault / "wiki" / f"{source}.md").is_file():
                errors.append(f"dynamic rule source missing: {block.id}: {source}")

    actual_rule_pages = {block.vault_relative_path for block in project_blocks}
    registered_rule_pages = set(reviewed_registry)
    for relative in sorted(actual_rule_pages - registered_rule_pages):
        errors.append(f"project rule page lacks reviewed non-rule baseline: {relative}")
    for relative in sorted(registered_rule_pages - actual_rule_pages):
        errors.append(f"reviewed rule page has no active project rule: {relative}")
    for relative in sorted(actual_rule_pages & registered_rule_pages):
        entry = reviewed_registry.get(relative)
        if not isinstance(entry, dict):
            errors.append(f"reviewed rule page entry must be an object: {relative}")
            continue
        expected_hash = entry.get("non_rule_sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            errors.append(f"reviewed rule page has invalid non_rule_sha256: {relative}")
        elif _non_rule_sha256(vault / relative) != expected_hash:
            errors.append(f"rule page non-rule content changed and needs layer review: {relative}")
        carriers = entry.get("carriers", [])
        if not isinstance(carriers, list):
            errors.append(f"reviewed rule page carriers must be an array: {relative}")
            continue
        for index, carrier in enumerate(carriers):
            if isinstance(carrier, dict):
                _validate_carrier(carrier, repo=repo, known_skills=known_skills, errors=errors, label=f"{relative}/reviewed-carrier-{index + 1}")
            else:
                errors.append(f"{relative}: reviewed carrier {index} must be an object")

    actual_repo_keys = {block.canonical_key for block in repo_rules}
    for key in sorted(actual_repo_keys - set(repo_registry)):
        errors.append(f"repo-authoritative rule has no local carrier registration: {key}")
    for key in sorted(set(repo_registry) - actual_repo_keys):
        errors.append(f"local carrier registration has no repo-authoritative rule: {key}")
    for block in repo_rules:
        entry = repo_registry.get(block.canonical_key)
        if not isinstance(entry, dict):
            continue
        if entry.get("rule_id") != block.id:
            errors.append(f"repo rule id mismatch for {block.canonical_key}")
        carriers = entry.get("carriers")
        if not isinstance(carriers, list) or not carriers:
            errors.append(f"repo rule has no carriers: {block.canonical_key}")
            continue
        covered: set[str] = set()
        for index, carrier in enumerate(carriers):
            if isinstance(carrier, dict):
                covered.update(_validate_carrier(carrier, repo=repo, known_skills=known_skills, errors=errors, label=f"{block.id}/carrier-{index + 1}"))
            else:
                errors.append(f"{block.canonical_key}: carrier {index} must be an object")
        expected = {skill for skill in block.stages if skill in known_skills}
        if expected - covered:
            errors.append(f"{block.id}: local carriers do not cover skills {sorted(expected - covered)}")

    # The three-layer migration moved production records into the project's
    # experience and confirmed-project folders. Keep legacy records readable.
    project_records = sorted({path for folder in (
        "wiki/projects/Master-Storyboarding",
        "projects/Master-Storyboarding/经验",
        "projects/Master-Storyboarding/定稿项目",
    ) for path in (vault / folder).rglob("*.md")})
    design_records = [path for path in (vault / "projects/Master-Storyboarding").rglob("*.md")
                      if path not in set(project_records)]
    source_pages = {source for block in blocks for source in block.sources}
    evolving_pages = list((vault / "wiki").rglob("*.evolving.md"))
    if not project_records:
        warnings.append("no Master-Storyboarding project record pages found")
    return {
        "schema_version": "2.0",
        "ok": not errors,
        "summary": {
            "skill_profiles": len(profiles),
            "dynamic_rule_blocks": len(wiki_rules),
            "repo_authoritative_rule_blocks": len(repo_rules),
            "project_record_pages": len(project_records),
            "project_design_pages": len(design_records),
            "referenced_source_pages": len(source_pages),
            "exploration_pages": len(evolving_pages),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "layers": {
            "dynamic_rules": sorted(block.id for block in wiki_rules),
            "repo_authoritative_rules": sorted(block.id for block in repo_rules),
            "project_records": sorted(path.resolve().relative_to(vault).as_posix() for path in project_records),
            "project_design_records": sorted(path.resolve().relative_to(vault).as_posix() for path in design_records),
            "evidence_sources": sorted(source_pages),
            "exploration": sorted(path.resolve().relative_to(vault).as_posix() for path in evolving_pages),
        },
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="审计 Master-Storyboarding 的 Skill profile、动态知识与本地载体")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        report = audit(ROOT, Path(args.vault))
    except (EntrypointAuditError, KnowledgeIndexError, OSError, ValueError) as exc:
        report = {"schema_version": "2.0", "ok": False, "errors": [str(exc)]}
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
