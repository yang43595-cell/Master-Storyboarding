"""Bounded offline reading/export regression; no deployment or approval claims."""
import copy
import json

from core.director_reading import director_scene_reading
from core.story_sources import export_markdown
from test_director_compact import compact_package
from test_storyboard_planner_contract import sample_package


def test_flow_is_main_and_old_summary_keeps_execution_visible():
    p = compact_package()
    before = copy.deepcopy(p)
    scene = p['scenes'][0]
    v = director_scene_reading(scene)
    assert v['technical_open'] and '阅读提示' in v['body']
    assert scene['space_rules']['axis'] in v['body'] and '左侧窗光' in v['body']
    scene['audiovisual_flow'] = 'S01-010 人物说：“不。” 随后放下物品。'
    scene['shots'][0]['overrides'] = {'music': []}
    v = director_scene_reading(scene)
    assert not v['technical_open'] and '无（源字段为空数组' in v['technical']
    assert before['scenes'][0]['audiovisual_flow'] != p['scenes'][0]['audiovisual_flow']


def test_md_readable_first_full_source_and_exclusive_create(tmp_path):
    for i, p in enumerate((compact_package(), sample_package())):
        before = copy.deepcopy(p)
        out = tmp_path / f'reading-{i}.md'
        export_markdown(p, out)
        text = out.read_text()
        main = text.split('## 完整结构化来源')[0]
        assert p['scenes'][0]['scene_id'] in main and '逐镜执行附录' in main
        assert 'DLG-01' in main and '阅读提示' in main
        if p['schema_version'] == '4.0':
            assert '不。' in main
        assert json.loads(text.split('```json\n')[1].rsplit('\n```', 1)[0]) == before == p
        try:
            export_markdown(p, out)
        except FileExistsError:
            pass
        else:
            raise AssertionError('must not overwrite')
