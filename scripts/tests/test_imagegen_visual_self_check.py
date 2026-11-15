"""Synthetic native-call and byte-binding checks. Never invokes Codex or a provider."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import visual_asset_library as m
from test_visual_asset_projects import synthetic_vault


def observations(path, prompt, references=(), status='pass'):
    return {'status': status, 'image_path': str(path.resolve()), 'image_sha256': m.digest(path),
            'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
            'observer': 'synthetic-native-agent', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'source_files': [{'path': str(p.resolve()), 'sha256': m.digest(p)} for p in references],
            'checks': {key: {'result': status, 'observation': 'Synthetic blue candidate; orange source differs only as requested.',
                             'locator': 'Full synthetic frame, upper-left and center.'}
                       for key in m.codex_bridge.IMAGE_CHECKS}, 'limitations': ['Synthetic observations only; no production visual acceptance.']}


class NativeVisualCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'codex-home'
        (self.home / 'generated_images').mkdir(parents=True)
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reference = self.root / 'reference.png'
        Image.new('RGB', (160, 120), 'orange').save(self.reference)
        second = self.root / 'second-reference.png'
        Image.new('RGB', (160, 120), 'grey').save(second)
        self.references = [self.reference, second]
        self.output = self.home / 'generated_images/candidate.png'
        Image.new('RGB', (160, 120), 'blue').save(self.output)
        self.request = {'reference_paths': [str(p) for p in self.references], 'prompt': '合成测试：蓝色方块；保持原字词。', 'ratio': '4:3'}

    def generate(self, folder, evidence=None):
        evidence = observations(self.output, self.request['prompt'], self.references) if evidence is None else evidence
        def native(command, **kwargs):
            result_path = Path(command[command.index('--output-last-message') + 1])
            result_path.write_text(json.dumps({'status': 'generated', 'output_path': str(self.output),
                                              'message': '', 'visual_self_check': evidence}), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(m.codex_bridge.subprocess, 'run', side_effect=native) as call:
            result = m.codex_bridge.generate_image('synthetic-codex', folder, self.request)
        return result, call

    def test_native_call_preserves_prompt_and_returns_bound_observations(self):
        result, call = self.generate(self.root / 'native')
        call.assert_called_once()
        self.assertIn('--ephemeral', call.call_args.args[0])
        task = call.call_args.kwargs['input']
        submitted = json.loads(task[task.index('{'):])
        self.assertEqual(submitted['prompt'], self.request['prompt'])
        self.assertEqual(submitted['source_references'][0]['sha256'], m.digest(self.reference))
        self.assertIn('生成后必须通过view_image', task)
        self.assertEqual(result['output_path'], str(self.output.resolve()))
        self.assertEqual(result['visual_self_check']['status'], 'pass')
        self.assertEqual(result['visual_self_check']['stored_image_sha256'], m.digest(self.output))

    def test_failed_unknown_and_incomplete_observations_cannot_pass(self):
        for status in ('fail', 'unknown'):
            with self.subTest(status=status):
                evidence = observations(self.output, self.request['prompt'], self.references, status)
                evidence['status'] = 'pass'  # A top-level assertion cannot erase actual failing checks.
                result, _ = self.generate(self.root / status, evidence)
                self.assertEqual(result['visual_self_check']['status'], status)
        result, _ = self.generate(self.root / 'notes-only', {'passed': True, 'notes': 'Looks fine'})
        self.assertEqual(result['visual_self_check']['status'], 'unknown')
        self.assertTrue(result['visual_self_check']['binding_issues'])

    def test_omitting_one_compared_reference_keeps_result_unknown(self):
        evidence = observations(self.output, self.request['prompt'], [self.reference])
        result, _ = self.generate(self.root / 'missing-reference', evidence)
        self.assertEqual(result['visual_self_check']['status'], 'unknown')
        self.assertTrue(any('second-reference.png' in issue for issue in result['visual_self_check']['binding_issues']))

    def test_changed_output_reference_or_input_invalidates_old_pass(self):
        folder = self.root / 'changed'
        self.generate(folder)
        Image.new('RGB', (160, 120), 'red').save(self.output)
        result = m.codex_bridge.generated_image_result(folder, self.request, time.time() - 60)
        self.assertEqual(result['visual_self_check']['status'], 'unknown')
        self.assertTrue(any('SHA256' in reason for reason in result['visual_self_check']['binding_issues']))
        # Restore output, mutate an actual compared reference instead.
        Image.new('RGB', (160, 120), 'blue').save(self.output)
        Image.new('RGB', (160, 120), 'green').save(self.reference)
        result = m.codex_bridge.generated_image_result(folder, self.request, time.time() - 60)
        self.assertEqual(result['visual_self_check']['status'], 'unknown')
        self.assertTrue(any('真实参考' in reason for reason in result['visual_self_check']['binding_issues']))
        changed = {**self.request, 'prompt': 'Different approved prompt'}
        result = m.codex_bridge.generated_image_result(folder, changed, time.time() - 60)
        self.assertEqual(result['visual_self_check']['status'], 'unknown')

    def test_legacy_result_is_unknown_and_generation_failure_is_not_a_candidate(self):
        folder = self.root / 'legacy'
        folder.mkdir()
        receipt = folder / 'image-result.json'
        receipt.write_text(json.dumps({'status': 'generated', 'output_path': str(self.output), 'message': ''}), encoding='utf-8')
        with patch.object(m.codex_bridge.subprocess, 'run') as call:
            result = m.codex_bridge.generated_image_result(folder, self.request, time.time() - 60)
        call.assert_not_called()
        self.assertEqual(result['visual_self_check']['status'], 'unknown')
        receipt.write_text(json.dumps({'status': 'failed', 'output_path': '', 'message': 'Synthetic generator failed'}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Synthetic generator failed'):
            m.codex_bridge.generated_image_result(folder, self.request, time.time() - 60)


class ImportedVisualCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.object(m.asset_skill, 'resolve_knowledge_root',
                                   return_value=synthetic_vault(self.root / 'synthetic-vault'))
        environment.start()
        self.addCleanup(environment.stop)
        self.lib = m.Library(self.root / 'library', vault=self.root / 'vault')
        source = self.root / 'source.png'
        Image.new('RGB', (160, 120), 'orange').save(source)
        self.source, _ = self.lib.import_file(source, role='参考', category='场景')

    def pending(self, name, kind='服装'):
        job = self.lib.create_job({'source_id': self.source['id'], 'subject': name, 'kind': kind})
        request = self.lib.step_request(job, 0)
        job['steps'][0].update(status='generating', generation_run_id=name,
                              authorization={'at': m.now(), 'request': request})
        self.lib.save(job, 'jobs')
        output = self.root / (name + '.png')
        Image.new('RGB', (160, 90), 'blue').save(output)
        return job, request, output

    def test_worker_persists_pass_fail_unknown_without_human_confirmation(self):
        for status in ('pass', 'fail', 'unknown'):
            with self.subTest(status=status):
                job, request, output = self.pending(status)
                evidence = observations(output, request['prompt'], status=status)
                with patch.object(m.codex_bridge, 'generate_image', return_value={
                        'output_path': str(output), 'visual_self_check': evidence}) as generate:
                    self.lib._imagegen_worker('synthetic', job['id'], 0, status, request)
                generate.assert_called_once()
                step = self.lib.get(job['id'], 'jobs')['steps'][0]
                asset = self.lib.get(step['outputs'][0])
                self.assertEqual(step['status'], 'awaiting_review')
                self.assertEqual(step['visual_self_check']['status'], status)
                self.assertEqual(asset['provenance']['visual_self_check'], step['visual_self_check'])
                self.assertEqual(asset['provenance']['visual_self_check']['stored_image_sha256'], m.digest(Path(asset['original'])))
                self.assertEqual(step['generation_history'][-1]['visual_self_check']['status'], status)
                self.assertEqual(asset['status'], 'unreviewed')
                self.assertIsNone(asset['confirmation'])
                self.assertNotIn('project_confirmation', step)
                if status != 'pass':
                    self.assertIn('候选', step['message'])
                    self.assertIn('Synthetic blue candidate', step['message'])

    def test_legacy_path_mock_and_post_generation_changes_stay_unknown(self):
        for name, legacy in (('legacy-path', True), ('changed-bytes', False)):
            job, request, output = self.pending(name)
            evidence = observations(output, request['prompt'])
            Image.new('RGB', (160, 90), 'green').save(output)
            result = str(output) if legacy else {'output_path': str(output), 'visual_self_check': evidence}
            with patch.object(m.codex_bridge, 'generate_image', return_value=result):
                self.lib._imagegen_worker('synthetic', job['id'], 0, name, request)
            step = self.lib.get(job['id'], 'jobs')['steps'][0]
            self.assertEqual(step['visual_self_check']['status'], 'unknown')
            self.assertTrue(step['visual_self_check']['binding_issues'])

    def test_byte_change_during_copy_rejects_binding_and_keeps_unknown_candidate(self):
        job, request, output = self.pending('copy-change')
        evidence = observations(output, request['prompt'])
        original_import = self.lib.import_file
        def changed_import(*args, **kwargs):
            item, created = original_import(*args, **kwargs)
            Image.new('RGB', (160, 90), 'red').save(item['original'])
            return item, created
        with patch.object(self.lib, 'import_file', side_effect=changed_import), \
             patch.object(m.codex_bridge, 'generate_image', return_value={
                 'output_path': str(output), 'visual_self_check': evidence}) as generate:
            self.lib._imagegen_worker('synthetic', job['id'], 0, 'copy-change', request)
        generate.assert_called_once()
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'unknown')
        self.assertEqual(step['outputs'], [])
        candidate = next(a for a in self.lib.items() if a['provenance'].get('job_id') == job['id'])
        self.assertEqual(candidate['provenance']['visual_self_check']['status'], 'unknown')

    def test_upload_normalization_does_not_approve_unseen_cropped_bytes(self):
        job, request, output = self.pending('crop-change', kind='大头照')
        job['upload_source_id'] = self.source['id']
        self.lib.save(job, 'jobs')
        evidence = observations(output, request['prompt'])
        with patch.object(m.codex_bridge, 'generate_image', return_value={
                'output_path': str(output), 'visual_self_check': evidence}):
            self.lib._imagegen_worker('synthetic', job['id'], 0, 'crop-change', request)
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'awaiting_review')
        self.assertEqual(step['visual_self_check']['status'], 'unknown')
        self.assertNotEqual(step['visual_self_check']['stored_image_sha256'], evidence['image_sha256'])
        self.assertEqual(self.lib.get(step['outputs'][0])['status'], 'unreviewed')

    def test_recovery_consumes_same_bound_evidence_without_generation(self):
        job, request, output = self.pending('recover')
        home = self.root / 'codex-home'
        generated = home / 'generated_images/output.png'
        generated.parent.mkdir(parents=True)
        generated.write_bytes(output.read_bytes())
        step = job['steps'][0]
        step['status'] = 'unknown'
        self.lib.save(job, 'jobs')
        folder = self.lib.root / 'requests' / job['id'] / '0/imagegen/recover'
        folder.mkdir(parents=True)
        references = [Path(p) for p in request['reference_paths']]
        evidence = observations(generated, request['prompt'], references)
        (folder / 'image-request.json').write_text(json.dumps({
            'prompt_sha256': evidence['prompt_sha256'], 'references': evidence['source_files']}), encoding='utf-8')
        (folder / 'image-result.json').write_text(json.dumps({
            'status': 'generated', 'output_path': str(generated), 'visual_self_check': evidence, 'message': ''}), encoding='utf-8')
        with patch.dict(os.environ, {'CODEX_HOME': str(home)}), patch.object(m.codex_bridge, 'generate_image') as generate:
            self.lib.recover_imagegen({'id': job['id'], 'index': 0})
        generate.assert_not_called()
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['visual_self_check']['status'], 'pass')
        self.assertTrue(step['generation_history'][-1]['recovered'])
        self.assertIn('没有重复生图', step['message'])
