"""Asset semantic regressions; synthetic text templates, no model or media generation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('asset_semantics', ROOT / 'skills/asset-image-assistant/library_runtime.py')
asset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asset)


class AssetSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.template = Path(self.temp.name) / 'synthetic-template.md'
        self.template.write_text('\n'.join('```text\nBLOCK_'+str(i)+'\n```' for i in range(5)), encoding='utf-8')

    def test_four_display_names_and_legacy_views_share_blocks(self):
        for index, (name, legacy, block) in enumerate(zip(asset.recipe('环境')['steps'], ['45°', '正面', '反面', '俯视'], [3, 1, 2, 4])):
            for view in [name, legacy, asset.recipe('环境')['step_keys'][index]]:
                with self.subTest(view=view), patch.object(asset, 'template_source', return_value=self.template):
                    self.assertEqual(asset._template_excerpt('', '环境', view)['block_index'], block)

    def test_unknown_view_and_invalid_index_do_not_fall_back(self):
        for view in ['', '未知', '反面兼俯视']:
            with self.subTest(view=view), patch.object(asset, 'template_source', return_value=self.template), self.assertRaises(ValueError):
                asset._template_excerpt('', '环境', view)
        for index in [-1, 4, True, '3']:
            with self.subTest(index=index), self.assertRaises(ValueError):
                asset.template_step('环境', index=index)

    def test_task_package_library_index_is_not_project_global_index(self):
        with patch.object(asset, 'template_source', return_value=self.template):
            request = {'kind':'环境','view':'90°俯瞰','reference_paths':['synthetic-reference'],'index':3}
            package = asset.build_task_package(request, refs=['skills/asset-image-assistant/library-profile.json'])
            self.assertEqual(package['production_item']['template']['block_index'],4)
            request.update(mode='project',index=7,view='俯视',narrative={'events':[]},requirement={})
            package = asset.build_task_package(request, refs=['skills/asset-image-assistant/library-profile.json'])
            self.assertEqual(package['production_item']['template']['block_index'],4)

    def test_source_name_is_not_quality_advertising(self):
        prompt='校园正面平视图；门楣保留剧情指定校名“海城高级中学”，灰色砖墙，午后侧光。'
        self.assertEqual(asset.language_issues(prompt), [])
        for literal in ['远处是大片草地。', '一大片的草地与高级中学门牌。', '海城高级中学的画面，机位平视。']:
            self.assertEqual(asset.language_issues(literal), [])
        for prompt in ['影视级8K高级感杯子', '高级画质', '唯美风格', '高级，大片，8K', '“高级感”画面', '打造唯美的画面', '呈现高级的画质', '好莱坞大片质感', '高端 的 摄影']:
            with self.subTest(prompt=prompt):
                self.assertTrue(asset.language_issues(prompt))
                review={'prompt':prompt,'passed':True,'issues':[], 'checks':[c['id']+':通过' for c in asset.checks()]}
                self.assertFalse(asset.validate_review(review)['passed'])
        data={'prompt':prompt,'passed':True,'issues':[], 'checks':[c['id']+':通过' for c in asset.checks()]}
        self.assertFalse(asset.validate_review(data)['passed'])

    def test_profile_change_invalidates_prompt_review_not_image_records(self):
        step={'prompt':'灰色砖墙，午后侧光。','status':'confirmed','selected':'existing-image', 'audit':{'status':'passed','skill_execution':{'version':'asset-library-skill-v3'}}}
        self.assertFalse(asset.approved(step))
        step['audit']['skill_execution']['version']=asset.profile()['version']
        self.assertTrue(asset.approved(step))
        self.assertEqual(step['selected'],'existing-image')
        self.assertEqual(step['status'],'confirmed')

    def test_object_lightweight_policy_is_not_expanded(self):
        checks = asset.checks('project', '物品')
        self.assertEqual(len(checks), 2)
        data = {'prompt':'影视级8K高级感杯子', 'passed':True, 'issues':[],
                'checks':[c['id']+':通过' for c in checks]}
        # Existing ambiguity R0 remains explicit, not silently changed by A3.
        self.assertTrue(asset.validate_review(data, 'project', '物品')['passed'])

    def test_actual_seed_to_step_request_to_package(self):
        import visual_asset_library as library
        lib=object.__new__(library.Library)
        lib.get=lambda *a, **kw: {'original':'synthetic-reference','id':'reference'}
        job={'id':'synthetic','kind':'环境','title':'海城高级中学','notes':'','source_id':'reference','provider':'imagegen','workflow_version':3,
             'steps':[{'name':name,'status':'confirmed','selected':'reference'} for name in asset.recipe('环境')['steps']]}
        with patch.object(library.asset_skill,'template_source',return_value=self.template):
            for index, expected in enumerate([3,1,2,4]):
                request=lib.step_request(job,index)
                self.assertIn('BLOCK_'+str(expected), request['prompt'])
                package=library.asset_skill.build_task_package(request,refs=['skills/asset-image-assistant/library-profile.json'])
                self.assertEqual(package['production_item']['template']['block_index'], expected)


if __name__ == '__main__':
    unittest.main()
