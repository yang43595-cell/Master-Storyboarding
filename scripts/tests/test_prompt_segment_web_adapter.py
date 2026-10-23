"""Synthetic offline fixtures only; never native production receipts or media."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from scripts.core.web_content import canonical, compile_content, prompt_segment_views, WebContentError
from test_web_content_adapter import registry, PNG


def fixture(tmp_path, shape='nested'):
    media = registry(tmp_path)
    sha = hashlib.sha256(PNG).hexdigest()
    materials = [dict(reference_id='SYNTHETIC-FACE', path=str(tmp_path/'fixture/face.png'),
                      sha256=sha, responsibility='synthetic identity only; not clothing', order=1,
                      native_entity_id=None, binding_status='not_uploaded_not_bound')]
    segment = dict(segment_id='SYNTHETIC-G01', prompt_text='Synthetic full prompt.\n对白：不删字。',
                   duration_seconds=9, candidate_upload_order=materials, actual_uploaded_materials=[],
                   submit_ready=False, blockers=['synthetic candidate only'], extra_fact={'retained': True})
    source = dict(revision='R01', status='candidate', review_status='unreviewed', production_blocked=True,
                  platform=dict(model='synthetic-model', reference_mode='全参考', aspect_ratio='9:16', resolution='720P'),
                  creative_sections=[dict(scene_id='SYNTHETIC-S01', generation_segments=[segment])])
    labels = {}
    if shape == 'top':
        source['generation_segments'] = [segment]
        source['creative_sections'][0]['generation_segments'] = [segment['segment_id']]
        segment['materials'] = segment.pop('candidate_upload_order')
        material = materials[0]
        material['material_id'] = material.pop('reference_id')
        material['file'] = material.pop('path')
        material['primary_role'] = material.pop('responsibility')
        labels = {'SYNTHETIC-FACE': 'SYNTHETIC-C02-FACE'}
        segment['parameters'] = dict(model='synthetic-model', ratio='9:16', resolution='720P',
                                     duration_seconds=9, reference_mode='全参考')
    elif shape == 'upload_index':
        materials[0]['upload_index'] = materials[0].pop('order')
        source['platform']['mode'] = source['platform'].pop('reference_mode')
    row = dict(segment_id=segment['segment_id'], refreshed=True, model_visible=True,
               nodes=[dict(id='synthetic-draft-node', text='候选｜SYNTHETIC-G01｜禁止生成')],
               prompt='[synthetic candidate]\n'+segment['prompt_text'].replace('\n','\n\n')+'\n[native chips]',
               references=[dict(id='synthetic-ref-node', material_id='synthetic-native-media',
                                label=labels.get('SYNTHETIC-FACE', 'SYNTHETIC-FACE'))],
               chip_order=['synthetic-ref-node'], parameter_controls=[dict(text=t) for t in ['9:16（竖屏）','720P','9s','全参考']])
    evidence = dict(source_sha256=hashlib.sha256(canonical(source).encode()).hexdigest(),
                    native_readback=[row], input_labels=labels)
    media['SYNTHETIC-FACE@'+sha] = copy.deepcopy(media['FRAME@R01'])
    return source, media, evidence


def run(source, media, evidence, **extra):
    return compile_content(canonical(source).encode(), stage='prompts', case_id='02',
                           source_script_version=hashlib.sha256(b'synthetic-basis').hexdigest(),
                           expected_version=None, scope='qa', media_registry=media,
                           transport_evidence=evidence, **extra)


def refresh_hash(source, evidence):
    evidence['source_sha256'] = hashlib.sha256(canonical(source).encode()).hexdigest()


def segment(source):
    return source.get('generation_segments', source['creative_sections'][0]['generation_segments'])[0]


@pytest.mark.parametrize('shape', ['top', 'nested', 'upload_index'])
def test_current_shapes_real_schema_lossless_and_not_confirmed(tmp_path, shape):
    source, media, evidence = fixture(tmp_path, shape)
    before = copy.deepcopy((source, media, evidence))
    result = run(source, media, evidence)
    blocks = result['request']['content']['blocks']
    assert json.loads(next(b['text'] for b in blocks if b['id']=='canonical-source')) == source
    bound = json.loads(next(b['text'] for b in blocks if b['id']=='source-binding'))
    assert bound['source_sha256'] == evidence['source_sha256']
    assert bound['native_sha256'] == hashlib.sha256(canonical(source).encode()).hexdigest()
    first = blocks[0]
    assert first['id'] == 'SYNTHETIC-G01' and segment(source)['prompt_text'] in first['text']
    assert 'not_uploaded_not_bound' in first['text'] and 'synthetic identity only' in first['text']
    assert result['transportEvidence']['segmentCount'] == result['transportEvidence']['inputCount'] == 1
    assert result['transportEvidence']['modelSelectionVerified'] is False
    assert not result['published'] and not result['remoteReadbackPerformed']
    assert not result['request']['content']['reviewable']
    assert (source, media, evidence) == before


@pytest.mark.parametrize('change', ['missing', 'source_hash', 'segment_id', 'missing_row', 'body',
    'native_id', 'media_id', 'label', 'chips', 'duration', 'ratio', 'unrefreshed', 'draft_node',
    'ambiguous_node', 'unused_label', 'partial_inputs'])
def test_bad_native_evidence_fails_without_manufacturing_ids(tmp_path, change):
    source, media, evidence = fixture(tmp_path)
    row = evidence['native_readback'][0]
    if change == 'missing': evidence = None
    elif change == 'source_hash': evidence['source_sha256'] = '0'*64
    elif change == 'segment_id': row['segment_id'] = 'SYNTHETIC-G02'
    elif change == 'missing_row': evidence['native_readback'] = []
    elif change == 'body': row['prompt'] = 'Synthetic summary, not full body.'
    elif change == 'native_id': row['references'][0]['id'] = ''
    elif change == 'media_id': row['references'][0]['material_id'] = None
    elif change == 'label': row['references'][0]['label'] = 'other source'
    elif change == 'chips': row['chip_order'] = ['other-node']
    elif change == 'duration': row['parameter_controls'][2]['text'] = '10s'
    elif change == 'ratio': row['parameter_controls'][0]['text'] = '16:9（横屏）'
    elif change == 'unrefreshed': row['refreshed'] = False
    elif change == 'draft_node': row['nodes'] = []
    elif change == 'ambiguous_node': row['nodes'].append(dict(id='another-node', text='SYNTHETIC-G01'))
    elif change == 'unused_label': evidence['input_labels']['other-source'] = 'irrelevant'
    elif change == 'partial_inputs': row['references'] = []
    with pytest.raises(WebContentError): run(source, media, evidence)


@pytest.mark.parametrize('change', ['missing_registry', 'missing_bytes', 'source_mismatch', 'readback', 'sha256'])
def test_new_shape_keeps_existing_media_guards(tmp_path, change):
    source, media, evidence = fixture(tmp_path)
    key = 'SYNTHETIC-FACE@'+hashlib.sha256(PNG).hexdigest()
    if change == 'missing_registry': media = {}
    elif change == 'missing_bytes': Path(media[key]['localPath']).unlink()
    elif change == 'source_mismatch': (tmp_path/'fixture/face.png').write_bytes(b'synthetic wrong bytes')
    elif change == 'readback': media[key]['readback'] = {}
    elif change == 'sha256': media[key]['sha256'] = '0'*64
    with pytest.raises(WebContentError): run(source, media, evidence)


def test_candidate_cannot_be_promoted_by_transport_readback(tmp_path):
    source, media, evidence = fixture(tmp_path)
    with pytest.raises(WebContentError, match='候选|未决'):
        run(source, media, evidence, reviewable=True)


def test_duplicate_and_reordered_inputs_keep_stable_media_anchors(tmp_path):
    source, media, evidence = fixture(tmp_path)
    s = segment(source); row = evidence['native_readback'][0]
    second = copy.deepcopy(s['candidate_upload_order'][0]); second.update(reference_id='SYNTHETIC-COSTUME', order=2)
    s['candidate_upload_order'].append(second)
    row['references'].append(dict(id='synthetic-costume-node', material_id='synthetic-costume-media', label='SYNTHETIC-COSTUME'))
    row['chip_order'].append('synthetic-costume-node')
    media['SYNTHETIC-COSTUME@'+second['sha256']] = copy.deepcopy(media['FRAME@R01'])
    refresh_hash(source, evidence)
    first = run(source, media, evidence)['request']['content']['blocks'][0]['media']
    s['candidate_upload_order'].reverse(); row['references'].reverse(); row['chip_order'].reverse()
    for i, m in enumerate(s['candidate_upload_order'], 1): m['order'] = i
    refresh_hash(source, evidence)
    second_result = run(source, media, evidence)['request']['content']['blocks'][0]['media']
    assert [m['id'] for m in second_result] == list(reversed([m['id'] for m in first]))
    duplicate = copy.deepcopy(s['candidate_upload_order'][0]); duplicate['order'] = 3
    s['candidate_upload_order'].append(duplicate)
    row['references'].append(copy.deepcopy(row['references'][0])); row['chip_order'].append(row['chip_order'][0])
    refresh_hash(source, evidence)
    third = run(source, media, evidence)['request']['content']['blocks'][0]['media']
    assert len({m['id'] for m in third}) == 3


def test_stdin_accepts_explicit_transport_evidence(tmp_path):
    import subprocess, sys
    source, media, evidence = fixture(tmp_path)
    path = tmp_path/'synthetic-source.json'; path.write_text(canonical(source))
    job = dict(sourcePath=str(path), stage='prompts', caseId='03', scope='qa', mediaRegistry=media,
               sourceScriptVersion=hashlib.sha256(b'synthetic-basis').hexdigest(), expectedVersion=None,
               transportEvidence=evidence)
    result = subprocess.run([sys.executable, '-m', 'scripts.core.web_content'], input=canonical(job),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['transportEvidence']['segmentCount'] == 1 and not receipt['published']
    assert path.read_text() == canonical(source)


@pytest.mark.parametrize('change', ['mixed_sources', 'duplicate_segment', 'wrong_link', 'source_duration', 'input_order', 'unexpected_actual_inputs'])
def test_ambiguous_or_incomplete_source_is_not_guessed(tmp_path, change):
    source, media, evidence = fixture(tmp_path, 'top')
    s = segment(source)
    if change == 'mixed_sources': source['drafts'] = []
    elif change == 'duplicate_segment': source['generation_segments'].append(copy.deepcopy(s))
    elif change == 'wrong_link': source['creative_sections'][0]['generation_segments'] = ['OTHER-SEGMENT']
    elif change == 'source_duration': s['duration_seconds'] = 8
    elif change == 'input_order': s['materials'][0]['order'] = 2
    elif change == 'unexpected_actual_inputs': s['actual_uploaded_materials'] = [{'id': 'synthetic-extra'}]
    refresh_hash(source, evidence)
    with pytest.raises(WebContentError): run(source, media, evidence)


@pytest.mark.parametrize('shape', ['top', 'nested'])
@pytest.mark.parametrize('field', ['prompt', 'prompt_text', 'both'])
def test_prompt_aliases_compile_full_body_without_mutation(tmp_path, shape, field):
    source, media, evidence = fixture(tmp_path, shape)
    s = segment(source); body = s.pop('prompt_text')
    s.update({'prompt': body, 'prompt_text': body} if field == 'both' else {field: body})
    # R12 has a top-level generation_segments list and prompt, without section links.
    if shape == 'top': source.pop('creative_sections')
    refresh_hash(source, evidence)
    before = copy.deepcopy((source, media, evidence))
    views = prompt_segment_views(source, source_sha256=evidence['source_sha256'], transport_evidence=evidence)
    assert views[0]['prompt'] == body
    result = run(source, media, evidence)
    blocks = result['request']['content']['blocks']
    assert blocks[0]['text'].startswith(body + '\n\n参数：')
    assert json.loads(next(b['text'] for b in blocks if b['id'] == 'canonical-source')) == source
    assert result['published'] is False and result['request']['content']['reviewable'] is False
    assert (source, media, evidence) == before


@pytest.mark.parametrize('fields, reason', [
    ({}, '缺完整提示词正文'), ({'prompt': ''}, '缺完整提示词正文'),
    ({'prompt_text': '  '}, '缺完整提示词正文'), ({'prompt': None}, '缺完整提示词正文'),
    ({'prompt_text': 42}, '缺完整提示词正文'),
    ({'prompt': {'body': 'not a string'}}, '缺完整提示词正文'),
    ({'prompt': 'valid', 'prompt_text': None}, '缺完整提示词正文'),
    ({'prompt_text': 'valid', 'prompt': ''}, '缺完整提示词正文'),
    ({'prompt': 'one', 'prompt_text': 'two'}, '不猜权威正文'),
    ({'prompt': 'one', 'prompt_text': 'one '}, '不猜权威正文'),
])
def test_prompt_aliases_reject_invalid_or_conflicting_bodies(tmp_path, fields, reason):
    source, media, evidence = fixture(tmp_path, 'top')
    s = segment(source); s.pop('prompt_text'); s.update(fields)
    s['metadata'] = {'prompt': 'Synthetic full prompt.\n对白：不删字。'}
    refresh_hash(source, evidence)
    with pytest.raises(WebContentError, match=reason):
        prompt_segment_views(source, source_sha256=evidence['source_sha256'], transport_evidence=evidence)
    with pytest.raises(WebContentError, match=reason):
        run(source, media, evidence)
