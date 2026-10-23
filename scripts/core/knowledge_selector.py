from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, fields
from typing import Any

from .knowledge_blocks import KnowledgeBlock


@dataclass(frozen=True)
class SelectionResult:
    selected: list[KnowledgeBlock]
    candidates: list[KnowledgeBlock]
    deferred: list[dict[str, str]]
    blocking_conflicts: list[dict[str, Any]]
    total_chars: int
    signals: list[str]
    match_reasons: dict[str, list[str]]
    candidate_reasons: dict[str, str] = field(default_factory=dict)
    applicable_confirmed: list[KnowledgeBlock] = field(default_factory=list)


def select_knowledge(
    blocks: list[KnowledgeBlock],
    policy: dict[str, Any],
    skill: str,
    request: dict[str, Any],
    project_state: dict[str, Any],
    tombstone_fingerprints: set[str],
    profile: dict[str, Any] | None = None,
) -> SelectionResult:
    budgets = _validated_budgets(policy)
    signals = _signals(request, project_state)
    skill_profile = profile or policy.get("profile")
    if not isinstance(skill_profile, dict):
        return SelectionResult([], [], [], [], 0, signals, {})

    skill_topics = set(skill_profile.get("topics", []))
    unique_blocks = _deduplicate_blocks(blocks)
    eligible: list[KnowledgeBlock] = []
    candidates: list[KnowledgeBlock] = []
    deferred: list[dict[str, str]] = []
    candidate_reasons: dict[str, str] = {}
    applicable_confirmed: list[KnowledgeBlock] = []

    for block in unique_blocks:
        contract_reason = _runtime_contract_reason(block, policy)
        if contract_reason is not None:
            deferred.append(_deferred(block, contract_reason))
            continue
        if not _matches_scope(block, policy, skill):
            continue
        topic_hits = skill_topics.intersection(block.topics)
        trigger_hits = _trigger_hits(block, signals)
        if not topic_hits and not trigger_hits:
            continue
        if block.fingerprint in tombstone_fingerprints:
            deferred.append(_deferred(block, "tombstoned"))
            continue
        boundary_reason = _profile_boundary_reason(block, skill_profile)
        # Applicability is evaluated before display budget. Trigger-missed
        # discovery-only candidates are not active constraints until adopted.
        if (block.status == "confirmed" and (not block.triggers or trigger_hits)
                and (boundary_reason is None or (skill in block.stages and topic_hits))):
            applicable_confirmed.append(block)
        if boundary_reason is not None:
            deferred.append(_deferred(block, boundary_reason))
            # Mixed knowledge explicitly naming this consumer remains discoverable.
            # It is never embedded as a whole-stage requirement by a word match.
            if skill in block.stages and topic_hits:
                candidates.append(block)
                candidate_reasons[block.id] = boundary_reason
            continue
        if block.triggers and not trigger_hits:
            candidates.append(block)
            candidate_reasons[block.id] = "trigger_required"
            continue
        eligible.append(block)

    ranked = sorted(
        eligible,
        key=lambda block: _rank_key(block, skill_topics, signals, policy),
    )
    ranked, duplicate_deferred = _deduplicate_canonical_keys(ranked)
    eligible_ids = {block.id for block in ranked}
    # A currently applicable confirmed mixed rule can be a read-on-demand
    # candidate, but cannot lose its conflict authority by staying out of body.
    conflict_inputs = {block.id: block for block in [*ranked, *applicable_confirmed]}
    conflict_free, conflicts, conflict_deferred = _resolve_conflicts(list(conflict_inputs.values()))
    conflict_free = [block for block in conflict_free if block.id in eligible_ids]
    selected, budget_deferred, total_chars = _apply_budget(conflict_free, budgets)
    budget_ids = {item["rule_id"] for item in budget_deferred}
    candidates.extend(block for block in conflict_free if block.id in budget_ids)
    candidate_reasons.update({item["rule_id"]: item["reason"] for item in budget_deferred})
    all_deferred = sorted(
        deferred + duplicate_deferred + conflict_deferred + budget_deferred,
        key=lambda item: (item["rule_id"], item["reason"]),
    )
    return SelectionResult(
        selected=selected,
        candidates=sorted(candidates, key=lambda block: block.id),
        deferred=all_deferred,
        blocking_conflicts=conflicts,
        total_chars=total_chars,
        signals=signals,
        candidate_reasons=candidate_reasons,
        applicable_confirmed=applicable_confirmed,
        match_reasons={
            block.id: _match_reasons(block, skill_topics, signals)
            for block in selected
        },
    )


