import base64
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts import redframe_image_revision as revision
from scripts.core.io import read_json, sha256_file, write_json


def inputs(tmp_path, monkeypatch, *, storyboard=False):
    source = tmp_path / '已确认.png'
    source.write_bytes(b'confirmed source bytes')
    target = 'SHOT-11' if storyboard else 'AST-HALL@BUSY'
    args = ['--project', str(tmp_path), '--request', '修订请求.json', '--previous', '上一版.json',
            '--receipt', '新候选.json']
    native = dict(model=json.dumps(['volcengineArkVideo', 'doubao-seedream-5-5-261028']),
                  count=1, ratio='16:9', size='2K')
    write_json(tmp_path / '上一版.json', dict(target='SHOT-11' if storyboard else 'AST-HALL@WIDE',
               image_submission=dict(node_id='old', canvas_id='hall', project_id='project1',
                                     settings=native, sources={'source': sha256_file(source)})))
    write_json(tmp_path / '修订请求.json', dict(image_targets=[target], previous_target='SHOT-11' if storyboard else 'AST-HALL@WIDE', brief='调近人物站位',
               image_output_kinds={target: '环境'}, source_files=[source.name]))
    monkeypatch.setattr(revision, 'load_kickoff', lambda _: dict(canvas=dict(platform='redframe',
                        target_url='https://redframe-ai.xmpaohong.com:28888/#/workspace?canvasId=hall',
                        target_project_id='project1'), image_generation=dict(provider='redframe',
                        model='Doubao Seedream 5.5', scope='required_assets_and_supplements')))
    return args


@pytest.mark.parametrize('storyboard', [False, True])
def test_single_revision_preview_uses_approved_source_only(tmp_path, monkeypatch, storyboard):
    args = inputs(tmp_path, monkeypatch, storyboard=storyboard)
    original = set(tmp_path.iterdir())
    plan = revision.main(args)
    assert plan['submits'] == 0 and set(plan['sources']) == {'source'}
    assert set(tmp_path.iterdir()) == original
    (tmp_path / '已确认.png').write_bytes(b'replaced')
    with pytest.raises(ValueError, match='唯一复用'):
        revision.main(args)


def test_gate_blocks_before_canvas_and_query_resumes_without_resubmission(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch, storyboard=True) + ['--apply', '--actor-id', 'editor', '--task-id', 'rev']
    calls = []
    output = 'assets/candidate/out.png'
    def write_attempt(node, *, receipt, save_receipt, **kwargs):
        calls.append('submit')
        receipt['image_submission'] = dict(node_id=node)
        save_receipt(receipt)
        raise RuntimeError('timeout after click')
    buf = BytesIO()
    Image.new('RGB', (32, 24)).save(buf, format='PNG')
    def query(r, *, timeout):
        calls.append('query')
        return dict(node_id='candidate', status='output_observed', output=output)
    ui = SimpleNamespace(
        resume_image_node=lambda r, save, x, y: r.setdefault('node_id', 'candidate'),
        rename=lambda *_: None, connect=lambda *_: None, write_prompt=lambda *_: 'prompt',
        submit_once=write_attempt, query=query,
        finish_asset_image=lambda p,r,s,output_kind:r['image_result'],
        assert_target=lambda:None, page=SimpleNamespace(reload=lambda **_:None,
            evaluate=lambda *_:base64.b64encode(buf.getvalue()).decode()),
        workspace=SimpleNamespace(directory='project1', read_canvas=lambda:{'nodes':[
            dict(type='remote-imageGenerationNode',id='candidate',data=dict(outputs={'image':{'value':{'url':output}}}))]}))
    @contextmanager
    def session(*args, **kwargs):
        calls.append('browser')
        yield ui
    monkeypatch.setattr(revision,'image_browser_session',session)
    monkeypatch.setattr(revision,'new_card_point',lambda *_:(10,20))
    monkeypatch.setattr(revision,'configure',lambda *_:None)
    with pytest.raises(ValueError):
        revision.main(args,host_browser=True)
    assert calls == []
    monkeypatch.setattr(revision,'check_preparation',lambda *_:calls.append('gate'))
    with pytest.raises(RuntimeError,match='timeout after click'):
        revision.main(args,host_browser=True)
    result=revision.main(args,host_browser=True)
    assert result['next_step']=='review_local_file'
    assert read_json(tmp_path/'新候选.json')['canvas_verified_phase']=='after_refresh'
    other = list(args)
    other[other.index('新候选.json')] = '另一个回执.json'
    assert revision.main(other,host_browser=True)==result
    assert not (tmp_path / '另一个回执.json').exists()
    assert revision.main(args,host_browser=True)==result
    assert calls==['gate','browser','submit','browser','query']


