"""Independent upstream table receiving, without model calls or production writes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_visual_asset_projects import fixture, pmod, load_project_state, write_project_state
from visual_asset_source import digest, prompt_context
import test_visual_asset_projects as project_tests


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lib, self.project, self.asset = fixture(Path(self.temp.name))
        self.root = Path(self.project['path'])
        self.manifest_path = self.root / '00_项目管理/artifact_manifest.json'
        self.manifest = pmod.read(self.manifest_path)
        self.narrative = pmod.read(self.root / 'narrative.json')
        self.script = self.root / 'script.md'
        self.script.write_text('独立编导正文，不能进入单格写词', encoding='utf-8')
        self.table = self.root / 'R01_资产表.md'
        n = self.narrative
        lines = ['---', 'document_type: asset-requirements', 'project_id: ' + n['project_id'],
                 'revision: R01', 'status: confirmed', 'source_script: script.md',
                 'structured_package: narrative.json', '---', '## 制作所需背景与范围',
                 '两件资产使用相同冷色灯光。', '## 资产索引']
        for r in n['asset_requirements']:
            lines.append('| ' + ' | '.join([r['asset_id'], r['category'], r['name'], '—', 'EVT-01']) + ' |')
        for r in n['asset_requirements']:
            lines += ['## ' + r['asset_id'] + '：' + r['name'], '- 叙事用途：' + r['narrative_purpose'],
                      '- 来源事件 ID 与必要事件简述：EVT-01 进入房间']
            for key, label in [('identity_constraints', '必须稳定的身份、外观与空间约束'),
                               ('required_states', '所需状态'), ('interactions', '交互对象与交互要求'),
                               ('reference_paths', '真实参考文件及用途'), ('unknowns', '未知项与受影响的制作内容')]:
                lines.append('- ' + label + '：' + ('；'.join(r[key]) or '无'))
        self.table.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        for ident, logical, file in [('script-r01', 'narrative.script', self.script),
                                     ('assets-r01', 'narrative.assets', self.table)]:
            self.manifest['artifacts'].append({'artifact_id': ident, 'logical_id': logical,
                'revision': 'R01', 'type': 'markdown', 'path_or_url': str(file),
                'status': 'current', 'user_approved': True, 'checksum': digest(file)})
        self.manifest['artifacts'][0]['checksum'] = digest(self.root / 'narrative.json')
        self.manifest['artifacts'][0]['depends_on'] = ['script-r01', 'assets-r01']
        self.commit_manifest()
        state = load_project_state(self.root)
        state['user_confirmations'].append({'artifact_ids': ['script-r01', 'assets-r01'], 'at': pmod.now()})
        write_project_state(self.root, state)

    def tearDown(self):
        self.temp.cleanup()

    def commit_manifest(self):
        pmod.write(self.manifest_path, self.manifest)

    def test_planning_preserves_portable_image_references(self):
        from unittest.mock import patch
        import os
        source = self.root / 'reference.png'
        source.write_bytes(Path(self.asset['original']).read_bytes())
        row = copy.deepcopy(self.project['rows'][0])
        row['requirement']['reference_paths'] = [r'C:\old-project\reference.png']
        project = copy.deepcopy(self.project)
        project['planning'] = {'status': 'running', 'run_id': 'test-reference'}
        self.lib.projects.save(project)
        captured = {}
        def boundary(exe, folder, request, rules):
            captured.update(request)
            raise ValueError('test stops before external model call')
        with patch.dict(os.environ, {'MSB_PROJECT_PATH_MAP': json.dumps({r'C:\old-project': str(self.root)})}), \
             patch.object(self.lib.projects.skill, 'prepare', return_value=('rules', {})), \
             patch.object(self.lib.projects.bridge, 'plan_assets', side_effect=boundary):
            self.lib.projects._plan_worker('not-executed', project, [row])
        self.assertEqual(captured['reference_paths'], [str(source)])

    def test_received_and_single_item_context(self):
        _, source, n = pmod.source_project(self.root)
        context = prompt_context(n, ['AST-CUP'])
        self.assertEqual([r['asset_id'] for r in context['asset_requirements']], ['AST-CUP'])
        self.assertEqual([e['event_id'] for e in context['events']], ['EVT-01'])
        self.assertNotIn('dialogues', context)
        self.assertIn('冷色灯光', context['_asset_table']['background'])
        self.assertEqual(source['logical_id'], 'narrative.package')
        self.assertEqual(context['_asset_table']['source']['sha256'], digest(self.table))

    def test_unconfirmed_table_cannot_fall_back(self):
        self.manifest['artifacts'][-1]['user_approved'] = False
        self.commit_manifest()
        with self.assertRaisesRegex(ValueError, '用户确认'):
            pmod.source_project(self.root)

    def test_frozen_candidate_requires_completed_hash_bound_adoption(self):
        package = self.root / 'narrative.json'
        narrative = pmod.read(package)
        narrative['status'] = 'candidate'
        pmod.write(package, narrative)
        self.table.write_text(self.table.read_text(encoding='utf-8').replace('status: confirmed', 'status: candidate'), encoding='utf-8')
        for entry in self.manifest['artifacts']:
            entry['checksum'] = digest(Path(entry['path_or_url']))
        self.commit_manifest()
        state = load_project_state(self.root)
        state['user_confirmations'] = []
        write_project_state(self.root, state)
        with self.assertRaisesRegex(ValueError, '用户确认'):
            pmod.source_project(self.root)
        change = {'change_id': 'test-adoption', 'status': 'completed',
                  'candidate_confirmation': {'change_id': 'test-adoption', 'approval_ref': 'explicit-user-adoption'},
                  'candidate_receipt': {'bundle': [
                      {'artifact_id': e['artifact_id'], 'path': e['path_or_url'], 'sha256': e['checksum']}
                      for e in self.manifest['artifacts']]}}
        control = self.root / '00_项目管理/change_control.json'
        pmod.write(control, {'history': [change]})
        state = load_project_state(self.root)
        state['user_confirmations'] = []
        write_project_state(self.root, state)
        _, _, received = pmod.source_project(self.root)
        self.assertEqual(received['status'], 'candidate')
        self.assertIn('_asset_table', received)
        change['status'] = 'commit_ready'
        pmod.write(control, {'history': [change]})
        with self.assertRaisesRegex(ValueError, '用户确认'):
            pmod.source_project(self.root)
        change['status'] = 'completed'
        change['candidate_receipt']['bundle'][-1]['sha256'] = 'wrong'
        pmod.write(control, {'history': [change]})
        with self.assertRaisesRegex(ValueError, '用户确认'):
            pmod.source_project(self.root)

    def test_missing_receipt_cannot_be_faked_by_frontmatter(self):
        state = load_project_state(self.root)
        state['user_confirmations'] = state['user_confirmations'][:1]
        write_project_state(self.root, state)
        with self.assertRaisesRegex(ValueError, '用户确认'):
            pmod.source_project(self.root)

    def test_unregistered_table_cannot_fall_back(self):
        self.manifest['artifacts'].pop()
        self.commit_manifest()
        with self.assertRaisesRegex(ValueError, '尚未登记'):
            pmod.source_project(self.root)

    def test_drift_and_semantic_mismatch(self):
        self.table.write_text(self.table.read_text(encoding='utf-8').replace('保留真实形状', '改变形状'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '内容变化'):
            pmod.source_project(self.root)
        self.manifest['artifacts'][-1]['checksum'] = digest(self.table)
        self.commit_manifest()
        # Once upstream re-registers the reviewed deliverable, reception does not
        # recompile Markdown fields; structured requirements remain authoritative.
        _, _, n = pmod.source_project(self.root)
        self.assertEqual(n['asset_requirements'][0]['identity_constraints'], ['保留真实形状'])

    def test_sync_retains_old_revision_history(self):
        old = copy.deepcopy(self.lib.projects.get(self.project['id']))
        result = self.lib.projects.sync(self.project['id'])
        self.assertTrue(result['changes'])
        saved = self.lib.projects.get(self.project['id'])
        self.assertEqual(len(saved['history']), len(old['rows']))
        self.assertEqual(len(saved['rows']), 2)

    def test_web_request_and_new_layout_export(self):
        self.lib.projects.sync(self.project['id'])
        project = self.lib.projects.get(self.project['id'])
        for row in project['rows']:
            row['reference_ids'] = [self.asset['id']]
        self.lib.projects.save(project)
        self.lib.projects.apply_plan(project, project['rows'], project_tests.plan_result(), {})
        rows = self.lib.projects.detail(project['id'])['rows']
        request = self.lib.projects.request(rows[1]['job'], 0)
        self.assertEqual(len(request['narrative']['events']), 1)
        self.assertEqual([r['asset_id'] for r in request['narrative']['asset_requirements']], ['AST-CUP'])
        for row in rows:
            for index in range(len(row['job']['steps'])):
                project_tests.ProjectTests.select_confirm(self, row['job'], index)
        self.base = Path(self.temp.name)
        project_tests.ProjectTests.backup(self)
        (self.root / '04_资产').mkdir(exist_ok=True)
        result = self.lib.projects.export({'project_id': project['id'], 'confirm': True})
        self.assertTrue(Path(result['manifest']).is_relative_to(self.root / '04_资产/母版'))
        receipt = pmod.read(Path(result['manifest']).parent / '用户确认记录.json')
        self.assertEqual(receipt['source_asset_table']['sha256'], digest(self.table))
        pmod.validate(pmod.read(result['manifest']), 'visual-asset-manifest.schema.json')


    def test_registered_relocation_and_anchor(self):
        from visual_asset_source import resolve_reference
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file = root / '02_script' / 'R03.md'
            file.parent.mkdir()
            file.write_text('## assets\n', encoding='utf-8')
            entry = {'artifact_id': 'A', 'path_or_url': '02_script/R03.md',
                     'status': 'current', 'user_approved': True, 'checksum': digest(file)}
            state = {'user_confirmations': [{'artifact_ids': ['A']}]}
            self.assertEqual(resolve_reference(root, 'R03.md#assets', [entry], state), str(file) + '#assets')
            with self.assertRaises(ValueError):
                resolve_reference(root, 'R03.md#missing', [entry], state)
            with self.assertRaises(ValueError):
                resolve_reference(root, 'R03.md#assets', [entry], {})
            file.write_text('## assets\nchanged', encoding='utf-8')
            with self.assertRaises(ValueError):
                resolve_reference(root, 'R03.md#assets', [entry], state)


if __name__ == '__main__':
    unittest.main()
