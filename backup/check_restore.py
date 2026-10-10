#!/usr/bin/env python3
"""Read-only verification of this historical source and dependent-knowledge backup."""
from pathlib import Path
import hashlib, json, os, runpy, sys

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / 'knowledge_snapshot' / 'vault'
os.environ['MSB_VAULT_ROOT'] = str(VAULT)
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

def resolve_snapshot_reference(target):
    """Prefer exact filenames (including literal #); only then treat # as anchor."""
    exact = VAULT / target
    if exact.exists(): return exact
    parts = target.split('#')
    for stop in range(len(parts)-1, 0, -1):
        candidate = VAULT / '#'.join(parts[:stop])
        if candidate.exists(): return candidate
    return VAULT / parts[0]

def verify():
    manifest = json.loads((ROOT / 'backup/source_manifest.json').read_text())
    errors = []
    complete = ROOT / 'backup/FILE_SHA256.json'
    if complete.is_file():
        for name, expected in json.loads(complete.read_text()).items():
            p = ROOT / name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                errors.append('complete backup mismatch: ' + name)
    for row in manifest['files']:
        p = ROOT / row['path']
        if not p.is_file():
            errors.append('missing file: ' + row['path'])
        elif hashlib.sha256(p.read_bytes()).hexdigest() != row['sha256']:
            errors.append('hash mismatch: ' + row['path'])
    methods = []
    templates = []
    for profile in (ROOT / 'skills').glob('*/knowledge-profile.json'):
        data = json.loads(profile.read_text())
        for method in data.get('professional_methods', []):
            p = VAULT / method['path']
            if not p.is_file():
                errors.append('missing bound method: ' + method['path'])
            else:
                methods.append(method['path'])
    data = json.loads((ROOT / 'skills/asset-image-assistant/library-profile.json').read_text())
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == 'template' and isinstance(v, str): templates.append(v)
                else: walk(v)
        elif isinstance(value, list):
            for x in value: walk(x)
    walk(data)
    for template in sorted(set(templates)):
        if not (VAULT / 'wiki/reference/提示词模板' / template).is_file():
            errors.append('missing runtime asset template: ' + template)
    refs = json.loads((ROOT / 'backup/knowledge_references.json').read_text())
    for ref in refs:
        if ref['status'].startswith('included'):
            target = ref['resolved']
            if not resolve_snapshot_reference(target).exists():
                errors.append('missing included reference: ' + target)
    literal = [x['target'] for x in refs if 'literal filename #' in x['status']]
    for target in literal:
        if resolve_snapshot_reference(target) != VAULT / target:
            errors.append('literal # filename regression: ' + target)
        if resolve_snapshot_reference(target + '#test-heading') != VAULT / target:
            # A fragment on a filename containing # requires longest existing prefix.
            errors.append('literal filename plus anchor regression: ' + target)
    audit = runpy.run_path(str(ROOT / 'scripts/audit-knowledge-entrypoints.py'))['audit'](ROOT, VAULT)
    errors.extend(audit.get('errors', []))
    result = {'ok': not errors, 'scope': 'historical source + pinned project knowledge, not latest production',
              'source_files_verified': len(manifest['files']), 'method_bindings': len(methods),
              'unique_method_files': len(set(methods)), 'runtime_templates': len(set(templates)),
              'knowledge_audit': audit['summary'], 'errors': errors,
              'known_limits': ['cloud-knowledge-manual.md original bytes unavailable',
                              'tutorial raw attachments and some historical production evidence are external/unrecovered',
                              'media must be restored separately using external asset manifests',
                              'production commit 6120 and dirty changes are not reconstructed']}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return not errors

if __name__ == '__main__':
    raise SystemExit(0 if verify() else 1)