def test_existing_legacy_submission_with_wrong_binding_is_not_reused(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch, storyboard=True)
    previous = read_json(tmp_path / '上一版.json')
    write_json(tmp_path / '新候选.json', {
        'target': previous['target'], 'node_id': 'new',
        'request_sha256': sha256_file(tmp_path / '修订请求.json'),
        'image_submission': {**previous['image_submission'], 'node_id': 'new'}})
    assert revision.main(args)['next_step'] == 'query_original_submission'
    current = read_json(tmp_path / '新候选.json')
    current['image_submission']['sources'] = {'wrong-card': 'a' * 64}
    write_json(tmp_path / '新候选.json', current)
    with pytest.raises(ValueError, match='其他请求'):
        revision.main(args)


def test_prepare_request_patches_only_one_scene_and_keeps_original(tmp_path):
    p = tmp_path / '制作请求.json'
    images = [tmp_path / (name+'.png') for name in ('旧候机厅','旧客厅')]
    for path in images:
        path.write_bytes(path.name.encode())
    scenes = [dict(asset_id='AST-'+str(i), output_id='WIDE', prompt='早班旅客稀少。',
                   source={'path':path.name,'sha256':sha256_file(path)}) for i,path in enumerate(images)]
    old = dict(brief='两场景制作', image_targets=['AST-0@WIDE','AST-1@WIDE'],
               scenes=scenes, image_output_kinds=dict.fromkeys(['AST-0@WIDE','AST-1@WIDE'],'环境'),
               source_files=[p.name for p in images], decision_ref='previous approval')
    write_json(p, old)
    write_json(tmp_path/'上版.json', {'request_sha256':sha256_file(p),'target':'AST-0@WIDE'})
    args = ['prepare-request','--project',str(tmp_path),'--request','新制作请求.json','--previous','上版.json',
            '--replace','早班旅客稀少','大部分座位坐满']
    result = revision.main(args)
    new = read_json(Path(result['request']))
    assert new['image_targets']==['AST-0@WIDE'] and new['source_files']==['旧候机厅.png']
    assert new['scenes'][0]['prompt']=='大部分座位坐满。' and 'decision_ref' not in new
    assert read_json(p)==old
    with pytest.raises(ValueError, match='未唯一命中'):
        revision.main(args[:-2]+['不存在','换措辞'])


def test_canvas_creation_only_is_serialized_across_requests(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    first_inside, second_attempting, second_inside = (threading.Event() for _ in range(3))
    def ui(label):
        def create(*args):
            if label=='first':
                first_inside.set()
                assert second_attempting.wait(2)
                assert not second_inside.wait(.1)
            else:
                second_inside.set()
            return label
        def end_before_submit(*args):
            if label=='first':
                assert second_inside.wait(2), '锁不应覆盖后续写词'
            raise ValueError('本测试停在建卡后')
        return SimpleNamespace(resume_image_node=create, rename=end_before_submit)
    plan = {'canvas_url':'https://redframe-ai.xmpaohong.com:28888/#/workspace?canvasId=one','original_node':'source'}
    monkeypatch.setattr(revision,'new_card_point',lambda *args:(10,10))
    def run(label):
        if label=='second':
            assert first_inside.wait(2)
            second_attempting.set()
        with pytest.raises(ValueError, match='停在建卡后'):
            revision.run_revision(ui(label), tmp_path, plan, {}, lambda:None, tmp_path/label)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run, ['first','second']))


def test_new_output_requires_explicit_parent_even_in_local_preview(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    request = tmp_path / '修订请求.json'
    spec = read_json(request)
    del spec['previous_target']
    write_json(request, spec)
    with pytest.raises(ValueError, match='previous_target'):
        revision.main(args)
    assert not (tmp_path / '新候选.json').exists()
