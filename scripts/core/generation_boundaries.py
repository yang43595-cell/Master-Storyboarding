"""Read-only checks for current generation_segments, not an automatic splitter.

The author still judges rhythm, actual model support and image contents. This
checks declared reasons against exact source fields/bytes and never invents a
duration ceiling, a scene boundary, a reference or an approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from .boundary_rules import (
    _BEFORE_CUT_SUFFIX,
    _CREATOR_BOWL_BRUSH_OFFER,
    _DIRECTOR_BODY_FIELDS,
    _NEGATED_CUT_PREFIX,
    _OUTGOING_CUT,
    _PHYSICAL_DIMENSIONS,
    _PHYSICAL_ROLES,
    _UNDECIDED_DIRECTOR_TEXT,
    _check_generation_duration,
    _check_project_duration,
    _check_target_edit_duration,
    _cross_scene_review,
    _cutpoint,
    _director_exit_marker,
    _director_field,
    _evidence_file,
    _local_dependency_payload,
    _model_capability,
    _model_limit,
    _need,
    _physical_requirements,
    _redframe_duration_options,
    _require_physical_refs,
    _rows,
    _seconds,
    _source_boundary,
    _text,
    _validate,
    diagnose_scene_state_plan,
    validate_delivery_cutpoints,
    validate_delivery_parameters as _rules_validate_delivery_parameters,
    validate_director_body,
    validate_generation_boundaries as _rules_validate_generation_boundaries,
)
from .project_kickoff import load_kickoff
from .prompt_body import (
    QUOTED_TEXT_RE,
    reference_shot_ids,
    scene_state_requirements,
    source_shot_ids,
    standalone_quality_issues,
    submitted_body_issues,
    submitted_prompt_body,
)
from .quality_rules import (
    MAX_GENERATION_SEGMENT_SECONDS,
    generation_duration_values,
    required_visuals,
    validate_required_visuals,
    validate_scene_state_coverage,
    validate_shot_durations,
)


def validate_delivery_parameters(rows, payload, project, *, platform=None, read_evidence=None, load_kickoff_fn=None):
    """Bind submission values to the exact source; caller bounds are not proof."""
    return _rules_validate_delivery_parameters(
        rows,
        payload,
        project,
        platform=platform,
        read_evidence=read_evidence,
        load_kickoff_fn=load_kickoff if load_kickoff_fn is None else load_kickoff_fn,
    )


def validate_generation_boundaries(
    payload,
    storyboard,
    project,
    *,
    require_physical=True,
    delivery_platform=None,
    delivery_segment_ids=None,
    action_shots=None,
    review_segment_ids=None,
    source_artifact=None,
    state=None,
    prompt_content_segment_ids=None,
    read_evidence=None,
    load_kickoff_fn=None,
):
    """Validate a full or partial current draft without writing/approving it."""
    return _rules_validate_generation_boundaries(
        payload,
        storyboard,
        project,
        require_physical=require_physical,
        delivery_platform=delivery_platform,
        delivery_segment_ids=delivery_segment_ids,
        action_shots=action_shots,
        review_segment_ids=review_segment_ids,
        source_artifact=source_artifact,
        state=state,
        prompt_content_segment_ids=prompt_content_segment_ids,
        read_evidence=read_evidence,
        load_kickoff_fn=load_kickoff if load_kickoff_fn is None else load_kickoff_fn,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt", type=Path, help="视频稿，支持项目相对路径")
    mode.add_argument("--shot-id", action="append", help="写词前核这些导演镜号的检查结论和视觉依赖，可重复")
    parser.add_argument("--storyboard", type=Path, required=True, help="准确导演稿，支持项目相对路径")
    parser.add_argument("--segment-id", action="append", help="仅自查所列生成段及稿内相邻接缝，可重复；省略时检查整稿")
    args = parser.parse_args(argv)
    if args.shot_id and args.segment_id:
        parser.error('--segment-id 只用于 --prompt')
    try:
        from .artifacts import (_read_confirmed_package_source, load_manifest, project_path,
                                validate_current_dependencies)
        from .project_state import load_project_state
        from .production_handoff import require_handoff_visual_inputs
        project = args.project.resolve()
        board_path = project_path(project, str(args.storyboard))
        manifest = load_manifest(project)
        directors = [item for item in manifest['artifacts']
                     if item.get('logical_id') == 'storyboard.package' and item.get('status') == 'current'
                     and project_path(project, item['path_or_url']) == board_path]
        if args.shot_id:
            if len(directors) != 1:
                raise ValueError('写词前核须使用准确已采用导演稿')
            director = directors[0]
            state = load_project_state(project)
            storyboard, _ = _read_confirmed_package_source(
                project, manifest, state,
                {'revision': director['revision'], 'path': str(board_path), 'artifact_id': director['artifact_id']},
                'storyboard.package')
            validate_current_dependencies(director, manifest, state, project)
            required_visuals(storyboard, shot_ids=set(args.shot_id))
            result = require_handoff_visual_inputs(project, storyboard, director, args.shot_id,
                                                   manifest=manifest, state=state, require_review=True)
            print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            return 0
        prompt_path = (args.project / args.prompt).resolve()
        prompt_bytes = prompt_path.read_bytes()
        payload = json.loads(prompt_bytes.decode('utf-8-sig'))
        storyboard = json.loads(board_path.read_text(encoding="utf-8-sig"))
        sources = [entry for entry in manifest['artifacts']
                   if entry.get('logical_id') == 'video.prompt'
                   and entry.get('status') in {'candidate', 'current'}
                   and project_path(args.project, entry.get('path_or_url', '')) == prompt_path]
        source_artifact = None
        if sources:
            if len(sources) != 1:
                raise ValueError('prompt_state_control source registration is ambiguous')
            entry, actual = sources[0], hashlib.sha256(prompt_bytes).hexdigest()
            if entry.get('checksum', actual) != actual or entry.get('revision') != payload.get('revision', entry.get('revision')):
                raise ValueError('prompt_state_control source bytes/revision differ from registration')
            source_artifact = {'artifact_id': entry['artifact_id'], 'revision': entry['revision'], 'sha256': actual}
        content_ids = args.segment_id or [row['segment_id'] for row in _rows(payload)]
        result = validate_generation_boundaries(payload, storyboard, args.project, review_segment_ids=args.segment_id,
                                                source_artifact=source_artifact,
                                                prompt_content_segment_ids=content_ids)
        if len(directors) == 1:
            rows = [row for row in _rows(payload) if not args.segment_id or row['segment_id'] in args.segment_id]
            result['visual_readiness'] = require_handoff_visual_inputs(
                project, storyboard, directors[0],
                {shot for row in rows for shot in source_shot_ids(row)}, rows=rows, manifest=manifest)
        if (payload.get('scope') or {}).get('type') in {'full_local_authored', 'selected_ranges'}:
            from .delivery_files import declared_input_roots, discover_dependencies
            dependency_payload = _local_dependency_payload(payload, result) if args.segment_id else payload
            dependencies = discover_dependencies(project, payload=dependency_payload,
                                                 input_roots=declared_input_roots(dependency_payload),
                                                 verify_hashes=True)
            if not dependencies['ok']:
                raise ValueError('视频稿输入不可用：' + '; '.join(dependencies['errors'] + dependencies['uncheckable']))
            result['input_files_checked'] = len(dependencies['checked_files'])
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
