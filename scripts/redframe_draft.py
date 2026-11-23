"""Verify Redframe draft persistence and prepare formal readback."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.canvas_readback import compare, compare_baseline, digest, rows_by_id, validate_submission
from scripts.core.artifacts import cache_file_reads, load_manifest, project_path
from scripts.core.draft_delivery import (
    DELIVERY_RECEIPT, clear_unobserved_draft, draft_delivery_lock, draft_record, load_draft_receipt,
    plan_bindings, publish_draft_readback, read_draft_delivery, readback_bundle,
    store_creation_plan, update_draft_record,
)
from scripts.core.io import read_json, write_json
from scripts.core.native_references import redframe_reference_labels
from scripts.core.quality_evidence import draft_binding, fingerprint
from scripts.core.redframe_image_ui import ImageUIError, RedframeImageUI, _ident
from scripts.core.redframe_workspace import (
    NativeTag, RedframeWorkspace, RedframeWorkspaceError, video_generation_records,
)


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


def verify_placements(occupied, planned):
    """Check reservations together, before any of the planned positions are used."""
    def overlap(a, b):
        x, y, w, h = a
        u, v, p, q = b
        return x < u+p and u < x+w and y < v+q and v < y+h

    checked = {key: value for key, value in occupied.items() if key not in planned}
    for ident, row in {**checked, **planned}.items():
        if (len(row) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in row)
                or min(row[2:]) <= 0):
            raise ValueError(f'节点 {ident} 缺可用的矩形尺寸或位置')
    for ident, rectangle in planned.items():
        for other, reserved in checked.items():
            if overlap(rectangle, reserved):
                raise ValueError(f'写前位置冲突：{ident} 与 {other}')
        checked[ident] = rectangle


def measured_placements(graph, sizes, target_ids):
    nodes = canvas_nodes(graph)
    if set(sizes) != set(nodes):
        raise ValueError('画布节点集合与上次实测尺寸不同；须先采集当前真实尺寸')
    rectangles = {ident: (row['position']['x'], row['position']['y'], *sizes[ident])
                  for ident, row in nodes.items()}
    verify_placements(rectangles, {ident: rectangles[ident] for ident in target_ids})
    return {ident: rectangles[ident] for ident in target_ids}


def source_capabilities(project, frozen, read_json_bytes=None):
    registered = {item['artifact_id']: item for item in load_manifest(project)['artifacts']}
    ref, = (ref for ref in frozen['source_artifacts']
            if registered[ref['artifact_id']]['logical_id'] == 'video.prompt')
    source = registered[ref['artifact_id']]
    read_bytes = Path.read_bytes if read_json_bytes is None else read_json_bytes

    def checked_json(path, expected):
        raw = read_bytes(project_path(project, path))
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('视频源或模型能力证据在写前已偏离登记字节')
        return json.loads(raw.decode('utf-8-sig'))

    model = checked_json(source['path_or_url'], ref['sha256'])['selected_video_model']
    by_mode = model.get('reference_mode_evidence') or {model['reference_mode']: model['capability_evidence']}
    return {mode: (model['id'], checked_json(proof['path'], proof['sha256'])['model'])
            for mode, proof in by_mode.items()}


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


def native_draft(graph, node, slots, models):
    ident = node['id']
    data = _draft_data(node, ident)
    if any(edge.get('target') == ident for edge in graph['edges']):
        raise ValueError(f'{ident} 存在入边，不能按直传草稿验收')
    refs = data['localReferences']['in']
    if len(refs) != len(slots):
        raise ValueError(f'{ident} 未收齐当前原生槽的下载证据')
    media = []
    for index, (ref, slot) in enumerate(zip(refs, slots), 1):
        if (ref['source'] != ident or
                (slot['source'], slot['media_id'], slot['path'], slot['index'])
                != (ident, ref['sourceHandle'], ref['value']['url'], index)):
            raise ValueError(f'{ident} 第 {index} 槽的下载证据与实际槽身份不一致')
        if slot['status'] != 200 or not slot.get('sha256'):
            raise ValueError(f'{ident} 第 {index} 槽附件读取失败 HTTP {slot["status"]}')
        media.append({'node_id': ident, 'media_id': slot['media_id'], 'sha256': slot['sha256']})
    prompt, parameters, mentions = _native_content(
        ident, data, media, [ref['dataType'].lower() for ref in refs], models)
    return {'node_id': ident, 'prompt': prompt, 'ordered_media': media, 'native_mentions': mentions,
            'edges': [], 'source_nodes': [], 'parameters': parameters}


def _check_new_plan(plan, *, allow_linked=False):
    drafts = rows_by_id(plan['drafts'])
    segments = {}
    for draft in drafts.values():
        segment = draft['source_segment_id']
        if not isinstance(segment, str) or not segment or segment in segments:
            raise ValueError('写前计划须有不重复的来源段号')
        if draft.get('realistic_face_slots') and not allow_linked:
            raise ValueError(f'{segment} 含认证源卡槽，须走整节点连线回读')
        if (draft.get('edges') != [] or draft.get('source_nodes') != []
                or draft.get('ordered_media', []) != []):
            raise ValueError(f'{segment} 须使用尚未绑定平台输入的首次写入计划')
        if draft['quality_evidence'].get('input_sha256') != fingerprint(draft_binding(draft)):
            raise ValueError(f'{segment} 写前自查证据与计划不一致')
        segments[segment] = draft
    return segments


def _bind_new_drafts(project, plan, segment_nodes, observed):
    if observed.get('revision') != plan['revision']:
        raise ValueError('刷新回读修订与写前计划不一致')
    frozen = deepcopy(plan)
    drafts = frozen['drafts']
    segments = {draft['source_segment_id'] for draft in drafts}
    actual = rows_by_id(observed['drafts'])
    if (segments != set(segment_nodes)
            or len(set(segment_nodes.values())) != len(drafts)
            or set(segment_nodes.values()) != set(actual)):
        raise ValueError('计划段号、实际创建的节点 ID 和刷新回读不一一对应')
    actual_ids = {draft['node_id']: segment_nodes[draft['source_segment_id']] for draft in drafts}
    for draft in drafts:
        segment = draft['source_segment_id']
        node_id = segment_nodes[segment]
        if node_id == draft['node_id']:
            raise ValueError(f'{segment} 须使用新建的实际节点 ID')
        row = actual[node_id]
        media = row['ordered_media']
        if [item['sha256'] for item in media] != [ref['sha256'] for ref in draft['reference_checks']]:
            raise ValueError(f'{segment} 实际输入槽身份或文件顺序与写前计划不一致')
        if draft.get('realistic_face_slots'):
            draft.update(edges=deepcopy(row['edges']), source_nodes=deepcopy(row['source_nodes']))
        elif any(item['node_id'] != node_id for item in media):
            raise ValueError(f'{segment} 实际输入槽身份或文件顺序与写前计划不一致')
        draft.update(node_id=node_id, ordered_media=deepcopy(media))
        continuity = draft.get('continuity', {})
        predecessor = continuity.get('previous_node_id')
        if predecessor in actual_ids:
            continuity['previous_node_id'] = actual_ids[predecessor]
        draft['quality_evidence']['input_sha256'] = fingerprint(draft_binding(draft))
    observed = {**observed, 'frozen_sha256': digest(frozen)}
    result = compare(frozen, observed, project, formal_delivery=True)
    if not result['ok']:
        raise ValueError('刷新回读未通过：' + json.dumps(result['differences'], ensure_ascii=False))
    return {'frozen': frozen, 'observed': observed, 'formal_readback': result}


def finalize_new_drafts(project, plan, preflight, segment_nodes, observed):
    """Bind real IDs to a passed direct-upload plan and compare a fresh native readback."""
    # accept-artifact bundles the same preflight under its adoption result.
    report = preflight.get('preflight', preflight)
    if (preflight.get('ok') is not True or not isinstance(report, dict) or report.get('ok') is not True
            or report.get('frozen_sha256') != digest(plan)):
        raise ValueError('写前预检未通过，或快照与通过的预检不一致')
    _check_new_plan(plan)
    project = Path(project).resolve()
    with draft_delivery_lock(project):
        result = _bind_new_drafts(project, plan, segment_nodes, observed)
        readback_path = publish_draft_readback(project, plan, result)
        return {**result, 'readback_path': readback_path, 'receipt_path': DELIVERY_RECEIPT.as_posix()}


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


def _check_linked_observation(graph, nodes, row, draft, direct_slots, models):
    node_id = row['node_id']
    observed = Counter((edge['source_node_id'], edge['source_port'], edge['target_node_id'], edge['target_port'])
                       for edge in row['edges'])
    actual = Counter((edge.get('source'), edge.get('sourceHandle'), edge.get('target'), edge.get('targetHandle'))
                     for edge in graph['edges'] if edge.get('target') == node_id)
    if observed != actual:
        raise ValueError(f'{node_id} 连线回读与刷新的持久画布不符')
    kinds = [ref['kind'] for ref in draft['reference_checks']]
    prompt, parameters, mentions = _native_content(
        node_id, nodes[node_id]['data'], row['ordered_media'], kinds, models, linked=True)
    actual_mentions = [(entry['node_id'], entry['media_id']) for entry in row['native_mentions']]
    if (row['prompt'] != prompt or row['parameters'] != parameters
            or actual_mentions != [(entry['node_id'], entry['media_id']) for entry in mentions]):
        raise ValueError(f'{node_id} 正文、参数或原生引用与刷新的持久画布不符')
    direct_media = [media for media, ref in zip(row['ordered_media'], draft['reference_checks'])
                    if ref['canvas_binding']['mode'] == 'direct']
    if (len(direct_media) != len(direct_slots)
            or any(slot.get('status') != 200 or (media['node_id'], media['media_id'], media['sha256'])
                   != (slot['source'], slot['media_id'], slot.get('sha256'))
                   for media, slot in zip(direct_media, direct_slots))):
        raise ValueError(f'{node_id} 直传槽原件与整批下载证据不符')
    for slot, record in row.get('face_certifications', {}).items():
        source = nodes[record['node_id']]['data']
        port = draft['reference_checks'][int(slot) - 1]['canvas_binding']['source_port']
        output = source.get('outputs', {}).get(port) or {}
        source_url = (output.get('value') or {}).get('url')
        if (source.get('faceCertification') != record['faceCertification']
                or source_url != record['source_url']):
            raise ValueError(f'{node_id} 源卡认证与刷新的持久画布不符')


def run_new_draft_batch(project, plan, write, *, host_browser, check_layout, collect_linked=None,
                        resume=None):
    """Create unstarted drafts, resume exact existing IDs, and publish the refreshed result.

    `write(ui, planned_draft)` creates through `ui.create_video_draft` and returns
    the native ID or full saved node. `resume(ui, planned_draft, node_id)`, when
    provided, only finishes incomplete fields on that original node. Without it
    a known node is refreshed read-only. Both callbacks must avoid generation.
    The layout and linked-slot collectors are read-only.
    """
    project = Path(project).resolve()
    with draft_delivery_lock(project):
        return _run_new_draft_batch(project, plan, write, host_browser=host_browser,
                                    check_layout=check_layout, collect_linked=collect_linked, resume=resume)


def _write_draft_nodes(project, plan, drafts, bindings, existing, receipt, ui, before, write, resume):
    plan_sha256 = digest(plan)
    previous_ids = {ident for ident, node in before.items() if node.get('type') == 'remote-videoGenerationNode'}
    segment_nodes, expected = {}, {}
    plan_path = store_creation_plan(project, plan, plan_sha256) if set(drafts) - existing.keys() else None
    for segment, draft in drafts.items():
        binding = bindings[segment]
        prior = existing.get(segment)
        wrote_fields = False
        if prior:
            node_id = prior['node_id']
            _draft_data(before.get(node_id), node_id)
            ui._resume_only = True
            record = draft_record(receipt, binding) or {}
            already_written = record.get('write_finished_for') == plan_sha256
            if resume is not None and prior['status'] == 'resume' and not already_written:
                saved = resume(ui, deepcopy(draft), node_id)
                if saved is not None and (saved.get('id') if isinstance(saved, dict) else saved) != node_id:
                    raise ValueError(f'{segment} 续作须使用原草稿节点 {node_id}')
                wrote_fields = True
            else:
                saved = None
        else:
            update_draft_record(project, receipt, binding, phase='creation_unknown',
                                plan_sha256=plan_sha256, plan_path=plan_path,
                                previous_node_ids=sorted(previous_ids))

            def record_created(ident):
                ident = _ident(ident)
                if ident in previous_ids or ident in expected or ident == draft['node_id']:
                    raise ValueError(f'{segment} 须绑定本批新建的唯一草稿节点 ID：{ident}')
                update_draft_record(project, receipt, binding, phase='node_observed', node_id=ident)
                ui._resume_only = True

            ui._draft_created = record_created
            saved = write(ui, deepcopy(draft))
            node_id = _ident(saved.get('id') if isinstance(saved, dict) else saved)
            record = draft_record(receipt, binding)
            if record.get('node_id') != node_id:
                observed_node = saved if isinstance(saved, dict) else canvas_nodes(ui.workspace.read_canvas()).get(node_id)
                _draft_data(observed_node, node_id)
                record_created(node_id)
            previous_ids.add(node_id)
            wrote_fields = True
        ui._draft_created = None
        ui._resume_only = False
        node = saved if isinstance(saved, dict) else None
        if node is not None:
            _draft_data(node, node_id)
        if node_id in expected:
            raise ValueError(f'{segment} 的草稿节点与本批其他段冲突：{node_id}')
        if wrote_fields:
            update_draft_record(project, receipt, binding, node_id=node_id, phase='node_observed',
                                write_finished_for=plan_sha256)
        segment_nodes[segment] = node_id
        expected[node_id] = node
    return segment_nodes, expected


def _run_new_draft_batch(project, plan, write, *, host_browser, check_layout, collect_linked, resume):
    started = time.monotonic()
    drafts = _check_new_plan(plan, allow_linked=True)
    linked = {segment: draft for segment, draft in drafts.items() if draft.get('realistic_face_slots')}
    if linked and collect_linked is None:
        raise ValueError('认证源卡槽须提供本批真实连线、原件和认证回读')
    if any(draft.get('native_identity_mode', 'media_id') != 'media_id' for draft in drafts.values()):
        raise ValueError('该批次入口只接收原生媒体 ID 回读')
    read_json_bytes = cache_file_reads(Path.read_bytes)
    preflight = validate_submission(plan, project, formal_delivery=True, preflight=True, read_json_bytes=read_json_bytes)
    bindings = plan_bindings(project, plan)
    existing = read_draft_delivery(project, plan, next(iter(bindings.values()))['source'], drafts)
    if unknown := [segment for segment, row in existing.items() if row['status'] == 'unknown']:
        raise ValueError('创建结果未知，先核原画布及原回执：' + '、'.join(sorted(unknown)))
    receipt = load_draft_receipt(project)
    plan_sha256 = digest(plan)
    complete = {segment: row for segment, row in existing.items() if row['status'] == 'complete'}
    if set(complete) == set(drafts) and all(
        (draft_record(receipt, binding) or {}).get('plan_sha256') == plan_sha256
        for binding in bindings.values()
    ):
        paths = {row['readback_path'] for row in complete.values()}
        if len(paths) == 1:
            path, = paths
            bundle = readback_bundle(project, path)
            nodes = {row['source_segment_id']: row['node_id'] for row in bundle['frozen']['drafts']}
            if set(nodes) == set(drafts):
                return {**bundle, 'preflight': preflight, 'segment_nodes': nodes, 'layout': None,
                        'timings': {'elapsed_seconds': round(time.monotonic() - started, 3)},
                        'reused_readback': True, 'readback_path': path,
                        'receipt_path': DELIVERY_RECEIPT.as_posix()}
    direct = [segment for segment in drafts if segment not in linked]
    models = source_capabilities(project, plan, read_json_bytes)
    target, project_id = plan['target_url'], plan['target_project_id']
    tick = time.monotonic()
    timings = {'plan_check_seconds': round(tick - started, 3)}
    with open_workspace(host_browser, target, project_id) as workspace:
        ui = RedframeVideoDraftUI(workspace.page, project_id, target, workspace=workspace)
        ui.assert_target()
        before = canvas_nodes(workspace.read_canvas())
        next_tick = time.monotonic()
        timings['open_seconds'] = round(next_tick - tick, 3)
        segment_nodes, expected = _write_draft_nodes(
            project, plan, drafts, bindings, existing, receipt, ui, before, write, resume)
        tick = time.monotonic()
        timings['write_seconds'] = round(tick - next_tick, 3)
        workspace.page.reload(wait_until='domcontentloaded')
        ui.assert_target()
        graph = verify_saved(workspace, expected)
        actual = canvas_nodes(graph)
        for node_id in segment_nodes.values():
            _draft_data(actual[node_id], node_id)
        next_tick = time.monotonic()
        timings['refresh_seconds'] = round(next_tick - tick, 3)
        layout = check_layout(ui, graph, segment_nodes)
        tick = time.monotonic()
        timings['layout_seconds'] = round(tick - next_tick, 3)
        slots = workspace.digest_slots([actual[node_id] for node_id in segment_nodes.values()])
        nodes = [actual[segment_nodes[segment]] for segment in direct]
        rows = {node['id']: native_draft(graph, node, slots[node['id']], models) for node in nodes}
        if linked:
            linked_rows = rows_by_id(collect_linked(ui, graph, segment_nodes, linked, slots))
            if set(linked_rows) != {segment_nodes[segment] for segment in linked}:
                raise ValueError('认证源卡回读须与本批实际连线草稿一一对应')
            for segment, draft in linked.items():
                node_id = segment_nodes[segment]
                _check_linked_observation(graph, actual, linked_rows[node_id], draft, slots[node_id], models)
            rows.update(linked_rows)
        next_tick = time.monotonic()
        timings['attachments_seconds'] = round(next_tick - tick, 3)
    tick = time.monotonic()
    timings['close_seconds'] = round(tick - next_tick, 3)
    observed = dict(target_url=target, target_project_id=project_id, revision=plan['revision'],
                    phase='after_refresh', drafts=[rows[node_id] for node_id in segment_nodes.values()])
    result = _bind_new_drafts(project, plan, segment_nodes, observed)
    readback_path = publish_draft_readback(project, plan, result)
    ended = time.monotonic()
    timings.update(formal_readback_seconds=round(ended - tick, 3),
                   elapsed_seconds=round(ended - started, 3))
    return {**result, 'preflight': preflight, 'segment_nodes': segment_nodes, 'layout': layout, 'timings': timings,
            'readback_path': readback_path, 'receipt_path': DELIVERY_RECEIPT.as_posix()}


def resolve_draft_creation(project, plan, segment_id, *, host_browser, node_id=None, confirm_absent=False):
    """Resolve a recorded unknown creation against the refreshed original workspace.

    Pass the independently identified native node ID to resume it, or use the
    explicit absence path after investigating the original attempt.
    """
    if (node_id is not None) == confirm_absent:
        raise ValueError('须明确选择核对原节点 ID 或确证未创建')
    project = Path(project).resolve()
    draft = _check_new_plan(plan, allow_linked=True).get(segment_id)
    if draft is None:
        raise ValueError('须指定原计划中的准确生成段')
    if node_id is not None:
        _ident(node_id)
    with draft_delivery_lock(project):
        validate_submission(plan, project, formal_delivery=True, preflight=True)
        binding = plan_bindings(project, plan)[segment_id]
        receipt = load_draft_receipt(project)
        record = draft_record(receipt, binding)
        if not record or record['phase'] != 'creation_unknown' or record.get('plan_sha256') != digest(plan):
            raise ValueError(f'{segment_id} 缺同一原计划的创建未知记录')
        baseline = record.get('previous_node_ids')
        if not isinstance(baseline, list) or len(set(baseline)) != len(baseline):
            raise ValueError(f'{segment_id} 缺准确创建前节点范围')
        baseline = {_ident(ident) for ident in baseline}
        target, project_id = plan['target_url'], plan['target_project_id']
        with open_workspace(host_browser, target, project_id) as workspace:
            ui = RedframeVideoDraftUI(workspace.page, project_id, target, workspace=workspace)
            workspace.page.reload(wait_until='domcontentloaded')
            ui.assert_target()
            nodes = {ident: node for ident, node in canvas_nodes(workspace.read_canvas()).items()
                     if node.get('type') == 'remote-videoGenerationNode'}
        new_ids = nodes.keys() - baseline
        if not baseline <= nodes.keys():
            raise ValueError(f'{segment_id} 创建前节点已变化，保留未知并核对原画布')
        if node_id is None:
            if new_ids:
                raise ValueError(f'{segment_id} 仍有新增节点，不能按缺失释放')
            clear_unobserved_draft(project, receipt, binding)
            return {'ok': True, 'segment_id': segment_id, 'status': 'confirmed_absent'}
        if node_id == draft['node_id'] or node_id not in new_ids or len(new_ids) != 1:
            raise ValueError(f'{segment_id} 实采新节点不唯一或不对应指定 ID，保留未知')
        _draft_data(nodes[node_id], node_id)
        update_draft_record(project, receipt, binding, phase='node_observed', node_id=node_id)
        return {'ok': True, 'segment_id': segment_id, 'status': 'resume', 'node_id': node_id}

def save_draft_batch(ui, baseline, staged_graph, changes, ordered_hashes, protected_ids):
    """Save staged draft edits and use one refreshed graph for cards, slots and prompts."""
    workspace = ui.workspace
    ui.assert_target()
    nodes = canvas_nodes(staged_graph)
    if set(changes) != set(ordered_hashes):
        raise ValueError("缺少本批节点有序附件哈希")
    for key, hashes in ordered_hashes.items():
        _draft_data(nodes[key], key)
        slots = changes[key].get("localReferences", nodes[key]["data"].get("localReferences", {})).get("in", [])
        if (len(hashes) != len(slots)
                or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)):
            raise ValueError(f"{key} 须提供每个目标槽已核对的 SHA-256")
    protected = set(protected_ids)
    expected = {key: nodes[key] for key in changes}
    graph = workspace.save_draft_changes(baseline, expected, changes, protected_ids=protected,
                                         staged_edges=staged_graph["edges"])
    ui.page.reload(wait_until="domcontentloaded")
    ui.assert_target()
    desired = canvas_nodes(graph)
    actual_graph = verify_saved(workspace, {key: desired[key] for key in changes})
    actual = canvas_nodes(actual_graph)
    for key in protected:
        if actual.get(key) != desired[key]:
            raise ValueError(f"刷新后素材卡缺失或已变化：{key}")
    if actual_graph["edges"] != graph["edges"]:
        raise ValueError("刷新后连线发生变化")
    slots = workspace.digest_slots([actual[key] for key in changes])
    for key, hashes in ordered_hashes.items():
        if [item.get("sha256") for item in slots[key]] != hashes:
            raise ValueError(f"{key} 刷新后的附件字节或顺序不符")
    return actual_graph, slots


def before_write_readback(graph, baseline, targets, models):
    """Reproject the unchanged native nodes, binding already verified exact slot IDs."""
    prior = rows_by_id(baseline['drafts'])
    nodes = canvas_nodes(graph)
    rows = []
    for ident in targets:
        node = nodes[ident]
        native = node['data']['localReferences']['in']
        media = prior[ident]['ordered_media']
        if len(native) != len(media):
            raise ValueError(f'{ident} 的写前附件槽数已改变')
        slots = [{'source': item['node_id'], 'media_id': item['media_id'], 'sha256': item['sha256'],
                  'path': ref['value']['url'], 'index': index, 'status': 200}
                 for index, (item, ref) in enumerate(zip(media, native), 1)]
        rows.append(native_draft(graph, node, slots, models))
    return {**baseline, 'phase': 'before_write', 'drafts': rows}


def retest(project, frozen, baseline, trusted_graph, dimensions, *, resave=False):
    """Run one existing-node batch, with optional authorized identical resave."""
    read_json_bytes = cache_file_reads(Path.read_bytes)
    validate_submission(frozen, project, formal_delivery=True, preflight=True, read_json_bytes=read_json_bytes)
    models = source_capabilities(project, frozen, read_json_bytes)
    del read_json_bytes
    targets = rows_by_id(frozen['drafts'])
    reference = canvas_nodes(trusted_graph)
    expected = {ident: reference[ident] for ident in targets}
    sizes = {row['id']: (row['w'], row['h']) for row in dimensions}
    if set(sizes) != set(reference):
        raise ValueError('尺寸证据与最后可信画布的节点集合不一致')
    if any(row.get('edges') != [] or row.get('source_nodes') != [] for row in targets.values()):
        raise ValueError('此红帧采集入口只支持现行直传草稿')
    sys.path.insert(0, str(Path.home() / '.local/share/codex-headless-browser'))
    from headless_edge import task_browser

    timers, started = {}, time.monotonic()
    with open_workspace(task_browser, frozen['target_url'], frozen['target_project_id']) as workspace:
        if resave:
            before = workspace.read_canvas()
            current = canvas_nodes(before)
            if any(current.get(ident) != node for ident, node in expected.items()):
                raise ValueError('草稿已偏离最后可信节点；拒绝保存覆盖')
            # Only an identical resave is authorized here. Reuse verified slot bytes
            # for the write-side check only while its full native node is unchanged.
            current_read = before_write_readback(before, baseline, targets, models)
            if not compare_baseline(baseline, current_read)['ok']:
                raise ValueError('现有草稿写前比对未通过')
            measured_placements(before, sizes, targets)
            try:
                workspace.write_canvas(before, before)
            except RedframeWorkspaceError as exc:
                # An identical reread can show survival but cannot acknowledge a PUT.
                try:
                    verify_saved(workspace, expected, timeout=0)
                except (RedframeWorkspaceError, ValueError) as checked:
                    raise ValueError(f'保存没有确认回执；只读回查也未确认：{checked}') from exc
                raise ValueError(f'保存没有确认回执；原节点仍可回读：{exc}') from exc
            timers['save_seconds'] = round(time.monotonic() - started, 3)
        tick = time.monotonic()
        if resave:
            workspace.page.reload(wait_until='domcontentloaded')
        snapshot = verify_saved(workspace, expected)
        if resave and snapshot != before:
            raise ValueError('保存后画布出现并发变化；保留当前现场，不覆盖')
        actual = canvas_nodes(snapshot)
        # Reuse already measured size only while every node has the same native
        # visual state. A changed graph needs new geometry, never placeholder sizes.
        if actual != reference:
            raise ValueError('画布节点内容或位置有变化；须用当前实测尺寸检查布局')
        boxes = measured_placements(snapshot, sizes, targets)
        timers['refresh_seconds'] = round(time.monotonic() - tick, 3)
        tick = time.monotonic()
        slots = workspace.digest_slots([actual[ident] for ident in targets])
        timers['attachments_seconds'] = round(time.monotonic() - tick, 3)
        observed = {key: frozen[key] for key in ('target_url', 'target_project_id', 'revision')}
        try:
            observed.update(phase='after_refresh', frozen_sha256=digest(frozen),
                            drafts=[native_draft(snapshot, actual[ident], slots[ident], models) for ident in targets])
            result = compare(frozen, observed, project, formal_delivery=True)
        except (KeyError, ValueError) as exc:
            result = {'ok': False, 'error': str(exc)}
    timers['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return {'ok': result['ok'], 'resave_acknowledged': resave, 'node_count': len(actual), 'layout': boxes,
            'timings': timers, 'slots': slots, 'observed': observed, 'formal_readback': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'frozen', 'baseline', 'trusted-canvas', 'dimensions', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--resave', action='store_true', help='仅在有本次授权时发送一次同值保存，再在原后台会话刷新回读')
    args = parser.parse_args()
    try:
        result = retest(args.project.resolve(), read_json(args.frozen), read_json(args.baseline),
                        read_json(args.trusted_canvas), read_json(args.dimensions), resave=args.resave)
        write_json(args.report, result)
        print(json.dumps({key: result[key] for key in ('ok', 'resave_acknowledged', 'node_count', 'timings')},
                         ensure_ascii=False))
        return 0 if result['ok'] else 2
    except (ValueError, KeyError, OSError, TypeError, RedframeWorkspaceError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


def finalize_new_cli(argv):
    parser = argparse.ArgumentParser(description='用实采刷新回读绑定首次直传计划并生成正式检查回执')
    for name in ('project', 'plan', 'preflight', 'segment-nodes', 'readback', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = finalize_new_drafts(args.project.resolve(), read_json(args.plan), read_json(args.preflight),
                                     read_json(args.segment_nodes), read_json(args.readback))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name in ('frozen', 'observed', 'formal_readback'):
            write_json(args.output_dir / f'{name}.json', result[name])
        report = result['formal_readback']
        print(json.dumps({**{key: report[key] for key in ('ok', 'draft_count', 'frozen_sha256')},
                          'receipt_path': result['receipt_path'], 'readback_path': result['readback_path']},
                         ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


def resolve_creation_cli(argv):
    parser = argparse.ArgumentParser(description='只读核对创建结果未知的视频草稿，更新原交付回执')
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--segment-id', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--node-id')
    mode.add_argument('--confirm-absent', action='store_true')
    args = parser.parse_args(argv)
    try:
        sys.path.insert(0, str(Path.home() / '.local/share/codex-headless-browser'))
        from headless_edge import task_browser

        result = resolve_draft_creation(args.project, read_json(args.plan), args.segment_id,
                                        host_browser=task_browser, node_id=args.node_id,
                                        confirm_absent=args.confirm_absent)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, TypeError, RuntimeError, ImportError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


def write_recreation_cli(argv):
    parser = argparse.ArgumentParser(description='按本项目已采用编导稿与复刻分组写入并回读红帧草稿')
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--append-revision', action='store_true')
    parser.add_argument('--changed-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        from scripts.core.redframe_recreation import _revision_scope, recreation_groups, write_recreation

        project = args.project.resolve()
        group_path = project / args.groups
        if args.preflight:
            packet, canvas = recreation_groups(project, group_path)
            result = {'ok': True, 'draft_count': len(packet['groups']), 'target_url': canvas['target_url']}
            if args.changed_only:
                from scripts.core.draft_delivery import load_draft_receipt
                if not args.append_revision:
                    raise ValueError('局部另建须使用 --append-revision')
                changed, _, _ = _revision_scope(project, packet, load_draft_receipt(project).get('recreation', {}))
                result['changed_groups'] = [group['id'] for group in changed]
                result['write_count'] = len(changed)
        else:
            sys.path.insert(0, str(Path.home() / '.local/share/codex-headless-browser'))
            from headless_edge import task_browser

            result = write_recreation(project, group_path, host_browser=task_browser,
                                      append_revision=args.append_revision, changed_only=args.changed_only)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, TypeError, RuntimeError, ImportError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    if sys.argv[1:2] == ['finalize-new']:
        raise SystemExit(finalize_new_cli(sys.argv[2:]))
    if sys.argv[1:2] == ['resolve-creation']:
        raise SystemExit(resolve_creation_cli(sys.argv[2:]))
    if sys.argv[1:2] == ['write-recreation']:
        raise SystemExit(write_recreation_cli(sys.argv[2:]))
    raise SystemExit(main())
