"""Narrow, read-only comparison of frozen draft payloads and editor readback.

This consumes evidence from a platform-specific reader; it does not invent a
remote backend or infer editor persistence from graph edges. No network/writes.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

if __package__:
    from .core import canvas_readback as readback
    from .core.delivery_files import check_package_files
    from .session_usage import checkpoint
else:
    from core import canvas_readback as readback
    from core.delivery_files import check_package_files
    from session_usage import checkpoint


def __getattr__(name):
    return getattr(readback, name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--readback', type=Path)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--preflight', action='store_true', help='只读核对现有交付 JSON，无远端操作')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--private-preview', action='store_true', help='仅显式授权的私有候选参考与未生成草稿；不授予权限或改变确认状态')
    modes.add_argument('--delivery', action='store_true',
                        help='正式交付：强制当前项目与修订核验；不替代人工动作自查或生成授权')
    parser.add_argument('--package', type=Path, action='append', default=[], help='交付文件包时必传实际成品路径，可重复；严格检查实际字节')
    parser.add_argument('--max-package-bytes', type=int, default=19_000_000, help='每包字节数必须严格小于此值')
    parser.add_argument('--baseline', type=Path, help='更新前最后可信的完整刷新回读')
    parser.add_argument('--current', type=Path, help='写前实际完整节点回读，phase=before_write')
    parser.add_argument('--usage-log', action='append')
    parser.add_argument('--usage-turn-id')
    parser.add_argument('--usage-boundaries-json')
    parser.add_argument('--max-input', type=int)
    parser.add_argument('--max-total', type=int)
    parser.add_argument('--max-requests', type=int)
    args = parser.parse_args()
    if args.preflight and args.readback is not None:
        parser.error('--preflight only checks local inputs; use --readback without --preflight for actual binding comparison')
    try:
        usage = checkpoint(args)
        if bool(args.baseline) != bool(args.current) or (args.baseline and not args.preflight):
            raise ValueError('--baseline and --current must be paired in --preflight')
        frozen = json.loads(args.frozen.read_text(encoding='utf-8'))
        baseline_check = None
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
            current = json.loads(args.current.read_text(encoding='utf-8'))
            if (readback.target_url(current['target_url']) != readback.target_url(frozen['target_url'])
                    or current.get('target_project_id') != frozen.get('target_project_id')
                    or set(readback.rows_by_id(current['drafts'])) != set(readback.rows_by_id(frozen['drafts']))):
                raise ValueError('write baseline does not cover the exact frozen target/nodes')
            baseline_check = readback.compare_baseline(baseline, current)
            if not baseline_check['ok']:
                print(json.dumps({'ok': False, 'baseline_check': baseline_check}, ensure_ascii=False))
                return 2
        if args.delivery or args.private_preview:
            if args.project is None:
                raise ValueError('selected delivery/preview mode requires --project; legacy comparison is insufficient')
            if not isinstance(frozen.get('revision'), str) or not frozen['revision'].strip():
                raise ValueError('selected delivery/preview mode requires exact submission revision')
        if args.preflight:
            if args.project is None:
                parser.error('--preflight requires --project')
            result = readback.validate_submission(frozen, args.project, private_preview=args.private_preview,
                                         formal_delivery=args.delivery, preflight=True)
        else:
            if args.readback is None:
                parser.error('--readback required unless --preflight')
            result = readback.compare(frozen, json.loads(args.readback.read_text(encoding='utf-8')), args.project,
                             private_preview=args.private_preview, formal_delivery=args.delivery)
        if args.package:
            result['packages'] = check_package_files(args.package, args.max_package_bytes)
        if baseline_check is not None:
            result['baseline_check'] = baseline_check
        if usage is not None:
            result['run_usage'] = usage
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
