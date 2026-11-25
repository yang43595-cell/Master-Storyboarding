"""Validate and deliver director-free Redframe recreation draft groups."""

import json
import math
import re
from contextlib import ExitStack
from copy import deepcopy
from itertools import combinations, permutations
from pathlib import Path

from .artifacts import artifact_is_adopted, digest, load_manifest, project_path
from .asset_refs import resolve_final_images
from .depth_reference_review import check_depth_coverage, check_depth_group, product_visible_frames
from .draft_delivery import DELIVERY_RECEIPT, draft_delivery_lock, load_draft_receipt
from .io import read_json, write_json
from .media_types import MEDIA_EXTENSIONS
from .native_references import redframe_reference_labels
from .product_reference_pack import resolve_product_pack
from .project_kickoff import load_kickoff
from .project_state import load_project_state
from .recreation_segments import PLAN, PROMPTS, prompt_narrative_issue, segment_reference_keys, segment_steps
from .redframe_image_ui import _valid_face_certificate
from .redframe_workspace import NativeTag, _image_output_path, video_generation_records


_SEEDANCE_MODEL = ['volcengineArkVideo', 'doubao-seedance-2-5-260628']
_SEEDANCE_REFERENCE_MODE = ['imageReference:30', 'videoReference:10', 'audioReference:10']
_SEEDANCE_REFERENCE_MODES = [list(mode) for mode in permutations(_SEEDANCE_REFERENCE_MODE)]


def matches_product_rendition(root, ref, product_images):
    """Verify a declared product derivative against the locked image pixels."""
    from PIL import Image

    resized = 'resized_product_source_sha256' in ref
    source_key = 'resized_product_source_sha256' if resized else 'lossless_product_source_sha256'
    original = product_images.get(ref.get(source_key))
    if not original or (resized and original['role'] != 'state'):
        return False
    path = project_path(root, ref['path'])
    if not path.is_relative_to(root) or not path.is_file():
        return False
    with Image.open(original['path']) as source, Image.open(path) as rendition:
        if (source.info.get('icc_profile') != rendition.info.get('icc_profile')
                or source.getexif().get(274, 1) != rendition.getexif().get(274, 1)):
            return False
        if resized:
            if (rendition.format != 'PNG' or rendition.width >= source.width
                    or rendition.width * source.height != rendition.height * source.width):
                return False
            expected = source.convert('RGBA').resize(rendition.size, Image.Resampling.LANCZOS)
        else:
            if rendition.format != 'WEBP' or rendition.size != source.size:
                return False
            expected = source.convert('RGBA')
        return expected.tobytes() == rendition.convert('RGBA').tobytes()


def _checked_path(project, relative, sha256):
    path = (project / relative).resolve()
    if not path.is_relative_to(project) or not path.is_file() or digest(path) != sha256:
        raise ValueError(f'复刻草稿来源路径或字节不符：{relative}')
    return path


def recreation_groups(project, group_path, *, for_write=False, required_faces=None):
    """Bind exact user-selected groups to the current adopted narrative and clip plan."""
    group_path = group_path.resolve()
    if not group_path.is_relative_to(project):
        raise ValueError('分组稿须在当前项目内')
    groups = read_json(group_path)
    kickoff = load_kickoff(project)
    if kickoff['creation']['workflow'] != 'depth-guided-recreation':
        raise ValueError('当前项目未采用复刻线')
    canvas = kickoff['canvas']
    if canvas['platform'] != 'redframe' or any(groups['canvas'].get(k) != v for k, v in canvas.items()):
        raise ValueError('分组稿的画布和参数与开工决定不符')
    state = load_project_state(project)
    entries = [row for row in load_manifest(project)['artifacts']
               if row['artifact_id'] == groups['narrative_id'] and row['logical_id'] == 'narrative.package'
               and row['artifact_id'] in state['current_artifacts']]
    if len(entries) != 1 or not artifact_is_adopted(entries[0], state, project):
        raise ValueError('复刻草稿须绑定本项目已采用的编导稿')
    entry = entries[0]
    if (entry['revision'], entry['checksum']) != (groups['narrative_revision'], groups['narrative_sha256']):
        raise ValueError('编导稿修订或字节已变化')
    narrative = read_json(_checked_path(project, entry['path_or_url'], entry['checksum']))
    if Path(groups['plan_path']) != PLAN or Path(groups['source_prompt_path']) != PROMPTS:
        raise ValueError('分组稿缺少本项目复刻分段与源提示词依赖')
    plan = read_json(_checked_path(project, PLAN, groups['plan_sha256']))
    source = read_json(_checked_path(project, PROMPTS, groups['source_prompt_sha256']))
    if issue := prompt_narrative_issue(source, entry):
        raise ValueError(issue)
    if source.get('plan_sha256') != groups['plan_sha256']:
        raise ValueError('复刻源提示词的 plan_sha256 与当前分段不符')
    if any(row['status'] == 'waiting' for row in segment_steps(project, narrative, entry, plan=plan, kickoff=kickoff)):
        raise ValueError('复刻分段存在未就绪的素材或工序')
    saved = load_draft_receipt(project).get('recreation', {})
    delivery_plan = _with_delivered_supplements(project, groups, plan, saved, kickoff.get('product'))
    _validate_group_contents(project, groups, delivery_plan, narrative)
    faces = _require_appearance_outputs(project, plan, kickoff.get('product'))
    if required_faces is not None:
        required_faces.update(faces)
    already_saved = (saved.get('phase') == 'after_refresh'
                     and saved.get('group_sha256') == digest(group_path)
                     and saved.get('group_path') == group_path.relative_to(project).as_posix())
    # A new plan needs the frame review even if someone removed every depth clip.
    needs_review = for_write or not already_saved
    windows = product_visible_frames(plan, required=needs_review)
    if needs_review:
        check_depth_coverage(plan, windows)
    if windows is not None:
        for group in groups['groups']:
            check_depth_group(delivery_plan, group, windows)
    return groups, canvas


