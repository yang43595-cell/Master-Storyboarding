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
