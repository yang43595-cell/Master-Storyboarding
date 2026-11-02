"""Synthetic-only endpoint fixtures; these tests never assert a live pass."""
import copy
import hashlib
import json

import pytest

from scripts.core.endpoint_control import verify_endpoint_control


HINT = '图片引用按顺序作为首帧、尾帧'


def descriptor(root, name, data):
    (root / name).write_bytes(data)
    return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}


def seal(root, declaration, reports):
    evidence = {}
    for label in ('post_refresh', 'source_ports', 'source_downloads', 'slot_downloads', 'edges', 'scope'):
        if label == 'scope':
            if 'project_input' in reports[label]:
                reports[label]['evidence_sha256'] = {k: v['sha256'] for k, v in evidence.items()}
            else:
                reports[label]['evidence_hashes'] = {v['path']: v['sha256'] for v in evidence.values()}
        evidence[label] = descriptor(root, label + '.json', json.dumps(reports[label], ensure_ascii=False).encode())
    declaration['evidence'] = evidence


def synthetic(root):
    downloads, media = [], []
    for slot in (1, 2):
        data = f'SYNTHETIC ONLY frame bytes {slot}'.encode()
        source = descriptor(root, f'source-{slot}.bin', data)
        inputs = descriptor(root, f'input-{slot}.bin', data)
        media.append({'node_id': f'node-{slot}', 'reference_port': 'image', 'sha256': source['sha256']})
        downloads.append(dict(slot=slot, role='start_frame' if slot == 1 else 'end_frame', source=source, input=inputs))
    expected = dict(target_url='https://synthetic.invalid/canvas', target_project_id='/synthetic/workspace',
                    draft_revision='R02', draft_node_id='draft-node', source_segment_id='SYNTH-6',
                    duration=17, model='Synthetic model', aspect_ratio='9:16', resolution='480p', ordered_media=media)
    declaration = dict(kind='first_last_frame_download', scope=copy.deepcopy(expected), phase='after_refresh',
                       refresh_id='SYNTHETIC-REFRESH', downloads=downloads)
    meta = dict(url=expected['target_url'], target_project_id=expected['target_project_id'], phase='after_refresh',
                refresh_id=declaration['refresh_id'], observed_at='2026-11-01T12:00:00Z',
                observed_after_refresh='2026-11-01T12:00:00Z')
    controls = {'选择视频生成模式': '首尾帧', '选择视频生成时长': '17s', '选择视频比例和分辨率': '9:16 · 480p'}
    html = '<div class="vue-flow__node" data-id="draft-node">'
    html += ''.join(f'<button aria-label="{label}">{text}</button>' for label, text in controls.items())
    html += f'<div class="referenceHint">{HINT}</div>'
    html += '<div class="modelSelect"><div class="el-select__placeholder"><span>Synthetic model</span></div></div>'
    html += ''.join(f'<div class="referenceItem"><img src="blob:synthetic-{slot}" alt="预览引用 {slot}"></div>' for slot in (1, 2)) + '</div>'
    node = dict(id='draft-node', buttons=[dict(label=k, text=v) for k, v in controls.items()], text=HINT,
                html=html, mentions=[], reference_thumbnails=[dict(order=s, src=f'blob:synthetic-{s}') for s in (1, 2)])
    reports = {label: copy.deepcopy(meta) for label in ('scope', 'post_refresh', 'source_ports', 'source_downloads', 'slot_downloads', 'edges')}
    reports['scope'].update(project=dict(title=expected['target_project_id'], outerHTML='<input title="/synthetic/workspace">'),
                            workspace_directory=expected['target_project_id'], **{'SYNTH-6': 'draft-node'})
    reports['post_refresh']['SYNTH-6'] = node
    reports['source_ports']['sources'] = [dict(id=m['node_id'], ports=[dict(port='image', handleid='image', **{'class': 'handle source'})]) for m in media]
    reports['source_downloads']['sources'] = [dict(node_id=m['node_id'], reference_port='image', method='normal_ui_download',
                                                    path=f'/original/observed/source-{slot}', sha256=m['sha256']) for slot, m in enumerate(media, 1)]
    reports['slot_downloads']['checks'] = {'SYNTH-6': dict(bound_media=[dict(slot=slot, src=f'blob:synthetic-{slot}',
                    method='input_thumbnail_download', path=f'/original/observed/input-{slot}', sha256=m['sha256']) for slot, m in enumerate(media, 1)])}
    reports['edges']['edges'] = [dict(html=f'<g data-id="not-used"><path source="{m["node_id"]}" target="draft-node" sourceHandleId="image" targetHandleId="in"></path></g>') for m in media]
    seal(root, declaration, reports)
    return declaration, expected, reports


