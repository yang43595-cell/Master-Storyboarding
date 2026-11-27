"""Collect exact candidate files for the user to inspect."""

import re
from pathlib import Path

from .manifest_store import project_path
from .io import read_json, sha256_file
from .media_types import MEDIA_EXTENSIONS


DIRECT_REQUESTS = Path('00_项目管理/执行上下文/直做图片请求')
DIRECT_RECEIPTS = Path('00_项目管理/执行上下文/直做图片回执')


def require_candidate_revision(previous, current):
    """An established candidate can keep its revision or advance within its numbered series."""
    if not previous or previous == current:
        return
    old = re.fullmatch(r"([vR])([0-9]+)", previous) if isinstance(previous, str) else None
    new = re.fullmatch(r"([vR])([0-9]+)", current) if isinstance(current, str) else None
    if not old or not new or old[1] != new[1] or int(new[2]) <= int(old[2]):
        raise ValueError("候选修订落后或无法与已登记版本比较")


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


def validate_asset_review_targets(targets):
    """The complete human review scope may span several execution batches."""
    if (not isinstance(targets, list) or not targets or not all(_target(item) for item in targets)
            or len(set(targets)) != len(targets)):
        raise ValueError('整轮 asset_review_targets 须列全不重复的准确 AST-ID@OUTPUT-ID')
    return list(targets)


def _inside(project, raw):
    path = project_path(project, raw)
    if path == project or not path.is_relative_to(project):
        raise ValueError('资产回执或成图路径越出项目')
    return path


def _field(row, current, historical):
    values = [row[key] for key in (current, historical) if row.get(key) is not None]
    return values[0] if values and all(value == values[0] for value in values) else None


def _pending(target, issue):
    return {'target': target, 'status': 'pending', 'issue': issue}


def _compact_issue(value, fallback='实看未通过，见原回执'):
    if not _identity(value) or any(char in value for char in '/\\<>`{}'):
        return fallback
    return value


def _candidate_row(project, target, row):
    problem = row.get('blocking_issue')
    if row.get('status') == 'blocked' and not _field(row, 'path', 'output_path'):
        return {'target': target, 'status': 'blocked', 'issue': _compact_issue(problem, '等待候选图片')}
    candidate = row.get('candidate_id')
    if not _identity(candidate):
        return _pending(target, '缺少候选版本')
    checked = {'target': target, 'candidate_id': candidate}
    if problem:
        checked['issue'] = _compact_issue(problem, '见候选记录')
    path, fingerprint = _field(row, 'path', 'output_path'), row.get('sha256')
    if (not isinstance(path, str) or not path.strip() or not isinstance(fingerprint, str)
            or not re.fullmatch(r'[A-Fa-f0-9]{64}', fingerprint)):
        return _pending(target, '候选记录缺准确图片路径或 SHA-256')
    try:
        local = _inside(project, path)
        if (local.suffix.lower() not in MEDIA_EXTENSIONS['image'] or not local.is_file()
                or not local.stat().st_size or sha256_file(local) != fingerprint.lower()):
            return _pending(target, '图片缺失、格式不受支持或文件 SHA-256 已改变')
    except (OSError, RuntimeError, ValueError):
        return _pending(target, '图片路径无法按当前项目核验')
    return {**checked, 'status': 'ready_for_review', 'path': str(local), 'sha256': fingerprint.lower()}


def _target_result(project, target, rows):
    matches = [row for row in rows if row.get('target') == target]
    if len(matches) != 1:
        return _pending(target, '请为当前输出指定一份准确候选')
    return _candidate_row(project, target, matches[0])


def summarize_asset_batch_receipt(project: Path, receipt_path: Path, targets: list[str], *, binding=None, document=None) -> dict:
    """Read only exact project-bound AST outputs, without turning self-check into approval."""
    targets = validate_asset_batch_targets(targets)
    project = project.resolve()
    receipt = _inside(project, receipt_path)
    if receipt.suffix.lower() != '.json':
        raise ValueError('资产候选记录须指定项目内的 JSON 文件')
    issue = None
    try:
        if document is None:
            document = read_json(receipt)
    except FileNotFoundError:
        issue = '候选记录尚未落盘'
    except (OSError, ValueError):
        issue = '候选记录尚不可读'
    if issue is None:
        rows = document.get('items') if isinstance(document, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            issue = '回执没有可识别的候选记录'
        elif binding is not None and any(document.get(key) != binding[key]
                                         for key in ('context_id', 'request_sha256')):
            issue = '候选记录与本批当前请求不符，待执行者交回当前结果'
    if issue:
        return {'receipt_path': str(receipt), 'status': 'pending', 'complete': False,
                'items': [{'target': item, 'status': 'pending'} for item in targets], 'issue': issue}

    items = [_target_result(project, item, rows) for item in targets]
    statuses = {item['status'] for item in items}
    status = 'pending' if 'pending' in statuses else 'blocked' if 'blocked' in statuses else 'ready_for_review'
    return {'receipt_path': str(receipt), 'status': status, 'complete': status == 'ready_for_review', 'items': items}
