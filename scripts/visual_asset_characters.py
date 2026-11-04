"""User-directed character routes; prompt production still uses the project asset Skill."""
import copy
import json
import re
from datetime import datetime, timezone
from uuid import uuid4
from core.character_assets import uses_wardrobe


def enable_character_ai_finals(job):
    """Unblock pending legacy boards; preserve confirmed outputs and active runs."""
    build_outfits = {v['id'] for v in job.get('character_workflow', {}).get('outfits', []) if v['route'] == 'build'}
    changed = False
    for step in job['steps']:
        if step.get('kind') != '人物1x4' or step.get('outfit_id') not in build_outfits:
            continue
        if step['status'] in {'confirmed', 'generating', 'submitting', 'submitted', 'querying'} or step.get('project_confirmation'):
            continue
        if step.get('generation_enabled') is not True:
            step['generation_enabled'] = True
            changed = True
        if 'composition_error' in step:
            step.pop('composition_error')
            changed = True
        if step.get('message', '').startswith('人物1×4自动合成未完成：'):
            step.pop('message')
            changed = True
    return changed


def upstream_outfits(req):
    """Read explicit clothing transitions, never emotion/skin-state transitions."""
    # Explicit production states separate continuity from the visual design.
    # Presence is authoritative: invalid/empty states must not fall back to guessing.
    if 'wardrobe_states' in req:
        states = req['wardrobe_states']
        if not isinstance(states, list) or not 1 <= len(states) <= 8:
            raise ValueError('人物服装状态必须明确登记1至8项')
        result = []
        for state in states:
            if not isinstance(state, dict) or not all(isinstance(state.get(k), str) and state[k].strip() for k in ('name', 'evidence')):
                raise ValueError('每项服装状态须包含名称与上游依据')
            result.append({'name': state['name'].strip(), 'evidence': state['evidence'].strip()})
        if len({s['name'] for s in result}) != len(result):
            raise ValueError('人物服装状态名称重复，请核对上游需求')
        return result
    # Legacy packages only: keep the old supported clothing notation.
    clothing = re.compile(r'西装|衬衫|马甲|开衫|Polo|[裙裤袍]|礼服|套装|制服|上衣|外套|针织衫|T恤|日常装|出差装|工作服|便装|睡衣', re.I)
    lines = [str(t).strip() for t in req.get('identity_constraints', [])]
    titles = re.findall(r'[（(]([^）)]+)[）)]', req.get('name', ''))
    transitions = []
    for line in lines + titles:
        parts = [t.strip() for t in re.split(r'→|->|换成|换为|切换为', line)]
        if len(parts) > 1 and all(clothing.search(t) for t in parts):
            transitions.append([{'name': t, 'evidence': line} for t in dict.fromkeys(parts)])
    if transitions:
        if len({len(t) for t in transitions}) > 1:
            raise ValueError('上游人物名称与服装约束中的套数不一致，请回项目核对')
        result = transitions[0]
    else:
        garments = [t for t in lines if clothing.search(t) and not re.search(r'→|->|换成|换为', t)]
        if not garments:
            garments = [t.split('/')[0] for t in titles if clothing.search(t.split('/')[0])]
        if not garments:
            raise ValueError('旧版需求缺少可读取的服装状态，请在上游登记 wardrobe_states；款式可在选图时确定')
        # Separate garments of one look do not imply multiple outfits.
        result = [{'name': '＋'.join(garments), 'evidence': garments[0]}]
    if len(result) > 8:
        raise ValueError('上游服装超过8套，请先核对需求')
    return result


