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

def generated_records(root):
    return {r['image']['path']: (path.parent, r) for path in (root/'attempts').glob('*/result.json')
            if (r := read(path)).get('image')}


def verify_attempt_methods(folder, request, image=None):
    """An explicit new review may replace method validity, never historical bytes."""
    current = [snap(r['path']) for r in request['methods']]
    if current == request['methods']:
        return
    for path in sorted((folder/'method-revalidations').glob('*.json')):
        evidence = read(path)
        if (evidence.get('source_request') != snap(folder/'request.json')
                or evidence.get('historical_methods') != request['methods']
                or evidence.get('current_methods') != current):
            continue
        verify([evidence['image']])
        verify(request['references_before'])
        if image is not None and evidence['image']['sha256'] != image['sha256']:
            raise ValueError('Method revalidation image differs from actual output')
        if any(not isinstance(evidence.get(k), str) or not evidence[k].strip()
               for k in ('observer', 'authorization_ref', 'method_observation', 'visual_observation')):
            raise ValueError('Method revalidation requires actual reading and image observations')
        return
    verify(request['methods'])


def revalidate_methods(root, review):
    """Append a current-method and exact-image review; no generation or approval."""
    root, _ = root_path(root)
    folder = attempt_folder(root, review['attempt_id'])
    request = read(folder/'request.json')
    for key in ('observer', 'authorization_ref', 'method_observation', 'visual_observation'):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError('Explicit authority, current method reading and visual review required')
    if review.get('passed') is not True:
        raise ValueError('A failed or unknown image cannot be method-revalidated')
    image = snap(review['image_path'])
    with Image.open(image['path']) as source:
        source.verify()
    if (folder/'result.json').exists():
        receipt = read(folder/'result.json')
        if (receipt.get('outcome') != 'qualified_candidate'
                or receipt.get('assessment', {}).get('passed') is not True
                or receipt.get('image') != image or baseline_rejected(folder, receipt)):
            raise ValueError('Only an unchanged qualified image can be revalidated')
    elif (folder/'completion.lock').exists():
        raise ValueError('Interrupted completion requires separate recovery')
    if image['sha256'] in {r['sha256'] for r in request['references_before']}:
        raise ValueError('A reference copy cannot be revalidated as generated output')
    verify(request['references_before'])
    current = [snap(r['path']) for r in request['methods']]
    if current == request['methods']:
        raise ValueError('No actual method drift to revalidate')
    if review.get('current_methods') != current or review.get('historical_methods') != request['methods']:
        raise ValueError('Exact historical and current method snapshots required')
    record = dict(source_request=snap(folder/'request.json'), image=image,
                  historical_methods=request['methods'], current_methods=current,
                  **{key: review[key] for key in
                     ('observer', 'authorization_ref', 'method_observation', 'visual_observation')},
                  **FLAGS)
    create(folder/'method-revalidations'/(token(review['review_id'])+'.json'), record)
    return record


def check_reference_reviews(root, references):
    records = generated_records(root)
    def visit(reference, visiting):
        if reference['path'] not in records:
            return -1
        folder, receipt = records[reference['path']]
        if reference['path'] in visiting:
            raise ValueError('Generated reference dependency cycle')
        if receipt['image'] != reference or snap(reference['path']) != reference:
            raise ValueError('Generated reference bytes changed')
        if (receipt.get('outcome') != 'qualified_candidate'
                or receipt.get('assessment', {}).get('passed') is not True
                or baseline_rejected(folder, receipt)):
            raise ValueError('Reference baseline rejected by later visual review')
        request = read(folder/'request.json')
        verify(request['references_before'])
        verify_attempt_methods(folder, request, receipt['image'])
        external = generated_reference_context(root, root_path(root)[1], request, request['references_before'])
        if external != request.get('generated_reference_context', []):
            raise ValueError('Generated reference evidence changed')
        parents = [visit(ref, visiting | {reference['path']}) for ref in request['references_before']]
        depth = 1 + max(parents + [edge['depth'] for edge in external], default=-1)
        layout = scene_layout_revision_context(root, root_path(root)[1], request, request['references_before'])
        if layout != request.get('scene_layout_revision_context'):
            raise ValueError('Scene layout revision lineage changed')
        if layout:
            # Retain and verify the edit lineage above, while the reviewed new
            # master starts the separate, finite scene-view method graph.
            depth = 0
        if depth > 2:
            raise ValueError('Generated reference dependency exceeds two edges')
        if request.get('dependency_role'):
            dependency = dependency_context(root, request, request['references_before'])
            if dependency != request.get('dependency'):
                raise ValueError('Ancestor method dependency evidence changed')
            depth = max(depth, dependency['depth'])
        return depth
    return {ref['path']: visit(ref, set()) for ref in references}


CHARACTER_BOARD_LAYOUT = {
    'canvas': '16:9', 'panels': '1x4',
    'left': 'head_only_neck_shoulder_boundary',
    'right': 'headless_front_side_back',
}

