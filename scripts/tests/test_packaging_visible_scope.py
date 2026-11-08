"""Synthetic visible-artwork scope; not real SKU approval or visual assessment."""
import copy
import json
from pathlib import Path
import pytest
from scripts.tests.test_packaging_identity import product_fixture, bind
from scripts import canvas_readback
from scripts.core import quality_evidence as q, asset_refs


def visible_fixture(root):
    frozen, manifest = product_fixture(root)
    row=frozen['drafts'][0];ref=row['reference_checks'][0];old=ref['product_reference']
    identity={'product_id':old['product_id'],'product_name':'SYNTHETIC visible name',
              'packaging_reference_revision':{'sha256':old['source']['sha256']},
              'physical_variant':{'status':'not_applicable','scope':'visible_packaging_only','basis':{
                  'source':copy.deepcopy(old['source']),'source_shot_ids':['S1'],
                  'rationale':'Synthetic front-only closed package, no batch/spec claim',
                  'visible_faces':['front'],'excluded_uses':['back_face','opened_packaging','formula_or_specification','efficacy_claims']}}}
    product={**identity,**{k:v for k,v in old.items() if k not in ('product_id','sku','version')}}
    ref['product_reference']=copy.deepcopy(product);manifest['artifacts'][0]['product_reference']=copy.deepcopy(product)
    row['quality_evidence']['checks']['packaging_identity']['items'][0]['product_reference']=copy.deepcopy(identity)
    image=manifest['artifacts'][0]['quality_evidence'];image['uses']=[q.image_use(ref,['S1'])]
    image['packaging'].update(product_sha256=q.fingerprint(product),version_scope='source_artwork_only',physical_variant_verified=False)
    bind(root,frozen,manifest)
    return frozen,manifest


def test_visible_artwork_scope_accepts_without_fabricating_physical_sku(tmp_path):
    frozen,manifest=visible_fixture(tmp_path)
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']
    assert asset_refs.require_references(tmp_path,['IMG'],['S1'])==(True,[])
    p=manifest['artifacts'][0]['product_reference']
    assert 'sku' not in p and 'version' not in p
    result=q.validate_image_evidence(tmp_path,tmp_path/'image.png',manifest['artifacts'][0]['quality_evidence'],['S1'],reference=frozen['drafts'][0]['reference_checks'][0])
    assert result['physical_variant_verified'] is False


def test_opened_interior_is_static_visible_artwork_not_opening_process(tmp_path):
    frozen, manifest = visible_fixture(tmp_path)
    row=frozen['drafts'][0]; ref=row['reference_checks'][0]
    product=ref['product_reference']; basis=product['physical_variant']['basis']
    basis.update(visible_faces=['opened_interior'],
                 rationale='Synthetic already-open tray observation, no opening motion or hidden face',
                 excluded_uses=['back_face','opening_process','formula_or_specification','efficacy_claims'])
    manifest['artifacts'][0]['product_reference']=copy.deepcopy(product)
    row['quality_evidence']['checks']['packaging_identity']['items'][0]['product_reference']={
        k:product[k] for k in ('product_id','product_name','packaging_reference_revision','physical_variant')}
    qa=manifest['artifacts'][0]['quality_evidence']; qa['uses']=[q.image_use(ref,['S1'])]
    qa['packaging']['product_sha256']=q.fingerprint(product)
    bind(tmp_path,frozen,manifest)
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']
    basis['excluded_uses'].remove('opening_process')
    with pytest.raises(ValueError): q.product_identity(product)
    basis['excluded_uses'].append('opening_process'); basis['rationale']=''
    with pytest.raises(ValueError): q.product_identity(product)


