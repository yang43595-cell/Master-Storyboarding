"""Record actual canvas draft identities and reuse the existing formal readback evidence."""
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .artifacts import load_manifest
from .io import file_lock, read_json, sha256_json, write_json

DELIVERY_RECEIPT = Path('06_视频/画布交付回执.json')
_READBACK_FILES = ('frozen', 'observed', 'formal_readback')
_EVIDENCE_ERRORS = (ValueError, KeyError, OSError, TypeError, AttributeError, RuntimeError)


def _project_path(project, relative):
    path = (project / relative).resolve()
    if not path.is_relative_to(project):
        raise ValueError('画布交付回执与回读文件须在项目内')
    return path


def draft_delivery_lock(project):
    """Serialize canvas draft attempts and explicit recovery without locking other stages."""
    return file_lock(_project_path(project, DELIVERY_RECEIPT.with_suffix('.lock')))


def load_draft_receipt(project):
    path = _project_path(project, DELIVERY_RECEIPT)
    try:
        data = read_json(path)
    except FileNotFoundError:
        return {'draft_receipts': {}}
    if not isinstance(data, dict) or not isinstance(data.get('draft_receipts', {}), dict):
        raise ValueError(f'画布交付回执结构无效：{path}')
    data.setdefault('draft_receipts', {})
    for key, row in data['draft_receipts'].items():
        if (not isinstance(row, dict) or not isinstance(row.get('binding'), dict)
                or key != sha256_json(row['binding']) or row.get('phase') not in
                {'creation_unknown', 'node_observed', 'readback_complete'}
                or (row['phase'] == 'creation_unknown') == _native_node_id(row.get('node_id'))):
            raise ValueError(f'画布草稿回执无法核对：{path}')
    return data


def _native_node_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value))


def plan_bindings(project, plan):
    entries = {item['artifact_id']: item for item in load_manifest(project)['artifacts']}
    source, = [ref for ref in plan['source_artifacts']
               if entries[ref['artifact_id']].get('logical_id') == 'video.prompt']
    return {row['source_segment_id']: _binding(plan, source, row['source_segment_id'])
            for row in plan['drafts']}


def _binding(canvas, source, segment):
    return {'target_url': urlunsplit(urlsplit(canvas['target_url'])), 'target_project_id': canvas.get('target_project_id'),
            'source': {'artifact_id': source['artifact_id'], 'revision': source['revision'],
                       'sha256': source.get('sha256', source.get('checksum'))},
            'segment_id': segment}


def draft_record(receipt, binding):
    return receipt['draft_receipts'].get(sha256_json(binding))


def _check_node_binding(receipt, binding, node_id):
    if node_id is None:
        return
    if not _native_node_id(node_id):
        raise ValueError('须提供准确的原生草稿节点 ID')
    if any(item.get('node_id') == node_id and item['binding']['segment_id'] != binding['segment_id']
           and all(item['binding'][field] == binding[field] for field in
                   ('target_url', 'target_project_id', 'source'))
           for item in receipt['draft_receipts'].values()):
        raise ValueError(f'{node_id} 已绑定同版稿的另一生成段')


def update_draft_record(project, receipt, binding, **evidence):
    """Caller holds the draft lock. Existing original nodes cannot be rebound."""
    prior = draft_record(receipt, binding) or {}
    node_id = evidence.get('node_id', prior.get('node_id'))
    if prior.get('node_id') and prior['node_id'] != node_id:
        raise ValueError(f"{binding['segment_id']} 已绑定原草稿节点 {prior['node_id']}")
    _check_node_binding(receipt, binding, node_id)
    row = {**prior, **evidence, 'binding': binding}
    receipt['draft_receipts'][sha256_json(binding)] = row
    write_json(_project_path(project, DELIVERY_RECEIPT), receipt)
    return row


def clear_unobserved_draft(project, receipt, binding):
    """The caller has confirmed absence in the original workspace after refresh."""
    record = draft_record(receipt, binding)
    if not record or record['phase'] != 'creation_unknown':
        raise ValueError('只有未获节点 ID 的创建记录可以核对缺失后释放')
    del receipt['draft_receipts'][sha256_json(binding)]
    write_json(_project_path(project, DELIVERY_RECEIPT), receipt)



def store_creation_plan(project, plan, plan_sha256):
    relative = DELIVERY_RECEIPT.parent / '草稿回读' / '写前计划' / f'{plan_sha256}.json'
    write_json(_project_path(project, relative), plan)
    return relative.as_posix()

def readback_bundle(project, directory):
    directory = _project_path(project, directory)
    return {name: read_json(_project_path(project, directory / f'{name}.json'))
            for name in _READBACK_FILES}


def _compare_readback(project, bundle):
    if __package__ == 'scripts.core':
        from ..canvas_readback import compare
    else:
        from canvas_readback import compare

    frozen, observed, report = (bundle[name] for name in _READBACK_FILES)
    return (report.get('ok') is True and report.get('native_refresh_binding') == 'selected_fields_match'
            and bool(frozen.get('revision')) and report.get('revision') == frozen['revision']
            and report.get('frozen_sha256') == sha256_json(frozen)
            and report.get('observed_sha256') == sha256_json(observed)
            and compare(frozen, observed, project, formal_delivery=True)['ok'])


def _matches_bundle(frozen, binding):
    return ('preview_authorization' not in frozen
            and urlunsplit(urlsplit(frozen.get('target_url', ''))) == binding['target_url']
            and frozen.get('target_project_id') == binding['target_project_id']
            and any(all(ref.get(k) == v for k, v in binding['source'].items())
                    for ref in frozen.get('source_artifacts', [])))


