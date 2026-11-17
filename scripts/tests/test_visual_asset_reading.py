"""Web method migration acceptance: real local loading, isolated Vault and no model calls."""
import importlib.util
import json
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
    def test_split_rules_reach_web_model_package_without_optional_branches(self):
        for mode in ['asset-library', 'project']:
            request = {'kind': '物品', 'mode': mode, 'subject': '隔离规则测试'}
            if mode == 'project':
                request.update(narrative={'story_paragraphs': []},
                               requirement={'name': '测试', 'source_paragraph_ids': []})
            rules = asset.build_task_package(request)['rules']
            execution = 'docs/rules/execution.md'
            self.assertNotIn(execution, rules)
            self.assertNotIn('skills/_shared/skill-layers.md', rules)
            self.assertIn('## 方向与红线', rules['PROJECT_RULES.md'])
            self.assertNotIn('## 网站审核到画布的统一入口', rules['PROJECT_RULES.md'])
            self.assertIn('用户调整资产图需求', rules['docs/rules/visual-production.md'])
            self.assertNotIn('## 导演稿之后的生产顺序', rules['docs/rules/visual-production.md'])
            continuity = rules['skills/storyboard-director/references/image-continuity-hard-rules.md']
            self.assertIn('## 返修参考与固定基准', continuity)
            self.assertIn('## 唯一面部来源', continuity)
            self.assertNotIn('## 连续事件因时长拆段时的实际衔接参考', continuity)
            self.assertNotIn('docs/rules/experiments.md', rules)
            self.assertNotIn('docs/rules/legacy-review.md', rules)

    def test_active_recovery_section_survives_single_and_batch_web_projection(self):
        import re
        ref = 'skills/_shared/knowledge-access.md'
        original = (ROOT / ref).read_text()
        section = re.search(r'(?ms)^## 压缩后的主动恢复核对\n.*?(?=^## |\Z)', original).group(0).rstrip()
        for mode in ['asset-library', 'project']:
            request = {'kind': '物品', 'mode': mode, 'subject': '恢复输入测试'}
            if mode == 'project':
                request.update(narrative={'story_paragraphs': []},
                               requirement={'name': '测试', 'source_paragraph_ids': []})
            self.assertIn(section, asset.build_task_package(request)['rules'][ref])
        request = self.planning_request({'category': 'LOCATION'})
        self.assertEqual(section, asset.build_task_package(request)['rules'][ref].rstrip())

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
        import visual_asset_library as library
        for runtime in (asset, library.asset_skill):
            resolver = patch.object(runtime, 'resolve_knowledge_root', return_value=self.vault)
            resolver.start()
            self.addCleanup(resolver.stop)

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
            template = self.vault / entry['template_path']
            template.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.live / entry['template_path'], template)
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

    def planning_request(self, *requirements):
        request = self.request('大头照', 'project')  # Existing planning API's placeholder kind.
        request.pop('requirement')
        request['rows'] = [{'requirement': {'asset_id': f'AST-{i}', 'source_event_ids': [], **req}}
                           for i, req in enumerate(requirements, 1)]
        return request

    def test_mixed_project_rows_only_load_rules_for_their_actual_planning_use(self):
        request = self.planning_request(*({'category': category} for category in ('LOCATION', 'REAL_PRODUCT', 'OTHER')))
        body, evidence = asset.prepare(self.root/'mixed', request)
        package = json.loads(body)
        self.assertNotIn('production_item', package)
        self.assertEqual({m['id'] for m in package['professional_methods']}, {
            'IMAGE-CONTINUITY-METHOD', 'ASSET-SCENE-METHOD', 'ASSET-OBJECT-METHOD'})
        rules = package['rules']
        guides = 'skills/asset-image-assistant/references/'
        scene = rules[guides + '03-scene-master-guide.md']
        self.assertIn('## 四、空间坐标与拓扑', scene)
        self.assertIn('### 9.1 多机位高风险场景的四图锁定', scene)
        self.assertNotIn('## 十一、image2 提示词', scene)
        objects = rules[guides + '04-prop-product-master-guide.md']
        self.assertIn('## 六、产品展示与使用合同', objects)
        self.assertNotIn('### 6.1 物品四视图', objects)
        product = rules['skills/_shared/product-reference.md']
        self.assertIn('缺少足够参考时阻塞相关项', product)
        self.assertNotIn('## 正式红帧回读复用已确认产品包', product)
        for excluded in (guides + '01-style-master-guide.md', guides + '02-character-master-guide.md',
                         guides + '02a-body-character-master-guide.md',
                         guides + '05-asset-manifest-handoff-guide.md',
                         'skills/storyboard-planner/references/lighting-prompt-standard.md',
                         'docs/rules/execution.md', 'docs/rules/artifacts.md'):
            self.assertNotIn(excluded, rules)
            self.assertNotIn(excluded, evidence['references'])

    def test_planning_loads_body_style_and_prop_guides_only_when_selected(self):
        request = self.planning_request({'category': 'CHARACTER', 'character_type': 'PET'},
                                        {'category': 'STYLE_REFERENCE'}, {'category': 'PROP'})
        body, _ = asset.prepare(self.root/'body-style-prop', request)
        package = json.loads(body)
        self.assertEqual({m['id'] for m in package['professional_methods']}, {
            'IMAGE-CONTINUITY-METHOD', 'ASSET-STYLE-METHOD', 'ASSET-OBJECT-METHOD'})
        rules = package['rules']
        guides = 'skills/asset-image-assistant/references/'
        self.assertIn('每个稳定造型制作一张16:9横图', rules[guides + '02a-body-character-master-guide.md'])
        self.assertIn('### 用途边界', rules[guides + '01-style-master-guide.md'])
        self.assertIn('### 6.1 物品四视图', rules[guides + '04-prop-product-master-guide.md'])
        self.assertNotIn(guides + '02-character-master-guide.md', rules)
        self.assertNotIn(guides + '03-scene-master-guide.md', rules)
        self.assertNotIn('skills/_shared/product-reference.md', rules)

    def test_missing_selected_planning_section_blocks_before_package_delivery(self):
        request = self.planning_request({'category': 'LOCATION'})
        guide = asset.SKILL/'references/03-scene-master-guide.md'
        raw_read = Path.read_text

        def altered_guide(path, *args, **kwargs):
            content = raw_read(path, *args, **kwargs)
            if path == guide:
                return content.replace('## 四、空间坐标与拓扑', '## 缺少原章节')
            return content

        folder = self.root/'missing-section'
        with patch.object(Path, 'read_text', altered_guide):
            with self.assertRaisesRegex(ValueError, '盘点合同章节缺失.*四、空间坐标与拓扑'):
                asset.prepare(folder, request)
        self.assertFalse((folder/'task-package.json').exists())

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

    def test_design_template_and_real_transport_use_normalized_body_without_changing_source(self):
        import hashlib
        import subprocess
        import visual_asset_library as library
        import visual_asset_codex as bridge
        source = self.vault / 'wiki/reference/提示词模板/1x4三视图模板提示词.md'
        blocks = ['身份链',
                  '人物设定：【性别与年龄感】。构图：3:4，从头顶到锁骨，画面内不出现任何衣服。\n风格：超写实专业人物摄影，真实自然，4K，高细节。',
                  '构图：16:9，正面、左侧、背面三格，无头无脸人台。超写实服装电商棚拍，4K，高细节。',
                  '人物1×4，服装图一、脸图二。']
        source.write_text('\n'.join('```text\n' + block + '\n```' for block in blocks), encoding='utf-8')
        before = source.read_bytes()
        for kind in ['大头照', '服装']:
            with self.subTest(kind=kind):
                request = self.request(kind, 'project')
                records = [] if kind == '大头照' else [{'category': '大头照', 'role': '已确认大头照，唯一面部身份来源'}]
                request.update(subject='海城高级中学角色', template_notes='保留标识4K-24；量规4K。',
                    allow_unreferenced_design=True, reference_records=records,
                    reference_paths=[] if not records else ['confirmed-face.png'])
                seed, path = library.template_prompt(kind, request['subject'], request['template_notes'],
                    allow_unreferenced_design=True, reference_records=records, has_references=bool(records))
                request.update(prompt=seed, template_source=path)
                folder = self.root / ('normalized-' + kind)
                package, _ = asset.prepare(folder, request)
                template = json.loads(package)['production_item']['template']
                self.assertEqual(template['content'], seed)
                self.assertEqual(template['sha256'], hashlib.sha256(Path(path).read_bytes()).hexdigest())
                self.assertEqual(template['content_sha256'], hashlib.sha256(seed.encode()).hexdigest())
                self.assertNotIn('4K，高细节', seed)
                if kind == '大头照':
                    self.assertNotIn('锁骨', seed)
                    self.assertIn('画面内无衣物、领口或肩带', seed)
                self.assertIn('保留标识4K-24；量规4K。', seed)
                self.assertIn('海城高级中学角色', seed)
                self.assertNotIn('从参考照片中提取', seed)
                self.assertNotIn('参照输入图片中的', seed)
                if records:
                    self.assertIn('图1：已确认大头照，唯一面部身份来源', seed)
                    self.assertIn('穿搭设定：', seed)
                result = {'prompt': seed, 'passed': True, 'observations': [], 'uncertainties': [],
                          'checks': [c['id'] + '：本测试检查证据' for c in json.loads(package)['review_checks']], 'issues': []}
                def simulated_cli(command, **kwargs):
                    delivered = kwargs['input']
                    self.assertIn(package, delivered)
                    self.assertIn('生成一张虚构人物的正面大头照' if kind == '大头照' else '穿搭设定：', delivered)
                    self.assertIn('保留标识4K-24', delivered)
                    self.assertNotIn('4K，高细节', delivered)
                    (folder / 'draft.json').write_text(json.dumps(result), encoding='utf-8')
                    return subprocess.CompletedProcess(command, 0, '', '')
                with patch.object(bridge, '_run_prompt_cli', side_effect=simulated_cli) as cli:
                    bridge.run('simulated-codex', folder, 'draft', request, seed, package)
                self.assertEqual(cli.call_count, 1)
        self.assertEqual(source.read_bytes(), before)

    def test_project_character_package_loads_approved_bodies_at_standard_slots(self):
        from core.character_layout import character_layout_reference, character_prompt_templates
        import visual_asset_library as library

        head = {"original": "head.png", "category": "大头照"}
        wardrobe = {"original": "wardrobe.png", "category": "服装三视图"}
        for kind, originals in (("大头照", [head]), ("服装", [wardrobe]),
                                ("人物1x4", [wardrobe, head])):
            with self.subTest(kind=kind):
                records = [*originals, character_layout_reference(kind)]
                seed, path = library.template_prompt(kind, "测试人物", "", reference_records=records)
                request = self.request(kind, "project")
                request.update(reference_records=records, reference_paths=[r["original"] for r in records],
                               prompt=seed, template_source=path)
                package = asset.build_task_package(request)
                self.assertEqual(seed, character_prompt_templates()[kind])
                self.assertEqual(package["production_item"]["template"]["content"], seed)
                self.assertNotIn('skills/asset-image-assistant/references/character-layout-prompts.md', package['rules'])

        records = [head, wardrobe, character_layout_reference("服装")]
        prompt, _ = library.template_prompt("服装", "测试人物", "", reference_records=records)
        self.assertIn("用参考图 2 制作同一套服装", prompt)
        self.assertIn("参考图 3 是服装灰模", prompt)

    def test_scene_seed_and_package_share_actual_view_order_in_both_web_modes(self):
        import visual_asset_library as library
        source = self.vault / 'wiki/reference/提示词模板/环境一致性模板提示词.md'
        blocks = ['身份卡',
                  '生成【SC-XXX 场景名称】的场景母图，用作后续所有机位的唯一空间锚点。',
                  '以图一【SC-XXX 场景母图】为唯一场景参考，生成同一环境的反向平视回望图。',
                  '以图一【SC-XXX 场景母图】为唯一场景参考，生成同一环境的45°斜俯视全景图。',
                  '同时以图一【场景母图】、图二【反向平视回望图】和图三【45°斜俯视全景图】为参考，生成90°俯视。']
        source.write_text('\n'.join('```text\n' + block + '\n```' for block in blocks), encoding='utf-8')
        expected = ['以用户所选照片为场景参考', '图一（已确认的45°母图）',
                    '图一（已确认的45°母图）和图二（已确认的正面图）',
                    '图一（45°母图）、图二（正面图）和图三（反面图）']
        for mode in ['project', 'asset-library']:
            for index, view in enumerate(['45°', '正面', '反面', '俯视']):
                with self.subTest(mode=mode, view=view):
                    request = self.request('环境', mode)
                    request.update(subject='房间', template_notes='当前状态常态', view=view,
                                   index=index if mode == 'asset-library' else index + 5)
                    seed, path = library.template_prompt('环境', request['subject'], request['template_notes'], index)
                    request['template_source'] = path
                    package = asset.build_task_package(request)
                    self.assertEqual(package['production_item']['template']['content'], seed)
                    self.assertIn(expected[index], seed)
                    self.assertNotIn('图二【反向平视回望图】', seed)

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
