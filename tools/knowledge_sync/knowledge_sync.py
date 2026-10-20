#!/usr/bin/env python3
"""Local export -> immutable candidate snapshot. No Mac connection or scheduler."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone


class SyncError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SyncError(f'Cannot read JSON: {path}: {exc}') from exc


def safe_path(root, relative, *, wiki_only=False):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise SyncError(f'Unsafe relative path: {relative!r}')
    parts = relative.split('/')
    if any(p in ('', '.', '..') for p in parts) or relative.startswith('/'):
        raise SyncError(f'Unsafe relative path: {relative!r}')
    if wiki_only and (parts[0] != 'wiki' or len(parts) < 2):
        raise SyncError(f'New source must be under wiki/: {relative}')
    root = Path(root).absolute()
    if any(parent.is_symlink() for parent in [root, *root.parents]):
        raise SyncError(f'Symlink root is not allowed: {root}')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise SyncError(f'Symlink is not allowed: {path}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise SyncError(f'Path escapes root: {relative}')
    return path


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SyncError(f'Symlink output: {path}')
    fd, temporary = tempfile.mkstemp(prefix='.writing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def snapshot_path(store, version):
    if not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{64}', version):
        raise SyncError('Snapshot ID must be its full 64-character content hash')
    return safe_path(store, 'snapshots/' + version)


def validate_snapshot(store, version):
    root = snapshot_path(store, version)
    manifest = read_json(safe_path(root, 'manifest.json'))
    identity = manifest.get('identity')
    if not isinstance(identity, dict) or sha(canonical(identity)) != version:
        raise SyncError('Snapshot manifest identity/hash mismatch')
    expected = set()
    for item in identity.get('files', []):
        path = safe_path(root / 'vault', item['path'])
        if not path.is_file() or sha(path.read_bytes()) != item['sha256']:
            raise SyncError(f'Snapshot bytes changed: {item["path"]}')
        expected.add(item['path'])
    actual = set()
    for path in (root / 'vault').rglob('*'):
        if path.is_symlink(): raise SyncError(f'Symlink in snapshot: {path}')
        if path.is_file(): actual.add(path.relative_to(root / 'vault').as_posix())
    if actual != expected:
        raise SyncError('Snapshot contains missing or undeclared files')
    return root, manifest


def inspect_export(export, *, expected_source_root):
    export = Path(export).absolute()
    if export.is_symlink(): raise SyncError('Symlink export root')
    data = read_json(safe_path(export, 'export.json'))
    if data.get('schema_version') != '1.0' or data.get('transport') not in ('authorized_mac_task', 'library_delivery', 'synthetic_fixture'):
        raise SyncError('Export requires explicit supported provenance; CLI never connects to Mac')
    if (not isinstance(expected_source_root, str) or
            not expected_source_root.startswith('/') or
            PurePosixPath(expected_source_root).name != 'wiki' or
            any(part in ('', '.', '..') for part in expected_source_root.split('/')[1:])):
        raise SyncError('Explicit expected source root must be an absolute wiki directory')
    if data.get('source_root') != expected_source_root:
        raise SyncError('Export source root differs from explicitly selected authorized wiki root')
    if data['transport'] != 'synthetic_fixture':
        try:
            stamp=datetime.fromisoformat(str(data.get('exported_at','')).replace('Z','+00:00'))
            if stamp.tzinfo is None: raise ValueError('timezone missing')
        except ValueError as exc: raise SyncError('Real export requires a timezone-qualified exported_at') from exc
    if type(data.get('complete')) is not bool or not isinstance(data.get('files'), list):
        raise SyncError('Export requires files and an explicit complete flag')
    if not isinstance(data.get('scope'), list) or not data['scope']:
        raise SyncError('Export requires a non-empty exact file scope')
    scope = set(); folded = set()
    for name in data['scope']:
        safe_path(export / 'payload', name, wiki_only=True)
        if unicodedata.normalize('NFC',name).casefold() in folded: raise SyncError('Duplicate/ambiguous scope path')
        scope.add(name); folded.add(unicodedata.normalize('NFC',name).casefold())
    records = {}; ids = set()
    for item in data['files']:
        if not isinstance(item, dict): raise SyncError('Invalid file record')
        name = item.get('path'); path = safe_path(export / 'payload', name, wiki_only=True)
        if name not in scope or name in records: raise SyncError('Duplicate or out-of-scope source')
        sid = item.get('source_id')
        if not isinstance(sid, str) or not sid or sid in ids: raise SyncError('Missing/duplicate source_id')
        ids.add(sid)
        if not path.is_file(): raise SyncError(f'Missing exported file: {name}')
        raw = path.read_bytes()
        if not isinstance(item.get('mtime'), (str,int,float)) or type(item.get('size')) is not int:
            raise SyncError('Source record requires mtime and integer size')
        if item.get('sha256') != sha(raw) or item.get('size') != len(raw):
            raise SyncError(f'Export bytes/hash mismatch: {name}')
        if not raw: raise SyncError(f'Empty source: {name}')
        records[name] = {**item, 'path': name}
    removals = data.get('removals', [])
    if not isinstance(removals, list): raise SyncError('removals must be an explicit list')
    for name in removals:
        safe_path(export / 'payload', name, wiki_only=True)
        if name not in scope or name in records: raise SyncError('Invalid removal scope')
    # Missing files are never inferred to be deletions. Completeness is a receipt,
    # not an instruction to delete; even complete exports require removals.
    if data['complete'] and scope != set(records) | set(removals):
        raise SyncError('Complete export has unexplained missing scope members')
    if removals and not data['complete']:
        raise SyncError('Incomplete export cannot retire sources')
    return data, records


def rule_inventory(vault, repo):
    # Reuse current project parser/uniqueness/metadata validation, no second index.
    sys.path.insert(0, str(Path(repo).resolve()))
    from scripts.core.knowledge_blocks import build_knowledge_index, load_policy
    try:
        blocks = build_knowledge_index(Path(vault), load_policy(Path(repo)))
    except (ValueError, OSError) as exc:
        raise SyncError(f'Rule conflict or invalid block: {exc}') from exc
    return [{'id': b.id, 'canonical_key': b.canonical_key, 'body_sha256': b.fingerprint,
             'path': b.vault_relative_path,
             'metadata_sha256': sha(canonical({k: getattr(b, k) for k in
                 ('id','canonical_key','kind','status','authority','projects','stages','topics','triggers','conflicts_with','priority','sources','applies_when')}))}
            for b in blocks]


def bind_source_root(store, expected_source_root):
    # One source root per store. Changing source requires a separate reviewed
    # store, never reusing another root's snapshots/provenance.
    path = safe_path(store, 'source-root.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    binding = {'source_root': expected_source_root}
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(binding, stream)
    except FileExistsError:
        if read_json(path) != binding:
            raise SyncError('Store is bound to a different source root')


def _build(store, export, repo, base=None, *, expected_source_root):
    store = Path(store).absolute(); safe_path(store, 'snapshots')
    data, incoming = inspect_export(export, expected_source_root=expected_source_root)
    if data.get('base_snapshot') not in (None, base):
        raise SyncError('Export base_snapshot differs from explicitly selected base')
    prior = {}; old_root = None; retired = []
    if base:
        old_root, old = validate_snapshot(store, base)
        prior = {r['path']: r for r in old['identity']['files']}
        retired = list(old['identity'].get('retired', []))
        if any(name.startswith('wiki/') for name in prior):
            if old['identity'].get('wiki_source_root') != expected_source_root:
                raise SyncError('Base wiki source root differs or is unbound; explicit migration review required')
    bind_source_root(store, expected_source_root)
    old_ids = {r['source_id']: name for name, r in prior.items()}
    for name, item in incoming.items():
        if name in prior and prior[name]['source_id'] != item['source_id']:
            raise SyncError(f'Source identity conflict at {name}')
        if item['source_id'] in old_ids and old_ids[item['source_id']] != name:
            raise SyncError('Source rename needs explicit review; not inferred from hash')
    if data.get('removals'):
        # First version deliberately refuses promotion of deletions rather than
        # trying to guess all project references. Old snapshots remain runnable.
        proposal={'status':'needs_dependency_review','base_snapshot':base,
                  'requested_paths':data['removals'],'source_records':[prior.get(n) for n in data['removals']],
                  'physical_deletion':False,'export_manifest_sha256':sha(canonical(data))}
        atomic_json(safe_path(store,'pending-retirements/'+sha(canonical(proposal))+'.json'),proposal)
        raise SyncError('Retirement requires dependency review; pending marker saved, no files deleted, old snapshot retained')
    merged = dict(prior)
    for name, item in incoming.items():
        merged[name] = {'path':name,'source_id':item['source_id'],'sha256':item['sha256'],'size':item['size']}
    folded_paths={}
    for name in merged:
        folded=unicodedata.normalize('NFC',name).casefold()
        if folded in folded_paths and folded_paths[folded]!=name:
            raise SyncError('Merged snapshot has case/Unicode-normalization path conflict')
        folded_paths[folded]=name
    # A no-op re-export reuses the same content version, even when mtime changed.
    store.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=store) as temporary:
        staging=Path(temporary); vault=staging/'vault';vault.mkdir()
        for name,item in merged.items():
            source = safe_path(Path(export)/'payload', name, wiki_only=True) if name in incoming else safe_path(old_root/'vault',name)
            target=safe_path(vault,name);target.parent.mkdir(parents=True,exist_ok=True)
            raw=source.read_bytes()
            if sha(raw)!=item['sha256']:raise SyncError('Source changed during copy')
            target.write_bytes(raw)
        rules=rule_inventory(vault,repo)
        identity={'files':sorted(merged.values(),key=lambda x:x['path']),'rules':rules,'retired':retired,'wiki_source_root':expected_source_root}
        version=sha(canonical(identity));destination=snapshot_path(store,version)
        existed=destination.exists()
        if existed:validate_snapshot(store,version)
        else:
            manifest={'schema_version':'1.0','identity':identity,'created_at':now(),
                      'base_snapshot':base,'provenance':data,'status':'candidate_structure_validated_only','production_readiness':'not_established','complete_wiki':False,'transport_note':'Local authorized export only; no live Mac connection'}
            atomic_json(staging/'manifest.json',manifest)
            destination.parent.mkdir(parents=True,exist_ok=True)
            try:os.rename(staging,destination)
            except OSError:
                if destination.exists():validate_snapshot(store,version)
                else:raise
        atomic_json(safe_path(store,'sync-status.json'),{'last_attempt':now(),'last_attempt_status':'success',
                    'last_success':now(),'last_available_snapshot':version,'source_export_time':data.get('exported_at'),
                    'live_mac_state':'not_checked','transport':data['transport']})
        return {'snapshot':version,'reused':existed,'files':len(merged),'rules':len(rules),'default_changed':False}


def build(store, export, repo, base=None, *, expected_source_root):
    try:
        return _build(store, export, repo, base, expected_source_root=expected_source_root)
    except (SyncError,OSError,KeyError,TypeError,ValueError) as exc:
        try:
            mark_failure(store, 'Local import failed: '+str(exc))
        except (SyncError,OSError,ValueError):
            pass  # Preserve the primary failure; never bypass an unsafe store.
        raise


def seed_baseline(store, source, manifest_path, repo):
    """Freeze an explicitly verified historical baseline; not a new wiki sync."""
    receipt=read_json(manifest_path)
    if receipt.get('kind')!='verified_P0_baseline' or not receipt.get('files'):
        raise SyncError('Baseline requires an explicit verified_P0_baseline receipt')
    store=Path(store).absolute();safe_path(store,'snapshots');store.mkdir(parents=True,exist_ok=True)
    files=[];names=set()
    with tempfile.TemporaryDirectory(prefix='.staging-baseline-',dir=store) as temporary:
        staging=Path(temporary);vault=staging/'vault';vault.mkdir()
        for row in receipt['files']:
            name=row['path']
            if not name.startswith('projects/Master-Storyboarding/') or name in names:
                raise SyncError('Baseline permits only unique, verified historical project paths')
            names.add(name);src=safe_path(source,name);raw=src.read_bytes()
            if sha(raw)!=row['sha256'] or len(raw)!=row['size']:
                raise SyncError('Baseline bytes differ from verification receipt')
            dst=safe_path(vault,name);dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
            files.append(dict(path=name,source_id='p0-recovery:'+name,sha256=sha(raw),size=len(raw)))
        identity={'files':sorted(files,key=lambda r:r['path']),'rules':rule_inventory(vault,repo),'retired':[]}
        version=sha(canonical(identity));destination=snapshot_path(store,version);existed=destination.exists()
        if existed:validate_snapshot(store,version)
        else:
            atomic_json(staging/'manifest.json',{'schema_version':'1.0','identity':identity,'created_at':now(),
                        'base_snapshot':None,'provenance':{'kind':'verified_P0_baseline','receipt':receipt,
                        'receipt_sha256':sha(Path(manifest_path).read_bytes()),'source_root':str(Path(source).resolve())},
                        'complete_wiki':False,'production_readiness':'not_established'})
            destination.parent.mkdir(parents=True,exist_ok=True);os.rename(staging,destination)
        return {'snapshot':version,'files':len(files),'reused':existed,'default_changed':False,'complete_wiki':False}


def now():
    return datetime.now(timezone.utc).isoformat()


def pin(store, version, project, task):
    root, manifest=validate_snapshot(store,version)
    if not task.strip():raise SyncError('task-id is required')
    project=str(Path(project).resolve())
    basis_path=Path(project)/'00_项目管理/执行上下文/task_basis.json'
    if basis_path.is_file():
        basis=read_json(basis_path);reading=basis.get('reading_manifest',{})
        if reading.get('task_id')==task and reading.get('vault_root')!=str(root/'vault'):
            raise SyncError('Existing task_basis pins another Vault; cannot switch this task')
    key=sha(canonical({'project':project,'task_id':task}))
    path=safe_path(store,'pins/'+key+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    record={'project':project,'task_id':task,'snapshot':version,'vault_root':str(root/'vault')}
    try:
        with path.open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False)
    except FileExistsError:
        if read_json(path)!=record:raise SyncError('Task is pinned to a different snapshot')
    return record


def status(store):
    path=safe_path(store,'sync-status.json')
    return read_json(path) if path.exists() else {'last_attempt_status':'unknown','live_mac_state':'not_checked'}


def mark_failure(store, reason):
    value=status(store);value.update(last_attempt=now(),last_attempt_status='failed',reason=reason,
                                    live_mac_state='not_checked',message='Keep the last verified cloud snapshot; source freshness is not confirmed')
    atomic_json(safe_path(store,'sync-status.json'),value);return value


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='action',required=True)
    a=s.add_parser('inspect');a.add_argument('--export',required=True);a.add_argument('--source-root',required=True)
    a=s.add_parser('build');a.add_argument('--source-root',required=True);a.add_argument('--store',required=True);a.add_argument('--export',required=True);a.add_argument('--repo',required=True);a.add_argument('--base')
    a=s.add_parser('seed-baseline');a.add_argument('--store',required=True);a.add_argument('--source',required=True);a.add_argument('--manifest',required=True);a.add_argument('--repo',required=True)
    a=s.add_parser('status');a.add_argument('--store',required=True)
    a=s.add_parser('sync-failed');a.add_argument('--store',required=True);a.add_argument('--reason',required=True)
    for command in ('pin','run'):
        a=s.add_parser(command);a.add_argument('--store',required=True);a.add_argument('--snapshot',required=True);a.add_argument('--project',required=True);a.add_argument('--task-id',required=True)
        if command=='run':a.add_argument('command',nargs=argparse.REMAINDER)
    args=p.parse_args()
    try:
        if args.action=='inspect':
            d,r=inspect_export(args.export, expected_source_root=args.source_root);result={'files':len(r),'complete':d['complete'],'transport':d['transport'],'mac_access':False}
        elif args.action=='build':result=build(args.store,args.export,args.repo,args.base, expected_source_root=args.source_root)
        elif args.action=='seed-baseline':result=seed_baseline(args.store,args.source,args.manifest,args.repo)
        elif args.action=='status':result=status(args.store)
        elif args.action=='sync-failed':result=mark_failure(args.store,args.reason)
        else:
            result=pin(args.store,args.snapshot,args.project,args.task_id)
            if args.action=='run':
                command=args.command[1:] if args.command[:1]==['--'] else args.command
                if not command:raise SyncError('run requires a command after --')
                for flag,want in [('--vault',result['vault_root']),('--project',str(Path(args.project).resolve())),('--task-id',args.task_id)]:
                    for i,arg in enumerate(command):
                        got=command[i+1] if arg==flag and i+1<len(command) else arg.split('=',1)[1] if arg.startswith(flag+'=') else None
                        if got is not None and got!=want:raise SyncError(f'Command conflicts with pinned {flag}')
                print(json.dumps({'pinned':result,'freshness':status(args.store)},ensure_ascii=False),flush=True)
                return subprocess.call(command,env={**os.environ,'MSB_VAULT_ROOT':result['vault_root']})
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (SyncError,OSError,KeyError,TypeError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 2


if __name__=='__main__':sys.exit(main())