def _observed_rows(frozen, observed, binding):
    if (not _matches_bundle(frozen, binding) or observed.get('phase') != 'after_refresh'
            or urlunsplit(urlsplit(observed.get('target_url', ''))) != binding['target_url']
            or any(frozen.get(key) != observed.get(key) for key in ('target_project_id', 'revision'))):
        return {}
    observed_ids = {row.get('node_id') for row in observed['drafts']}
    return {row['source_segment_id']: row['node_id'] for row in frozen['drafts']
            if row.get('node_id') in observed_ids and _native_node_id(row['node_id'])}


def publish_draft_readback(project, plan, result):
    """Publish checked actual IDs and immutable evidence to the existing delivery receipt."""
    receipt = load_draft_receipt(project)
    bindings = plan_bindings(project, plan)
    by_segment = {row['source_segment_id']: row['node_id'] for row in result['frozen']['drafts']}
    directory = DELIVERY_RECEIPT.parent / '草稿回读' / sha256_json([result[name] for name in _READBACK_FILES])
    records = receipt['draft_receipts']
    for segment, binding in bindings.items():
        if prior := draft_record(receipt, binding):
            if prior.get('node_id') not in (None, by_segment[segment]):
                raise ValueError(f"{segment} 已绑定原草稿节点 {prior['node_id']}")
            if prior['phase'] == 'creation_unknown':
                raise ValueError(f'{segment} 创建结果未知，先按实采节点解决原记录再收口')
        _check_node_binding(receipt, binding, by_segment[segment])
    for name in _READBACK_FILES:
        write_json(_project_path(project, directory / f'{name}.json'), result[name])
    for segment, binding in bindings.items():
        records[sha256_json(binding)] = {'binding': binding, 'node_id': by_segment[segment],
                                         'phase': 'readback_complete', 'plan_sha256': sha256_json(plan),
                                         'readback_path': directory.as_posix()}
    write_json(_project_path(project, DELIVERY_RECEIPT), receipt)
    return directory.as_posix()


def read_draft_delivery(project, canvas, entry, segment_ids):
    """Return complete/resume/unknown for exact source segments; absent means unstarted."""
    project = Path(project).resolve()
    if not canvas or not canvas.get('target_url'):
        return {}
    receipt = load_draft_receipt(project)
    bindings = {segment: _binding(canvas, entry, segment) for segment in segment_ids}
    result, verified = {}, {}
    for segment, binding in bindings.items():
        if not (row := draft_record(receipt, binding)):
            continue
        status = {'status': 'unknown', 'receipt_path': DELIVERY_RECEIPT.as_posix(),
                  'reason': '创建结果未知，先在原画布核对创建前后节点'}
        if plan_path := row.get('plan_path'):
            status['plan_path'] = plan_path
        if node_id := row.get('node_id'):
            status.update(status='resume', node_id=node_id, reason='续用原节点并补齐正式回读')
        if row['phase'] == 'readback_complete':
            path = row.get('readback_path')
            if not isinstance(path, str):
                raise ValueError(f'{segment} 缺准确正式回读路径')
            if path not in verified:
                try:
                    bundle = readback_bundle(project, path)
                    checked = _compare_readback(project, bundle)
                    verified[path] = bundle if checked else None
                except _EVIDENCE_ERRORS:
                    verified[path] = None
            if bundle := verified[path]:
                if _observed_rows(bundle['frozen'], bundle['observed'], binding).get(segment) == node_id:
                    status.update(status='complete', readback_path=path)
                    status.pop('reason', None)
        result[segment] = status

    # Older executors saved named triples without registration. Use exact scope only.
    missing = set(bindings) - result.keys()
    if not missing:
        return result
    root = _project_path(project, DELIVERY_RECEIPT.parent)
    if not root.is_dir():
        return result
    candidates = {}
    exact_scope = bindings[next(iter(missing))]
    for frozen_path in sorted(root.rglob('frozen.json')):
        if not frozen_path.resolve().is_relative_to(project):
            continue
        try:
            frozen = read_json(frozen_path)
            observed = read_json(_project_path(project, frozen_path.with_name('observed.json')))
            matched = _observed_rows(frozen, observed, exact_scope)
            present = missing & matched.keys()
            if not present:
                continue
            path = frozen_path.parent.relative_to(project).as_posix()
            try:
                report = read_json(_project_path(project, frozen_path.with_name('formal_readback.json')))
                complete = _compare_readback(project, {'frozen': frozen, 'observed': observed, 'formal_readback': report})
            except _EVIDENCE_ERRORS:
                complete = False
            for segment in present:
                status = {'status': 'complete' if complete else 'resume', 'node_id': matched[segment],
                          'receipt_path': path}
                if complete:
                    status['readback_path'] = path
                else:
                    status['reason'] = '已有草稿节点，须补齐正式回读'
                candidates.setdefault(segment, {}).setdefault(matched[segment], status)
                if complete:
                    candidates[segment][matched[segment]] = status
        except _EVIDENCE_ERRORS:
            continue
    for segment, by_node in candidates.items():
        if len(by_node) == 1:
            result[segment] = next(iter(by_node.values()))
        else:
            result[segment] = {'status': 'unknown', 'receipt_path': DELIVERY_RECEIPT.as_posix(),
                               'reason': '同一来源段存在多个原草稿节点，须核对原画布'}
    return result