def _match_reasons(
    block: KnowledgeBlock, stage_topics: set[str], signals: list[str]
) -> list[str]:
    """Return stable, user-auditable reasons for a selected rule."""
    reasons = [f"skill_topic:{topic}" for topic in sorted(stage_topics.intersection(block.topics))]
    reasons.extend(
        f"trigger:{trigger}"
        for trigger in sorted(block.triggers)
        if _normalize_signal(trigger)
        and any(_has_positive_trigger(signal, _normalize_signal(trigger)) for signal in signals)
    )
    return reasons


def render_knowledge_bundle(skill: str, result: SelectionResult) -> str:
    lines = [
        f"# {skill} 当前知识包",
        "",
        "> 本文件只包含当前 Skill 实际选中的规则块。",
        "",
    ]
    for block in result.selected:
        lines.extend(
            [
                f"## {block.id} [{block.status}]",
                "",
                f"适用条件：{block.applies_when}",
                "",
                block.body.strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_knowledge_candidates(result: SelectionResult) -> str:
    if not result.candidates:
        return ""
    lines = [
        "## 按需知识候选",
        "",
        "> 以下条目未载入正文、未声明已读或采用。仅当当前任务的已确认输入事实表明适用时，按列出的精确 Vault 路径和锚点打开；不得扫描 Vault。",
        "",
    ]
    for block in result.candidates:
        reason = result.candidate_reasons.get(block.id, "trigger_required")
        label = ("预算延后；按任务需要精确补读" if reason.startswith("budget_") else
                 "含跨职责内容；仅候选，采用前须声明本职适用范围，其他实现项不成为当前要求"
                 if reason.startswith("skill_boundary:") else "topic 匹配，当前未命中 trigger")
        lines.append(f"- `{block.id}`：适用条件：{block.applies_when}；路径：`{block.vault_relative_path}`；锚点：`^{block.anchor}`；原因：{label}")
    return "\n".join(lines) + "\n"


def _validated_budgets(policy: dict[str, Any]) -> dict[str, int]:
    budgets = policy.get("budgets")
    if not isinstance(budgets, dict):
        raise ValueError("knowledge budget must be an object")
    result: dict[str, int] = {}
    for name in ("max_blocks", "max_chars"):
        value = budgets.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"knowledge budget {name} must be a positive integer")
        result[name] = value
    return result


def _signals(request: dict[str, Any], project_state: dict[str, Any]) -> list[str]:
    values: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in sorted(value, key=lambda item: str(item).casefold()):
                child = value[key]
                normalized_key = _normalize_signal(str(key))
                if _is_nonsemantic_field(normalized_key):
                    continue
                if normalized_key and _is_active_signal_value(child):
                    values.add(normalized_key)
                visit(child)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(item)
        elif isinstance(value, bool) or value is None:
            return
        elif _is_active_signal_value(value):
            normalized = _normalize_signal(str(value))
            if normalized:
                values.add(normalized)

    visit(request)
    visit(project_state)
    return sorted(values)


_INACTIVE_SIGNAL_TEXT = {
    "0",
    "false",
    "none",
    "null",
    "off",
    "disabled",
    "no",
    "关闭",
    "否",
    "无",
}

_NONSEMANTIC_EXACT_FIELDS = {
    "file",
    "files",
    "file_path",
    "file_paths",
    "path",
    "paths",
    "ref",
    "refs",
    "reference",
    "references",
    "hash",
    "checksum",
    "artifact",
    "artifacts",
    "history",
    "manifest",
}
_NONSEMANTIC_TERMINAL_TOKENS = {
    "file",
    "files",
    "path",
    "paths",
    "ref",
    "refs",
    "reference",
    "references",
}
_NONSEMANTIC_METADATA_TOKENS = {
    "hash",
    "checksum",
    "artifact",
    "artifacts",
    "history",
    "manifest",
}


def _normalize_signal(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _is_nonsemantic_field(normalized_key: str) -> bool:
    if normalized_key in _NONSEMANTIC_EXACT_FIELDS:
        return True
    tokens = tuple(
        token for token in re.split(r"[_\-.\s]+", normalized_key) if token
    )
    if not tokens:
        return False
    if any(token in _NONSEMANTIC_METADATA_TOKENS for token in tokens):
        return True
    return tokens[-1] in _NONSEMANTIC_TERMINAL_TOKENS


def _is_active_signal_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        normalized = _normalize_signal(value)
        return bool(normalized) and normalized not in _INACTIVE_SIGNAL_TEXT
    if isinstance(value, (int, float, complex)):
        return value != 0
    if isinstance(value, dict):
        return any(_is_active_signal_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_is_active_signal_value(item) for item in value)
    return True


def _runtime_contract_reason(
    block: KnowledgeBlock, policy: dict[str, Any]
) -> str | None:
    if block.authority != "wiki":
        return (
            "repo_authoritative"
            if block.authority == "repo"
            else "authority_not_allowed"
        )

    discovery = policy.get("discovery", {})
    allowed_statuses = set(discovery.get("allowed_statuses", ())).intersection(
        {"enabled", "confirmed"}
    )
    if block.status not in allowed_statuses:
        return "status_not_allowed"

    allowed_kinds = set(discovery.get("allowed_kinds", ()))
    if block.kind not in allowed_kinds:
        return "kind_not_allowed"
    return None


def _deduplicate_blocks(blocks: list[KnowledgeBlock]) -> list[KnowledgeBlock]:
    by_id: dict[str, KnowledgeBlock] = {}
    field_names = tuple(field.name for field in fields(KnowledgeBlock))
    for block in blocks:
        previous = by_id.get(block.id)
        if previous is None:
            by_id[block.id] = block
            continue
        if any(getattr(previous, name) != getattr(block, name) for name in field_names):
            raise ValueError(f"duplicate rule id has inconsistent content: {block.id}")
    return [by_id[rule_id] for rule_id in sorted(by_id)]


def _matches_scope(
    block: KnowledgeBlock, policy: dict[str, Any], skill: str
) -> bool:
    project = policy.get("project")
    project_match = "*" in block.projects or (
        isinstance(project, str) and project in block.projects
    )
    skill_match = "*" in block.stages or skill in block.stages
    return project_match and skill_match


def _profile_boundary_reason(
    block: KnowledgeBlock, profile: dict[str, Any]
) -> str | None:
    """Keep mixed-topic knowledge from crossing a Skill's declared hard boundary."""
    terms = profile.get("forbidden_knowledge_terms", [])
    if not isinstance(terms, list):
        raise ValueError("knowledge profile forbidden_knowledge_terms must be an array")
    searchable = "\n".join((block.applies_when, block.body))
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            raise ValueError(
                "knowledge profile forbidden_knowledge_terms must contain non-empty strings"
            )
        normalized = term.strip()
        if normalized in searchable:
            return f"skill_boundary:{normalized}"
    return None


def _trigger_hits(block: KnowledgeBlock, signals: list[str]) -> int:
    return sum(
        1
        for trigger in block.triggers
        if _normalize_signal(trigger)
        and any(
            _has_positive_trigger(signal, _normalize_signal(trigger))
            for signal in signals
        )
    )


_CHINESE_NEGATION_RE = re.compile(
    r"(?:并非|不是|不能|请勿|不得|不要|不应|避免|不需要|不包含|没有|不含|无需|禁止|禁用|关闭|无)"
    r"(?:\s*(?:再|说|使用|出现|包含|加入|展示|显示|呈现|采用|添加|安排|设置|保留|任何|相关|真实|的))*\s*$"
)
_CHINESE_NEGATOR_TOKEN_RE = re.compile(
    r"(?:并非|不是|不能|请勿|不得|不要|不应|避免|不需要|不包含|没有|不含|无需|禁止|禁用|关闭|无)"
)
_ENGLISH_NEGATION_RE = re.compile(
    r"(?:^|\s)(?:please\s+)?"
    r"(?:do\s+not|must\s+not|should\s+not|cannot|can't|avoid|without|no|disabled|disable|off)"
    r"(?:\s+(?:include|use|using|show|add|display|feature|contain|any|the))*\s*$"
)
_NEGATION_SUFFIXES = (
    "并非",
    "不是",
    "不能",
    "不需要",
    "不要",
    "禁止",
    "禁用",
    "关闭",
    "disabled",
    "off",
)
_NEGATION_SEPARATORS = " \t\r\n,，。.!！?？:：;；、()（）[]【】{}"
_CLAUSE_BOUNDARY_RE = re.compile(r"[,，。.!！?？:：;；\r\n]")
_PARALLEL_CONNECTOR_RE = re.compile(r"(?:和|以及|及|、)\s*$")
_NEGATION_RESET_RE = re.compile(r"(?:但|但是|不过|然而|却|而是|改为|需要|要|必须|保留|加入)")


def _has_positive_trigger(signal: str, trigger: str) -> bool:
    start = 0
    while True:
        index = signal.find(trigger, start)
        if index < 0:
            return False
        end = index + len(trigger)
        if _is_ascii_alphanumeric_trigger(trigger) and not _has_ascii_word_boundaries(
            signal, index, end
        ):
            start = index + 1
            continue
        raw_prefix = signal[max(0, index - 64) : index]
        prefix = raw_prefix.rstrip(_NEGATION_SEPARATORS)
        suffix = signal[end : end + 16].lstrip(_NEGATION_SEPARATORS)
        clause_prefix = _CLAUSE_BOUNDARY_RE.split(prefix)[-1]
        raw_clause_prefix = _CLAUSE_BOUNDARY_RE.split(raw_prefix)[-1]
        prefix_negated = bool(
            _CHINESE_NEGATION_RE.search(clause_prefix)
            or _ENGLISH_NEGATION_RE.search(clause_prefix)
            or _inherits_parallel_negation(raw_clause_prefix)
        )
        suffix_negated = any(
            _starts_with_negator(suffix, negator) for negator in _NEGATION_SUFFIXES
        )
        if not prefix_negated and not suffix_negated:
            return True
        start = end


def _inherits_parallel_negation(clause_prefix: str) -> bool:
    negators = list(_CHINESE_NEGATOR_TOKEN_RE.finditer(clause_prefix))
    if not negators or not _PARALLEL_CONNECTOR_RE.search(clause_prefix):
        return False
    tail = clause_prefix[negators[-1].end() :]
    return not _NEGATION_RESET_RE.search(tail)


def _is_ascii_alphanumeric_trigger(trigger: str) -> bool:
    return trigger.isascii() and trigger.isalnum()


def _has_ascii_word_boundaries(signal: str, start: int, end: int) -> bool:
    left_is_continuation = start > 0 and _continues_ascii_word(signal[start - 1])
    right_is_continuation = end < len(signal) and _continues_ascii_word(signal[end])
    return not left_is_continuation and not right_is_continuation


def _continues_ascii_word(character: str) -> bool:
    if character.isascii():
        return character.isalnum()
    if not character.isalnum():
        return False
    # Accented Latin letters remain part of the same word. Other scripts, most
    # importantly CJK in text such as "AI视频", form an intentional script
    # boundary. Underscores and hyphens are separators for structured flags.
    return "LATIN" in unicodedata.name(character, "")


def _starts_with_negator(value: str, negator: str) -> bool:
    if not value.startswith(negator):
        return False
    if not negator.isascii():
        return True
    boundary = len(negator)
    return boundary == len(value) or not value[boundary].isalnum()


def _rank_key(
    block: KnowledgeBlock,
    stage_topics: set[str],
    signals: list[str],
    policy: dict[str, Any],
) -> tuple[int, int, int, int, str]:
    status_score = policy.get("status_priority", {}).get(block.status, 0)
    topic_hits = len(stage_topics.intersection(block.topics))
    trigger_hits = _trigger_hits(block, signals)
    # Descending scores and an ascending ID are made explicit; using reverse=True
    # would accidentally reverse the deterministic ID tie-break as well.
    return (-trigger_hits, -topic_hits, -block.priority, -status_score, block.id)


def _deduplicate_canonical_keys(
    ranked: list[KnowledgeBlock],
) -> tuple[list[KnowledgeBlock], list[dict[str, str]]]:
    winners: list[KnowledgeBlock] = []
    seen_keys: set[str] = set()
    deferred: list[dict[str, str]] = []
    for block in ranked:
        if block.canonical_key in seen_keys:
            deferred.append(_deferred(block, "duplicate_canonical_key"))
            continue
        seen_keys.add(block.canonical_key)
        winners.append(block)
    return winners, deferred


def _conflicts(left: KnowledgeBlock, right: KnowledgeBlock) -> bool:
    return (
        right.canonical_key in left.conflicts_with
        or left.canonical_key in right.conflicts_with
    )


def _resolve_conflicts(
    ranked: list[KnowledgeBlock],
) -> tuple[list[KnowledgeBlock], list[dict[str, Any]], list[dict[str, str]]]:
    withheld: dict[str, str] = {}
    conflict_pairs: dict[tuple[str, str], tuple[str, str]] = {}

    confirmed = [block for block in ranked if block.status == "confirmed"]
    enabled = [block for block in ranked if block.status != "confirmed"]

    for index, left in enumerate(confirmed):
        for right in confirmed[index + 1 :]:
            if _conflicts(left, right):
                withheld[left.id] = "blocking_conflict"
                withheld[right.id] = "blocking_conflict"
                conflict_pairs[tuple(sorted((left.id, right.id)))] = (
                    "confirmed_peer_conflict",
                    "blocking_conflict",
                )

    active_confirmed = [block for block in confirmed if block.id not in withheld]
    for candidate in enabled:
        conflicting_winners = [
            winner for winner in active_confirmed if _conflicts(candidate, winner)
        ]
        if conflicting_winners:
            withheld[candidate.id] = "conflicts_with_confirmed"
            for winner in conflicting_winners:
                conflict_pairs[tuple(sorted((candidate.id, winner.id)))] = (
                    "confirmed_enabled_conflict",
                    "conflicts_with_confirmed",
                )

    active_enabled = [block for block in enabled if block.id not in withheld]
    for index, left in enumerate(active_enabled):
        for right in active_enabled[index + 1 :]:
            if _conflicts(left, right):
                withheld[left.id] = "blocking_conflict"
                withheld[right.id] = "blocking_conflict"
                conflict_pairs[tuple(sorted((left.id, right.id)))] = (
                    "enabled_peer_conflict",
                    "blocking_conflict",
                )

    by_id = {block.id: block for block in ranked}
    conflicts = []
    for left_id, right_id in sorted(conflict_pairs):
        conflict_type, reason = conflict_pairs[(left_id, right_id)]
        conflicts.append(
            {
                "canonical_keys": sorted(
                    [by_id[left_id].canonical_key, by_id[right_id].canonical_key]
                ),
                "page_paths": sorted(
                    {
                        by_id[left_id].vault_relative_path,
                        by_id[right_id].vault_relative_path,
                    }
                ),
                "reason": reason,
                "rule_ids": [left_id, right_id],
                "type": conflict_type,
            }
        )
    survivors = [block for block in ranked if block.id not in withheld]
    deferred = [
        {"rule_id": rule_id, "reason": withheld[rule_id]}
        for rule_id in sorted(withheld)
    ]
    return survivors, conflicts, deferred


def _apply_budget(
    ranked: list[KnowledgeBlock], budgets: dict[str, int]
) -> tuple[list[KnowledgeBlock], list[dict[str, str]], int]:
    selected: list[KnowledgeBlock] = []
    deferred: list[dict[str, str]] = []
    total_chars = 0
    for index, block in enumerate(ranked):
        body_chars = len(block.body.strip())
        if len(selected) >= budgets["max_blocks"]:
            reason = "budget_blocks"
        elif total_chars + body_chars > budgets["max_chars"]:
            deferred.append(_deferred(block, "budget_chars"))
            continue
        else:
            selected.append(block)
            total_chars += body_chars
            continue
        deferred.extend(_deferred(item, reason) for item in ranked[index:])
        break
    return selected, deferred, total_chars


def _deferred(block: KnowledgeBlock, reason: str) -> dict[str, str]:
    return {"rule_id": block.id, "reason": reason}
