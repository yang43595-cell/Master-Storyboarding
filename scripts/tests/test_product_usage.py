import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_product_usage as usage
from core import product_reference_pack
from core.io import sha256_file, write_json


class ProductUsageTests(unittest.TestCase):
    def setUp(self):
        self.narrative = {'revision': 'R02', 'story_paragraphs': [{'paragraph_id': 'STP-006',
            'text': '面膜加入碗，刷子搅拌。'}, {'paragraph_id': 'STP-007', 'text': '公主穿泳装裙泡在面膜温泉中。'}],
            'asset_requirements': [{'asset_id': 'AST-MASK', 'category': 'REAL_PRODUCT', 'name': '造物者面膜',
                'reference_paths': ['creator-polishing-mask-2/PRODUCT_BRIEF.md']},
                {'asset_id': 'AST-PRINCESS', 'category': 'CHARACTER', 'name': '公主',
                 'wardrobe_states': [{'name': '泳装裙'}]}]}
        self.inventory = [{'product_id': 'creator-polishing-mask-2', 'product_name': '造物者面膜',
            'aliases': ['造物者面膜'], 'action_key': 'stir', 'reference_role': 'action', 'asset_id': 'a',
            'media_type': 'video', 'name': '搅拌参考', 'original': 'missing.mp4'}]
        self.matches = {'references': [{'product_ast_id': 'AST-MASK', 'action_key': 'stir',
            'paragraph_ids': ['STP-006'], 'evidence': '刷子搅拌'}], 'effects': [{'product_ast_id': 'AST-MASK',
            'character_ast_id': 'AST-PRINCESS', 'paragraph_ids': ['STP-007'], 'evidence': '泡在面膜温泉中',
            'wardrobe_state': '泳装裙', 'state': '微缩泡温泉'}]}

    def test_wrong_product_and_invented_evidence_rejected(self):
        for field, value in [('product_ast_id', 'AST-OTHER'), ('evidence', '公主涂抹在脸上')]:
            wrong = copy.deepcopy(self.matches)
            wrong['references'][0][field] = value
            with self.assertRaises(ValueError):
                usage.validate_matches(self.narrative, self.inventory, wrong)

    def test_missing_action_stays_blank_and_no_generation_route(self):
        matches = copy.deepcopy(self.matches)
        matches['references'][0]['action_key'] = 'no_reference'
        usage.validate_matches(self.narrative, self.inventory, matches)
        service = SimpleNamespace(copy_to_project=lambda *a: self.fail('缺少参考不能复制'))
        saved = usage.persist_matches(service, {'narrative': self.narrative, 'source': {'revision': 'R02'}},
                                      self.inventory, matches, 'signature')
        self.assertIsNone(saved['references'][0]['project_path'])
        effect = saved['effects'][0]
        self.assertIsNone(effect['generation_route'])
        self.assertIsNone(effect['image_path'])
        self.assertEqual(effect['status'], 'awaiting_reference_review')
        self.assertEqual(effect['reference_need'], '')
        self.assertNotIn('asset_id', effect)
        self.assertNotIn('微缩', effect['reference_need'])

    def test_story_revision_invalidates_reference_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);path = root / usage.FILE;path.parent.mkdir()
            path.write_text(json.dumps({'story_hash': usage.story_hash(self.narrative), 'status': 'matched'}))
            self.assertEqual(usage.load_usage(root, self.narrative)['status'], 'matched')
            changed = copy.deepcopy(self.narrative)
            changed['story_paragraphs'][0]['text'] = '只展示包装'
            self.assertEqual(usage.load_usage(root, changed)['status'], 'stale')

    def test_duplicate_or_wrong_wardrobe_effect_rejected(self):
        wrong = copy.deepcopy(self.matches);wrong['effects'].append(wrong['effects'][0])
        with self.assertRaises(ValueError): usage.validate_matches(self.narrative, self.inventory, wrong)
        wrong = copy.deepcopy(self.matches);wrong['effects'][0]['wardrobe_state'] = '现实浴袍'
        with self.assertRaises(ValueError): usage.validate_matches(self.narrative, self.inventory, wrong)

    def test_unrelated_product_not_matched(self):
        inventory = copy.deepcopy(self.inventory)
        inventory[0].update(product_id='another-product', product_name='另一款', aliases=['另一款'])
        self.assertEqual(usage.products(self.narrative, inventory), [])

    def test_pinned_pack_scopes_web_matches_and_previews_without_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product_id = 'creator-polishing-mask-2'
            pack = root / 'packs' / product_id / 'R01'
            pack.mkdir(parents=True)
            entries = []
            for ident, filename, role, key in (
                ('BOX', 'box.png', 'packaging', ''),
                ('STIR', 'stir.mp4', 'action', 'stir'),
                ('MONTAGE', 'all.mp4', 'technique', 'montage'),
            ):
                path = pack / filename
                path.write_bytes(filename.encode())
                entries.append({'id': ident, 'path': filename, 'role': role, 'action_key': key,
                                'source': 'fixture', 'coverage': 'fixture', 'sha256': sha256_file(path),
                                'sequence_only': ident == 'MONTAGE',
                                'usages': ['real_use'] if key else ['display', 'real_use']})
            manifest = pack / 'manifest.json'
            write_json(manifest, {'product_id': product_id, 'product_name': '造物者面膜',
                                  'revision': 'R01', 'references': entries})
            kickoff = {'decision_ref': 'fixture', 'product': {'mode': 'insert', 'product_id': product_id,
                       'reference_pack_revision': 'R01', 'reference_pack_sha256': sha256_file(manifest)},
                       'canvas': {'platform': 'redframe', 'model': 'Seedance 2.5', 'aspect_ratio': '9:16',
                                  'resolution': '720p', 'create_new': True}}
            request = root / 'project' / '00_项目管理' / 'project_request.json'
            project = {'path': str(request.parent.parent), 'narrative': self.narrative, 'source': {'revision': 'R02'}}
            with patch.object(product_reference_pack, 'PACK_ROOT', root / 'packs'):
                for scope, ids in [('display', ['BOX']), ('real_use', ['BOX', 'STIR'])]:
                    kickoff['product']['product_usage'] = scope
                    write_json(request, {'kickoff': kickoff})
                    inventory = usage.project_catalogue(None, project)
                    self.assertEqual([row['asset_id'] for row in inventory], ids)
                    result = self.matches if scope == 'real_use' else {'references': [], 'effects': []}
                    saved = usage.persist_matches(None, project, inventory, result, 'fixture')
                    write_json(Path(project['path']) / usage.FILE, saved)
                    self.assertEqual(usage.preview_path(project, ids[-1]).read_bytes(),
                                     (pack / ('stir.mp4' if scope == 'real_use' else 'box.png')).read_bytes())
                    with self.assertRaises(ValueError):
                        usage.preview_path(project, 'MONTAGE')

if __name__ == '__main__': unittest.main()
