"""A compact coordinator view of an existing image worker self-check receipt."""

import hashlib
import json
import re
from pathlib import Path

from .artifacts import project_path
from .io import read_json, sha256_file
from .media_types import MEDIA_EXTENSIONS
from .visual_context_budget import assert_payload_free, VisualContextBudgetError


CONTEXT_ROOT = Path('00_项目管理/执行上下文')
BATCH_CARD_PREFIX = '- 图片批次依据 `'


_REVIEW_STATUS = {
    'pass': 'passed',
    'passed': 'passed',
    'fail': 'failed',
    'failed': 'failed',
    'repair_required': 'failed',
    '通过，可作为待审服装候选': 'passed',
    '服装外观通过，三视图构图待处理': 'failed',
}


def _identity(value):
    return (isinstance(value, str) and 0 < len(value) <= 160 and value == value.strip()
            and value.isprintable())


def _target(value):
    if not _identity(value) or value.count('@') != 1 or any(char in value for char in '/\\*?'):
        return False
    asset, output = value.split('@')
    return asset.startswith('AST-') and len(asset) > 4 and bool(output.strip())


def validate_asset_batch_targets(targets):
    if (not isinstance(targets, list) or not 1 <= len(targets) <= 8
            or not all(_target(item) for item in targets) or len(set(targets)) != len(targets)):
        raise ValueError('资产 image_targets 须列 1–8 个不重复的准确 AST-ID@OUTPUT-ID')
    return list(targets)


def _inside(project, raw):
    path = project_path(project, raw)
    if path == project or not path.is_relative_to(project):
        raise ValueError('资产回执或成图路径越出项目')
    return path


def asset_batch_binding_entries(text):
    return {line.removeprefix(BATCH_CARD_PREFIX).removesuffix('`') for line in text.splitlines()
            if line.startswith(BATCH_CARD_PREFIX) and line.endswith('`')}


def _card_has_narrative(card, narrative):
    for block in card.split('\n- artifact_id：`')[1:]:
        lines = block.splitlines()
        if (lines and lines[0] == f'{narrative["artifact_id"]}`'
                and '  - logical_id：`narrative.package`' in lines
                and f'  - revision：`{narrative["revision"]}`' in lines
                and f'  - sha256：`{narrative["checksum"]}`' in lines):
            return True
    return False


def _basis_has_narrative(basis, narrative):
    snapshot = basis.get('input_snapshot', {})
    if not isinstance(snapshot, dict) or snapshot.get('candidate_test'):
        return False
    for fact in snapshot.get('artifacts', []):
        if not isinstance(fact, dict):
            continue
        record, source = fact.get('record'), fact.get('file')
        if not isinstance(record, dict) or not isinstance(source, dict):
            continue
        if (record.get('logical_id') == 'narrative.package'
                and all(record.get(field) == narrative[field] for field in ('artifact_id', 'revision', 'checksum'))
                and source.get('sha256') == narrative['checksum']):
            return True
    return False


def _validate_bound_request(project, binding):
    request = _inside(project, binding['request'])
    receipt = _inside(project, binding['receipt'])
    if request == receipt or any(path.suffix.lower() != '.json' for path in (request, receipt)):
        raise ValueError('本批请求和回执须为项目内不同的 JSON 文件')
    raw = request.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['request_sha256']:
        raise ValueError('原请求内容已改变')
    payload = json.loads(raw.decode('utf-8'))
    if not isinstance(payload, dict) or payload.get('image_targets') != binding['targets']:
        raise ValueError('本批约定目标与准确请求不一致')
    return receipt