DEPENDENCY_ROLES = {'face','wardrobe_front','wardrobe_side','wardrobe_back','wardrobe_three_view',
                    'identity_board','scene_master','scene_reverse','scene_oblique','scene_top'}


def original_dependency_role(root, original, receipt):
    expected = original.get('dependency_role') or {'face':'face','wardrobe':'wardrobe_front',
               'scene_master':'scene_master','wardrobe_three_view':'wardrobe_three_view'}.get(original.get('baseline_role'))
    if not expected and original.get('stage') in {'scene_master','scene_reverse','scene_oblique'}:
        expected = original['stage']
    # Exact historical scene spellings only; retain the original frozen request.
    legacy_scene_roles = {'reverse': 'scene_reverse', 'high45': 'scene_oblique'}
    if not expected and original.get('stage') in legacy_scene_roles:
        records = generated_records(root)
        parents = [records[ref['path']][1] for ref in original['references_before'] if ref['path'] in records]
        if (len(parents) != 1 or parents[0].get('baseline_role') != 'scene_master'
                or parents[0].get('queue_id') != receipt.get('queue_id')):
            raise ValueError('Legacy scene alias must derive from its own scene master')
        expected = legacy_scene_roles[original['stage']]
    return expected


def approve_dependency(root, request):
    """Bind an exact method use with the explicitly supplied kind of visual review."""
    root, _ = root_path(root)
    folder = attempt_folder(root, request['attempt_id'])
    receipt, original = read(folder/'result.json'), read(folder/'request.json')
    if (original.get('attempt_id') != request['attempt_id']
            or receipt.get('attempt_id') != request['attempt_id']
            or receipt.get('queue_id') != original.get('queue_id')):
        raise ValueError('Method review must match the exact original attempt and asset')
    check_reference_reviews(root, [receipt['image']])
    role = request['dependency_role']
    if role not in DEPENDENCY_ROLES - {'identity_board','scene_top'}:
        raise ValueError('Only nonterminal method roles can be reference-approved')
    expected = original_dependency_role(root, original, receipt)
    if role != expected:
        raise ValueError('Review role does not match original generation role')
    wardrobe_set = request.get('wardrobe_set')
    if role == 'wardrobe_three_view':
        if (not wardrobe_set or request.get('independent_single_view') is True
                or request.get('headless_three_view') is not True):
            raise ValueError('Three-view wardrobe requires exact set and reviewed headless three-view layout')
    elif role.startswith('wardrobe_') and (not wardrobe_set or request.get('independent_single_view') is not True):
        raise ValueError('Wardrobe role requires exact set and reviewed independent single view')
    if original.get('wardrobe_set') and original['wardrobe_set'] != wardrobe_set:
        raise ValueError('Wardrobe set differs from generation request')
    approval = dict(attempt_id=request['attempt_id'],image=receipt['image'],dependency_role=role,
                    wardrobe_set=wardrobe_set,independent_single_view=request.get('independent_single_view',False),
                    **({'headless_three_view':True} if role == 'wardrobe_three_view' else {}))
    review_kind = request.get('review_kind', 'independent_qa')
    if review_kind == 'producer_self_check':
        if (any(not isinstance(request.get(k), str) or not request[k].strip()
                for k in ('observer', 'visual_observation'))
                or request.get('image_sha256') != receipt['image']['sha256']):
            raise ValueError('Producer self-check requires actual observer, observation and exact image hash')
        approval.update(review_kind=review_kind,
                        role_review=request.get('role_review') or request['visual_observation'],
                        self_check=dict(observer=request['observer'],visual_observation=request['visual_observation'],
                                        image_sha256=request['image_sha256'],passed=True),
                        source_request=snap(folder/'request.json'),source_result=snap(folder/'result.json'))
    elif review_kind == 'independent_qa':
        index = read(request['review_index'])
        matches = [r for r in index['items'] if r.get('attempt_id') == request['attempt_id']
                   and r.get('reviewed_path') == receipt['image']['path']
                   and r.get('image_sha256') == receipt['image']['sha256'] and r.get('status') == 'passed']
        if len(matches) != 1 or not request.get('role_review'):
            raise ValueError('Exact independent passed review and explicit role review required')
        approval.update(review_kind=review_kind,role_review=request['role_review'],independent_qa=matches[0],
                        review_index_at_binding=snap(request['review_index']))
    else:
        raise ValueError('Unsupported dependency review kind')
    create(folder/'dependency-approval.json',approval)
    return approval