def _with_delivered_supplements(project, packet, plan, saved, product):
    """Restore only scoped product references already present in verified native slots."""
    motions = packet.get('supplemental_product_motion', {})
    stills = packet.get('supplemental_product_stills', {})
    if not motions and not stills:
        return plan
    if not product or product.get('product_usage') != 'real_use' or saved.get('phase') != 'after_refresh':
        raise ValueError('补充产品参考须登记进本版分段计划，或沿用本项目已回读的同段素材')
    baseline = read_json(_checked_path(project, saved['group_path'], saved['group_sha256']))
    if (baseline['narrative_sha256'] != packet['narrative_sha256'] or
            baseline.get('supplemental_product_motion', {}) != motions or
            baseline.get('supplemental_product_stills', {}) != stills):
        raise ValueError('新增或改版的产品参考须先登记来源与本段采用，不能套用旧回执')
    by_segment = {s: g for g in baseline['groups'] for s in g['source_segment_ids']}
    rows = {r['source_segment_id']: r for r in saved['rows']}
    resolved = deepcopy(plan)
    segments = {s['id']: s for s in resolved['segments']}
    approved = saved.get('scoped_product_still_revision', {})
    for sid in motions.keys() | stills.keys():
        segment = segments[sid]
        if segment['mode'] not in {'product', 'product-display'}:
            raise ValueError(f'{sid} 的补充参考超出产品段')
        group = by_segment[sid]
        row = rows[group['id']]
        if row['node_id'] != saved['nodes'][group['id']]:
            raise ValueError(f'{sid} 的补充产品参考回执与草稿节点不符')
        media = dict(zip(group['reference_keys'], row['ordered_media'], strict=True))
        materials = {m['reference'][1:]: m for m in group['materials']}
        extra = [*motions.get(sid, []), *stills.get(sid, {})]
        for key in extra:
            material, observed = materials[key], media[key]
            if sid not in material['uses_by_source_segment'] or observed['sha256'] != material['sha256']:
                raise ValueError(f'{sid} 的补充产品参考缺同段原件回读：{key}')
            if key in stills.get(sid, {}):
                record = stills[sid][key]
                source = {k: v for k, v in record.items() if k not in {'path', 'sha256'}}
                if (key in plan['references'] or approved.get('phase') != 'after_refresh' or
                        approved.get('segment_id') != sid or approved.get('provenance') != source or
                        not source.get('use_approval') or not source.get('image_approval') or
                        saved['assets'].get(key) != observed['node_id'] or
                        approved.get('source_card_id') != observed['node_id'] or
                        record['sha256'] != approved.get('sha256') or record['sha256'] != material['sha256'] or
                        source.get('product_id') != product['product_id']):
                    raise ValueError(f'{sid} 的产品补图缺少同图同段采用依据：{key}')
                pack = resolve_product_pack({**product, 'reference_pack_revision': source['pack_revision'],
                                             'reference_pack_sha256': source['pack_sha256']})
                if not any(r['id'] == source['product_reference_id'] and r['role'] == 'state' and
                           r['sha256'] == source['source_sha256'] == record['sha256'] and
                           r.get('user_confirmation', {}).get('decision_ref') == source['image_approval']
                           for r in pack['references']):
                    raise ValueError(f'{sid} 的产品补图与已确认锁版原件不符：{key}')
                resolved['references'][key] = {'path': record['path'], 'sha256': record['sha256'], 'role': 'appearance'}
            elif (plan['references'][key]['role'] != 'product' or material['role'] != 'product' or
                  plan['references'][key]['sha256'] != observed['sha256']):
                raise ValueError(f'{sid} 的补充动作参考不是计划内的同款实操：{key}')
    return resolved


