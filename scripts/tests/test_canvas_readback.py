import copy
import pytest
from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core.io import write_json
from scripts.core.project_kickoff import REQUEST_PATH


def sample():
    frozen = {'target_url': 'https://fixture.invalid/canvas/one', 'drafts': [{
        'node_id': 'draft-13', 'prompt': 'body with native chip labels',
        'parameters': {'duration': 16, 'aspect': '9:16', 'model': 'fixture'},
        'ordered_media': [{'node_id': 'image-1', 'media_id': 'm1'},
                          {'node_id': 'image-2', 'media_id': 'm2'},
                          {'node_id': 'image-1', 'media_id': 'm1'}]}]}
    observed = {**copy.deepcopy(frozen), 'phase': 'after_refresh', 'frozen_sha256': digest(frozen)}
    return frozen, observed


def test_full_fields_and_repeated_order_pass():
    frozen, observed = sample()
    assert compare(frozen, observed)['ok']


def test_partial_dom_node_identity_never_substitutes_for_media_identity():
    frozen, observed = sample()
    frozen['drafts'][0]['native_entity_type'] = 'image'
    observed['frozen_sha256'] = digest(frozen)
    # Shape emitted by readRichEditor. Even if all other fields were obtained,
    # public node/type attributes do not prove the actual media version/bytes.
    observed['drafts'][0]['native_mentions'] = [
        {'node_id': ref['node_id'], 'entity_type': 'image', 'native_noneditable_entity': True}
        for ref in frozen['drafts'][0]['ordered_media']]
    result = compare(frozen, observed)
    assert not result['ok']
    assert result['native_reference_checks']['draft-13'] == 'failed'


@pytest.mark.parametrize('field', ['prompt', 'parameters', 'ordered_media', 'missing_fields', 'node_ids'])
def test_graph_equivalent_editor_mutations_fail(field):
    frozen, observed = sample()
    row = observed['drafts'][0]
    if field == 'prompt': row['prompt'] += 'wrong'
    elif field == 'parameters': row['parameters']['duration'] = 99
    elif field == 'ordered_media': row['ordered_media'][0]['media_id'] = 'wrong-media-same-label'
    elif field == 'missing_fields': row.pop('prompt')
    else: row['node_id'] = 'another-draft'
    assert not compare(frozen, observed)['ok']


def test_order_is_not_deduplicated_to_edges():
    frozen, observed = sample()
    observed['drafts'][0]['ordered_media'][0:2] = reversed(observed['drafts'][0]['ordered_media'][0:2])
    assert not compare(frozen, observed)['ok']


@pytest.mark.parametrize('field,value', [('target_url', 'https://fixture.invalid/canvas/other'),
    ('phase', 'before_refresh'), ('frozen_sha256', 'stale')])
def test_wrong_evidence_identity_rejected(field, value):
    frozen, observed = sample(); observed[field] = value
    with pytest.raises(ValueError): compare(frozen, observed)


def test_legacy_comparison_honors_an_explicit_project_id():
    frozen, observed = sample()
    frozen['target_project_id'] = 'exact-project'
    observed['frozen_sha256'] = digest(frozen)
    with pytest.raises(ValueError, match='readback project differs'):
        compare(frozen, observed)
    observed['target_project_id'] = 'exact-project'
    assert compare(frozen, observed)['submission_checks'] == 'not_checked'


def test_self_reported_refresh_without_native_readback_does_not_pass():
    frozen, observed = sample()
    frozen['drafts'][0]['native_entity_type'] = 'fixture-chip'
    observed['frozen_sha256'] = digest(frozen)
    # A phase label and matching write payload are claims, not native editor evidence.
    assert not compare(frozen, observed)['ok']
    observed['drafts'][0]['native_mentions'] = [{'entity_type': 'fixture-chip',
        'native_noneditable_entity': True, 'node_id': 'image-1', 'media_id': 'm1'},
        {'entity_type': 'fixture-chip', 'native_noneditable_entity': True,
         'node_id': 'image-2', 'media_id': 'm2'},
        {'entity_type': 'fixture-chip', 'native_noneditable_entity': True,
         'node_id': 'image-1', 'media_id': 'm1'}]
    report = compare(frozen, observed)
    assert report['ok'] and report['native_reference_checks']['draft-13'] == 'passed'
    assert 'cannot establish remote provenance' in report['limitations']