def load_asset_batch_binding(project, directory, *, narrative=None, validate_request=True):
    """Read one existing batch. Historical cards and prepare-only tasks share this reader."""
    project = project.resolve()
    directory = Path(directory)
    context_root = project / CONTEXT_ROOT
    is_root = directory == context_root
    if (not is_root and directory.parent != context_root / '批次') or directory.resolve() != directory:
        raise ValueError('资产批次上下文须为当前项目的原目录')
    context_id = None if is_root else directory.name
    handoff_path = directory / 'next_stage_handoff.md'
    startup_path = directory / 'stage_startup.md'
    basis_path = directory / 'task_basis.json'
    carriers = (handoff_path, startup_path, basis_path)
    for path in carriers:
        if path.is_symlink():
            raise ValueError('资产批次依据须使用本批原文件')
    card = handoff_path.read_text(encoding='utf-8') if handoff_path.is_file() else ''
    startup = startup_path.read_text(encoding='utf-8') if startup_path.is_file() else ''
    card_rows = (asset_batch_binding_entries(card)
                 if '- 目标 Skill：`asset-image-assistant`' in card else set())
    startup_rows = (asset_batch_binding_entries(startup)
                    if '- Skill：`asset-image-assistant`' in startup else set())
    rows = card_rows | startup_rows
    try:
        basis = read_json(basis_path) if basis_path.is_file() else {}
    except (OSError, ValueError):
        if startup_rows and not card_rows and narrative is not None:
            raise ValueError('资产任务依据无法核对编导版本')
        basis = {}
    if not isinstance(basis, dict):
        basis = {}
    has_basis = (basis.get('skill') == 'asset-image-assistant'
                 and basis.get('context_id') == context_id)
    bindings = [json.loads(row) for row in rows]
    if has_basis and 'asset_batch' in basis:
        bindings.append(basis['asset_batch'])
    if not bindings:
        return None
    batch = bindings[0]
    if not isinstance(batch, dict) or any(item != batch for item in bindings):
        raise ValueError('本批交接卡或任务依据的图片批次绑定冲突')
    if batch.get('context_id') != context_id:
        raise ValueError('批次身份不一致')
    validate_asset_batch_targets(batch.get('targets'))
    if narrative is not None and not (
        (card_rows and _card_has_narrative(card, narrative))
        or (has_basis and _basis_has_narrative(basis, narrative))
    ):
        return None
    if validate_request:
        _validate_bound_request(project, batch)
    return batch


def receive_bound_asset_batch(project, binding):
    project = project.resolve()
    receipt = _validate_bound_request(project, binding)
    return summarize_asset_batch_receipt(project, receipt, binding['targets'], binding=binding)


def _field(row, current, historical):
    values = [row[key] for key in (current, historical) if row.get(key) is not None]
    return values[0] if values and all(value == values[0] for value in values) else None


def _observation(value):
    if isinstance(value, dict):
        return any(isinstance(item, str) and item.strip() for item in value.values())
    return isinstance(value, str) and bool(value.strip())


def _pending(target, issue):
    return {'target': target, 'status': 'pending', 'issue': issue}


def _compact_issue(value, fallback='实看未通过，见原回执'):
    if not _identity(value) or any(char in value for char in '/\\<>`{}'):
        return fallback
    return value


def _observation_summary(observations):
    # Full observations stay in receipt_path; never transport arbitrary row fields.
    for value in observations:
        if isinstance(value, dict):
            value = '；'.join(f'{key}: {item}' for key, item in value.items()
                             if isinstance(item, str) and item.strip())
        if isinstance(value, str) and value.strip():
            try:
                assert_payload_free(value)
            except VisualContextBudgetError:
                continue
            text = ' '.join(value.split())
            return text if len(text) <= 240 else text[:239] + '…'
    return '观察摘要见原回执'


