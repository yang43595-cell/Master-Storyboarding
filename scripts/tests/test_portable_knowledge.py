"""Small portable-runtime regression suite (also runs without pytest)."""
import hashlib
import json
import os
import subprocess
import shutil
import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.core.knowledge_runtime import (
    KnowledgeRuntimeError, resolve_knowledge_root, verify_bundle, verify_selected_file,
)
from scripts.core.knowledge_blocks import build_knowledge_index, load_policy
from scripts.core.method_documents import resolve_method_documents, MethodDocumentError
from scripts import stage_context_cli as cli

REPO = Path(__file__).resolve().parents[2]
METHOD = b'---\ntitle: Example\nversion: 1\nstatus: enabled\nlifecycle:\n  state: current\n---\nFull method.\n'


class PortableKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.root = self.repo / 'knowledge/runtime'
        self.root.mkdir(parents=True)
        self.relative = 'wiki/concepts/method.md'
        self.file = self.root / self.relative
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(METHOD)
        self.manifest = {'source': {'kind': 'test'}, 'dynamic_rule_ids': [], 'files': [
            {'path': self.relative, 'sha256': hashlib.sha256(METHOD).hexdigest(), 'role': 'method'}]}
        self.save()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def save(self):
        (self.root.parent / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_production_uses_only_the_bundle(self):
        self.assertEqual(resolve_knowledge_root(repo_root=self.repo), self.root)
        external = self.repo / 'external'
        external.mkdir()
        with patch.dict(os.environ, {'MSB_VAULT_ROOT': str(external)}):
            self.assertEqual(resolve_knowledge_root(repo_root=self.repo), self.root)
            self.assertEqual(resolve_knowledge_root(self.root, repo_root=self.repo), self.root)
            with self.assertRaisesRegex(KnowledgeRuntimeError, 'repository'):
                resolve_knowledge_root(external, repo_root=self.repo)
        with self.assertRaises(KnowledgeRuntimeError):
            resolve_knowledge_root(self.repo / 'absent', repo_root=self.repo)
        with self.assertRaises(KnowledgeRuntimeError):
            resolve_knowledge_root('', repo_root=self.repo)
        with patch.object(cli, 'ROOT', self.repo):
            self.assertEqual(cli._vault_root(None), self.root)

    def test_missing_and_drift_fail_closed(self):
        self.file.write_bytes(b'drift')
        with self.assertRaisesRegex(KnowledgeRuntimeError, 'hash mismatch'):
            verify_bundle(self.root)
        self.file.unlink()
        with self.assertRaisesRegex(KnowledgeRuntimeError, 'missing'):
            verify_bundle(self.root)
        (self.root.parent / 'manifest.json').unlink()
        with self.assertRaises(KnowledgeRuntimeError):
            build_knowledge_index(self.root, {})

    def test_escape_and_symlink_rejected(self):
        for path in ('../outside.md', '/outside.md', 'C:/outside.md', 'wiki/../bad', 'wiki\\bad'):
            with self.subTest(path=path), self.assertRaises(KnowledgeRuntimeError):
                verify_selected_file(self.root, path)
        outside = self.repo / 'outside.md'
        outside.write_bytes(METHOD)
        self.file.unlink()
        self.file.symlink_to(outside)
        with self.assertRaisesRegex(KnowledgeRuntimeError, 'escapes'):
            verify_bundle(self.root)

    def test_only_manifest_files_are_checked_and_read(self):
        extra = self.root / 'extra.md'
        extra.write_text('not a runtime dependency')
        verify_bundle(self.root)
        with self.assertRaisesRegex(KnowledgeRuntimeError, 'not selected'):
            verify_selected_file(self.root, 'extra.md')
        # Optional external assets are deliberately absent and not required.
        self.assertFalse((self.root / 'raw/videos').exists())
        self.assertEqual(resolve_knowledge_root(repo_root=self.repo), self.root)

    def test_reference_blocks_never_activate_and_exact_read_is_guarded(self):
        self.file.write_bytes(METHOD + b'<!-- msb-knowledge\n{"status":"enabled"}\n-->\nreference only\n<!-- /msb-knowledge -->')
        self.manifest['files'][0]['sha256'] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.save()
        self.assertEqual(build_knowledge_index(self.root, {}), [])
        with self.assertRaises(cli.StageContextError):
            cli._read_candidate_exact(self.root, {'id': 'MSB-KB-OLD', 'vault_relative_path': self.relative})

    def test_nonempty_allowlist_excludes_other_enabled_and_candidate_rules(self):
        def block(identifier, status):
            metadata = {'id': identifier, 'canonical_key': identifier.lower(), 'kind': 'rule',
                        'status': status, 'authority': 'wiki', 'projects': ['Master-Storyboarding'],
                        'stages': ['asset-image-assistant'], 'topics': ['image-prompt'], 'triggers': [],
                        'conflicts_with': [], 'priority': 1, 'sources': [], 'applies_when': 'test'}
            return '<!-- msb-knowledge\n' + json.dumps(metadata) + '\n-->\nBody\n^' + identifier.lower() + '\n<!-- /msb-knowledge -->\n'
        self.file.write_text(block('MSB-KB-SELECTED', 'enabled') + '\n' +
                             block('MSB-KB-OTHER', 'enabled') + '\n' + block('MSB-KB-CANDIDATE', 'candidate'))
        self.manifest['files'][0]['sha256'] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.manifest['dynamic_rule_ids'] = ['MSB-KB-SELECTED']
        self.save()
        policy = load_policy(REPO)
        self.assertEqual([b.id for b in build_knowledge_index(self.root, policy)], ['MSB-KB-SELECTED'])
        external = self.repo / 'external'
        (external / 'wiki/concepts').mkdir(parents=True)
        (external / self.relative).write_bytes(self.file.read_bytes())
        self.assertEqual({b.id for b in build_knowledge_index(external, policy)},
                         {'MSB-KB-SELECTED', 'MSB-KB-OTHER', 'MSB-KB-CANDIDATE'})

    def test_unknown_manifest_role_is_rejected(self):
        self.manifest['files'][0]['role'] = 'methdo'
        self.save()
        with self.assertRaisesRegex(KnowledgeRuntimeError, 'role/hash'):
            verify_bundle(self.root)

    def test_runtime_root_symlink_cannot_escape_bundle(self):
        external = self.repo / 'external'
        self.root.rename(external)
        self.root.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(KnowledgeRuntimeError, 'escapes'):
            verify_bundle(self.root)
        result = subprocess.run([sys.executable, '-m', 'scripts.core.knowledge_runtime',
                                 '--root', str(self.root)], cwd=REPO, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('escapes', result.stderr)

    def test_method_load_and_proposed_binding(self):
        entry = {'id': 'TEST', 'path': self.relative, 'binding_status': 'user_confirmed',
                 'applies_when': 'test', 'requires': {'skill': 'test'}}
        profile = {'professional_methods': [entry]}
        result = resolve_method_documents(self.root, profile, {'skill': 'test'})
        self.assertEqual(result.required[0].content, METHOD.decode())
        entry['binding_status'] = 'proposed'
        self.assertEqual(resolve_method_documents(self.root, profile, {'skill': 'test'}).required, [])
        entry['binding_status'] = 'user_confirmed'
        self.file.write_bytes(b'drift')
        with self.assertRaises(MethodDocumentError):
            resolve_method_documents(self.root, profile, {'skill': 'test'})

    def test_applicable_template_is_required_and_hashed(self):
        entry = {'id': 'TEST', 'path': self.relative, 'binding_status': 'user_confirmed',
                 'applies_when': 'test', 'requires': {'request.face': True}, 'template_path': 'template.md'}
        profile = {'professional_methods': [entry]}
        self.assertEqual(resolve_method_documents(self.root, profile, {'request': {}}).required, [])
        with self.assertRaisesRegex(MethodDocumentError, '模板不可用'):
            resolve_method_documents(self.root, profile, {'request': {'face': True}})
        template = self.root / 'template.md'
        template.write_text('Actual template')
        self.manifest['files'].append({'path': 'template.md', 'role': 'template',
            'sha256': hashlib.sha256(template.read_bytes()).hexdigest()})
        self.save()
        method = resolve_method_documents(self.root, profile, {'request': {'face': True}}).required[0]
        self.assertEqual(method.template_content, 'Actual template')
        self.assertEqual(method.template_checksum, self.manifest['files'][-1]['sha256'])
        template.write_text('Drift')
        with self.assertRaises(MethodDocumentError):
            resolve_method_documents(self.root, profile, {'request': {'face': True}})

    def test_real_asset_face_and_headshot_branches_are_available_from_bundle(self):
        sys.path.insert(0, str(REPO / 'scripts'))
        spec = importlib.util.spec_from_file_location('portable_asset_runtime', REPO / 'skills/asset-image-assistant/library_runtime.py')
        asset = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(asset)
        request = {'kind': '大头照', 'mode': 'asset-library'}
        _, evidence = asset.prepare(self.repo / 'ordinary-web', request)
        self.assertTrue(evidence['input_complete'])
        _, face = asset.prepare(self.repo / 'face-web', {**request, 'creator_mask_face_effect': True})
        self.assertTrue(face['input_complete'])

    def test_prepared_method_removed_from_manifest_is_rejected(self):
        shutil.rmtree(self.root.parent)
        shutil.copytree(REPO / 'knowledge', self.root.parent)
        project = self.repo / 'project'
        manage = project / '00_项目管理'
        manage.mkdir(parents=True)
        (manage / 'project_state.json').write_text(json.dumps({'current_stage': 'creative-director',
            'current_artifacts': [], 'user_confirmations': [], 'next_stage': ''}))
        request = project / 'request.json'
        request.write_text(json.dumps({'use_story_method': True}))
        args = cli.parser().parse_args(['prepare', '--project', str(project), '--skill', 'creative-director',
            '--objective', 'portable regression', '--vault', str(self.root), '--request-json', str(request)])
        with patch.object(cli, 'resolve_knowledge_root', lambda explicit=None, **_: resolve_knowledge_root(explicit, repo_root=self.repo)):
            cli.prepare(args)
            basis = json.loads(cli._task_basis_path(project).read_text())
            cli._validate_current_inputs(project, basis)
            removed = basis['standards']['professional_methods'][0]['path']
            manifest = json.loads((self.root.parent / 'manifest.json').read_text())
            manifest['files'] = [item for item in manifest['files'] if item['path'] != removed]
            (self.root.parent / 'manifest.json').write_text(json.dumps(manifest))
            self.assertTrue((self.root / removed).is_file())
            with self.assertRaisesRegex(ValueError, 'not selected'):
                cli._validate_current_inputs(project, basis)

    def test_applicable_template_is_frozen_in_actual_stage_preparation(self):
        shutil.rmtree(self.root.parent)
        shutil.copytree(REPO / 'knowledge', self.root.parent)
        profile = json.loads((REPO / 'skills/asset-image-assistant/knowledge-profile.json').read_text())
        relative = next(item['template_path'] for item in profile['professional_methods'] if item.get('template_path'))
        template = self.root / relative
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text('Synthetic face template, isolated regression only.')
        manifest = json.loads((self.root.parent / 'manifest.json').read_text())
        checksum = hashlib.sha256(template.read_bytes()).hexdigest()
        entry = next(item for item in manifest['files'] if item['path'] == relative)
        entry['sha256'] = checksum
        (self.root.parent / 'manifest.json').write_text(json.dumps(manifest))
        project = self.repo / 'template-project'
        project.mkdir()
        request = project / 'request.json'
        request.write_text(json.dumps({'creator_mask_face_effect': True}))
        args = cli.parser().parse_args(['prepare', '--project', str(project), '--skill', 'asset-image-assistant',
            '--objective', 'template regression', '--vault', str(self.root), '--request-json', str(request)])
        with patch.object(cli, 'resolve_knowledge_root', lambda explicit=None, **_: resolve_knowledge_root(explicit, repo_root=self.repo)):
            cli.prepare(args)
            basis = json.loads(cli._task_basis_path(project).read_text())
            frozen = next(item for item in basis['standards']['professional_methods'] if item.get('template_path'))
            self.assertEqual(frozen['template_sha256'], checksum)
            cli._validate_current_inputs(project, basis)
            template.write_text('Changed template')
            # Updating the bundle manifest must not refresh old preparation evidence.
            entry['sha256'] = hashlib.sha256(template.read_bytes()).hexdigest()
            (self.root.parent / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, '已改变'):
                cli._validate_current_inputs(project, basis)

    def test_real_bundle_loads_confirmed_methods_and_only_allowlisted_rules(self):
        root = resolve_knowledge_root(repo_root=REPO)
        self.assertEqual({block.id for block in build_knowledge_index(root, load_policy(REPO))},
                         set(verify_bundle(root)["dynamic_rule_ids"]))
        loaded = set()
        for path in (REPO / 'skills').glob('*/knowledge-profile.json'):
            profile = json.loads(path.read_text())
            for entry in profile.get('professional_methods', []):
                if entry['binding_status'] != 'user_confirmed':
                    continue
                context = {}
                for dotted, value in entry['requires'].items():
                    target = context
                    keys = dotted.split('.')
                    for key in keys[:-1]:
                        target = target.setdefault(key, {})
                    target[keys[-1]] = value[0] if isinstance(value, list) else value
                result = resolve_method_documents(root, {**profile, 'professional_methods': [entry]}, context)
                loaded.update(method.id for method in result.required)
        self.assertEqual(len(loaded), 12)



if __name__ == '__main__':
    unittest.main()
