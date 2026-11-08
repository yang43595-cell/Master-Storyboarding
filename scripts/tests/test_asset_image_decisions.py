import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.story_sources import validate_asset_decisions
from core.asset_refs import build_asset_list
from visual_asset_choices import image_work_items, save_choices, read_choices
from test_visual_asset_projects import fixture, update_narrative_checksum


class ImageDecisions(unittest.TestCase):
    def test_markdown_requires_judgement_but_does_not_rejudge_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'candidate.md'
            p.write_text('| 资产 ID | 名称 |\n| AST-A | 草莓 |', encoding='utf-8')
            with self.assertRaises(ValueError):
                validate_asset_decisions(p)
            p.write_text('| 资产 ID | 处理结论 | 判断依据 |\n| AST-A | 无需独立图 | 随卧室分镜表现比例 |', encoding='utf-8')
            validate_asset_decisions(p)
            p.write_text('| 资产 ID | 处理结论 | 判断依据 |\n| AST-A | 需要制作 | |', encoding='utf-8')
            with self.assertRaises(ValueError):
                validate_asset_decisions(p)

    def test_workbench_filters_without_losing_story_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib, p, _ = fixture(Path(tmp))
            root = Path(p['path'])
            n = json.loads((root / 'narrative.json').read_text(encoding='utf-8'))
            n['asset_requirements'][0].update(image_decision='无需独立图', image_reason='随分镜表现')
            n['asset_requirements'][1].update(image_decision='待定', image_reason='等拍法明确')
            (root / 'narrative.json').write_text(json.dumps(n), encoding='utf-8')
            update_narrative_checksum(root)
            result = lib.projects.sync(p['id'])
            self.assertEqual(result['rows'], [])
            with self.assertRaisesRegex(ValueError, '待定'):
                lib.projects.export({'project_id': p['id'], 'confirm': True})
            self.assertEqual(len(result['narrative']['asset_requirements']), 2)
            snapshot = build_asset_list(root, n)
            self.assertEqual([r['state'] for r in snapshot['items']], ['no_image', 'pending'])
            save_choices(root, n, read_choices(root))
            table = (root / '04_资产/当前资产需求表.md').read_text(encoding='utf-8')
            self.assertIn('判断依据', table)
            self.assertIn('随分镜表现', table)
            n['asset_requirements'][1].update(image_decision='无需独立图', image_reason='随场景表现')
            (root / 'narrative.json').write_text(json.dumps(n), encoding='utf-8')
            update_narrative_checksum(root)
            lib.projects.sync(p['id'])
            self.assertTrue(lib.projects.export({'project_id': p['id'], 'confirm': True}))

    def test_reuse_remains_selectable_but_cannot_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib, p, _ = fixture(Path(tmp))
            req = copy.deepcopy(p['rows'][0]['requirement'])
            req.update(image_decision='复用现有素材', image_reason='已有确认场景')
            self.assertEqual(image_work_items({'asset_requirements': [req]}), [req])
            with patch.object(lib.projects, 'context', return_value=(p, {'requirement': req}, {})):
                with self.assertRaisesRegex(ValueError, '复用项只选图'):
                    lib.projects.request({}, 0)

    def test_legacy_requirements_keep_existing_behavior(self):
        req = {'asset_id': 'AST-OLD'}
        self.assertEqual(image_work_items({'asset_requirements': [req]}), [req])

    def test_project_judgement_overlay_preserves_confirmed_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib, project, _ = fixture(Path(tmp))
            root = Path(project['path'])
            keep_id = project['rows'][1]['requirement']['asset_id']
            lib.projects.prepare_reuse({'project_id': project['id'], 'ast_id': keep_id})
            old_job = next(r['job_id'] for r in lib.projects.get(project['id'])['rows'] if r['requirement']['asset_id'] == keep_id)
            original = (root / 'narrative.json').read_bytes()
            n = json.loads(original)
            choices = read_choices(root)
            choices['revision'] = 1
            choices['image_decisions'] = {'source_revision': n['revision'], 'items': {
                n['asset_requirements'][0]['asset_id']: {'image_decision': '无需独立图', 'image_reason': '随场景表现'}}}
            save_choices(root, n, choices)
            result = lib.projects.sync(project['id'])
            self.assertEqual(len(result['rows']), 1)
            self.assertEqual(result['rows'][0]['job_id'], old_job)
            self.assertEqual((root / 'narrative.json').read_bytes(), original)
            self.assertEqual(build_asset_list(root, n)['items'][0]['state'], 'no_image')
            choices['image_decisions']['source_revision'] = 'R99'
            from visual_asset_choices import effective
            with self.assertRaisesRegex(ValueError, '上游版本'):
                effective(n, choices)
