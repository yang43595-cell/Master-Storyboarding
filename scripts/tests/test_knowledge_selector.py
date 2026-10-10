from pathlib import Path

from scripts.core.knowledge_blocks import KnowledgeBlock
from scripts.core.knowledge_selector import render_knowledge_bundle, render_knowledge_candidates, select_knowledge


def _block(
    rule_id: str,
    *,
    triggers: tuple[str, ...],
    vault_relative_path: str = "wiki/test.md",
    anchor: str | None = None,
) -> KnowledgeBlock:
    return KnowledgeBlock(
        id=rule_id,
        canonical_key=rule_id.casefold(),
        kind="rule",
        status="enabled",
        authority="wiki",
        projects=("Master-Storyboarding",),
        stages=("voiceover-to-video-script",),
        topics=("continuity",),
        triggers=triggers,
        conflicts_with=(),
        priority=1,
        sources=(),
        applies_when="测试",
        body="测试正文。",
        page_path=Path(vault_relative_path),
        vault_relative_path=vault_relative_path,
        anchor=anchor or rule_id.casefold(),
        fingerprint=rule_id,
    )


def test_triggered_rules_are_discoverable_without_body_until_a_positive_trigger_and_general_rules_keep_topic_match() -> None:
    triggered = _block(
        "MSB-KB-TEST-TRIGGERED",
        triggers=("科普动画",),
        vault_relative_path="wiki/concepts/按需候选.md",
        anchor="msb-kb-explicit-anchor",
    )
    general = _block("MSB-KB-TEST-GENERAL", triggers=())
    policy = {
        "project": "Master-Storyboarding",
        "budgets": {"max_blocks": 8, "max_chars": 6000},
        "discovery": {"allowed_statuses": ["enabled"], "allowed_kinds": ["rule"]},
    }
    profile = {"topics": ["continuity"]}

    no_trigger = select_knowledge(
        [triggered, general], policy, "voiceover-to-video-script",
        {"task": "真人饭局"}, {}, set(), profile,
    )
    assert [block.id for block in no_trigger.selected] == ["MSB-KB-TEST-GENERAL"]
    assert [block.id for block in no_trigger.candidates] == [triggered.id]
    assert "MSB-KB-TEST-TRIGGERED" not in render_knowledge_bundle("voiceover-to-video-script", no_trigger)
    directory = render_knowledge_candidates(no_trigger)
    assert "MSB-KB-TEST-TRIGGERED" in directory
    assert "wiki/concepts/按需候选.md" in directory
    assert "^msb-kb-explicit-anchor" in directory
    assert triggered.body not in directory

    positive_trigger = select_knowledge(
        [triggered, general], policy, "voiceover-to-video-script",
        {"task": "科普动画"}, {}, set(), profile,
    )
    assert {block.id for block in positive_trigger.selected} == {triggered.id, general.id}
    assert positive_trigger.candidates == []
    assert triggered.body in render_knowledge_bundle("voiceover-to-video-script", positive_trigger)


def test_large_candidate_catalog_stays_out_of_startup_body() -> None:
    blocks = [_block(f"MSB-KB-CAND-{index}", triggers=(f"触发{index}",), vault_relative_path=f"wiki/candidates/{index}.md") for index in range(500)]
    policy = {"project": "Master-Storyboarding", "budgets": {"max_blocks": 8, "max_chars": 6000}, "discovery": {"allowed_statuses": ["enabled"], "allowed_kinds": ["rule"]}}
    result = select_knowledge(blocks, policy, "voiceover-to-video-script", {"task": "真人"}, {}, set(), {"topics": ["continuity"]})
    directory = render_knowledge_candidates(result)
    assert len(result.candidates) == 500
    assert len(directory.encode("utf-8")) > 48 * 1024
    startup_summary = f"- 共 {len(result.candidates)} 条；完整可读目录：`knowledge_candidates.md`。"
    assert len(startup_summary.encode("utf-8")) < 200
