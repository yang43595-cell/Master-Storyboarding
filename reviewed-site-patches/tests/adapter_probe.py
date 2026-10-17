"""Thin stdin/stdout caller of the unchanged pinned project adapter. No parser copy."""
import json
from pathlib import Path
import sys

job = json.load(sys.stdin)
sys.path.insert(0, str(Path(job['adapterRoot']).resolve()))
from scripts.core.web_content import compile_content, effective_feedback
try:
    if job['action'] == 'compile':
        value = compile_content(job['raw'].encode('utf-8'), stage='assets',
            case_id='01', source_script_version=job['sourceScriptVersion'],
            expected_version=job['expectedVersion'], scope='live',
            media_registry=job['mediaRegistry'], reviewable=True,
            contract_root=job['contractRoot'], project_root=Path(job['projectRoot']))
    elif job['action'] == 'effective':
        value = effective_feedback(job['response'], scope=job['scope'], case_id=job['caseId'],
                                   stage=job['stage'], version=job['version'])
    else:
        raise ValueError('Unknown local test operation')
    print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
except Exception as error:
    print(json.dumps({'ok': False, 'errorType': type(error).__name__, 'error': str(error)}, ensure_ascii=False))
