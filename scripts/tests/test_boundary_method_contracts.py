"""Bounded method-text regressions, not an NLP or production approval gate.

These checks detect loss of the selected operational instructions. Actual prose
still needs source/body/state/neighbor review; keyword presence proves neither
method application nor model behavior. All fixtures below are text/JSON only.
"""
import copy
import os
from pathlib import Path

import pytest
from scripts.core import quality_evidence as q

ROOT = Path(os.environ.get('MSB_METHOD_TEST_ROOT', Path(__file__).resolve().parents[2]))
PATHS = {
    'director': 'skills/storyboard-planner/modules/shot-planning.md',
    'video': 'skills/voiceover-to-video-script/modules/video-method.md',
    'image': 'skills/storyboard-director/references/image-continuity-hard-rules.md',
}
# Anchors are finite documentation regressions, NOT a semantic parser.
REQUIRED = {
    'director': ('回查已知完整导演/视频源', '核验同版后恢复映射', '未读取不等于缺资料'),
    'video': ('`cut.exit/trigger`', '不可直接复制成 `exit_state`',
              '本段正文最后实际动作', '下段入态及公共开场', '下段正文第一动作',
              '不只改标签而保留正文重播', '完整来源的全部适用参考职责',
              '不强制全部完成', '跨场、明确时间省略和新一轮事件'),
    'image': ('明确冲突的整图退出本轮候选活动输入', 'limited-purpose免责',
              '不把本地计划排除说成远端已解绑', '逐项核原职责', '继续阻断，不造素材ID'),
}

def text(kind):
    return (ROOT / PATHS[kind]).read_text()

def missing(kind, body):
    return [anchor for anchor in REQUIRED[kind] if anchor not in body]

@pytest.mark.parametrize('kind', PATHS)
def test_existing_method_contains_operational_repair(kind):
    assert not missing(kind, text(kind))

@pytest.mark.parametrize('kind,anchor', [
    ('director', '未读取不等于缺资料'),
    ('video', '不可直接复制成 `exit_state`'),
    ('video', '下段入态及公共开场'),
    ('video', '不只改标签而保留正文重播'),
    ('video', '完整来源的全部适用参考职责'),
    ('video', '不强制全部完成'),
    ('image', '明确冲突的整图退出本轮候选活动输入'),
    ('image', '不把本地计划排除说成远端已解绑'),
])
def test_removing_specific_instruction_is_detected(kind, anchor):
    body = text(kind)
    assert anchor in body
    assert anchor in missing(kind, body.replace(anchor, ''))

@pytest.mark.parametrize('aspect', ['identity_wardrobe', 'space_objects', 'boundary_state'])
def test_known_pixel_conflict_stays_failed_despite_limited_use_label(aspect):
    evidence = dict(status='pass', observer='SYNTHETIC ONLY', checked_at='2026-10-26T00:00:00Z',
                    checks={k: dict(result='pass', observation='synthetic', locator='text fixture')
                            for k in q.IMAGE_ASPECTS})
    evidence['checks'][aspect].update(result='fail', observation='known whole-image conflict')
    evidence['limited_use'] = {'purpose': 'only_prop', 'excluded_roles': [aspect]}
    with pytest.raises(ValueError, match=aspect):
        q._observations(evidence, q.IMAGE_ASPECTS)

def test_new_state_or_body_cannot_reuse_old_review_but_new_hash_is_not_semantic_proof():
    draft = dict(prompt='The hand has released the box.', entry_state='Box supported.',
                 exit_state='Hand empty.', source_shots=['SYNTHETIC-1'])
    draft['quality_evidence'] = dict(status='pass', observer='SYNTHETIC ONLY',
        checked_at='2026-10-26T00:00:00Z', checks={k: dict(result='pass', observation='synthetic',
        locator='text fixture') for k in q.DRAFT_ASPECTS})
    draft['quality_evidence']['checks']['packaging_identity'].update(scope='present', result='pass', items=[
        dict(entity_id='SYNTHETIC-BOX', classification='ordinary_prop', source_basis='Synthetic ordinary storage prop',
             source_shot_ids=['SYNTHETIC-1'], prompt_claim='ordinary_prop')])
    draft['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(draft))
    assert q.validate_draft_evidence(draft)['visual_quality_verified'] is False
    changed = copy.deepcopy(draft); changed['exit_state'] = 'Next segment starts the transfer.'
    with pytest.raises(ValueError, match='旧检查失效'):
        q.validate_draft_evidence(changed)
    # A reviewer, not the fingerprint function, must identify semantic failure.
    changed['quality_evidence']['input_sha256'] = q.fingerprint(q.draft_binding(changed))
    changed['quality_evidence']['checks']['state_boundaries']['result'] = 'fail'
    with pytest.raises(ValueError, match='state_boundaries'):
        q.validate_draft_evidence(changed)
