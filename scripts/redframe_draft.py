"""Save or reopen existing Redframe draft IDs and feed the formal readback check."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import unquote

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.canvas_readback import compare, digest, rows_by_id, validate_submission
from scripts.core.artifacts import load_manifest, project_path
from scripts.core.io import read_json, write_json
from scripts.core.redframe_workspace import RedframeWorkspace, RedframeWorkspaceError


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
    while True:
        snapshot = workspace.read_canvas()
        nodes = canvas_nodes(snapshot)
        changed = [ident for ident, expected in expected_nodes.items()
                   if ident not in nodes or (expected is not None and nodes[ident] != expected)
                   or nodes[ident].get('type') != 'remote-videoGenerationNode']
        if not changed:
            return snapshot
        if time.monotonic() >= stop:
            raise ValueError('保存未在持久画布确证；先保留原节点 ID：' + ', '.join(changed))
        time.sleep(min(1, max(0, stop - time.monotonic())))


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


class NativeTag(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.refs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'data-reference-id' in values:
            if tag != 'span' or 'imageReference' not in values.get('class', '').split():
                raise ValueError('原生提示词引用格式已改变')
            self.refs.append((json.loads(unquote(values['data-reference-id'])), values['data-value']))


def source_capabilities(project, frozen):
    registered = {item['artifact_id']: item for item in load_manifest(project)['artifacts']}
    source, = (registered[ref['artifact_id']] for ref in frozen['source_artifacts']
               if registered[ref['artifact_id']]['logical_id'] == 'video.prompt')
    model = read_json(project_path(project, source['path_or_url']))['selected_video_model']
    by_mode = model.get('reference_mode_evidence') or {model['reference_mode']: model['capability_evidence']}
    return {mode: (model['id'], read_json(project_path(project, ref['path']))['model'])
            for mode, ref in by_mode.items()}


def native_draft(graph, node, slots, models):
    ident, data = node['id'], node['data']
    if node.get('type') != 'remote-videoGenerationNode' or data.get('outputs') != {}:
        raise ValueError(f'{ident} 不是无生成输出的草稿节点')
    aliases = {'IMAGE': '图片', 'VIDEO': '视频', 'AUDIO': '音频'}
    mode = data['videoMode']
    model_name, capabilities = models[mode]
    if (json.loads(data['model']) != [capabilities['providerId'], capabilities['modelId']]
            or json.loads(data['mode']) not in capabilities['mode']):
        raise ValueError(f'{ident} 的实际模型或参考模式与本轮能力不符')
    if any(edge.get('target') == ident for edge in graph['edges']):
        raise ValueError(f'{ident} 存在入边，不能按直传草稿验收')
    refs = data['localReferences']['in']
    if len(refs) != len(slots):
        raise ValueError(f'{ident} 未收齐当前原生槽的下载证据')
    types = Counter()
    names, media = {}, []
    for index, (ref, slot) in enumerate(zip(refs, slots), 1):
        if (ref['source'] != ident or
                (slot['source'], slot['media_id'], slot['path'], slot['index'])
                != (ident, ref['sourceHandle'], ref['value']['url'], index)):
            raise ValueError(f'{ident} 第 {index} 槽的下载证据与实际槽身份不一致')
        if slot['status'] != 200 or not slot.get('sha256'):
            raise ValueError(f'{ident} 第 {index} 槽附件读取失败 HTTP {slot["status"]}')
        kind = ref['dataType']
        types[kind] += 1
        names[index] = f'@{aliases[kind]}{types[kind]}'
        media.append({'node_id': ident, 'media_id': slot['media_id'], 'sha256': slot['sha256']})
    token_pattern = r'\{\{ref (\d+)\}\}'
    mentions = []
    for line in data['promptModel']:
        for token in line:
            if token['type'] != 'Custom':
                continue
            ((node_id, media_id), value), = NativeTag(token['html']).refs
            index = int(re.fullmatch(token_pattern, value)[1])
            if not 1 <= index <= len(media) or (node_id, media_id) != (ident, media[index-1]['media_id']):
                raise ValueError(f'{ident} 的原生提示词引用和实际槽不符')
            mentions.append({'node_id': node_id, 'media_id': media_id, 'entity_type': 'reference',
                             'native_noneditable_entity': True})
    prompt = re.sub(token_pattern, lambda m: names[int(m[1])], data['prompt']).replace('\ufeff', '')
    return {'node_id': ident, 'prompt': prompt, 'ordered_media': media, 'native_mentions': mentions,
            'edges': [], 'source_nodes': [],
            'parameters': {'model': model_name, 'aspect_ratio': data['ratio'],
                           'resolution': data['resolution'].lower(), 'duration': data['duration'],
                           'reference_mode': mode}}


@contextmanager
def open_workspace(host_browser, target_url, project_id):
    with host_browser(redframe=True) as context:
        page = context.new_page()
        page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
        yield RedframeWorkspace(page, project_id)


def before_write_readback(graph, baseline, targets, models):
    """Reproject the unchanged native nodes, binding already verified exact slot IDs."""
    prior = rows_by_id(baseline['drafts'])
    rows = []
    for ident in targets:
        node = canvas_nodes(graph)[ident]
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
    models = source_capabilities(project, frozen)
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
    open_connection = lambda: open_workspace(task_browser, frozen['target_url'], frozen['target_project_id'])
    if resave:
        with open_connection() as workspace:
            before = workspace.read_canvas()
            current = canvas_nodes(before)
            if any(current.get(ident) != node for ident, node in expected.items()):
                raise ValueError('草稿已偏离最后可信节点；拒绝保存覆盖')
            # Only an identical resave is authorized here. Reuse verified slot bytes
            # for the write-side check only while its full native node is unchanged.
            current_read = before_write_readback(before, baseline, targets, models)
            preflight = validate_submission(frozen, project, formal_delivery=True, preflight=True,
                                            current=current_read, baseline=baseline)
            if not preflight['ok']:
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
    with open_connection() as workspace:
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
    parser.add_argument('--resave', action='store_true', help='仅在有本次授权时发送一次同值保存后独立重开')
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


if __name__ == '__main__':
    raise SystemExit(main())
