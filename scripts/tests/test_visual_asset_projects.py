"""Project bridge acceptance checks using isolated project files and a fake Vault guard."""
import builtins
from collections import Counter
from contextlib import contextmanager
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_library as m
import visual_asset_projects as pmod
from core.artifacts import load_manifest
from core.character_layout import character_layout_reference
from core.project_state import new_project_state, write_project_state, load_project_state


@contextmanager
def image_reads(root):
    root = root.resolve()
    reads = Counter()

    def count(open_file):
        def open_image(path, mode='r', *args, **kwargs):
            if isinstance(path, (str, Path)) and 'r' in mode:
                image = Path(path)
                if image.suffix.lower() == '.png':
                    image = image.resolve()
                    if image.is_relative_to(root):
                        reads[image] += 1
            return open_file(path, mode, *args, **kwargs)
        return open_image

    with patch.object(builtins, 'open', count(builtins.open)), patch.object(io, 'open', count(io.open)):
        yield reads


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
        with patch.object(visual_asset_upload, 'pick_local_files', return_value=[str(source)]):
            result = self.lib.projects.action({'action': 'upload-reference', 'project_id': self.p['id'],
                                              'job_id': job['id'], 'index': 0})
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
        env = patch.object(m.asset_skill, 'resolve_knowledge_root', return_value=synthetic_vault(self.base / 'synthetic-vault'))
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

    def test_ast_original_images_reach_planning_and_generation_request(self):
        repository = self.base / 'repository'
        original = repository / 'assets' / 'original.png'
        original.parent.mkdir(parents=True)
        Image.new('RGB', (400, 300), 'green').save(original)
        story = Path(self.p['path']) / 'narrative.json'
        narrative = pmod.read(story)
        narrative['asset_requirements'][0]['reference_paths'] = ['assets/original.png']
        pmod.write(story, narrative)
        update_narrative_checksum(story.parent)
        service = self.lib.projects
        with patch('visual_asset_source.REPOSITORY', repository):
            service.sync(self.p['id'])
            project = service.get(self.p['id'])
            project['rows'][0]['reference_ids'] = []
            project['planning'] = {'status': 'running', 'run_id': 'synthetic-original'}
            service.save(project)
            result = {'rows': [plan_result()['rows'][0]]}
            with patch.object(service.skill, 'prepare', return_value=('synthetic rules', {})), \
                    patch.object(service.bridge, 'plan_assets', return_value=result) as plan:
                service._plan_worker('synthetic', project, [project['rows'][0]])
            self.assertIn(str(original), plan.call_args.args[2]['reference_paths'])
            request = service.request(self.job(), 0)
        self.assertEqual(request['reference_paths'], [str(original)])
        self.assertEqual(request['reference_records'][0]['sha256'], pmod.digest(original))
        self.assertFalse(request['reference_records'][0]['project_confirmed'])

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
        self.assertEqual(job['steps'][0]['view'], '原图视角')
        self.assertEqual(job['steps'][0]['name'], '物品原图 · 常态')
        route = {'project_id': p['id'], 'ast_id': 'AST-CUP'}
        build = service.production_route({**route, 'route': 'build'})['rows'][1]['job']['steps'][0]
        self.assertEqual((build['view'], build['ratio']), ('正面、左侧、背面、俯视四视图', '16:9'))
        job = service.production_route({**route, 'route': 'reuse'})['rows'][1]['job']
        # Older reuse rows kept the generated layout even on direct selection.
        job['steps'][0]['view'] = '正面、左侧、背面、俯视四视图'
        job['steps'][0]['name'] = '物品四视图 · 常态'
        service.lib.save(job, 'jobs')
        service.select({'job_id':job['id'],'index':0,'asset_id':self.asset['id']})
        selected = service.lib.get(job['id'],'jobs')['steps'][0]
        self.assertEqual(selected['ratio'], '400:300')
        self.assertEqual(selected['view'], '原图视角')
        self.assertEqual(selected['name'], '物品原图 · 常态')
        self.assertFalse(selected['generation_enabled'])
        oldrow=copy.deepcopy(p['rows'][1])
        with self.assertRaises(ValueError):
            service.apply_plan(service.get(p['id']),[oldrow],{'rows':[plan_result()['rows'][1]]},{})
        self.assertEqual(service.get(p['id'])['rows'][1]['job_id'],job['id'])

    def test_planning_respects_prop_reuse_decision_without_a_selected_route(self):
        service = self.lib.projects
        p = service.get(self.p['id'])
        row = p['rows'][1]
        row['job_id'] = None
        row.pop('production_route', None)
        row['requirement']['image_decision'] = '复用现有素材'
        service.apply_plan(p, [row], {'rows': [plan_result()['rows'][1]]}, {})
        step = self.job(1)['steps'][0]
        self.assertFalse(step['generation_enabled'])
        self.assertEqual(step['view'], '原图视角')

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

    def test_sync_blocked_by_later_job_preserves_all_existing_jobs(self):
        service = self.lib.projects
        ident = self.p['id']
        first_job, busy_job = self.job(0), self.job(1)
        busy_job['steps'][0]['status'] = 'generating'
        self.lib.save(busy_job, 'jobs')
        before = service.get(ident)
        story_path = Path(before['source']['path'])
        narrative = pmod.read(story_path)
        narrative['objective'] += '；本轮调整制作背景'
        pmod.write(story_path, narrative)
        update_narrative_checksum(story_path.parent)

        with self.assertRaisesRegex(ValueError, '受影响需求仍在生成'):
            service.sync(ident)
        self.assertEqual(service.get(ident), before)
        self.assertEqual(self.lib.get(first_job['id'], 'jobs'), first_job)
        self.assertEqual(self.lib.get(busy_job['id'], 'jobs'), busy_job)

        busy_job['steps'][0]['status'] = 'waiting'
        self.lib.save(busy_job, 'jobs')
        result = service.sync(ident)
        self.assertEqual([row['job_id'] for row in result['rows']], [None, None])
        for previous_job in (first_job, busy_job):
            self.assertTrue(self.lib.get(previous_job['id'], 'jobs')['project_stale'])

    def test_sync_does_not_write_unchanged_jobs(self):
        with patch.object(self.lib, 'save', wraps=self.lib.save) as save:
            self.lib.projects.sync(self.p['id'])
        save.assert_not_called()

    def test_rebinding_existing_project_reuses_sync_source_check(self):
        service = self.lib.projects
        with patch.object(pmod, 'source_project', wraps=pmod.source_project) as load_source:
            result = service.bind(self.p['path'])
        self.assertEqual(result['id'], self.p['id'])
        self.assertLessEqual(load_source.call_count, 2)

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

    def test_image_observations_follow_selection_and_reopen_preserves_history(self):
        from test_imagegen_visual_self_check import observations
        asset = self.lib.get(self.asset['id'])
        asset['provenance']['visual_self_check'] = observations(Path(asset['original']), 'Synthetic prompt')
        self.lib.save(asset)
        job = self.job()
        self.lib.projects.select({'job_id': job['id'], 'index': 0, 'asset_id': asset['id']})
        self.assertEqual(self.job()['steps'][0]['visual_self_check']['status'], 'pass')
        replacement = self.base / 'replacement.png'
        Image.new('RGB', (400, 300), 'green').save(replacement)
        other, _ = self.lib.import_file(replacement, category='场景')
        self.lib.projects.select({'job_id': job['id'], 'index': 0, 'asset_id': other['id']})
        self.assertNotIn('visual_self_check', self.job()['steps'][0])
        self.lib.projects.select({'job_id': job['id'], 'index': 0, 'asset_id': asset['id']})
        self.lib.projects.confirm({'job_id': job['id'], 'index': 0, 'confirm': True})
        checked = self.job()['steps'][0]['visual_self_check']
        self.lib.projects.reopen({'job_id': job['id'], 'index': 0})
        reset = self.job()['steps'][0]
        self.assertNotIn('visual_self_check', reset)
        self.assertEqual(reset['history'][-1]['visual_self_check'], checked)
        self.assertIsNone(reset['project_confirmation'])

    def test_export_keeps_observations_in_existing_confirmation_provenance(self):
        from test_imagegen_visual_self_check import observations
        asset = self.lib.get(self.asset['id'])
        evidence = observations(Path(asset['original']), 'Synthetic prompt')
        asset['provenance']['visual_self_check'] = evidence
        self.lib.save(asset)
        for row in self.lib.projects.detail(self.p['id'])['rows']:
            for index in range(len(row['job']['steps'])):
                self.select_confirm(row['job'], index)
        self.backup()
        result = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        receipt = pmod.read(Path(result['manifest']).parent / '用户确认记录.json')
        preserved = [item['provenance']['visual_self_check'] for item in receipt['items']
                     if item['library_asset_id'] == asset['id']]
        self.assertTrue(preserved)
        self.assertTrue(all(item == evidence for item in preserved))
        # Existing human confirmation is still a separate exact-byte receipt.
        self.assertTrue(all(item['confirmation']['image_sha256'] for item in receipt['items']))

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
        request['kind'] = '服装'
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
        self.assertEqual({m['id'] for m in package['professional_methods']}, {'ASSET-CHARACTER-METHOD'})
        self.assertIn('无头隐形人台', package['production_item']['template']['content'])
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

    def test_object_plan_and_review_use_minimal_four_view_contract(self):
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

    def test_new_project_without_kickoff_cannot_fall_back_to_legacy_review(self):
        service = self.lib.projects
        job = self.job()
        root = Path(self.p['path'])
        pmod.write(root / '00_项目管理/project_request.json', {'project_mode': 'new'})
        selected = service.select({'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id']})
        before = self.lib.get(job['id'], 'jobs')
        self.assertEqual(selected['review_workflow'], 'awaiting_kickoff')
        with self.assertRaisesRegex(ValueError, '先登记开工决定'):
            service.confirm({'job_id': job['id'], 'index': 0, 'confirm': True})
        with self.assertRaisesRegex(ValueError, '先登记开工决定'):
            service.export({'project_id': self.p['id'], 'confirm': True})
        self.assertEqual(self.lib.get(job['id'], 'jobs'), before)
        self.assertFalse(service.get(self.p['id']).get('export'))

    def test_project_kickoff_reuses_only_scoped_generation_and_requires_website_review(self):
        job = self.job()
        step = job['steps'][0]
        prompt = '保留参考颜色、轮廓和相对位置。'
        step.update(prompt=prompt, audit={'status': 'passed', 'prompt': prompt,
            'skill_execution': {'version': m.asset_skill.profile()['version'], 'mode': 'project'}})
        self.lib.save(job, 'jobs')
        grant = {'provider': 'imagegen', 'model': '5.0', 'fee_scope': 'existing_quota',
                 'reference_upload_authorized': True,
                 'authorization_ref': 'synthetic grant', 'outputs': {job['ast_id']: [step['key']]}}
        kickoff = {'decision_ref': 'synthetic kickoff', 'product': {'mode': 'none'}, 'image_generation': grant,
                   'canvas': {'platform': 'redframe', 'model': 'fixture', 'aspect_ratio': '9:16',
                              'resolution': '720p', 'target_url': 'https://fixture.invalid/canvas/one'}}
        request = Path(self.p['path']) / '00_项目管理/project_request.json'
        pmod.write(request, {'kickoff': kickoff})
        service = self.lib.projects
        payload = {'project_id': self.p['id'], 'mode': 'generate',
                   'tasks': [{'job_id': job['id'], 'index': 0, 'prompt': prompt}]}

        with self.assertRaisesRegex(ValueError, '工具或模型'):
            service.batch({**payload, 'provider': 'dreamina', 'authorize': True})
        grant['outputs'][job['ast_id']] = ['wrong-output']
        pmod.write(request, {'kickoff': kickoff})
        with self.assertRaisesRegex(ValueError, '不在开工生图授权范围'):
            service.batch({**payload, 'authorize': True})
        grant.pop('outputs')
        grant['scope'] = 'required_asset_outputs'
        pmod.write(request, {'kickoff': kickoff})
        with patch.object(pmod.threading.Thread, 'start'):
            service.batch(payload)
        self.assertEqual(service.get(self.p['id'])['batch']['provider'], 'imagegen')

        kickoff.pop('image_generation')
        pmod.write(request, {'kickoff': kickoff})
        with patch.object(self.lib, 'submit_imagegen') as submit:
            service._batch_worker(self.p['id'])
        submit.assert_not_called()
        self.assertIn('排队期间已改变', service.get(self.p['id'])['batch']['entries'][0]['message'])
        with self.assertRaisesRegex(ValueError, '审核站真实通过回执'):
            service.confirm({'project_id': self.p['id'], 'job_id': job['id'], 'index': 0, 'confirm': True})
        with self.assertRaisesRegex(ValueError, '审核站真实通过回执'):
            service.export({'project_id': self.p['id'], 'confirm': True})

    def test_current_website_review_adopts_exact_asset_without_another_confirmation(self):
        from types import SimpleNamespace
        from scripts.tests.test_review_text_cli import narrative
        from core.artifacts import adopt_initial_package, register_workspace_artifact
        from core.project_kickoff import REQUEST_PATH

        root = (self.base / 'website-review').resolve()
        root.mkdir()
        script_path, package = narrative(root)
        requirement = package['asset_requirements'][0]
        requirement.update(image_decision='复用现有素材', image_reason='合成测试图片')
        pmod.write(script_path, package)
        register_workspace_artifact(root, {'artifact_id': 'N-1', 'logical_id': 'narrative.package',
            'type': 'narrative.package', 'revision': 'R01', 'path_or_url': script_path.name, 'depends_on': []})
        adopt_initial_package(root, 'N-1', 'synthetic upstream approval')
        pmod.write(root / REQUEST_PATH, {'kickoff': {'decision_ref': 'synthetic kickoff',
            'product': {'mode': 'none'}, 'canvas': {'platform': 'redframe', 'model': 'fixture',
                'aspect_ratio': '9:16', 'resolution': '720p', 'create_new': True}}})
        service = self.lib.projects
        p = service.bind(str(root))
        ast_id = requirement['asset_id']
        planned = service.prepare_reuse({'project_id': p['id'], 'ast_id': ast_id})
        job = planned['rows'][0]['job']
        service.select({'project_id': p['id'], 'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id']})
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        image = root / 'image.png'
        image.write_bytes(Path(self.asset['original']).read_bytes())

        def receipt(stage, command, source, version, **fields):
            sha = pmod.digest(source)
            ids = dict(projectId='fixture-site', scope='live', caseId='12', stage=stage, version=version,
                       assetId=stage, sha256=sha, operationId=stage, ok=True)
            binding = {'path': source.name, 'sha256': sha}
            parameters = dict(caseId='12', operationId=stage, **fields)
            return {'kind': 'agent-review-api-receipt', 'command': command, 'project_path': str(root),
                    'base_url': 'https://fixture.invalid', 'response': ids, 'request': {'parameters': parameters},
                    ('image' if stage == 'assets' else 'source_json'): binding}

        pmod.write(root / 'script.json', receipt('script', 'publish', script_path, 'a' * 64))
        publication = receipt('assets', 'publish-asset', image, 'b' * 64, astId=ast_id,
                              outputId=step['key'], purpose=step['purpose'], references=[])
        publication.update(script_publication='script.json', script_version='a' * 64, reference_publications=[])
        pmod.write(root / 'asset.json', publication)
        asset_status = 'pending'
        asset_is_current = True
        script_is_current = True
        change_script_on_decision = False
        def decision(**target):
            status = asset_status if target['stage'] == 'assets' else 'approved'
            active = asset_is_current if target['stage'] == 'assets' else script_is_current
            if change_script_on_decision and target['stage'] == 'script':
                script_path.write_bytes(script_bytes + b'\n')
            return dict(case={'projectId': 'fixture-site'}, decisionStatus=status,
                        validity=dict(isCurrent=active, stillEffective=active, reviewable=True, sourceStale=False,
                                      dependencyStale=False, dependencyUnapproved=False),
                        approval={'scope': {'projectId': 'fixture-site', 'scope': 'live',
                                            'caseId': target['case_id'], 'stage': target['stage'],
                                            'version': target['version']}, 'actorId': 'test-reviewer'})
        client = SimpleNamespace(base_url='https://fixture.invalid', decision=decision)
        self.assertEqual(service.apply_website_review(p['id'], 'asset.json', client)['decision_status'], 'pending')
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['status'], 'awaiting_review')
        asset_status = 'approved'

        # The remote decision may arrive after the local source has drifted.
        script_bytes = script_path.read_bytes()
        change_script_on_decision = True
        try:
            with self.assertRaisesRegex(ValueError, '叙事包内容已变化'):
                service.apply_website_review(p['id'], 'asset.json', client)
        finally:
            change_script_on_decision = False
            script_path.write_bytes(script_bytes)
        self.assertIsNone(self.lib.get(job['id'], 'jobs')['steps'][0]['project_confirmation'])

        # Commit still checks the exact output on the current job.
        saved_job = self.lib.get(job['id'], 'jobs')
        confirm_outputs = service._confirm_reviewed_outputs

        def change_output_after_preflight(*args, **kwargs):
            changed = copy.deepcopy(saved_job)
            changed['steps'][0]['output_id'] = 'wrong-output'
            self.lib.save(changed, 'jobs')
            return confirm_outputs(*args, **kwargs)

        try:
            with patch.object(service, '_confirm_reviewed_outputs', side_effect=change_output_after_preflight):
                with self.assertRaisesRegex(ValueError, '输出身份已变化'):
                    service.apply_website_review(p['id'], 'asset.json', client)
        finally:
            self.assertIsNone(self.lib.get(job['id'], 'jobs')['steps'][0]['project_confirmation'])
            self.lib.save(saved_job, 'jobs')

        saved_project = service.get(p['id'])

        def change_workbench_after_preflight(*args, **kwargs):
            changed = copy.deepcopy(saved_project)
            service.row(changed, ast_id)['job_id'] = None
            service.save(changed)
            return confirm_outputs(*args, **kwargs)

        try:
            with patch.object(service, '_confirm_reviewed_outputs', side_effect=change_workbench_after_preflight):
                with self.assertRaisesRegex(ValueError, '旧版需求'):
                    service.apply_website_review(p['id'], 'asset.json', client)
        finally:
            self.assertIsNone(self.lib.get(job['id'], 'jobs')['steps'][0]['project_confirmation'])
            service.save(saved_project)

        def backup_then_change_decision(ids):
            nonlocal asset_status
            result = m.visual_asset_backup.run(self.lib, m.management, asset_ids=ids)
            asset_status = next_status
            return result

        output = ast_id + '@' + step['key']
        for next_status in ('changes', 'pending'):
            with self.subTest(backup_decision=next_status):
                asset_status = 'approved'
                returned = service.apply_website_review(p['id'], 'asset.json', client,
                                                        backup=backup_then_change_decision)
                self.assertFalse(returned['adopted'])
                self.assertEqual(returned['withdrawn_outputs'], [output])
                self.assertNotIn('export', service.get(p['id']))
                self.assertIsNone(self.lib.get(job['id'], 'jobs')['steps'][0]['project_confirmation'])

        def backup_then_expire_script(ids):
            nonlocal script_is_current
            result = m.visual_asset_backup.run(self.lib, m.management, asset_ids=ids)
            script_is_current = False
            return result

        asset_status = 'approved'
        try:
            with self.assertRaisesRegex(ValueError, '上游编导稿的网站通过已经失效'):
                service.apply_website_review(p['id'], 'asset.json', client, backup=backup_then_expire_script)
        finally:
            script_is_current = True
        self.assertNotIn('export', service.get(p['id']))
        asset_status = 'changes'
        service.apply_website_review(p['id'], 'asset.json', client)

        asset_status = 'approved'
        published_bytes = image.read_bytes()

        def backup_then_change_published_image(ids):
            result = m.visual_asset_backup.run(self.lib, m.management, asset_ids=ids)
            image.write_bytes(published_bytes + b'\n')
            return result

        try:
            with self.assertRaisesRegex(ValueError, 'source changed after publication'):
                service.apply_website_review(p['id'], 'asset.json', client, backup=backup_then_change_published_image)
        finally:
            image.write_bytes(published_bytes)
        self.assertNotIn('export', service.get(p['id']))
        asset_status = 'changes'
        service.apply_website_review(p['id'], 'asset.json', client)
        asset_status = 'approved'
        changed_original = None

        def backup_then_change_selected_image(ids):
            nonlocal changed_original
            result = m.visual_asset_backup.run(self.lib, m.management, asset_ids=ids)
            path = Path(self.lib.get(ids[0])['original'])
            data = path.read_bytes()
            changed_original = path, data
            path.write_bytes(data + b'\n')
            return result

        try:
            with self.assertRaisesRegex(ValueError, '图片内容.*变化|图片字节|SHA-256'):
                service.apply_website_review(p['id'], 'asset.json', client, backup=backup_then_change_selected_image)
        finally:
            if changed_original:
                path, data = changed_original
                path.write_bytes(data)
        self.assertNotIn('export', service.get(p['id']))
        asset_status = 'changes'
        service.apply_website_review(p['id'], 'asset.json', client)
        asset_status = 'approved'

        with (patch.object(service, 'detail', side_effect=AssertionError('批量采用无需组装工作台整表')),
              patch.object(service, '_website_members', wraps=service._website_members) as members,
              patch.object(service, '_confirm_reviewed_outputs', wraps=service._confirm_reviewed_outputs) as commit):
            result = m.apply_project_review(self.lib, lambda **options: service.apply_website_review(
                p['id'], 'asset.json', client, **options))
            self.assertEqual((members.call_count, commit.call_count), (1, 1))
        self.assertTrue(result['adopted'])
        self.assertEqual(result['backup']['count'], 1)
        self.assertEqual(load_project_state(root)['user_confirmations'][-1]['method'], '审核站资产图通过')
        self.assertEqual(service.apply_website_review(p['id'], 'asset.json', client)['export'], result['export'])

        from core.asset_refs import require_final_images
        old_receipt = Path(result['export']['folder']) / '用户确认记录.json'
        original = old_receipt.read_bytes()
        asset_status = 'changes'
        asset_is_current = False
        service.apply_website_review(p['id'], 'asset.json', client)
        self.assertEqual(require_final_images(root, [output]), (True, []))
        asset_is_current = True
        changed = service.apply_website_review(p['id'], 'asset.json', client)
        self.assertEqual(changed['withdrawn_outputs'], [output])
        self.assertFalse(require_final_images(root, [output])[0])
        reset = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(reset['selected'], step['selected'])
        self.assertEqual(reset['status'], 'awaiting_review')
        self.assertIsNone(reset['project_confirmation'])
        self.assertEqual(reset['history'][-1]['website_review_change']['decision']['decisionStatus'], 'changes')
        with patch.object(service, '_export_locked') as export:
            self.assertEqual(service.apply_website_review(p['id'], 'asset.json', client)['withdrawn_outputs'], [])
            export.assert_not_called()
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0], reset)
        self.assertEqual(old_receipt.read_bytes(), original)

        # The same local image can be confirmed again by a current approval.
        asset_status = 'approved'
        self.assertTrue(service.apply_website_review(p['id'], 'asset.json', client)['waiting_for_backup'])
        self.backup()
        self.assertTrue(service.apply_website_review(p['id'], 'asset.json', client)['adopted'])
        self.assertEqual(require_final_images(root, [output]), (True, []))

    def test_current_bundle_changes_withdraws_confirmed_members_without_old_publication(self):
        from types import SimpleNamespace
        from core.asset_refs import require_final_images

        cup = {'asset_id': 'AST-PROP-CUP', 'category': 'PROP', 'name': '杯子', 'narrative_purpose': '合成测试',
               'image_decision': '复用现有素材', 'image_reason': '合成测试', 'source_paragraph_ids': ['STP-01'],
               'interactions': [], 'identity_constraints': [], 'required_states': [], 'reference_paths': [], 'unknowns': []}
        root, service, project = self.kickoff_project('website-group', [cup])
        ident = project['id']

        def publication(filename, stage, source, version, name, **parameters):
            sha = pmod.digest(source)
            binding = {'path': source.name, 'sha256': sha}
            receipt = {'kind': 'agent-review-api-receipt', 'command': 'publish' if stage == 'script' else 'publish-asset',
                       'project_path': str(root), 'base_url': 'https://fixture.invalid',
                       'response': dict(projectId='fixture-site', scope='live', caseId='12', stage=stage, version=version,
                                        assetId=name, sha256=sha, operationId=name, ok=True),
                       'request': {'parameters': dict(caseId='12', operationId=name, **parameters)},
                       ('source_json' if stage == 'script' else 'image'): binding}
            if stage == 'assets':
                receipt.update(script_publication='script.json', script_version='a' * 64,
                               reference_publications=['member.json'] if parameters['references'] else [])
            pmod.write(root / filename, receipt)

        publication('script.json', 'script', root / 'R01.json', 'a' * 64, 'script')
        jobs, outputs = [], []
        for index, row in enumerate(project['rows']):
            ast = row['requirement']['asset_id']
            prepared = service.prepare_reuse({'project_id': ident, 'ast_id': ast})
            job_id = next(row['job']['id'] for row in prepared['rows'] if row['requirement']['asset_id'] == ast)
            service.select({'project_id': ident, 'job_id': job_id, 'index': 0, 'asset_id': self.asset['id']})
            step = self.lib.get(job_id, 'jobs')['steps'][0]
            output_id = pmod.step_output_id(step)
            image = root / f'upload-{index}.png'
            image.write_bytes(Path(self.asset['original']).read_bytes())
            name = 'bundle' if index else 'member'
            publication(name + '.json', 'assets', image, ('c' if index else 'b') * 64, name,
                        astId=ast, outputId=output_id, purpose=step['purpose'], references=['member'] if index else [])
            jobs.append(job_id)
            outputs.append(ast + '@' + output_id)

        status = 'approved'

        def decision(**target):
            current = status if target['stage'] == 'assets' else 'approved'
            return dict(case={'projectId': 'fixture-site'}, decisionStatus=current,
                        validity=dict(isCurrent=True, stillEffective=current == 'approved', reviewable=True,
                                      sourceStale=False, dependencyStale=False, dependencyUnapproved=False),
                        approval={'scope': dict(projectId='fixture-site', scope='live', caseId=target['case_id'],
                                                stage=target['stage'], version=target['version']), 'actorId': 'reviewer'})

        client = SimpleNamespace(base_url='https://fixture.invalid', decision=decision)
        self.assertTrue(service.apply_website_review(ident, 'bundle.json', client)['waiting_for_backup'])
        self.backup()
        self.assertTrue(service.apply_website_review(ident, 'bundle.json', client)['adopted'])
        self.assertEqual(require_final_images(root, outputs), (True, []))
        for job_id in jobs:
            confirmed = self.lib.get(job_id, 'jobs')['steps'][0]['project_confirmation']
            self.assertEqual(confirmed['website_review']['approval']['scope']['version'], 'c' * 64)
        (root / 'member.json').unlink()
        status = 'changes'
        returned = service.apply_website_review(ident, 'bundle.json', client)
        self.assertCountEqual(returned['withdrawn_outputs'], outputs)
        self.assertFalse(require_final_images(root, outputs)[0])
        with patch.object(service, '_export_locked') as export:
            self.assertEqual(service.apply_website_review(ident, 'bundle.json', client)['withdrawn_outputs'], [])
            export.assert_not_called()

    def test_website_approval_unblocks_character_dependencies_before_full_export(self):
        from types import SimpleNamespace
        from scripts.tests.test_review_text_cli import narrative
        from core.artifacts import adopt_initial_package, register_workspace_artifact
        from core.project_kickoff import REQUEST_PATH

        root = (self.base / 'website-character').resolve()
        root.mkdir()
        script_path, package = narrative(root)
        prop = package['asset_requirements'][0]
        prop.update(image_decision='复用现有素材', image_reason='已有合成道具图')
        character = {**prop, 'asset_id': 'AST-PERSON', 'category': 'CHARACTER', 'entity_id': 'CHR-PERSON',
                     'name': '人物甲', 'narrative_purpose': '固定人物外观与日常装',
                     'character_output_kind': '人物1x4',
                     'wardrobe_states': [{'name': '日常装', 'evidence': '本段人物穿日常装',
                                          'output_id': 'HOME-FINAL'}],
                     'image_decision': '需要制作', 'image_reason': '人物板需要头照和服装'}
        package['asset_requirements'].append(character)
        pmod.write(script_path, package)
        register_workspace_artifact(root, {'artifact_id': 'N-1', 'logical_id': 'narrative.package',
            'type': 'narrative.package', 'revision': 'R01', 'path_or_url': script_path.name, 'depends_on': []})
        adopt_initial_package(root, 'N-1', 'synthetic upstream approval')
        pmod.write(root / REQUEST_PATH, {'kickoff': {'decision_ref': 'synthetic kickoff',
            'product': {'mode': 'none'}, 'canvas': {'platform': 'redframe', 'model': 'fixture',
                'aspect_ratio': '9:16', 'resolution': '720p', 'create_new': True}}})
        service = self.lib.projects
        project = service.bind(str(root))
        ident = project['id']
        character_payload = {'project_id': ident, 'ast_id': character['asset_id']}
        prepared = pmod.prepare_character(service, character_payload)
        outfit = prepared['rows'][1]['job']['character_workflow']['outfits'][0]
        project = pmod.choose_character_route(service, {**character_payload, 'outfit_id': outfit['id'], 'route': 'build'})
        job = project['rows'][1]['job']
        head, wardrobe, final = [next(i for i, step in enumerate(job['steps']) if step['kind'] == kind)
                                 for kind in ('大头照', '服装', '人物1x4')]

        def publish(name, source, version, job=None, index=0, references=()):
            stage = 'script' if job is None else 'assets'
            params = {'caseId': '12', 'operationId': name}
            checksum = pmod.digest(source)
            receipt = {'kind': 'agent-review-api-receipt', 'project_path': str(root),
                'base_url': 'https://fixture.invalid', 'command': 'publish' if job is None else 'publish-asset',
                'response': {'ok': True, 'projectId': 'fixture-site', 'scope': 'live', 'stage': stage,
                             'assetId': name, 'version': version, 'sha256': checksum, **params},
                'request': {'parameters': params},
                'source_json' if job is None else 'image': {'path': str(source.relative_to(root)), 'sha256': checksum}}
            if job is not None:
                step = job['steps'][index]
                params.update(astId=job['ast_id'], outputId=pmod.step_output_id(step), purpose=step['purpose'],
                              references=list(references))
                receipt.update(script_publication='script.json', script_version='a' * 64,
                               reference_publications=[ref + '.json' for ref in references])
            pmod.write(root / (name + '.json'), receipt)
            return name + '.json'

        def select(job, index, name, category, size, color):
            source = self.base / (name + '.png')
            Image.new('RGB', size, color).save(source)
            asset, _ = self.lib.import_file(source, category=category)
            service.select({'project_id': ident, 'job_id': job['id'], 'index': index, 'asset_id': asset['id']})
            return service.copy_to_project(project, asset)

        current_version = None
        changes_for = None

        def decision(case_id, stage, version):
            current = stage == 'script' or version == current_version
            status = 'changes' if stage == 'assets' and version == changes_for else 'approved'
            return {'case': {'projectId': 'fixture-site'}, 'decisionStatus': status,
                'validity': {'isCurrent': current, 'stillEffective': current, 'reviewable': True,
                             'sourceStale': False, 'dependencyStale': False, 'dependencyUnapproved': False},
                'approval': {'scope': {'projectId': 'fixture-site', 'scope': 'live', 'caseId': case_id,
                                       'stage': stage, 'version': version}, 'actorId': 'test-reviewer'}}

        client = SimpleNamespace(base_url='https://fixture.invalid', decision=decision)
        publish('script', script_path, 'a' * 64)

        def saved_step(job, index):
            return self.lib.get(job['id'], 'jobs')['steps'][index]

        with self.assertRaisesRegex(ValueError, '确认共用大头照'):
            service.request(job, wardrobe)
        head_request = self.lib.prepare(job['id'], head)
        head_layout = character_layout_reference('大头照')
        self.assertEqual(head_request['reference_paths'], [head_layout['original']])
        self.assertEqual(head_request['reference_records'], [head_layout])
        self.assertIn('参考图 1 是大头照灰模，只参考', head_request['prompt'])
        self.assertTrue(head_request['allow_unreferenced_design'])
        head_image = select(job, head, 'head', '大头照', (300, 400), 'red')
        current_version = 'b' * 64
        head_publication = publish('head', head_image, current_version, job, head)
        selected = self.lib.get(saved_step(job, head)['selected'])
        original = Path(selected['original'])
        original_bytes = original.read_bytes()
        confirm = service._confirm_step

        def replace_after_preflight(*args, **kwargs):
            Image.new('RGB', (300, 400), 'yellow').save(original)
            return confirm(*args, **kwargs)

        try:
            with patch.object(service, '_confirm_step', side_effect=replace_after_preflight):
                with self.assertRaisesRegex(ValueError, '当前选图字节已变化'):
                    service.apply_website_review(ident, head_publication, client)
        finally:
            original.write_bytes(original_bytes)
        self.assertIsNone(saved_step(job, head)['project_confirmation'])

        result = service.apply_website_review(ident, head_publication, client)
        self.assertTrue(result['waiting_for_remaining_outputs'])
        self.assertFalse(result['adopted'])
        head_confirmation = saved_step(job, head)['project_confirmation']
        self.assertIsNone(saved_step(job, wardrobe)['project_confirmation'])
        self.assertFalse(service.get(ident).get('export'))
        wardrobe_request = service.request(self.lib.get(job['id'], 'jobs'), wardrobe)
        self.assertEqual(wardrobe_request['reference_paths'],
                         [str(head_image), character_layout_reference('服装')['original']])

        wardrobe_image = select(job, wardrobe, 'wardrobe', '服装三视图', (160, 120), 'green')
        current_version = 'c' * 64
        wardrobe_publication = publish('wardrobe', wardrobe_image, current_version, job, wardrobe, ['head'])
        self.assertTrue(service.apply_website_review(ident, wardrobe_publication, client)['waiting_for_remaining_outputs'])
        wardrobe_confirmation = saved_step(job, wardrobe)['project_confirmation']
        self.assertEqual(saved_step(job, head)['project_confirmation'], head_confirmation)
        with self.assertRaisesRegex(ValueError, '不再生图'):
            self.lib.prepare(job['id'], final)
        self.assertEqual(pmod.step_output_id(job['steps'][final]), 'HOME-FINAL')
        self.assertNotEqual(job['steps'][final]['key'], 'HOME-FINAL')

        final_image = select(job, final, 'board', '人物1x4', (750, 360), 'blue')
        current_version = 'd' * 64
        final_publication = publish('board', final_image, current_version, job, final, ['head', 'wardrobe'])
        self.assertTrue(service.apply_website_review(ident, final_publication, client)['waiting_for_remaining_outputs'])
        self.assertIsNone(service.get(ident)['rows'][0]['job_id'])
        self.assertFalse(service.get(ident).get('export'))
        board_confirmation = saved_step(job, final)['project_confirmation']

        self.backup()
        # A newer pending website package may advance the review head before
        # backup completes. Reading A's old publication can still export A's
        # stored exact approval, without approving any current waiting member.
        current_version = 'e' * 64
        partial = service.apply_website_review(ident, final_publication, client)
        self.assertFalse(partial['validity']['isCurrent'])
        self.assertTrue(partial['adopted'])
        self.assertTrue(partial['waiting_for_remaining_outputs'])
        self.assertEqual(partial['export']['count'], 3)
        from core.asset_refs import require_final_images
        self.assertEqual(require_final_images(root, ['AST-PERSON@HOME-FINAL']), (True, []))
        self.assertFalse(service.get(ident).get('export'))

        prop_job = service.prepare_reuse({'project_id': ident, 'ast_id': prop['asset_id']})['rows'][0]['job']
        prop_image = select(prop_job, 0, 'prop', '物品', (160, 90), 'purple')
        publish('different-head-member', head_image, 'e' * 64, job, head)
        current_version = 'f' * 64
        bad_publication = publish('bad-full', prop_image, current_version, prop_job, 0,
                                  ['different-head-member', 'wardrobe', 'board'])
        with self.assertRaisesRegex(ValueError, '未引用准确的已确认图片'):
            service.apply_website_review(ident, bad_publication, client)
        self.assertIsNone(saved_step(prop_job, 0)['project_confirmation'])

        current_version = '1' * 64
        full_publication = publish('full', prop_image, current_version, prop_job, 0, ['head', 'wardrobe', 'board'])
        result = service.apply_website_review(ident, full_publication, client)
        self.assertTrue(result['waiting_for_backup'])
        self.assertEqual(result['export'], partial['export'])
        self.backup()
        result = service.apply_website_review(ident, full_publication, client)
        self.assertTrue(result['adopted'])
        self.assertEqual(result['export']['count'], 4)
        self.assertEqual(saved_step(job, head)['project_confirmation'], head_confirmation)
        self.assertEqual(saved_step(job, wardrobe)['project_confirmation'], wardrobe_confirmation)
        self.assertEqual(saved_step(job, final)['project_confirmation'], board_confirmation)
        receipt = pmod.read(Path(result['export']['folder']) / '用户确认记录.json')
        self.assertEqual(receipt['website_review']['approval']['scope']['version'], current_version)
        self.assertEqual(service.apply_website_review(ident, full_publication, client)['export'], result['export'])
        old_export = Path(result['export']['manifest'])
        first_outputs = [output['output_id'] for asset in pmod.read(old_export)['assets']
                         for output in asset['confirmed_outputs']]
        self.assertIn('HOME-FINAL', first_outputs)
        self.assertNotIn(job['steps'][final]['key'], first_outputs)
        old_receipt_path = Path(result['export']['folder']) / '用户确认记录.json'
        old_receipt_bytes = old_receipt_path.read_bytes()
        # Simulate an unchanged image originally confirmed by an older client.
        old_prop_job = self.lib.get(prop_job['id'], 'jobs')
        prop_confirmation = old_prop_job['steps'][0]['project_confirmation']
        prop_confirmation['website_review'].pop('binding')
        self.lib.save(old_prop_job, 'jobs')

        # Only the board changes. The prop's first approval came from the full bundle.
        service.reopen({'project_id': ident, 'job_id': job['id'], 'index': final})
        revised_board = select(job, final, 'board-v2', '人物1x4', (160, 90), 'cyan')
        current_version = '2' * 64
        revised_publication = publish('board-v2', revised_board, current_version, job, final, ['head', 'wardrobe', 'full'])
        self.assertTrue(service.apply_website_review(ident, revised_publication, client)['waiting_for_backup'])
        self.backup()
        revised = service.apply_website_review(ident, revised_publication, client)['export']
        self.assertGreater(int(revised['revision'][1:]), int(result['export']['revision'][1:]))
        self.assertEqual(saved_step(prop_job, 0)['project_confirmation'], prop_confirmation)
        self.assertEqual(old_receipt_path.read_bytes(), old_receipt_bytes)
        self.assertEqual(saved_step(job, final)['project_confirmation']['website_review']['approval']['scope']['version'],
                         current_version)
        revised_receipt = pmod.read(Path(revised['folder']) / '用户确认记录.json')
        self.assertEqual(revised_receipt['website_review']['approval']['scope']['version'], current_version)
        self.assertEqual(service.apply_website_review(ident, revised_publication, client)['export'], revised)

        from core.asset_refs import resolve_final_images
        prop_output = prop['asset_id'] + '@' + pmod.step_output_id(saved_step(prop_job, 0))
        prop_master = resolve_final_images(root, [prop_output])[prop_output]
        video = root / 'unchanged-video.json'
        material = {'asset_id': prop['asset_id'], 'output_id': prop_master['output_id'],
                    'path': prop_master['path'], 'sha256': prop_master['sha256']}
        pmod.write(video, {'generation_segments': [{'segment_id': 'V001', 'prompt': '合成镜头',
                                                    'materials': [material]}]})
        manifest_path = root / '00_项目管理/artifact_manifest.json'
        manifest = pmod.load_manifest(root)
        manifest['artifacts'].append({'artifact_id': 'UNCHANGED-VIDEO', 'logical_id': 'video.prompt',
                                     'type': 'video.prompt', 'status': 'current', 'revision': 'R01',
                                     'path_or_url': video.name, 'checksum': pmod.digest(video),
                                     'depends_on': ['visual.asset.manifest']})
        pmod.write(manifest_path, manifest)

        def video_status():
            return next(entry['status'] for entry in pmod.load_manifest(root)['artifacts']
                        if entry['artifact_id'] == 'UNCHANGED-VIDEO')

        changes_for = current_version
        # Interrupt after the exact manifest invalidation and individual job saves.
        with patch.object(service, 'save', side_effect=OSError('save interrupted')):
            with self.assertRaisesRegex(OSError, 'save interrupted'):
                service.apply_website_review(ident, revised_publication, client)
        self.assertEqual(video_status(), 'current')
        self.assertEqual(service.get(ident)['export'], revised)
        # Interrupt after the old project export was cleared, before a new pending export exists.
        with patch.object(service, '_export_locked', side_effect=OSError('before pending')):
            with self.assertRaisesRegex(OSError, 'before pending'):
                service.apply_website_review(ident, revised_publication, client)
        self.assertEqual(video_status(), 'current')
        self.assertNotIn('export', service.get(ident))
        self.assertNotIn('export_pending', service.get(ident))
        self.assertFalse(require_final_images(root, [prop_output])[0])
        # The next retry sets a new pending export, which must be reused if interrupted again.
        with patch.object(pmod, 'register_workspace_artifact', side_effect=OSError('after pending')):
            with self.assertRaisesRegex(OSError, 'after pending'):
                service.apply_website_review(ident, revised_publication, client)
        pending = service.get(ident)['export_pending']
        decision_result = service.apply_website_review(ident, revised_publication, client)
        self.assertTrue(decision_result['feedback_scope_unverified'])
        self.assertEqual(decision_result['withdrawn_outputs'], [])
        self.assertEqual(service.get(ident)['partial_export']['artifact_id'], 'visual-assets-' + pending['id'])
        self.assertEqual(video_status(), 'current')
        self.assertFalse(require_final_images(root, ['AST-PERSON@HOME-FINAL'])[0])
        self.assertEqual(require_final_images(root, [prop_output]), (True, []))
        self.assertEqual(resolve_final_images(root, [prop_output])[prop_output]['path'], prop_master['path'])
        self.assertEqual(saved_step(prop_job, 0)['project_confirmation'], prop_confirmation)
        self.assertEqual(saved_step(job, head)['project_confirmation'], head_confirmation)
        self.assertEqual(saved_step(job, wardrobe)['project_confirmation'], wardrobe_confirmation)
        resumed = service.get(ident)['partial_export']
        resumed_bytes = Path(resumed['manifest']).read_bytes()
        with patch.object(service, '_export_locked') as export:
            service.apply_website_review(ident, revised_publication, client)
            export.assert_not_called()
        self.assertEqual(service.get(ident)['partial_export'], resumed)
        self.assertEqual(Path(resumed['manifest']).read_bytes(), resumed_bytes)

    def kickoff_project(self, name, extra_requirements=()):
        from scripts.tests.test_review_text_cli import narrative
        from core.artifacts import adopt_initial_package, register_workspace_artifact
        from core.project_kickoff import REQUEST_PATH
        root = (self.base / name).resolve()
        root.mkdir()
        script_path, package = narrative(root)
        for req in package['asset_requirements']:
            req.update(image_decision='复用现有素材', image_reason='合成测试图片')
        package['asset_requirements'].extend(extra_requirements)
        pmod.write(script_path, package)
        register_workspace_artifact(root, {'artifact_id': 'N-1', 'logical_id': 'narrative.package',
            'type': 'narrative.package', 'revision': 'R01', 'path_or_url': script_path.name, 'depends_on': []})
        adopt_initial_package(root, 'N-1', 'synthetic upstream approval')
        pmod.write(root / REQUEST_PATH, {'kickoff': {'decision_ref': 'synthetic kickoff',
            'product': {'mode': 'none'}, 'canvas': {'platform': 'redframe', 'model': 'fixture',
                'aspect_ratio': '9:16', 'resolution': '720p', 'create_new': True}}})
        service = self.lib.projects
        return root, service, service.bind(str(root))

    def test_delegated_narrative_asset_review_rework_and_handoff(self):
        from core import agent_acceptance as aa, artifacts
        from core.asset_refs import require_final_images, resolve_final_images
        from core.project_kickoff import REQUEST_PATH
        from test_formal_package_submission import narrative, register

        root = (self.base / 'delegated-review').resolve()
        root.mkdir()
        package = narrative(root)
        package.pop('human_readable_path')
        package['status'] = 'candidate'
        package['asset_requirements'][0].update(image_decision='无需独立图', image_reason='合成测试无需人物图')
        real_reference = root / 'product-reference.png'
        Image.new('RGB', (400, 300), 'yellow').save(real_reference)
        package['asset_requirements'].extend([
            {'asset_id': ast, 'category': category, 'name': name, 'narrative_purpose': '合成验收',
             'source_paragraph_ids': ['STP-01'], 'identity_constraints': [], 'required_states': [],
             'interactions': [], 'reference_paths': refs, 'unknowns': [],
             'image_decision': '复用现有素材', 'image_reason': '合成测试图片'}
            for ast, category, name, refs in (
                ('AST-PROP-PAPER', 'PROP', '纸张', []),
                ('AST-REAL-BOX', 'REAL_PRODUCT', '合成盒', [real_reference.name]))])
        pmod.write(root / REQUEST_PATH, {'kickoff': {'decision_ref': 'synthetic kickoff',
            'product': {'mode': 'none'}, 'canvas': {'platform': 'redframe', 'model': 'fixture',
                'aspect_ratio': '9:16', 'resolution': '720p', 'create_new': True}}})
        dialogue = root / package['dialogue_evidence_path']
        evidence = pmod.read(dialogue)
        evidence['dialogues'][0].pop('verification')
        pmod.write(dialogue, evidence)
        package['dialogue_evidence_sha256'] = pmod.digest(dialogue)
        entry = register(root, 'N-AGENT', 'narrative.package', package)
        aa.register_workflow_authorization(root, authorization_id='SYNTHETIC-TEXT',
            authorization_ref='synthetic creative and planning authorization',
            skills=['creative-director', 'storyboard-planner'])
        profile = aa.ROOT / 'skills/creative-director/review-profile.json'
        report_path = root / 'narrative-review.json'
        checked_at = '2026-11-17T03:00:00Z'
        report = {'schema_version': '1.0', 'kind': 'agent-self-check', 'scope': 'artifact',
                  'verdict': 'pass', 'agent_id': 'synthetic reviewer', 'checked_at': checked_at,
                  'authorization_id': 'SYNTHETIC-TEXT', **aa.acceptance_binding(root, entry),
                  'review_profile_sha256': pmod.digest(profile),
                  'checks': [{'id': check['id'], 'result': 'pass', 'observation': 'synthetic check',
                              'locator': 'synthetic input'}
                             for check in pmod.read(profile)['production_artifact_checks']]}
        # A valid whole-report shape cannot clear a dialogue check marked inapplicable.
        dialogue_check = next(check for check in report['checks'] if check['id'] == 'CREATIVE-DIALOGUE')
        dialogue_check.update(result='not_applicable', reason='synthetic negative case')
        pmod.write(report_path, report)
        with self.assertRaisesRegex(ValueError, '未核对白证据'):
            artifacts.adopt_initial_agent_artifact(root, entry['artifact_id'], str(report_path))
        dialogue_check.update(result='pass')
        dialogue_check.pop('reason')
        pmod.write(report_path, report)
        artifacts.adopt_initial_agent_artifact(root, entry['artifact_id'], str(report_path))
        self.assertEqual(pmod.read(dialogue), evidence)

        service = self.lib.projects
        project = service.action({'action': 'bind', 'path': str(root)})
        ident = project['id']
        grant = subprocess.run([sys.executable, str(Path(m.__file__)), 'authorize-asset-review',
            '--project', str(root), '--authorization-id', 'SYNTHETIC-ASSETS',
            '--authorization-ref', 'synthetic delegated authority for image review'], capture_output=True, text=True)
        self.assertEqual(grant.returncode, 0, grant.stderr)
        aa.register_asset_workflow_authorization(root, authorization_id='SYNTHETIC-CHAT',
            authorization_ref='synthetic authority for the human chat route')
        with self.assertRaisesRegex(ValueError, '未明确覆盖逐图代审'):
            service.apply_agent_review(ident, authorization_id='SYNTHETIC-CHAT', review={'outputs': []})
        jobs = {}
        for ast in ('AST-PROP-PAPER', 'AST-REAL-BOX'):
            service.prepare_reuse({'project_id': ident, 'ast_id': ast})
            jobs[ast] = service.row(service.get(ident), ast)['job_id']

        def select(ast, name, color):
            image = self.base / name
            Image.new('RGB', (400, 300), color).save(image)
            asset, _ = self.lib.import_file(image, category='其他')
            service.select({'project_id': ident, 'job_id': jobs[ast], 'index': 0, 'asset_id': asset['id']})
            step = self.lib.get(jobs[ast], 'jobs')['steps'][0]
            return {'ast_id': ast, 'output_id': pmod.step_output_id(step), 'asset_id': asset['id'],
                    'image_sha256': pmod.digest(Path(asset['original'])), 'purpose': step['purpose'],
                    'result': 'pass', 'observation': 'synthetic fixture pixels', 'locator': 'whole original image'}

        paper = select('AST-PROP-PAPER', 'paper.png', 'blue')
        box = select('AST-REAL-BOX', 'box.png', 'yellowgreen')
        source = project['source']
        review = {'source_narrative': {**source, 'sha256': pmod.digest(Path(source['path']))},
                  'agent_id': 'synthetic reviewer', 'checked_at': checked_at, 'report_ref': 'synthetic batch'}
        authorization = 'SYNTHETIC-ASSETS'
        with self.assertRaisesRegex(ValueError, '产品参考图'):
            service.apply_agent_review(ident, authorization_id=authorization, review={**review, 'outputs': [paper, box]})
        self.assertIsNone(self.lib.get(jobs[paper['ast_id']], 'jobs')['steps'][0]['project_confirmation'])
        box['product_check'] = {'result': 'pass', 'observation': 'synthetic product check',
            'locator': 'whole source image', 'references': [{'path': str(real_reference),
                                                             'sha256': pmod.digest(real_reference)}]}
        asset_report = root / 'asset-review.json'
        pmod.write(asset_report, {**review, 'outputs': [paper, box]})
        command = [sys.executable, str(Path(m.__file__)), '--library', str(self.lib.root),
            '--asset-vault', str(self.lib.vault), 'apply-agent-review', '--project-id', ident,
            '--authorization-id', authorization, '--review-file', str(asset_report)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        initial = json.loads(first.stdout)['export']
        queries = [f"{item['ast_id']}@{item['output_id']}" for item in (paper, box)]
        first_outputs = resolve_final_images(root, queries)
        self.assertEqual(set(first_outputs), set(queries))
        receipt_file = Path(initial['folder']) / '资产采用记录.json'
        first_receipt = receipt_file.read_bytes()
        first_manifest = Path(initial['manifest']).read_bytes()
        self.assertTrue(all('agent_review_ref' in asset['confirmed_outputs'][0]
                            for asset in json.loads(first_manifest)['assets']))
        self.assertEqual(load_project_state(root)['user_confirmations'], [])
        repeated = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)['export'], initial)

        service.reopen({'project_id': ident, 'job_id': jobs[box['ast_id']], 'index': 0})
        self.assertEqual(require_final_images(root, queries[:1]), (True, []))
        self.assertFalse(require_final_images(root, queries[1:])[0])
        revised = select(box['ast_id'], 'box-reworked.png', 'orange')
        revised['product_check'] = box['product_check']
        outdated = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(outdated.returncode, 0)
        self.assertIn('选图、用途或字节', outdated.stderr)
        pmod.write(asset_report, {**review, 'report_ref': 'synthetic rework', 'outputs': [revised]})
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        new_export = json.loads(second.stdout)['export']
        final_outputs = resolve_final_images(root, queries)
        self.assertEqual(final_outputs[queries[0]]['path'], first_outputs[queries[0]]['path'])
        self.assertEqual(final_outputs[queries[1]]['sha256'], revised['image_sha256'])
        self.assertNotEqual(new_export['revision'], initial['revision'])
        self.assertEqual(receipt_file.read_bytes(), first_receipt)
        self.assertEqual(Path(initial['manifest']).read_bytes(), first_manifest)
        self.assertEqual(load_project_state(root)['user_confirmations'], [])

        handoff = subprocess.run([sys.executable, str(pmod.ROOT / 'scripts/stage_context_cli.py'),
            'handoff', '--project', str(root), '--to-skill', 'storyboard-planner'], capture_output=True, text=True)
        self.assertEqual(handoff.returncode, 0, handoff.stderr)
        self.assertIn(new_export['artifact_id'], service.continue_project(ident)['content'])
        real_reference.write_bytes(b'changed product reference')
        self.assertEqual(require_final_images(root, queries[:1]), (True, []))
        self.assertFalse(require_final_images(root, queries[1:])[0])

    def test_chat_confirmation_adopts_exact_asset_with_dual_basis(self):
        from core.agent_acceptance import register_asset_workflow_authorization, register_workflow_authorization
        root, service, p = self.kickoff_project('chat-confirmation')
        ast_id = p['rows'][0]['requirement']['asset_id']
        planned = service.prepare_reuse({'project_id': p['id'], 'ast_id': ast_id})
        job = planned['rows'][0]['job']
        service.select({'project_id': p['id'], 'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id']})
        confirmation = {'quote': '确认这批资产，继续推', 'at': '2026-11-13T05:30:00Z',
                        'review_ref': '04_资产/资产清单.md「协调方复核」一节'}
        # The kickoff default is unchanged: no basis, no adoption.
        with self.assertRaisesRegex(ValueError, '审核站真实通过回执'):
            service.confirm({'project_id': p['id'], 'job_id': job['id'], 'index': 0, 'confirm': True})
        with self.assertRaisesRegex(ValueError, '审核站真实通过回执'):
            service.export({'project_id': p['id'], 'confirm': True})
        # Missing authorization and wrong-scope authorization both fail closed.
        with self.assertRaisesRegex(ValueError, '流程授权不存在或不唯一'):
            service.apply_chat_confirmation(p['id'], authorization_id='MISSING', confirmation=confirmation)
        register_workflow_authorization(root, authorization_id='FLOW-TEXT-ONLY',
            authorization_ref='synthetic manuscript-only authorization', skills=['creative-director'])
        with self.assertRaisesRegex(ValueError, '未覆盖资产采用范围'):
            service.apply_chat_confirmation(p['id'], authorization_id='FLOW-TEXT-ONLY', confirmation=confirmation)
        register_asset_workflow_authorization(root, authorization_id='FLOW-ASSET-TEST',
            authorization_ref='synthetic user decision for the chat confirmation route')
        # The recorded user confirmation itself must be complete.
        with self.assertRaisesRegex(ValueError, '对话确认依据'):
            service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                            confirmation={'quote': '确认这批资产，继续推'})
        unrelated_path = self.base / 'unrelated.png'
        Image.new('RGB', (400, 300), 'orange').save(unrelated_path)
        unrelated, _ = self.lib.import_file(unrelated_path, project='other', category='其他', role='候选')
        unrelated['backup'] = {'status': 'pending'}
        self.lib.save(unrelated)
        confirmation_path = self.base / 'chat-confirmation.json'
        pmod.write(confirmation_path, confirmation)
        completed = subprocess.run([
            sys.executable, str(Path(m.__file__)), '--library', str(self.lib.root),
            '--asset-vault', str(self.lib.vault), 'apply-chat-confirmation', '--project-id', p['id'],
            '--authorization-id', 'FLOW-ASSET-TEST', '--confirmation-file', str(confirmation_path),
        ], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result['adopted'])
        self.assertEqual(result['backup']['count'], 1)
        self.assertEqual(self.lib.get(unrelated['id'])['backup']['status'], 'pending')
        self.assertEqual(result['confirmation_channel'], 'chat_confirmation')
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['project_confirmation']['method'], '用户对话整批确认')
        self.assertEqual(step['project_confirmation']['chat_confirmation']['confirmation']['quote'],
                         '确认这批资产，继续推')
        self.assertNotIn('website_review', step['project_confirmation'])
        folder = Path(result['export']['folder'])
        receipt = pmod.read(folder / '用户确认记录.json')
        self.assertEqual(receipt['chat_confirmation']['authorization_id'], 'FLOW-ASSET-TEST')
        self.assertEqual(receipt['chat_confirmation']['confirmation']['review_ref'],
                         '04_资产/资产清单.md「协调方复核」一节')
        self.assertNotIn('website_review', receipt)
        self.assertTrue(all(item['confirmation']['method'] == '用户对话整批确认'
                            and 'website_review' not in item['confirmation']
                            for item in receipt['items']))
        manifest = pmod.read(result['export']['manifest'])
        self.assertTrue(manifest['assets'][0]['confirmed_outputs'])
        state = load_project_state(root)
        self.assertEqual(state['user_confirmations'][-1]['method'], '用户对话整批确认')
        self.assertEqual(state['user_confirmations'][-1]['artifact_ids'], [result['export']['artifact_id']])
        entry = next(a for a in load_manifest(root)['artifacts']
                     if a['artifact_id'] == result['export']['artifact_id'])
        self.assertTrue(entry['user_approved'])
        again = service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                confirmation=confirmation)
        self.assertEqual(again['export'], result['export'])

    def test_chat_confirmation_waits_for_remaining_outputs(self):
        from core.agent_acceptance import register_asset_workflow_authorization, register_workflow_authorization
        extra = {'asset_id': 'AST-PROP-CUP', 'category': 'PROP', 'name': '合成杯子',
                 'narrative_purpose': '承载合成验收动作', 'source_paragraph_ids': ['STP-01'],
                 'identity_constraints': [], 'required_states': [], 'interactions': [],
                 'reference_paths': [], 'unknowns': [], 'image_decision': '复用现有素材',
                 'image_reason': '合成测试图片'}
        root, service, p = self.kickoff_project('chat-partial', [extra])
        register_asset_workflow_authorization(root, authorization_id='FLOW-ASSET-TEST',
            authorization_ref='synthetic user decision for the chat confirmation route')
        jobs = {}
        for row in service.get(p['id'])['rows']:
            planned = service.prepare_reuse({'project_id': p['id'], 'ast_id': row['requirement']['asset_id']})
            jobs[row['requirement']['asset_id']] = next(
                r['job'] for r in planned['rows'] if r['requirement']['asset_id'] == row['requirement']['asset_id'])
        first_ast = p['rows'][0]['requirement']['asset_id']
        service.select({'project_id': p['id'], 'job_id': jobs[first_ast]['id'], 'index': 0,
                        'asset_id': self.asset['id']})
        confirmation = {'quote': '确认这批资产，继续推', 'at': '2026-11-13T05:30:00Z',
                        'review_ref': '04_资产/资产清单.md「协调方复核」一节'}
        result = service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                 confirmation=confirmation)
        self.assertFalse(result['adopted'])
        self.assertTrue(result['waiting_for_remaining_outputs'])
        self.assertNotIn('export', service.get(p['id']))
        done = self.lib.get(jobs[first_ast]['id'], 'jobs')['steps'][0]
        self.assertEqual(done['project_confirmation']['method'], '用户对话整批确认')
        other_ast = next(ast for ast in jobs if ast != first_ast)
        pending = self.lib.get(jobs[other_ast]['id'], 'jobs')['steps'][0]
        self.assertIsNone(pending['project_confirmation'])

        from core.asset_refs import require_final_images
        first_key = pmod.step_output_id(done)
        self.backup()
        partial = service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                  confirmation={'outputs': []})
        self.assertTrue(partial['adopted'])
        self.assertTrue(partial['waiting_for_remaining_outputs'])
        self.assertEqual(partial['export']['count'], 1)
        self.assertEqual(require_final_images(root, [first_ast + '@' + first_key]), (True, []))
        partial_manifest = Path(partial['export']['manifest'])
        partial_receipt = partial_manifest.parent / '用户确认记录.json'
        snapshots = {path: path.read_bytes() for path in (partial_manifest, partial_receipt)}
        self.assertEqual(service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                         confirmation={'outputs': []}), partial)
        from core.asset_refs import resolve_final_images
        def video_segment(segment_id, ast, output):
            selected = resolve_final_images(root, [ast + '@' + output])[ast + '@' + output]
            return {'segment_id': segment_id, 'prompt': '合成拍摄场景', 'materials': [
                {'asset_id': ast, 'output_id': output, 'path': selected['path'], 'sha256': selected['sha256']}]}

        def seed_video(artifact_id, segments, *, replaced=None):
            path = root / f'{artifact_id}.json'
            pmod.write(path, {'generation_segments': segments})
            manifest = load_manifest(root)
            if replaced:
                next(item for item in manifest['artifacts'] if item['artifact_id'] == replaced)['status'] = 'superseded'
            manifest['artifacts'].append({'artifact_id': artifact_id, 'logical_id': 'video.prompt',
                'type': 'video.prompt', 'path_or_url': path.name, 'checksum': pmod.digest(path),
                'status': 'current', 'revision': 'R01', 'depends_on': ['visual.asset.manifest']})
            pmod.write(root / '00_项目管理/artifact_manifest.json', manifest)

        first_segment = video_segment('V001', first_ast, first_key)
        seed_video('EXACT-A', [first_segment])
        pmod.write(root / 'legacy.md', '旧整表依赖')
        manifest = load_manifest(root)
        manifest['artifacts'].append({'artifact_id': 'LEGACY-A', 'logical_id': 'fixture.legacy',
            'type': 'legacy', 'path_or_url': 'legacy.md', 'status': 'current', 'depends_on': ['visual.asset.manifest']})
        pmod.write(root / '00_项目管理/artifact_manifest.json', manifest)
        other_image = self.base / 'cup.png'
        Image.new('RGB', (160, 90), 'green').save(other_image)
        distinct, _ = self.lib.import_file(other_image, category='物品')
        service.select({'project_id': p['id'], 'job_id': jobs[other_ast]['id'], 'index': 0,
                        'asset_id': distinct['id']})
        # Replaying A's old unscoped receipt must not confirm newly selected B.
        service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                        confirmation=confirmation)
        selected = self.lib.get(jobs[other_ast]['id'], 'jobs')['steps'][0]
        self.assertIsNone(selected['project_confirmation'])
        with image_reads(self.base) as reads:
            repeated = service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                       confirmation={'outputs': []})
        self.assertEqual(repeated, partial)
        self.assertEqual(reads[Path(distinct['original']).resolve()], 0)
        second_key = pmod.step_output_id(selected)
        scope = {'ast_id': other_ast, 'output_id': second_key, 'asset_id': distinct['id'],
                 'purpose': selected['purpose'], 'image_sha256': pmod.digest(Path(distinct['original']))}
        with self.assertRaisesRegex(ValueError, '原确认范围不能扩大'):
            service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                            confirmation={**confirmation, 'outputs': [scope]})
        new_basis = {**confirmation, 'quote': '确认这张杯子', 'at': '2026-11-13T06:00:00Z'}
        with self.assertRaisesRegex(ValueError, '选图、用途或字节'):
            service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                            confirmation={**new_basis, 'outputs': [{**scope, 'purpose': '错误用途'}]})
        result = service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                 confirmation={**new_basis, 'outputs': [scope]})
        self.assertTrue(result['waiting_for_backup'])
        self.assertEqual(result['export'], partial['export'])
        self.assertFalse(require_final_images(root, [other_ast + '@' + second_key])[0])
        self.backup()
        complete = service.apply_chat_confirmation(p['id'], authorization_id='FLOW-ASSET-TEST',
                                                    confirmation={'outputs': []})
        self.assertTrue(complete['adopted'])
        self.assertNotIn('waiting_for_remaining_outputs', complete)
        self.assertEqual(require_final_images(root, [first_ast + '@' + first_key,
                                                     other_ast + '@' + second_key]), (True, []))
        receipts = pmod.read(Path(complete['export']['folder']) / '用户确认记录.json')['items']
        self.assertEqual([item['confirmation']['chat_confirmation']['confirmation']['quote'] for item in receipts],
                         [confirmation['quote'], new_basis['quote']])
        self.assertEqual(self.lib.get(jobs[first_ast]['id'], 'jobs')['steps'][0], done)
        self.assertEqual({path: path.read_bytes() for path in snapshots}, snapshots)
        entries = {item['artifact_id']: item for item in load_manifest(root)['artifacts']}
        self.assertEqual(entries['EXACT-A']['status'], 'current')
        self.assertEqual(entries['LEGACY-A']['status'], 'stale')
        self.assertEqual(video_segment('V001', first_ast, first_key), first_segment)

        second_segment = video_segment('V002', other_ast, second_key)
        seed_video('MIXED-AB', [first_segment, second_segment], replaced='EXACT-A')
        from core import asset_refs
        with patch.object(asset_refs, 'registered_output_snapshot', wraps=asset_refs.registered_output_snapshot) as snapshots:
            service.reopen({'project_id': p['id'], 'job_id': jobs[other_ast]['id'], 'index': 0})
        self.assertEqual([call.args[1]['artifact_id'] for call in snapshots.call_args_list],
                         [complete['export']['artifact_id'], service.get(p['id'])['partial_export']['artifact_id']])
        entries = {item['artifact_id']: item for item in load_manifest(root)['artifacts']}
        self.assertEqual(entries['MIXED-AB']['status'], 'stale')
        self.assertEqual(entries['MIXED-AB']['stale_reasons'][-1]['asset_output_segment_ids'], ['V002'])
        self.assertEqual(video_segment('V001', first_ast, first_key), first_segment)
        self.assertEqual(pmod.read(root / 'MIXED-AB.json')['generation_segments'][0], first_segment)
        self.assertFalse(require_final_images(root, [other_ast + '@' + second_key])[0])

        service.reopen({'project_id': p['id'], 'job_id': jobs[first_ast]['id'], 'index': 0})
        entries = {item['artifact_id']: item for item in load_manifest(root)['artifacts']}
        self.assertEqual(entries['MIXED-AB']['stale_reasons'][-1]['asset_output_scope'], 'unknown')

    def test_two_outfits_publish_one_current_output_and_keep_it_on_other_reopen(self):
        from core.agent_acceptance import register_asset_workflow_authorization
        from core.asset_refs import require_final_images

        ast = 'AST-PERSON'
        character = {'asset_id': ast, 'category': 'CHARACTER', 'entity_id': 'CHR-PERSON', 'name': '人物甲',
                     'narrative_purpose': '保持居家和外出服装区分', 'source_paragraph_ids': ['STP-01'],
                     'identity_constraints': [], 'required_states': [], 'interactions': [],
                     'reference_paths': [], 'unknowns': [], 'image_decision': '需要制作',
                     'image_reason': '人物分两套服装', 'character_output_kind': '人物1x4',
                     'wardrobe_states': [{'name': '居家装', 'evidence': '居家装', 'output_id': 'HOME-FINAL'},
                                         {'name': '外出装', 'evidence': '外出装', 'output_id': 'VISIT-FINAL'}]}
        root, service, project = self.kickoff_project('same-ast-outputs', [character])
        ident = project['id']
        auth = 'FLOW-ASSET-TEST'
        register_asset_workflow_authorization(root, authorization_id=auth,
            authorization_ref='synthetic user decision for the chat confirmation route')
        payload = {'project_id': ident, 'ast_id': ast}
        service.prepare_reuse({'project_id': ident, 'ast_id': project['rows'][0]['requirement']['asset_id']})
        pmod.prepare_character(service, payload)
        job_id = service.row(service.get(ident), ast)['job_id']

        def current():
            return self.lib.get(job_id, 'jobs')

        def index(output_id):
            return next(i for i, step in enumerate(current()['steps'])
                        if pmod.step_output_id(step) == output_id)

        for outfit in current()['character_workflow']['outfits']:
            pmod.choose_character_route(service, {**payload, 'outfit_id': outfit['id'],
                'route': 'reuse' if outfit['name'] == '居家装' else 'build'})
        home = index('HOME-FINAL')
        visit = index('VISIT-FINAL')
        for i, color in ((home, 'blue'), (visit, 'green')):
            path = self.base / (color + '-board.png')
            Image.new('RGB', (160, 90), color).save(path)
            selected, _ = self.lib.import_file(path, category='人物1x4')
            if i == visit:
                with self.assertRaisesRegex(ValueError, '先在项目表中确认前置制作项'):
                    service.select({'project_id': ident, 'job_id': job_id, 'index': i, 'asset_id': selected['id']})
                continue
            service.select({'project_id': ident, 'job_id': job_id, 'index': i, 'asset_id': selected['id']})
        step = current()['steps'][home]
        receipt = {'quote': '确认居家装', 'at': '2026-11-13T06:00:00Z', 'review_ref': 'Synthetic fixture',
                   'outputs': [{'ast_id': ast, 'output_id': 'HOME-FINAL', 'asset_id': step['selected'],
                                'purpose': step['purpose'],
                                'image_sha256': pmod.digest(Path(self.lib.get(step['selected'])['original']))}]}
        service.apply_chat_confirmation(ident, authorization_id=auth, confirmation=receipt)
        self.backup()
        partial = service.apply_chat_confirmation(ident, authorization_id=auth, confirmation={'outputs': []})
        self.assertEqual(partial['export']['count'], 1)
        self.assertEqual(require_final_images(root, [ast + '@HOME-FINAL']), (True, []))
        self.assertFalse(require_final_images(root, [ast + '@VISIT-FINAL'])[0])
        self.assertFalse(require_final_images(root, [ast])[0])
        original = current()['steps'][home]['project_confirmation']
        service.reopen({'project_id': ident, 'job_id': job_id, 'index': visit})
        self.assertEqual(require_final_images(root, [ast + '@HOME-FINAL']), (True, []))
        self.assertFalse(require_final_images(root, [ast + '@VISIT-FINAL'])[0])
        self.assertEqual(current()['steps'][home]['project_confirmation'], original)

    def prepare_export(self):
        for row in self.lib.projects.detail(self.p['id'])['rows']:
            for i in range(len(row['job']['steps'])):
                self.select_confirm(row['job'], i)
        self.backup()

    def test_export_reuses_shared_image_checks_and_rechecks_existing_delivery(self):
        self.prepare_export()
        service = self.lib.projects
        request = {'project_id': self.p['id'], 'confirm': True}
        asset = self.lib.get(self.asset['id'])
        original = Path(asset['original']).resolve()
        backup = (self.base / 'independent-scene-backup.png').resolve()
        backup.write_bytes(original.read_bytes())
        asset['backup']['path'] = str(backup)
        self.lib.save(asset)
        with image_reads(self.base) as first_reads:
            result = service.export(request)
        with image_reads(self.base) as retry_reads:
            self.assertEqual(service.export(request), result)

        self.assertEqual(result['count'], 5)
        # Four scene views share one source and backup. Allow a source
        # read for each new copy plus shared checks and decoding.
        self.assertGreater(first_reads[original], 0, first_reads)
        self.assertLessEqual(first_reads[original], 8, first_reads)
        self.assertEqual(first_reads[backup], 1, first_reads)
        self.assertEqual(retry_reads[original], 1, retry_reads)
        self.assertEqual(retry_reads[backup], 1, retry_reads)

        manifest = pmod.read(result['manifest'])
        delivered = Path(manifest['assets'][0]['confirmed_outputs'][0]['path'])
        replacement = b'a different file at the same delivery path'
        delivered.write_bytes(replacement)
        with self.assertRaisesRegex(ValueError, '已导出确认图片内容已变化'):
            service.export(request)
        self.assertEqual(delivered.read_bytes(), replacement)

    def test_export_large_distinct_batch_does_not_buffer_all_source_images(self):
        service = self.lib.projects
        project = service.get(self.p['id'])
        plan = plan_result()
        first_prop = plan['rows'][1]['outputs'][0]
        plan['rows'][1]['outputs'] = [{**first_prop, 'key': f'prop{i}'} for i in range(8)]
        service.apply_plan(project, project['rows'], plan, {})

        expected = {}
        total_bytes = 0
        for row in service.detail(self.p['id'])['rows']:
            job = row['job']
            for index, step in enumerate(job['steps']):
                path = self.base / f"{job['ast_id']}-{step['key']}.png"
                canvas = (400, 300) if step['kind'] == '环境' else (1600, 900)
                Image.new('RGB', canvas, (index + 1, 20, 40)).save(path)
                # Trailing bytes enlarge the physical input without making
                # the generated, deterministic test image expensive to decode.
                with path.open('ab') as stream:
                    for _ in range(48):
                        stream.write(b'x' * (64 * 1024))
                asset, _ = self.lib.import_file(path, category='场景' if step['kind'] == '环境' else '物品')
                service.select({'job_id': job['id'], 'index': index, 'asset_id': asset['id']})
                service.confirm({'job_id': job['id'], 'index': index, 'confirm': True})
                expected[(job['ast_id'], pmod.step_output_id(step))] = pmod.digest(path)
                total_bytes += path.stat().st_size
        self.backup()

        self.assertEqual(len(set(expected.values())), 12)
        tracing = tracemalloc.is_tracing()
        if not tracing:
            tracemalloc.start()
        try:
            current, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            result = service.export({'project_id': self.p['id'], 'confirm': True})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            if not tracing:
                tracemalloc.stop()

        self.assertEqual(result['count'], len(expected))
        manifest = pmod.read(result['manifest'])
        actual = {(asset['asset_id'], output['output_id']): pmod.digest(Path(output['path']))
                  for asset in manifest['assets'] for output in asset['confirmed_outputs']}
        self.assertEqual(actual, expected)
        self.assertLess(peak - current, total_bytes)

    def test_export_rechecks_source_replaced_after_technical_check(self):
        self.prepare_export()
        service = self.lib.projects
        step = self.job(1)['steps'][0]
        source = Path(self.lib.get(step['selected'])['original'])
        approved = source.read_bytes()
        replacement = b'bytes replaced after reading the approved image'
        check = service.skill.technical_check
        replaced = False

        def replace_after_check(path, ratio):
            nonlocal replaced
            result = check(path, ratio)
            if ratio == step['ratio'] and not replaced:
                source.write_bytes(replacement)
                replaced = True
            return result

        with patch.object(service.skill, 'technical_check', side_effect=replace_after_check):
            with self.assertRaisesRegex(ValueError, '已变化|不一致|校验未通过'):
                service.export({'project_id': self.p['id'], 'confirm': True})
        self.assertTrue(replaced)
        self.assertEqual(source.read_bytes(), replacement)
        project = service.get(self.p['id'])
        self.assertNotIn('export', project)
        root = Path(project['path'])
        self.assertFalse(any(entry['logical_id'] == 'visual.asset.manifest' and entry['status'] == 'current'
                             for entry in load_manifest(root)['artifacts']))
        self.assertFalse(any(path.read_bytes() == replacement
                             for path in (root / '04_资产/母版').rglob('*.png')))
        source.write_bytes(approved)
        result = service.export({'project_id': self.p['id'], 'confirm': True})
        if pending := project.get('export_pending'):
            self.assertEqual(result['revision'], pending['revision'])
        manifest = pmod.read(result['manifest'])
        target = Path(manifest['assets'][1]['confirmed_outputs'][0]['path'])
        self.assertEqual(target.read_bytes(), approved)

    def test_export_partial_copy_does_not_block_same_revision_retry(self):
        self.prepare_export()
        service = self.lib.projects
        request = {'project_id': self.p['id'], 'confirm': True}
        asset_root = Path(self.p['path']) / '04_资产/母版'
        copy2 = pmod.shutil.copy2
        written = []

        def interrupt_second_copy(source, destination, *args, **kwargs):
            path = Path(destination)
            if path.is_relative_to(asset_root):
                written.append(path)
                if len(written) == 2:
                    with Path(source).open('rb') as stream:
                        path.write_bytes(stream.read(11))
                    raise OSError('模拟写图中断')
            return copy2(source, destination, *args, **kwargs)

        with patch.object(pmod.shutil, 'copy2', side_effect=interrupt_second_copy):
            with self.assertRaisesRegex(OSError, '模拟写图中断'):
                service.export(request)
        self.assertEqual(len(written), 2)
        self.assertFalse(written[1].exists())
        pending = service.get(self.p['id'])['export_pending']
        folder = asset_root / (pending['revision'] + '_' + pending['id'])
        first, = (path for path in folder.rglob('*') if path.is_file())
        original_bytes = first.read_bytes()

        result = service.export(request)
        self.assertEqual(result['revision'], pending['revision'])
        self.assertEqual(Path(result['folder']), folder)
        self.assertEqual(result['count'], 5)
        self.assertEqual(first.read_bytes(), original_bytes)
        receipt = pmod.read(folder / '用户确认记录.json')
        expected = {(item['ast_id'], item['output_id']): item['confirmation']['image_sha256']
                    for item in receipt['items']}
        manifest = pmod.read(result['manifest'])
        actual = {(asset['asset_id'], output['output_id']): pmod.digest(Path(output['path']))
                  for asset in manifest['assets'] for output in asset['confirmed_outputs']}
        self.assertEqual(actual, expected)

    def test_export_rechecks_row_if_workbench_is_replaced_between_outputs(self):
        self.prepare_export()
        service = self.lib.projects
        ident = self.p['id']
        before = service.get(ident)
        replacement = copy.deepcopy(before)
        replacement['rows'][0]['job_id'] = 'f' * 16
        project_step = service._project_step
        replaced = False

        def replace_after_first_check(*args, **kwargs):
            nonlocal replaced
            result = project_step(*args, **kwargs)
            if not replaced:
                service.save(replacement)
                replaced = True
            return result

        with patch.object(service, '_project_step', side_effect=replace_after_first_check):
            with self.assertRaisesRegex(ValueError, '旧版需求'):
                service.export({'project_id': ident, 'confirm': True})
        self.assertTrue(replaced)
        self.assertEqual(service.get(ident), replacement)

    def test_export_interruption_resumes_same_revision_without_duplicates(self):
        self.prepare_export()
        with patch.object(pmod, 'register_workspace_artifact', side_effect=OSError('模拟导出中断')):
            with self.assertRaises(OSError):
                self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        pending = self.lib.projects.get(self.p['id'])['export_pending']
        asset_root = Path(self.p['path']) / '04_资产/母版'
        folder = asset_root / (pending['revision'] + '_' + pending['id'])
        copies = {path: pmod.digest(path) for path in folder.rglob('*.png')}
        self.assertEqual(len(copies), 5)
        with patch.object(self.lib.projects, 'get', wraps=self.lib.projects.get) as read_snapshot:
            result = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        self.assertEqual(result['count'], 5)
        self.assertEqual(read_snapshot.call_count, 2)
        self.assertEqual(result['revision'], pending['revision'])
        self.assertEqual(list(asset_root.iterdir()), [folder])
        self.assertEqual({path: pmod.digest(path) for path in folder.rglob('*.png')}, copies)


if __name__ == '__main__':
    unittest.main()