def test_synthetic_endpoint_constraints_only(tmp_path):
    declaration, expected, _ = synthetic(tmp_path)
    assert verify_endpoint_control(tmp_path, declaration, expected) == {
        'endpoint_constraints_verified': True, 'motion_verified': False, 'production_ready': False}


@pytest.mark.parametrize('mutation', ['mode_only', 'missing_mode', 'wrong_mode', 'missing_hint', 'prompt_hint',
    'reverse_slots', 'extra_ref', 'wrong_url', 'wrong_project', 'wrong_draft', 'wrong_phase', 'naive_time',
    'stale_time', 'cross_refresh', 'cross_refresh_time', 'slot_hash', 'source_hash', 'source_node', 'source_port',
    'target_port', 'output_port', 'missing_source_download', 'missing_slot_download', 'empty_input', 'arbitrary_local',
    'media_id', 'model_in_prompt', 'wrong_model', 'wrong_duration', 'wrong_ratio', 'wrong_resolution', 'scope_hash',
    'draft_revision', 'thumbnail_order', 'thumbnail_src', 'edge_data_id_only', 'inline_entity', 'file_hash', 'root_escape'])
def test_fail_closed_raw_and_local_evidence(tmp_path, mutation):
    declaration, expected, reports = synthetic(tmp_path)
    node = reports['post_refresh']['SYNTH-6']
    slots = reports['slot_downloads']['checks']['SYNTH-6']['bound_media']
    sources = reports['source_downloads']['sources']
    edges = reports['edges']['edges']
    if mutation == 'mode_only': declaration = {'kind': 'first_last_frame_download'}
    elif mutation == 'missing_mode': node['buttons'].pop(0)
    elif mutation == 'wrong_mode': node['buttons'][0]['text'] = '智能引用'
    elif mutation == 'missing_hint': node['text'] = ''
    elif mutation == 'prompt_hint': node['html'] = node['html'].replace('referenceHint', 'prompt')
    elif mutation == 'reverse_slots': slots.reverse()
    elif mutation == 'extra_ref': node['reference_thumbnails'].append(dict(order=3, src='blob:third'))
    elif mutation == 'wrong_url': reports['edges']['url'] = 'https://elsewhere.invalid/'
    elif mutation == 'wrong_project': reports['scope']['project']['title'] = 'other'
    elif mutation == 'wrong_draft': node['id'] = 'wrong'
    elif mutation == 'wrong_phase': reports['source_ports']['phase'] = 'before_refresh'
    elif mutation == 'naive_time': reports['source_downloads']['observed_at'] = '2026-11-01T12:00:00'
    elif mutation == 'stale_time': reports['source_downloads']['observed_at'] = '2026-11-01T11:59:00Z'
    elif mutation == 'cross_refresh': reports['edges']['refresh_id'] = 'other'
    elif mutation == 'cross_refresh_time': reports['slot_downloads']['observed_after_refresh'] = '2026-11-01T12:01:00Z'
    elif mutation == 'slot_hash': slots[0]['sha256'] = '0' * 64
    elif mutation == 'source_hash': sources[0]['sha256'] = '0' * 64
    elif mutation == 'source_node': sources[0]['node_id'] = 'other'
    elif mutation == 'source_port': sources[0]['reference_port'] = 'main'
    elif mutation == 'target_port': edges[0]['html'] = edges[0]['html'].replace('targetHandleId="in"', 'targetHandleId="image"')
    elif mutation == 'output_port': reports['source_ports']['sources'][0]['ports'][0]['class'] = 'source target'
    elif mutation == 'missing_source_download': sources.clear()
    elif mutation == 'missing_slot_download': slots.clear()
    elif mutation == 'empty_input': (tmp_path / 'input-1.bin').write_bytes(b'')
    elif mutation == 'arbitrary_local': declaration['downloads'][0]['input'] = descriptor(tmp_path, 'arbitrary.bin', b'arbitrary local file')
    elif mutation == 'media_id': declaration['downloads'][0]['media_id'] = 'image'
    elif mutation == 'model_in_prompt': node['html'] = node['html'].replace('modelSelect', 'prompt')
    elif mutation == 'wrong_model': node['html'] = node['html'].replace('Synthetic model', 'Wrong model')
    elif mutation == 'wrong_duration': node['buttons'][1]['text'] = '18s'
    elif mutation == 'wrong_ratio': node['buttons'][2]['text'] = '16:9 · 480p'
    elif mutation == 'wrong_resolution': node['buttons'][2]['text'] = '9:16 · 720p'
    elif mutation == 'draft_revision': declaration['scope']['draft_revision'] = 'R01'
    elif mutation == 'thumbnail_order': node['reference_thumbnails'][0]['order'] = 2
    elif mutation == 'thumbnail_src': node['reference_thumbnails'][0]['src'] = 'blob:wrong'
    elif mutation == 'edge_data_id_only': edges[0]['html'] = '<g data-id="node-1image-draft-nodein"></g>'
    elif mutation == 'inline_entity': node['mentions'] = [{'node_id': 'node-1', 'media_id': 'image'}]
    elif mutation == 'root_escape':
        outside = tmp_path.parent / (tmp_path.name + '-outside'); outside.write_bytes((tmp_path / 'input-1.bin').read_bytes())
        (tmp_path / 'input-1.bin').unlink(); (tmp_path / 'input-1.bin').symlink_to(outside)
    if mutation != 'mode_only': seal(tmp_path, declaration, reports)
    if mutation == 'scope_hash':
        reports['scope']['evidence_hashes']['edges.json'] = '0' * 64
        declaration['evidence']['scope'] = descriptor(tmp_path, 'scope.json', json.dumps(reports['scope']).encode())
    elif mutation == 'file_hash': (tmp_path / 'edges.json').write_text('{}')
    with pytest.raises(ValueError):
        verify_endpoint_control(tmp_path, declaration, expected)