def external_method_sources(root, request, references):
    """Exact cross-batch baselines, admitted only for a terminal character board."""
    bindings = request.get('method_reference_bindings', [])
    if not bindings:
        return {}
    if (not isinstance(bindings, list) or len(bindings) != 2 or len(references) != 2
            or request.get('dependency_role') != 'identity_board'
            or request.get('baseline_role') or request.get('generated_reference_bindings')
            or request.get('output_kind') != '人物1x4'
            or {b.get('role') for b in bindings} != {'face','wardrobe_three_view'}):
        raise ValueError('Cross-trial methods require exactly HEAD and wardrobe baselines for a final 人物1x4')
    root, scope = root_path(root)
    target = scope['items'][request['queue_id']]
    sources = {}
    for binding in bindings:
        source_root, source_scope = root_path(binding['source_trial_root'])
        if source_root == root:
            raise ValueError('Explicit cross-trial method binding must use another trial')
        folder = attempt_folder(source_root, binding['attempt_id'])
        original, receipt = read(folder/'request.json'), read(folder/'result.json')
        donor = source_scope['items'].get(original.get('queue_id'), {})
        if (not target.get('asset_id') or target['asset_id'] != donor.get('asset_id')
                or not target.get('source_narrative')
                or target['source_narrative'] != donor.get('source_narrative')):
            raise ValueError('Cross-trial methods must retain the same AST and source narrative')
        narrative = snap(target['source_narrative'])
        if any(narrative not in value['source_snapshots'] for value in (scope, source_scope)):
            raise ValueError('Cross-trial methods require the exact frozen narrative')
        role = binding['role']
        image = receipt.get('image')
        if (original.get('attempt_id') != binding['attempt_id']
                or receipt.get('attempt_id') != binding['attempt_id']
                or receipt.get('queue_id') != original.get('queue_id')
                or original.get('baseline_role') != role or original.get('dependency_role')
                or original.get('method_reference_bindings')
                or receipt.get('baseline_role') != role
                or receipt.get('prompt_sha256') != original.get('prompt_sha256')
                or not image or image not in references
                or Path(image['path']).parent != folder or Path(image['path']).stem != 'candidate'
                or snap(image['path']) != image):
            raise ValueError('Cross-trial source must be the exact native newly generated method baseline')
        approval = read(folder/'dependency-approval.json')
        if approval.get('review_kind') != 'producer_self_check' or approval.get('dependency_role') != role:
            raise ValueError('Cross-trial method baseline requires exact producer self-check approval')
        if role == 'wardrobe_three_view' and (not request.get('wardrobe_set')
                or original.get('wardrobe_set') != request['wardrobe_set']):
            raise ValueError('Cross-trial wardrobe set must match the exact requested clothing state')
        if image['path'] in sources:
            raise ValueError('Duplicate cross-trial method source')
        sources[image['path']] = (source_root, source_scope, folder, receipt, binding)
    if set(sources) != {ref['path'] for ref in references}:
        raise ValueError('Cross-trial method bindings must account for exactly both references')
    return sources


