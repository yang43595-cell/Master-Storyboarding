"""Shared Redframe video draft UI controls and Seedance canvas node helpers."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from itertools import combinations, permutations
import json
from pathlib import Path
import re
import time

from .media_types import MEDIA_EXTENSIONS
from .native_references import redframe_reference_labels
from .redframe_image_ui import ImageUIError, RedframeImageUI, _ident
from .redframe_workspace import NativeTag, RedframeWorkspace, video_generation_records

_SEEDANCE_MODEL = ['volcengineArkVideo', 'doubao-seedance-2-5-260628']
_SEEDANCE_REFERENCE_MODE = ['imageReference:30', 'videoReference:10', 'audioReference:10']
_SEEDANCE_REFERENCE_MODES = [list(mode) for mode in permutations(_SEEDANCE_REFERENCE_MODE)]


def _aliases(keys):
    return re.compile('|'.join(re.escape('@' + key) for key in sorted(keys, key=len, reverse=True)))


def canvas_nodes(graph):
    nodes = graph['nodes']
    registered = {node['id']: node for node in nodes}
    if len(registered) != len(nodes):
        raise ValueError('画布返回重复节点 ID')
    return registered


def verify_saved(workspace, expected_nodes, timeout=15):
    """Confirm persisted IDs (None) or exact draft contents; never retry a mutation."""
    if not expected_nodes:
        raise ValueError('保存确认须有准确草稿节点 ID')
    stop = time.monotonic() + timeout
    changed = list(expected_nodes)
    while True:
        if timeout > 0:
            budget_ms = min(workspace.read_budget, int((stop - time.monotonic()) * 1000))
            if budget_ms <= 0:
                break
            snapshot = workspace.read_canvas(budget_ms=budget_ms)
        else:
            snapshot = workspace.read_canvas()
        nodes = canvas_nodes(snapshot)
        changed = [ident for ident, expected in expected_nodes.items()
                   if ident not in nodes or (expected is not None and nodes[ident] != expected)
                   or nodes[ident].get('type') != 'remote-videoGenerationNode']
        if not changed:
            return snapshot
        remaining = stop - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1, remaining))
    raise ValueError('保存未在持久画布确证；先保留原节点 ID：' + ', '.join(changed))


def _draft_data(node, node_id):
    data = node.get('data') if node and node.get('type') == 'remote-videoGenerationNode' else None
    if not isinstance(data, dict):
        raise ValueError(f'视频草稿节点身份不符：{node_id}')
    if video_generation_records(data):
        raise ValueError(f'视频草稿节点已有生成记录：{node_id}')
    return data


def _native_content(ident, data, media, kinds, models, *, linked=False):
    if data.get('outputs') != {}:
        raise ValueError(f'{ident} 缺少草稿节点的空输出字段')
    mode = data['videoMode']
    model_name, capabilities = models[mode]
    if (json.loads(data['model']) != [capabilities['providerId'], capabilities['modelId']]
            or json.loads(data['mode']) not in capabilities['mode']):
        raise ValueError(f'{ident} 的实际模型或参考模式与本轮能力不符')
    names = dict(enumerate(redframe_reference_labels(kinds), 1))
    token_pattern = r'\{\{ref (\d+)\}\}'
    mentions = []
    slots_by_identity = {(item['node_id'], item['media_id']): slot
                         for slot, item in enumerate(media, 1)}
    if len(slots_by_identity) != len(media):
        raise ValueError(f'{ident} 的实际参考身份重复')
    native_to_source = {}
    prompt_indices = []
    for line in data['promptModel']:
        for token in line:
            if token['type'] != 'Custom':
                continue
            ((node_id, media_id), value), = NativeTag(token['html']).refs
            index = int(re.fullmatch(token_pattern, value)[1])
            slot = slots_by_identity.get((node_id, media_id))
            if (not 1 <= index <= len(media) or slot is None
                    or (not linked and slot != index)
                    or native_to_source.get(index, slot) != slot):
                raise ValueError(f'{ident} 的原生提示词引用和实际槽不符')
            native_to_source[index] = slot
            prompt_indices.append(str(index))
            mentions.append({'node_id': node_id, 'media_id': media_id, 'entity_type': 'reference',
                             'native_noneditable_entity': True})
    if (len(set(native_to_source.values())) != len(native_to_source)
            or re.findall(token_pattern, data['prompt']) != prompt_indices):
        raise ValueError(f'{ident} 的正文原生编号与实际引用实体不一致')
    prompt = re.sub(token_pattern, lambda m: names[native_to_source[int(m[1])]],
                    data['prompt']).replace('\ufeff', '')
    parameters = {'model': model_name, 'aspect_ratio': data['ratio'],
                  'resolution': data['resolution'].lower(), 'duration': data['duration'],
                  'reference_mode': mode}
    return prompt, parameters, mentions


@contextmanager
def open_workspace(host_browser, target_url, project_id):
    with host_browser(redframe=True) as context:
        page = context.new_page()
        page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
        yield RedframeWorkspace(page, project_id)


class RedframeVideoDraftUI(RedframeImageUI):
    """Native draft controls for one target workspace, with no generate action."""

    def _draft_locator(self, node_id):
        self.assert_target()
        node = canvas_nodes(self.workspace.read_canvas()).get(_ident(node_id))
        _draft_data(node, node_id)
        return self.page.locator(f'[data-id="{node_id}"]')

    _draft_created = None
    _resume_only = False

    def create_video_draft(self, x, y, label):
        if self._resume_only:
            raise ValueError('续作须使用原草稿节点，不能再新建')
        if not isinstance(label, str) or not label.strip():
            raise ValueError('请为视频草稿提供明确名称')
        node_id = self._new_node(x, y, menu_name='视频', generation_kind='remote-videoGenerationNode')
        if self._draft_created is not None:
            self._draft_created(node_id)
        locator = self._draft_locator(node_id)
        locator.locator('.labelText').dblclick(timeout=self.timeout)
        title = locator.get_by_label('节点名称', exact=True)
        title.fill(label, timeout=self.timeout)
        title.press('Enter', timeout=self.timeout)
        self._wait_canvas(lambda g: canvas_nodes(g)[node_id]['data'].get('label') == label or None,
                          'save video draft label')
        return node_id

    def upload_draft_reference(self, path, x, y, label):
        if not isinstance(label, str) or not label.strip():
            raise ValueError('请为参考素材卡提供明确名称')
        checksum = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        try:
            source_id = self.upload_source(path, x, y)
        except ImageUIError as exc:
            if not exc.mutation_unknown or not hasattr(exc, 'previous_node_ids'):
                raise
            previous = set(exc.previous_node_ids)

            def recover(graph):
                matches = [node for node in graph['nodes'] if node['id'] not in previous
                           and node['type'] == 'remote-imageNode']
                if len(matches) > 1:
                    raise ValueError('上传后发现多个新素材卡，请核对原画布')
                return matches[0]['id'] if matches else None

            source_id = self._wait_canvas(recover, 'recover original draft reference', timeout=30)
        source = canvas_nodes(self.workspace.read_canvas())[source_id]
        actual, = self.workspace.digest_image_outputs([source])
        if actual.get('sha256') != checksum:
            raise ValueError(f'素材原件回读与本次上传不符：{source_id}')
        self.rename(source_id, label)
        return source_id

    def connect_reference(self, source_id, node_id, *, source_port='image'):
        def validate_target(graph, target_id):
            _draft_data(canvas_nodes(graph).get(target_id), target_id)

        self.close_dialog()
        self._click(self.page.get_by_role('button', name='适应视图', exact=True), 'fit video reference ports')
        self._connect(source_id, node_id, 'remote-videoGenerationNode', source_port=source_port,
                      save_timeout=35, validate_target=validate_target)

    def drag_duration(self, node_id, seconds):
        if type(seconds) is not int:
            raise ValueError('时长须为整数秒')
        self.close_dialog()
        locator = self._draft_locator(node_id)
        self._click(locator.locator('.labelText'), 'focus video draft', node_id=node_id)
        duration_control = locator.get_by_role('button', name='选择视频生成时长', exact=True)
        self._unique(duration_control, 'open video duration').evaluate('(control) => control.click()')
        slider = self.page.locator('input[type="range"][aria-label="视频时长滑块"]:visible')
        slider.wait_for(state='visible', timeout=self.timeout)
        value = self._unique(slider, 'video duration track').evaluate('''input => {
            const r = input.getBoundingClientRect();
            const marks = [...input.closest('.durationTrack').querySelectorAll('.durationMarks span')];
            return {min: +input.min, max: +input.max, step: +input.step, value: +input.value,
                    seconds: parseInt(input.getAttribute('aria-valuetext'), 10),
                    first: Number(marks[0]?.textContent), last: Number(marks.at(-1)?.textContent),
                    x: r.x, y: r.y, width: r.width, height: r.height};
        }''')
        span = value['max'] - value['min']
        if (span <= 0 or value['step'] != 1 or value['last'] - value['first'] != span
                or value['seconds'] != value['first'] + value['value'] - value['min']
                or not value['first'] <= seconds <= value['last'] or value['width'] <= 12):
            raise ValueError(f'视频时长轨道刻度与本轮秒数不符：{node_id}')
        if value['seconds'] != seconds:
            # The observed native thumb spans 12 CSS px. Its center stops inside the track edges.
            left = value['x'] + 6
            length = value['width'] - 12
            current_x = left + (value['seconds'] - value['first']) * length / span
            target_x = left + (seconds - value['first']) * length / span
            y = value['y'] + value['height'] / 2
            self.page.mouse.move(current_x, y)
            self.page.mouse.down()
            try:
                self.page.mouse.move(target_x, y, steps=18)
            finally:
                self.page.mouse.up()
            if slider.get_attribute('aria-valuetext') != f'{seconds}秒':
                raise ValueError(f'视频时长轨道未到达 {seconds} 秒：{node_id}')
        return self._wait_canvas(
            lambda g: next((node for node in g['nodes'] if node['id'] == node_id
                            and node['data'].get('duration') == seconds), None),
            'save video duration', timeout=35)

    def refresh_linked_draft(self, node_id, source_id, expected, reference_path):
        self.page.reload(wait_until='domcontentloaded')
        self.assert_target()
        snapshot = verify_saved(self.workspace, {node_id: expected})
        nodes = canvas_nodes(snapshot)
        data = _draft_data(nodes[node_id], node_id)
        edges = [edge for edge in snapshot['edges'] if
                 (edge.get('source'), edge.get('sourceHandle'), edge.get('target'), edge.get('targetHandle'))
                 == (source_id, 'image', node_id, 'in')]
        if len(edges) != 1:
            raise ValueError(f'刷新后未确认唯一参考连线：{node_id}')
        actual, = self.workspace.digest_image_outputs([nodes[source_id]])
        if actual.get('sha256') != hashlib.sha256(Path(reference_path).read_bytes()).hexdigest():
            raise ValueError(f'刷新后的参考原件与本次上传不符：{source_id}')
        return {'node_id': node_id, 'duration': data['duration'], 'source_id': source_id,
                'edge_id': edges[0]['id'], 'reference_sha256': actual['sha256']}



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


def _rename_new_node(ui, ident, name, *, reload_page=True):
    graph = ui.workspace.read_canvas()
    updated = deepcopy(graph)
    _nodes(updated)[ident]['data']['label'] = name
    if updated != graph:
        ui.workspace.write_canvas(graph, updated)
        if reload_page:
            _reload(ui)
    if _label(_nodes(ui.workspace.read_canvas())[ident]) != name:
        raise ValueError(f'原生节点改名未保存：{ident}')


def _reuse_or_create(ui, state, field, key, name, create, save, *, expected_hash=None, reload_after_rename=True):
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
                _rename_new_node(ui, ident, name, reload_page=reload_after_rename)
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
    _rename_new_node(ui, ident, name, reload_page=reload_after_rename)
    registered[key] = ident
    state.pop('creating')
    save()
    return ident


def _check_layout(page, ids):
    boxes = page.locator('.vue-flow__node:visible').evaluate_all(
        '(nodes)=>nodes.map(el=>[el.dataset.id,el.getBoundingClientRect().toJSON()])')
    boxes = [box for ident, box in boxes if ident in ids]
    if len(boxes) != len(ids) or any(max(a['left'], b['left']) < min(a['right'], b['right']) and
                                    max(a['top'], b['top']) < min(a['bottom'], b['bottom'])
                                    for a, b in combinations(boxes, 2)):
        raise ValueError('刷新后草稿节点有缺失或重叠')