def test_synthetic_main_port_and_offscreen_source(tmp_path):
    declaration, expected, reports = synthetic(tmp_path)
    expected['ordered_media'][1]['reference_port'] = 'main'
    declaration['scope'] = copy.deepcopy(expected)
    reports['source_downloads']['sources'][1]['reference_port'] = 'main'
    reports['source_ports']['sources'][1]['ports'][0].update(port='main', handleid='main')
    reports['edges']['edges'][1]['html'] = reports['edges']['edges'][1]['html'].replace('sourceHandleId="image"', 'sourceHandleId="main"')
    seal(tmp_path, declaration, reports)
    assert verify_endpoint_control(tmp_path, declaration, expected)['endpoint_constraints_verified']


@pytest.mark.parametrize('mutation', ['edge_source', 'edge_source_port', 'edge_target', 'extra_edge',
                                      'html_mode', 'source_method', 'slot_method', 'missing_group', 'reverse_bytes'])
def test_cross_source_and_raw_dom_negatives(tmp_path, mutation):
    declaration, expected, reports = synthetic(tmp_path)
    edge = reports['edges']['edges'][0]
    if mutation == 'edge_source': edge['html'] = edge['html'].replace('source="node-1"', 'source="other"')
    elif mutation == 'edge_source_port': edge['html'] = edge['html'].replace('sourceHandleId="image"', 'sourceHandleId="main"')
    elif mutation == 'edge_target': edge['html'] = edge['html'].replace('target="draft-node"', 'target="other"')
    elif mutation == 'extra_edge': reports['edges']['edges'].append(copy.deepcopy(edge))
    elif mutation == 'html_mode': reports['post_refresh']['SYNTH-6']['html'] = reports['post_refresh']['SYNTH-6']['html'].replace('>首尾帧<', '>智能引用<')
    elif mutation == 'source_method': reports['source_downloads']['sources'][0]['method'] = 'local_file'
    elif mutation == 'slot_method': reports['slot_downloads']['checks']['SYNTH-6']['bound_media'][0]['method'] = 'source_node_download'
    elif mutation == 'missing_group': reports['source_ports'].pop('refresh_id')
    elif mutation == 'reverse_bytes':
        rows = reports['slot_downloads']['checks']['SYNTH-6']['bound_media']
        rows[0]['sha256'], rows[1]['sha256'] = rows[1]['sha256'], rows[0]['sha256']
        declaration['downloads'][0]['input'], declaration['downloads'][1]['input'] = declaration['downloads'][1]['input'], declaration['downloads'][0]['input']
    seal(tmp_path, declaration, reports)
    with pytest.raises(ValueError):
        verify_endpoint_control(tmp_path, declaration, expected)