def dependency_context(root, request, references):
    role = request.get('dependency_role')
    if role not in DEPENDENCY_ROLES:
        raise ValueError('Explicit method dependency role required')
    records = generated_records(root)
    external = external_method_sources(root, request, references)
    depths = check_reference_reviews(root, references)
    edges = []
    if role == 'identity_board':
        _, scope = root_path(root)
        item = scope['items'][request['queue_id']]
        face = item.get('independent_face_reference')
        if face is not None:
            if (not isinstance(face, dict) or not face.get('index_path') or not face.get('library_asset_id')
                    or external or len(references) != 2):
                raise ValueError('Library face boards require exactly one independent face and one same-trial wardrobe')
            index_path = Path(face['index_path']).resolve(strict=True)
            entries = [entry for entry in read(index_path)['assets']
                       if entry.get('library_asset_id') == face['library_asset_id']]
            if len(entries) != 1:
                raise ValueError('Expected one exact library face entry')
            entry = entries[0]
            confirmation = entry.get('confirmation') or {}
            if (entry.get('category') != '大头照' or entry.get('state') != '已确认'
                    or not confirmation.get('id') or confirmation.get('asset_id') != face['library_asset_id']):
                raise ValueError('Library face requires its own active confirmation')
            image = snap(index_path.parent / entry['destination'])
            if (not Path(image['path']).is_relative_to(index_path.parent)
                    or image['path'] in records or references.count(image) != 1
                    or not any(r.get('exists') and r.get('resolved_path')
                               and Path(r['resolved_path']).resolve() == Path(image['path'])
                               and r.get('sha256') == image['sha256'] for r in item.get('references', []))):
                raise ValueError('Confirmed library face must match the frozen, actually selected source image')
            edges.append(dict(role='face', wardrobe_set=None, depth=0, image=image,
                              library_index=str(index_path), library_asset_id=face['library_asset_id'],
                              confirmation_id=confirmation['id']))
    for ref in references:
        source_root = root
        source = external.get(ref['path'])
        if source:
            source_root, source_scope, folder, receipt, binding = source
            depths.update(check_reference_reviews(source_root, [ref]))
        elif ref['path'] in records:
            folder, receipt = records[ref['path']]
            if receipt['queue_id'] != request['queue_id']:
                raise ValueError('Method dependency must remain in the same AST')
        else:
            continue
        if not (folder/'dependency-approval.json').is_file():
            raise ValueError('Reference lacks explicit method-role review')
        approval = read(folder/'dependency-approval.json')
        if approval.get('image') != ref or approval.get('attempt_id') != receipt['attempt_id']:
            raise ValueError('Method-role review does not match reference')
        review_kind = approval.get('review_kind', 'independent_qa')
        if review_kind == 'producer_self_check':
            review = approval.get('self_check', {})
            if (review.get('passed') is not True or review.get('image_sha256') != ref['sha256']
                    or any(not isinstance(review.get(k), str) or not review[k].strip()
                           for k in ('observer', 'visual_observation'))
                    or not approval.get('source_request') or not approval.get('source_result')):
                raise ValueError('Producer self-check missing or stale')
            verify([approval['source_request'], approval['source_result']])
            if (approval['source_request']['path'] != str(folder/'request.json')
                    or approval['source_result']['path'] != str(folder/'result.json')
                    or approval['dependency_role'] != original_dependency_role(source_root, read(folder/'request.json'), receipt)):
                raise ValueError('Producer self-check source or role differs from original')
        elif review_kind == 'independent_qa':
            qa = approval.get('independent_qa', {})
            if qa.get('status') != 'passed' or qa.get('image_sha256') != ref['sha256']:
                raise ValueError('Independent QA approval missing or stale')
        else:
            raise ValueError('Unsupported dependency review kind')
        edges.append(dict(attempt_id=receipt['attempt_id'],role=approval['dependency_role'],
                          wardrobe_set=approval.get('wardrobe_set'),depth=depths[ref['path']],
                          approval=snap(folder/'dependency-approval.json'),
                          parent_request=snap(folder/'request.json'),parent_result=snap(folder/'result.json'),
                          **(dict(source_trial_root=str(source_root),source_scope=snap(source_root/'asset_generation_trial.json'),
                                  binding=binding,design_lineage=receipt.get('generated_reference_context', [])) if source else {})))
    needed = {'wardrobe_side':{'wardrobe_front'},'wardrobe_back':{'wardrobe_front'},
              'identity_board':{'face','wardrobe_front','wardrobe_side','wardrobe_back'},
              'scene_reverse':{'scene_master'},'scene_oblique':{'scene_master'},
              'scene_top':{'scene_master','scene_reverse','scene_oblique'}}
    if role == 'identity_board' and {e['role'] for e in edges} == {'face','wardrobe_three_view'}:
        needed[role] = {'face','wardrobe_three_view'}
        if request.get('output_kind') != '人物1x4' or request.get('layout_requirements') != CHARACTER_BOARD_LAYOUT:
            raise ValueError('Final character board requires explicit 人物1x4 output_kind and exact layout requirements')
        wardrobe_approvals = [read(e['approval']['path']) for e in edges if e['role'] == 'wardrobe_three_view']
        if any(a.get('headless_three_view') is not True or a.get('independent_single_view') is True
               for a in wardrobe_approvals):
            raise ValueError('Three-view wardrobe review must retain its actual multi-view layout')
    elif any('library_index' in edge for edge in edges):
        raise ValueError('Independent library face requires one reviewed headless wardrobe three-view')
    if role not in needed or {e['role'] for e in edges} != needed[role] or len(edges) != len(needed[role]):
        raise ValueError('Method dependency role set is incomplete, duplicated or not permitted')
    if role.startswith('wardrobe_') or role == 'identity_board':
        if not request.get('wardrobe_set') or any(e['wardrobe_set'] != request['wardrobe_set'] for e in edges if e['role'].startswith('wardrobe_')):
            raise ValueError('Wardrobe dependencies must belong to one explicit set')
    depth = 1 + max(e['depth'] for e in edges)
    if depth > 2:
        raise ValueError('Method dependency exceeds two edges / three image layers')
    return dict(role=role,depth=depth,edges=edges)


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


