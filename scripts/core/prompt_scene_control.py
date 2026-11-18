"""One scoped user decision shared by source diagnostics and canvas delivery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def decision_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def resolve_prompt_scene_control(row, context, project, state, requirements, *, formal_delivery=False):
    """Validate an existing accepted_issues entry; never certify generated pixels.

    The consumer must first verify the exact source_artifacts in context.
    Source adoption supplies its own exact candidate bytes instead.
    """
    claim = row.get('prompt_state_control')
    if claim is None:
        return None
    if (not formal_delivery or not isinstance(claim, dict)
            or set(claim) != {'decision_id', 'sha256'}):
        raise ValueError('prompt_state_control requires an exact formal user-decision reference')
    matches = [item for item in state.get('accepted_issues', [])
               if isinstance(item, dict) and item.get('issue_id') == claim['decision_id']
               and item.get('kind') == 'prompt_scene_state_control']
    if len(matches) != 1 or decision_digest(matches[0]) != claim['sha256']:
        raise ValueError('prompt_state_control decision missing, ambiguous or changed')
    decision = matches[0]
    if (decision.get('status') != 'user_accepted'
            or decision.get('project_path') != str(Path(project).resolve())
            or decision.get('target_url') != context.get('target_url')
            or any(not isinstance(decision.get(key), str) or not decision[key].strip()
                   for key in ('quote', 'at', 'review_ref'))):
        raise ValueError('prompt_state_control decision belongs to another project/canvas or lacks user evidence')
    source = decision.get('source_artifact')
    if (not isinstance(source, dict) or set(source) != {'artifact_id', 'revision', 'sha256'}
            or not any(all(ref.get(key) == value for key, value in source.items())
                       for ref in context.get('source_artifacts', []))):
        raise ValueError('prompt_state_control decision does not bind this exact source')
    from .quality_evidence import source_shot_ids
    shots = source_shot_ids(row)
    segment_id = row.get('source_segment_id', row.get('segment_id'))
    segments = decision.get('segments')
    if not isinstance(segments, dict) or segments.get(segment_id) != shots or not shots:
        raise ValueError('prompt_state_control segment or shot scope differs from user decision')
    entities = {item['entity_id'] for item in requirements
                if set(shots) & set(item['source_shot_ids'])}
    allowed = decision.get('entity_ids')
    if (not entities or not isinstance(allowed, list)
            or any(not isinstance(value, str) or not value for value in allowed)
            or not entities <= set(allowed)):
        raise ValueError('prompt_state_control entity scope differs from user decision')
    return {'status': 'user_accepted_prompt_control', 'decision_id': claim['decision_id'],
            'decision_sha256': claim['sha256'], 'review_ref': decision['review_ref'],
            'source_artifact': source, 'source_shot_ids': shots,
            'entity_ids': sorted(entities), 'generated_state_verification': 'pending'}


def source_prompt_scene_control(row, payload, project, state, source_artifact, requirements):
    """Reuse a decision bound to the exact registered source without rewriting it."""
    claim = row.get('prompt_state_control')
    if source_artifact is None:
        if claim is not None:
            raise ValueError('prompt_state_control needs the exact registered source bytes')
        return None
    if claim is None:
        matches = [item for item in state.get('accepted_issues', [])
                   if isinstance(item, dict) and item.get('kind') == 'prompt_scene_state_control'
                   and item.get('source_artifact') == source_artifact
                   and row.get('segment_id') in (item.get('segments') or {})]
        if len(matches) > 1:
            raise ValueError('prompt_state_control source decision is ambiguous')
        if not matches:
            return None
        claim = {'decision_id': matches[0].get('issue_id'), 'sha256': decision_digest(matches[0])}
    target = payload.get('target_canvas') or {}
    context = {'target_url': target.get('target_url', target.get('url')),
               'source_artifacts': [source_artifact]}
    return resolve_prompt_scene_control({**row, 'prompt_state_control': claim}, context,
                                        project, state, requirements, formal_delivery=True)