def prepare_character(service, payload):
    """Bind exact upstream outfit slots; keep ambiguous legacy results for reassignment."""
    with service.lib.lock:
        p = service.get(payload['project_id'])
        service.fresh(p)
        row = service.row(p, payload['ast_id'])
        if not service.row_uses_wardrobe(row):
            raise ValueError('此入口只处理人物需求')
        expected = upstream_outfits(row['requirement'])
        old = service.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
        if old and old.get('character_workflow', {}).get('upstream_outfits') == expected:
            return service.detail(p['id'])
        if p.get('batch', {}).get('status') == 'running' or p.get('planning', {}).get('status') == 'running' or (old and any(s['status'] in {'generating','submitting','submitted','querying'} or s.get('audit', {}).get('status') in {'drafting','reviewing'} for s in old['steps'])):
            raise ValueError('任务正在处理，完成后再读取上游服装')
        matching = old and [v['name'] for v in old.get('character_workflow', {}).get('outfits', [])] == [v['name'] for v in expected]
        if matching:
            old['character_workflow']['upstream_outfits'] = expected
            service.lib.save(old, 'jobs')
            return service.detail(p['id'])
        if old:
            p.setdefault('history', []).append(copy.deepcopy(row))
            row['previous_character_images'] = list(dict.fromkeys(row.get('previous_character_images', []) + [i for s in old['steps'] for i in ([s['selected']] if s.get('selected') else s.get('outputs', []))]))
            row['job_id'] = None
            service.invalidate_export(p, '按上游重新对应服装；旧选图归属需要核对')
            p.pop('export_pending', None)
            service.save(p)
        for v in expected:
            character_route(service, {'project_id':p['id'], 'ast_id':payload['ast_id'], 'route':'reuse', **v})
        p = service.get(p['id'])
        job = service.lib.get(service.row(p, payload['ast_id'])['job_id'], 'jobs')
        job['character_workflow']['upstream_outfits'] = expected
        service.lib.save(job, 'jobs')
        return service.detail(p['id'])


def choose_character_route(service, payload):
    if payload.get('name') or payload.get('evidence') or not payload.get('outfit_id'):
        raise ValueError('服装套数与名称由上游需求确定，网页仅选择复用或生成')
    prepare_character(service, payload)
    return character_route(service, payload)