def source_method_revalidation(binding, original, folder, receipt, target):
    """Re-attest a lost mutable reading ledger, never changed method content."""
    methods = original['methods']
    drift = [record for record in methods if snap(record['path']) != record]
    evidence_path = binding.get('source_method_revalidation')
    if not drift:
        if evidence_path:
            raise ValueError('Reading revalidation requires actual historical ledger drift')
        return None
    if (len(drift) != 1 or Path(drift[0]['path']).name != 'method_reading_evidence.json'
            or original.get('reading_ledger_sources') or not evidence_path):
        verify(methods)  # Preserve the default hard failure, including method-body drift.
        raise ValueError('Only a historical mutable reading ledger can be revalidated')
    unchanged = [record for record in methods if record != drift[0]]
    verify(unchanged)
    evidence = read(evidence_path)
    expected = dict(source_request=snap(folder/'request.json'),source_result=snap(folder/'result.json'),
                    image=receipt['image'],historical_ledger=drift[0],current_ledger=snap(drift[0]['path']),
                    methods_re_read=unchanged)
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError('Reading revalidation does not bind the exact historical source and current bytes')
    if any(not isinstance(evidence.get(key), str) or not evidence[key].strip()
           for key in ('observer', 'method_observation', 'visual_observation')):
        raise ValueError('Reading revalidation requires actual observer, method reading and image-use observations')
    preparation = evidence.get('current_preparation', {})
    if set(preparation) != {'basis', 'ledger', 'packet'}:
        raise ValueError('Exact current preparation basis, reading ledger and method packet required')
    verify(preparation.values())
    names = {'basis':'task_basis.json','ledger':'method_reading_evidence.json','packet':'method_reading_packet.md'}
    if (any(Path(preparation[key]['path']).name != name for key, name in names.items())
            or len({str(Path(record['path']).parent) for record in preparation.values()}) != 1):
        raise ValueError('Preparation files must belong to one exact current context')
    basis, ledger = read(preparation['basis']['path']), read(preparation['ledger']['path'])
    packet_hash = preparation['packet']['sha256']
    if (basis.get('skill') != 'asset-image-assistant' or ledger.get('skill') != 'asset-image-assistant'
            or not basis.get('context_id') or basis['context_id'] != ledger.get('context_id')
            or not basis.get('reading_reuse_key') or basis['reading_reuse_key'] != ledger.get('reading_reuse_key')
            or ledger.get('actor_id') != evidence['observer']
            or ledger.get('attestation') != 'agent_reported_complete_reading'
            or ledger.get('packet_sha256') != packet_hash or ledger.get('method_packet_sha256') != packet_hash
            or not any(Path(record['path']).name == 'method_reading_packet.md'
                       and record['sha256'] == packet_hash for record in unchanged)
            or not any(record == snap(target['source_narrative']) for record in basis.get('input_snapshot', {}).get('sources', []))
            or target['asset_id']+'@'+target['output_id'] not in basis.get('task_request', {}).get('image_targets', [])):
        raise ValueError('Current preparation does not attest the observer, unchanged method and target project/output')
    return dict(historical_method_drift=True,revalidation=snap(evidence_path),**expected,
                observer=evidence['observer'],method_observation=evidence['method_observation'],
                visual_observation=evidence['visual_observation'],current_preparation=preparation)


