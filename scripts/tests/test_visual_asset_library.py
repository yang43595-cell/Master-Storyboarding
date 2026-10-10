"""Direct checks for preservation, reuse, generation gates and local HTTP access."""
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / 'visual_asset_library.py'
spec = importlib.util.spec_from_file_location('visual_asset_library', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LibraryTests(unittest.TestCase):
    def test_file_upload_keeps_inputs_out_of_collection_and_normalizes_generated_output(self):
        source = self.root / 'input-portrait.png'
        Image.new('RGB', (401, 399), 'red').save(source)
        original = source.read_bytes()
        recognition = {'category': '大头照', 'confidence': '高', 'description': '穿粉白格纹上衣的人物',
                       'asset_name': '人物面部', 'has_multiple_subjects': False}
        with patch.object(m.visual_asset_upload, 'pick_local_files', return_value=[str(source)]), \
             patch.object(m.visual_asset_upload, 'recognize', return_value=recognition), \
             patch.object(self.lib, 'audit_prompt') as audit:
            result = self.lib.pick_files({'multiple': True})['results'][0]
            again = self.lib.pick_files({'multiple': True})['results'][0]
        self.assertEqual(result['upload_processing']['status'], 'prepared')
        self.assertNotIn('上衣', self.lib.get(result['job_id'], 'jobs')['must_inherit'])
        self.assertIn('面部身份', self.lib.get(result['job_id'], 'jobs')['must_inherit'])
        self.assertEqual(again['job_id'], result['job_id'])
        audit.assert_called_once()
        record = self.lib.get(result['id'])
        self.assertTrue(record['reference_only'])
        self.assertEqual(Path(record['original']).read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        index = json.loads((self.lib.vault / '资产库/资产索引.json').read_text(encoding='utf-8'))
        self.assertNotIn(record['id'], [r['library_asset_id'] for r in index['assets']])
        with self.assertRaises(ValueError):
            self.lib.confirm(record['id'])
        with self.assertRaises(ValueError):
            self.lib.export([record['id']])
        with self.assertRaises(ValueError):
            self.lib.submit_imagegen({'id': result['job_id'], 'index': 0, 'authorize': True})
        output = self.root / 'generated.png'
        Image.new('RGB', (401, 399), 'blue').save(output)
        generated_bytes = output.read_bytes()
        job = self.lib.attach(result['job_id'], 0, [output])
        candidate = self.lib.get(job['steps'][0]['outputs'][0])
        self.assertFalse(candidate['reference_only'])
        self.assertEqual(candidate['width'] * 4, candidate['height'] * 3)
        self.assertEqual(candidate['status'], 'unreviewed')
        self.assertEqual(candidate['provenance']['upload_source_id'], record['id'])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(output.read_bytes(), generated_bytes)

    def test_upload_ambiguity_and_recognition_failure_preserve_retryable_input(self):
        source = self.root / 'multiple-people.png'
        Image.new('RGB', (400, 400), 'orange').save(source)
        recognition = {'category': '大头照', 'confidence': '高', 'description': '两个人物',
                       'has_multiple_subjects': True}
        with patch.object(m.visual_asset_upload, 'pick_local_files', return_value=[str(source)]), \
             patch.object(m.visual_asset_upload, 'recognize', return_value=recognition), \
             patch.object(self.lib, 'audit_prompt') as audit:
            result = self.lib.pick_files({})['results'][0]
            self.assertEqual(result['upload_processing']['status'], 'needs_choice')
            self.assertEqual(self.lib.items('jobs'), [])
            audit.assert_not_called()
            chosen = self.lib.prepare_upload({'source_id': result['id'], 'kind': '服装', 'subject': '左侧外套'})
            self.assertEqual(self.lib.get(chosen['job_id'], 'jobs')['kind'], '服装')
            audit.side_effect = ValueError('准备队列已满')
            retryable = self.lib.prepare_upload({'source_id': result['id'], 'kind': '大头照', 'subject': '右侧人物'})
            self.assertEqual(self.lib.get(retryable['job_id'], 'jobs')['steps'][0]['audit']['status'], 'error')
        with patch.object(m.visual_asset_upload, 'pick_local_files', return_value=[str(source)]), \
             patch.object(m.visual_asset_upload, 'recognize', side_effect=ValueError('看图失败')):
            failed = self.lib.pick_files({})['results'][0]
        self.assertEqual(failed['id'], result['id'])
        self.assertEqual(failed['upload_processing']['status'], 'error')
        self.assertTrue(source.is_file())

    def test_mixed_file_upload_samples_video_and_serves_input_previews(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('ffmpeg/ffprobe unavailable')
        video = self.root / 'uploaded.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x72:r=5',
                        '-t', '1', '-pix_fmt', 'yuv420p', str(video)], check=True)
        picture = self.root / 'uploaded.png'
        Image.new('RGB', (80, 60), 'orange').save(picture)
        original = video.read_bytes()
        recognition = {'category': '服装三视图', 'confidence': '高', 'description': '同一件外套',
                       'asset_name': '外套', 'has_multiple_subjects': False}
        with patch.object(m.visual_asset_upload, 'pick_local_files', return_value=[str(picture), str(video)]), \
             patch.object(m.visual_asset_upload, 'recognize', return_value=recognition) as recognize, \
             patch.object(self.lib, 'audit_prompt'):
            results = self.lib.pick_files({'multiple': True})['results']
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['upload_processing']['status'] == 'prepared' for r in results), results)
        self.assertEqual(len(recognize.call_args_list[1].args[0]), 3)
        request = self.lib.prepare(results[1]['job_id'], 0)
        self.assertEqual(len(request['reference_paths']), 3)
        self.assertTrue(all(Path(p).suffix == '.png' for p in request['reference_paths']))
        self.assertEqual(video.read_bytes(), original)
        server = m.ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        server.library, server.token = self.lib, 'test-token'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/api/state') as response:
                state = json.load(response)
            self.assertEqual(len(state['assets']), 1)
            self.assertEqual(len(state['references']), 5)
            with urlopen(base + '/media/' + results[1]['id'] + '/thumbnail') as response:
                self.assertEqual(response.status, 200)
            frame = self.lib.get(results[1]['id'])['frame_ids'][0]
            with urlopen(base + '/media/' + frame + '/original') as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_rejected_local_upload_preserves_source(self):
        source = self.root / 'oversized-canvas.png'
        Image.new('RGB', (400, 400), 'red').save(source)
        original = source.read_bytes()
        recognition = {'category': '大头照', 'confidence': '高'}
        with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition), \
             patch.object(m.visual_asset_upload, 'crop_to_category', return_value={
                 'status': 'skipped', 'reason': '画布过大'}):
            result = self.lib.upload_local_file(source)
        self.assertEqual(result['upload_processing']['status'], 'rejected')
        self.assertFalse(result['upload_processing']['source_deleted'])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(self.lib.items()), 1)

    def test_duplicate_wrong_category_preserves_source_without_recognition(self):
        source = self.root / 'wrong-category-copy.png'
        source.write_bytes(self.original_bytes)
        category = '场景' if self.asset['category'] != '场景' else '物品'
        with patch.object(m.visual_asset_upload, 'recognize') as recognize:
            result = self.lib.upload_local_file(source, category=category)
        recognize.assert_not_called()
        self.assertEqual(result['upload_processing']['status'], 'error')
        self.assertFalse(result['upload_processing']['source_deleted'])
        self.assertEqual(source.read_bytes(), self.original_bytes)

    def test_manual_product_category_survives_read_and_web_view_preserves_evidence(self):
        asset = self.lib.get(self.asset['id'])
        asset.update(name='手镜', category='产品')
        self.lib.save(asset)
        self.assertEqual(self.lib.get(asset['id'])['category'], '产品')
        record = {'authorization': {'at': 'time', 'request': {'prompt': 'large snapshot'}},
                  'audit': {'status': 'passed', 'checks': ['checked']}, 'project_confirmation': {'id': 'receipt'}}
        view = m.web_view(record)
        self.assertNotIn('request', view['authorization'])
        self.assertEqual(view['audit'], record['audit'])
        self.assertEqual(view['project_confirmation'], record['project_confirmation'])
        self.assertIn('request', record['authorization'])

    def test_missing_original_isolated_at_startup_and_healthy_import(self):
        self.lib.confirm(self.asset['id'])
        m.visual_asset_backup.run(self.lib, m.management)
        original = Path(self.lib.get(self.asset['id'])['original'])
        contents = original.read_bytes()
        original.unlink()
        library = m.Library(self.lib.root, vault=self.lib.vault)
        missing = library.get(self.asset['id'])
        self.assertEqual(missing['status'], 'confirmed')
        self.assertIn('file_error', missing)
        with self.assertRaisesRegex(ValueError, '原图缺失'):
            library.confirm(missing['id'])
        with self.assertRaisesRegex(ValueError, '原图缺失'):
            library.export([missing['id']])
        source = self.root / 'healthy.png'
        Image.new('RGB', (320, 180), 'blue').save(source)
        asset, added = library.import_file(source, category='场景')
        self.assertTrue(added)
        self.assertEqual(asset['backup']['status'], 'saved')
        self.assertTrue(Path(asset['original']).is_file())
        original.write_bytes(contents)
        self.assertNotIn('file_error', library.get(missing['id']))

    def test_external_import_preserves_origin_and_never_creates_an_approval(self):
        source = self.root / 'external-candidate.png'
        reference = self.root / 'external-reference.png'
        Image.new('RGB', (160, 90), 'blue').save(source)
        Image.new('RGB', (160, 90), 'grey').save(reference)
        record = {'tool': 'external image tool', 'model': 'host-managed, not disclosed',
                  'user_authorization_ref': 'test-only authorization', 'prompt': '保留同一空间',
                  'reference_paths': [str(reference)], 'image_sha256': m.digest(source)}
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.lib.import_tree(source, external_generation={**record, 'image_sha256': '0' * 64})
        result = self.lib.import_tree(source, external_generation=record)
        self.assertEqual(result['added'], 1)
        item = next(a for a in self.lib.items() if a['source'] == str(source))
        self.assertEqual(item['status'], 'unreviewed')
        self.assertIsNone(item['confirmation'])
        self.assertEqual(item['provenance']['external_generation']['tool'], record['tool'])
        self.assertEqual(item['provenance']['reference_images'][0]['sha256'], m.digest(reference))
        self.assertNotIn('authorization', item['provenance'])
        self.assertEqual(self.lib.import_tree(source, external_generation=record)['existing'], 1)
        conflict = self.lib.import_tree(source, external_generation={**record, 'tool': 'different tool'})
        self.assertEqual(len(conflict['errors']), 1)
        original = Path(item['original'])
        original.write_bytes(original.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, '外部生成来源'):
            self.lib.get(item['id'], require_file=True)

    def test_cli_failure_diagnosis_distinguishes_fatal_runtime_from_warnings(self):
        bridge = m.codex_bridge
        fatal = 'Error: failed to initialize in-process app-server client: Read-only file system (os error 30)'
        message = bridge.cli_failure_message('结果待核实', fatal)
        self.assertIn('运行环境目录只读', message)
        self.assertIn('不代表账号未登录', message)
        self.assertNotIn('未提交', message)  # No claim about remote side effects from a log classifier.
        for text in ('WARNING: could not create PATH aliases: Read-only file system (os error 30)',
                     'Error: model unavailable', 'request timed out'):
            with self.subTest(diagnostic=text):
                self.assertEqual(bridge.cli_failure_message('结果待核实', text), '结果待核实')

    def test_all_native_cli_consumers_surface_runtime_failure_without_retry(self):
        fatal = 'Error: failed to initialize in-process app-server client: Read-only file system (os error 30)'
        bridge = m.codex_bridge
        upload_bridge = m.visual_asset_upload.visual_asset_codex
        source = self.root / 'runtime-reference.png'
        Image.new('RGB', (80, 60), 'blue').save(source)
        def failed(command, **kwargs):
            if hasattr(kwargs.get('stderr'), 'write'):
                kwargs['stderr'].write(fatal)
            return subprocess.CompletedProcess(command, 1, '', fatal)
        cases = [
            ('review', bridge, '_run_prompt_cli', lambda folder: bridge.run('codex', folder, 'review',
                {'reference_paths': []}, '保持白模布局', '{}')),
            ('plan', bridge.subprocess, 'run', lambda folder: bridge.plan_assets('codex', folder,
                {'reference_paths': [], 'rows': []}, '{}')),
            ('recognition', upload_bridge, '_run_prompt_cli', lambda folder:
                m.visual_asset_upload.recognize(source, folder)),
            ('generation', bridge.subprocess, 'run', lambda folder: bridge.generate_image('codex', folder,
                {'reference_paths': [str(source)], 'prompt': '工程候选'})),
        ]
        with patch.object(upload_bridge, 'executable', return_value='codex'):
            for name, owner, attr, call in cases:
                with self.subTest(consumer=name), patch.object(owner, attr, side_effect=failed) as run:
                    with self.assertRaisesRegex(ValueError, '运行环境目录只读'):
                        call(self.root / ('runtime-' + name))
                    self.assertEqual(run.call_count, 1)

    def test_complete_cli_message_does_not_wait_for_a_stalled_process_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'complete.json'
            value = {'prompt': '四视图', 'passed': True, 'checks': ['真实检查'],
                     'issues': [], 'observations': [], 'uncertainties': []}
            script = 'import sys,json,time,pathlib; sys.stdin.read(); pathlib.Path(sys.argv[1]).write_text(json.dumps(json.loads(sys.argv[2])),encoding="utf-8"); time.sleep(30)'
            started = time.monotonic()
            result = m.codex_bridge._run_prompt_cli([sys.executable, '-c', script, str(output), json.dumps(value)],
                completion_path=output, input='只读测试', capture_output=True, text=True, encoding='utf-8', timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding='utf-8')), value)
            self.assertLess(time.monotonic() - started, 10)

    def test_incomplete_cli_message_stops_at_the_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'incomplete.json'
            script = 'import sys,time,pathlib; sys.stdin.read(); pathlib.Path(sys.argv[1]).write_text("{",encoding="utf-8"); time.sleep(30)'
            with self.assertRaises(subprocess.TimeoutExpired):
                m.codex_bridge._run_prompt_cli([sys.executable, '-c', script, str(output)],
                    completion_path=output, input='只读测试', capture_output=True, text=True, encoding='utf-8', timeout=2,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def test_upload_recognition_accepts_complete_result_without_waiting_for_cli_cleanup(self):
        picture = self.root / 'recognition-stall.png'
        Image.new('RGB', (80, 80), 'blue').save(picture)
        value = {'category': '大头照', 'confidence': '高', 'description': '单个人物',
                 'asset_name': '人物', 'focus_x': .5, 'focus_y': .5,
                 'critical_box': [.2, .1, .8, .9], 'has_multiple_subjects': False}
        script = 'import sys,json,time,pathlib; sys.stdin.read(); pathlib.Path(sys.argv[1]).write_text(sys.argv[2],encoding="utf-8"); time.sleep(30)'
        upload_bridge = m.visual_asset_upload.visual_asset_codex
        actual_runner = upload_bridge._run_prompt_cli
        def stalled_cli(command, **kwargs):
            self.assertIn('has_multiple_subjects', kwargs['required_fields'])
            return actual_runner([sys.executable, '-c', script, str(kwargs['completion_path']),
                                  json.dumps(value)], **kwargs)
        started = time.monotonic()
        with patch.object(upload_bridge, 'executable', return_value='codex.exe'), \
             patch.object(upload_bridge, '_run_prompt_cli', side_effect=stalled_cli):
            result = m.visual_asset_upload.recognize(picture, self.root / 'stalled-recognition')
        self.assertEqual(result, value)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(picture.is_file())

    def test_empty_optional_observations_do_not_fail_a_valid_luna_result(self):
        result = {'prompt': '香水瓶四视图。', 'passed': True, 'observations': [], 'uncertainties': [],
                  'checks': ['ASSET-SCOPE：香水瓶', 'ASSET-QC：四视图排版符合'], 'issues': []}
        with tempfile.TemporaryDirectory() as temp:
            def fake_run(command, **kwargs):
                self.assertIn('ASSET-SCOPE', kwargs['input'])
                schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text(encoding='utf-8'))
                self.assertEqual(schema['properties']['checks']['minItems'], 2)
                Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(result), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch.object(m.codex_bridge, '_run_prompt_cli', side_effect=fake_run):
                checked = m.codex_bridge.run('codex', Path(temp), 'draft', {'kind': '物品', 'reference_paths': []}, '',
                    json.dumps({'review_checks': m.asset_skill.checks('project', '物品')}))
            self.assertTrue(checked['passed'])

    def test_missing_review_evidence_gets_one_real_recheck_and_no_fabricated_pass(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装'})
        job['steps'][0]['audit'] = {'status': 'drafting', 'run_id': 'repair'}
        self.lib.save(job, 'jobs')
        bad = {'prompt': '无头人台上的蓝色衣服。', 'passed': True, 'observations': [], 'uncertainties': [],
               'checks': ['中文标题：未带检查编号'], 'issues': []}
        good = {**bad, 'checks': [c['id'] + '：按实际参考核对。' for c in m.asset_skill.checks()]}
        with patch.object(m.codex_bridge, 'run', side_effect=[dict(bad), dict(good)]) as run:
            self.lib._audit_worker('codex', job['id'], 0, 'repair', 'write', self.lib.step_request(job, 0))
        self.assertEqual([c.args[2] for c in run.call_args_list], ['draft', 'review'])
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['audit']['status'], 'passed')
        self.assertEqual(step['audit']['repair_attempts'], 1)
        self.assertFalse(step.get('prompt_approval'))
        job['steps'][0]['audit'] = {'status': 'drafting', 'run_id': 'still-missing'}
        self.lib.save(job, 'jobs')
        with patch.object(m.codex_bridge, 'run', side_effect=[dict(bad), dict(bad)]) as run:
            self.lib._audit_worker('codex', job['id'], 0, 'still-missing', 'write', self.lib.step_request(job, 0))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['audit']['status'], 'needs_changes')

    def test_codex_blank_failed_draft_preserves_actionable_issues(self):
        response = {'prompt': '', 'passed': False, 'observations': ['未提供参考图'],
                    'uncertainties': [], 'checks': ['身份参考缺失'], 'issues': ['请提供真人身份参考']}
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            def fake_run(command, **kwargs):
                Path(command[command.index('--output-last-message') + 1]).write_text(
                    json.dumps(response, ensure_ascii=False), encoding='utf8')
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch.object(m.codex_bridge, '_run_prompt_cli', side_effect=fake_run):
                review = m.codex_bridge.run('codex', folder, 'draft',
                                           {'reference_paths': [], 'kind': '大头照'}, '', '任务')
            self.assertFalse(review['passed'])
            self.assertEqual(review['issues'], response['issues'])
            response['passed'] = True
            with patch.object(m.codex_bridge, '_run_prompt_cli', side_effect=fake_run):
                with self.assertRaisesRegex(ValueError, '格式不完整'):
                    m.codex_bridge.run('codex', folder, 'draft',
                                       {'reference_paths': [], 'kind': '大头照'}, '', '任务')

    def test_recover_imagegen_imports_verified_existing_output_without_resubmitting(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装'})
        request = self.lib.step_request(job, 0)
        at = m.now()
        job['steps'][0].update(status='unknown', generation_run_id='finished-run',
                              authorization={'at': at, 'request': request})
        self.lib.save(job, 'jobs')
        output_dir = self.root / 'codex-home/generated_images'
        output_dir.mkdir(parents=True)
        output = output_dir / 'already-generated.png'
        Image.new('RGB', (1600, 900), 'blue').save(output)
        folder = self.lib.root / 'requests' / job['id'] / '0/imagegen/finished-run'
        folder.mkdir(parents=True)
        (folder / 'image-result.json').write_text(json.dumps({'status': 'generated', 'output_path': str(output), 'message': ''}), encoding='utf-8')
        with patch.dict(m.os.environ, {'CODEX_HOME': str(output_dir.parent)}), patch.object(m.codex_bridge, 'generate_image') as generate:
            self.lib.recover_imagegen({'id': job['id'], 'index': 0})
            generate.assert_not_called()
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['status'], 'awaiting_review')
        # A reference path cannot be passed off as a fresh generated result.
        request['reference_paths'] = [str(output)]
        with patch.dict(m.os.environ, {'CODEX_HOME': str(output_dir.parent)}):
            with self.assertRaisesRegex(ValueError, '不是本次新生成'):
                m.codex_bridge.generated_image_result(folder, request, datetime.fromisoformat(at).timestamp())

    def test_video_reference_import_preview_and_image_job_gate(self):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('ffmpeg/ffprobe unavailable')
        video = self.root / '参考片段.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=96x72:r=5',
                        '-t', '1', '-pix_fmt', 'yuv420p', str(video)], check=True)
        item, added = self.lib.import_file(video)
        self.assertTrue(added)
        self.assertEqual(item['category'], '视频参考')
        self.assertEqual(item['media_type'], 'video')
        self.assertTrue(Path(item['thumbnail']).is_file())
        self.assertGreater(item['duration'], 0)
        self.assertTrue(video.is_file())
        with self.assertRaisesRegex(ValueError, '视频参考'):
            self.lib.create_job({'source_id': item['id'], 'kind': '大头照', 'subject': '错误'})
        with self.assertRaisesRegex(ValueError, '不能互换'):
            m.management.manage(self.lib, [item['id']], 'move', '产品')
        server = m.ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        server.library, server.token = self.lib, 'test-token'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            request = Request(f'http://127.0.0.1:{server.server_port}/media/{item["id"]}/original',
                              headers={'Range': 'bytes=0-15'})
            with urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(len(response.read()), 16)
        finally:
            server.shutdown()
            server.server_close()

    def test_user_can_rename_asset_after_import(self):
        self.lib.update({'id': self.asset['id'], 'name': '白色面膜包装正面'})
        renamed = self.lib.get(self.asset['id'])
        self.assertEqual(renamed['name'], '白色面膜包装正面')
        self.assertEqual(renamed['name_history'], [self.source.stem])
        self.assertEqual(renamed['name_source'], 'user')

    def test_product_manual_categories_and_folder_order(self):
        category = self.lib.product_category({'action': 'create', 'name': '礼盒'})
        m.management.manage(self.lib, [self.asset['id']], 'move', '产品')
        self.lib.assign_product_category({'ids': [self.asset['id']], 'product_category_id': category['id']})
        self.lib.product_category({'action': 'rename', 'id': category['id'], 'name': '包装'})
        self.assertEqual(self.lib.get(self.asset['id'])['product_category_id'], category['id'])
        with self.assertRaisesRegex(ValueError, '还有资产'):
            self.lib.product_category({'action': 'delete', 'id': category['id']})
        other_source = self.root / '第二张.png'
        Image.new('RGB', (40, 40), 'red').save(other_source)
        other, _ = self.lib.import_file(other_source)
        first = self.lib.folder({'ids': [self.asset['id']], 'name': '一组'})['folder_id']
        second = self.lib.folder({'ids': [other['id']], 'name': '二组'})['folder_id']
        self.lib.folder({'action': 'reorder', 'folder_ids': [second, first]})
        self.assertEqual(self.lib.get(other['id'])['folder_order'], 0)
        self.assertEqual(self.lib.get(self.asset['id'])['folder_order'], 1)

    def test_character_1x4_groups_by_identity_and_respects_manual_removal(self):
        ids = []
        for n in range(3):
            path = self.root / f'服装{n}.png'
            Image.new('RGB', (160, 90), (n * 40, 20, 80)).save(path)
            asset, _ = self.lib.import_file(path, category='人物1x4')
            ids.append(asset['id'])
        self.lib.auto_group_character(ids[0], 'project:p:AST-PERSON', '公主')
        self.assertFalse(self.lib.get(ids[0]).get('folder_id'))
        self.lib.auto_group_character(ids[1], 'project:p:AST-PERSON', '公主')
        first, second = (self.lib.get(i) for i in ids[:2])
        self.assertEqual(first['folder_id'], second['folder_id'])
        self.assertEqual(first['folder_name'], '公主｜人物1×4')
        self.lib.auto_group_character(ids[2], 'project:p:AST-OTHER', '二公主')
        self.assertFalse(self.lib.get(ids[2]).get('folder_id'))
        self.lib.folder({'action': 'remove', 'ids': [ids[0]]})
        self.lib.auto_group_character(ids[0], 'project:p:AST-PERSON', '公主')
        self.assertFalse(self.lib.get(ids[0]).get('folder_id'))

    def test_manual_purge_removes_managed_files_and_blocks_confirmed(self):
        item = self.lib.get(self.asset['id'])
        m.management.manage(self.lib, [item['id']], 'trash')
        trashed = self.lib.get(item['id'])
        self.assertEqual(m.management.manage(self.lib, [item['id']], 'purge', manual=True)['count'], 1)
        self.assertFalse(Path(trashed['original']).exists())
        self.assertTrue(self.source.exists())
        second, _ = self.lib.import_file(self.source)
        self.lib.confirm(second['id'])
        m.management.manage(self.lib, [second['id']], 'trash')
        with self.assertRaisesRegex(ValueError, '已确认'):
            m.management.manage(self.lib, [second['id']], 'purge', manual=True)
        self.assertTrue(Path(self.lib.get(second['id'])['original']).is_file())

    def test_upload_classifies_and_keeps_only_adjusted_asset(self):
        source = self.root / 'uploads' / 'new.png'
        source.parent.mkdir()
        Image.new('RGB', (400, 400), 'red').save(source)
        original_bytes = source.read_bytes()
        recognition = {'category': '大头照', 'confidence': '高', 'description': '人像',
                       'asset_name': '黑发人物正面大头照',
                       'focus_x': 0.5, 'focus_y': 0.5, 'critical_box': [0.2, 0.05, 0.8, 0.95]}
        def recognize(_source, folder):
            reference = folder / 'reference'
            reference.mkdir(parents=True)
            (reference / '1.png').write_bytes(original_bytes)
            return recognition
        with patch.object(m.visual_asset_upload, 'recognize', side_effect=recognize):
            crop = self.lib.upload_file(source)
        self.assertEqual(crop['category'], '大头照')
        self.assertEqual((crop['width'], crop['height']), (300, 400))
        self.assertEqual(crop['status'], 'unreviewed')
        self.assertEqual(crop['name'], '黑发人物正面大头照')
        self.assertEqual(self.lib.get(crop['id'])['name'], '黑发人物正面大头照')
        self.assertEqual(crop['name_source'], 'codex_cli')
        self.assertEqual(crop['name_history'], [source.stem])
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(crop['upload_processing']['status'], 'added')
        self.assertEqual(len(self.lib.items()), 2)
        self.assertEqual(crop['provenance']['kind'], 'upload_adjusted')
        self.assertFalse(list((self.lib.root / 'requests' / 'uploads').rglob('*.png')))

    def test_upload_mismatch_and_cli_failure_keep_uncropped_original(self):
        source = self.root / 'uploads' / 'another.png'
        source.parent.mkdir()
        Image.new('RGB', (400, 400), 'green').save(source)
        recognition = {'category': '场景', 'confidence': '高', 'description': '房间',
                       'focus_x': 0.5, 'focus_y': 0.5, 'critical_box': [0, 0, 1, 1]}
        with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition):
            item = self.lib.upload_file(source, category='大头照')
        self.assertEqual(item['upload_processing']['status'], 'error')
        self.assertEqual(len(self.lib.items()), 1)
        failed_source = source.with_name('failed.png')
        Image.new('RGB', (400, 400), 'blue').save(failed_source)
        with patch.object(m.visual_asset_upload, 'recognize', side_effect=ValueError('识别失败')):
            failed = self.lib.upload_file(failed_source)
        self.assertEqual(failed['upload_processing']['status'], 'error')
        self.assertIn('识别失败', failed['upload_processing']['message'])
        explicit_source = source.with_name('explicit.png')
        Image.new('RGB', (400, 400), 'blue').save(explicit_source)
        with patch.object(m.visual_asset_upload, 'recognize', side_effect=ValueError('识别失败')):
            explicit = self.lib.upload_file(explicit_source, category='大头照')
        self.assertEqual(explicit['upload_processing']['status'], 'error')
        self.assertEqual(len(self.lib.items()), 1)

    def test_upload_pads_light_background_when_subject_would_be_cut(self):
        source = self.root / 'uploads' / 'edge-portrait.png'
        source.parent.mkdir()
        Image.new('RGB', (400, 400), 'white').save(source)
        recognition = {'category': '大头照', 'confidence': '高', 'description': '人像',
                       'focus_x': 0.5, 'focus_y': 0.5, 'critical_box': [0, 0, 1, 1]}
        with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition):
            candidate = self.lib.upload_file(source)
        self.assertEqual(candidate['upload_crop']['status'], 'padded')
        self.assertEqual(candidate['width'] * 4, candidate['height'] * 3)
        self.assertEqual(candidate['status'], 'unreviewed')
        self.assertEqual(len(self.lib.items()), 2)

    def test_local_upload_preserves_source_after_verified_crop(self):
        source = self.root / 'local-portrait.png'
        Image.new('RGB', (400, 400), 'red').save(source)
        recognition = {'category': '大头照', 'confidence': '高', 'description': '人像',
                       'focus_x': 0.5, 'focus_y': 0.5, 'critical_box': [0.2, 0.1, 0.8, 0.9]}
        with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition):
            result = self.lib.upload_local_file(source)
        self.assertEqual(result['upload_processing']['status'], 'added')
        self.assertFalse(result['upload_processing']['source_deleted'])
        self.assertTrue(source.exists())
        self.assertTrue(Path(result['original']).is_file())
        self.assertEqual(len(self.lib.items()), 2)

    def test_local_upload_skips_duplicate_and_preserves_selected_copy(self):
        duplicate = self.root / 'duplicate.png'
        duplicate.write_bytes(self.source.read_bytes())
        with patch.object(m.visual_asset_upload, 'recognize') as model:
            result = self.lib.upload_local_file(duplicate)
        model.assert_not_called()
        self.assertEqual(result['upload_processing']['status'], 'duplicate')
        self.assertFalse(result['upload_processing']['source_deleted'])
        self.assertTrue(duplicate.exists())
        self.assertTrue(self.source.exists())
        self.assertEqual(len(self.lib.items()), 1)

    def test_local_upload_pads_dark_background_instead_of_rejecting(self):
        source = self.root / 'unfixable.png'
        Image.new('RGB', (400, 400), 'red').save(source)
        recognition = {'category': '大头照', 'confidence': '高', 'description': '人像',
                       'focus_x': 0.5, 'focus_y': 0.5, 'critical_box': [0, 0, 1, 1]}
        with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition):
            result = self.lib.upload_local_file(source)
        self.assertEqual(result['upload_processing']['status'], 'added')
        self.assertEqual(result['upload_crop']['status'], 'padded')
        self.assertFalse(result['upload_processing']['source_deleted'])
        self.assertTrue(source.exists())
        self.assertEqual(len(self.lib.items()), 2)
        self.assertEqual(result['width'] * 4, result['height'] * 3)

    def test_upload_padding_preserves_all_views_and_transparency(self):
        source = self.root / 'multiview.png'
        image = Image.new('RGBA', (123, 119), (10, 40, 90, 100))
        # Distinct corners stand for the outer edges of the first and last views.
        for point, color in [((0, 0), (255, 0, 0, 255)), ((122, 118), (0, 255, 0, 255))]:
            image.putpixel(point, color)
        image.save(source)
        for category in ('服装三视图', '宠物', '场景', '其他'):
            with self.subTest(category=category):
                destination = self.root / f'{category}.png'
                recognition = {'focus_x': .5, 'focus_y': .5, 'critical_box': [0, 0, 1, 1]}
                result = m.visual_asset_upload.crop_to_category(source, destination, category, recognition)
                self.assertEqual(result['status'], 'padded')
                m.visual_asset_upload.validate_dimensions(destination, category)
                x, y, width, height = result['placement']
                with Image.open(destination) as fitted:
                    self.assertEqual(fitted.crop((x, y, x + width, y + height)).tobytes(), image.tobytes())
                    self.assertEqual(fitted.getpixel((0, 0))[3], 0)

    def test_upload_final_size_gate_blocks_bad_processor_output(self):
        source = self.root / 'bad-processor.png'
        Image.new('RGB', (401, 399), 'purple').save(source)
        recognition = {'category': '大头照', 'confidence': '高', 'description': '人像',
                       'focus_x': .5, 'focus_y': .5, 'critical_box': [0, 0, 1, 1]}
        with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition), \
             patch.object(m.visual_asset_upload, 'crop_to_category',
                          return_value={'status': 'already_correct', 'ratio': '3:4'}):
            result = self.lib.upload_local_file(source)
        self.assertEqual(result['upload_processing']['status'], 'error')
        self.assertIn('尺寸核对失败', result['upload_processing']['message'])
        self.assertTrue(source.is_file())
        self.assertEqual(len(self.lib.items()), 1)

    def test_upload_corrects_exif_orientation_before_size_gate(self):
        source, destination = self.root / 'rotated.jpg', self.root / 'upright.png'
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (400, 300), 'orange').save(source, exif=exif)
        result = m.visual_asset_upload.crop_to_category(source, destination, '大头照')
        self.assertEqual(result['status'], 'oriented')
        m.visual_asset_upload.validate_dimensions(destination, '大头照')
        with Image.open(destination) as image:
            self.assertEqual(image.size, (300, 400))

    def test_local_upload_protects_existing_source_and_model_failure(self):
        with self.assertRaisesRegex(ValueError, '引用'):
            self.lib.upload_local_file(self.source)
        self.assertTrue(self.source.exists())
        fresh = self.root / 'failed-local.png'
        Image.new('RGB', (400, 400), 'blue').save(fresh)
        with patch.object(m.visual_asset_upload, 'recognize', side_effect=ValueError('失败')):
            result = self.lib.upload_local_file(fresh)
        self.assertEqual(result['upload_processing']['status'], 'error')
        self.assertFalse(result['upload_processing']['source_deleted'])
        self.assertTrue(fresh.exists())

    def test_upload_recognition_uses_luna(self):
        source = self.root / 'sample.png'
        Image.new('RGB', (40, 40), 'red').save(source)
        def cli(command, **kwargs):
            self.assertEqual(command[command.index('--model') + 1], 'gpt-6-luna')
            self.assertIn('has_multiple_subjects', kwargs['required_fields'])
            output = Path(command[command.index('--output-last-message') + 1])
            output.write_text(json.dumps({'category': '产品', 'confidence': '高', 'description': '瓶子',
                                          'asset_name': '透明瓶子正面图',
                                          'focus_x': 0.5, 'focus_y': 0.5,
                                          'critical_box': [0.2, 0.1, 0.8, 0.9]}), encoding='utf-8')
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch.object(m.visual_asset_upload.visual_asset_codex, 'executable', return_value='codex.exe'), \
             patch.object(m.visual_asset_upload.visual_asset_codex, '_run_prompt_cli', side_effect=cli):
            result = m.visual_asset_upload.recognize(source, self.root / 'recognition')
        self.assertEqual(result['category'], '产品')
        self.assertEqual(result['asset_name'], '透明瓶子正面图')

    def test_folder_membership_is_atomic_and_preserves_production_group(self):
        from PIL import Image
        source = self.lib.root / 'folder-reference.png'
        Image.new('RGB', (80, 100), 'blue').save(source)
        item, _ = self.lib.import_file(source, category='其他')
        item['group'] = 'AST original group'
        self.lib.save(item)
        with self.assertRaises(ValueError):
            self.lib.folder({'ids': [item['id'], 'nonexistent'], 'name': 'bad'})
        self.assertNotIn('folder_id', self.lib.get(item['id']))
        folder = self.lib.folder({'ids': [item['id']], 'name': '组合'})
        self.lib.folder({'ids': [item['id']], 'folder_id': folder['folder_id'], 'action': 'rename', 'name': '改名'})
        saved = self.lib.get(item['id'])
        self.assertEqual(saved['folder_name'], '改名')
        self.assertEqual(saved['group'], 'AST original group')
        self.assertEqual(saved['backup']['status'], 'pending')
        self.lib.folder({'ids': [item['id']], 'action': 'remove'})
        self.assertNotIn('folder_id', self.lib.get(item['id']))
        self.assertTrue(source.is_file())

    def test_delete_job_hides_record_and_preserves_assets(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '旧任务', 'kind': '大头照'})
        job = self.lib.attach(job['id'], 0, [self.source])
        before = self.lib.items()
        self.lib.delete_job(job['id'])
        self.assertEqual(m.Library(self.lib.root, vault=self.lib.vault).items('jobs'), [])
        self.assertEqual(self.lib.items(), before)
        self.assertTrue(self.source.exists())
        with self.assertRaisesRegex(ValueError, '已删除'):
            self.lib.prepare(job['id'], 0)

    def test_delete_job_cannot_interrupt_generation_or_review(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '进行中', 'kind': '大头照'})
        for status, audit in [('generating', 'passed'), ('submitted', 'passed'), ('waiting', 'reviewing')]:
            job['steps'][0].update(status=status, audit={'status': audit})
            self.lib.save(job, 'jobs')
            with self.assertRaisesRegex(ValueError, '正在处理'):
                self.lib.delete_job(job['id'])
            self.assertEqual(len(self.lib.items('jobs')), 1)

    def test_skill_rejects_language_and_incomplete_review_even_if_model_passes(self):
        data = {'prompt': '4K高细节，唯美大片', 'passed': True, 'checks': [], 'issues': []}
        self.assertFalse(m.asset_skill.validate_review(data)['passed'])
        self.assertTrue(any('PROMPT-LANG' in issue for issue in data['issues']))
        clean = {'prompt': '正面柔光，皮肤毛孔可见。', 'passed': True, 'checks': [], 'issues': []}
        self.assertFalse(m.asset_skill.validate_review(clean)['passed'])
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': '大头照'})
        prompt = '正面柔光，皮肤毛孔可见。'
        job['steps'][0].update(prompt=prompt, audit={'status': 'passed', 'prompt': prompt})
        self.lib.save(job, 'jobs')
        with self.assertRaisesRegex(ValueError, '审查'):
            self.lib.save_prompt({'id': job['id'], 'index': 0, 'prompt': prompt, 'approve': True})

    def test_skill_categories_and_confirmed_reference_roles(self):
        for kind in ['物品', '产品', '风格', '色卡']:
            job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': kind})
            self.assertEqual(self.lib.prepare(job['id'], 0)['ratio'], m.asset_skill.recipe(kind)['ratio'])
        with self.assertRaises(ValueError):
            self.lib.create_job({'source_id': self.asset['id'], 'secondary_id': self.asset['id'], 'subject': '人物', 'kind': '人物1x4'})
        clothing = self.lib.get(self.asset['id'])
        clothing.update(category='服装三视图', status='confirmed')
        self.lib.save(clothing)
        face_path = self.root / 'face.png'
        Image.new('RGB', (300, 400), 'blue').save(face_path)
        face, _ = self.lib.import_file(face_path, category='大头照')
        self.lib.confirm(face['id'])
        job = self.lib.create_job({'source_id': clothing['id'], 'secondary_id': face['id'], 'subject': '人物', 'kind': '人物1x4'})
        request = self.lib.prepare(job['id'], 0)
        self.assertEqual(request['reference_paths'], [clothing['original'], face['original']])
        self.assertIn('1×4', request['prompt'])
        m.management.manage(self.lib, [face['id']], 'trash')
        with self.assertRaises(ValueError):
            self.lib.prepare(job['id'], 0)

    def test_library_confirmation_binds_bytes_without_reapproving_changed_original(self):
        from core.artifacts import digest
        confirmed = self.lib.confirm(self.asset['id'])
        path = Path(confirmed['original'])
        receipt = dict(confirmed['confirmation'])
        self.assertEqual(receipt['image_sha256'], digest(path))
        Image.new('RGB', (400, 300), 'red').save(path)
        with self.assertRaisesRegex(ValueError, '图片内容已变化'):
            self.lib.confirm(self.asset['id'])
        with self.assertRaisesRegex(ValueError, '图片内容已变化'):
            self.lib.export([self.asset['id']])
        self.assertEqual(self.lib.get(self.asset['id'])['confirmation'], receipt)

    def test_bad_generated_ratio_cannot_be_confirmed(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '脸', 'kind': '大头照'})
        wrong = self.root / 'square.png'
        Image.new('RGB', (300, 300), 'blue').save(wrong)
        job = self.lib.attach(job['id'], 0, [wrong])
        with self.assertRaisesRegex(ValueError, '技术检查'):
            self.lib.confirm(job['steps'][0]['outputs'][0])

    def test_rejected_result_can_be_restored_and_redone_without_generation(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': '大头照'})
        output = self.root / 'unsatisfactory.png'
        Image.new('RGB', (300, 400), 'blue').save(output)
        job = self.lib.attach(job['id'], 0, [str(output)])
        ident = job['steps'][0]['outputs'][0]
        old_prompt = job['steps'][0]['prompt']
        with patch.object(m.threading, 'Thread') as worker:
            self.lib.reject_result({'id': job['id'], 'index': 0})
            step = self.lib.get(job['id'], 'jobs')['steps'][0]
            self.assertEqual(step['status'], 'discarded')
            self.assertEqual(step['outputs'], [])
            self.assertEqual(step['result_history'][0]['outputs'], [ident])
            self.assertIn('trashed_at', self.lib.get(ident))
            self.lib.reject_result({'id': job['id'], 'index': 0, 'redo': True})
            worker.assert_not_called()
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'waiting')
        self.assertEqual(step['prompt'], old_prompt)
        self.assertFalse(self.lib.prompt_is_approved(self.lib.get(job['id'], 'jobs'), 0))
        m.management.manage(self.lib, [ident], 'restore')
        self.assertNotIn('trashed_at', self.lib.get(ident))
        self.assertTrue(output.exists())
        self.assertNotIn('trashed_at', self.lib.get(self.asset['id']))

    def test_reject_result_preserves_state_when_trash_fails_and_blocks_confirmed(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': '大头照'})
        output = self.root / 'result.png'
        Image.new('RGB', (300, 400), 'blue').save(output)
        job = self.lib.attach(job['id'], 0, [str(output)])
        with patch.object(m.management, 'manage', side_effect=ValueError('locked')):
            with self.assertRaisesRegex(ValueError, 'locked'):
                self.lib.reject_result({'id': job['id'], 'index': 0, 'redo': True})
        self.assertEqual(self.lib.get(job['id'], 'jobs'), job)
        self.lib.confirm(job['steps'][0]['outputs'][0])
        with self.assertRaises(ValueError):
            self.lib.reject_result({'id': job['id'], 'index': 0, 'redo': True})

    def test_large_reference_is_bounded_without_changing_original_or_dimensions(self):
        source = self.root / 'large.png'
        Image.effect_noise((1500, 1500), 80).convert('RGB').save(source, compress_level=0)
        before = source.read_bytes()
        paths = m.codex_bridge.stage_image_references(self.root / 'prepared', [source])
        self.assertEqual(source.read_bytes(), before)
        self.assertLessEqual(Path(paths[0]).stat().st_size, 3 * 1024 * 1024)
        with Image.open(paths[0]) as image:
            image.load()
            self.assertEqual(image.size, (1500, 1500))

    def test_imagegen_reprepare_does_not_generate_and_retains_failure(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': '大头照'})
        job['steps'][0].update(status='unknown', generation_run_id='failed-run',
            message='Invalid symbol 61', prompt_approval={'prompt': 'old'})
        self.lib.save(job, 'jobs')
        with patch.object(m.threading, 'Thread') as worker:
            self.lib.reset_imagegen({'id': job['id'], 'index': 0})
            worker.assert_not_called()
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'waiting')
        self.assertNotIn('prompt_approval', step)
        self.assertEqual(step['generation_history'][0]['generation_run_id'], 'failed-run')
        with self.assertRaises(ValueError):
            self.lib.reset_imagegen({'id': job['id'], 'index': 0})

    def test_imagegen_requires_approval_blocks_duplicates_and_imports(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': '大头照'})
        ident = job['id']
        with patch.object(m.threading, 'Thread') as worker:
            with self.assertRaisesRegex(ValueError, '确认'):
                self.lib.submit_imagegen({'id': ident, 'index': 0})
            with self.assertRaisesRegex(ValueError, '审查'):
                self.lib.submit_imagegen({'id': ident, 'index': 0, 'authorize': True})
            job['steps'][0]['prompt'] = '3:4白底正面人脸，头顶到锁骨。'
            job['steps'][0].update(audit={'status': 'passed', 'skill_execution': {'version': m.asset_skill.profile()['version']}, 'prompt': job['steps'][0]['prompt']},
                prompt_approval={'prompt': job['steps'][0]['prompt']})
            self.lib.save(job, 'jobs')
            with patch.object(m.codex_bridge, 'executable', return_value='codex.exe'):
                self.lib.submit_imagegen({'id': ident, 'index': 0, 'authorize': True})
            self.assertEqual(worker.call_count, 1)
            with self.assertRaisesRegex(ValueError, '不会重复'):
                self.lib.submit_imagegen({'id': ident, 'index': 0, 'authorize': True})
        step = self.lib.get(ident, 'jobs')['steps'][0]
        generated = self.root / 'generated.png'
        Image.new('RGB', (320, 320), 'blue').save(generated)
        with patch.object(m.codex_bridge, 'generate_image', return_value=str(generated)):
            self.lib._imagegen_worker('codex', ident, 0, step['generation_run_id'], step['authorization']['request'])
        updated = self.lib.get(ident, 'jobs')['steps'][0]
        self.assertEqual(updated['status'], 'awaiting_review')
        self.assertEqual(len(updated['outputs']), 1)
        asset = self.lib.get(updated['outputs'][0])
        self.assertEqual(asset['category'], '大头照')
        self.assertEqual(asset['status'], 'unreviewed')
        self.assertTrue(Path(asset['original']).exists())

    def test_imagegen_failure_is_unknown_and_never_retried(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试', 'kind': '服装'})
        job['steps'][0].update(status='generating', generation_run_id='test')
        self.lib.save(job, 'jobs')
        with patch.object(m.codex_bridge, 'generate_image', side_effect=ValueError('等待超时')) as run:
            self.lib._imagegen_worker('codex', job['id'], 0, 'test', self.lib.step_request(job, 0))
            self.assertEqual(run.call_count, 1)
        result = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['outputs'], [])

    def test_codex_review_approval_is_bound_to_current_prompt(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装'})
        ident = job['id']
        with self.assertRaisesRegex(ValueError, '审查'):
            self.lib.save_prompt({'id': ident, 'index': 0, 'prompt': '未审查提示词', 'approve': True})
        job['steps'][0]['audit'] = {'status': 'drafting', 'run_id': 'test'}
        self.lib.save(job, 'jobs')
        result = {'prompt': '白底无头人台，正侧背三格，同一套蓝色衣服。', 'passed': True,
                  'observations': ['蓝色衣服'], 'checks': [c['id'] + '：已按参考核对。' for c in m.asset_skill.checks()], 'uncertainties': [], 'issues': []}
        with patch.object(m.codex_bridge, 'run', return_value=result) as run:
            self.lib._audit_worker('codex', ident, 0, 'test', 'write', self.lib.step_request(job, 0))
            self.assertEqual([c.args[2] for c in run.call_args_list], ['draft'])
        self.assertEqual(self.lib.prepare(ident, 0)['handoff'], '')
        p = {'id': ident, 'index': 0, 'prompt': result['prompt'], 'approve': True}
        self.assertTrue(self.lib.save_prompt(p)['handoff'])
        p.update(prompt='改成红色衣服', approve=False)
        edited = self.lib.save_prompt(p)
        self.assertEqual(edited['audit']['status'], 'stale')
        self.assertEqual(edited['handoff'], '')
        self.assertFalse(edited['approved'])
        with self.assertRaisesRegex(ValueError, '审查'):
            self.lib.save_prompt({**p, 'approve': True})

        # An explicit recheck also invokes the model once, without auto approval.
        job = self.lib.get(ident, 'jobs')
        job['steps'][0]['audit'] = {'status': 'reviewing', 'run_id': 'recheck'}
        self.lib.save(job, 'jobs')
        with patch.object(m.codex_bridge, 'run', return_value=result) as run:
            self.lib._audit_worker('codex', ident, 0, 'recheck', 'review', self.lib.step_request(job, 0))
        self.assertEqual([c.args[2] for c in run.call_args_list], ['review'])
        saved = self.lib.get(ident, 'jobs')['steps'][0]
        self.assertEqual(saved['prompt'], '改成红色衣服')
        self.assertEqual(saved['audit']['prompt'], '改成红色衣服')
        self.assertIn('蓝色衣服', saved['audit']['suggested_prompt'])
        self.assertEqual(saved['audit']['status'], 'needs_changes')
        self.assertFalse(self.lib.prepare(ident, 0)['approved'])

    def test_audit_entry_allows_three_distinct_items_and_rejects_duplicates(self):
        jobs = [self.lib.create_job({'source_id': self.asset['id'], 'subject': str(i), 'kind': '服装'}) for i in range(4)]
        with patch.object(m.codex_bridge, 'executable', return_value='codex'), patch.object(m.threading.Thread, 'start'):
            for job in jobs[:3]:
                self.lib.audit_prompt({'id': job['id'], 'index': 0, 'mode': 'review'})
            with self.assertRaisesRegex(ValueError, '重复'):
                self.lib.audit_prompt({'id': jobs[0]['id'], 'index': 0, 'mode': 'review'})
            with self.assertRaisesRegex(ValueError, '3 项'):
                self.lib.audit_prompt({'id': jobs[3]['id'], 'index': 0, 'mode': 'review'})

    def test_codex_error_does_not_approve_or_replace_prompt(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '脸', 'kind': '大头照'})
        original = job['steps'][0]['prompt']
        job['steps'][0]['audit'] = {'status': 'reviewing', 'run_id': 'test'}
        self.lib.save(job, 'jobs')
        with self.assertRaisesRegex(ValueError, '正在处理'):
            self.lib.save_prompt({'id': job['id'], 'index': 0, 'prompt': '修改'})
        with patch.object(m.codex_bridge, 'run', side_effect=ValueError('登录失败')):
            self.lib._audit_worker('codex', job['id'], 0, 'test', 'review', self.lib.step_request(job, 0))
        result = self.lib.prepare(job['id'], 0)
        self.assertEqual(result['audit']['status'], 'error')
        self.assertEqual(result['prompt'], original)
        self.assertFalse(result['approved'])

    def test_template_outputs_and_edited_prompt_survive_reload(self):
        for kind, expected in [('大头照', '从头顶到锁骨的紧凑大头特写'), ('服装', '1×3 网格')]:
            job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '照片中的对象', 'kind': kind})
            self.assertEqual(len(job['steps']), 1)
            request = self.lib.prepare(job['id'], 0)
            self.assertIn(expected, request['prompt'])
            self.assertEqual(request['ratio'], '3:4' if kind == '大头照' else '16:9')
            if kind == '大头照':
                self.assertIn('画面内不出现任何衣服', request['prompt'])
                self.assertIn('下边缘止于锁骨下缘', request['prompt'])
            self.assertNotIn('【', request['prompt'])
            self.lib.save_prompt({'id': job['id'], 'index': 0, 'prompt': '保留指定细节，蓝色背景', 'provider': 'dreamina', 'model': '5.0Pro'})
            request = m.Library(self.lib.root, vault=self.lib.vault).prepare(job['id'], 0)
            self.assertEqual(request['prompt'], '保留指定细节，蓝色背景')
            self.assertEqual(request['provider'], 'dreamina')
            self.assertEqual(request['model'], '5.0Pro')
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '房间', 'kind': '环境'})
        self.assertEqual([s['name'] for s in job['steps']], ['45°斜俯视', '正面', '反面', '90°俯瞰'])
        self.assertIn('用户所选照片', self.lib.prepare(job['id'], 0)['prompt'])
        scene = self.root / 'scene-result.png'
        Image.new('RGB', (320, 180), 'green').save(scene)
        self.lib.attach(job['id'], 0, [scene])
        self.lib.confirm(self.lib.get(job['id'], 'jobs')['steps'][0]['outputs'][0])
        self.lib.prepare(job['id'], 1)
        with self.assertRaisesRegex(ValueError, '前置'):
            self.lib.prepare(job['id'], 2)

    def test_scene_group_persists_and_rejects_duplicate_views(self):
        m.management.manage(self.lib, [self.asset['id']], 'move', '场景')
        result = self.lib.group_scene({'name': '卧室', 'views': [{'id': self.asset['id'], 'view': '45°'}]})
        item = m.Library(self.lib.root, vault=self.lib.vault).get(self.asset['id'])
        self.assertEqual(item['scene_id'], result['scene_id'])
        self.assertEqual(item['scene_view'], '45°')
        with self.assertRaises(ValueError):
            self.lib.group_scene({'name': '错误组', 'views': [{'id': item['id'], 'view': '45°'}] * 2})
        m.management.manage(self.lib, [item['id']], 'trash')
        with self.assertRaises(ValueError):
            self.lib.group_scene({'name': '错误组', 'views': [{'id': item['id'], 'view': '正面'}]})
        m.management.manage(self.lib, [item['id']], 'restore')
        self.assertEqual(self.lib.get(item['id'])['scene_id'], result['scene_id'])

    def test_move_trash_restore_and_expiration(self):
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        m.management.manage(self.lib, [self.asset['id']], 'move', '物品')
        moved = self.lib.get(self.asset['id'])
        self.assertEqual(Path(moved['original']).parent.parent.name, '物品')
        m.management.manage(self.lib, [self.asset['id']], 'trash', at=at)
        trashed = self.lib.get(self.asset['id'])
        self.assertEqual(Path(trashed['original']).parent.parent.name, '垃圾桶')
        self.assertEqual(m.management.cleanup(self.lib, at=at+timedelta(days=29))['count'], 0)
        m.management.manage(self.lib, [self.asset['id']], 'restore')
        self.assertNotIn('trashed_at', self.lib.get(self.asset['id']))
        self.assertEqual(Path(self.lib.get(self.asset['id'])['original']).read_bytes(), self.original_bytes)
        m.management.manage(self.lib, [self.asset['id']], 'trash', at=at)
        self.assertEqual(m.management.cleanup(self.lib, at=at+timedelta(days=30))['count'], 1)
        self.assertEqual(self.lib.items(), [])
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_batch_preflight_prevents_partial_move(self):
        original = self.lib.get(self.asset['id'])['original']
        with self.assertRaises(ValueError):
            m.management.manage(self.lib, [self.asset['id'], 'missing'], 'move', '产品')
        self.assertTrue(Path(original).is_file())
        with self.assertRaises(ValueError):
            m.management.manage(self.lib, [self.asset['id']], 'move', '../outside')

    def test_exact_duplicate_is_trashed_with_one_canonical(self):
        duplicate = self.root / 'duplicate.png'
        duplicate.write_bytes(self.original_bytes)
        other, added = self.lib.import_file(duplicate, project='重复来源', role='候选')
        self.assertTrue(added)
        result = m.management.dedupe(self.lib, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result['duplicate_groups'], 1)
        self.assertEqual(result['count'], 1)
        active = [item for item in self.lib.items() if not item.get('trashed_at')]
        trashed = [item for item in self.lib.items() if item.get('trashed_at')]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(trashed), 1)
        self.assertTrue(Path(trashed[0]['original']).parent.parent.name == '垃圾桶')
        self.assertTrue(self.source.exists())

    def test_dedupe_keeps_distinct_confirmed_ids_and_project_confirmation_records(self):
        original = self.lib.get(self.asset['id'])
        original.update(status='confirmed', project_confirmations=[{'project_id': 'a' * 16, 'id': 'receipt'}])
        self.lib.save(original)
        duplicate = self.root / 'duplicate.png'
        duplicate.write_bytes(self.original_bytes)
        other, _ = self.lib.import_file(duplicate, project='重复来源', role='母版')
        self.lib.confirm(other['id'])
        result = m.management.dedupe(self.lib)
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['kept_count'], 2)
        kept = self.lib.get(original['id'])
        self.assertNotIn('trashed_at', kept)
        self.assertEqual(kept['project_confirmations'], original['project_confirmations'])
        self.assertNotIn('trashed_at', self.lib.get(other['id']))

    def test_dedupe_preserves_current_project_output_and_reference_ids(self):
        duplicate = self.root / 'duplicate.png'
        duplicate.write_bytes(self.original_bytes)
        other, _ = self.lib.import_file(duplicate, project='重复来源', role='母版')
        project_id, job_id = 'a' * 16, 'b' * 16
        job = {'id': job_id, 'project_id': project_id, 'steps': [
            {'selected': self.asset['id'], 'outputs': [self.asset['id']], 'reference_ids': []}]}
        self.lib.save(job, 'jobs')
        project = {'id': project_id, 'rows': [{'job_id': job_id, 'reference_ids': []}]}
        self.lib.projects.save(project)
        result = m.management.dedupe(self.lib)
        self.assertEqual(result['count'], 1)
        self.assertNotIn('trashed_at', self.lib.get(self.asset['id']))
        self.assertIn('trashed_at', self.lib.get(other['id']))
        self.assertEqual(self.lib.get(job_id, 'jobs'), job)
        self.lib.projects.save({'id': project_id, 'rows': [{'job_id': None, 'reference_ids': [self.asset['id']]}]})
        m.management.manage(self.lib, [other['id']], 'restore')
        result = m.management.dedupe(self.lib)
        self.assertEqual(result['count'], 1)
        self.assertNotIn('trashed_at', self.lib.get(self.asset['id']))

    def test_interrupted_index_write_keeps_the_previous_index_readable_on_restart(self):
        root = self.lib.vault / '资产库'
        path = root / '资产索引.json'
        original = path.read_bytes()
        index = json.loads(original)
        index['note'] = '尚未写入的更新'
        write_text = Path.write_text
        def interrupted(file, value, *args, **kwargs):
            if file.parent == root and file.name.startswith('.tmp-'):
                write_text(file, '{"assets":', *args, **kwargs)
                raise OSError('simulated interrupted write')
            return write_text(file, value, *args, **kwargs)
        with patch.object(Path, 'write_text', interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted write'):
                m.management.write_index(root, index)
        self.assertEqual(path.read_bytes(), original)
        self.assertIsInstance(json.loads(path.read_text(encoding='utf-8'))['assets'], list)
        self.assertEqual(list(root.glob('.tmp-*')), [])
        restarted = m.Library(self.lib.root, vault=self.lib.vault)
        self.assertTrue(Path(restarted.get(self.asset['id'])['original']).is_file())

    def test_vault_copy_follows_move_trash_and_restore(self):
        vault = self.root / 'vault'
        root = vault / '资产库'
        folder = root / '其他' / 'OTHER-000001_衣服'
        folder.mkdir(parents=True)
        photo = folder / '衣服.png'
        photo.write_bytes(self.original_bytes)
        relative = photo.relative_to(root).as_posix()
        (folder / '资产说明.md').write_text('- 分类：其他\n[[资产库/' + relative + ']]', encoding='utf-8')
        index = {'assets': [{'asset_id': 'OTHER-000001', 'library_asset_id': self.asset['id'],
                             'destination': relative, 'category': '其他', 'name': '衣服'}]}
        (root / '资产索引.json').write_text(json.dumps(index), encoding='utf-8')
        self.lib.vault = vault
        for action, category in [('move', '服装三视图'), ('trash', None), ('restore', None)]:
            m.management.manage(self.lib, [self.asset['id']], action, category)
            row = json.loads((root / '资产索引.json').read_text(encoding='utf-8'))['assets'][0]
            self.assertEqual((root / row['destination']).read_bytes(), self.original_bytes)
            self.assertIn(row['destination'], (root / row['destination']).parent.joinpath('资产说明.md').read_text(encoding='utf-8'))
        self.assertEqual(row['category'], '服装三视图')
        self.assertFalse(photo.exists())

    def test_template_and_methods_share_expanded_vault_root(self):
        vault = self.root / 'cloud-vault'
        template = vault / 'wiki/reference/提示词模板' / m.asset_skill.recipe('大头照')['template']
        template.parent.mkdir(parents=True)
        template.write_text('isolated path-resolution fixture', encoding='utf-8')
        with patch.dict(m.os.environ, {'HOME': str(self.root), 'USERPROFILE': str(self.root),
                                       'MSB_VAULT_ROOT': '~/cloud-vault'}):
            from stage_context_cli import _vault_root
            self.assertEqual(_vault_root(None), vault.resolve())
            self.assertEqual(m.asset_skill.template_source('大头照'), template.resolve())

    def test_local_upload_protects_the_actual_vault(self):
        source = self.lib.vault / 'not-yet-indexed.png'
        Image.new('RGB', (80, 60), 'orange').save(source)
        original = source.read_bytes()
        with self.assertRaisesRegex(ValueError, '知识库'):
            self.lib.local_upload_source(source)
        self.assertEqual(source.read_bytes(), original)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lib = m.Library(self.root / 'library', vault=self.root / 'vault')
        self.source = self.root / '衣服参考.png'
        Image.new('RGB', (200, 300), '#ceb3ae').save(self.source)
        self.original_bytes = self.source.read_bytes()
        self.asset, _ = self.lib.import_file(self.source, project='测试来源')

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_preserves_source_and_skips_unchanged(self):
        self.assertTrue(Path(self.asset['original']).is_relative_to(self.lib.vault / '资产库'))
        self.assertTrue(Path(self.asset['original']).is_file())
        self.assertEqual(list((self.lib.root / 'assets' / self.asset['id']).glob('original.*')), [])
        repeated, added = self.lib.import_file(self.source)
        self.assertFalse(added)
        self.assertEqual(repeated['id'], self.asset['id'])
        self.assertEqual(self.source.read_bytes(), self.original_bytes)
        self.assertEqual(Path(self.asset['original']).read_bytes(), self.original_bytes)
        self.assertEqual(self.asset['status'], 'unreviewed')
        self.assertIsNone(self.asset['confirmation'])
        self.source.unlink()
        self.assertTrue(Path(self.asset['original']).exists())

    def test_changed_source_creates_new_copy(self):
        Image.new('RGB', (200, 300), '#003300').save(self.source)
        changed, added = self.lib.import_file(self.source)
        self.assertTrue(added)
        self.assertNotEqual(changed['id'], self.asset['id'])
        self.assertEqual(Path(self.asset['original']).read_bytes(), self.original_bytes)

    def test_selected_pack_is_self_contained_and_not_project_confirmation(self):
        exported = self.lib.export([self.asset['id']])
        data = json.loads(Path(exported['manifest']).read_text(encoding='utf-8'))
        self.assertTrue(Path(data['assets'][0]['original']).is_relative_to(exported['folder']))
        self.assertEqual(data['assets'][0]['status'], 'unreviewed')
        self.assertNotIn('source_narrative', data)

    def test_views_require_user_confirmation_and_topdown_uses_three_views(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '测试房间', 'kind': '环境', 'provider': 'imagegen'})
        with self.assertRaisesRegex(ValueError, '前置'):
            self.lib.prepare(job['id'], 1)
        with self.assertRaisesRegex(ValueError, '前置'):
            self.lib.prepare(job['id'], 3)
        for index in range(3):
            candidate = self.root / f'view{index}.png'
            Image.new('RGB', (320, 180), (index * 50, 80, 70)).save(candidate)
            attached = self.lib.attach(job['id'], index, [candidate])
            self.assertEqual(attached['steps'][index]['status'], 'awaiting_review')
            self.lib.confirm(attached['steps'][index]['outputs'][0])
        request = self.lib.prepare(job['id'], 3)
        self.assertEqual(len(request['reference_paths']), 3)
        self.assertEqual(request['count'], 1)
        with self.assertRaisesRegex(ValueError, '已确认'):
            self.lib.attach(job['id'], 0, [self.source])

    def test_generation_requires_explicit_authorization_and_never_auto_retries(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '连衣裙', 'kind': '服装',
                                   'provider': 'dreamina', 'model': '5.0'})
        with patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '确认'):
                self.lib.submit({'id': job['id'], 'index': 0})
            job['steps'][0]['status'] = 'unknown'
            self.lib.save(job, 'jobs')
            with self.assertRaisesRegex(ValueError, '重复'):
                self.lib.submit({'id': job['id'], 'index': 0, 'authorize': True})
            run.assert_not_called()

    def test_confirmed_view_is_not_replaced_by_confirming_another_candidate(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装', 'provider': 'imagegen'})
        alternatives = []
        for index in range(2):
            path = self.root / f'candidate-{index}.png'
            Image.new('RGB', (320, 180), (50, index * 70, 20)).save(path)
            alternatives.append(path)
        job = self.lib.attach(job['id'], 0, alternatives)
        first, second = job['steps'][0]['outputs']
        self.lib.confirm(first)
        self.lib.confirm(second)
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['selected'], first)

    def test_dreamina_worker_uses_one_image_and_collects_downloaded_candidate(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装',
                                   'provider': 'dreamina', 'model': '5.0Pro'})
        request = self.lib.prepare(job['id'], 0)
        with patch.object(m.subprocess, 'run') as run:
            run.return_value = m.subprocess.CompletedProcess([], 0, '{"submit_id":"test-task-123"}', '')
            self.lib._submit_worker('dreamina.exe', job['id'], 0, request)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--generate_num')+1], '1')
            self.assertEqual(command[command.index('--model_version')+1], '5.0Pro')
            self.assertIn(self.asset['original'], command)
        self.assertEqual(self.lib.get(job['id'], 'jobs')['steps'][0]['submit_id'], 'test-task-123')
        def downloaded(command, **kwargs):
            output = Path(command[command.index('--download_dir')+1]) / 'result.png'
            Image.new('RGB', (320, 180), '#ffeedd').save(output)
            return m.subprocess.CompletedProcess(command, 0, '{"status":"success"}', '')
        with patch.object(m.subprocess, 'run', side_effect=downloaded):
            self.lib._query_worker(job['id'], 0)
        updated = self.lib.get(job['id'], 'jobs')
        self.assertEqual(updated['steps'][0]['status'], 'awaiting_review')
        self.assertEqual(self.lib.get(updated['steps'][0]['outputs'][0])['status'], 'unreviewed')

    def test_query_failures_stop_auto_polling_but_keep_original_task(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装',
                                   'provider': 'dreamina', 'model': '5.0Pro'})
        job['steps'][0].update(status='submitted', submit_id='original-task')
        self.lib.save(job, 'jobs')
        with patch.object(m.subprocess, 'run', side_effect=m.subprocess.TimeoutExpired('query', 90)):
            for attempt in range(3):
                self.lib._query_worker(job['id'], 0)
                step = self.lib.get(job['id'], 'jobs')['steps'][0]
                self.assertEqual(step['query_blocked'], attempt == 2)
                self.assertEqual(step['submit_id'], 'original-task')
        with self.assertRaises(ValueError):
            self.lib.query(job['id'], 0, automatic=True)
        with patch.object(m.threading.Thread, 'start'):
            self.lib.query(job['id'], 0)
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'querying')
        self.assertFalse(step.get('query_blocked', False))
        self.assertEqual(step['submit_id'], 'original-task')

    def test_dreamina_redo_ignores_old_downloads_and_starts_fresh_query_state(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装',
                                   'provider': 'dreamina', 'model': '5.0'})
        legacy = self.lib.root / 'requests' / job['id'] / '0' / 'results'
        legacy.mkdir(parents=True)
        old_path = legacy / 'old-task.png'
        Image.new('RGB', (320, 180), 'red').save(old_path)
        job = self.lib.attach(job['id'], 0, [old_path])
        old_id = job['steps'][0]['outputs'][0]
        self.lib.reject_result({'id': job['id'], 'index': 0, 'redo': True})
        job = self.lib.get(job['id'], 'jobs')
        prompt = '浅灰背景，无头人台展示同一套衣服正面、侧面和背面。'
        job['steps'][0].update(prompt=prompt,
            audit={'status': 'passed', 'prompt': prompt, 'skill_execution': {'version': m.asset_skill.profile()['version']}},
            prompt_approval={'prompt': prompt}, submit_id='old-task', query_blocked=True,
            query_failures=3, query_retry_after=60)
        self.lib.save(job, 'jobs')
        with patch.object(m.threading, 'Thread') as worker, patch.object(m.shutil, 'which', return_value='dreamina.exe'):
            self.lib.submit({'id': job['id'], 'index': 0, 'authorize': True})
        pending = self.lib.get(job['id'], 'jobs')['steps'][0]
        for field in ('submit_id', 'query_blocked', 'query_failures', 'query_retry_after'):
            self.assertNotIn(field, pending)
        run_id = pending['generation_run_id']
        run_folder = self.lib.root / 'requests' / job['id'] / '0' / 'dreamina' / run_id
        with patch.object(m.subprocess, 'run', return_value=m.subprocess.CompletedProcess([], 0, '{"submit_id":"new-task"}', '')):
            self.lib._submit_worker(*worker.call_args.kwargs['args'])
        self.assertTrue((run_folder / 'request.json').is_file())
        self.assertTrue((run_folder / 'submit.log').is_file())
        with patch.object(m.subprocess, 'run', return_value=m.subprocess.CompletedProcess([], 0, '{"status":"running"}', '')) as query:
            self.lib._query_worker(job['id'], 0)
        command = query.call_args.args[0]
        self.assertEqual(Path(command[command.index('--download_dir') + 1]), run_folder / 'results')
        waiting = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(waiting['status'], 'submitted')
        self.assertEqual(waiting['outputs'], [])
        self.assertFalse(waiting['query_blocked'])
        self.assertTrue(old_path.is_file())
        self.assertIn('trashed_at', self.lib.get(old_id))
        def downloaded(command, **kwargs):
            destination = Path(command[command.index('--download_dir') + 1]) / 'new-task.png'
            Image.new('RGB', (320, 180), 'blue').save(destination)
            return m.subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(m.subprocess, 'run', side_effect=downloaded):
            self.lib._query_worker(job['id'], 0)
        finished = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(finished['status'], 'awaiting_review')
        self.assertEqual(len(finished['outputs']), 1)
        self.assertNotIn(old_id, finished['outputs'])

    def test_legacy_dreamina_query_ignores_shared_results_and_queries_original_task(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装',
                                   'provider': 'dreamina', 'model': '5.0'})
        job['steps'][0].update(status='submitted', submit_id='legacy-current-task')
        self.lib.save(job, 'jobs')
        old_folder = self.lib.root / 'requests' / job['id'] / '0' / 'results'
        old_folder.mkdir(parents=True)
        old_path = old_folder / 'different-task.png'
        Image.new('RGB', (320, 180), 'red').save(old_path)
        with patch.object(m.subprocess, 'run', return_value=m.subprocess.CompletedProcess([], 0, '', '')) as query:
            self.lib._query_worker(job['id'], 0)
        command = query.call_args.args[0]
        self.assertEqual(command[command.index('--submit_id') + 1], 'legacy-current-task')
        scoped = old_folder.parent / 'dreamina' / 'legacy' / 'legacy-current-task' / 'results'
        self.assertEqual(Path(command[command.index('--download_dir') + 1]), scoped)
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'submitted')
        self.assertEqual(step['outputs'], [])
        def downloaded(command, **kwargs):
            Image.new('RGB', (320, 180), 'blue').save(Path(command[command.index('--download_dir') + 1]) / 'current.png')
            return m.subprocess.CompletedProcess(command, 0, '', '')
        with patch.object(m.subprocess, 'run', side_effect=downloaded):
            self.lib._query_worker(job['id'], 0)
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertEqual(step['status'], 'awaiting_review')
        self.assertEqual(len(step['outputs']), 1)
        self.assertEqual(self.lib.get(step['outputs'][0])['source'], str(scoped / 'current.png'))
        self.assertTrue(old_path.is_file())

    def test_failed_query_command_pauses_while_pending_result_keeps_polling(self):
        job = self.lib.create_job({'source_id': self.asset['id'], 'subject': '衣服', 'kind': '服装',
                                   'provider': 'dreamina', 'model': '5.0Pro'})
        job['steps'][0].update(status='submitted', submit_id='original-task')
        self.lib.save(job, 'jobs')
        with patch.object(m.subprocess, 'run', return_value=m.subprocess.CompletedProcess([], 0, '', '')):
            for _ in range(4):
                self.lib._query_worker(job['id'], 0)
        self.assertFalse(self.lib.get(job['id'], 'jobs')['steps'][0]['query_blocked'])
        with patch.object(m.subprocess, 'run', return_value=m.subprocess.CompletedProcess([], 1, '', 'query rejected')):
            self.lib._query_worker(job['id'], 0)
        step = self.lib.get(job['id'], 'jobs')['steps'][0]
        self.assertTrue(step['query_blocked'])
        self.assertEqual(step['submit_id'], 'original-task')

    def test_http_rejects_foreign_origins_and_unauthorized_writes(self):
        server = m.ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        server.library, server.token = self.lib, 'test-token'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url + '/api/state') as response:
                self.assertEqual(len(json.load(response)['assets']), 1)
            for request in [Request(url + '/api/state', headers={'Origin': 'https://foreign.invalid'}),
                            Request(url + '/api/confirm', data=json.dumps({'id': self.asset['id']}).encode())]:
                with self.assertRaises(HTTPError) as context:
                    urlopen(request)
                self.assertEqual(context.exception.code, 403)
            self.assertEqual(self.lib.get(self.asset['id'])['status'], 'unreviewed')
        finally:
            server.shutdown()
            server.server_close()

    def test_http_local_upload_returns_one_asset_and_preserves_source(self):
        server = m.ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        server.library, server.token = self.lib, 'test-token'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        image = self.root / 'http-upload.png'
        Image.new('RGB', (400, 400), 'purple').save(image)
        recognition = {'category': '大头照', 'confidence': '高', 'description': '人像',
                       'focus_x': 0.5, 'focus_y': 0.5, 'critical_box': [0.2, 0.1, 0.8, 0.9]}
        try:
            request = Request(f'http://127.0.0.1:{server.server_port}/api/upload-local',
                              data=json.dumps({'multiple': False}).encode(),
                              headers={'X-Library-Token': 'test-token'}, method='POST')
            with patch.object(m.visual_asset_upload, 'recognize', return_value=recognition), \
                 patch.object(m.visual_asset_upload, 'pick_local_files', return_value=[str(image)]):
                with urlopen(request) as response:
                    result = json.load(response)['results'][0]
            self.assertEqual(result['upload_crop']['status'], 'cropped')
            self.assertEqual(result['status'], 'unreviewed')
            self.assertFalse(result['upload_processing']['source_deleted'])
            self.assertTrue(image.exists())
            self.assertEqual(len(self.lib.items()), 2)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