def _validate_group_contents(project, packet, plan, narrative):
    groups = packet['groups']
    segments = {row['id']: row for row in plan['segments']}
    lines = {row['dialogue_id']: row['text'] for row in narrative['dialogues']}
    fps = plan['fps']
    if fps != packet['fps'] or [s for g in groups for s in g['source_segment_ids']] != list(segments):
        raise ValueError('分组必须按原序恰好覆盖全部源段')
    if len({g['node_name'] for g in groups}) != len(groups):
        raise ValueError('复刻草稿名称重复')
    checked = set()
    for group in groups:
        rows = [segments[ident] for ident in group['source_segment_ids']]
        expected = list(dict.fromkeys(key for row in rows for key in segment_reference_keys(row)))
        for row in rows:
            for key in [*packet.get('supplemental_product_motion', {}).get(row['id'], []),
                        *packet.get('supplemental_product_stills', {}).get(row['id'], {})]:
                if key not in expected:
                    expected.append(key)
        dialogue_ids = [line for row in rows for line in row['dialogue_ids']]
        frames = [rows[0]['frames'][0], rows[-1]['frames'][1]]
        duration = (frames[1] - frames[0]) / fps
        if (group['id'] != '+'.join(row['id'].removeprefix('SEG-') if i else row['id'] for i, row in enumerate(rows))
                or group['source_frames'] != frames or group['reference_keys'] != expected
                or group['dialogue_ids'] != dialogue_ids
                or group['generate_duration_seconds'] != math.ceil(duration) or not 4 <= duration <= 30):
            raise ValueError(f"{group['id']} 的原片范围、对白、素材顺序或生成时长不符")
        if [row['reference'] for row in group['materials']] != ['@' + key for key in expected]:
            raise ValueError(f"{group['id']} 的素材槽不符")
        last = -1
        for line in dialogue_ids:
            text = lines[line]
            index = group['prompt'].find(text, last + 1)
            if index < 0:
                raise ValueError(f"{group['id']} 未按原文和顺序保留 {line}")
            last = index
        aliases = _aliases(expected)
        if group['prompt'].count('@') != len(aliases.findall(group['prompt'])) or any(
                '@' + key not in aliases.findall(group['prompt']) for key in expected):
            raise ValueError(f"{group['id']} 的正文引用缺失或超出本组")
        for key, material in zip(expected, group['materials'], strict=True):
            ref = plan['references'][key]
            if any(material[field] != ref[field] for field in ('path', 'sha256', 'role')):
                raise ValueError(f'{key} 与原复刻计划不符')
            if key not in checked:
                _checked_path(project, ref['path'], ref['sha256'])
                checked.add(key)


def _aliases(keys):
    return re.compile('|'.join(re.escape('@' + key) for key in sorted(keys, key=len, reverse=True)))


def _require_appearance_outputs(project, plan, product):
    pack = resolve_product_pack(product) if product and product['mode'] != 'none' else None
    product_images = {row['sha256']: row for row in (pack or {}).get('references', [])
                      if row['role'] in {'packaging', 'material', 'state'}}
    appearance = dict.fromkeys(key for row in plan['segments'] for key in row['appearance'])
    bindings = {}
    missing = []
    for key in appearance:
        ref = plan['references'][key]
        if ref['sha256'] in product_images:
            continue
        if 'lossless_product_source_sha256' in ref or 'resized_product_source_sha256' in ref:
            path = _checked_path(project, ref['path'], ref['sha256'])
            if path.stat().st_size >= 8_000_000 or not matches_product_rendition(project, ref, product_images):
                raise ValueError(f'{key} 须小于8 MB，且为与锁定原图逐像素一致的 WebP 或锁定膜态图的等比缩小 PNG')
            continue
        if not ref.get('asset_id') or not ref.get('output_id'):
            missing.append(key)
            continue
        bindings[key] = f"{ref['asset_id']}@{ref['output_id']}"
    if missing:
        raise ValueError('复刻外观参考缺少正式资产输出绑定：' + '、'.join(missing))
    if len(set(bindings.values())) != len(bindings):
        raise ValueError('同一正式资产输出只能登记一个复刻参考名')
    outputs = resolve_final_images(project, list(bindings.values()))
    for key, binding in bindings.items():
        ref = plan['references'][key]
        output = outputs[binding]
        if (Path(output['path']).resolve() != (project / ref['path']).resolve()
                or output['sha256'] != ref['sha256']):
            raise ValueError(f'{key} 与已确认资产 {binding} 的文件或字节不符')

    return {key for key, binding in bindings.items() if outputs[binding].get("requires_face_certification")}


def _video_data(node):
    data = node['data']
    if node['type'] != 'remote-videoGenerationNode' or video_generation_records(data):
        raise ValueError(f"目标不是未生成的视频草稿：{node['id']}")
    return data


def _nodes(graph):
    return {row['id']: row for row in graph['nodes']}


def _label(node):
    return node['data'].get('label')


def _fit(ui):
    ui.page.get_by_role('button', name='适应视图', exact=True).click(timeout=9000)
    ui.page.wait_for_timeout(500)


def _empty_point(ui):
    _fit(ui)
    return ui.page.evaluate('''() => {
        const pane = document.querySelector('.vue-flow__pane');
        const r = pane.getBoundingClientRect();
        for (let y = r.bottom - 110; y > r.top + 130; y -= 48) {
            for (let x = r.right - 110; x > r.left + 70; x -= 53) {
                if (document.elementFromPoint(x,y) === pane) return [x,y];
            }
        }
        throw Error('画布缺少已核对空位');
    }''')


def _position(ui, positions):
    graph = ui.workspace.read_canvas()
    changed = deepcopy(graph)
    nodes = _nodes(changed)
    for ident, (x, y) in positions.items():
        nodes[ident]['position'] = {'x': x, 'y': y}
    if graph != changed:
        ui.workspace.write_canvas(graph, changed)
        _reload(ui)


def _reload(ui):
    ui.page.reload(wait_until='domcontentloaded', timeout=30000)
    ui.page.get_by_role('button', name='适应视图', exact=True).wait_for(timeout=60000)
    ui.assert_target()


def _native_tokens(data):
    tokens = {}
    for line in data.get('promptModel') or []:
        for token in line:
            if token['type'] == 'Custom':
                (identity, mark), = NativeTag(token['html']).refs
                tokens[tuple(identity)] = (mark, token)
    return tokens


def _editor_node(ui, node_id):
    ui.close_dialog()
    host = ui._draft_locator(node_id)
    host.locator('.labelText').evaluate('(element) => element.click()', timeout=9000)
    return host


