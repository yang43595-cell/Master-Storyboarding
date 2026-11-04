"""Project bridge acceptance checks using isolated project files and a fake Vault guard."""
import copy
import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_library as m
import visual_asset_projects as pmod
from core.artifacts import load_manifest
from core.project_state import new_project_state, write_project_state, load_project_state


def fixture(base, *, product=False):
    project = base / '测试项目'
    manage = project / '00_项目管理'
    manage.mkdir(parents=True)
    narrative = {'schema_version': '1.0', 'project_id': '验收项目', 'revision': 'R01', 'status': 'confirmed',
        'human_readable_path': '叙事.md', 'objective': '测试已有图复用与场景制作', 'locked_facts': [],
        'events': [{'event_id': 'EVT-01', 'summary': '进入房间', 'start_state': '门外', 'change': '进入', 'end_state': '室内', 'participants': [], 'depends_on': []}],
        'dialogues': [], 'asset_requirements': [], 'change_summary': [], 'open_questions': []}
    for ast, cat, name in [('AST-ROOM', 'LOCATION', '采光房间'), ('AST-CUP', 'PROP', '蓝色杯子')]:
        if product and ast == 'AST-CUP':
            cat = 'REAL_PRODUCT'
        requirement = {'asset_id': ast, 'category': cat, 'name': name,
            'narrative_purpose': '保持房间与实物连续性', 'source_event_ids': ['EVT-01'], 'identity_constraints': ['保留真实形状'],
            'required_states': ['常态'], 'interactions': [],
            'reference_paths': [str(base / '测试参考.png')] if cat == 'REAL_PRODUCT' else [], 'unknowns': []}
        if cat == 'LOCATION':
            requirement['spatial_scope'] = 'SINGLE_CONTIGUOUS_SPACE'
        narrative['asset_requirements'].append(requirement)
    path = project / 'narrative.json'
    pmod.write(path, narrative)
    pmod.write(manage / 'artifact_manifest.json', {'schema_version': '1.0', 'artifacts': [
        {'artifact_id': 'narrative-r01', 'logical_id': 'narrative.package', 'type': 'narrative-package',
         'revision': 'R01', 'status': 'current', 'user_approved': True, 'path_or_url': str(path),
         'checksum': pmod.digest(path)}]})
    state = new_project_state(current_stage='storyboard-planner')
    state.update(current_artifacts=['narrative-r01'], next_stage='storyboard-director',
                 user_confirmations=[{'artifact_ids': ['narrative-r01'], 'at': pmod.now()}])
    write_project_state(project, state)
    library = m.Library(base / 'library', vault=base / 'vault')
    img = base / '测试参考.png'
    Image.new('RGB', (400, 300), '#5091ab').save(img)
    asset, _ = library.import_file(img, project='验收', category='场景', role='参考')
    p = library.projects.bind(str(project))
    return library, p, asset


def update_narrative_checksum(project_root):
    """Keep isolated manifests bound to the exact narrative bytes after fixture edits."""
    project_root = Path(project_root)
    narrative = project_root / 'narrative.json'
    manifest = load_manifest(project_root)
    entry = next(item for item in manifest['artifacts'] if item['artifact_id'] == 'narrative-r01')
    entry['checksum'] = pmod.digest(narrative)
    pmod.write(project_root / '00_项目管理/artifact_manifest.json', manifest)


def plan_result():
    result = {'rows': []}
    for ast, kind, views in [('AST-ROOM', '环境', ['45°', '正面', '反面', '俯视']), ('AST-CUP', '物品', ['正面'])]:
        outputs = []
        for i, view in enumerate(views):
            outputs.append({'key': f'view{i}', 'kind': kind, 'view': view, 'ratio': '4:3', 'purpose': '连续性参考',
                            'state': '常态', 'rationale': '当前需求中的必要展示面', 'dependencies': [], 'must_not_inherit': ['临时污损']})
        result['rows'].append({'ast_id': ast, 'outputs': outputs, 'rationale': '复用优先，只处理需求内参考', 'blockers': []})
    return result



