import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_visual_asset_projects import fixture, update_narrative_checksum
from visual_asset_choices import RELATIVE, change, read_choices
from visual_asset_projects import source_project


class AssetChoices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lib, self.p, self.asset = fixture(Path(self.temp.name))
        self.root = Path(self.p['path'])
        self.original = (self.root / 'narrative.json').read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def act(self, operation, **payload):
        return change(self.lib.projects, {'project_id': self.p['id'], 'operation': operation,
                                         'confirm': True, **payload})

    def test_delete_restore_retains_job_and_does_not_rewrite_story(self):
        self.lib.projects.prepare_reuse({'project_id': self.p['id'], 'ast_id': 'AST-CUP'})
        before = self.lib.projects.detail(self.p['id'])
        row = next(r for r in before['rows'] if r['requirement']['asset_id'] == 'AST-CUP')
        removed = self.act('exclude', ast_id='AST-CUP')
        self.assertNotIn('AST-CUP', [r['requirement']['asset_id'] for r in removed['rows']])
        self.assertIn('AST-CUP', removed['narrative']['_asset_choices']['excluded'])
        # Re-reading the project from disk must honor the user's decision.
        self.assertNotIn('AST-CUP', [r['asset_id'] for r in source_project(self.root)[2]['asset_requirements']])
        restored = self.act('restore', ast_id='AST-CUP')
        self.assertEqual(next(r for r in restored['rows'] if r['requirement']['asset_id'] == 'AST-CUP')['job_id'], row['job_id'])
        self.assertEqual((self.root / 'narrative.json').read_bytes(), self.original)

    def test_add_requires_confirmed_real_location_and_is_persisted(self):
        params = dict(name='用户新增酒瓶', category='PROP', source_ids=['MISSING'],
                      appearance='入口由人物手提', location_confirmed=True)
        with self.assertRaises(ValueError):
            self.act('add', **params)
        self.assertFalse((self.root / RELATIVE).exists())
        params['source_ids'] = ['EVT-01']
        params['location_confirmed'] = False
        with self.assertRaises(ValueError):
            self.act('add', **params)
        params['location_confirmed'] = True
        result = self.act('add', **params)
        added = next(r['requirement'] for r in result['rows'] if r['requirement']['name'] == params['name'])
        self.assertEqual(added['source_event_ids'], ['EVT-01'])
        self.assertIn(params['name'], (self.root / '04_资产/当前资产需求表.md').read_text(encoding='utf-8'))
        self.assertEqual((self.root / 'narrative.json').read_bytes(), self.original)
        with self.assertRaises(ValueError):
            self.act('add', **params)

    def test_all_excluded_can_export_explicit_no_image_handoff(self):
        for row in list(self.p['rows']):
            self.act('exclude', ast_id=row['requirement']['asset_id'])
        exported = self.lib.projects.export({'project_id': self.p['id'], 'confirm': True})
        result = json.loads(Path(exported['manifest']).read_text(encoding='utf-8'))
        self.assertEqual(result['assets'], [])
        self.assertEqual(len(result['asset_requirement_decisions']['excluded']), len(self.p['rows']))
        self.assertEqual(exported['count'], 0)
        self.act('restore', ast_id='AST-CUP')
        self.assertFalse(self.lib.projects.get(self.p['id']).get('export'))

    def test_running_work_blocks_requirement_mutation(self):
        p = self.lib.projects.get(self.p['id'])
        p['planning'] = {'status': 'running'}
        self.lib.projects.save(p)
        with self.assertRaises(ValueError):
            self.act('exclude', ast_id='AST-CUP')
        self.assertEqual(read_choices(self.root)['revision'], 0)

    def scene_visits(self):
        path = self.root / 'narrative.json'
        narrative = json.loads(path.read_text(encoding='utf8'))
        narrative['asset_requirements'][0]['required_states'] = ['净脸前到访', '卸膜后到访']
        path.write_text(json.dumps(narrative, ensure_ascii=False), encoding='utf8')
        update_narrative_checksum(self.root)
        self.lib.projects.sync(self.p['id'])
        return path.read_bytes()

    def production_spec(self, specification, **extra):
        return self.act('production-spec', ast_id='AST-ROOM', specification=specification,
                        decision_ref='synthetic-user-message-1', quote='同一空场母图覆盖两次到访', **extra)

    def test_one_static_scene_covers_multiple_visits_without_rewriting_story(self):
        original = self.scene_visits()
        specification = {'scene_state_coverage': {'净脸前到访': ['净脸前到访', '卸膜后到访']},
                         'unchanged_static_space': True}
        self.production_spec(specification)
        revision = read_choices(self.root)['revision']
        self.production_spec(specification)
        self.assertEqual(read_choices(self.root)['revision'], revision)
        result = self.lib.projects.prepare_reuse({'project_id': self.p['id'], 'ast_id': 'AST-ROOM'})
        job = next(r['job'] for r in result['rows'] if r['requirement']['asset_id'] == 'AST-ROOM')
        self.assertEqual(len(job['steps']), 1)
        self.assertEqual(job['steps'][0]['covered_states'], ['净脸前到访', '卸膜后到访'])
        self.assertIsNone(job['steps'][0]['project_confirmation'])
        self.assertEqual((self.root / 'narrative.json').read_bytes(), original)
        self.assertEqual(result['narrative']['asset_requirements'][0]['required_states'], ['净脸前到访', '卸膜后到访'])
        expanded = self.lib.projects.scene_output_mode({
            'project_id': self.p['id'], 'ast_id': 'AST-ROOM', 'mode': 'four'})
        expanded_job = next(r['job'] for r in expanded['rows'] if r['requirement']['asset_id'] == 'AST-ROOM')
        self.assertEqual(len([s for s in expanded_job['steps'] if s.get('required') is not False]), 4)
        self.assertEqual({s['state'] for s in expanded_job['steps']}, {'净脸前到访'})

    def test_production_spec_rejects_story_patch_and_unproven_scene_merge(self):
        self.scene_visits()
        for specification in (
            {'identity_constraints': ['换成其他人物']},
            {'scene_state_coverage': {'净脸前到访': ['净脸前到访', '卸膜后到访']}},
            {'scene_state_coverage': {'净脸前到访': ['净脸前到访']}, 'unchanged_static_space': True},
            {'scene_state_coverage': {'净脸前到访': ['净脸前到访', '未知状态']}, 'unchanged_static_space': True},
        ):
            with self.subTest(specification=specification), self.assertRaises(ValueError):
                self.production_spec(specification)
        self.assertFalse((self.root / RELATIVE).exists())

    def test_scene_spec_does_not_survive_changed_source_requirement(self):
        self.scene_visits()
        self.production_spec({'scene_state_coverage': {'净脸前到访': ['净脸前到访', '卸膜后到访']},
                              'unchanged_static_space': True})
        path = self.root / 'narrative.json'
        narrative = json.loads(path.read_text(encoding='utf8'))
        narrative['asset_requirements'][0]['interactions'] = ['后次台面新增碗刷']
        path.write_text(json.dumps(narrative, ensure_ascii=False), encoding='utf8')
        update_narrative_checksum(self.root)
        with self.assertRaisesRegex(ValueError, '制作规格的上游'):
            source_project(self.root)

    def test_static_scene_coverage_retains_original_picture_confirmation(self):
        self.scene_visits()
        service = self.lib.projects
        result = service.prepare_reuse({'project_id': self.p['id'], 'ast_id': 'AST-ROOM'})
        job = next(r['job'] for r in result['rows'] if r['requirement']['asset_id'] == 'AST-ROOM')
        service.select({'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id']})
        service.confirm({'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id'], 'confirm': True})
        before = self.lib.get(job['id'], 'jobs')['steps'][0]['project_confirmation']
        result = self.production_spec({'scene_state_coverage': {'净脸前到访': ['净脸前到访', '卸膜后到访']},
                                      'unchanged_static_space': True})
        retained = next(r['job'] for r in result['rows'] if r['requirement']['asset_id'] == 'AST-ROOM')
        self.assertEqual(retained['id'], job['id'])
        self.assertEqual(retained['steps'][0]['project_confirmation'], before)
        self.assertFalse(retained['steps'][1]['required'])
        self.assertIsNone(retained['steps'][1]['project_confirmation'])

    def check_shared_static_scene_export(self, *, paragraph_mode=False):
        from core.asset_refs import require_final_images, resolve_final_images, _confirmed_output
        from core.schema_contracts import require_schema
        original = self.scene_visits()
        if paragraph_mode:
            story = self.root / 'narrative.json'
            narrative = json.loads(story.read_text(encoding='utf8'))
            narrative.pop('events')
            narrative.pop('human_readable_path')
            narrative.update(schema_version='2.0', story_paragraphs=[
                {'paragraph_id': 'STP-01', 'text': '人物先后两次到访同一静态洗手间。'}])
            for requirement in narrative['asset_requirements']:
                requirement.pop('source_event_ids')
                requirement.update(source_paragraph_ids=['STP-01'], image_decision='复用现有素材',
                                   image_reason='复用已确认的静态空间母图')
            story.write_text(json.dumps(narrative, ensure_ascii=False), encoding='utf8')
            update_narrative_checksum(self.root)
            self.lib.projects.sync(self.p['id'])
            original = story.read_bytes()
        self.act('exclude', ast_id='AST-CUP')
        service = self.lib.projects
        self.production_spec({'scene_state_coverage': {'净脸前到访': ['净脸前到访', '卸膜后到访']},
                              'unchanged_static_space': True})
        job = service.prepare_reuse({'project_id': self.p['id'], 'ast_id': 'AST-ROOM'})['rows'][0]['job']
        self.assertEqual(len(job['steps']), 1)
        service.select({'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id']})
        service.confirm({'job_id': job['id'], 'index': 0, 'asset_id': self.asset['id'], 'confirm': True})
        image = self.lib.get(self.asset['id'])
        backup = self.root / 'scene-backup.png'
        shutil.copy2(image['original'], backup)
        image['backup'] = {'status': 'saved', 'path': str(backup)}
        self.lib.save(image)
        exported = service.export({'project_id': self.p['id'], 'confirm': True})
        manifest = json.loads(Path(exported['manifest']).read_text(encoding='utf8'))
        require_schema(manifest, 'visual-asset-manifest.schema.json', label='项目资产数据')
        asset = manifest['assets'][0]
        self.assertEqual(asset['required_states'], ['净脸前到访', '卸膜后到访'])
        self.assertEqual(len(asset['confirmed_outputs']), 1)
        output = asset['confirmed_outputs'][0]
        self.assertEqual(output['covered_states'], ['净脸前到访', '卸膜后到访'])
        binding = 'AST-ROOM@' + output['output_id']
        self.assertEqual(require_final_images(self.root, [binding]), (True, []))
        resolved = resolve_final_images(self.root, [binding])[binding]
        self.assertEqual(resolved['covered_states'], ['净脸前到访', '卸膜后到访'])
        for invalid in (['净脸前到访', '未知状态'], ['净脸前到访'] * 2, ['卸膜后到访'], []):
            with self.subTest(invalid=invalid):
                self.assertIsNone(_confirmed_output(self.root, 'AST-ROOM', {**output, 'covered_states': invalid}))
        self.assertEqual((self.root / 'narrative.json').read_bytes(), original)

    def test_shared_static_scene_survives_formal_export_and_final_image_gate(self):
        self.check_shared_static_scene_export()

    def test_shared_static_scene_exports_current_paragraph_manifest(self):
        self.check_shared_static_scene_export(paragraph_mode=True)
