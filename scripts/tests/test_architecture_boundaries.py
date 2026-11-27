"""Automated architecture and branch-boundary guardrails for scripts/."""
from __future__ import annotations

import ast
import collections
import re
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_ROOT.parent

LEGACY_CORE_SCC = frozenset({
    "core.agent_acceptance",
    "core.artifacts",
    "core.asset_batch_receipt",
    "core.asset_refs",
    "core.candidate_test",
    "core.canvas_readback",
    "core.character_boards",
    "core.delivery_files",
    "core.director_inputs",
    "core.draft_delivery",
    "core.endpoint_states",
    "core.generation_boundaries",
    "core.production_handoff",
    "core.project_kickoff",
    "core.project_state",
    "core.prompt_body",
    "core.prompt_scene_control",
    "core.quality_evidence",
    "core.recreation_assets",
    "core.recreation_segments",
    "core.review_publication",
    "core.revision_control",
    "core.schema_contracts",
    "core.story_sources",
    "core.visual_asset_choices",
    "core.visual_asset_product_usage",
    "core.visual_readiness",
})

COMPAT_SHIMS = frozenset({
    "visual_asset_choices",
    "visual_asset_product_usage",
    "visual_asset_source",
})

REDFRAME_MODULES = frozenset({
    "core.redframe_creative",
    "core.redframe_recreation",
    "core.redframe_draft_ui",
    "core.redframe_image_ui",
    "core.redframe_workspace",
    "redframe_draft",
})

L0_FOUNDATION_MODULES = frozenset({
    "core.errors",
    "core.media_types",
    "core.io",
    "core.character_assets",
    "core.character_layout",
    "core.image_technical",
    "core.director_reading",
    "core.depth_reference_review",
    "core.knowledge_runtime",
    "core.native_port_binding",
    "core.native_references",
    "core.runtime_deps",
    "core.scene_realism",
    "core.confirmation_scope",
    "core.agent_review_api",
    "core.redframe_workspace",
})

BRANCH_A_MODULES = frozenset({
    "director_package",
    "visual_stage_cli",
    "core.visual_readiness",
    "core.redframe_creative",
})
BRANCH_B_MODULES = frozenset({
    "prepare_depth_clips",
    "return_director_segment",
    "core.recreation_assets",
    "core.recreation_segments",
    "core.redframe_recreation",
    "core.depth_reference_review",
})


def _collect_modules() -> tuple[dict[str, Path], dict[str, set[str]]]:
    files = sorted(
        p for p in SCRIPTS_ROOT.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )
    mods: dict[str, Path] = {}
    for path in files:
        rel = path.relative_to(SCRIPTS_ROOT).with_suffix("")
        mod = ".".join(rel.parts)
        if mod.endswith(".__init__"):
            mod = mod[:-9] or "__init__"
        mods[mod] = path

    adj: dict[str, set[str]] = collections.defaultdict(set)
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name.removeprefix("scripts.")
                    if target in mods and target != mod:
                        adj[mod].add(target)
            elif isinstance(node, ast.ImportFrom):
                base = (node.module or "").removeprefix("scripts.")
                if node.level:
                    parts = mod.split(".")[:-node.level]
                    base = ".".join(parts + ([base] if base else []))
                if base in mods and base != mod:
                    adj[mod].add(base)
                for alias in node.names:
                    candidate = f"{base}.{alias.name}" if base else alias.name
                    if candidate in mods and candidate != mod:
                        adj[mod].add(candidate)
    return mods, adj


def _tarjan_sccs(nodes: set[str], adj: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(adj.get(v, ())):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for node in sorted(nodes):
        if node not in indices:
            strongconnect(node)
    return sccs


def test_no_core_to_top_level_cli_imports() -> None:
    mods, adj = _collect_modules()
    violations: list[tuple[str, list[str]]] = []
    for mod in sorted(mods):
        if not mod.startswith("core."):
            continue
        top_targets = sorted(
            target for target in adj[mod]
            if "." not in target and target not in {"__init__", "core"}
        )
        if top_targets:
            violations.append((mod, top_targets))
    assert violations == [], f"core.* must not import top-level scripts/*.py: {violations}"


def test_creative_and_recreation_branches_are_isolated() -> None:
    _, adj = _collect_modules()
    a_to_b = {
        m: sorted(adj[m] & BRANCH_B_MODULES)
        for m in BRANCH_A_MODULES
        if adj[m] & BRANCH_B_MODULES
    }
    b_to_a = {
        m: sorted(adj[m] & BRANCH_A_MODULES)
        for m in BRANCH_B_MODULES
        if adj[m] & BRANCH_A_MODULES
    }
    assert a_to_b == {}, f"Branch A (creative) must not import Branch B (recreation): {a_to_b}"
    assert b_to_a == {}, f"Branch B (recreation) must not import Branch A (creative): {b_to_a}"


def test_redframe_modules_have_no_circular_imports() -> None:
    mods, adj = _collect_modules()
    redframe_nodes = REDFRAME_MODULES & set(mods)
    redframe_adj = {m: adj[m] & redframe_nodes for m in redframe_nodes}
    assert _tarjan_sccs(redframe_nodes, redframe_adj) == []


def test_top_level_scripts_are_cli_entrypoints_or_declared_shims() -> None:
    mods, _ = _collect_modules()
    non_cli: list[str] = []
    for mod, path in sorted(mods.items()):
        if "." in mod or mod in {"__init__", "core"} or mod in COMPAT_SHIMS:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        has_main_def = any(
            isinstance(node, ast.FunctionDef) and node.name == "main"
            for node in tree.body
        )
        has_main_guard = "__main__" in source
        if not (has_main_def or has_main_guard):
            non_cli.append(mod)
    assert non_cli == [], (
        f"Top-level scripts/*.py must be CLI entrypoints (move shared libs to scripts/core/): {non_cli}"
    )


def test_circular_sccs_do_not_grow_beyond_legacy_core_baseline() -> None:
    mods, adj = _collect_modules()
    sccs = _tarjan_sccs(set(mods), adj)
    assert len(sccs) <= 1, f"Unexpected new circular SCCs introduced: {sccs}"
    if sccs:
        unknown = set(sccs[0]) - LEGACY_CORE_SCC
        assert unknown == set(), f"Modules added to legacy circular SCC: {sorted(unknown)}"


def test_l0_foundation_has_no_upward_dependencies() -> None:
    mods, adj = _collect_modules()
    missing = sorted(L0_FOUNDATION_MODULES - set(mods))
    assert missing == [], f"L0_FOUNDATION_MODULES contains missing modules: {missing}"
    upward = {
        m: sorted(adj[m] - L0_FOUNDATION_MODULES)
        for m in sorted(L0_FOUNDATION_MODULES)
        if adj[m] - L0_FOUNDATION_MODULES
    }
    assert upward == {}, f"L0 foundation modules must not import L1-L4 modules: {upward}"


def test_compat_shims_are_thin_reexports() -> None:
    mods, _ = _collect_modules()
    non_thin: list[str] = []
    for shim in sorted(COMPAT_SHIMS):
        assert shim in mods, f"Declared compat shim not found: {shim}"
        source = mods[shim].read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(mods[shim]))
        has_defs = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for node in tree.body
        )
        if has_defs or len(source.splitlines()) > 30:
            non_thin.append(shim)
    assert non_thin == [], f"Compat shims must be thin re-exports only (no defs, <=30 lines): {non_thin}"