def _set_duration(ui, node_id, seconds):
    _fit(ui)
    host = _editor_node(ui, node_id)
    slider = ui.page.locator('input[type="range"][aria-label="视频时长滑块"]:visible')
    if not slider.count():
        host.get_by_role('button', name='选择视频生成时长', exact=True).evaluate('(e) => e.click()')
    slider.wait_for(timeout=9000)
    value, low, high, step, first = slider.evaluate('''e => [Number(e.value),Number(e.min),Number(e.max),
        Number(e.step),Number(e.closest('.durationTrack').querySelector('.durationMarks span').textContent)]''')
    desired = low + seconds - first
    if step != 1 or not low <= desired <= high:
        raise ValueError(f'{node_id} 的实际时长轨道不支持 {seconds} 秒')
    key = 'ArrowRight' if value < desired else 'ArrowLeft'
    for _ in range(abs(desired - value)):
        slider.press(key, timeout=7000)
    if slider.get_attribute('aria-valuetext') != f'{seconds}秒':
        raise ValueError(f'{node_id} 的实际时长轨道未到达 {seconds} 秒')
    ui._wait_canvas(lambda graph: _video_data(_nodes(graph)[node_id])['duration'] == seconds or None,
                    '保存整秒时长', timeout=35)


def _upload_video(ui, node_id, source_path, slot_count):
    host = _editor_node(ui, node_id)
    with ui.page.expect_file_chooser(timeout=12000) as picker:
        host.get_by_role('button', name='添加图片或视频参考', exact=True).evaluate('(element) => element.click()')
    picker.value.set_files(str(source_path), timeout=15000)
    ui._wait_canvas(lambda graph: len(_video_data(_nodes(graph)[node_id]).get('localReferences', {}).get('in', [])) == slot_count
                    or None, '保存直传视频引用', timeout=55)


def _wire_image_slots(ui, groups, state):
    pairs = [(state['assets'][m['reference'][1:]], state['nodes'][g['id']])
             for g in groups for m in g['materials'] if _media_kind(m) == 'image']
    if not pairs:
        return
    graph = ui.workspace.read_canvas()
    def signature(edge):
        return edge['source'], edge['sourceHandle'], edge['target'], edge['targetHandle']
    nodes = _nodes(graph)
    source_ids = {source for source, _ in pairs}
    first = next((edge for edge in graph['edges'] if edge['sourceHandle'] == 'image'
                  and edge['targetHandle'] == 'in' and edge['source'] in source_ids
                  and nodes.get(edge['target'], {}).get('type') == 'remote-videoGenerationNode'), None)
    if first is None:
        ui.connect_reference(*pairs[0])
        graph = ui.workspace.read_canvas()
        first, = (edge for edge in graph['edges'] if signature(edge) == (pairs[0][0], 'image', pairs[0][1], 'in'))
    for ids, port in (({s for s, _ in pairs}, 'image'), ({t for _, t in pairs}, 'in')):
        for ident in ids:
            if ui.page.locator(f'[data-nodeid="{ident}"][data-handleid="{port}"]').count() != 1:
                raise ValueError(f'未观察到唯一原生图片连线端口：{ident}/{port}')
    out = deepcopy(graph)
    known = {signature(edge) for edge in graph['edges']}
    style = {key: deepcopy(value) for key, value in first.items()
             if key in ('type', 'updatable', 'selectable', 'focusable', 'data', 'label',
                        'interactionWidth', 'animated')}
    for source, target in pairs:
        if (source, 'image', target, 'in') not in known:
            out['edges'].append({**style, 'id': f'vueflow__edge-{source}image-{target}in',
                                 'source': source, 'sourceHandle': 'image', 'target': target, 'targetHandle': 'in'})
    if out != graph:
        ui.workspace.write_canvas(graph, out)
        _reload(ui)
    _actual = [signature(edge) for edge in ui.workspace.read_canvas()['edges']]
    if any(_actual.count((source, 'image', target, 'in')) != 1 for source, target in pairs):
        raise ValueError('刷新后缺少准确的图片输入连线')


def _insert_references(ui, node_id, labels):
    _fit(ui)
    host = _editor_node(ui, node_id)
    editor = host.get_by_role('textbox', name='生成提示词', exact=True)
    for label in labels:
        editor.evaluate('''e=>{e.focus();const span=[...e.querySelectorAll('[data-set-richtype="richWrite"]')].at(-1);
            if(!span)throw Error('missing text span');const r=document.createRange();r.selectNodeContents(span);
            r.collapse(false);const s=window.getSelection();s.removeAllRanges();s.addRange(r)}''')
        host.get_by_role('button', name='引用参考素材', exact=True).evaluate('(element) => element.click()')
        option = ui.page.get_by_text(label, exact=True)
        option.wait_for(state='visible', timeout=10000)
        option.evaluate('(element) => element.click()')
        ui.page.wait_for_timeout(130)


def _serialize(group, identities, tokens):
    names = group['reference_keys']
    pattern = _aliases(names)
    lookup = {'@' + key: tokens[ident] for key, ident in zip(names, identities, strict=True)}
    rich = []
    for line in group['prompt'].split('\n'):
        parts, pos = [], 0
        for match in pattern.finditer(line):
            if pos < match.start():
                parts.append({'type': 'Write', 'text': line[pos:match.start()]})
            parts.append(deepcopy(lookup[match[0]][1]))
            pos = match.end()
        if pos < len(line) or not parts:
            parts.append({'type': 'Write', 'text': line[pos:]})
        rich.append(parts)
    return pattern.sub(lambda m: lookup[m[0]][0], group['prompt']), rich


