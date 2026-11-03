"""Synthetic bidirectional packaging inventory checks, without source mandates."""
import copy
import json
import pytest
from scripts.core import quality_evidence as q
from scripts.canvas_readback import validate_submission
from scripts.tests.test_packaging_identity import product_fixture, bind


def ordinary(entity='OTHER', shots=None):
    return dict(entity_id=entity, classification='ordinary_prop', source_basis='Synthetic ordinary prop',
                source_shot_ids=shots or ['S1'], prompt_claim='ordinary_prop')


@pytest.mark.parametrize('mutation', ['same_entity_ordinary', 'unrelated_ordinary_only', 'wrong_product_entity',
                                      'partial_inventory_shots', 'conflicting_second_product'])
def test_active_packaging_cannot_be_relabelled_or_omitted(tmp_path, mutation):
    frozen, manifest = product_fixture(tmp_path);row=frozen['drafts'][0]
    inventory=row['quality_evidence']['checks']['packaging_identity']['items']
    if mutation == 'same_entity_ordinary': inventory[:] = [ordinary('E1')]
    elif mutation == 'unrelated_ordinary_only': inventory[:] = [ordinary()]
    elif mutation == 'wrong_product_entity': inventory[0]['entity_id']='OTHER'
    elif mutation == 'partial_inventory_shots': row['source_shot_ids']=['S1','S2']
    elif mutation == 'conflicting_second_product':
        second=copy.deepcopy(row['reference_checks'][0]);second['product_reference']['version']='OTHER'
        row['reference_checks'].append(second);row['ordered_media'].append(copy.deepcopy(row['ordered_media'][0]))
    bind(tmp_path,frozen,manifest)
    # Roundtrip the declaration and call the actual formal consumer too.
    frozen=json.loads(json.dumps(frozen))
    with pytest.raises(ValueError):q.validate_packaging_scope(frozen['drafts'][0])
    with pytest.raises(ValueError):validate_submission(frozen,tmp_path)


def test_other_entity_ordinary_prop_coexists_with_exact_product(tmp_path):
    frozen,manifest=product_fixture(tmp_path)
    frozen['drafts'][0]['quality_evidence']['checks']['packaging_identity']['items'].append(ordinary())
    bind(tmp_path,frozen,manifest)
    assert validate_submission(json.loads(json.dumps(frozen)),tmp_path)['ok']


def test_narrow_packaging_use_does_not_require_unrelated_shot_inventory(tmp_path):
    frozen,_=product_fixture(tmp_path);row=frozen['drafts'][0]
    row['source_shot_ids']=['S1','S2'];row['reference_checks'][0]['source_shot_ids']=['S1']
    row['quality_evidence']['checks']['packaging_identity']['items'].append(ordinary(shots=['S2']))
    assert len(q.validate_packaging_scope(row)) == 2


@pytest.mark.parametrize('extra', ['CHARACTER-HOLDING-PRODUCT', 'ORDINARY-BAG'])
def test_composite_image_does_not_classify_every_entity_as_product(tmp_path, extra):
    frozen,manifest=product_fixture(tmp_path);row=frozen['drafts'][0]
    ref=row['reference_checks'][0]
    ref['entity_ids'].append(extra)
    row['required_entities'].append(extra)
    row['required_reference_roles'][extra]=['appearance']
    if extra == 'ORDINARY-BAG':
        row['quality_evidence']['checks']['packaging_identity']['items'].append(ordinary(extra))
    manifest['artifacts'][0]['quality_evidence']['uses']=[q.image_use(ref,['S1'])]
    bind(tmp_path,frozen,manifest)
    assert validate_submission(json.loads(json.dumps(frozen)),tmp_path)['ok']
    # Removing the only actual product inventory cannot be hidden by the other
    # entity (person or ordinary bag) in the same otherwise unchanged image.
    row['quality_evidence']['checks']['packaging_identity']['items']=[ordinary(extra)]
    bind(tmp_path,frozen,manifest)
    with pytest.raises(ValueError, match='明确商品盘点'):
        validate_submission(json.loads(json.dumps(frozen)),tmp_path)
