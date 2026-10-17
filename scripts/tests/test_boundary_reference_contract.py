"""Configuration assertions only; not a generated-image input inspection."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_new_boundary_images_use_base_references_not_previous_boundary():
    skill = (ROOT / 'skills/storyboard-director/SKILL.md').read_text()
    rules = (ROOT / 'skills/storyboard-director/references/image-continuity-hard-rules.md').read_text()
    assert '#新边界分镜图独立取基础参考' in skill
    assert '## 新边界分镜图独立取基础参考' in rules
    assert '禁止把前一张已生成的边界分镜图作为下一张边界图的生图输入' in rules
    assert '获准的基础场景、人物、服装和道具参考' in rules
    assert '同一基础素材和明确的位置、朝向、持物及动作相位' in rules
    assert '不扩大为禁止普通图像编辑' in rules
    assert '新边界图生图须遵守上节独立基础参考规则' in rules


def test_shared_video_boundary_and_multi_reference_remain_valid():
    rules = (ROOT / 'skills/storyboard-director/references/image-continuity-hard-rules.md').read_text()
    video = (ROOT / 'skills/voiceover-to-video-script/SKILL.md').read_text()
    platform = (ROOT / 'skills/voiceover-to-video-script/references/seedance-platform-contract.md').read_text()
    assert '相邻视频仍可共用同一张已确认的边界图作为前段尾态和后段首态' in rules
    assert '当前只能多参考时，把同一状态图真正放入两段输入' in rules
    assert '图片连续性' in video
    assert 'multimodal2video' in platform and '全能参考' in platform