def generated_reference_context(root, scope, request, references):
    """Bind exact inspected design provenance to a newly generated baseline.

    Root identity composites and one root-derived clothing state are bounded
    design sources, never original references or method-role approvals.
    """
    bindings = request.get('generated_reference_bindings', [])
    if not isinstance(bindings, list):
        raise ValueError('Generated reference bindings must be a list')
    if not bindings:
        return []
    routes = {
        ('wardrobe_state', None): 'identity_for_wardrobe',
        ('character_head_from_composite', 'face'): 'identity_for_head',
        ('wardrobe_three_view_from_composite', 'wardrobe_three_view'): 'clothing_for_three_view',
    }
    purpose = routes.get((request.get('stage'), request.get('baseline_role')))
    if len(bindings) != 1 or not purpose or request.get('dependency_role'):
        raise ValueError('One qualified composite may bind only to an explicit new character baseline or wardrobe state')
    if purpose == 'clothing_for_three_view' and not request.get('wardrobe_set'):
        raise ValueError('New clothing baseline requires an exact wardrobe set')
    target = scope['items'][request['queue_id']]
    binding = bindings[0]
    if (binding.get('purpose') != purpose
            or not isinstance(binding.get('visual_observation'), str)
            or not binding['visual_observation'].strip()):
        raise ValueError('Explicit wardrobe identity purpose and actual visual observation required')
    source_root, source_scope = root_path(binding['source_trial_root'])
    if source_root == root:
        raise ValueError('Cross-trial identity binding requires a different source trial')
    folder = attempt_folder(source_root, binding['attempt_id'])
    original, receipt = read(folder/'request.json'), read(folder/'result.json')
    donor = source_scope['items'].get(original.get('queue_id'), {})
    if (not target.get('asset_id') or target['asset_id'] != donor.get('asset_id')
            or not target.get('source_narrative')
            or target['source_narrative'] != donor.get('source_narrative')):
        raise ValueError('Generated identity must remain in the same source project and AST')
    if purpose == 'clothing_for_three_view' and request.get('wardrobe_set') != donor.get('output_id'):
        raise ValueError('Clothing baseline must bind the exact source wardrobe output set')
    # root_path verifies both frozen narrative snapshots, not just matching names.
    narrative = snap(target['source_narrative'])
    if any(narrative not in s['source_snapshots'] for s in (scope, source_scope)):
        raise ValueError('Generated identity narrative is not the exact frozen source')
    root_composite = (donor.get('output_id') == 'INITIAL-COMPOSITE'
        and original.get('stage') in {'initial_composite_prompt_only_revision',
                                      'initial_composite_original_face_revision'}
        and original.get('references_before') == []
        and not original.get('generated_reference_bindings')
        and not original.get('baseline_role') and not original.get('dependency_role'))
    clothing_state = (purpose == 'clothing_for_three_view'
        and original.get('stage') == 'wardrobe_state'
        and not original.get('baseline_role') and not original.get('dependency_role')
        and request.get('wardrobe_set') == donor.get('output_id')
        and len(original.get('references_before', [])) == 1)
    source_lineage = None
    if clothing_state:
        # A single historical wardrobe step from the root may seed a new
        # clothing design baseline; never another rebuilt baseline or board.
        source_lineage = generated_reference_context(source_root, source_scope, original,
                                                     original['references_before'])
        if (len(source_lineage) != 1 or source_lineage[0]['depth'] != 0
                or source_lineage[0]['purpose'] != 'identity_for_wardrobe'
                or source_lineage != original.get('generated_reference_context')
                or original['references_before'] != [source_lineage[0]['image']]):
            raise ValueError('Clothing rebuild requires one exact root identity parent')
    if not root_composite and not clothing_state:
        raise ValueError('Only an original root composite without generated/image parents or one qualified root-derived wardrobe state is supported')
    if (original.get('attempt_id') != binding['attempt_id']
            or receipt.get('attempt_id') != binding['attempt_id']
            or receipt.get('queue_id') != original['queue_id']
            or receipt.get('prompt_sha256') != original.get('prompt_sha256')
            or receipt.get('outcome') != 'qualified_candidate'
            or receipt.get('assessment', {}).get('passed') is not True
            or not receipt.get('assessment', {}).get('notes')
            or baseline_rejected(folder, receipt)):
        raise ValueError('Generated identity lacks an exact qualified producer self-check')
    image = receipt['image']
    if (image not in references or snap(image['path']) != image
            or Path(image['path']).parent != folder
            or Path(image['path']).stem != 'candidate'):
        raise ValueError('Reference must be the exact native generated candidate, without copies or crops')
    revalidation = source_method_revalidation(binding, original, folder, receipt, target)
    edge = dict(source_trial_root=str(source_root),attempt_id=binding['attempt_id'],
                 source_kind='generated_candidate',purpose=binding['purpose'],
                 visual_observation=binding['visual_observation'],asset_id=target['asset_id'],
                 narrative=narrative,image=image,depth=0,
                 scope=snap(source_root/'asset_generation_trial.json'),
                 request=snap(folder/'request.json'),result=snap(folder/'result.json'))
    if source_lineage is not None:
        edge.update(source_lineage=source_lineage, source_provenance_depth=1)
    if revalidation:
        edge.update(historical_method_drift=True,source_method_revalidation=revalidation)
    return [edge]

def scene_layout_revision_context(root, scope, request, references):
    """One same-AST root-master edit; provenance is separate from view depth."""
    binding = request.get('scene_layout_revision')
    if binding is None:
        return None
    root = Path(root).resolve()
    if (not isinstance(binding, dict) or request.get('stage') != 'scene_master_revision'
            or request.get('baseline_role') != 'scene_master' or request.get('dependency_role')
            or request.get('generated_reference_bindings')):
        raise ValueError('Layout revision requires an explicit scene-master revision baseline')
    if any(not isinstance(binding.get(key), str) or not binding[key].strip()
           for key in ('observer', 'edit_purpose', 'visual_observation')):
        raise ValueError('Layout revision requires actual observer, edit purpose and visual observations')
    folder = attempt_folder(root, binding['source_attempt_id'])
    original, receipt = read(folder/'request.json'), read(folder/'result.json')
    item = scope['items'].get(request['queue_id'], {})
    if (not item.get('asset_id') or original.get('queue_id') != request['queue_id']
            or receipt.get('queue_id') != request['queue_id']
            or original.get('attempt_id') != binding['source_attempt_id']
            or receipt.get('attempt_id') != binding['source_attempt_id']):
        raise ValueError('Layout revision must remain in the same trial queue and AST')
    if (original_dependency_role(root, original, receipt) != 'scene_master'
            or original.get('scene_layout_revision') or original.get('dependency_role')
            or original.get('generated_reference_bindings')):
        raise ValueError('Layout revision accepts only a root scene master, never a second revision')
    originals = {str(Path(r['resolved_path']).resolve()):r.get('sha256')
                 for r in item.get('references', []) if r.get('exists') and r.get('resolved_path')}
    additions, _ = amended_references(root, scope, request['queue_id'])
    originals.update(additions)
    if any(ref['path'] not in originals or (originals[ref['path']] and originals[ref['path']] != ref['sha256'])
           for ref in original['references_before']):
        raise ValueError('Layout revision source must have only bound original image parents')
    image = receipt.get('image')
    if (receipt.get('outcome') != 'qualified_candidate' or receipt.get('assessment', {}).get('passed') is not True
            or baseline_rejected(folder, receipt) or not image
            or binding.get('image_sha256') != image['sha256'] or references != [image]
            or snap(image['path']) != image or Path(image['path']).parent != folder
            or Path(image['path']).stem != 'candidate'):
        raise ValueError('Layout revision requires the exact native qualified root-master image')
    verify(original['methods']);verify(original['references_before'])
    verify(original.get('reference_amendments', []))
    if check_reference_reviews(root, [image])[image['path']] != 0:
        raise ValueError('Layout revision source must have method depth zero')
    return dict(source_kind='scene_layout_revision',lineage_depth=1,method_baseline_depth=0,
                asset_id=item['asset_id'],source_attempt_id=binding['source_attempt_id'],image=image,
                source_request=snap(folder/'request.json'),source_result=snap(folder/'result.json'),
                observer=binding['observer'],edit_purpose=binding['edit_purpose'],
                visual_observation=binding['visual_observation'])


