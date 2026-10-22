"""Bounded offline reading/export regression; no deployment or approval claims."""
import copy
import json
import subprocess
from pathlib import Path
from core.director_reading import director_scene_reading, TECHNICAL_MARKER
from core.story_sources import export_markdown
from test_director_compact import compact_package
from test_storyboard_planner_contract import sample_package
from test_web_content_adapter import compile_fixture, registry

RENDERER = Path(__file__).resolve().parents[1] / 'review/script-template.js'


def render(content):
    code = "const m=require(process.argv[1]);const c=JSON.parse(require('fs').readFileSync(0,'utf8'));const v=m.fromPackage(c);console.log(JSON.stringify({view:v,html:m.story(v.scenes,c.revision)}));"
    result = subprocess.run(['node','-e',code,str(RENDERER)],input=json.dumps(content),text=True,capture_output=True,check=True)
    return json.loads(result.stdout)


def test_flow_is_main_and_old_summary_keeps_execution_visible(tmp_path):
    p=compact_package();before=copy.deepcopy(p);scene=p['scenes'][0]
    v=director_scene_reading(scene)
    assert v['technical_open'] and '阅读提示' in v['body']
    assert scene['space_rules']['axis'] in v['body'] and '左侧窗光' in v['body']
    scene['audiovisual_flow']='S01-010 人物说：“不。” 随后放下物品。'
    scene['shots'][0]['overrides']={'music':[]}
    v=director_scene_reading(scene)
    assert not v['technical_open'] and '无（源字段为空数组' in v['technical']
    output=compile_fixture('director',p,registry(tmp_path))
    content=output['request']['content'];view=render(content)
    assert len(view['view']['scenes'])==1
    s=view['view']['scenes'][0]
    assert s['id']==scene['scene_id'] and scene['audiovisual_flow'] in '\n'.join(s['lines'])
    assert '观看目的' not in '\n'.join(s['lines'])
    assert '<details class="simple-details director-technical">' in view['html']
    assert any(b['id']=='canonical-source' for b in view['view']['extras'])
    assert json.loads(next(b['text'] for b in content['blocks'] if b['id']=='canonical-source'))==p
    assert output['published'] is False
    assert before['scenes'][0]['audiovisual_flow'] != p['scenes'][0]['audiovisual_flow']


def test_old_payload_and_escaped_text_are_lossless(tmp_path):
    p=compact_package();p['scenes'][0]['audiovisual_flow'] += '<img src=x onerror=alert(1)>'
    content=compile_fixture('director',p,registry(tmp_path))['request']['content']
    view=render(content)
    assert 'director-technical" open' in view['html']
    assert '<img' not in view['html'] and '&lt;img' in view['html']
    content['blocks'][0]['text']=content['blocks'][0]['text'].replace(TECHNICAL_MARKER,'历史执行卡')
    view=render(content)
    assert '历史执行卡' in '\n'.join(view['view']['scenes'][0]['lines'])
    assert all('结构化来源' not in s['heading'] for s in view['view']['scenes'])


def test_md_readable_first_full_source_and_exclusive_create(tmp_path):
    for i,p in enumerate((compact_package(), sample_package())):
        before=copy.deepcopy(p);out=tmp_path/f'reading-{i}.md';export_markdown(p,out)
        text=out.read_text();main=text.split('## 完整结构化来源')[0]
        assert p['scenes'][0]['scene_id'] in main and '逐镜执行附录' in main
        assert 'DLG-01' in main and '阅读提示' in main
        if p['schema_version']=='4.0':
            assert '不。' in main
        assert json.loads(text.split('```json\n')[1].rsplit('\n```',1)[0])==before==p
        try:export_markdown(p,out)
        except FileExistsError:pass
        else:raise AssertionError('must not overwrite')


def test_non_numeric_script_ids_keep_source_out_of_main():
    content={'stage':'script','revision':'R01','blocks':[
        {'id':'STP-X','heading':'正文段落｜STP-X','text':'完整正文','media':[]},
        {'id':'canonical-source','heading':'结构化来源（只读）','text':'{}','media':[]},
        {'id':'source-binding','heading':'来源版本','text':'{}','media':[]}]}
    view=render(content)['view']
    assert len(view['scenes'])==1 and view['scenes'][0]['lines']==['完整正文']
    assert len(view['extras'])==2
