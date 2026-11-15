"""Narrow offline regressions: no paid calls, media work or production writes."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import stage_context_cli as cli
from scripts.core.knowledge_blocks import KnowledgeBlock, load_policy
from scripts.core.knowledge_selector import select_knowledge, render_knowledge_candidates
from scripts.core.review_delta import fingerprint, lightweight_revision, revision_delta
from test_stage_context_cli import isolated_stage_knowledge


pytestmark = pytest.mark.usefixtures("isolated_stage_knowledge")


def block(i, **changes):
    value = KnowledgeBlock(id=f'MSB-KB-TEST-{i:03}', canonical_key=f'test-{i}', kind='rule',
        status='enabled', authority='wiki', projects=('Master-Storyboarding',),
        stages=('creative-director',), topics=('narrative',), triggers=(), conflicts_with=(),
        priority=1, sources=(), applies_when='本次故事判断', body='核对人物动机。',
        page_path=Path('wiki/concepts/fixture.md'), vault_relative_path='wiki/concepts/fixture.md',
        anchor=f'test-{i}', fingerprint=f'hash-{i}')
    return replace(value, **changes)


def select(blocks, tombstones=set()):
    return select_knowledge(blocks, load_policy(cli.ROOT), 'creative-director', {}, {}, tombstones,
                            {'topics':['narrative'], 'forbidden_knowledge_terms':['机位','提示词']})


def test_boundary_mixed_declared_consumer_is_discoverable_but_not_selected():
    mixed=block(1, body='人物动机应清楚；机位实现留导演。')
    wrong_stage=block(2, stages=('storyboard-planner',), body='设计机位参数。')
    result=select([mixed,wrong_stage])
    assert result.selected == []
    assert [item.id for item in result.candidates] == [mixed.id]
    assert '本职' in render_knowledge_candidates(result)
    assert not select([mixed], {mixed.fingerprint}).candidates


def test_budget_deferred_is_discoverable_without_expanding_default():
    result=select([block(i) for i in range(9)])
    assert len(result.selected)==8
    assert [item.id for item in result.candidates]==['MSB-KB-TEST-008']
    assert '预算' in render_knowledge_candidates(result)
    assert '核对人物动机。' not in render_knowledge_candidates(result)


def test_budget_does_not_restore_conflicts_or_tombstones():
    result=select([block(i) for i in range(9)]+[block(10,conflicts_with=('test-11',)),block(11)],{'hash-8'})
    assert not ({'MSB-KB-TEST-008','MSB-KB-TEST-010','MSB-KB-TEST-011'} & {b.id for b in result.selected+result.candidates})
    assert result.blocking_conflicts


def test_asset_conditions_keep_required_cross_skill_and_shared_contracts():
    documents=cli._local_standard_documents(cli.ROOT/'skills/asset-image-assistant', {'asset_method_scopes':{'object':True}})
    names={path.name for path in documents}
    assert 'project-web-mode.md' not in names and 'library-mode.md' not in names
    assert '02-character-master-guide.md' not in names
    assert '03-scene-master-guide.md' not in names
    assert '04-prop-product-master-guide.md' in names
    assert 'image-continuity-hard-rules.md' in names
    assert 'prompt-language-hard-rules.md' in names
    assert 'knowledge-access.md' in names
    unknown={p.name for p in cli._local_standard_documents(cli.ROOT/'skills/asset-image-assistant', {})}
    assert '02-character-master-guide.md' in unknown


@pytest.mark.parametrize('scopes,human,body', [
    ({}, True, True),
    ({'body': False}, True, True),
    ({'character': True}, True, True),
    ({'character': True, 'body': False}, True, False),
    ({'body': True}, False, True),
    ({'character': True, 'body': True}, True, True),
    ({'object': True, 'body': False}, False, False),
])
def test_asset_anatomy_routes_keep_legacy_and_shared_contracts(scopes, human, body):
    names = {path.name for path in cli._local_standard_documents(
        cli.ROOT / 'skills/asset-image-assistant', {'asset_method_scopes': scopes})}
    assert ('02-character-master-guide.md' in names) is human
    assert ('02a-body-character-master-guide.md' in names) is body
    assert {'image-continuity-hard-rules.md', 'prompt-language-hard-rules.md',
            'knowledge-access.md', 'skill-layers.md'} <= names


@pytest.mark.parametrize('scopes,reference', [
    ({'body': True}, '02-character-master-guide.md'),
    ({'character': True, 'body': False}, '02a-body-character-master-guide.md'),
])
def test_asset_anatomy_cannot_reintroduce_excluded_guide(scopes, reference):
    request = {'asset_method_scopes': scopes,
               'local_references': ['skills/asset-image-assistant/references/' + reference]}
    with pytest.raises(cli.StageContextError, match='asset_method_scopes 不一致'):
        cli._local_standard_documents(cli.ROOT / 'skills/asset-image-assistant', request)


@pytest.mark.parametrize('value', ['true', 1, None])
def test_asset_body_scope_requires_boolean(value):
    with pytest.raises(cli.StageContextError, match='布尔值'):
        cli._local_standard_documents(cli.ROOT / 'skills/asset-image-assistant',
                                      {'asset_method_scopes': {'body': value}})


@pytest.mark.parametrize('change',['changed','removed','added'])
def test_light_revision_reports_external_evidence_facts_only(tmp_path,change):
    current=tmp_path/'candidate.md';current.write_text('甲持蓝杯。')
    evidence=tmp_path/'evidence.md';evidence.write_text('甲持蓝杯。')
    scope={'skill':'creative-director','review_kind':'production','task_basis_identity':'identity',
           'artifacts':[fingerprint(current)],'direct_evidence':[] if change=='added' else [fingerprint(evidence)]}
    previous=tmp_path/'previous.json';previous.write_text('{"issues":[]}')
    evidence.write_text('乙持红杯。')
    paths=[] if change=='removed' else [evidence]
    delta=lightweight_revision(previous,scope,[current],'creative-director','production',[],[],[],paths)
    assert len(delta['direct_evidence_changes'][change])==1
    assert delta['evidence_impact']=='requires_reviewer_judgment'
    assert delta['changed_artifacts']==[]
    assert 'verdict' not in delta and 'full_review_required' not in delta


def test_formal_revision_reports_evidence_without_losing_identity(tmp_path):
    old=tmp_path/'old.md';old.write_text('甲持蓝杯。');current=tmp_path/'current.md';current.write_text('甲持蓝杯。')
    evidence=tmp_path/'evidence.md';evidence.write_text('来源A')
    scope={'skill':'creative-director','review_kind':'production','task_basis_identity':'identity',
           'task_basis_path':'basis.json','task_basis_sha256':'basis-hash',
           'artifacts':[fingerprint(old)],'direct_evidence':[fingerprint(evidence)]}
    previous=tmp_path/'previous.json';previous.write_text(json.dumps({'reviewer_id':'fixture-reviewer','scope':scope,'issues':[]}))
    evidence.write_text('来源B')
    delta,_=revision_delta(previous,[old],[current],{'responses':[]},'creative-director',[],[],[evidence])
    assert delta['direct_evidence_changes']['changed']
    assert delta['reviewer_id']=='fixture-reviewer'
    assert delta['actual_diff'][0]['before_sha256']==delta['actual_diff'][0]['after_sha256']


def test_reviewer_metadata_is_utf8_and_light_profile_is_mode_aware():
    metadata=(cli.ROOT/'skills/stage-reviewer/agents/openai.yaml').read_text(encoding='utf-8')
    assert '阶段审核' in metadata
    profile=json.loads((cli.ROOT/'skills/stage-reviewer/review-profile.json').read_text())
    evidence=next(item['text'] for item in profile['production_artifact_checks'] if item['id']=='REVIEW-EVIDENCE')
    assert '轻' in evidence and '正式' in evidence


def asset_fixture(tmp_path, monkeypatch):
    """Copy only contract/data fixtures; imported executable code stays candidate."""
    import shutil
    original=cli.ROOT
    root=tmp_path/'contract-fixture'
    docs=cli._local_standard_documents(original/'skills/asset-image-assistant')
    docs += [original/'PROJECT_RULES.md',original/'references/workflow/knowledge-policy.json',
             original/'skills/asset-image-assistant/knowledge-profile.json',
             original/'skills/asset-image-assistant/review-profile.json']
    for source in docs:
        target=root/source.relative_to(original);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    vault=tmp_path/'vault'
    profile=json.loads((root/'skills/asset-image-assistant/knowledge-profile.json').read_text())
    for method in profile['professional_methods']:
        path=vault/method['path'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('---\ntitle: isolated fixture\nversion: fixture-v1\nstatus: enabled\nlifecycle:\n  state: current\n---\n# 方法\n只核对隔离夹具，不产生用户确认。\n')
    project=tmp_path/'project';manage=project/'00_项目管理';manage.mkdir(parents=True)
    (manage/'project_state.json').write_text(json.dumps({'current_stage':'asset-image-assistant','current_artifacts':[],
                                                        'user_confirmations':[],'accepted_issues':[]}))
    request=tmp_path/'request.json';request.write_text(json.dumps({'asset_method_scopes':{'object':True}}))
    monkeypatch.setattr(cli,'ROOT',root)
    common=['--project',str(project),'--skill','asset-image-assistant']
    actor=['--actor-id','fixture-actor','--task-id','fixture-task']
    prepare=cli.parser().parse_args(['prepare',*common,*actor,'--objective','isolated prop check','--request-json',str(request),'--vault',str(vault)])
    cli.prepare(prepare)
    context=project/cli.CONTEXT_DIR
    basis=json.loads((context/'task_basis.json').read_text())
    (context/'method_reading_packet.md').read_text()
    methods=[arg for item in basis['reading_manifest']['methods'] for arg in ['--method',item['id']]]
    cli.record_method_reading(cli.parser().parse_args(['record-method-reading',*common,*actor,*methods]))
    gate=cli.parser().parse_args(['ensure-prepared',*common,*actor])
    return root,project,prepare,gate


@pytest.mark.parametrize('human', [False, True])
def test_asset_body_prepare_freezes_only_applicable_methods_and_requires_reading(tmp_path, monkeypatch, human):
    root, project, prepare, gate = asset_fixture(tmp_path, monkeypatch)
    state_path = project / '00_项目管理/project_state.json'
    original_state = state_path.read_bytes()
    scopes = {'body': True, **({'character': True} if human else {})}
    Path(prepare.request_json).write_text(json.dumps({'asset_method_scopes': scopes}))
    if not human:
        # A body-only task must not even require the human method to exist.
        (Path(prepare.vault) / 'projects/Master-Storyboarding/应用知识/图片资产/人物资产方法.md').unlink()
    cli.prepare(prepare)
    context = project / cli.CONTEXT_DIR
    basis = json.loads((context / 'task_basis.json').read_text())
    assert basis['task_request']['asset_method_scopes'] == scopes
    methods = {item['id'] for item in basis['reading_manifest']['methods']}
    expected = {'IMAGE-CONTINUITY-METHOD'}
    if human:
        expected.add('ASSET-CHARACTER-METHOD')
    assert methods == expected
    names = {Path(item['path']).name for item in basis['standards']['local_documents']}
    assert '02a-body-character-master-guide.md' in names
    assert ('02-character-master-guide.md' in names) is human
    packet = (context / 'method_reading_packet.md').read_text()
    assert ('## ASSET-CHARACTER-METHOD' in packet) is human
    # The previous object-only reading receipt cannot satisfy this new scope.
    assert cli.ensure_prepared(gate)['ok'] is False
    arguments = ['record-method-reading', '--project', str(project), '--skill', 'asset-image-assistant',
                 '--actor-id', 'fixture-actor', '--task-id', 'fixture-task']
    for method in sorted(methods):
        arguments += ['--method', method]
    cli.record_method_reading(cli.parser().parse_args(arguments))
    assert cli.ensure_prepared(gate)['ok'] is True
    request = Path(prepare.request_json)
    original_request = request.read_bytes()
    request.write_text(json.dumps({'asset_method_scopes': {'character': True, 'body': False}}))
    with pytest.raises(cli.StageContextError, match='开工决定或阶段请求已改变'):
        cli.ensure_prepared(gate)
    request.write_bytes(original_request)
    if human:
        required_method = Path(prepare.vault) / 'projects/Master-Storyboarding/应用知识/图片资产/人物资产方法.md'
        method_bytes = required_method.read_bytes()
        required_method.unlink()
        with pytest.raises(cli.StageContextError, match='专业方法文件缺失：ASSET-CHARACTER-METHOD'):
            cli.prepare(prepare)
        required_method.write_bytes(method_bytes)
    if not human:
        unused = root / 'skills/asset-image-assistant/references/02-character-master-guide.md'
        unused.write_text(unused.read_text() + '\nUnused human guide fixture change.\n')
        assert cli.ensure_prepared(gate)['ok'] is True
    required = root / 'skills/asset-image-assistant/references/02a-body-character-master-guide.md'
    required.write_text(required.read_text() + '\nRequired body guide fixture change.\n')
    with pytest.raises(cli.StageContextError, match='已改变'):
        cli.ensure_prepared(gate)
    assert state_path.read_bytes() == original_state


def test_real_asset_prepare_ignores_unused_guide_but_detects_required_changes(tmp_path,monkeypatch):
    root,project,prepare,gate=asset_fixture(tmp_path,monkeypatch)
    assert cli.ensure_prepared(gate)['ok']
    candidate=project/'candidate.md';candidate.write_text('# 道具候选\n隔离夹具，无图像或确认。')
    review=cli.parser().parse_args(['review-pack','--project',str(project),'--skill','asset-image-assistant','--artifact',str(candidate)])
    scope_path=Path(cli.review_pack(review)['review_scope_path']);identity=json.loads(scope_path.read_text())['task_basis_identity']
    unused=root/'skills/asset-image-assistant/references/02-character-master-guide.md'
    unused.write_text(unused.read_text()+'\n未用人物章节的隔离修改。\n')
    assert cli.ensure_prepared(gate)['ok']
    cli.prepare(prepare)
    assert cli.ensure_prepared(gate)['ok']
    assert json.loads(Path(cli.review_pack(review)['review_scope_path']).read_text())['task_basis_identity']==identity
    for relative in ['skills/storyboard-director/references/image-continuity-hard-rules.md',
                     'skills/_shared/product-reference.md','PROJECT_RULES.md']:
        path=root/relative;original=path.read_bytes();path.write_bytes(original+b'\nfixture change\n')
        with pytest.raises(cli.StageContextError,match='已改变'):
            cli.ensure_prepared(gate)
        path.write_bytes(original)
    assert cli.ensure_prepared(gate)['ok']
    required=root/'skills/storyboard-director/references/image-continuity-hard-rules.md';required.unlink()
    with pytest.raises(cli.StageContextError,match='明确引用的本地规范缺失'):
        cli.prepare(prepare)


def test_explicit_local_reference_freezes_at_prepare_never_on_consumption(tmp_path,monkeypatch):
    root,project,prepare,gate=asset_fixture(tmp_path,monkeypatch)
    extra=root/'skills/asset-image-assistant/references/extra-contract.md';extra.write_text('# 本轮新增合同\n隔离。')
    gate.required_reference=[str(extra.relative_to(root))]
    before=(project/cli.CONTEXT_DIR/'task_basis.json').read_bytes()
    with pytest.raises(cli.StageContextError,match='未在本轮冻结'):
        cli.ensure_prepared(gate)
    assert (project/cli.CONTEXT_DIR/'task_basis.json').read_bytes()==before
    request=Path(prepare.request_json);data=json.loads(request.read_text());data['local_references']=gate.required_reference
    request.write_text(json.dumps(data));cli.prepare(prepare)
    # Fresh preparation needs a matching reading declaration; the reference
    # itself is already frozen and a change cannot be laundered into it.
    extra.write_text('# 已改变\n隔离。')
    with pytest.raises(cli.StageContextError,match='已变更'):
        cli.ensure_prepared(gate)
    data['local_references']=['../../outside.md'];request.write_text(json.dumps(data))
    with pytest.raises(cli.StageContextError,match='越出仓库'):
        cli.prepare(prepare)


@pytest.mark.parametrize('relative',[
    'skills/asset-image-assistant/references/04-prop-product-master-guide.md',
    'schemas/visual-asset-manifest.schema.json',
])
def test_reprepare_cannot_drop_a_missing_required_dependency(tmp_path,monkeypatch,relative):
    root,project,prepare,gate=asset_fixture(tmp_path,monkeypatch)
    assert cli.ensure_prepared(gate)['ok']
    (root/relative).unlink()
    with pytest.raises(cli.StageContextError,match='已改变'):
        cli.ensure_prepared(gate)
    with pytest.raises(cli.StageContextError,match='明确引用的本地规范缺失'):
        cli.prepare(prepare)


def test_missing_unselected_asset_guide_is_not_a_new_requirement(tmp_path,monkeypatch):
    root,project,prepare,gate=asset_fixture(tmp_path,monkeypatch)
    (root/'skills/asset-image-assistant/references/02-character-master-guide.md').unlink()
    cli.prepare(prepare)
    assert cli.ensure_prepared(gate)['ok']


@pytest.mark.parametrize('confirmed_is_applicable',[True,False])
def test_real_reading_cannot_bypass_budget_delayed_confirmed_constraint(tmp_path,monkeypatch,confirmed_is_applicable):
    root,project,prepare,gate=asset_fixture(tmp_path,monkeypatch)
    # Use the existing asset fixture entry and object scope; only the dynamic
    # knowledge is isolated. No production confirmation is manufactured.
    profile_path=root/'skills/asset-image-assistant/knowledge-profile.json'
    profile=json.loads(profile_path.read_text());profile['topics']=['narrative'];profile['forbidden_knowledge_terms']=['机位']
    profile_path.write_text(json.dumps(profile,ensure_ascii=False))
    rows=[]
    blocks=[block(i,priority=100,stages=('asset-image-assistant',)) for i in range(8)]
    blocks += [block(8,status='confirmed',stages=('asset-image-assistant',),
                     triggers=() if confirmed_is_applicable else ('not-requested-condition',)),
               block(9,body='核对物件身份；机位留导演。',stages=('asset-image-assistant',),conflicts_with=('test-8',))]
    for item in blocks:
        meta={key:getattr(item,key) for key in ['id','canonical_key','kind','status','authority','projects','stages',
              'topics','triggers','conflicts_with','priority','sources','applies_when']}
        rows.append('<!-- msb-knowledge\n'+json.dumps(meta,ensure_ascii=False)+'\n-->\n'+item.body+'\n^'+item.id.lower()+'\n<!-- /msb-knowledge -->\n')
    path=Path(prepare.vault)/'wiki/concepts/confirmed-budget-fixture.md';path.parent.mkdir(parents=True,exist_ok=True);path.write_text('\n'.join(rows))
    cli.prepare(prepare)
    basis=json.loads((project/cli.CONTEXT_DIR/'task_basis.json').read_text())
    manifest=basis['reading_manifest'];assert len(manifest['selected'])==8
    (project/cli.CONTEXT_DIR/'method_reading_packet.md').read_text();path.read_text()
    methods=[arg for item in manifest['methods'] for arg in ['--method',item['id']]]
    receipt=cli.parser().parse_args(['record-method-reading','--project',str(project),'--skill','asset-image-assistant',
             '--actor-id','fixture-actor','--task-id','fixture-task',*methods,'--vault',prepare.vault,
             '--candidate-rule','MSB-KB-TEST-009','--candidate-scope','MSB-KB-TEST-009=只核对本职物件身份，不采用导演实现'])
    if confirmed_is_applicable:
        with pytest.raises(cli.StageContextError,match='冲突'):
            cli.record_method_reading(receipt)
    else:
        assert cli.record_method_reading(receipt)['ok']
        assert cli.ensure_prepared(gate)['ok']
