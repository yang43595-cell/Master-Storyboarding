"""Synthetic SKU fixtures only; no assertion about any real product/version."""
import copy
import json
from pathlib import Path

import pytest

from scripts import canvas_readback
from scripts.core import artifacts, asset_refs, quality_evidence as q
from scripts.core.io import write_json
from scripts.tests.test_quality_evidence_consumers import fixture, observations


def product_fixture(root):
    frozen, manifest = fixture(root)
    row = frozen['drafts'][0]
    product = dict(product_id='SYNTHETIC-PRODUCT', sku='SYNTHETIC-SKU-A', version='TEST-V1',
        reference_role='packaging', evidence='SYNTHETIC brief; not real approval',
        source={'path':'source.json','sha256':artifacts.digest(root/'source.json')},
        coverage=list(q.PACKAGING_ASPECTS))
    row['prompt'] = '按测试商品版本还原包装。'
    ref = row['reference_checks'][0]
    ref.update(roles=['appearance'],product_reference=copy.deepcopy(product))
    row['required_reference_roles'] = {'E1':['appearance']}
    item = manifest['artifacts'][0]
    item['product_reference'] = copy.deepcopy(product)
    image_qa = item['quality_evidence']
    image_qa['uses'] = [q.image_use(ref,['S1'])]
    image_qa['packaging'] = observations(q.PACKAGING_ASPECTS)
    image_qa['packaging']['product_sha256'] = q.fingerprint(product)
    check = row['quality_evidence']['checks']['packaging_identity']
    check.update(scope='present', result='pass', items=[dict(entity_id='E1',classification='product',
        source_basis='SYNTHETIC source requirement',source_shot_ids=['S1'],
        product_reference={k:product[k] for k in ('product_id','sku','version')},
        prompt_claim='product',prompt_quote=row['prompt'])])
    bind(root,frozen,manifest)
    return frozen,manifest


