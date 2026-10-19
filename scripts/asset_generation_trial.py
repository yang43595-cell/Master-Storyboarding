"""Explicit image-only trial bridge. Never relaxes candidate_test production guards.

The host executes image_gen after preflight; this script never calls a provider.
Authorization records evidence supplied by the caller, not inferred approval.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from PIL import Image
try:
    from .core import artifacts, candidate_test
    from .core.project_state import new_project_state, write_project_state
except ImportError:
    from core import artifacts, candidate_test
    from core.project_state import new_project_state, write_project_state

MODEL = 'platform-managed; exact model not disclosed'
FLAGS = dict(status='candidate', review_status='unreviewed', production_blocked=True, user_approved=False)

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def create(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)

def snap(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Expected existing file')
    return dict(path=str(path), sha256=artifacts.digest(path))

def verify(records):
    for record in records:
        if snap(record['path']) != record:
            raise ValueError('Source bytes changed: ' + record['path'])

def token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value):
        raise ValueError('Invalid stable run/attempt ID')
    return value

def root_path(root):
    root = Path(root).resolve()
    scope = read(root/'asset_generation_trial.json')
    if scope['trial_root'] != str(root):
        raise ValueError('Trial scope belongs to another directory')
    context = candidate_test.context(root)
    if not context or context['run_id'] != scope['run_id']:
        raise ValueError('Trial candidate identity missing or changed')
    verify(scope['source_snapshots'])
    return root, scope

def initialize(root, queue_path, authorization_path):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Use a new empty isolated trial directory')
    auth = read(authorization_path)
    required = ['run_id','message_id','quote','tool','model_disclosure','allowed_queue_ids']
    if any(not auth.get(key) for key in required):
        raise ValueError('Missing explicit authorization evidence or exact scope')
    if auth['tool'] != 'image_gen' or auth['model_disclosure'] != MODEL:
        raise ValueError('Only disclosed host-managed image_gen is supported')
    token(auth['run_id'])
    queue = read(queue_path)
    ids = auth['allowed_queue_ids']
    if not isinstance(ids,list) or len(set(ids)) != len(ids):
        raise ValueError('Exact unique queue IDs required')
    indexed = {item['queue_id']:item for item in queue['items']}
    if not set(ids).issubset(indexed):
        raise ValueError('Unknown queue ID')
    scoped = {key:indexed[key] for key in ids}
    snapshots = {str(Path(queue_path).resolve()):snap(queue_path)}
    for item in scoped.values():
        for key in ('source_manifest','source_narrative'):
            record = snap(item[key]); snapshots[record['path']] = record
    scope = dict(schema='asset-generation-trial-v1', trial_root=str(root),run_id=auth['run_id'],
                 authorization=auth, source_snapshots=list(snapshots.values()), items=scoped, **FLAGS)
    root.mkdir(parents=True,exist_ok=True)
    write_project_state(root,new_project_state(current_stage='asset-image-assistant'))
    candidate_test.configure(root,auth['run_id'],auth['message_id'],[])
    create(root/'asset_generation_trial.json',scope)
    return dict(trial_root=str(root),run_id=auth['run_id'],allowed_queue_ids=ids,**FLAGS)

def attempt_folder(root, ident):
    folder = root/'attempts'/token(ident)
    if folder.parent.is_symlink() or folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError('Trial output path escapes isolated directory')
    return folder

def baseline_rejected(folder, receipt):
    """Append-only QA rejection invalidates reuse without rewriting old self-checks."""
    for path in (folder/'review-overrides').glob('*.json'):
        override = read(path)
        if (override.get('image_sha256') != receipt.get('image', {}).get('sha256')
                or override.get('passed') is not False or not override.get('notes')):
            raise ValueError('Invalid review override; reference blocked pending review')
        return True
    return False

def check_reference_reviews(root, references):
    for path in (root/'attempts').glob('*/result.json'):
        receipt = read(path)
        if receipt.get('image') in references and baseline_rejected(path.parent, receipt):
            raise ValueError('Reference baseline rejected by later visual review')

def validate_reference_amendment(root, scope, record):
    """Only rebind an exact, already frozen original within the same source case."""
    if (record.get('trial_root') != str(root) or record.get('run_id') != scope['run_id']
            or record.get('scope_sha256') != artifacts.digest(root/'asset_generation_trial.json')):
        raise ValueError('Reference amendment belongs to another scope')
    target = scope['items'].get(record.get('target_queue_id'))
    donor = scope['items'].get(record.get('donor_queue_id'))
    if (not target or not donor or target.get('source_narrative') != donor.get('source_narrative')
            or target.get('case_id') != donor.get('case_id')):
        raise ValueError('Reference amendment must remain in the same frozen source case')
    for key in ('visual_purpose', 'visual_observation', 'authorization_ref'):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError('Reference amendment requires actual visual review and authority evidence')
    reference = record.get('reference')
    if not isinstance(reference, dict) or set(reference) != {'path','sha256'}:
        raise ValueError('Exact original reference path and hash required')
    if not any(r.get('exists') and r.get('resolved_path')
               and str(Path(r['resolved_path']).resolve()) == reference['path']
               and r.get('sha256') == reference['sha256'] for r in donor.get('references', [])):
        raise ValueError('Amendment reference is not an exact frozen donor original')
    verify([reference])
    with Image.open(reference['path']) as image:
        image.verify()


def amend_reference(root, request):
    root, scope = root_path(root)
    ident = token(request['amendment_id'])
    record = {key: request.get(key) for key in
              ('target_queue_id','donor_queue_id','reference','visual_purpose','visual_observation','authorization_ref')}
    record.update(amendment_id=ident,trial_root=str(root),run_id=scope['run_id'],
                  scope_sha256=artifacts.digest(root/'asset_generation_trial.json'))
    validate_reference_amendment(root, scope, record)
    folder = root/'reference-amendments'
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError('Reference amendment directory escapes trial')
    create(folder/(ident+'.json'), record)
    return record


def amended_references(root, scope, queue_id):
    references, evidence = {}, []
    folder = root/'reference-amendments'
    if folder.is_symlink():
        raise ValueError('Reference amendment directory must not be a symlink')
    for path in sorted(folder.glob('*.json')):
        if path.is_symlink():
            raise ValueError('Reference amendment must not be a symlink')
        record = read(path)
        validate_reference_amendment(root, scope, record)
        if record['target_queue_id'] == queue_id:
            reference = record['reference']
            references[reference['path']] = reference['sha256']
            evidence.append(snap(path))
    return references, evidence

def preflight(root, request):
    root, scope = root_path(root)
    ident = token(request['attempt_id'])
    item = scope['items'].get(request['queue_id'])
    if not item:
        raise ValueError('Asset outside explicit trial scope')
    baseline_role = request.get('baseline_role')
    if baseline_role not in (None, 'face', 'wardrobe', 'scene_master', 'object_master'):
        raise ValueError('Invalid independent baseline role')
    prompt = request.get('prompt')
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError('Exact prompt required')
    if request.get('tool','image_gen') != 'image_gen' or request.get('count',1) != 1:
        raise ValueError('One host image_gen result per attempt')
    if not request.get('method_evidence') or not request.get('visual_inspection'):
        raise ValueError('Method reading and actual reference inspection evidence required')
    methods = [snap(p) for p in request['method_evidence']]
    allowed = {str(Path(r['resolved_path']).resolve()):r.get('sha256')
               for r in item.get('references',[]) if r.get('exists') and r.get('resolved_path')}
    additions, amendment_evidence = amended_references(root, scope, request['queue_id'])
    allowed.update(additions)
    refs = []
    for raw in request.get('reference_paths',[]):
        record = snap(raw)
        if record['path'] in allowed:
            if allowed[record['path']] and allowed[record['path']] != record['sha256']:
                raise ValueError('Bound original reference has changed')
        else:
            matching = []
            for receipt_path in (root/'attempts').glob('*/result.json'):
                receipt = read(receipt_path)
                if (receipt.get('image') == record and receipt.get('assessment',{}).get('passed') is True
                    and receipt.get('queue_id') == request['queue_id'] and receipt.get('outcome') == 'qualified_candidate'
                    and receipt.get('baseline_role') in {'face','wardrobe','scene_master','object_master'}
                    and not baseline_rejected(receipt_path.parent, receipt)):
                    matching.append(receipt)
            if baseline_role is not None:
                raise ValueError('Independent baselines must return to bound original references')
            if len(matching) != 1:
                raise ValueError('Reference is neither bound original nor qualified same-trial asset baseline')
        with Image.open(record['path']) as image:
            image.verify()
        refs.append(record)
    if allowed and not refs:
        raise ValueError('Bound original references cannot be silently omitted')
    record = dict(attempt_id=ident,queue_id=request['queue_id'],stage=request.get('stage','asset'),
                  baseline_role=baseline_role,reference_roles=request.get('reference_roles', []),
                  prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                  reference_amendments=amendment_evidence,references_before=refs,methods=methods,visual_inspection=request['visual_inspection'],
                  tool='image_gen',model_disclosure=MODEL,count=1,
                  authorization_ref=scope['authorization']['message_id'],state='reserved_for_one_host_call',**FLAGS)
    folder = attempt_folder(root, ident)
    folder.mkdir(parents=True,exist_ok=False)
    create(folder/'request.json',record)
    return dict(attempt=str(folder),request=record,instruction='Execute image_gen once with this exact prompt and references; never retry an uncertain call. Finish with the actual output and visual assessment.')

def finish(root, attempt_id, image_path, assessment):
    root, scope = root_path(root)
    folder = attempt_folder(root, attempt_id)
    request = read(folder/'request.json')
    if (folder/'result.json').exists() or (folder/'completion.lock').exists():
        raise ValueError('Attempt already completed or completion interrupted; do not overwrite')
    verify(request['references_before']); verify(request['methods'])
    verify(request.get('reference_amendments', []))
    check_reference_reviews(root, request['references_before'])
    if not isinstance(assessment.get('passed'),bool) or not assessment.get('notes'):
        raise ValueError('Explicit visual self-check result and observations required')
    source = Path(image_path).resolve(strict=True)
    with Image.open(source) as image:
        image.verify()
    with Image.open(source) as image:
        dimensions = list(image.size)
    source_record = snap(source)
    if source_record['sha256'] in {r['sha256'] for r in request['references_before']}:
        raise ValueError('A copied reference is not a newly generated asset')
    if source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
        raise ValueError('Unsupported image format')
    create(folder/'completion.lock',{'state':'copying'})
    destination = folder/('candidate'+source.suffix.lower())
    with source.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src,dst)
    if artifacts.digest(destination) != source_record['sha256']:
        raise ValueError('Output copy checksum mismatch')
    verify(scope['source_snapshots']); verify(request['references_before'])
    receipt = dict(attempt_id=attempt_id,queue_id=request['queue_id'],baseline_role=request.get('baseline_role'),image=snap(destination),
                   source_image=source_record,dimensions=dimensions,assessment=assessment,
                   references_after=[snap(r['path']) for r in request['references_before']],
                   prompt_sha256=request['prompt_sha256'],tool=request['tool'],model_disclosure=MODEL,
                   authorization_ref=request['authorization_ref'],
                   outcome='qualified_candidate' if assessment['passed'] else 'failed_candidate',**FLAGS)
    create(folder/'result.json',receipt)
    # Existing registration propagates all test-only flags; no current/approval writes.
    artifacts.register_workspace_artifact(root,dict(artifact_id=attempt_id,logical_id='asset-trial.'+attempt_id,
        type='visual-asset-trial',path_or_url=str((folder/'result.json').relative_to(root)),revision='R01'))
    return receipt

def fail(root, attempt_id, reason):
    root, _ = root_path(root)
    folder = attempt_folder(root, attempt_id)
    request = read(folder/'request.json')
    if not reason or (folder/'result.json').exists():
        raise ValueError('Reason required; cannot overwrite completed attempt')
    receipt = dict(attempt_id=attempt_id,queue_id=request['queue_id'],outcome='generation_failed_or_unknown',reason=reason,**FLAGS)
    create(folder/'result.json',receipt)
    return receipt

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','preflight','finish','fail','amend-reference'])
    parser.add_argument('--trial-root',required=True)
    parser.add_argument('--queue'); parser.add_argument('--authorization-file'); parser.add_argument('--request')
    parser.add_argument('--attempt-id'); parser.add_argument('--image'); parser.add_argument('--assessment'); parser.add_argument('--reason')
    args = parser.parse_args()
    if args.action == 'init': result = initialize(args.trial_root,args.queue,args.authorization_file)
    elif args.action == 'amend-reference': result = amend_reference(args.trial_root,read(args.request))
    elif args.action == 'preflight': result = preflight(args.trial_root,read(args.request))
    elif args.action == 'finish': result = finish(args.trial_root,args.attempt_id,args.image,read(args.assessment))
    else: result = fail(args.trial_root,args.attempt_id,args.reason)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