def native_collection(root):
    declaration, expected, reports = synthetic(root)
    for report in reports.values(): report['target_url'] = report.pop('url')
    scope = reports['scope']
    project = scope.pop('project')
    scope['project_input'] = dict(title=project['title'], html=project['outerHTML'])
    scope.pop('workspace_directory'); scope.pop('evidence_hashes')
    scope['draft_node_ids'] = {'SYNTH-6': scope.pop('SYNTH-6')}
    # This collector's text is editor text, not the complete node innerText.
    reports['post_refresh']['SYNTH-6']['text'] = 'Synthetic editor text only.'
    for source in reports['source_ports']['sources']:
        ident = source['id']
        html = f'<div class="vue-flow__handle source" data-nodeid="{ident}" data-node-port="image" data-handleid="image"></div>'
        source['ports'] = [dict(port='image', html=html)]
        source['html'] = f'<div class="vue-flow__node" data-id="{ident}">{html}</div>'
    seal(root, declaration, reports)
    return declaration, expected, reports


def test_synthetic_native_collection_shape(tmp_path):
    declaration, expected, _ = native_collection(tmp_path)
    assert verify_endpoint_control(tmp_path, declaration, expected)['endpoint_constraints_verified']


@pytest.mark.parametrize('mutation', ['url_alias', 'scope_alias', 'port_alias', 'raw_port_node', 'source_node_dom',
                                      'port_absent_from_source', 'hint_only_in_editor', 'wrong_hash_binding'])
def test_native_collection_does_not_mix_aliases_or_infer_dom(tmp_path, mutation):
    declaration, expected, reports = native_collection(tmp_path)
    source = reports['source_ports']['sources'][0]
    if mutation == 'url_alias': reports['post_refresh']['url'] = expected['target_url']
    elif mutation == 'scope_alias': reports['scope']['project'] = {'title': expected['target_project_id']}
    elif mutation == 'port_alias': source['ports'][0]['handleid'] = 'image'
    elif mutation == 'raw_port_node': source['ports'][0]['html'] = source['ports'][0]['html'].replace('data-nodeid="node-1"', 'data-nodeid="other"')
    elif mutation == 'source_node_dom': source['html'] = source['html'].replace('data-id="node-1"', 'data-id="other"')
    elif mutation == 'port_absent_from_source': source['html'] = '<div class="vue-flow__node" data-id="node-1"></div>'
    elif mutation == 'hint_only_in_editor':
        node = reports['post_refresh']['SYNTH-6'];node['text'] = HINT
        node['html'] = node['html'].replace('referenceHint', 'prompt')
    seal(tmp_path, declaration, reports)
    if mutation == 'wrong_hash_binding':
        reports['scope']['evidence_sha256']['edges'] = '0' * 64
        declaration['evidence']['scope'] = descriptor(tmp_path, 'scope.json', json.dumps(reports['scope']).encode())
    with pytest.raises(ValueError): verify_endpoint_control(tmp_path, declaration, expected)