def bind(root,frozen,manifest):
    for row in frozen['drafts']:
        row['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(row))
    write_json(root/artifacts.MANIFEST_PATH,manifest)


def test_correct_product_reference_and_separate_packaging_qa_are_consistency_only(tmp_path):
    frozen,_=product_fixture(tmp_path)
    report=canvas_readback.validate_submission(frozen,tmp_path)
    assert report['ok'] and report['visual_quality_verified'] is False
    assert report['packaging_checks']=='declared_scope_and_evidence_consistency_only'
    assert asset_refs.require_references(tmp_path,['IMG'],['S1'])==(True,[])


@pytest.mark.parametrize('mutation', ['unknown','missing_scope','wrong_sku','wrong_version','prompt_conflict',
    'quoted_exclusion','four_correct_refs_no_product','action_qa_only','geometry_not_applicable','source_hash'])
def test_packaging_rejects_semantic_and_coverage_gaps_despite_matching_transport(tmp_path,mutation):
    frozen,manifest=product_fixture(tmp_path);row=frozen['drafts'][0]
    check=row['quality_evidence']['checks']['packaging_identity']; ref=row['reference_checks'][0]
    if mutation=='unknown': check['items'][0]['classification']='unknown'
    elif mutation=='missing_scope': check.pop('scope')
    elif mutation in {'wrong_sku','wrong_version'}:
        key='sku' if mutation=='wrong_sku' else 'version'
        ref['product_reference'][key]='OTHER'
        check['items'][0]['product_reference'][key]='OTHER'
        manifest['artifacts'][0]['quality_evidence']['uses']=[q.image_use(ref,['S1'])]
    elif mutation=='prompt_conflict': check['items'][0]['prompt_claim']='excludes_product'
    elif mutation=='quoted_exclusion':
        row['prompt']='卫生间的细长白盒不认定为后场商品盒。'
        check['items'][0]['prompt_quote']=row['prompt']
    elif mutation=='four_correct_refs_no_product':
        ref.pop('product_reference');row['reference_checks']=[copy.deepcopy(ref) for _ in range(4)]
        row['ordered_media']*=4
    elif mutation=='action_qa_only': manifest['artifacts'][0]['quality_evidence'].pop('packaging')
    elif mutation=='geometry_not_applicable':
        manifest['artifacts'][0]['quality_evidence']['packaging']['checks']['shape_proportions']['result']='not_applicable'
    else: ref['product_reference']['source']['sha256']='0'*64
    # Fresh transport/input fingerprints cannot turn these failures into a pass.
    bind(tmp_path,frozen,manifest)
    with pytest.raises(ValueError,match='packaging|quality_evidence'):
        canvas_readback.validate_submission(frozen,tmp_path)


def test_true_ordinary_prop_without_product_reference_remains_legal(tmp_path):
    frozen,manifest=fixture(tmp_path);row=frozen['drafts'][0]
    row['prompt']='普通剧情白盒，不是广告商品；仅作收纳道具。'
    row['quality_evidence']['checks']['packaging_identity'].update(scope='present',result='pass',items=[
        dict(entity_id='E1',classification='ordinary_prop',source_basis='SYNTHETIC ordinary prop requirement',
             source_shot_ids=['S1'],prompt_claim='ordinary_prop')])
    bind(tmp_path,frozen,manifest)
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']


def test_old_action_qa_without_packaging_inventory_cannot_promote(tmp_path):
    frozen,manifest=fixture(tmp_path)
    frozen['drafts'][0]['quality_evidence']['checks'].pop('packaging_identity')
    bind(tmp_path,frozen,manifest)
    with pytest.raises(ValueError,match='packaging_identity'):
        canvas_readback.validate_submission(frozen,tmp_path)


def test_version_change_invalidates_only_affected_use_and_retries_read_only(tmp_path):
    frozen,manifest=product_fixture(tmp_path)
    before=copy.deepcopy(frozen)
    item=manifest['artifacts'][0]; ref=frozen['drafts'][0]['reference_checks'][0]
    unrelated=q.image_use({'roles':['identity'],'entity_ids':['UNRELATED']},['S2'])
    item['quality_evidence']['uses'].append(copy.deepcopy(unrelated))
    for value in (item['product_reference'],ref['product_reference'],
                  frozen['drafts'][0]['quality_evidence']['checks']['packaging_identity']['items'][0]['product_reference']):
        value['version']='TEST-V2'
    bind(tmp_path,frozen,manifest)
    for _ in range(2):
        with pytest.raises(ValueError,match='旧图审失效'):
            canvas_readback.validate_submission(frozen,tmp_path)
        assert unrelated in item['quality_evidence']['uses']
    ok,errors=asset_refs.require_references(tmp_path,['IMG'],['S1'])
    assert not ok and any('packaging' in e for e in errors)
    # Restoring the exact original source/target and its observations is retryable.
    frozen,manifest=product_fixture(tmp_path)
    assert frozen==before
    files={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']
    assert files=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_source_director_requirement_cannot_be_omitted_downgraded_or_relabelled(tmp_path):
    frozen,_=product_fixture(tmp_path);row=frozen['drafts'][0]
    item=copy.deepcopy(row['quality_evidence']['checks']['packaging_identity']['items'][0])
    payload={'scenes':[{'shots':[{'shot_id':'S1','packaging_requirements':[
        {k:item[k] for k in ('entity_id','classification','source_basis','product_reference')}]}]}]}
    requirements=q.packaging_requirements(payload)
    q.validate_packaging_scope(row,requirements)
    for mutation in ('omit','ordinary','wrong_target','missing_shots'):
        changed=copy.deepcopy(row);check=changed['quality_evidence']['checks']['packaging_identity']
        if mutation=='omit':check.update(scope='none',items=[],result='not_applicable')
        elif mutation=='ordinary':check['items'][0].update(classification='ordinary_prop',prompt_claim='ordinary_prop');check['items'][0].pop('product_reference')
        elif mutation=='missing_shots':changed.pop('source_shot_ids')
        else:requirements[0]['product_reference']['version']='OTHER'
        with pytest.raises(ValueError,match='packaging'):
            q.validate_packaging_scope(changed,requirements)


def test_reference_scope_covers_required_packaging_shots(tmp_path):
    frozen,_=product_fixture(tmp_path);row=frozen['drafts'][0]
    row['source_shot_ids']=['S1','S2']
    row['quality_evidence']['checks']['packaging_identity']['items'][0]['source_shot_ids']=['S2']
    row['reference_checks'][0]['source_shot_ids']=['S1']
    with pytest.raises(ValueError,match='未覆盖所需镜号'):q.validate_packaging_scope(row)


def test_real_preflight_binds_packaging_to_director_and_video_segment(tmp_path):
    frozen,manifest=product_fixture(tmp_path);row=frozen['drafts'][0]
    target=row['quality_evidence']['checks']['packaging_identity']['items'][0]
    director={'scenes':[{'shots':[{'shot_id':'S1','packaging_requirements':[
        {k:target[k] for k in ('entity_id','classification','source_basis','product_reference')}]},
        {'shot_id':'S2'}]}]}
    write_json(tmp_path/'director.json',director)
    write_json(tmp_path/'video.json',{'generation_segments':[{'segment_id':'V1','source_shot_ids':['S1']}]})
    state=json.loads((tmp_path/'00_项目管理/project_state.json').read_text())
    for ident,path in [('DIR','director.json'),('VID','video.json')]:
        sha=artifacts.digest(tmp_path/path)
        manifest['artifacts'].append(dict(artifact_id=ident,logical_id=ident,type='fixture-source',revision='R1',
            path_or_url=path,checksum=sha,status='current',user_approved=True,depends_on=[]))
        state['current_artifacts'].append(ident)
        state['user_confirmations'].append(dict(scope='artifact',artifact_ids=[ident],approval_ref='SYNTHETIC ONLY'))
        frozen['source_artifacts'].append(dict(artifact_id=ident,revision='R1',sha256=sha))
    row['source_segment_id']='V1'
    write_json(tmp_path/'00_项目管理/project_state.json',state)
    bind(tmp_path,frozen,manifest)
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']
    for mutation in ('omit','unknown_shot','other_valid_shot','missing_video'):
        altered=copy.deepcopy(frozen);d=altered['drafts'][0]
        d['quality_evidence']['checks']['packaging_identity'].update(scope='none',items=[],result='not_applicable')
        d['reference_checks'][0].pop('product_reference')
        if mutation=='unknown_shot':d['source_shot_ids']=['S999']
        elif mutation=='other_valid_shot':d['source_shot_ids']=['S2']
        elif mutation=='missing_video':altered['source_artifacts']=[r for r in altered['source_artifacts'] if r['artifact_id']!='VID']
        bind(tmp_path,altered,manifest)
        with pytest.raises(ValueError,match='packaging|scene_state'):
            canvas_readback.validate_submission(altered,tmp_path)


def test_director_schema_keeps_unknown_as_candidate_but_requires_exact_product_target():
    import jsonschema
    schema=json.loads((Path(__file__).parents[2]/'schemas/storyboard-package.schema.json').read_text())
    validator=jsonschema.Draft202012Validator(schema['$defs']['shot']['properties']['packaging_requirements'])
    ordinary=dict(entity_id='SYNTHETIC',classification='ordinary_prop',source_basis='fixture')
    assert not list(validator.iter_errors([ordinary]))
    assert not list(validator.iter_errors([{**ordinary,'classification':'unknown'}]))
    product={**ordinary,'classification':'product'}
    assert list(validator.iter_errors([product]))
    product['product_reference']=dict(product_id='TEST',sku='TEST-SKU',version='TEST-V1')
    assert not list(validator.iter_errors([product]))
    product['product_reference'].pop('version')
    assert list(validator.iter_errors([product]))
