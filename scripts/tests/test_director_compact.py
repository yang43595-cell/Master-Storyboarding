"""Compact contract preserves take states and exact cross-shot speech."""
import copy
import json
import pytest
from test_storyboard_planner_contract import sample_package
from test_story_paragraph_sources import narrative
from core.schema_contracts import schema_errors
from core.story_sources import validate_story_sources


def compact_package():
    p = sample_package()
    p['schema_version'] = '4.0'
    p.pop('continuity_register')
    p['timing_basis'] = {'status':'not_evaluable','source_type':None,'source_path':None,'source_field':None}
    scene = p['scenes'][0]
    scene.pop('beats')
    scene['audiovisual_flow'] = 'S01-010 人物拒绝并放下物品，随后看对方反应。'
    scene['defaults'] = {'light_color_intent':'左侧窗光','ambience':['室内底声'],'music':[]}
    old = scene['shots'][0]
    shot = {k:old[k] for k in ['shot_id','source_paragraph_ids','source_dialogue_ids','source_asset_requirement_ids','purpose','cut']}
    shot.update(camera={'framing':'中近景，眼高，人物偏左','movement':'固定','focus':'人物眼睛'},
                take={'start':'人物持物看对方','process':'说完拒绝，把物品放在桌面','end':'物品在桌上，手已松开'},
                rhythm='拒绝后停半拍，接对方反应',audio={'dialogue':[{'dialogue_id':'DLG-01','speaker_id':'CHR-A','text':'不。','presentation':'SYNC'}],'sfx':['放物轻声']})
    scene['shots']=[shot]
    return p


def test_compact_and_legacy_sources_both_read(tmp_path):
    n=narrative(tmp_path)
    validate_story_sources(n,compact_package(),project_root=tmp_path)
    validate_story_sources(n,sample_package(),project_root=tmp_path)


@pytest.mark.parametrize('key',['start','process','end'])
def test_missing_take_state_rejected(key):
    p=compact_package();p['scenes'][0]['shots'][0]['take'].pop(key)
    assert schema_errors(p,'storyboard-package.schema.json')


def test_cross_shot_fragments_and_reaction(tmp_path):
    p=compact_package();s=p['scenes'][0]['shots'][0]
    s['audio']['dialogue'][0]['text']='不'
    reaction=copy.deepcopy(s);reaction['shot_id']='S01-020'
    reaction['audio']['dialogue'][0].update(text='。',presentation='OFFSCREEN')
    p['scenes'][0]['shots'].append(reaction)
    validate_story_sources(narrative(tmp_path),p,project_root=tmp_path)
    reaction['audio']['dialogue'][0]['text']='不。'
    with pytest.raises(ValueError,match='遗漏、重复或改写'):
        validate_story_sources(narrative(tmp_path),p,project_root=tmp_path)


@pytest.mark.parametrize('mutation',['speaker','missing','reference'])
def test_bad_speech_rejected(tmp_path,mutation):
    p=compact_package();s=p['scenes'][0]['shots'][0]
    if mutation=='speaker':s['audio']['dialogue'][0]['speaker_id']='OTHER'
    if mutation=='missing':s['audio']['dialogue'][0]['text']='不'
    if mutation=='reference':s['source_dialogue_ids']=[]
    with pytest.raises(ValueError):validate_story_sources(narrative(tmp_path),p,project_root=tmp_path)


def test_motion_handoff_time_jump_and_explicit_silence(tmp_path):
    p=compact_package();scene=p['scenes'][0];s=scene['shots'][0]
    s['camera']['movement']='连续右移至桌边，跟随手中物，保持同一侧'
    s['take']={'start':'甲持物，乙空手','process':'甲递出，乙接稳后甲松手','end':'物在乙手中，甲空手'}
    s['overrides']={'music':[]}
    scene['defaults']['music']=['低音乐']
    second=copy.deepcopy(scene);second['scene_id']='S02';second['start_state']='稍后，同房间，乙已站着持物'
    second['shots'][0]['shot_id']='S02-010';second['shots'][0]['source_dialogue_ids']=[];second['shots'][0]['audio']['dialogue']=[]
    second['shots'][0].pop('overrides')
    p['scenes'].append(second)
    validate_story_sources(narrative(tmp_path),p,project_root=tmp_path)
    assert s['overrides']['music']==[] and 'overrides' not in second['shots'][0]


def test_delivery_survives_cross_shot_roundtrip_without_changing_text(tmp_path):
    p = compact_package()
    shot = p['scenes'][0]['shots'][0]
    shot['audio']['dialogue'][0].update(text='不', delivery='直接拒绝，坚定')
    listener = copy.deepcopy(shot)
    listener['shot_id'] = 'S01-020'
    listener['audio']['dialogue'][0].update(text='。', presentation='OFFSCREEN', delivery='承接前镜坚定语气，收句')
    p['scenes'][0]['shots'].append(listener)
    saved = json.loads(json.dumps(p, ensure_ascii=False))
    validate_story_sources(narrative(tmp_path), saved, project_root=tmp_path)
    assert saved['scenes'][0]['shots'][1]['audio']['dialogue'][0]['delivery'] == '承接前镜坚定语气，收句'
    listener['audio']['dialogue'][0]['text'] = '（坚定）。'
    with pytest.raises(ValueError, match='遗漏、重复或改写'):
        validate_story_sources(narrative(tmp_path), p, project_root=tmp_path)


