import copy
import json
from urllib.parse import quote

from PIL import Image
import pytest

from scripts import redframe_draft
from scripts.canvas_readback import compare, digest
from scripts.core import artifacts
from scripts.core.io import read_json, write_json
from scripts.core.project_state import load_project_state, write_project_state
from scripts.core.redframe_workspace import RedframeWorkspace
from scripts.tests.test_workflow_repair_cli import bind_redframe_production_sources, redframe_fixture


def test_first_saved_node_waits_for_the_original_id(monkeypatch):
    monkeypatch.setattr(redframe_draft.time, 'sleep', lambda _: None)
    node = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}
    reads = iter([{'nodes': [dict(node, id='some-other-node')]}, {'nodes': [node]}])

    class Workspace:
        def read_canvas(self):
            return next(reads)

    assert redframe_draft.verify_saved(Workspace(), {'original': None}) == {'nodes': [node]}


def test_persistence_rejects_a_stale_existing_node():
    desired = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}

    class Workspace:
        def read_canvas(self):
            return {'nodes': [dict(desired, data={'prompt': 'old'})]}

    with pytest.raises(ValueError, match='原节点 ID.*original'):
        redframe_draft.verify_saved(Workspace(), {'original': desired}, timeout=0)


def test_two_drafts_cannot_reserve_the_same_default_position():
    existing = {'earlier': (0, 0, 220, 262)}
    planned = {'first': (6656, 2800, 220, 262), 'second': (6656, 2800, 220, 262)}
    with pytest.raises(ValueError, match='second 与 first'):
        redframe_draft.verify_placements(existing, planned)
    planned['second'] = (6944, 2800, 220, 262)
    redframe_draft.verify_placements(existing, planned)


def test_reusing_measured_geometry_requires_current_node_ids():
    graph = {'nodes': [{'id': 'kept', 'position': {'x': 0, 'y': 0}},
                       {'id': 'new', 'position': {'x': 250, 'y': 0}}]}
    with pytest.raises(ValueError, match='节点集合'):
        redframe_draft.measured_placements(graph, {'kept': (220, 262)}, ['kept'])


def linked_graph(prompt, face_hash):
    """W03 native field shape with synthetic IDs, text, bytes and two slots."""
    path = 'assets/person/head.png'
    certificate = dict(characterId='human', assetId='face-asset', imageDigest=face_hash, sourceUrl=path,
                       mimeType='image/png', projectName='test')
    person = dict(id='person', type='remote-characterNode', data=dict(
        outputs={'image': dict(dataType='IMAGE', value=dict(url=path, mimeType='image/png', assetId='face-asset'))},
        characterLibrary={'id': 'human'}, faceCertification=certificate,
        faceCertifications={'image': dict(certificate)}))

    def chip(owner, handle, index):
        encoded = quote(json.dumps([owner, handle]), safe='')
        return dict(type='Custom', html=f'<span class="imageReference" data-reference-id="{encoded}" '
                                       f'data-value="{{{{ref {index}}}}}">参考 {index}</span>')

    draft = dict(id='one', type='remote-videoGenerationNode', data=dict(
        outputs={}, videoMode='fixture-reference', model='["fixture","fixture"]',
        mode='"fixture-reference"', ratio='9:16', resolution='480p', duration=4, prompt=prompt,
        promptModel=[[chip('person', 'image', 1), chip('one', 'local-frame', 2)]],
        localReferences={'in': [dict(source='one', sourceHandle='local-frame', dataType='IMAGE',
                                     value=dict(url='assets/one/frame.png'))]}))
    edge = dict(type='simple-bezier', source='person', sourceHandle='image', target='one', targetHandle='in')
    return dict(toonflowCanvas=True, nodes=[person, draft], edges=[edge]), draft


def collect_linked_draft(graph, draft, face_hash, direct_hash):
    class AttachmentPage:
        def evaluate(self, _script, request):
            assert request['operation'] == 'attachments'
            assert request['payload']['slots'] == [{'path': 'assets/person/head.png', 'family': 'image'},
                                                   {'path': 'assets/one/frame.png', 'family': 'image'}]
            return {'results': [dict(status=200, error=None, sha256=value, bytes=8) if value else
                                dict(status=200, error='budget', phase='body')
                                for value in (face_hash, direct_hash)]}

    slots = RedframeWorkspace(AttachmentPage(), 'synthetic-project').digest_slots([draft], graph=graph)
    models = {'fixture-reference': ('fixture', dict(providerId='fixture', modelId='fixture',
                                                     mode=['fixture-reference']))}
    return redframe_draft.native_draft(graph, draft, slots['one'], models)