def test_agents_and_claude_entry_stay_synced_and_link_architecture() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert agents == claude, "AGENTS.md and CLAUDE.md must remain byte-for-byte identical"
    lines = agents.splitlines()
    arch_lines = [line for line in lines if "docs/ARCHITECTURE.md" in line]
    assert len(arch_lines) == 1, "AGENTS.md must link docs/ARCHITECTURE.md exactly once"
    assert "常规制作开工不读" in arch_lines[0], (
        "docs/ARCHITECTURE.md must stay under the maintenance-only entry, not regular production startup"
    )


def test_rhtv_js_layering_and_cross_language_boundaries() -> None:
    js_files = {p.name: p for p in sorted(SCRIPTS_ROOT.glob("*.js"))}
    expected_js = {
        "rhtv_rich_editor.js",
        "rhtv_target.js",
        "rhtv_canvas.js",
        "rhtv_cdp.js",
        "rhtv_shot.js",
    }
    assert set(js_files) == expected_js, f"Unexpected or missing scripts/*.js files: {sorted(js_files)}"

    local_require_re = re.compile(r"""require\(\s*['"](\.[^'"]+)['"]\s*\)""")
    any_require_re = re.compile(r"""\brequire\s*\(""")

    # JS-L0: rhtv_rich_editor.js must stay a pure, zero-dependency browser DOM callback
    rich_src = js_files["rhtv_rich_editor.js"].read_text(encoding="utf-8")
    assert not any_require_re.search(rich_src), "rhtv_rich_editor.js must have zero require() dependencies"
    for forbidden in ("fetch(", "WebSocket", "node:fs", "child_process", "playwright"):
        assert forbidden not in rich_src, f"rhtv_rich_editor.js must remain pure read-only DOM code (found {forbidden})"

    # JS-L1: rhtv_target.js may only require ./rhtv_rich_editor locally and never Playwright/CLIs
    target_src = js_files["rhtv_target.js"].read_text(encoding="utf-8")
    target_local_deps = {Path(m).with_suffix(".js").name for m in local_require_re.findall(target_src)}
    assert target_local_deps <= {"rhtv_rich_editor.js"}, (
        f"rhtv_target.js must only depend on rhtv_rich_editor.js locally, got: {sorted(target_local_deps)}"
    )
    assert "playwright" not in target_src, "rhtv_target.js must stay lightweight CDP without Playwright"

    # JS-L2: CLI scripts must define main() and only require ./rhtv_target locally
    for cli_name in ("rhtv_canvas.js", "rhtv_cdp.js", "rhtv_shot.js"):
        cli_src = js_files[cli_name].read_text(encoding="utf-8")
        assert "function main(" in cli_src, f"{cli_name} must define a CLI main() entrypoint"
        cli_local_deps = {Path(m).with_suffix(".js").name for m in local_require_re.findall(cli_src)}
        assert cli_local_deps <= {"rhtv_target.js"}, (
            f"{cli_name} must only depend on rhtv_target.js locally, got: {sorted(cli_local_deps)}"
        )

    # Cross-language boundary: Python core/* must not shell out to rhtv_*.js CLIs
    cli_invocations: list[str] = []
    for py_path in sorted((SCRIPTS_ROOT / "core").glob("*.py")):
        py_src = py_path.read_text(encoding="utf-8")
        for cli_name in ("rhtv_canvas.js", "rhtv_cdp.js", "rhtv_shot.js"):
            if cli_name in py_src:
                cli_invocations.append(f"{py_path.name} -> {cli_name}")
    assert cli_invocations == [], f"scripts/core/*.py must not shell out to rhtv_*.js CLIs: {cli_invocations}"
