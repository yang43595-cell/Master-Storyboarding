"""No Blender/network: real boundary functions, synthetic fixture and injected I/O failures.

Standalone stdlib regression replay; reuses only upstream fixture setup, not pytest.
"""
import ast
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.blender_previs import project as p
from scripts.blender_previs.output_contract import reference_name, shot_still_times
from scripts import blender_previs_cli as cli
from scripts.core import artifacts, revision_control as rc
from scripts.core.io import read_json, write_json
from scripts.core.project_state import new_project_state
from PIL import Image

# Extract only fixture function, without importing or pretending to run pytest.
tree = ast.parse((Path(__file__).with_name('test_blender_previs_project.py')).read_text())
fixture = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'project')
fixture.decorator_list = []
exec(compile(ast.Module(body=[fixture], type_ignores=[]), 'upstream_synthetic_fixture', 'exec'))

class Patches(contextlib.ExitStack):
    def setattr(self, obj, name, value):
        self.enter_context(patch.object(obj, name, value))

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.patches = self.enterContext(Patches())
        self.root, self.binding, self.spec, self.base, _ = project(Path(self.temp), self.patches)

    def candidate(self, revision=None):
        binding = self.binding if revision is None else p.prepare_shot(self.root, 'S03-150', revision, self.base['artifact_id'])
        return p.register_reference(self.root, binding, self.spec)

    def confirm(self, entry):
        return p.confirm_reference(self.root, entry['artifact_id'], 'SYNTHETIC', 'SYNTHETIC not real approval')

    def cloud(self, ids=('S03-150',)):
        spec = read_json(self.spec)
        scene = Path(self.temp) / 'cloud.blend'; scene.write_bytes(b'SYNTHETIC_NOT_BLENDER')
        image = Path(spec['files'][0]['path'])
        manifest = Path(self.temp) / 'shoot.json'
        write_json(manifest, {'scene_sha256': p.sha256_file(scene), 'shots': [
            {'id': ident, 'stills': [{'sha256': p.sha256_file(image)}]} for ident in ids]})
        evidence = Path(spec['technical_evidence'])
        write_json(evidence, {'ok': True, 'blender_version': '4.3.2', 'scene_sha256': p.sha256_file(scene),
                              'render_manifest_sha256': p.sha256_file(manifest)})
        spec.update(runtime_location='cloud', render_backend='cloud Blender 4.3.2 synthetic')
        spec['files'] += [{'role': 'shot_scene', 'path': str(scene)}, {'role': 'render_manifest', 'path': str(manifest)}]
        write_json(self.spec, spec)
        return evidence, manifest

    def test_exact_cloud_shot_and_reread(self):
        self.cloud()
        entry = self.candidate(); self.confirm(entry)
        self.assertEqual(p.confirmed_reference_data(self.root, entry['artifact_id'])[1]['shot_id'], 'S03-150')

    def test_wrong_and_duplicate_cloud_shot_rejected(self):
        for ids in [('OTHER',), ('S03-150', 'S03-150')]:
            self.cloud(ids)
            with self.assertRaisesRegex(p.ProjectPrevisError, '准确镜号'):
                self.candidate()
            self.assertFalse((self.root / p.PREVIS / '镜头/S03-150_R02').exists())
            # reset the synthetic file list before next case
            spec = read_json(self.spec); spec['files'] = spec['files'][:1]; write_json(self.spec, spec)

    def test_correct_hash_wrong_shot_main_image_rejected(self):
        evidence, manifest = self.cloud()
        data = read_json(manifest)
        correct_hash = data['shots'][0]['stills'][0]['sha256']
        data['shots'][0]['stills'][0]['sha256'] = '0' * 64
        data['shots'].append({'id': 'OTHER', 'stills': [{'sha256': correct_hash}]})
        write_json(manifest, data)
        ev = read_json(evidence); ev['render_manifest_sha256'] = p.sha256_file(manifest); write_json(evidence, ev)
        with self.assertRaisesRegex(p.ProjectPrevisError, '主图'):
            self.candidate()

    def test_reread_rejects_wrong_shot_even_with_updated_outer_checksums(self):
        self.cloud(); entry = self.candidate()
        path = p.project_file(self.root, entry['path_or_url']); data = read_json(path)
        m = next(f for f in data['files'] if f['role'] == 'render_manifest')
        mp = p.project_file(self.root, m['path']); manifest = read_json(mp); manifest['shots'][0]['id'] = 'OTHER'; write_json(mp, manifest)
        m['sha256'] = p.sha256_file(mp)
        ev = next(f for f in data['files'] if f['role'] == 'technical_evidence')
        ep = p.project_file(self.root, ev['path']); e = read_json(ep); e['render_manifest_sha256'] = m['sha256']; write_json(ep, e); ev['sha256'] = p.sha256_file(ep)
        write_json(path, data)
        manifest = artifacts.load_manifest(self.root)
        next(a for a in manifest['artifacts'] if a['artifact_id'] == entry['artifact_id'])['checksum'] = p.sha256_file(path)
        artifacts.save_manifest(self.root, manifest)
        with self.assertRaisesRegex(p.ProjectPrevisError, '准确镜号'):
            p.reference_data(self.root, entry['artifact_id'])

    def test_first_confirm_retry_and_conflict(self):
        entry = self.candidate(); receipt = self.confirm(entry)
        before = {f: f.read_bytes() for f in (self.root / '00_项目管理').glob('*.json')}
        self.assertEqual(receipt, self.confirm(entry))
        self.assertEqual(before, {f: f.read_bytes() for f in before})
        with self.assertRaisesRegex(p.ProjectPrevisError, '不可重写'):
            p.confirm_reference(self.root, entry['artifact_id'], 'OTHER', 'OTHER')
        self.assertEqual(read_json(self.root / '00_项目管理/project_state.json')['custom_user_field'], {'preserve': True})

    def test_existing_exact_legacy_receipt_recovers_without_deletion(self):
        entry = self.candidate()
        # Reproduce the old implementation's receipt-first interruption.
        def old_interruption(root, updates, **kwargs):
            receipt_path = next(path for path in updates if path.name == 'user-confirmation.json')
            write_json(receipt_path, updates[receipt_path])
            raise OSError('synthetic legacy interruption')
        with patch.object(rc, '_commit_json_transaction_unlocked', side_effect=old_interruption):
            with self.assertRaises(OSError): self.confirm(entry)
        receipt_path = p.project_file(self.root, entry['path_or_url']).parent / 'user-confirmation.json'
        before = receipt_path.read_bytes()
        self.confirm(entry)
        self.assertEqual(before, receipt_path.read_bytes())
        p.confirmed_reference_data(self.root, entry['artifact_id'])

    def test_preflight_bad_source_preserves_old_current(self):
        old = self.candidate(); self.confirm(old); new = self.candidate('R03')
        source = p.project_file(self.root, read_json(self.binding)['source_storyboard']['path'])
        source.write_text('{}')
        with self.assertRaises(p.ProjectPrevisError): self.confirm(new)
        self.assertEqual(p.entry_by_id(self.root, old['artifact_id'])['status'], 'current')
        self.assertFalse((p.project_file(self.root, new['path_or_url']).parent / 'user-confirmation.json').exists())

    def test_confirmation_preserves_effective_legacy_selection(self):
        variants = [('legacy-1', 'active_artifact_ids', False),
                    ('legacy-1', 'active_artifact_ids', True),
                    ('legacy-1', 'current_artifacts', True),
                    ('stage-1.0', 'current_artifacts', True)]
        for schema, field, objects in variants:
            with self.subTest(schema=schema, field=field, objects=objects), tempfile.TemporaryDirectory() as folder, Patches() as patches:
                root, binding, spec, _, _ = project(Path(folder), patches)
                entry = p.register_reference(root, binding, spec)
                state_path = root / '00_项目管理/project_state.json'
                raw = read_json(state_path)
                expected = p.load_project_state(root)['current_artifacts']
                raw.pop('current_artifacts')
                raw['schema_version'] = schema
                raw[field] = [{'artifact_id': ident, 'legacy_note': 'preserve raw alias'} for ident in expected] if objects else list(expected)
                raw['custom_user_field'] = {'preserve': True, 'nested': ['unchanged']}
                if field == 'current_artifacts':
                    raw['active_artifact_ids'] = ['OLD-ALIAS-NOT-SELECTED']
                write_json(state_path, raw)
                self.assertEqual(p.load_project_state(root)['current_artifacts'], expected)
                receipt = p.confirm_reference(root, entry['artifact_id'], 'SYNTHETIC', 'legacy selection test')
                after = read_json(state_path)
                self.assertEqual(after['current_artifacts'], expected + [entry['artifact_id']])
                self.assertEqual(after['custom_user_field'], raw['custom_user_field'])
                self.assertEqual(after['schema_version'], schema)
                if 'active_artifact_ids' in raw:
                    self.assertEqual(after['active_artifact_ids'], raw['active_artifact_ids'])
                self.assertEqual(receipt, p.confirm_reference(root, entry['artifact_id'], 'SYNTHETIC', 'legacy selection test'))
                p.confirmed_reference_data(root, entry['artifact_id'])

    def test_frozen_replacement_preserves_old_current(self):
        old = self.candidate(); self.confirm(old); new = self.candidate('R03')
        rc.open_change(self.root, change_id='TEST', tier='T1', logical_ids=[new['logical_id']], reason='synthetic')
        with self.assertRaisesRegex(ValueError, '冻结'):
            self.confirm(new)
        self.assertEqual(p.entry_by_id(self.root, old['artifact_id'])['status'], 'current')
        self.assertFalse((p.project_file(self.root, new['path_or_url']).parent / 'user-confirmation.json').exists())

    def test_before_intent_failure_preserves_old_current_and_retries(self):
        old = self.candidate(); self.confirm(old); new = self.candidate('R03')
        before = (self.root / artifacts.MANIFEST_PATH).read_bytes()
        real_write = rc.write_json
        def fault(path, data):
            if path.name == 'commit_intent.json': raise OSError('synthetic before intent')
            return real_write(path, data)
        with patch.object(rc, 'write_json', side_effect=fault):
            with self.assertRaisesRegex(OSError, 'before intent'): self.confirm(new)
        self.assertEqual(before, (self.root / artifacts.MANIFEST_PATH).read_bytes())
        self.assertEqual(p.entry_by_id(self.root, old['artifact_id'])['status'], 'current')
        self.assertFalse((p.project_file(self.root, new['path_or_url']).parent / 'user-confirmation.json').exists())
        self.confirm(new)
        self.assertEqual(p.entry_by_id(self.root, old['artifact_id'])['status'], 'superseded')

    def test_every_committed_write_failure_rolls_forward_same_receipt(self):
        # Each fresh revision fails at one real transaction replacement, then retries.
        for fail_after in range(1, 5):
            entry = self.candidate('R' + str(10 + fail_after))
            real_commit = rc._commit_json_transaction_unlocked
            def fault(*args, **kwargs):
                return real_commit(*args, **kwargs, fail_after=fail_after)
            with patch.object(rc, '_commit_json_transaction_unlocked', side_effect=fault):
                with self.assertRaisesRegex(RuntimeError, 'fault injection'): self.confirm(entry)
            receipt = self.confirm(entry)
            self.assertEqual(receipt, self.confirm(entry))
            self.assertEqual(read_json(self.root / '00_项目管理/project_state.json')['user_confirmations'].count(receipt), 1)
            p.confirmed_reference_data(self.root, entry['artifact_id'])

    def test_replacement_invalidates_only_dependent_selection(self):
        old = self.candidate(); old_receipt = self.confirm(old)
        for ident, deps in [('DEPENDENT', [old['logical_id']]), ('UNRELATED', [])]:
            path = self.root / (ident + '.txt'); path.write_text('synthetic existing artifact')
            artifacts.register_workspace_artifact(self.root, dict(artifact_id=ident, logical_id=ident,
                type='test', path_or_url=path.name, status='current', revision='R01', depends_on=deps))
            p.save_state_additions(self.root, ident)
        new = self.candidate('R03'); self.confirm(new)
        self.assertEqual(p.entry_by_id(self.root, 'DEPENDENT')['status'], 'stale')
        self.assertEqual(p.entry_by_id(self.root, 'UNRELATED')['status'], 'current')
        state = read_json(self.root / '00_项目管理/project_state.json')
        self.assertNotIn('DEPENDENT', state['current_artifacts'])
        self.assertNotIn(old['artifact_id'], state['current_artifacts'])
        self.assertIn('UNRELATED', state['current_artifacts'])
        self.assertIn(old_receipt, state['user_confirmations'])

    def test_still_names_do_not_imply_start_or_extra_render(self):
        shot = {'id': 'S01', 'start': 0, 'end': 4}
        self.assertEqual(shot_still_times(shot), [0.5])
        self.assertEqual(reference_name(shot, 0, 0.5), 'REF-S01_T00.50.png')
        self.assertEqual(reference_name(shot, 0, 2), 'REF-S01_T02.00.png')
        self.assertEqual(reference_name(shot, 1, 0), 'REF-S01_START.png')

    def test_generated_asset_and_unknown_no_network(self):
        assets = read_json(ROOT / 'assets/blender-previs/asset-registry.json')['assets']
        with patch.object(cli, 'registry', return_value=(Path(self.temp) / 'cache', assets)), patch.object(cli.urllib.request, 'urlopen', side_effect=AssertionError('NETWORK FORBIDDEN')) as net:
            with self.assertRaisesRegex(cli.PrevisError, 'bootstrap-human'): cli.download_assets({'previs.human.neutral'})
            with self.assertRaisesRegex(cli.PrevisError, '未知资产'): cli.download_assets({'missing'})
            net.assert_not_called()

if __name__ == '__main__':
    unittest.main(verbosity=2)