def _reviewed_row(project, target, row, batch_observer):
    review_results = [row[key] for key in ('visual_status', 'review_result') if row.get(key) is not None]
    observations = [row.get('observed'), row.get('observations')]
    observer = _field(row, 'observer', 'review_actor')
    problem = row.get('blocking_issue') or row.get('disposition')
    self_check = row.get('visual_self_check')
    if isinstance(self_check, dict):
        review_results.append(self_check.get('status'))
        observations.append(self_check.get('observed'))
        if row.get('observer') is None and row.get('review_actor') is None:
            observer = batch_observer
        if problem is None and self_check.get('status') == '服装外观通过，三视图构图待处理':
            problem = self_check['status']
    if (row.get('status') == 'blocked' and not review_results and _identity(observer)
            and isinstance(row.get('blocking_issue'), str) and row['blocking_issue'].strip()):
        return {'target': target, 'observer': observer, 'status': 'blocked',
                'issue': _compact_issue(row['blocking_issue'], '成图阻塞，见原回执')}
    states = {_REVIEW_STATUS.get(value) if isinstance(value, str) else None for value in review_results}
    candidate = row.get('candidate_id')
    if len(states) != 1 or None in states or not _identity(observer) or not _identity(candidate):
        return _pending(target, '实看结果、观察者或准确候选身份未明确')
    state = states.pop()
    has_observation = any(_observation(value) for value in observations)
    if not has_observation and not (state == 'failed' and isinstance(problem, str) and problem.strip()):
        return _pending(target, '缺实际视觉观察记录')
    checked = {'target': target, 'candidate_id': candidate, 'observer': observer, 'visual_status': state,
               'observation_summary': _observation_summary(observations)}
    if state == 'failed':
        return {**checked, 'status': 'blocked', 'issue': _compact_issue(problem)}

    path, fingerprint = _field(row, 'path', 'output_path'), row.get('sha256')
    if (not isinstance(path, str) or not path.strip() or not isinstance(fingerprint, str)
            or not re.fullmatch(r'[A-Fa-f0-9]{64}', fingerprint)):
        return _pending(target, '实看通过记录缺准确图片路径或 SHA-256')
    try:
        local = _inside(project, path)
        if (local.suffix.lower() not in MEDIA_EXTENSIONS['image'] or not local.is_file()
                or not local.stat().st_size or sha256_file(local) != fingerprint.lower()):
            return _pending(target, '图片缺失、格式不受支持或文件 SHA-256 已改变')
    except (OSError, RuntimeError, ValueError):
        return _pending(target, '图片路径无法按当前项目核验')
    return {**checked, 'status': 'ready_for_review', 'path': str(local), 'sha256': fingerprint.lower()}


def _target_result(project, target, rows, batch_observer):
    matches = [_reviewed_row(project, target, row, batch_observer) for row in rows if row.get('target') == target]
    if not matches:
        return _pending(target, '此目标的实看回执尚未记录')
    if len(matches) == 1:
        return matches[0]
    if any(row['status'] == 'pending' for row in matches):
        return _pending(target, '同一目标的多份候选仍需核对')
    passed = [row for row in matches if row['status'] == 'ready_for_review']
    if len(passed) == 1:
        return passed[0]
    if passed:
        return _pending(target, '多份实看通过候选，须明确本次交接身份')
    return {'target': target, 'status': 'blocked', 'issue': '本目标的候选均有阻塞，见原回执'}


def summarize_asset_batch_receipt(project: Path, receipt_path: Path, targets: list[str], *, binding=None) -> dict:
    """Read only exact project-bound AST outputs, without turning self-check into approval."""
    targets = validate_asset_batch_targets(targets)
    project = project.resolve()
    receipt = _inside(project, receipt_path)
    if receipt.suffix.lower() != '.json':
        raise ValueError('资产实看回执须指定项目内的 JSON 文件')
    issue = None
    try:
        document = read_json(receipt)
    except FileNotFoundError:
        issue = '实看回执尚未落盘'
    except (OSError, ValueError):
        issue = '实看回执尚不可读'
    if issue is None:
        rows = document.get('items') if isinstance(document, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            issue = '回执没有可识别的逐项实看记录'
        elif binding is not None and any(document.get(key) != binding[key]
                                         for key in ('context_id', 'request_sha256')):
            issue = '实看回执与本批当前请求不符，待执行者交回当前结果'
    if issue:
        return {'receipt_path': str(receipt), 'status': 'pending', 'complete': False,
                'items': [{'target': item, 'status': 'pending'} for item in targets], 'issue': issue}

    items = [_target_result(project, item, rows, document.get('observer')) for item in targets]
    statuses = {item['status'] for item in items}
    status = 'pending' if 'pending' in statuses else 'blocked' if 'blocked' in statuses else 'ready_for_review'
    return {'receipt_path': str(receipt), 'status': status, 'complete': status == 'ready_for_review', 'items': items}