def preflight(root, request):
    root, scope = root_path(root)
    ident = token(request['attempt_id'])
    item = scope['items'].get(request['queue_id'])
    if not item:
        raise ValueError('Asset outside explicit trial scope')
    baseline_role = request.get('baseline_role')
    if baseline_role not in (None, 'face', 'wardrobe', 'wardrobe_three_view', 'scene_master', 'object_master'):
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
    reference_snapshots = [snap(raw) for raw in request.get('reference_paths', [])]
    generated_context = generated_reference_context(root, scope, request, reference_snapshots)
    generated_paths = {edge['image']['path'] for edge in generated_context}
    if request.get('method_reference_bindings'):
        dependency_context(root, request, reference_snapshots)
        generated_paths.update(ref['path'] for ref in reference_snapshots)
    layout = scene_layout_revision_context(root, scope, request, reference_snapshots)
    if layout:
        generated_paths.add(layout['image']['path'])
    refs = []
    for raw in request.get('reference_paths',[]):
        record = snap(raw)
        if record['path'] in allowed:
            if allowed[record['path']] and allowed[record['path']] != record['sha256']:
                raise ValueError('Bound original reference has changed')
        elif record['path'] not in generated_paths:
            matching = []
            for receipt_path in (root/'attempts').glob('*/result.json'):
                receipt = read(receipt_path)
                if (receipt.get('image') == record and receipt.get('assessment',{}).get('passed') is True
                    and receipt.get('queue_id') == request['queue_id'] and receipt.get('outcome') == 'qualified_candidate'
                    and (request.get('dependency_role') or receipt.get('baseline_role') in {'face','wardrobe','scene_master','object_master'})
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
    dependency = dependency_context(root, request, refs) if request.get('dependency_role') else None
    check_reference_reviews(root, refs)
    record = dict(attempt_id=ident,queue_id=request['queue_id'],stage=request.get('stage','asset'),
                  dependency_role=request.get('dependency_role'),wardrobe_set=request.get('wardrobe_set'),dependency=dependency,
                  baseline_role=baseline_role,reference_roles=request.get('reference_roles', []),
                  prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                  generated_reference_bindings=request.get('generated_reference_bindings', []),
                  generated_reference_context=generated_context,
                  reference_amendments=amendment_evidence,references_before=refs,methods=methods,visual_inspection=request['visual_inspection'],
                  tool='image_gen',model_disclosure=MODEL,count=1,
                  authorization_ref=scope['authorization']['message_id'],state='reserved_for_one_host_call',**FLAGS)
    if request.get('method_reference_bindings'):
        record['method_reference_bindings'] = request['method_reference_bindings']
    if request.get('output_kind'):
        record.update(output_kind=request['output_kind'],layout_requirements=request.get('layout_requirements'))
    if layout:
        record.update(scene_layout_revision=request['scene_layout_revision'],scene_layout_revision_context=layout)
    folder = attempt_folder(root, ident)
    folder.mkdir(parents=True,exist_ok=False)
    ledger_sources = []
    for index, method in enumerate(record['methods']):
        if Path(method['path']).name != 'method_reading_evidence.json':
            continue
        verify([method])
        frozen = folder/'reading-ledgers'/str(index)/'method_reading_evidence.json'
        frozen.parent.mkdir(parents=True,exist_ok=False)
        with Path(method['path']).open('rb') as src, frozen.open('xb') as dst:
            shutil.copyfileobj(src,dst)
        frozen_record = snap(frozen)
        if frozen_record['sha256'] != method['sha256']:
            raise ValueError('Reading ledger changed while freezing attempt evidence')
        ledger_sources.append(dict(source=method,frozen=frozen_record))
        record['methods'][index] = frozen_record
    if ledger_sources:
        record['reading_ledger_sources'] = ledger_sources
    create(folder/'request.json',record)
    return dict(attempt=str(folder),request=record,instruction='Execute image_gen once with this exact prompt and references; never retry an uncertain call. Finish with the actual output and visual assessment.')

def finish(root, attempt_id, image_path, assessment):
    root, scope = root_path(root)
    folder = attempt_folder(root, attempt_id)
    request = read(folder/'request.json')
    if (folder/'result.json').exists() or (folder/'completion.lock').exists():
        raise ValueError('Attempt already completed or completion interrupted; do not overwrite')
    verify(request['references_before']); verify_attempt_methods(folder, request, snap(image_path))
    verify(request.get('reference_amendments', []))
    check_reference_reviews(root, request['references_before'])
    if scene_layout_revision_context(root, scope, request, request['references_before']) != request.get('scene_layout_revision_context'):
        raise ValueError('Scene layout revision lineage changed during generation')
    if generated_reference_context(root, scope, request, request['references_before']) != request.get('generated_reference_context', []):
        raise ValueError('Generated identity evidence changed during generation')
    if request.get('dependency_role'):
        if dependency_context(root, request, request['references_before']) != request.get('dependency'):
            raise ValueError('Method dependency evidence changed during generation')
    if not isinstance(assessment.get('passed'),bool) or not assessment.get('notes'):
        raise ValueError('Explicit visual self-check result and observations required')
    source = Path(image_path).resolve(strict=True)
    with Image.open(source) as image:
        image.verify()
    with Image.open(source) as image:
        dimensions = list(image.size)
    if request.get('output_kind') == '人物1x4' and assessment['passed']:
        if abs(dimensions[0] / dimensions[1] - 16 / 9) > 0.05:
            raise ValueError('Character board actual canvas must be close to 16:9')
        if not isinstance(assessment.get('layout_visual_observation'), str) or not assessment['layout_visual_observation'].strip():
            raise ValueError('Character board requires actual visual layout observation; dimensions are not pixel-content proof')
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
    if scene_layout_revision_context(root, scope, request, request['references_before']) != request.get('scene_layout_revision_context'):
        raise ValueError('Scene layout revision lineage changed during output copy')
    if generated_reference_context(root, scope, request, request['references_before']) != request.get('generated_reference_context', []):
        raise ValueError('Generated identity evidence changed during output copy')
    if request.get('method_reference_bindings') or any(
            'library_index' in edge for edge in (request.get('dependency') or {}).get('edges', [])):
        if dependency_context(root, request, request['references_before']) != request.get('dependency'):
            raise ValueError('External method evidence changed during output copy')
    receipt = dict(attempt_id=attempt_id,queue_id=request['queue_id'],baseline_role=request.get('baseline_role'),
                   dependency_role=request.get('dependency_role'),wardrobe_set=request.get('wardrobe_set'),dependency=request.get('dependency'),image=snap(destination),
                   generated_reference_context=request.get('generated_reference_context', []),
                   source_image=source_record,dimensions=dimensions,assessment=assessment,
                   references_after=[snap(r['path']) for r in request['references_before']],
                   prompt_sha256=request['prompt_sha256'],tool=request['tool'],model_disclosure=MODEL,
                   authorization_ref=request['authorization_ref'],
                   outcome='qualified_candidate' if assessment['passed'] else 'failed_candidate',**FLAGS)
    if request.get('output_kind'):
        receipt.update(output_kind=request['output_kind'],layout_requirements=request.get('layout_requirements'))
    if request.get('scene_layout_revision_context'):
        receipt.update(source_kind='scene_layout_revision',scene_layout_revision_context=request['scene_layout_revision_context'])
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
    parser.add_argument('action',choices=['init','preflight','finish','fail','amend-reference','approve-dependency','revalidate-methods'])
    parser.add_argument('--trial-root',required=True)
    parser.add_argument('--queue'); parser.add_argument('--authorization-file'); parser.add_argument('--request')
    parser.add_argument('--attempt-id'); parser.add_argument('--image'); parser.add_argument('--assessment'); parser.add_argument('--reason')
    args = parser.parse_args()
    if args.action == 'init': result = initialize(args.trial_root,args.queue,args.authorization_file)
    elif args.action == 'revalidate-methods': result = revalidate_methods(args.trial_root,read(args.request))
    elif args.action == 'approve-dependency': result = approve_dependency(args.trial_root,read(args.request))
    elif args.action == 'amend-reference': result = amend_reference(args.trial_root,read(args.request))
    elif args.action == 'preflight': result = preflight(args.trial_root,read(args.request))
    elif args.action == 'finish': result = finish(args.trial_root,args.attempt_id,args.image,read(args.assessment))
    else: result = fail(args.trial_root,args.attempt_id,args.reason)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