def character_route(service, payload):
    with service.lib.lock:
        p = service.get(payload['project_id'])
        service.fresh(p)
        if p.get('planning', {}).get('status') == 'running' or p.get('batch', {}).get('status') == 'running':
            raise ValueError('项目正在处理，请完成后再调整人物制作方式')
        row = service.row(p, payload['ast_id'])
        req = row['requirement']
        if not service.row_uses_wardrobe(row):
            raise ValueError('此入口只处理人物需求')
        route = payload.get('route')
        if route not in {'reuse', 'build'}:
            raise ValueError('请选择已有1×4或新制作')
        job = service.lib.get(row['job_id'], 'jobs') if row.get('job_id') else None
        if job and not job.get('character_workflow'):
            raise ValueError('此人物已有旧制作项，请保留旧结果并先核对制作结构，不能自动替换')
        if job and any(s['status'] in {'generating', 'submitting', 'submitted', 'querying'} or s.get('audit', {}).get('status') in {'drafting', 'reviewing'} for s in job['steps']):
            raise ValueError('人物图片仍在处理，请稍后调整')
        now = datetime.now(timezone.utc).isoformat()
        if not job:
            job = {'id': uuid4().hex[:16], 'title': req['name'], 'kind': '人物1x4', 'source_id': None,
                   'provider': 'imagegen', 'model': '5.0', 'notes': '', 'workflow_version': 5,
                   'created_at': now, 'project_id': p['id'], 'project_name': p['name'], 'ast_id': req['asset_id'],
                   'source_narrative': p['source'], 'requirement': copy.deepcopy(req), 'steps': [],
                   'planning_evidence': {'mode': 'project', 'method': 'user-selected-character-route'},
                   'plan_rationale': '按用户选择复用最终1×4，或共用大头照、逐套服装与1×4制作；生成仍执行项目Skill。',
                   'blockers': [], 'character_workflow': {'outfits': []}}
        outfits = job['character_workflow']['outfits']
        outfit_id = payload.get('outfit_id')
        variant = next((v for v in outfits if v['id'] == outfit_id), None) if outfit_id else None
        if outfit_id and not variant:
            raise ValueError('服装版本已变化，请刷新')
        if not variant:
            if len(outfits) >= 8:
                raise ValueError('请先核对上游服装需求，当前每个人物最多登记8套')
            evidence = str(payload.get('evidence', '')).strip()
            name = str(payload.get('name', '')).strip()[:100]
            source_text = json.dumps(req, ensure_ascii=False)
            if outfits or name:
                if not name or not evidence or evidence not in source_text or len(evidence) < 2:
                    raise ValueError('请填写服装名称，并引用当前上游需求中的原文，不能新增表外服装')
            else:
                name, evidence = '需求所述服装', '；'.join(req.get('identity_constraints', []) + req.get('required_states', [])) or req['narrative_purpose']
            outfit_id = 'outfit_' + uuid4().hex[:8]
            variant = {'id': outfit_id, 'name': name, 'evidence': evidence, 'route': route}
            outfits.append(variant)
        renamed = False
        if outfit_id and payload.get('name'):
            name = str(payload['name']).strip()[:100]
            evidence = str(payload.get('evidence', '')).strip()
            if len(evidence) < 2 or evidence not in json.dumps(req, ensure_ascii=False):
                raise ValueError('服装标注必须引用当前上游原文')
            renamed = name != variant['name'] or evidence != variant['evidence']
            variant.update(name=name, evidence=evidence)
            if renamed:
                for s in job['steps']:
                    if s.get('outfit_id') == outfit_id:
                        s.setdefault('history', []).append({k: copy.deepcopy(s.get(k)) for k in ('selected', 'outputs', 'project_confirmation', 'prompt', 'audit')})
                        s.update(status='waiting', outputs=[], project_confirmation=None, audit={'status': 'stale'}, state=name)
                        s['name'] = name + (' · 服装三视图' if s['kind'] == '服装' else ' · 人物1×4')
                        s['view'] = s['name']
                        s['purpose'] = '此服装对应当前人物；依据上游：' + evidence
                        for key in ('selected', 'prompt_approval', 'prompt'):
                            s.pop(key, None)
        old_route = variant['route']
        final_key = outfit_id + '_final'
        if old_route != route and not renamed:
            final = next(s for s in job['steps'] if s['key'] == final_key)
            final.setdefault('history', []).append({k: copy.deepcopy(final.get(k)) for k in ('selected', 'outputs', 'project_confirmation', 'prompt', 'audit')})
            final.update(status='waiting', outputs=[], project_confirmation=None, audit={'status': 'stale'})
            for key in ('selected', 'prompt_approval', 'prompt', 'composition_error', 'message'):
                final.pop(key, None)
        variant['route'] = route
        def add(key, kind, name, purpose, state, dependencies, outfit=None):
            if any(s['key'] == key for s in job['steps']):
                return
            recipe = service.skill.recipe(kind)
            job['steps'].append({'key': key, 'kind': kind, 'name': name, 'view': name, 'purpose': purpose,
                'state': state, 'ratio': recipe['ratio'], 'dependencies': dependencies, 'required': True,
                'outfit_id': outfit, 'status': 'waiting', 'outputs': [], 'reference_ids': row['reference_ids'][:],
                'project_confirmation': None, 'template_source': recipe.get('template') or recipe['guide'],
                'rationale': '来自当前人物AST及用户选择的制作路径；具体生成按资产Skill编写并审查。',
                'must_not_inherit': ['临时表情、剧情布光、污损或镜头构图']})
        if route == 'build':
            add('shared_head', '大头照', '共用大头照', '固定人物身份；供本人物各套服装共用', '人物身份', [])
            add(outfit_id + '_wardrobe', '服装', variant['name'] + ' · 服装三视图',
                '固定此套服装的外观与结构；具体约束见人物上游需求。', variant['name'], [], outfit_id)
        add(final_key, '人物1x4', variant['name'] + ' · 人物1×4',
            '当前人物在此服装下的直接参考；须核对全部上游所需状态与交互', variant['name'], [], outfit_id)
        for v in outfits:
            for s in job['steps']:
                if s['key'] == v['id'] + '_final':
                    s['dependencies'] = ['shared_head', v['id'] + '_wardrobe'] if v['route'] == 'build' else []
                    if s['status'] != 'confirmed' and not s.get('project_confirmation'):
                        s['generation_enabled'] = v['route'] == 'build'
                if s['key'] == v['id'] + '_wardrobe':
                    s['required'] = v['route'] == 'build'
        for s in job['steps']:
            if s['key'] == 'shared_head':
                s['required'] = any(v['route'] == 'build' for v in outfits)
        # Never renumber existing steps: saved prompts, requests and UI selections use their indices.
        service.lib.save(job, 'jobs')
        row['job_id'] = job['id']
        service.invalidate_export(p, '人物制作路径或服装版本已调整，请重新核对交接')
        p.pop('export_pending', None)
        service.save(p)
        return service.detail(p['id'])