@pytest.mark.parametrize('mutation',['unknown','mixed_sku','empty_basis','missing_source','wrong_artwork','wrong_shot','back_face','open_use','claim_physical_pass','stale_use','source_bytes'])
def test_visible_scope_rejects_unsupported_use_and_false_physical_claim(tmp_path,mutation):
    frozen,manifest=visible_fixture(tmp_path);row=frozen['drafts'][0];ref=row['reference_checks'][0];p=ref['product_reference'];qa=manifest['artifacts'][0]['quality_evidence']
    if mutation=='unknown':p['physical_variant']['status']='unknown'
    elif mutation=='mixed_sku':p['sku']='DO-NOT-INVENT'
    elif mutation=='empty_basis':p['physical_variant']['basis']['rationale']=''
    elif mutation=='missing_source':p['physical_variant']['basis']['source']['path']='missing.json'
    elif mutation=='wrong_artwork':p['packaging_reference_revision']['sha256']='0'*64
    elif mutation=='wrong_shot':p['physical_variant']['basis']['source_shot_ids']=['S2']
    elif mutation=='back_face':p['physical_variant']['basis']['visible_faces'].append('back')
    elif mutation=='open_use':p['physical_variant']['basis']['excluded_uses'].remove('opened_packaging')
    elif mutation=='claim_physical_pass':qa['packaging']['physical_variant_verified']=True
    elif mutation=='stale_use':p['physical_variant']['basis']['visible_faces'].append('top')
    elif mutation=='source_bytes':(tmp_path/'source.json').write_text('{"changed":true}')
    manifest['artifacts'][0]['product_reference']=copy.deepcopy(p)
    row['quality_evidence']['checks']['packaging_identity']['items'][0]['product_reference']={k:p[k] for k in ('product_id','product_name','packaging_reference_revision','physical_variant')}
    if mutation!='stale_use':qa['uses']=[q.image_use(ref,['S1'])]
    qa['packaging']['product_sha256']=q.fingerprint(p)
    bind(tmp_path,frozen,manifest)
    with pytest.raises(ValueError):canvas_readback.validate_submission(frozen,tmp_path)


def test_specific_source_sku_cannot_be_downgraded_even_with_same_source_bytes(tmp_path):
    frozen,_=visible_fixture(tmp_path);row=frozen['drafts'][0]
    requirement={'entity_id':'E1','classification':'product','source_basis':'specific SKU required',
                 'source_shot_ids':['S1'],'product_reference':{'product_id':'SYNTHETIC-PRODUCT','sku':'SYNTHETIC-SKU-A','version':'TEST-V1'}}
    with pytest.raises(ValueError,match='来源导演'):q.validate_packaging_scope(row,[requirement])


def test_director_schema_validates_bounded_branch_and_rejects_unknown(tmp_path):
    from jsonschema import Draft202012Validator
    frozen,_=visible_fixture(tmp_path);identity=frozen['drafts'][0]['quality_evidence']['checks']['packaging_identity']['items'][0]['product_reference']
    schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/storyboard-package.schema.json').read_text())
    def find(v):
        if isinstance(v,dict):
            if 'product_reference' in v and isinstance(v['product_reference'],dict) and 'oneOf' in v['product_reference']:return v['product_reference']
            for x in v.values():
                r=find(x)
                if r:return r
        elif isinstance(v,list):
            for x in v:
                r=find(x)
                if r:return r
    validator=Draft202012Validator(find(schema));assert not list(validator.iter_errors(identity))
    identity['physical_variant']['status']='unknown';assert list(validator.iter_errors(identity))


def test_legacy_descriptive_product_name_does_not_select_bounded_branch(tmp_path):
    frozen,manifest=product_fixture(tmp_path)
    row=frozen['drafts'][0];ref=row['reference_checks'][0]
    ref['product_reference']['product_name']='Existing descriptive name'
    manifest['artifacts'][0]['product_reference']=copy.deepcopy(ref['product_reference'])
    qa=manifest['artifacts'][0]['quality_evidence']
    qa['uses']=[q.image_use(ref,['S1'])]
    qa['packaging']['product_sha256']=q.fingerprint(ref['product_reference'])
    bind(tmp_path,frozen,manifest)
    assert canvas_readback.validate_submission(frozen,tmp_path)['ok']