@pytest.mark.parametrize('mutation', [None, 'delivery', 'speaker', 'text', 'missing', 'duplicate'])
def test_on_screen_text_keeps_silent_contract_and_source_coverage(tmp_path, mutation):
    p = compact_package()
    shot = p['scenes'][0]['shots'][0]
    placement = shot['audio']['dialogue'][0]
    placement['presentation'] = 'ON_SCREEN_TEXT'
    shot['take'] = {'start': '画面留白', 'process': '显示原文：不。', 'end': '文字保持可读'}
    shot['rhythm'] = '按阅读停留，不发声'
    if mutation == 'delivery':
        placement['delivery'] = '仅文字，不发声'
    elif mutation == 'speaker':
        placement['speaker_id'] = 'OTHER'
    elif mutation == 'text':
        placement['text'] = '改字'
    elif mutation == 'missing':
        shot['audio']['dialogue'] = []
    elif mutation == 'duplicate':
        shot['audio']['dialogue'].append(copy.deepcopy(placement))
    source = narrative(tmp_path)
    if mutation is None:
        validate_story_sources(source, p, project_root=tmp_path)
    else:
        with pytest.raises(ValueError):
            validate_story_sources(source, p, project_root=tmp_path)


def write_asset_choices(tmp_path, n, *, confirmed=True, revision='R01', refs=None, excluded=None):
    from visual_asset_choices import RELATIVE
    added = copy.deepcopy(n['asset_requirements'][0])
    added.update(asset_id='AST-USER-01', source_paragraph_ids=refs or ['STP-01'])
    choices = {'revision': 1, 'added': [added], 'excluded': excluded or {}, 'replaced': {},
               'history': [{'operation': 'add', 'ast_id': 'AST-USER-01',
                            'user_confirmed': confirmed, 'source_revision': revision}]}
    path = tmp_path / RELATIVE
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(choices), encoding='utf-8')


def test_confirmed_asset_addition_and_image_exclusion_preserve_story(tmp_path):
    n = narrative(tmp_path)
    before = copy.deepcopy(n)
    write_asset_choices(tmp_path, n, excluded={'AST-CHAR-A': {'name': '主角'}})
    p = compact_package()
    p['source_asset_requirement_ids'].append('AST-USER-01')
    p['scenes'][0]['shots'][0]['source_asset_requirement_ids'].append('AST-USER-01')
    validate_story_sources(n, p, project_root=tmp_path)
    assert n == before  # Neither original references nor story approval is rewritten.


@pytest.mark.parametrize('kwargs,message', [
    ({'confirmed': False}, '用户确认'),
    ({'revision': 'R00'}, '用户确认'),
    ({'refs': ['STP-MISSING']}, '出现位置已失效'),
])
def test_unconfirmed_or_stale_asset_additions_fail(tmp_path, kwargs, message):
    n = narrative(tmp_path)
    write_asset_choices(tmp_path, n, **kwargs)
    with pytest.raises(ValueError, match=message):
        validate_story_sources(n, compact_package(), project_root=tmp_path)


@pytest.mark.parametrize('mutation', [None, 'unconfirmed', 'stale', 'missing_snapshot',
                                    'changed_child', 'changed_mapping', 'unrelated_child'])
def test_batch_split_approval_is_bound_to_saved_children(tmp_path, mutation):
    from visual_asset_choices import RELATIVE, save_choices
    n = narrative(tmp_path)
    child = copy.deepcopy(n['asset_requirements'][0])
    child['asset_id'] = 'AST-SPLIT-01'
    choices = {'revision': 1, 'added': [child], 'excluded': {},
               'replaced': {'AST-CHAR-A': {'name': '主角', 'children': ['AST-SPLIT-01']}},
               'history': [{'operation': 'split', 'user_confirmed': mutation != 'unconfirmed',
                            'source_revision': 'R00' if mutation == 'stale' else 'R01'}]}
    save_choices(tmp_path, n, choices)
    path = tmp_path / RELATIVE
    if mutation == 'missing_snapshot':
        path.parent.joinpath('需求调整历史/R001.json').unlink()
    if mutation == 'changed_child': choices['added'][0]['name'] = '另一个人物'
    if mutation == 'changed_mapping': choices['replaced']['AST-CHAR-A']['children'] = []
    if mutation == 'unrelated_child':
        other = copy.deepcopy(child)
        other['asset_id'] = 'AST-UNAPPROVED'
        choices['added'].append(other)
    path.write_text(json.dumps(choices), encoding='utf-8')
    before = path.read_bytes()
    p = compact_package()
    p['source_asset_requirement_ids'].append('AST-SPLIT-01')
    p['scenes'][0]['shots'][0]['source_asset_requirement_ids'].append('AST-SPLIT-01')
    if mutation is None:
        validate_story_sources(n, p, project_root=tmp_path)
    else:
        with pytest.raises(ValueError, match='用户确认'):
            validate_story_sources(n, p, project_root=tmp_path)
    assert path.read_bytes() == before
