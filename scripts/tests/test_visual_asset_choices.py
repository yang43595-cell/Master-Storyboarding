import json
import tempfile
import unittest
from pathlib import Path

from test_visual_asset_projects import fixture
from visual_asset_choices import RELATIVE, change, read_choices
from visual_asset_projects import source_project


class AssetChoices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lib, self.p, _ = fixture(Path(self.temp.name))
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
