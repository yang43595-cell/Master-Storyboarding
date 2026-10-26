"""Web method migration acceptance: real local loading, isolated Vault and no model calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('asset_reading_test', ROOT / 'skills/asset-image-assistant/library_runtime.py')
asset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asset)
from core.method_documents import resolve_method_documents, MethodDocumentError


class WebReadingTests(unittest.TestCase):
    def test_writing_projection_only_omits_named_workflow_sections(self):
        for ref, headings in {
            'PROJECT_RULES.md': {'项目目录按生产流程排列','双文件产物串行交付', 'JSON 交付与按需导出','导演稿之后的生产顺序'},
            'skills/asset-image-assistant/SKILL.md': {'后续交接'},
            'skills/_shared/knowledge-access.md': {'局部维护边界','读取与执行开销'},
        }.items():
            import re
            original = (ROOT / ref).read_text(encoding='utf-8')
            projected = asset._writing_rules(ref, original, True)
            for section in re.split(r'(?m)(?=^## )', original):
                heading = section.split('\n',1)[0].removeprefix('## ').strip()
                if heading not in headings:
                    self.assertIn(section, projected)
            self.assertLess(len(projected), len(original))
            self.assertEqual(asset._writing_rules(ref, original, False), original)
        for ref in ['skills/storyboard-director/references/image-continuity-hard-rules.md',
                    'skills/_shared/product-reference.md',
                    'skills/voiceover-to-video-script/references/prompt-language-hard-rules.md']:
            original = (ROOT / ref).read_text(encoding='utf-8')
            self.assertEqual(asset._writing_rules(ref, original, True), original)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'
        from test_visual_asset_projects import synthetic_vault
        live = self.live = synthetic_vault(self.root / 'synthetic-live-vault')
        self.profile = json.loads((asset.SKILL / 'knowledge-profile.json').read_text(encoding='utf-8'))
        # Copy only declared methods and the existing required dynamic rule source.
        for relative in [m['path'] for m in self.profile['professional_methods']] + [
                'wiki/concepts/03-AI生成与视频/提示词污染.md']:
            target = self.vault / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(live / relative, target)
        templates = self.vault / 'wiki/reference/提示词模板'
        templates.mkdir(parents=True)
        for name in {r['template'] for r in asset.profile()['recipes'].values() if 'template' in r}:
            (templates / name).write_text('\n\n'.join('```text\n隔离模板 '+str(i)+'\n```' for i in range(5)), encoding='utf-8')
        env = patch.dict(os.environ, {'MSB_VAULT_ROOT': str(self.vault)})
        env.start()
        self.addCleanup(env.stop)

    def request(self, kind, mode):
        request = {'kind': kind, 'mode': mode, 'reference_paths': ['fixture-reference.png'], 'view': '正面'}
        if mode == 'project':
            project = self.root / 'project'
            project.mkdir(exist_ok=True)
            request.update(project_path=str(project),
                narrative={'project_id': 'isolated', 'revision': 'TEST', 'locked_facts': [], 'events': []},
                requirement={'asset_id': 'AST-TEST', 'source_event_ids': []})
        return request

    def test_creator_mask_method_is_selected_only_for_explicit_face_effect_request(self):
        relative = 'projects/Master-Storyboarding/应用知识/图片资产/视觉补充方法.md'
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.live / relative, target)
        for skill in ['asset-image-assistant', 'storyboard-director']:
            profile = json.loads((ROOT / 'skills' / skill / 'knowledge-profile.json').read_text(encoding='utf-8'))
            entry = next(x for x in profile['professional_methods'] if x['id'] == 'VGEN-CREATOR-POLISHING-MASK-USE')
            self.assertTrue((self.live / entry['template_path']).is_file())
            for selected in [None, False, True]:
                with self.subTest(skill=skill, selected=selected):
                    request = {} if selected is None else {'creator_mask_face_effect': selected}
                    result = resolve_method_documents(self.vault, profile, {'skill': skill, 'request': request})
                    methods = {m.id: m for m in result.required}
                    self.assertEqual(entry['id'] in methods, selected is True)
                    if selected:
                        self.assertEqual(methods[entry['id']].content,
                                         (self.vault / entry['path']).read_text(encoding='utf-8'))

    def test_every_kind_delivers_complete_applicable_methods_in_both_modes(self):
        for mode in ['asset-library', 'project']:
            for kind, recipe in asset.profile()['recipes'].items():
                with self.subTest(mode=mode, kind=kind):
                    folder = self.root / (mode + kind)
                    text, evidence = asset.prepare(folder, self.request(kind, mode))
                    package = json.loads(text)
                    methods = package['professional_methods']
                    expected_methods = {'IMAGE-CONTINUITY-METHOD'}
                    if recipe['method_scope'] != 'body':
                        expected_methods.add('ASSET-'+recipe['method_scope'].upper()+'-METHOD')
                    self.assertEqual({m['id'] for m in methods}, expected_methods)
                    if recipe['method_scope'] == 'body':
                        self.assertNotIn('ASSET-CHARACTER-METHOD', {m['id'] for m in methods})
                        self.assertIn('02a-body-character-master-guide.md', json.dumps(package, ensure_ascii=False))
                    for method in methods:
                        self.assertEqual(method['content'], (self.vault / method['path']).read_text(encoding='utf-8-sig'))
                    expected_mode = 'project-web-mode' if mode == 'project' else 'library-mode'
                    other_mode = 'library-mode' if mode == 'project' else 'project-web-mode'
                    self.assertNotIn('skills/asset-image-assistant/modules/'+expected_mode+'.md', package['rules'])
                    self.assertNotIn('skills/asset-image-assistant/modules/'+other_mode+'.md', package['rules'])
                    template = package['production_item']['template']
                    body = template.get('content') or package['rules'][template['content_ref'].removeprefix('rules.')]
                    self.assertTrue(body.strip())
                    self.assertEqual(evidence['attestation'], 'input_assembled_not_agent_reading')
                    self.assertFalse(list(folder.rglob('method_reading_evidence.json')))
        self.assertEqual(list((self.root/'project').iterdir()), [])

    def test_mixed_project_rows_do_not_load_dummy_kind_method_or_guide(self):
        request = self.request('大头照', 'project')  # Existing planning API's placeholder kind.
        request['rows'] = [{'requirement': {'category': c, 'source_event_ids': []}} for c in ['LOCATION', 'PROP']]
        body, _ = asset.prepare(self.root/'mixed', request)
        package = json.loads(body)
        self.assertNotIn('production_item', package)
        self.assertEqual({m['id'] for m in package['professional_methods']}, {
            'IMAGE-CONTINUITY-METHOD', 'ASSET-SCENE-METHOD', 'ASSET-OBJECT-METHOD'})
        self.assertNotIn('skills/asset-image-assistant/references/02-character-master-guide.md', package['rules'])

    def test_paragraph_text_reaches_model_without_event_summary(self):
        request = self.request('物品', 'project')
        request['rows'] = [{'requirement': {'asset_id': 'AST-TEST', 'category': 'PROP', 'source_paragraph_ids': ['STP-01']}}]
        request['narrative'] = {'project_id': 'isolated', 'revision': 'R01', 'story_paragraphs': [
            {'paragraph_id': 'STP-01', 'text': '两剂面膜掉到地上，琳琳拾起。'},
            {'paragraph_id': 'STP-02', 'text': '不相关人物离开。'}]}
        request.pop('requirement')
        package = asset.build_task_package(request)
        actual = package['request']['narrative']
        self.assertEqual(actual['story_paragraphs'], request['narrative']['story_paragraphs'][:1])
        self.assertNotIn('events', actual)

    def test_missing_empty_or_inactive_method_blocks_before_package_delivery(self):
        method = next(m for m in self.profile['professional_methods'] if m['id'] == 'ASSET-OBJECT-METHOD')
        path = self.vault / method['path']
        text = path.read_text(encoding='utf-8')
        for condition in ['missing', 'empty', 'inactive']:
            with self.subTest(condition=condition):
                if condition == 'missing':
                    path.unlink()
                elif condition == 'empty':
                    path.write_text(text.split('---', 2)[0]+'---'+text.split('---', 2)[1]+'---\n', encoding='utf-8')
                else:
                    path.write_text(text.replace('status: enabled', 'status: deprecated'), encoding='utf-8')
                folder = self.root / condition
                with self.assertRaises(ValueError):
                    asset.prepare(folder, self.request('物品', 'asset-library'))
                self.assertFalse((folder/'task-package.json').exists())
                path.write_text(text, encoding='utf-8')

    def test_missing_binding_and_required_dynamic_rule_are_not_silently_ignored(self):
        raw_read = Path.read_text
        def missing_binding(path, *args, **kwargs):
            if path == asset.SKILL/'knowledge-profile.json':
                return json.dumps({**self.profile, 'professional_methods': []})
            return raw_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', missing_binding):
            with self.assertRaisesRegex(ValueError, '未完整绑定'):
                asset.prepare(self.root/'no-binding', self.request('物品', 'asset-library'))
        (self.vault/'wiki/concepts/03-AI生成与视频/提示词污染.md').unlink()
        with self.assertRaisesRegex(ValueError, '动态规则缺失'):
            asset.prepare(self.root/'no-rule', self.request('物品', 'asset-library'))

    def test_only_required_proposed_bindings_block_the_current_item(self):
        raw_read = Path.read_text
        proposed_id = 'ASSET-CHARACTER-METHOD'
        def proposed_binding(path, *args, **kwargs):
            if path == asset.SKILL/'knowledge-profile.json':
                return json.dumps({**self.profile, 'professional_methods': [
                    {**m, 'binding_status': 'proposed'} if m['id'] == proposed_id else m
                    for m in self.profile['professional_methods']]})
            return raw_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', proposed_binding):
            body, _ = asset.prepare(self.root/'unrelated-proposed', self.request('物品', 'asset-library'))
            self.assertNotIn(proposed_id, {m['id'] for m in json.loads(body)['professional_methods']})
            with self.assertRaisesRegex(ValueError, '绑定尚未确认'):
                asset.prepare(self.root/'required-proposed', self.request('服装', 'asset-library'))

    def test_explicit_missing_template_does_not_fall_back(self):
        request = self.request('服装', 'asset-library')
        request['template_source'] = str(self.root/'missing-template.md')
        with self.assertRaisesRegex(ValueError, '本次指定'):
            asset.prepare(self.root/'no-template', request)

    def test_missing_local_contract_and_conflicting_confirmed_rules_block(self):
        refs = asset.local_references('物品') + ['skills/asset-image-assistant/missing-contract.md']
        with patch.object(asset, 'local_references', return_value=refs):
            with self.assertRaisesRegex(ValueError, '本地合同缺失'):
                asset.prepare(self.root/'no-contract', self.request('物品', 'asset-library'))
        blocks = []
        for number, (key, other) in enumerate([('probe.a', 'probe.b'), ('probe.b', 'probe.a')], 1):
            meta = {'id': f'MSB-KB-PROBE-{number:03d}', 'canonical_key': key, 'kind': 'rule', 'status': 'confirmed',
                    'authority': 'wiki', 'projects': ['Master-Storyboarding'], 'stages': ['asset-image-assistant'],
                    'topics': ['image-prompt'], 'triggers': [], 'conflicts_with': [other], 'priority': 90,
                    'sources': [], 'applies_when': '隔离冲突检查'}
            blocks.append('<!-- msb-knowledge\n'+json.dumps(meta)+'\n-->\n隔离冲突正文 '+str(number)+
                          '。\n^'+meta['id'].lower()+'\n<!-- /msb-knowledge -->')
        (self.vault/'wiki/concepts/conflicts.md').write_text('\n'.join(blocks), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'confirmed 冲突'):
            asset.prepare(self.root/'conflicts', self.request('物品', 'asset-library'))

    def test_web_derives_method_scope_and_does_not_trust_override_flags(self):
        request = self.request('物品', 'asset-library')
        request['asset_method_scopes'] = {'character': True, 'object': False}
        body, _ = asset.prepare(self.root/'scopes', request)
        self.assertEqual({m['id'] for m in json.loads(body)['professional_methods']},
                         {'IMAGE-CONTINUITY-METHOD', 'ASSET-OBJECT-METHOD'})

    def test_model_transport_receives_the_complete_package_without_extra_calls(self):
        import subprocess
        import visual_asset_codex as bridge
        request = self.request('物品', 'asset-library')
        folder = self.root/'transport'
        package, _ = asset.prepare(folder, request)
        result = {'prompt': '隔离测试返回，不用于生成。', 'passed': True,
                  'observations': ['模拟观察'], 'checks': ['模拟检查'], 'uncertainties': [], 'issues': []}
        def simulated_cli(command, **kwargs):
            (folder/'draft.json').write_text(json.dumps(result), encoding='utf-8')
            self.assertIn(package, kwargs['input'])
            return subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(bridge, '_run_prompt_cli', side_effect=simulated_cli) as cli:
            bridge.run('simulated-codex', folder, 'draft', request, '', package)
        self.assertEqual(cli.call_count, 1)

    def test_visual_supplement_shares_same_source_and_other_skill_does_not_load_it(self):
        profile = json.loads((ROOT/'skills/storyboard-director/knowledge-profile.json').read_text(encoding='utf-8'))
        for method in profile['professional_methods']:
            target = self.vault / method['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.live / method['path'], target)
        result = resolve_method_documents(self.vault, profile, {'skill': 'storyboard-director'})
        self.assertEqual({m.id for m in result.required}, {'VISUAL-SUPPLEMENT-METHOD', 'IMAGE-CONTINUITY-METHOD'})
        shared = next(m for m in result.required if m.id == 'IMAGE-CONTINUITY-METHOD')
        asset_method = resolve_method_documents(self.vault, self.profile, {'skill': 'asset-image-assistant'}).required[0]
        self.assertEqual(shared.content, asset_method.content)
        self.assertEqual(shared.vault_path, asset_method.vault_path)
        body, _ = asset.prepare(self.root/'asset-with-visual-method-present', self.request('物品', 'asset-library'))
        self.assertNotIn('VISUAL-SUPPLEMENT-METHOD', {m['id'] for m in json.loads(body)['professional_methods']})
        self.assertEqual(resolve_method_documents(self.vault, profile, {'skill': 'blender-previs'}).required, [])