def _media_kind(material):
    """A still stays an image whatever pack role it carries on the plan."""
    suffix = Path(material['path']).suffix.lower()
    if suffix in MEDIA_EXTENSIONS['image']:
        return 'image'
    if suffix in MEDIA_EXTENSIONS['video']:
        return 'video'
    raise ValueError(f"复刻参考须为图像或视频：{material['reference']}")


def _read_images(ui, image_nodes):
    return {row['node_id']: row['sha256'] for row in ui.workspace.digest_image_outputs(image_nodes)}


def _collect(group, node_id, graph, image_ids, slots):
    nodes = _nodes(graph)
    data = _video_data(nodes[node_id])
    actual_edges = [e for e in graph['edges'] if e['target'] == node_id]
    refs = iter(slots)
    identities, ordered = [], []
    tokens = _native_tokens(data)
    for material in group['materials']:
        key = material['reference'][1:]
        if _media_kind(material) == 'image':
            source = image_ids[key]
            edges = [e for e in actual_edges if e['source'] == source and e['sourceHandle'] == 'image'
                     and e['targetHandle'] == 'in']
            if len(edges) != 1:
                raise ValueError(f'{node_id} 缺唯一图片原生连线：{key}')
            identity, = (ident for ident in tokens if ident[0] == source)
        else:
            slot = next(refs, None)
            if slot is None or slot['status'] != 200 or slot['sha256'] != material['sha256']:
                raise ValueError(f'{node_id} 的视频参考回读与分组稿不符：{key}')
            identity = (node_id, slot['media_id'])
        identities.append(identity)
        ordered.append({'node_id': identity[0], 'media_id': identity[1], 'sha256': material['sha256']})
    if next(refs, None) is not None or set(identities) != set(tokens) or len(actual_edges) != (
            len(ordered) - len(slots)):
        raise ValueError(f'{node_id} 的原生素材槽或引用多于分组稿')
    return data, identities, tokens, ordered


def _verify_parameters(data, duration, canvas, *, populated=False):
    # Redframe's observed persistent identity for the selected native model.
    if (canvas['model'] != 'Seedance 2.5'
            or json.loads(data['model']) != _SEEDANCE_MODEL
            or data['videoMode'] != 'reference'
            or (populated and json.loads(data['mode']) not in _SEEDANCE_REFERENCE_MODES)
            or data['ratio'] != canvas['aspect_ratio']
            or data['resolution'].lower() != canvas['resolution'].lower()
            or data['duration'] != duration):
        raise ValueError('红帧原生模型、参考模式、比例、清晰度或时长与本组不符')


def _configure(ui, group, ident, image_ids, project, canvas):
    graph = ui.workspace.read_canvas()
    draft = _nodes(graph)[ident]
    data = _video_data(draft)
    _verify_parameters(data, data['duration'], canvas)
    duration = group['generate_duration_seconds']
    if data['duration'] != duration:
        _set_duration(ui, ident, duration)
        draft = _nodes(ui.workspace.read_canvas())[ident]
    # _wire_image_slots has already persisted and checked every required edge.
    videos = [m for m in group['materials'] if _media_kind(m) == 'video']
    slots = ui.workspace.digest_slots([draft])[ident]
    if ([m['sha256'] for m in videos[:len(slots)]] != [slot['sha256'] for slot in slots]
            or len(slots) > len(videos)):
        raise ValueError(f'{ident} 已有视频素材与目标顺序不符')
    for slot_count, material in enumerate(videos[len(slots):], len(slots) + 1):
        _upload_video(ui, ident, project / material['path'], slot_count)
    labels = [label[1:] for label in redframe_reference_labels(map(_media_kind, group['materials']))]
    data = _video_data(_nodes(ui.workspace.read_canvas())[ident])
    known = _native_tokens(data)
    direct_slots = iter(data.get('localReferences', {}).get('in', []))
    missing = []
    for label, material in zip(labels, group['materials'], strict=True):
        if _media_kind(material) == 'image':
            source_id = image_ids[material['reference'][1:]]
            exists = any(source_id == node_id for node_id, _ in known)
        else:
            exists = (ident, next(direct_slots)['sourceHandle']) in known
        if not exists:
            missing.append(label)
    if missing:
        _insert_references(ui, ident, missing)
    return ui._wait_canvas(lambda graph: len(_native_tokens(_video_data(_nodes(graph)[ident]))) == len(labels)
                           or None, '保存原生引用', timeout=35)


def _rename_new_node(ui, ident, name):
    graph = ui.workspace.read_canvas()
    updated = deepcopy(graph)
    _nodes(updated)[ident]['data']['label'] = name
    if updated != graph:
        ui.workspace.write_canvas(graph, updated)
        _reload(ui)
    if _label(_nodes(ui.workspace.read_canvas())[ident]) != name:
        raise ValueError(f'原生节点改名未保存：{ident}')


