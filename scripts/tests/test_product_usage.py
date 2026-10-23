import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_product_usage as usage


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

if __name__ == '__main__': unittest.main()
