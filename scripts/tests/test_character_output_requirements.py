import copy
import unittest

from scripts.core.character_assets import (
    final_output_problems,
    scoped_output_requirements,
    validate_output_plan,
)


class CharacterOutputRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.narrative = {
            'revision': 'R04',
            'asset_requirements': [
                {'asset_id': 'AST-001', 'category': 'CHARACTER', 'character_type': 'HUMAN',
                 'image_decision': '需要制作'},
                {'asset_id': 'AST-002', 'category': 'CHARACTER', 'character_type': 'ANIMAL',
                 'image_decision': '需要制作'},
                {'asset_id': 'AST-003', 'category': 'CHARACTER', 'character_type': 'HUMANOID',
                 'image_decision': '需要制作'},
            ],
        }
        self.record = {
            'source_revision': 'R04', 'evidence': '用户要求定位：人物板为1×4',
            'outputs': {'HOME-FINAL': '人物1x4', 'OUT-FINAL': '人物1x4'},
        }
        self.choices = {'character_output_requirements': {'AST-001': self.record}}
        self.requirements = {'AST-001': dict(self.record['outputs'])}

    def test_explicit_requirements_do_not_change_inputs(self):
        before = copy.deepcopy((self.narrative, self.choices))
        self.assertEqual(scoped_output_requirements(self.narrative, self.choices, ['AST-001']),
                         self.requirements)
        self.assertEqual((self.narrative, self.choices), before)

    def test_legacy_and_legal_single_or_nonhuman_assets_have_no_new_requirements(self):
        self.assertEqual(scoped_output_requirements(self.narrative, {}, ['AST-001']), {})
        self.assertEqual(scoped_output_requirements(self.narrative, self.choices, ['AST-002', 'AST-003']), {})
        self.assertEqual(validate_output_plan({}, ['AST-001@SINGLE'], {}), [])
        self.assertEqual(final_output_problems({}, ['AST-001@SINGLE'], {}), [])

    def test_other_assets_bad_record_does_not_block_exact_scope(self):
        self.choices['character_output_requirements']['AST-003'] = {'source_revision': 'R00'}
        self.assertEqual(scoped_output_requirements(self.narrative, self.choices, ['AST-001']),
                         self.requirements)
        self.assertEqual(scoped_output_requirements(self.narrative, self.choices, ['AST-002']), {})

    def test_scoped_stale_source_and_missing_evidence_are_rejected(self):
        for key, value in [('source_revision', 'R03'), ('evidence', '  ')]:
            with self.subTest(key=key):
                choices = copy.deepcopy(self.choices)
                choices['character_output_requirements']['AST-001'][key] = value
                with self.assertRaises(ValueError):
                    scoped_output_requirements(self.narrative, choices, ['AST-001'])

    def test_nonhuman_explicit_board_requirement_is_rejected(self):
        self.choices['character_output_requirements']['AST-002'] = copy.deepcopy(self.record)
        with self.assertRaisesRegex(ValueError, '人类或人形'):
            scoped_output_requirements(self.narrative, self.choices, ['AST-002'])

    def test_local_type_override_is_respected(self):
        self.choices['character_types'] = {
            'AST-001': {'source_revision': 'R04', 'character_type': 'PET', 'evidence': '用户指定宠物'},
            'AST-003': {'source_revision': 'R00'},
        }
        with self.assertRaisesRegex(ValueError, '人类或人形'):
            scoped_output_requirements(self.narrative, self.choices, ['AST-001'])

    def test_cancelled_and_split_assets_do_not_require_boards(self):
        for field in ['excluded', 'replaced']:
            with self.subTest(field=field):
                choices = copy.deepcopy(self.choices)
                choices[field] = {'AST-001': {}}
                choices['character_output_requirements']['AST-001'] = {'source_revision': 'R00'}
                self.assertEqual(scoped_output_requirements(self.narrative, choices, ['AST-001']), {})

    def test_no_image_and_pending_decisions_do_not_revive_board_requirements(self):
        for decision in ['无需独立图', '待定']:
            with self.subTest(decision=decision):
                self.narrative['asset_requirements'][0]['image_decision'] = decision
                self.choices['character_output_requirements']['AST-001'] = {'source_revision': 'R00'}
                self.assertEqual(scoped_output_requirements(self.narrative, self.choices, ['AST-001']), {})

    def test_local_image_decision_override_is_respected(self):
        self.choices['image_decisions'] = {
            'source_revision': 'R04',
            'items': {'AST-001': {'image_decision': '无需独立图', 'image_reason': '当前需求无需此图'}},
        }
        self.assertEqual(scoped_output_requirements(self.narrative, self.choices, ['AST-001']), {})

    def test_outputs_must_be_nonempty_exact_ids_and_supported_kinds(self):
        for outputs in [{}, {'../HOME': '人物1x4'}, {'HOME@OTHER': '人物1x4'},
                        {'HOME': '人物单图'}, {' HOME': '人物1x4'}]:
            with self.subTest(outputs=outputs):
                choices = copy.deepcopy(self.choices)
                choices['character_output_requirements']['AST-001']['outputs'] = outputs
                with self.assertRaises(ValueError):
                    scoped_output_requirements(self.narrative, choices, ['AST-001'])

    def test_final_plan_cannot_downgrade_or_omit_kind(self):
        target = 'AST-001@HOME-FINAL'
        for kinds in [{}, {target: '人物单图'}]:
            with self.subTest(kinds=kinds):
                self.assertEqual(len(validate_output_plan(self.requirements, [target], kinds)), 1)
        self.assertEqual(validate_output_plan(self.requirements, [target], {target: '人物1x4'}), [])

    def test_intermediate_and_unregistered_targets_are_not_blocked(self):
        self.assertEqual(validate_output_plan(self.requirements,
                                             ['AST-001@HEAD', 'AST-001@HOME-WARDROBE', 'AST-002@SINGLE'], {}), [])

    def test_direct_final_reuse_needs_no_process_images(self):
        available = {'AST-001': [{'output_id': 'HOME-FINAL', 'output_kind': '人物1x4'}]}
        self.assertEqual(final_output_problems(self.requirements, ['AST-001@HOME-FINAL'], available), [])

    def test_exact_scope_does_not_require_other_outfit_or_asset(self):
        available = {'AST-001': [{'output_id': 'HOME-FINAL', 'output_kind': '人物1x4'}]}
        self.requirements['AST-003'] = {'MISSING-FINAL': '人物1x4'}
        self.assertEqual(final_output_problems(self.requirements, ['AST-001@HOME-FINAL'], available), [])
        self.assertEqual(len(final_output_problems(self.requirements, ['AST-001'], available)), 1)

    def test_final_check_rejects_missing_ambiguous_or_wrong_kind_outputs(self):
        output = {'output_id': 'HOME-FINAL', 'output_kind': '人物1x4'}
        for available in [{}, {'AST-001': [output, output]},
                          {'AST-001': [{'output_id': 'HOME-FINAL'}]},
                          {'AST-001': [{'output_id': 'HOME-FINAL', 'output_kind': '人物单图'}]}]:
            with self.subTest(available=available):
                self.assertEqual(len(final_output_problems(self.requirements,
                                                          ['AST-001@HOME-FINAL'], available)), 1)

    def test_final_check_rejects_selecting_intermediate_as_completed_board(self):
        available = {'AST-001': [{'output_id': 'HEAD', 'output_kind': '大头照'}]}
        self.assertEqual(len(final_output_problems(self.requirements, ['AST-001@HEAD'], available)), 1)


if __name__ == '__main__':
    unittest.main()