def _reuse_or_create(ui, state, field, key, name, create, save, *, expected_hash=None):
    graph = ui.workspace.read_canvas()
    nodes = _nodes(graph)
    registered = state[field]
    if key in registered:
        # A registered native ID must always be the exact original card/draft.
        ident = registered[key]
        if ident not in nodes or _label(nodes[ident]) != name:
            raise ValueError(f'已记录节点被移动、改名或删除：{name}')
        return ident
    if marker := state.get('creating'):
        if (marker['field'], marker['key']) != (field, key):
            raise ValueError(f"须先回读结果未知的原节点：{marker['name']}")
        new_ids = set(nodes) - set(marker['before_ids'])
        if not new_ids:
            raise ValueError(f'原节点创建结果未知，核实画布前不能再建：{name}')
        found = [ident for ident in new_ids if _label(nodes[ident]) == name]
        if not found and expected_hash and len(new_ids) == 1:
            ident, = new_ids
            if nodes[ident]['type'] == 'remote-imageNode' and _read_images(ui, [nodes[ident]]).get(ident) == expected_hash:
                _rename_new_node(ui, ident, name)
                found = [ident]
        if len(found) != 1:
            raise ValueError(f'有未归属的原节点，停止重复创建：{name}')
        ident, = found
        registered[key] = ident
        state.pop('creating')
        save()
        return ident
    elif any(_label(node) == name for node in nodes.values()):
        raise ValueError(f'已存在同名节点，不能擅自认领：{name}')
    state['creating'] = {'field': field, 'key': key, 'name': name, 'before_ids': sorted(nodes)}
    save()
    ident = create(*_empty_point(ui))
    node = _nodes(ui.workspace.read_canvas())[ident]
    if expected_hash:
        if _read_images(ui, [node]).get(ident) != expected_hash:
            raise ValueError(f'上传图片的原件字节不符：{ident}')
    else:
        _video_data(node)
    _rename_new_node(ui, ident, name)
    registered[key] = ident
    state.pop('creating')
    save()
    return ident


def _revision_scope(project, packet, previous):
    """Compare exact group content against the last refreshed packet."""
    if previous.get('phase') != 'after_refresh':
        raise ValueError('局部另建须有上一版已刷新回读的完整草稿')
    baseline = read_json(_checked_path(project, previous['group_path'], previous['group_sha256']))
    prior = {g['id']: g for g in baseline['groups']}
    rows = {r['source_segment_id']: r for r in previous['rows']}
    if (len(prior) != len(baseline['groups']) or set(prior) != set(rows) or
            any(previous['nodes'].get(k) != rows[k]['node_id'] for k in prior)):
        raise ValueError('上一版分组与节点回读不一致')
    changed, unchanged, replaced = [], {}, []
    for group in packet['groups']:
        ident = group['id']
        old = prior.get(ident)
        copied = old is not None and group == old
        previous_id = group.get('previous_id', ident)
        if old is not None and old.get('previous_id') == group.get('previous_id'):
            previous_id = ident
        replaced.append(previous_id)
        if copied:
            unchanged[ident] = rows[ident]
        else:
            changed.append(group)
    if len(set(g['id'] for g in packet['groups'])) != len(packet['groups']) or sorted(replaced) != sorted(prior):
        raise ValueError('局部另建必须准确覆盖上一版分组；改段号须声明 previous_id')
    return changed, unchanged, prior


