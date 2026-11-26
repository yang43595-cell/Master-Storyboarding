import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_product_usage as usage
from core import product_reference_pack
from core.asset_refs import load_asset_list
from core.io import sha256_file, write_json
from core.project_state import new_project_state, write_project_state


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

    def test_web_cached_matches_restore_only_missing_copies_without_model(self):
        from visual_asset_projects import Projects

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            vault = root / 'vault'
            originals = vault / '资产库'
            originals.mkdir(parents=True)
            records = {}
            for ident, suffix in [('stir', '.mp4'), ('spread', '.mp4'), ('box', '.png'), ('label', '.png')]:
                source = originals / (ident + suffix)
                source.write_bytes(ident.encode())
                records[ident] = dict(id=ident, name=ident, original=str(source),
                    media_type='image' if suffix == '.png' else 'video',
                    product_reference={**self.inventory[0], 'action_key': ident})
            lib = SimpleNamespace(root=root, vault=vault, lock=threading.RLock(),
                                  items=lambda: list(records.values()), get=records.__getitem__)
            service = Projects(lib, None, SimpleNamespace(executable=lambda: 'model-adapter'))
            project = dict(id='f' * 16, path=str(root / 'project'), narrative=self.narrative, source={'revision': 'R02'})
            inventory = usage.project_catalogue(lib, project)
            sig = usage.signature(self.narrative, inventory)
            matches = dict(self.matches, references=[{**self.matches['references'][0], 'action_key': key}
                                                     for key in ('stir', 'spread')])
            result = usage.persist_matches(service, project, inventory, matches, sig)
            saved = Path(project['path']) / usage.FILE
            write_json(saved, result)
            raw = saved.read_bytes()
            project['product_reference_check'] = dict(status='done', signature=sig, model=usage.MODEL)
            service.save(project)
            copies = [Path(row['project_path']) for row in result['references'] + result['material_references']]
            self.assertEqual(len(copies), 4)
            expected = [path.read_bytes() for path in copies]
            request = dict(action='product-usage', mode='codex-cli', project_id=project['id'])

            def run():
                return service.action(request, background=False)['product_reference_check']

            with (patch('visual_asset_projects.source_project', return_value=(None, project['source'], self.narrative)),
                  patch.object(service, 'detail', side_effect=service.get),
                  patch.object(service.bridge, 'executable') as adapter,
                  patch.object(usage, 'match') as model,
                  patch.object(shutil, 'copy2', wraps=shutil.copy2) as copy_file):
                self.assertEqual(run()['status'], 'done')
                copy_file.assert_not_called()
                for missing in (1, 2):
                    copies[missing].unlink()
                    self.assertEqual(run()['status'], 'done')
                    self.assertEqual([p.read_bytes() for p in copies], expected)
                    self.assertEqual(copy_file.call_count, 1)
                    copy_file.reset_mock()
                # Lose the cached material list and just one of two project images.
                incomplete = {key: value for key, value in result.items() if key != 'material_references'}
                box = copies[2]
                original = Path(records['box']['original'])
                for cached in (incomplete, {**incomplete, 'material_references': []},
                               {**incomplete, 'material_references': result['material_references'][1:]}):
                    write_json(saved, cached)
                    box.unlink()
                    self.assertEqual(run()['status'], 'done')
                    self.assertEqual([Path(call.args[0]) for call in copy_file.call_args_list], [original])
                    self.assertEqual([p.read_bytes() for p in copies], expected)
                    self.assertEqual(saved.read_bytes(), raw)
                    copy_file.reset_mock()

                write_json(saved, incomplete)
                original.unlink()
                box.unlink()
                self.assertEqual(run()['status'], 'error')
                self.assertEqual(service.get(project['id'])['product_reference_check']['status'], 'error')
                failed = json.loads(saved.read_bytes())
                self.assertEqual(failed['status'], 'error')
                self.assertEqual({k: v for k, v in failed.items() if k not in {'status', 'message'}},
                                 {k: v for k, v in incomplete.items() if k != 'status'})
                self.assertEqual(usage.load_usage(project['path'], self.narrative)['references'], [])
                self.assertFalse(box.exists())
                copy_file.assert_not_called()
                original.write_bytes(expected[2])
                box.write_bytes(b'conflicting package')
                self.assertEqual(run()['status'], 'error')
                self.assertEqual(service.get(project['id'])['product_reference_check']['status'], 'error')
                self.assertEqual(original.read_bytes(), expected[2])
                self.assertEqual(box.read_bytes(), b'conflicting package')
                self.assertEqual(json.loads(saved.read_bytes())['status'], 'error')
                copy_file.assert_not_called()
                box.unlink()
                self.assertEqual(run()['status'], 'done')
                self.assertEqual([Path(call.args[0]) for call in copy_file.call_args_list], [original])
                self.assertEqual(saved.read_bytes(), raw)
                copy_file.reset_mock()

                copies[0].unlink()
                source = Path(records['stir']['original'])
                source.unlink()
                self.assertEqual(run()['status'], 'error')
                copy_file.assert_not_called()
                source.write_bytes(expected[0])
                self.assertEqual(run()['status'], 'done')
                self.assertEqual(copies[0].read_bytes(), expected[0])
                copies[0].write_bytes(b'changed existing copy')
                self.assertEqual(run()['status'], 'error')
                self.assertEqual(copies[0].read_bytes(), b'changed existing copy')
                self.assertEqual(json.loads(saved.read_bytes())['status'], 'error')

                # No use in the matched story and no pinned pack means no required material.
                empty = usage.persist_matches(service, project, inventory, {'references': [], 'effects': []}, sig)
                self.assertEqual(empty['material_references'], [])
                write_json(saved, empty)
                stored = service.get(project['id'])
                stored['narrative']['_product_usage'] = empty
                service.save(stored)
                empty_raw = saved.read_bytes()
                copy_file.reset_mock()
                self.assertEqual(run()['status'], 'done')
                self.assertEqual(saved.read_bytes(), empty_raw)
                write_json(saved, {key: value for key, value in empty.items() if key != 'material_references'})
                self.assertEqual(run()['status'], 'done')
                self.assertEqual(saved.read_bytes(), empty_raw)
                copy_file.assert_not_called()
                adapter.assert_not_called()
                model.assert_not_called()

    def test_story_revision_invalidates_reference_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);path = root / usage.FILE;path.parent.mkdir()
            write_json(path, {'story_hash': usage.story_hash(self.narrative), 'status': 'matched',
                              'references': [], 'effects': [], 'material_references': []})
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
        from test_visual_asset_projects import fixture

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product_id = 'creator-polishing-mask-2'
            pack = root / 'packs' / '造物者温泉酸抛光面膜'
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
                                  'aliases': ['蓝色杯子'], 'revision': 'R01', 'references': entries})
            kickoff = {'decision_ref': 'fixture', 'product': {'mode': 'insert', 'product_id': product_id,
                       'reference_pack_revision': 'R01', 'reference_pack_sha256': sha256_file(manifest)},
                       'canvas': {'platform': 'redframe', 'model': 'Seedance 2.5', 'aspect_ratio': '9:16',
                                  'resolution': '720p', 'create_new': True}}
            with patch.object(product_reference_pack, 'PACK_ROOT', root / 'packs'):
                self.assertEqual(product_reference_pack.product_catalog()[0]['product_id'], product_id)
                for scope, ids in [('display', ['BOX']), ('real_use', ['BOX', 'STIR'])]:
                    library, project, _ = fixture(root / scope, product=True)
                    service = library.projects
                    kickoff['product']['product_usage'] = scope
                    folder = Path(project['path'])
                    write_json(folder / '00_项目管理/project_request.json', {'kickoff': kickoff})
                    project = service.sync(project['id'])
                    action = dict(action='product-usage', project_id=project['id'])
                    if scope == 'real_use':
                        task = service.action({**action, 'mode': 'prepare'})
                        event = task['inputs']['story'][0]
                        result = {'references': [{'product_ast_id': 'AST-CUP', 'action_key': 'stir',
                                                   'paragraph_ids': [event['event_id']], 'evidence': event['summary']}],
                                  'effects': []}
                        project = service.action({**action, 'mode': 'apply', 'signature': task['signature'], 'result': result})
                    self.assertEqual(project['product_reference_check']['status'], 'done')
                    self.assertEqual([row['asset_id'] for row in usage.project_catalogue(None, project)], ids)
                    cache = folder / usage.FILE
                    raw = cache.read_bytes()
                    saved = json.loads(raw)
                    write_json(cache, {k: v for k, v in saved.items() if k != 'material_references'})
                    with (patch.object(service.bridge, 'executable') as adapter,
                          patch.object(usage, 'match') as model, patch.object(shutil, 'copy2') as copy_file):
                        def status():
                            return service.action({**action, 'mode': 'codex-cli'}, background=False)['product_reference_check']['status']

                        self.assertEqual(status(), 'done')
                        self.assertEqual(cache.read_bytes(), raw)
                        self.assertEqual([r['asset_id'] for r in saved['material_references']], ['BOX'])
                        if scope == 'real_use':
                            image = pack / 'box.png'
                            content = image.read_bytes()
                            image.unlink()
                            self.assertEqual(status(), 'error')
                            self.assertEqual(usage.load_usage(folder, project['narrative'])['references'], [])
                            image.write_bytes(content)
                            self.assertEqual(status(), 'done')
                            self.assertEqual(cache.read_bytes(), raw)
                        adapter.assert_not_called()
                        model.assert_not_called()
                        copy_file.assert_not_called()
                    self.assertEqual(usage.preview_path(project, ids[-1]),
                                     (pack / ('stir.mp4' if scope == 'real_use' else 'box.png')).resolve())
                    self.assertFalse((folder / '04_资产' / '项目产品参考').exists())
                    with self.assertRaises(ValueError):
                        usage.preview_path(project, 'MONTAGE')

    def test_executor_matches_real_pack_end_to_end_without_codex_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / 'project'
            manage = project / '00_项目管理'
            selected = product_reference_pack.resolve_product_pack({
                'mode': 'insert', 'product_id': 'creator-polishing-mask-2',
                'product_usage': 'real_use', 'reference_pack_revision': 'R04',
            }, for_registration=True)
            pack_path = Path(selected['manifest_path'])
            pack = pack_path.parent
            manifest = json.loads(pack_path.read_text(encoding='utf-8'))
            stir = next(ref for ref in manifest['references'] if ref['id'] == 'ACTION-06')
            story = [
                {'paragraph_id': 'STP-001', 'text': '女孩用面膜刷在碗里搅拌双剂，膏体开始起泡。'},
                {'paragraph_id': 'STP-002', 'text': '女孩把碗里的面膜倒入漏斗。'},
            ]
            common = {
                'narrative_purpose': '测试动作与参考对接', 'source_paragraph_ids': ['STP-001', 'STP-002'],
                'identity_constraints': [], 'required_states': [], 'interactions': [], 'unknowns': [],
            }
            narrative_path = project / 'narrative.json'
            write_json(narrative_path, {
                'schema_version': '2.0', 'project_id': '产品动作匹配验收', 'revision': 'R01',
                'status': 'confirmed', 'objective': '测试当前执行者对接实际产品参考',
                'story_paragraphs': story, 'dialogues': [], 'locked_facts': [],
                'change_summary': [], 'open_questions': [],
                'asset_requirements': [
                    {**common, 'asset_id': 'AST-MASK', 'category': 'REAL_PRODUCT',
                     'name': manifest['product_name'], 'reference_paths': [str(pack_path)],
                     'image_decision': '复用现有素材', 'image_reason': '已登记产品参考包'},
                    {**common, 'asset_id': 'AST-GIRL', 'category': 'CHARACTER', 'entity_id': 'CHR-GIRL',
                     'name': '女孩', 'reference_paths': [], 'image_decision': '无需独立图',
                     'image_reason': '仅核对动作参考'},
                ],
            })
            write_json(manage / 'artifact_manifest.json', {'schema_version': '1.0', 'artifacts': [
                {'artifact_id': 'narrative-r01', 'logical_id': 'narrative.package',
                 'type': 'narrative-package', 'revision': 'R01', 'status': 'current',
                 'user_approved': True, 'path_or_url': 'narrative.json',
                 'checksum': sha256_file(narrative_path)},
            ]})
            state = new_project_state(current_stage='asset-image-assistant')
            state.update(current_artifacts=['narrative-r01'], user_confirmations=[
                {'artifact_ids': ['narrative-r01'], 'at': '2026-01-01T00:00:00Z'},
            ])
            write_project_state(project, state)
            write_json(manage / 'project_request.json', {'kickoff': {
                'decision_ref': '产品动作匹配验收', 'product': {
                    'mode': 'insert', 'product_id': manifest['product_id'], 'product_usage': 'real_use',
                    'reference_pack_revision': 'R04', 'reference_pack_sha256': sha256_file(pack_path),
                },
            }})
            empty_path = root / 'empty'
            empty_path.mkdir()
            env = {**os.environ, 'PATH': str(empty_path), 'APPDATA': str(empty_path)}
            self.assertIsNone(shutil.which('codex', path=env['PATH']))
            self.assertIsNone(shutil.which('codex.exe', path=env['PATH']))
            command = [
                sys.executable, str(Path(__file__).resolve().parents[1] / 'visual_asset_library.py'),
                '--library', str(root / 'library'), '--asset-vault', str(root / 'vault'),
                'product-usage', '--project', str(project),
            ]
            original = narrative_path.read_bytes()
            prepared = subprocess.run(command, env=env, check=True, capture_output=True, encoding='utf-8')
            task = json.loads(prepared.stdout)
            self.assertEqual(task['status'], 'ready')
            self.assertEqual(task['source_revision'], 'R01')
            self.assertEqual(task['inputs']['story'], story)
            keys = {ref['action_key'] for ref in task['inputs']['available_references']}
            self.assertIn(stir['action_key'], keys)
            self.assertNotIn('使用动作顺序合辑', keys)
            result = {
                'references': [
                    {'product_ast_id': 'AST-MASK', 'action_key': key, 'paragraph_ids': [paragraph['paragraph_id']],
                     'evidence': paragraph['text']}
                    for key, paragraph in zip((stir['action_key'], '倒入漏斗'), story)
                ],
                'effects': [{'product_ast_id': 'AST-MASK', 'character_ast_id': 'AST-GIRL',
                             'paragraph_ids': ['STP-001'], 'evidence': story[0]['text'],
                             'state': '搅拌膏体', 'wardrobe_state': '', 'usage_kind': 'actual',
                             'needs_reference': False, 'reference_need': '',
                             'reference_reason': '故事只要求看清碗内搅拌动作，分动作视频足够'}],
            }
            envelope = {'signature': task['signature'], 'result': result}
            result_path = root / 'result.json'
            output = project / usage.FILE
            wrong_quote = copy.deepcopy(envelope)
            wrong_quote['result']['references'][0]['evidence'] = '凭空添加的动作原文。'
            for invalid in ({**envelope, 'signature': '0' * 64}, wrong_quote):
                with self.subTest(invalid=invalid):
                    write_json(result_path, invalid)
                    rejected = subprocess.run([*command, '--result-file', str(result_path)],
                                              env=env, capture_output=True, encoding='utf-8')
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse(output.exists())
            write_json(result_path, envelope)
            subprocess.run([*command, '--result-file', str(result_path)],
                           env=env, check=True, capture_output=True, encoding='utf-8')

            saved = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(narrative_path.read_bytes(), original)
            matched, missing = saved['references']
            self.assertEqual((matched['status'], matched['asset_id'], matched['sha256']),
                             ('matched', stir['id'], stir['sha256']))
            self.assertEqual(sha256_file(Path(matched['project_path'])), sha256_file(pack / stir['path']))
            self.assertEqual((missing['status'], missing['project_path']), ('missing', None))
            self.assertIsNone(saved['effects'][0]['generation_route'])
            self.assertIsNone(saved['effects'][0]['image_path'])
            assets = load_asset_list(project)
            self.assertEqual(assets['product_usage'], saved)
            product = next(item for item in assets['items'] if item['asset_id'] == 'AST-MASK')
            self.assertEqual(product['action_references'], saved['references'])

if __name__ == '__main__': unittest.main()