def test_mixed_character_and_direct_slot_use_formal_readback(tmp_path):
    frozen, observed = redframe_fixture(tmp_path)
    row = frozen['drafts'][0]
    row['prompt'] += '\n@图片1 人物；@图片2 对应切点。'
    row['native_entity_type'] = 'reference'
    Image.new('RGB', (8, 8), 'green').save(tmp_path / 'head.png')
    face_hash = artifacts.digest(tmp_path / 'head.png')
    manifest = artifacts.load_manifest(tmp_path)
    state = load_project_state(tmp_path)
    manifest['artifacts'].append(dict(artifact_id='PERSON', logical_id='PERSON', status='current',
                                      revision='R01', checksum=face_hash, path_or_url='head.png', user_approved=True))
    state['current_artifacts'].append('PERSON')
    state['user_confirmations'].append(dict(artifact_ids=['PERSON'], scope='artifact', approval_ref='synthetic'))
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    write_project_state(tmp_path, state)
    direct, = row['reference_checks']
    direct['canvas_binding'] = dict(mode='direct', purpose='synthetic cut', prompt_reference=True)
    direct['contains_realistic_face'] = False
    person = dict(artifact_id='PERSON', revision='R01', sha256=face_hash, role='identity', kind='image',
                  entity_ids=['ACTOR'], contains_realistic_face=True)
    row['reference_checks'] = [dict(person, canvas_binding=dict(mode='edge', source_port='image',
        target_port='in', purpose='synthetic character', prompt_reference=True)), direct]
    row['ordered_media'] = [dict(node_id='person', reference_port='image', sha256=face_hash),
                            dict(node_id='one', media_id='local-frame', sha256=direct['sha256'])]
    row['edges'] = [dict(source_node_id='person', source_port='image', target_node_id='one',
                        target_port='in', slot=1)]
    row['source_nodes'] = [dict(node_id='person', port='image', sha256=face_hash)]
    bind_redframe_production_sources(tmp_path, frozen, observed)
    path = tmp_path / 'video.json'
    video = read_json(path)
    video['generation_segments'][0]['materials'][0] = person
    write_json(path, video)
    video_sha = artifacts.digest(path)
    manifest = artifacts.load_manifest(tmp_path)
    next(item for item in manifest['artifacts'] if item['artifact_id'] == 'VIDEO')['checksum'] = video_sha
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    frozen['source_artifacts'][0]['sha256'] = video_sha
    observed['frozen_sha256'] = digest(frozen)
    raw_prompt = row['prompt'].replace('@图片1', '{{ref 1}}').replace('@图片2', '{{ref 2}}')
    graph, draft = linked_graph(raw_prompt, face_hash)
    observed['drafts'][0] = collect_linked_draft(graph, draft, face_hash, direct['sha256'])

    report = compare(frozen, observed, tmp_path, formal_delivery=True)
    assert report['ok']
    assert report['native_reference_checks']['one'] == 'passed'
    assert [c['status'] for c in report['face_certification_checks']['one']['slots']] == ['passed', 'not_applicable']
    for change in ('missing_certification', 'pending', 'not_certified', 'wrong_digest', 'reordered'):
        wrong = copy.deepcopy(observed)
        actual = wrong['drafts'][0]
        if change == 'missing_certification':
            actual.pop('face_certification')
        elif change == 'reordered':
            actual['native_mentions'].reverse()
        elif change == 'wrong_digest':
            actual['face_certification'][0]['image_digest'] = '0' * 64
        else:
            actual['face_certification'][0]['status'] = change
        assert not compare(frozen, wrong, tmp_path, formal_delivery=True)['ok'], change

    # A downloaded direct slot can replace a missing legacy mapping, while a
    # contradictory active mapping for that same native identity still blocks.
    person_entry = next(item for item in manifest['artifacts'] if item['artifact_id'] == 'PERSON')
    person_entry['upload_bindings'] = [dict(target_url=frozen['target_url'], node_id='one',
        media_id='local-frame', revision='R01', sha256=face_hash)]
    write_json(tmp_path / artifacts.MANIFEST_PATH, manifest)
    with pytest.raises(ValueError, match='conflicting upload mapping'):
        compare(frozen, observed, tmp_path, formal_delivery=True)


@pytest.mark.parametrize('change', ('wrong_port', 'wrong_type', 'unlocated_chip', 'wrong_asset',
                                   'lost_certificate', 'unknown_certificate', 'wrong_download', 'download_timeout'))
def test_character_source_fails_closed(change):
    face_hash = 'a' * 64
    graph, draft = linked_graph('{{ref 1}} {{ref 2}}', face_hash)
    person = graph['nodes'][0]
    if change == 'wrong_port':
        graph['edges'][0]['targetHandle'] = 'another-input'
    elif change == 'wrong_type':
        person['type'] = 'remote-imageNode'
    elif change == 'unlocated_chip':
        draft['data']['promptModel'][0].pop(0)
    elif change == 'wrong_asset':
        person['data']['outputs']['image']['value']['assetId'] = 'different-asset'
    elif change == 'lost_certificate':
        person['data'].pop('faceCertifications')
    elif change == 'unknown_certificate':
        person['data']['faceCertification']['status'] = 'pending'
        person['data']['faceCertifications']['image']['status'] = 'pending'
    downloaded = {'wrong_download': 'c' * 64, 'download_timeout': None}.get(change, face_hash)
    with pytest.raises(ValueError):
        collect_linked_draft(graph, draft, downloaded, 'b' * 64)