def write_recreation(project, group_path, *, host_browser, append_revision=False, changed_only=False):
    """Save native drafts for one exact recreation batch. Never submit generation."""
    from ..redframe_draft import RedframeVideoDraftUI, _native_content, open_workspace

    project = project.resolve()
    required_faces = set()
    if changed_only and not append_revision:
        raise ValueError('局部另建须使用 --append-revision')
    with draft_delivery_lock(project), ExitStack() as session:
        source = {'group_path': str(group_path.resolve().relative_to(project)), 'group_sha256': digest(group_path)}
        packet, canvas = recreation_groups(project, group_path, for_write=True, required_faces=required_faces)
        if digest(group_path) != source['group_sha256']:
            raise ValueError('预检期间分组稿已变化')
        all_groups = packet['groups']
        url, project_id = canvas['target_url'], canvas['target_project_id']
        receipt = load_draft_receipt(project)
        previous = receipt.get('recreation', {})
        if pending := receipt.get('recreation_pending'):
            if (not append_revision or pending.get('changed_only', False) != changed_only or
                    any(pending.get(key) != value for key, value in source.items())):
                raise ValueError('请先按原文件和写入范围恢复未完成的复刻修订')
        if (receipt.get('target_url', url), receipt.get('target_project_id', project_id)) != (url, project_id):
            raise ValueError('画布回执指向其他项目')
        if (not pending and previous.get('phase') == 'after_refresh'
                and all(previous.get(key) == value for key, value in source.items())):
            return {'ok': True, 'draft_count': len(all_groups), 'written_draft_count': 0,
                    'receipt_path': str(project / DELIVERY_RECEIPT)}
        groups, unchanged_rows, prior = all_groups, {}, {}
        if changed_only:
            groups, unchanged_rows, prior = _revision_scope(project, packet, previous)
            if not groups:
                return {'ok': True, 'draft_count': len(all_groups), 'written_draft_count': 0,
                        'receipt_path': str(project / DELIVERY_RECEIPT)}
        prior_hashes = {m['reference'][1:]: m['sha256'] for g in prior.values() for m in g['materials']}
        prior_names = {g['node_name'] for g in prior.values()}
        materials = {m['reference'][1:]: m for group in groups for m in group['materials']}
        images = {key: m for key, m in materials.items() if _media_kind(m) == 'image'}
        reused_assets = {key for key in previous.get('assets', {}) if key in images and
                         (not changed_only or prior_hashes.get(key) == images[key]['sha256'])}
        if append_revision and previous.get('group_sha256') != source['group_sha256']:
            state = receipt.setdefault('recreation_pending', {
                **source, 'changed_only': changed_only, 'nodes': {},
                'assets': {key: node for key, node in previous.get('assets', {}).items() if key in reused_assets},
            })
        else:
            state = receipt.setdefault('recreation', {**source, 'nodes': {}, 'assets': {}})
        is_new_revision = state is receipt.get('recreation_pending')
        reuse_previous_layout = is_new_revision or state.get('append_revision', False)
        if not reuse_previous_layout:
            reused_assets = set()
        if any(state.get(key) != value for key, value in source.items()):
            raise ValueError('原画布已绑定其他分组稿；请沿原修订和节点办理返修')
        receipt.update(target_url=url, target_project_id=project_id)

        def save():
            write_json(project / DELIVERY_RECEIPT, receipt)

        plan = read_json(_checked_path(project, PLAN, packet['plan_sha256']))
        bundle = plan.get('product_pack') or {}
        pack_receipt = receipt.get('product_pack', {})
        if pack_receipt and pack_receipt.get('product_id') != bundle.get('product_id'):
            raise ValueError('原画布的产品包与本版分段不符')
        workspace = session.enter_context(open_workspace(host_browser, url, project_id))
        page = workspace.page
        page.set_viewport_size({'width': 2480, 'height': 1660})
        page.get_by_role('button', name='适应视图', exact=True).wait_for(timeout=60000)
        ui = RedframeVideoDraftUI(page, project_id, url, workspace=workspace)
        ui.close_dialog()
        existing_pack = pack_receipt.get('packaging', {})
        for key, asset in existing_pack.items():
            if key in images:
                if asset['sha256'] != images[key]['sha256']:
                    raise ValueError(f'原产品素材已换版：{key}')
                state['assets'][key] = asset['node_id']
        # Resume the pending native object first, without creating its replacement.
        order = list(images)
        if state.get('creating', {}).get('field') == 'assets':
            key = state['creating']['key']
            order.remove(key)
            order.insert(0, key)
        for key in order:
            if key in existing_pack or key in reused_assets:
                continue
            path = project / images[key]['path']
            name = '1:1参考｜' + key
            if changed_only and key in prior_hashes and prior_hashes[key] != images[key]['sha256']:
                name += '｜' + images[key]['sha256'][:12]
            ident = _reuse_or_create(ui, state, 'assets', key, name,
                                     lambda x, y: ui.upload_source(path, x, y), save,
                                     expected_hash=images[key]['sha256'])
            print('素材卡', key, ident, flush=True)
        graph = workspace.read_canvas()
        nodes = _nodes(graph)
        used_images = [nodes[state['assets'][key]] for key in images]
        image_checks = _read_images(ui, used_images)
        if any(image_checks[state['assets'][key]] != material['sha256'] for key, material in images.items()):
            raise ValueError('源素材卡的实际字节与分组稿不符')
        attempts = receipt.setdefault("face_certification_attempts", {})
        for key in sorted(required_faces.intersection(images)):
            ui.ensure_face_certification(state["assets"][key], images[key]["sha256"], attempts, save)
        new_asset_ids = [state['assets'][k] for k in images if k not in existing_pack and k not in reused_assets]
        asset_y = 270
        if changed_only and new_asset_ids:
            moving = set(new_asset_ids) | set(state['nodes'].values())
            asset_y = max((float(n['position']['y']) + float(n.get('dimensions', {}).get('height') or 420)
                           for n in nodes.values() if n['id'] not in moving), default=asset_y) + 220
        asset_positions = {ident: (720 + 300 * (i % 5), asset_y + 290 * (i // 5))
                           for i, ident in enumerate(new_asset_ids)}
        _position(ui, asset_positions)
        for group in groups:
            name, key = group['node_name'], group['id']
            if name in prior_names:
                name += '｜' + source['group_sha256'][:12]
            ident = _reuse_or_create(ui, state, 'nodes', key, name,
                                     lambda x, y: ui._new_node(x, y, menu_name='视频',
                                                              generation_kind='remote-videoGenerationNode'), save)
            print('草稿节点', key, ident, flush=True)
        base_y = 340 + 290 * math.ceil(len(asset_positions) / 5)
        if reuse_previous_layout:
            new_ids = set(state['nodes'].values())
            base_y = max((float(node['position']['y']) + float(node.get('dimensions', {}).get('height') or 420)
                          for node in workspace.read_canvas()['nodes'] if node['id'] not in new_ids),
                         default=base_y) + 220
        draft_positions = {state['nodes'][g['id']]: (720 + 370 * (i % 4), base_y + 470 * (i // 4))
                           for i, g in enumerate(groups)}
        _position(ui, draft_positions)
        _wire_image_slots(ui, groups, state)
        for group in groups:
            ident = state['nodes'][group['id']]
            _configure(ui, group, ident, state['assets'], project, canvas)
            print('已绑定原生素材', group['id'], flush=True)
        graph = workspace.read_canvas()
        staged = deepcopy(graph)
        observed = _nodes(graph)
        next_nodes = _nodes(staged)
        slots = workspace.digest_slots([observed[ident] for ident in state['nodes'].values()])
        for group in groups:
            ident = state['nodes'][group['id']]
            _, identities, tokens, _ = _collect(group, ident, graph, state['assets'], slots[ident])
            prompt, rich = _serialize(group, identities, tokens)
            next_nodes[ident]['data'].update(prompt=prompt, promptModel=rich)
        if graph != staged:
            workspace.write_canvas(graph, staged)
        _reload(ui)
        graph = workspace.read_canvas()
        observed = _nodes(graph)
        slots = workspace.digest_slots([observed[ident] for ident in state['nodes'].values()])
        checked = _read_images(ui, [observed[state['assets'][key]] for key in images])
        if any(checked[state['assets'][key]] != material['sha256'] for key, material in images.items()):
            raise ValueError('刷新后图片源卡字节变化')
        for key in sorted(required_faces.intersection(images)):
            node = observed[state['assets'][key]]
            cert = node['data'].get('faceCertification')
            if not _valid_face_certificate(cert, _image_output_path(node), images[key]['sha256']):
                raise ValueError(f'刷新后人物源卡缺本图认证：{key}')
        models = {'reference': ('Seedance 2.5', {'providerId': _SEEDANCE_MODEL[0],
                                                'modelId': _SEEDANCE_MODEL[1],
                                                'mode': _SEEDANCE_REFERENCE_MODES})}
        rows = []
        for group in groups:
            ident = state['nodes'][group['id']]
            data, _, _, ordered = _collect(group, ident, graph, state['assets'], slots[ident])
            _verify_parameters(data, group['generate_duration_seconds'], canvas, populated=True)
            if (data['prompt'], data['promptModel']) != (next_nodes[ident]['data']['prompt'],
                                                       next_nodes[ident]['data']['promptModel']):
                raise ValueError(f'刷新后正文或引用结构发生变化：{ident}')
            prompt, parameters, mentions = _native_content(ident, data, ordered,
                list(map(_media_kind, group['materials'])), models, linked=True)
            labels = redframe_reference_labels(map(_media_kind, group['materials']))
            label_by_key = dict(zip(group['reference_keys'], labels, strict=True))
            expected = _aliases(label_by_key).sub(lambda m: label_by_key[m[0][1:]], group['prompt'])
            if prompt != expected or len(mentions) < len(ordered):
                raise ValueError(f'刷新后的原生正文、引用或素材身份核对失败：{ident}')
            rows.append({'source_segment_id': group['id'], 'node_id': ident, 'parameters': parameters,
                         'ordered_media': ordered, 'native_mentions': mentions, 'prompt': prompt,
                         'position': observed[ident]['position']})
            print('刷新回读', group['id'], len(ordered), flush=True)
        _fit(ui)
        _check_layout(page, state['nodes'].values())
        if changed_only:
            new_rows = {row['source_segment_id']: row for row in rows}
            rows = [new_rows.get(group['id']) or unchanged_rows[group['id']] for group in all_groups]
            state['nodes'] = {row['source_segment_id']: row['node_id'] for row in rows}
            state['assets'] = {**previous['assets'], **state['assets']}
        state.update(phase='after_refresh', rows=rows)
        if is_new_revision:
            state['append_revision'] = True
            state['previous'] = previous
            for key in ('face_certification', 'film_png_certification_attempt', 'product_image_size_repair', 'scoped_product_still_revision'):
                if key in previous:
                    state[key] = previous[key]
            receipt['recreation'] = receipt.pop('recreation_pending')
        receipt['phase'] = 'after_refresh'
        receipt['plan_sha256'] = packet['plan_sha256']
        if bundle:
            packaging = {
                key: {'node_id': state['assets'][key], 'sha256': material['sha256']}
                for key, material in images.items() if key in bundle['packaging']
            }
            receipt['product_pack'] = {
                'product_id': bundle['product_id'], 'packaging': {**existing_pack, **packaging},
            }
        receipt['segments'] = _product_delivery_rows(packet, rows, receipt.get('product_pack', {}).get('packaging', {}))
        receipt.setdefault('video_draft_preparation', {}).update(
            status='saved_and_read_back', written_video_nodes=len(groups))
        receipt['video_draft_preparation'].pop('blocker', None)
        save()
        return {'ok': True, 'draft_count': len(rows), 'written_draft_count': len(groups),
                'receipt_path': str(project / DELIVERY_RECEIPT)}


def _check_layout(page, ids):
    boxes = page.locator('.vue-flow__node:visible').evaluate_all(
        '(nodes)=>nodes.map(el=>[el.dataset.id,el.getBoundingClientRect().toJSON()])')
    boxes = [box for ident, box in boxes if ident in ids]
    if len(boxes) != len(ids) or any(max(a['left'], b['left']) < min(a['right'], b['right']) and
                                    max(a['top'], b['top']) < min(a['bottom'], b['bottom'])
                                    for a, b in combinations(boxes, 2)):
        raise ValueError('刷新后草稿节点有缺失或重叠')


def _product_delivery_rows(packet, observed, packaging):
    """Keep the ordinary product handoff keyed to its original source segment."""
    by_id = {row['source_segment_id']: row for row in observed}
    output = {}
    for group in packet['groups']:
        actual = by_id[group['id']]
        identities = dict(zip(group['reference_keys'], actual['ordered_media'], strict=True))
        for source_id in group['source_segment_ids']:
            relevant = [m['reference'][1:] for m in group['materials']
                        if source_id in m['uses_by_source_segment'] and (m['role'] == 'product' or
                                                                        m['reference'][1:] in packaging)]
            if relevant:
                output[source_id] = {'node_id': actual['node_id'],
                                     'reference_identities': {key: identities[key] for key in relevant},
                                     'verified_product_inputs': {key: identities[key]['sha256'] for key in relevant}}
    return output