def test_duplicate_drafts_rejected():
    frozen, observed = sample(); observed['drafts'] *= 2
    with pytest.raises(ValueError): compare(frozen, observed)


def test_native_parameter_type_change_is_not_equal():
    frozen, observed = sample()
    frozen['drafts'][0]['parameters']['enabled'] = True
    observed['frozen_sha256'] = digest(frozen)
    observed['drafts'][0]['parameters']['enabled'] = 1
    assert not compare(frozen, observed)['ok']


def test_frozen_delivery_cannot_override_project_kickoff(tmp_path):
    from scripts.tests.test_delivery_preflight import bind_synthetic_quality, bind_synthetic_canvas, delivery

    frozen, observed = delivery(tmp_path)
    canvas = {"platform": "redframe", "model": "fixture", "aspect_ratio": "9:16",
              "resolution": "720p", "target_url": frozen["target_url"]}
    for row, readback in zip(frozen["drafts"], observed["drafts"]):
        row["parameters"].update(model="fixture", aspect_ratio="9:16", resolution="720p")
        readback["parameters"] = copy.deepcopy(row["parameters"])
    bind_synthetic_canvas(tmp_path, frozen, observed)
    observed["frozen_sha256"] = digest(frozen)
    write_json(tmp_path / REQUEST_PATH, {"kickoff": {
        "decision_ref": "synthetic fixture", "product": {"mode": "none"}, "canvas": canvas}})
    assert compare(frozen, observed, tmp_path)["ok"]
    frozen["target_project_id"] = "wrong-project"
    with pytest.raises(ValueError, match="交付目标与开工记录不符"):
        validate_submission(frozen, tmp_path)
    del frozen["target_project_id"]
    frozen["drafts"][0]["parameters"]["resolution"] = "480p"
    with pytest.raises(ValueError, match="模型、画幅或分辨率与开工决定不符"):
        validate_submission(frozen, tmp_path)


@pytest.mark.parametrize("kickoff_bound", [False, True])
@pytest.mark.parametrize("text_only", [False, True])
def test_formal_readback_checks_declared_project_id(tmp_path, kickoff_bound, text_only):
    from scripts.tests.test_delivery_preflight import bind_synthetic_quality, bind_synthetic_canvas, delivery

    frozen, observed = delivery(tmp_path)
    frozen["target_project_id"] = "confirmed-project"
    if text_only:
        row = frozen["drafts"][0]
        row.pop("native_entity_type")
        row.update(required_entities=[], required_reference_roles={}, ordered_media=[], reference_checks=[])
        frozen["drafts"] = [row]
        observed["drafts"] = [{**copy.deepcopy(row), "native_mentions": []}]
    if kickoff_bound:
        canvas = {"platform": "redframe", "model": "Seedance 2.5", "aspect_ratio": "9:16",
                  "resolution": "720p", "target_url": frozen["target_url"],
                  "target_project_id": frozen["target_project_id"]}
        for row, readback in zip(frozen["drafts"], observed["drafts"]):
            row["parameters"].update(model=canvas["model"], aspect_ratio=canvas["aspect_ratio"],
                                     resolution=canvas["resolution"])
            readback["parameters"] = copy.deepcopy(row["parameters"])
        write_json(tmp_path / REQUEST_PATH, {"kickoff": {
            "decision_ref": "synthetic fixture", "product": {"mode": "none"}, "canvas": canvas}})
    if kickoff_bound:
        bind_synthetic_canvas(tmp_path, frozen, observed)
    else:
        bind_synthetic_quality(tmp_path, frozen)
    observed["frozen_sha256"] = digest(frozen)
    with pytest.raises(ValueError, match="readback project differs"):
        compare(frozen, observed, tmp_path)
    observed["target_project_id"] = "other-project"
    with pytest.raises(ValueError, match="readback project differs"):
        compare(frozen, observed, tmp_path)
    observed["target_project_id"] = "confirmed-project"
    assert compare(frozen, observed, tmp_path)["ok"]
