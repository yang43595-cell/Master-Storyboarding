"""User-directed character routes; prompt production still uses the project asset Skill."""
import copy
import json
import re
from datetime import datetime, timezone
from uuid import uuid4
from PIL import Image, ImageOps
from core.character_assets import uses_wardrobe
from core.artifacts import digest
from pathlib import Path


def automatic_final(job, step):
    return step.get('kind') == '人物1x4' and any(
        v['id'] == step.get('outfit_id') and v['route'] == 'build'
        for v in job.get('character_workflow', {}).get('outfits', []))


def compose_character_finals(service, project, job):
    """Derive identity boards from project-confirmed inputs, without another model call."""
    for step in job['steps']:
        if not automatic_final(job, step):
            continue
        step['generation_enabled'] = False
        if step['status'] == 'confirmed' or step['status'] in {'generating', 'submitting', 'submitted', 'querying'}:
            continue
        dependencies = [next(s for s in job['steps'] if s['key'] == key) for key in step['dependencies']]
        if len(dependencies) != 2 or not all(s['status'] == 'confirmed' and s.get('project_confirmation') for s in dependencies):
            continue
        try:
            inputs = service.dependencies(job, step)
            head = next(a for a in inputs if a['category'] == '大头照')
            clothes = next(a for a in inputs if a['category'] == '服装三视图')
            source_receipts = [{'asset_id': s['selected'], 'output_key': s['key'],
                                'confirmation_id': s['project_confirmation']['id']} for s in dependencies]
            board = Image.new('RGB', (2048, 1152), 'white')
            with Image.open(head['original']) as image:
                panels = [ImageOps.exif_transpose(image).convert('RGBA')]
            with Image.open(clothes['original']) as image:
                image = ImageOps.exif_transpose(image).convert('RGBA')
                panels.extend(image.crop((round(i * image.width / 3), 0, round((i + 1) * image.width / 3), image.height)) for i in range(3))
            for i, panel in enumerate(panels):
                panel = ImageOps.contain(panel, (512, 1152), Image.Resampling.LANCZOS)
                board.paste(panel, (i * 512 + (512 - panel.width) // 2, (1152 - panel.height) // 2), panel)
            path = service.lib.root / 'requests' / job['id'] / step['key'] / (uuid4().hex + '.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            board.save(path)
            check = service.skill.technical_check(path, '16:9')
            if not check['passed']:
                raise ValueError(check['message'])
            asset, _ = service.lib.import_file(path, project=project['name'], role='母版', category='人物1x4',
                provenance={'kind': 'character_composite', 'job_id': job['id'], 'project_id': project['id'],
                            'ast_id': job['ast_id'], 'output_id': step['key'], 'state': step['state'],
                            'source_narrative': project['source'], 'source_confirmations': source_receipts})
            receipt = {'id': uuid4().hex[:16], 'at': datetime.now(timezone.utc).isoformat(),
                       'asset_id': asset['id'], 'source': project['source'], 'technical_qc': check,
                       'image_sha256': digest(Path(asset['original'])),
                       'method': '已确认大头照与服装三视图自动拼版', 'source_confirmations': source_receipts}
            asset.update(name=job['title'] + '_' + step['name'] + '_' + asset['id'][:6], group=job['title'],
                         status='confirmed', confirmation=receipt, technical_check=check, backup={'status': 'pending'})
            asset.setdefault('project_confirmations', []).append({'project_id': project['id'], 'ast_id': job['ast_id'], **receipt})
            service.lib.save(asset)
            service.lib.auto_group_character(asset['id'], f'project:{project["id"]}:{job["ast_id"]}', job['title'])
            service.copy_to_project(project, service.lib.get(asset['id']))
            if step.get('outputs'):
                step.setdefault('history', []).append({k: copy.deepcopy(step.get(k)) for k in ('selected', 'outputs', 'project_confirmation', 'prompt', 'audit')})
            step.update(selected=asset['id'], outputs=[asset['id']], status='confirmed', project_confirmation=receipt,
                        ratio='16:9', message='', composition_error=False)
            service.lib.backup_wakeup.set()
        except Exception as exc:
            step.update(composition_error=True, message='人物1×4自动合成未完成：' + str(exc)[:400])
    service.lib.save(job, 'jobs')


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
                    s['generation_enabled'] = False
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
        compose_character_finals(service, p, job)
        return service.detail(p['id'])