def synthetic_vault(root):
    """Independent synthetic files: tests must not depend on a personal Vault."""
    from test_stage_context_cli import _method_vault
    _method_vault(root)
    for skill in ('asset-image-assistant', 'storyboard-director'):
        profile_path = m.ROOT / 'skills' / skill / 'knowledge-profile.json'
        for method in json.loads(profile_path.read_text(encoding='utf-8'))['professional_methods']:
            path = root / method['path']
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('---\ntitle: Synthetic test method\nversion: 2026-09-11\nstatus: enabled\nlifecycle:\n  state: current\n---\n\n# Synthetic method\n\nIsolated complete method body; not a production reference.\n', encoding='utf-8')
            if method.get('template_path'):
                target = root / method['template_path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('Synthetic optional method template; test only.\n', encoding='utf-8')
    profile = m.asset_skill.profile()
    templates = root / 'wiki/reference/提示词模板'
    templates.mkdir(parents=True, exist_ok=True)
    for name in {r['template'] for r in profile['recipes'].values() if 'template' in r}:
        (templates / name).write_text('\n'.join(
            f"```text\nSynthetic test template view {i}; preserve the supplied reference 用户所选照片. "
            + {1: '从头顶到锁骨的紧凑大头特写，画面内不出现任何衣服，下边缘止于锁骨下缘', 2: '1×3 网格', 3: '1×4'}.get(i, '合成视图') + '\n```'
            for i in range(5)), encoding='utf-8')
    meta = {'id': 'MSB-KB-PROMPT-002', 'canonical_key': 'synthetic.prompt',
        'kind': 'rule', 'status': 'enabled', 'authority': 'wiki',
        'projects': ['Master-Storyboarding'], 'stages': ['asset-image-assistant'],
        'topics': ['image-prompt'], 'triggers': [], 'conflicts_with': [], 'priority': 1,
        'sources': [], 'applies_when': 'synthetic isolated asset input test'}
    page = root / 'wiki/concepts/03-AI生成与视频/提示词污染.md'
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text('<!-- msb-knowledge\n' + json.dumps(meta) +
        '\n-->\nSynthetic observable prompt rule.\n^msb-kb-prompt-002\n<!-- /msb-knowledge -->', encoding='utf-8')
    return root


class ProjectTests(unittest.TestCase):
    def test_project_upload_is_a_reference_not_a_deliverable_or_vault_asset(self):
        import visual_asset_upload
        source = self.base / 'portrait-input.png'
        Image.new('RGB', (511, 300), 'purple').save(source)
        original = source.read_bytes()
        index_path = self.lib.vault / '资产库' / '资产索引.json'
        before = index_path.read_bytes()
        job = self.job()
        with patch.object(visual_asset_upload, 'pick_local_files', return_value=[str(source)]), \
             patch.object(visual_asset_upload, 'recognize') as recognize:
            result = self.lib.projects.action({'action': 'upload-reference', 'project_id': self.p['id'],
                                              'job_id': job['id'], 'index': 0})
        recognize.assert_not_called()
        ref = self.lib.get(result['asset_id'])
        self.assertTrue(ref['reference_only'])
        self.assertEqual((ref['width'], ref['height']), (511, 300))
        self.assertEqual(Path(ref['original']).read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(Path(ref['original']).is_relative_to(Path(self.p['path']) / '04_资产' / '项目参考'))
        self.assertEqual(index_path.read_bytes(), before)
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertIn(ref['id'], step['reference_ids'])
        self.assertNotIn(ref['id'], step['outputs'])
        self.assertEqual(step['status'], 'waiting')
        request = self.lib.projects.request(self.lib.get(job['id'], 'jobs'), 0)
        self.assertIn(ref['original'], request['reference_paths'])
        with self.assertRaisesRegex(ValueError, '生成参考'):
            self.lib.projects.select({'job_id': job['id'], 'index': 0, 'asset_id': ref['id']})
        with self.assertRaisesRegex(ValueError, '项目参考'):
            self.lib.confirm(ref['id'])
        with self.assertRaisesRegex(ValueError, '项目参考'):
            self.lib.export([ref['id']])
        reopened = m.Library(self.lib.root, vault=self.lib.vault)
        self.assertEqual(reopened.get(ref['id'])['original'], ref['original'])
        self.assertTrue(Path(ref['original']).parent.joinpath('参考记录.json').is_file())

    def test_legacy_reference_relocation_preserves_id_links_and_confirmed_result(self):
        source = self.base / 'legacy-input.png'
        Image.new('RGB', (400, 300), 'yellow').save(source)
        ref, _ = self.lib.import_file(source, category='场景', provenance={'project_id': self.p['id']})
        ref['provenance']['confirmation_scope'] = 'reference_only'
        self.lib.save(ref)
        old = Path(ref['original'])
        original = old.read_bytes()
        job = self.job()
        self.lib.projects.select({'job_id': job['id'], 'index': 0, 'asset_id': ref['id'], 'as_reference': True})
        self.select_confirm(job, 0)
        before_job = self.lib.get(job['id'], 'jobs')
        output = self.lib.get(before_job['steps'][0]['selected'])
        moved = self.lib.relocate_project_reference(ref['id'], self.p)
        self.assertEqual(moved['id'], ref['id'])
        self.assertEqual(Path(moved['original']).read_bytes(), original)
        self.assertFalse(old.exists())
        index = pmod.read(self.lib.vault / '资产库' / '资产索引.json')
        self.assertFalse(any(r['library_asset_id'] == ref['id'] for r in index['assets']))
        self.assertEqual(self.lib.get(job['id'], 'jobs'), before_job)
        self.assertEqual(self.lib.get(output['id']), output)
        restarted = m.Library(self.lib.root, vault=self.lib.vault)
        self.assertEqual(restarted.get(ref['id'])['original'], moved['original'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        env = patch.dict(os.environ, {'MSB_VAULT_ROOT': str(synthetic_vault(self.base / 'synthetic-vault'))})
        env.start()
        self._synthetic_env = env
        self.addCleanup(env.stop)
        self.lib, self.p, self.asset = fixture(self.base)
        service = self.lib.projects
        p = service.get(self.p['id'])
        for row in p['rows']:
            row['reference_ids'] = [self.asset['id']]
        service.save(p)
        service.apply_plan(p, p['rows'], plan_result(), {'version': m.asset_skill.profile()['version'], 'mode': 'project'})

    def tearDown(self):
        self._synthetic_env.stop()
        self.temp.cleanup()

    def job(self, row=0):
        return self.lib.projects.detail(self.p['id'])['rows'][row]['job']

    def test_manual_attach_checks_current_project_and_active_state(self):
        job = self.job()
        source = self.base / 'manual-result.png'
        Image.new('RGB', (400, 300), 'blue').save(source)
        job['project_stale'] = True
        self.lib.save(job, 'jobs')
        with self.assertRaisesRegex(ValueError, '旧版需求'):
            self.lib.attach(job['id'], 0, [source], manual=True)
        job.pop('project_stale')
        job['steps'][0]['status'] = 'generating'
        self.lib.save(job, 'jobs')
        with self.assertRaisesRegex(ValueError, '正在处理'):
            self.lib.attach(job['id'], 0, [source], manual=True)
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['outputs'], [])
        # The original worker's bounded completion remains able to attach its result.
        self.lib.attach(job['id'], 0, [source])
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['status'], 'awaiting_review')
        manual_source = self.base / 'manual-other-tool.png'
        Image.new('RGB', (400, 300), 'green').save(manual_source)
        self.lib.attach(job['id'], 0, [manual_source], manual=True)
        output = self.lib.get(self.lib.get(job['id'], 'jobs')['steps'][0]['outputs'][-1])
        self.assertEqual(output['provenance']['kind'], 'manual_result_import')
        self.assertFalse({'provider', 'model', 'authorization'} & output['provenance'].keys())

    def test_task_gets_own_copy_and_project_images_do_not_flow_back(self):
        source = Path(self.asset['original'])
        before = len(self.lib.items())
        self.lib.projects.select({'job_id': self.job()['id'], 'index': 0, 'asset_id': self.asset['id']})
        copy_path = self.lib.projects.copy_to_project(self.p, self.lib.get(self.asset['id']))
        self.assertTrue(copy_path.is_file())
        self.assertEqual(copy_path.read_bytes(), source.read_bytes())
        self.assertNotEqual(copy_path, source)
        self.assertEqual(len(self.lib.items()), before)
        with self.assertRaisesRegex(ValueError, '不回流'):
            self.lib.import_tree(self.p['path'])
        task_image = Path(self.p['path']) / '04_资产' / '任务候选.png'
        Image.new('RGB', (400, 300), 'blue').save(task_image)
        with self.assertRaisesRegex(ValueError, '不回流'):
            self.lib.attach(self.job()['id'], 0, [task_image])

    def test_existing_scene_choice_cannot_accidentally_generate(self):
        payload={'project_id':self.p['id'],'ast_id':'AST-ROOM','route':'reuse'}
        self.lib.projects.production_route(payload)
        with self.assertRaises(ValueError):
            self.lib.projects.request(self.job(),0)
        self.select_confirm(self.job(),0)
        self.lib.projects.production_route({**payload,'route':'build'})
        self.assertTrue(self.job()['steps'][0]['project_confirmation'])
        self.assertTrue(self.job()['steps'][1]['generation_enabled'])

    def test_direct_selection_during_other_planning_preserves_planning_and_images(self):
        service=self.lib.projects
        p=service.get(self.p['id'])
        p['rows'][1]['job_id']=None
        p['planning']={'status':'running','run_id':'other-scene'}
        service.save(p)
        before=service.get(p['id'])['planning']
        result=service.prepare_reuse({'project_id':p['id'],'ast_id':'AST-CUP'})
        self.assertEqual(result['planning'],before)
        job=result['rows'][1]['job']
        self.assertTrue(job['direct_reuse'])
        service.select({'job_id':job['id'],'index':0,'asset_id':self.asset['id']})
        self.assertEqual(service.lib.get(job['id'],'jobs')['steps'][0]['ratio'],'400:300')
        oldrow=copy.deepcopy(p['rows'][1])
        with self.assertRaises(ValueError):
            service.apply_plan(service.get(p['id']),[oldrow],{'rows':[plan_result()['rows'][1]]},{})
        self.assertEqual(service.get(p['id'])['rows'][1]['job_id'],job['id'])

    def test_location_requires_explicit_single_space_and_route_never_splits_it(self):
        service=self.lib.projects
        p=service.get(self.p['id'])
        with patch.object(service,'fresh'):
            with self.assertRaisesRegex(ValueError, '不按名称或状态拆分'):
                service.production_route({'project_id':p['id'],'ast_id':'AST-ROOM','route':'build','scene':'家中客厅'})
        path = Path(self.p['path']) / 'narrative.json'
        narrative = pmod.read(path)
        del narrative['asset_requirements'][0]['spatial_scope']
        pmod.write(path, narrative)
        with self.assertRaisesRegex(Exception, 'spatial_scope|单一连续空间'):
            pmod.source_project(self.p['path'])

    def select_confirm(self, job, index):
        asset = self.asset
        if job['steps'][index]['kind'] == '物品':
            path = self.lib.root / 'object-three-view.png'
            Image.new('RGB', (1600, 900), 'blue').save(path)
            asset, _ = self.lib.import_file(path, category='物品')
        self.lib.projects.select({'job_id': job['id'], 'index': index, 'asset_id': asset['id']})
        return self.lib.projects.confirm({'job_id': job['id'], 'index': index, 'confirm': True})

    def backup(self):
        self.lib.vault = self.base / 'vault'
        self.lib.vault.mkdir(exist_ok=True)
        with patch.object(m.management, 'guard'):
            m.visual_asset_backup.run(self.lib, m.management)

    def test_requires_real_confirmed_narrative_and_no_table_external_rows(self):
        path = Path(self.p['path']) / 'narrative.json'
        narrative = pmod.read(path)
        manifest_path = Path(self.p['path']) / '00_项目管理/artifact_manifest.json'
        manifest = pmod.read(manifest_path)
        manifest['artifacts'][0]['user_approved'] = False
        pmod.write(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, '确认'):
            self.lib.projects.bind(self.p['path'])
        result = plan_result()
        result['rows'][0]['ast_id'] = 'AST-INVENTED'
        p = self.lib.projects.get(self.p['id'])
        with self.assertRaisesRegex(ValueError, '表外'):
            self.lib.projects.apply_plan(p, p['rows'], result, {})

    def test_prop_story_actions_can_reuse_one_appearance_master(self):
        service = self.lib.projects
        project = service.get(self.p['id'])
        project['rows'][1]['requirement']['required_states'] = ['吃剩', '被交换', '吃掉']
        result = plan_result()
        prop = result['rows'][1]
        prop['outputs'][0]['state'] = '吃剩'
        prop['rationale'] = '交换复用吃剩母版；吃掉过程由分镜承载，无需另外两套四视图。'
        service.apply_plan(project, project['rows'], result, {})
        job = self.job(1)
        self.assertEqual(len(job['steps']), 1)
        self.assertEqual(job['steps'][0]['state'], '吃剩')

    def test_scene_still_requires_all_visual_states(self):
        project = self.lib.projects.get(self.p['id'])
        project['rows'][0]['requirement']['required_states'] = ['白天', '夜晚']
        result = plan_result()
        for step in result['rows'][0]['outputs']:
            step['state'] = '白天'
        with self.assertRaisesRegex(ValueError, '完整覆盖'):
            self.lib.projects.apply_plan(project, project['rows'], result, {})

    def test_scene_dependencies_and_library_confirmation_are_separate(self):
        job = self.job()
        self.lib.confirm(self.asset['id'])
        with self.assertRaisesRegex(ValueError, '前置'):
            self.lib.step_request(job, 1)
        self.select_confirm(job, 0)
        request = self.lib.step_request(self.job(), 1)
        self.assertEqual(request['mode'], 'project')
        self.assertEqual(request['ast_id'], 'AST-ROOM')
        self.assertEqual(request['ratio'], '4:3')
        self.assertEqual(request['reference_paths'], [str(self.lib.projects.copy_to_project(self.p, self.lib.get(self.asset['id'])))])

    def test_confirm_is_idempotent_and_stale_alternative_cannot_replace_confirmed_parent(self):
        job = self.job()
        other_path = self.base / 'other-room.png'
        Image.new('RGB', (400, 300), 'red').save(other_path)
        other, _ = self.lib.import_file(other_path, category='场景')
        service = self.lib.projects
        for asset in (self.asset, other):
            service.select({'job_id': job['id'], 'index': 0, 'asset_id': asset['id']})
        payload = {'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id'], 'confirm': True}
        service.confirm(payload)
        self.select_confirm(self.job(), 1)
        before = self.lib.get(job['id'], 'jobs')
        before['steps'][0].update(generation_run_id='old-run', submit_id='old-task',
            authorization={'at': 'earlier'}, query_blocked=True, query_failures=3)
        self.lib.save(before, 'jobs')
        before_asset = self.lib.get(self.asset['id'])
        service.confirm(payload)
        self.assertEqual(self.lib.get(job['id'], 'jobs'), before)
        self.assertEqual(self.lib.get(self.asset['id']), before_asset)
        with self.assertRaisesRegex(ValueError, '重新选图'):
            service.confirm({**payload, 'asset_id': other['id']})
        self.assertEqual(self.lib.get(job['id'], 'jobs'), before)
        service.reopen({'job_id': job['id'], 'index': 0})
        reopened = self.lib.get(job['id'], 'jobs')['steps'][0]
        for field in ('generation_run_id', 'submit_id', 'authorization', 'query_blocked', 'query_failures'):
            self.assertNotIn(field, reopened)
        self.assertEqual(reopened['history'][-1]['submit_id'], 'old-task')
        self.assertEqual(reopened['history'][-1]['generation_run_id'], 'old-run')
        self.assertEqual(reopened['history'][-1]['authorization'], {'at': 'earlier'})
        service.select({'job_id': job['id'], 'index': 0, 'asset_id': other['id']})
        service.confirm({**payload, 'asset_id': other['id']})
        revised = self.lib.get(job['id'], 'jobs')
        self.assertEqual(revised['steps'][0]['selected'], other['id'])
        self.assertEqual(revised['steps'][0]['history'][-1]['project_confirmation'], before['steps'][0]['project_confirmation'])
        self.assertIsNone(revised['steps'][1]['project_confirmation'])

    def test_changed_source_invalidates_only_affected_requirement(self):
        self.select_confirm(self.job(), 0)
        path = Path(self.p['path']) / 'narrative.json'
        narrative = pmod.read(path)
        narrative['asset_requirements'][1]['identity_constraints'].append('杯沿无缺口')
        pmod.write(path, narrative)
        update_narrative_checksum(path.parent)
        with self.assertRaisesRegex(ValueError, '变化'):
            self.lib.projects.select({'job_id': self.job()['id'], 'index': 1, 'asset_id': self.asset['id']})
        result = self.lib.projects.sync(self.p['id'])
        self.assertTrue(result['rows'][0]['job']['steps'][0]['project_confirmation'])
        self.assertIsNone(result['rows'][1]['job'])

    def test_backup_lock_failure_then_retry_trash_and_restore(self):
        self.select_confirm(self.job(), 0)
        self.lib.vault = self.base / 'vault'
        self.lib.vault.mkdir(exist_ok=True)
        with patch.object(m.management, 'guard', side_effect=ValueError('write lock exists')):
            m.visual_asset_backup.run(self.lib, m.management)
        self.assertEqual(self.lib.get(self.asset['id'])['backup']['status'], 'pending')
        self.backup()
        image = self.lib.get(self.asset['id'])
        self.assertEqual(image['backup']['status'], 'saved')
        index = pmod.read(self.lib.vault / '资产库/资产索引.json')
        self.assertEqual(len(index['assets']), 1)
        with patch.object(m.management, 'guard'):
            m.management.manage(self.lib, [image['id']], 'trash')
            self.assertTrue(Path(self.lib.get(image['id'])['backup']['path']).is_file())
            m.management.manage(self.lib, [image['id']], 'restore')
        self.assertTrue(Path(self.lib.get(image['id'])['backup']['path']).is_file())

    def test_export_normalizes_legacy_structured_current_artifacts(self):
        root = Path(self.p['path'])
        state = load_project_state(root)
        entry = {'artifact_id': 'narrative-r01', 'logical_id': 'narrative.package', 'revision': 'R01'}
        state['current_artifacts'] = [entry]
        # Reproduce an existing legacy file; current writers only accept artifact IDs.
        pmod.write(root / '00_项目管理/project_state.json', state)
        for row in self.lib.projects.detail(self.p['id'])['rows']:
            for i in range(len(row['job']['steps'])):
                self.select_confirm(row['job'], i)
        self.backup()
        result = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        current = load_project_state(root)['current_artifacts']
        self.assertIn('narrative-r01', current)
        self.assertTrue(all(isinstance(a, str) for a in current))
        self.assertEqual(current.count(result['artifact_id']), 1)
        self.assertTrue(Path(result['handoff']).is_file())
        self.assertEqual(self.lib.projects.export({'project_id': self.p['id'], 'confirm': True}), result)

    def test_export_group_preserves_stage_and_is_idempotent(self):
        for row in self.lib.projects.detail(self.p['id'])['rows']:
            for i in range(len(row['job']['steps'])):
                self.select_confirm(row['job'], i)
        with self.assertRaisesRegex(ValueError, '备份'):
            self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        self.backup()
        result = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        out = pmod.read(result['manifest'])
        pmod.validate(out, 'visual-asset-manifest.schema.json')
        self.assertEqual(len(out['assets'][0]['confirmed_outputs']), 4)
        self.assertTrue(all(Path(o['path']).is_file() for a in out['assets'] for o in a['confirmed_outputs']))
        state = load_project_state(Path(self.p['path']))
        self.assertEqual(state['current_stage'], 'storyboard-planner')
        self.assertEqual(state['next_stage'], 'storyboard-director')
        self.assertEqual(self.lib.projects.export({'project_id': self.p['id'], 'confirm': True}), result)
        self.assertEqual(len(load_manifest(Path(self.p['path']))['artifacts']), 2)
        handoff = self.lib.projects.continue_project(self.p['id'])
        self.assertIn('其他交接条件尚未满足', handoff['warning'])
        self.assertIn('核对交接条件', handoff['instruction'])
        # Rework a confirmed image keeps the old delivery and resets dependent confirmations.
        self.lib.projects.reopen({'job_id': self.job()['id'], 'index': 0})
        self.assertTrue(Path(result['manifest']).is_file())
        self.assertTrue(all(not s['project_confirmation'] for s in self.job()['steps']))

    def test_product_export_does_not_invent_fact_review_from_user_selection(self):
        lib, project, reference = fixture(self.base / 'product-case', product=True)
        service = lib.projects
        p = service.get(project['id'])
        for row in p['rows']:
            row['reference_ids'] = [reference['id']]
        service.save(p)
        plan = plan_result()
        plan['rows'][1]['outputs'][0]['kind'] = '产品'
        service.apply_plan(p, p['rows'], plan,
                           {'version': m.asset_skill.profile()['version'], 'mode': 'project'})
        for row in service.detail(p['id'])['rows']:
            job = row['job']
            for index, step in enumerate(job['steps']):
                width, height = [int(n) for n in step['ratio'].split(':')]
                path = self.base / (job['ast_id'] + '-' + str(index) + '.png')
                Image.new('RGB', (width * 100, height * 100), 'blue').save(path)
                image, _ = lib.import_file(path, category=step['kind'])
                service.select({'job_id': job['id'], 'index': index, 'asset_id': image['id']})
                service.confirm({'job_id': job['id'], 'index': index, 'confirm': True})
        lib.vault.mkdir(exist_ok=True)
        with patch.object(m.management, 'guard'):
            m.visual_asset_backup.run(lib, m.management)
        result = service.export({'project_id': p['id'], 'confirm': True})
        out = pmod.read(result['manifest'])
        product = next(a for a in out['assets'] if a['category'] == 'REAL_PRODUCT')
        self.assertTrue(product['confirmed_outputs'])
        for candidate in product['candidates']:
            self.assertTrue(candidate['technical_qc']['path_exists'])
            self.assertNotIn('product_fact_check', candidate['technical_qc'])
        scene = next(a for a in out['assets'] if a['category'] == 'LOCATION')
        self.assertTrue(all(c['technical_qc']['product_fact_check'] == 'not_applicable'
                            for c in scene['candidates']))
        receipt = pmod.read(Path(result['manifest']).parent / '用户确认记录.json')
        self.assertTrue(all(item['confirmation']['image_sha256'] for item in receipt['items']))
        self.assertIn('未记录 Agent 产品事实核对结果', Path(result['handoff']).read_text())
        pmod.validate(out, 'visual-asset-manifest.schema.json')
        self.assertEqual(service.export({'project_id': p['id'], 'confirm': True}), result)

    def test_scene_mode_change_invalidates_existing_export(self):
        job = self.job()
        self.lib.projects.scene_output_mode({'project_id': self.p['id'], 'ast_id': 'AST-ROOM', 'mode': 'single'})
        for row in self.lib.projects.detail(self.p['id'])['rows']:
            current = self.lib.get(row['job_id'], 'jobs')
            for index, step in enumerate(current['steps']):
                if step.get('required') is not False:
                    self.select_confirm(current, index)
        self.backup()
        exported = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        # Repeating the already-effective single-output contract must be a no-op.
        self.lib.projects.scene_output_mode({'project_id': self.p['id'], 'ast_id': 'AST-ROOM', 'mode': 'single'})
        self.assertEqual(self.lib.projects.get(self.p['id'])['export'], exported)
        self.lib.projects.scene_output_mode({'project_id': self.p['id'], 'ast_id': 'AST-ROOM', 'mode': 'four'})
        project = self.lib.projects.get(self.p['id'])
        self.assertNotIn('export', project)
        self.assertEqual(project['export_history'][-1], exported)
        artifact = next(a for a in load_manifest(Path(self.p['path']))['artifacts']
                        if a['artifact_id'] == exported['artifact_id'])
        self.assertEqual(artifact['status'], 'stale')

    def test_any_angle_scene_input_creates_four_outputs_without_counting_source(self):
        service = self.lib.projects
        for view in ('俯视布局', '平视', '侧视'):
            with self.subTest(view=view):
                p = service.get(self.p['id'])
                p['rows'][0]['job_id'] = None
                service.save(p)
                result = service.prepare_reuse({'project_id': p['id'], 'ast_id': 'AST-ROOM'})
                old = result['rows'][0]['job']
                self.assertEqual(old['steps'][0]['view'], '任意视角')
                self.asset['provenance'] = {'view': view}
                self.lib.save(self.asset)
                self.select_confirm(old, 0)
                confirmed = self.lib.get(old['id'], 'jobs')
                service.scene_output_mode({'project_id': p['id'], 'ast_id': 'AST-ROOM', 'mode': 'four'})
                job = self.job()
                self.assertNotEqual(job['id'], old['id'])
                self.assertEqual(self.lib.get(old['id'], 'jobs'), confirmed)
                self.assertEqual([s['view'] for s in job['steps']], ['45°', '正面', '反面', '俯视'])
                self.assertTrue(all(not s.get('selected') and s['generation_enabled'] for s in job['steps']))
                output_path = self.base / 'environment-output.png'
                Image.new('RGB', (1600, 900), 'green').save(output_path)
                output, _ = self.lib.import_file(output_path, category='场景')
                for index in range(4):
                    request = service.request(self.job(), index)
                    self.assertIn(self.asset['id'], [a['id'] for a in request['reference_records']])
                    self.assertIn('输入参考可能是俯视', request['prompt'])
                    service.select({'job_id': job['id'], 'index': index, 'asset_id': output['id']})
                    service.confirm({'job_id': job['id'], 'index': index, 'confirm': True})

        p = service.get(self.p['id'])
        p['rows'][0]['job_id'] = None
        service.save(p)
        service.scene_reference({'project_id': p['id'], 'ast_id': 'AST-ROOM', 'asset_id': self.asset['id']})
        self.assertEqual(len(self.job()['steps']), 4)

    def test_scene_reference_preserves_confirmed_master_and_enables_remaining_views(self):
        service = self.lib.projects
        job = self.job()
        job['steps'] = job['steps'][:1]
        self.lib.save(job, 'jobs')
        self.select_confirm(job, 0)
        first = copy.deepcopy(self.job()['steps'][0])
        service.scene_reference({'project_id': self.p['id'], 'ast_id': 'AST-ROOM', 'asset_id': self.asset['id'], 'mode': 'four'})
        job = self.job()
        self.assertEqual(len(job['steps']), 4)
        self.assertEqual(job['steps'][0]['project_confirmation'], first['project_confirmation'])
        self.assertEqual(job['steps'][0]['selected'], first['selected'])
        self.assertEqual(job['steps'][1]['reference_ids'], [self.asset['id']])
        self.assertEqual(job['steps'][1]['dependencies'], [first['key']])

    def test_generation_gate_and_restart_do_not_resubmit(self):
        task = {'job_id': self.job()['id'], 'index': 0, 'prompt': '未经审查'}
        with self.assertRaisesRegex(ValueError, '审查'):
            self.lib.projects.batch({'project_id': self.p['id'], 'mode': 'generate', 'tasks': [task], 'authorize': True})
        p = self.lib.projects.get(self.p['id'])
        p['batch'] = {'status': 'running', 'entries': [{'status': 'queued'}, {'status': 'running'}]}
        p['planning'] = {'status': 'running'}
        self.lib.projects.save(p)
        self.lib.projects.recover()
        p = self.lib.projects.get(p['id'])
        self.assertEqual(p['batch']['status'], 'interrupted')
        self.assertTrue(all(e['status'] == 'interrupted' for e in p['batch']['entries']))

    def test_project_uses_project_checks_not_library_scope(self):
        job = self.job(1)
        request = self.lib.step_request(job, 0)
        data = {'prompt': '白色背景，蓝色杯子正面，保留杯柄形状。', 'passed': True,
                'issues': [], 'checks': [c['id'] + '：已核对' for c in m.asset_skill.checks('project')]}
        self.assertTrue(m.asset_skill.validate_review(data, 'project')['passed'])
        self.assertIn('requirement', request)
        self.assertIn('narrative', request)
        self.lib.attach(job['id'], 0, [self.asset['original']])
        asset = self.lib.get(self.lib.get(job['id'], 'jobs')['steps'][0]['outputs'][0])
        self.assertEqual(asset['provenance']['ast_id'], 'AST-CUP')
        self.assertEqual(asset['backup']['status'], 'pending')

    def test_wardrobe_task_package_keeps_applicable_facts_and_excludes_dialogue(self):
        """The Codex call gets a bounded production item, not a shortened whole narrative."""
        job = self.job(1)
        request = self.lib.step_request(job, 0)
        request['kind'] = '服装'
        request.pop('template_source', None)  # This fixture changes the output kind from PROP.
        request['requirement'] = {**request['requirement'], 'asset_id': 'AST-POLO', 'category': 'WARDROBE',
                                  'name': 'Polo 衫', 'reference_paths': [], 'identity_constraints': ['深蓝色 Polo 衫']}
        request['narrative'] = {**request['narrative'], 'locked_facts': ['袖口不得有品牌文字'],
                                'dialogues': [{'speaker': '无关角色', 'line': '不会进入服装任务包'}],
                                'events': request['narrative']['events'] + [{'event_id': 'EVT-OTHER', 'summary': '无关事件'}]}
        package = m.asset_skill.build_task_package(request, startup_text='动态规则：服装袖口不得出现未确认标识。')
        encoded = json.dumps(package, ensure_ascii=False)
        self.assertIn('深蓝色 Polo 衫', encoded)
        self.assertIn('袖口不得有品牌文字', encoded)
        self.assertIn('未锁定', encoded)
        self.assertIn('动态规则：服装袖口不得出现未确认标识。', encoded)
        self.assertIn('人物母版构建指南', encoded)
        self.assertNotIn('不会进入服装任务包', encoded)
        self.assertNotIn('EVT-OTHER', encoded)
        changed = m.asset_skill.build_task_package({**request, 'requirement': {**request['requirement'], 'identity_constraints': ['米白色 Polo 衫']}})
        self.assertNotEqual(package['request']['requirement'], changed['request']['requirement'])
        other_project = m.asset_skill.build_task_package({**request, 'narrative': {**request['narrative'], 'project_id': '另一个项目'}})
        self.assertNotEqual(package['request']['narrative']['project_id'], other_project['request']['narrative']['project_id'])

    def test_prepare_has_one_input_with_template_and_dynamic_rules(self):
        request = self.lib.step_request(self.job(1), 0)
        request['kind'] = '人物1x4'
        request['asset_method_scopes'] = ['character']
        request.pop('template_source', None)
        startup = Path(request['project_path']) / '00_项目管理/执行上下文/stage_startup.md'
        startup.parent.mkdir(parents=True, exist_ok=True)
        startup.write_text('动态知识正文', encoding='utf-8')
        before = {p: p.read_bytes() for p in Path(request['project_path']).rglob('*') if p.is_file()}
        with patch.object(pmod.subprocess, 'run', side_effect=AssertionError('Web input must not execute stage CLI')):
            body, evidence = m.asset_skill.prepare(self.base / 'ordinary', request)
        package = json.loads(body)
        self.assertIn('MSB-KB-PROMPT-002', package['rules']['startup_dynamic_knowledge'])
        self.assertNotIn('动态知识正文', package['rules']['startup_dynamic_knowledge'])
        self.assertEqual(before, {p: p.read_bytes() for p in Path(request['project_path']).rglob('*') if p.is_file()})
        self.assertEqual(evidence['attestation'], 'input_assembled_not_agent_reading')
        self.assertEqual({m['id'] for m in package['professional_methods']}, {'ASSET-CHARACTER-METHOD', 'IMAGE-CONTINUITY-METHOD'})
        self.assertTrue(package['production_item']['template']['content'])
        self.assertEqual(package['production_item']['template']['block_index'], 3)
        self.assertTrue(Path(evidence['task_package']['path']).is_file())

    def test_check_current_prompt_does_not_replay_old_observations(self):
        """An explicit recheck uses source inputs, not previous model assertions."""
        request = self.lib.step_request(self.job(1), 0)
        folder = self.base / 'review-input'
        result = {'prompt': '最终服装提示词', 'passed': True, 'observations': ['独立观察'],
                  'uncertainties': [], 'checks': ['ASSET-SCOPE：当前 AST'], 'issues': []}
        def fake_run(command, **kwargs):
            self.assertIn('最终服装提示词', kwargs['input'])
            self.assertNotIn('写作者私有观察', kwargs['input'])
            self.assertIn('已组装实际输入', kwargs['input'])
            Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            return pmod.subprocess.CompletedProcess(command, 0, '{"type":"completed"}', '')
        with patch.object(m.codex_bridge, '_run_prompt_cli', side_effect=fake_run):
            reviewed = m.codex_bridge.run('codex.exe', folder, 'review', request,
                                          {'prompt': '最终服装提示词', 'observations': ['写作者私有观察']}, '已组装实际输入')
        self.assertEqual(reviewed['prompt'], '最终服装提示词')

    def test_single_call_self_check_enforces_evidence_for_both_actions(self):
        request = self.lib.step_request(self.job(1), 0)
        for phase in ('draft', 'review'):
            for defect in (None, 'observations', 'checks', 'issues'):
                with self.subTest(phase=phase, defect=defect):
                    result = {'prompt': '最终服装提示词', 'passed': True,
                              'observations': ['参考图中的蓝色衣服'],
                              'uncertainties': [], 'checks': ['ASSET-SCOPE：当前 AST'], 'issues': []}
                    if defect:
                        result[defect] = ['未解决的冲突'] if defect == 'issues' else []
                    def fake_run(command, **kwargs):
                        self.assertIn('写出简短提示词并自查' if phase == 'draft' else 'prompt逐字保留用户原文', kwargs['input'])
                        self.assertNotIn('单独的审查轮次', kwargs['input'])
                        Path(command[command.index('--output-last-message') + 1]).write_text(
                            json.dumps(result, ensure_ascii=False), encoding='utf-8')
                        return pmod.subprocess.CompletedProcess(command, 0, '', '')
                    with patch.object(m.codex_bridge, '_run_prompt_cli', side_effect=fake_run) as call:
                        checked = m.codex_bridge.run('codex.exe', self.base / 'self-check',
                                                     phase, request, '当前稿', '实际输入')
                    call.assert_called_once()
                    self.assertEqual(checked['passed'], defect in (None, 'observations'))

    def test_prepare_preflight_failure_does_not_block_other_items(self):
        service = self.lib.projects
        payload = {'project_id': self.p['id'], 'mode': 'prepare', 'tasks': [
            {'job_id': self.job()['id'], 'index': 0, 'generation_direction': '保留卧室布局'}, {'job_id': self.job(1)['id'], 'index': 0}]}
        original_request = service.request
        def request(job, index):
            if job['ast_id'] == 'AST-ROOM':
                raise ValueError('缺少卧室空间参考图')
            return original_request(job, index)
        with patch.object(service, 'request', side_effect=request), patch.object(pmod.threading.Thread, 'start'):
            service.batch(payload)
        batch = service.get(self.p['id'])['batch']
        self.assertEqual([e['status'] for e in batch['entries']], ['error', 'queued'])
        self.assertIn('缺少卧室空间参考图', batch['entries'][0]['message'])
        self.assertEqual(self.job()['steps'][0]['audit']['status'], 'error')
        service.batch_running = False

    def test_batch_scheduler_failure_is_terminal_and_does_not_keep_spinning(self):
        service = self.lib.projects
        p = service.get(self.p['id'])
        p['batch'] = {'status': 'running', 'entries': [{'status': 'queued'}]}
        service.save(p)
        service.batch_running = True
        with patch.object(pmod, 'ThreadPoolExecutor', side_effect=RuntimeError('本机工作线程未启动')):
            service._batch_worker(p['id'])
        batch = service.get(p['id'])['batch']
        self.assertEqual(batch['status'], 'error')
        self.assertEqual(batch['entries'][0]['status'], 'error')
        self.assertFalse(service.batch_running)

    def test_empty_project_path_does_not_resolve_to_program_directory(self):
        for path in ['', '   ', None]:
            with self.assertRaisesRegex(ValueError, '请填写项目文件夹'):
                pmod.source_project(path)

    def test_object_plan_and_review_use_minimal_three_view_contract(self):
        job = self.job(1)
        step = job['steps'][0]
        self.assertEqual(step['ratio'], '16:9')
        self.assertEqual(step['view'], '正面、左侧、背面、俯视四视图')
        request = self.lib.step_request(job, 0)
        package = m.asset_skill.build_task_package(request)
        self.assertEqual([c['id'] for c in package['review_checks']], ['ASSET-SCOPE', 'ASSET-QC'])
        result = {'prompt':'用户自写的物品四视图，高清。','passed':True,'issues':[], 'checks':['ASSET-SCOPE：符合用户物品','ASSET-QC：正侧背俯四格，16:9']}
        self.assertTrue(m.asset_skill.validate_review(result, 'project', '物品')['passed'])

    def test_prop_can_generate_without_reference_but_advertised_product_cannot(self):
        job = self.job(1)
        job['steps'][0]['reference_ids'] = []
        with patch.object(self.lib.projects, 'dependencies', return_value=[]):
            request = self.lib.projects.request(job, 0)
        self.assertEqual(request['reference_paths'], [])
        self.assertTrue(request['allow_unreferenced_design'])
        project, row, step = self.lib.projects.context(job, 0)
        row['requirement']['category'] = 'REAL_PRODUCT'
        with patch.object(self.lib.projects, 'context', return_value=(project, row, step)), patch.object(self.lib.projects, 'dependencies', return_value=[]):
            with self.assertRaisesRegex(ValueError, '真实参考'):
                self.lib.projects.request(job, 0)

    def test_direction_change_persists_and_invalidates_old_approval(self):
        job = self.job()
        job['steps'][0].update(prompt='旧稿', audit={'status': 'passed', 'prompt': '旧稿'}, prompt_approval={'prompt': '旧稿'})
        self.lib.save(job, 'jobs')
        task = {'job_id': job['id'], 'index': 0, 'generation_direction': '明亮现代客厅'}
        with self.assertRaisesRegex(ValueError, '生成方向'):
            self.lib.projects.batch({'project_id': self.p['id'], 'mode': 'generate', 'tasks': [task], 'authorize': True})
        with patch.object(pmod.threading.Thread, 'start'):
            self.lib.projects.batch({'project_id': self.p['id'], 'mode': 'prepare', 'tasks': [task]})
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['generation_direction'], '明亮现代客厅')
        self.assertEqual(step['audit'], {})
        self.assertNotIn('prompt_approval', step)
        entry = self.lib.projects.get(self.p['id'])['batch']['entries'][0]
        self.assertEqual(entry['operation'], 'write')
        self.assertEqual(entry['request']['generation_direction'], '明亮现代客厅')

    def test_first_scene_uses_guide_and_derived_template_requires_reference(self):
        job = self.job()
        job['steps'][0]['reference_ids'] = []
        with patch.object(self.lib.projects, 'dependencies', return_value=[]):
            request = self.lib.projects.request(job, 0)
        package = m.asset_skill.build_task_package(request)
        self.assertEqual(package['production_item']['template']['status'], 'category-guide')
        request['template_source'] = ''
        with self.assertRaisesRegex(ValueError, '参考母图'):
            m.asset_skill.build_task_package(request)

    def test_prepare_handles_missing_and_existing_prompts_in_one_batch(self):
        jobs = [self.job(), self.job(1)]
        jobs[0]['steps'][0]['prompt'] = ''
        jobs[1]['steps'][0].update(prompt='用户已修改的提示词', audit={'status': 'stale'})
        for job in jobs:
            self.lib.save(job, 'jobs')
        tasks = [{'job_id': j['id'], 'index': 0} for j in jobs]
        with patch.object(pmod.threading.Thread, 'start'):
            self.lib.projects.batch({'project_id': self.p['id'], 'mode': 'prepare', 'tasks': tasks})
        calls = []
        def audit(payload):
            calls.append(payload['mode'])
            job = self.lib.get(payload['id'], 'jobs')
            step = job['steps'][0]
            if payload['mode'] == 'write':
                step['prompt'] = '新提示词'
            step['audit'] = {'status': 'passed', 'prompt': step['prompt']}
            self.lib.save(job, 'jobs')
        with patch.object(self.lib, 'audit_prompt', side_effect=audit), patch.object(self.lib, 'submit_imagegen') as generate:
            self.lib.projects._batch_worker(self.p['id'])
        self.assertCountEqual(calls, ['write', 'review'])
        self.assertEqual(self.lib.get(jobs[1]['id'], 'jobs')['steps'][0]['prompt'], '用户已修改的提示词')
        generate.assert_not_called()
        self.assertEqual(self.lib.projects.get(self.p['id'])['batch']['status'], 'done')

    def test_batch_starts_second_item_before_first_finishes(self):
        tasks = [{'job_id': self.job(i)['id'], 'index': 0} for i in range(2)]
        with patch.object(pmod.threading.Thread, 'start'):
            self.lib.projects.batch({'project_id': self.p['id'], 'mode': 'prepare', 'tasks': tasks})
        started, completed = [], []
        def finish():
            with self.lib.lock:
                for ident in started:
                    job = self.lib.get(ident, 'jobs')
                    job['steps'][0]['audit'] = {'status': 'passed'}
                    self.lib.save(job, 'jobs')
                completed.append(len(started))
        def audit(payload):
            started.append(payload['id'])
            job = self.lib.get(payload['id'], 'jobs')
            job['steps'][0]['audit'] = {'status': 'reviewing'}
            self.lib.save(job, 'jobs')
            if len(started) == 2:
                finish()
        timer = pmod.threading.Timer(2, finish)
        timer.start()
        try:
            with patch.object(self.lib, 'audit_prompt', side_effect=audit):
                self.lib.projects._batch_worker(self.p['id'])
        finally:
            timer.cancel()
        self.assertEqual(completed[0], 2)
        self.assertEqual([e['status'] for e in self.lib.projects.get(self.p['id'])['batch']['entries']], ['done', 'done'])

    def test_batch_sends_exact_approved_prompts_and_continues_after_failure(self):
        tasks = []
        for job in [self.job(), self.job(1)]:
            prompt = '保留参考颜色、轮廓和相对位置。'
            job['steps'][0].update(prompt=prompt, audit={'status': 'passed', 'prompt': prompt,
                'skill_execution': {'version': m.asset_skill.profile()['version'], 'mode': 'project'}})
            self.lib.save(job, 'jobs')
            tasks.append({'job_id': job['id'], 'index': 0, 'prompt': prompt})
        with patch.object(pmod.threading.Thread, 'start'):
            self.lib.projects.batch({'project_id': self.p['id'], 'mode': 'generate', 'tasks': tasks,
                                     'authorize': True, 'provider': 'imagegen', 'model': '5.0'})
        calls = []
        def submit(payload):
            calls.append(payload)
            job = self.lib.get(payload['id'], 'jobs')
            self.assertTrue(self.lib.prompt_is_approved(job, 0))
            self.assertEqual(job['steps'][0]['prompt'], tasks[0]['prompt'])
            if len(calls) == 1:
                raise ValueError('一次调用失败，禁止自动重试')
            job['steps'][0]['status'] = 'awaiting_review'
            self.lib.save(job, 'jobs')
        with patch.object(self.lib, 'submit_imagegen', side_effect=submit), patch.object(pmod.time, 'sleep'):
            self.lib.projects._batch_worker(self.p['id'])
        batch = self.lib.projects.get(self.p['id'])['batch']
        self.assertEqual([e['status'] for e in batch['entries']], ['error', 'done'])
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.lib.projects.batch_running)

    def test_export_interruption_resumes_same_revision_without_duplicates(self):
        for row in self.lib.projects.detail(self.p['id'])['rows']:
            for i in range(len(row['job']['steps'])):
                self.select_confirm(row['job'], i)
        self.backup()
        with patch.object(pmod, 'register_workspace_artifact', side_effect=OSError('模拟导出中断')):
            with self.assertRaises(OSError):
                self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        pending = self.lib.projects.get(self.p['id'])['export_pending']
        result = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        self.assertEqual(result['revision'], pending['revision'])
        self.assertEqual(len(list((Path(self.p['path'])/'04_资产/母版').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
