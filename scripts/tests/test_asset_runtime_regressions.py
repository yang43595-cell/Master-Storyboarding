"""Runtime sequences use isolated catalogs/projects; no model or real Vault calls."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from test_visual_asset_projects import fixture, plan_result
import visual_asset_library as library_module
import visual_asset_projects as project_module
from core.artifacts import load_manifest, save_manifest
from core.io import write_json
from core.project_state import load_project_state, write_project_state
from stage_context_cli import StageContextError, _confirmed_handoff_inputs


class AssetRuntimeRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.lib, self.project, self.asset = fixture(self.base)
        self.service = self.lib.projects
        self.root = Path(self.project['path'])
        path = self.root / 'narrative.json'
        narrative = project_module.read(path)
        narrative['asset_requirements'] = narrative['asset_requirements'][:1]
        project_module.write(path, narrative)
        manifest = load_manifest(self.root)
        next(item for item in manifest['artifacts'] if item['artifact_id'] == 'narrative-r01')['checksum'] = project_module.digest(path)
        # Simulate a pre-existing manually edited legacy project record. The
        # supported save_manifest API now refuses rebinding formal current IDs.
        write_json(self.root / '00_项目管理/artifact_manifest.json', manifest)
        self.service.sync(self.project['id'])
        project = self.service.get(self.project['id'])
        result = plan_result()
        result['rows'] = result['rows'][:1]
        self.service.apply_plan(project, project['rows'], result, {'mode': 'project'})
        self.job_id = self.service.detail(self.project['id'])['rows'][0]['job']['id']

    def tearDown(self):
        self.temp.cleanup()

    def job(self):
        return self.lib.get(self.job_id, 'jobs')

    def confirm(self, index, asset=None):
        self.service.select({'job_id': self.job_id, 'index': index,
                             'asset_id': (asset or self.asset)['id']})
        self.service.confirm({'job_id': self.job_id, 'index': index, 'confirm': True})

    def confirmed_scene(self):
        for index in range(4):
            self.confirm(index)
        library_module.visual_asset_backup.run(self.lib, library_module.management)

    def mode(self, mode):
        return self.service.scene_output_mode({'project_id': self.project['id'],
                                               'ast_id': 'AST-ROOM', 'mode': mode})

    def test_job_cannot_modify_or_confirm_another_selected_project(self):
        before = self.job()
        payload = {'project_id': 'different-project', 'job_id': self.job_id,
                   'index': 0, 'asset_id': self.asset['id']}
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            self.service.select(payload)
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            self.service.action({'action': 'select', **payload})
        self.assertEqual(self.job(), before)
        self.service.select({**payload, 'project_id': self.project['id']})
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            self.service.action({'action': 'confirm', 'confirm': True, **payload})
        self.assertEqual(self.job()['steps'][0]['status'], 'awaiting_review')
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            self.service.action({'action': 'reopen', 'job_id': self.job_id, 'index': 0})

    def test_library_job_operations_reject_project_mismatch_before_any_side_effect(self):
        payload = {'id': self.job_id, 'index': 0, 'project_id': 'another-project',
                   'prompt': '当前场景提示词', 'authorize': True, 'redo': True}
        operations = [lambda: self.lib.save_prompt(payload), lambda: self.lib.audit_prompt(payload),
            lambda: self.lib.reject_result(payload), lambda: self.lib.reset_imagegen(payload),
            lambda: self.lib.recover_imagegen(payload), lambda: self.lib.submit_imagegen(payload),
            lambda: self.lib.submit(payload),
            lambda: self.lib.prepare(self.job_id, 0, project_id=payload['project_id']),
            lambda: self.lib.attach(self.job_id, 0, [], project_id=payload['project_id']),
            lambda: self.lib.query(self.job_id, 0, project_id=payload['project_id'])]
        before = self.job()
        before_assets = self.lib.items()
        with patch.object(self.lib, 'save', wraps=self.lib.save) as save, \
                patch.object(library_module.threading.Thread, 'start') as start:
            for operation in operations:
                with self.subTest(operation=operations.index(operation)):
                    with self.assertRaisesRegex(ValueError, '不属于当前项目'):
                        operation()
        save.assert_not_called()
        start.assert_not_called()
        self.assertEqual(self.job(), before)
        self.assertEqual(self.lib.items(), before_assets)

    def test_query_mismatch_preserves_original_task_and_unscoped_protocol_still_works(self):
        job = self.job()
        job['steps'][0].update(status='submitted', submit_id='original-task',
                              query_failures=2, query_blocked=True)
        self.lib.save(job, 'jobs')
        with patch.object(library_module.threading.Thread, 'start') as start:
            with self.assertRaisesRegex(ValueError, '不属于当前项目'):
                self.lib.query(self.job_id, 0, project_id='another-project')
            self.assertEqual(self.job(), job)
            start.assert_not_called()
            self.lib.query(self.job_id, 0)
            start.assert_called_once()
        self.assertEqual(self.job()['steps'][0]['status'], 'querying')

    def test_matching_project_prompt_and_independent_asset_protocol_remain_available(self):
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            self.lib.save_prompt({'id': self.job_id, 'index': 0, 'project_id': None,
                                  'prompt': '不应写入'})
        self.lib.save_prompt({'id': self.job_id, 'index': 0, 'project_id': self.project['id'],
                              'prompt': '当前场景提示词'})
        self.assertEqual(self.job()['steps'][0]['prompt'], '当前场景提示词')
        independent = self.lib.create_job({'source_id': self.asset['id'], 'subject': '独立房间', 'kind': '环境'})
        self.lib.save_prompt({'id': independent['id'], 'index': 0, 'prompt': '独立房间提示词'})
        self.assertEqual(self.lib.get(independent['id'], 'jobs')['steps'][0]['prompt'], '独立房间提示词')
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            self.lib.save_prompt({'id': independent['id'], 'index': 0, 'project_id': self.project['id'],
                                  'prompt': '不应写入'})

    def test_http_job_routes_preserve_project_ownership_before_operating(self):
        server = library_module.ThreadingHTTPServer(('127.0.0.1', 0), library_module.Handler)
        server.library, server.token = self.lib, 'runtime-test-token'
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        before = self.job()
        try:
            for endpoint in ('prompt', 'prepare', 'attach', 'query', 'imagegen-reset', 'reject-result'):
                payload = {'id': self.job_id, 'index': 0, 'project_id': 'another-project',
                           'prompt': '不应写入', 'paths': [], 'redo': True}
                request = Request(f'http://127.0.0.1:{server.server_port}/api/{endpoint}',
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'X-Library-Token': server.token}, method='POST')
                with self.subTest(endpoint=endpoint):
                    with self.assertRaises(HTTPError) as failure:
                        urlopen(request, timeout=5)
                    self.assertEqual(failure.exception.code, 400)
                    self.assertIn('不属于当前项目', json.load(failure.exception)['error'])
                    self.assertEqual(self.job(), before)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_single_delivery_keeps_production_graph_and_invalidates_hidden_angles(self):
        self.confirmed_scene()
        old = copy.deepcopy(self.job()['steps'])
        self.mode('single')
        self.assertEqual(self.job()['steps'][3]['dependencies'], ['view0', 'view1', 'view2'])
        self.service.reopen({'job_id': self.job_id, 'index': 0})
        self.assertTrue(all(s['status'] == 'waiting' for s in self.job()['steps']))
        source = self.base / 'replacement.png'
        Image.new('RGB', (400, 300), 'red').save(source)
        replacement, _ = self.lib.import_file(source, category='场景')
        self.confirm(0, replacement)
        self.mode('four')
        job = self.job()
        self.assertEqual([s['status'] for s in job['steps']], ['confirmed', 'waiting', 'waiting', 'waiting'])
        for index in range(1, 4):
            self.assertEqual(job['steps'][index]['history'][-1]['project_confirmation'], old[index]['project_confirmation'])
            self.assertEqual(job['steps'][index]['outputs'], [])
        library_module.visual_asset_backup.run(self.lib, library_module.management)
        with self.assertRaisesRegex(ValueError, '尚未逐项确认'):
            self.service.export({'project_id': self.project['id'], 'confirm': True})

    def test_quantity_switch_without_new_inputs_preserves_confirmed_angles(self):
        self.confirmed_scene()
        receipts = [s['project_confirmation'] for s in self.job()['steps']]
        self.mode('single')
        self.mode('four')
        self.assertEqual([s['project_confirmation'] for s in self.job()['steps']], receipts)

    def test_legacy_erased_dependency_graph_requires_angle_reconfirmation(self):
        self.confirmed_scene()
        job = self.job()
        for index, step in enumerate(job['steps']):
            step['dependencies'] = []
            step['required'] = index == 0
            step['project_confirmation'].pop('dependency_confirmations', None)
        job['scene_output_mode'] = 'single'
        self.lib.save(job, 'jobs')
        self.mode('four')
        self.assertEqual([s['status'] for s in self.job()['steps']], ['confirmed', 'waiting', 'waiting', 'waiting'])
        self.assertTrue(all(s['history'][-1]['project_confirmation'] for s in self.job()['steps'][1:]))

    def test_export_rejects_angle_bound_to_another_confirmation_version(self):
        self.confirmed_scene()
        job = self.job()
        job['steps'][0]['project_confirmation']['id'] = 'different-confirmation'
        self.lib.save(job, 'jobs')
        with self.assertRaisesRegex(ValueError, '确认版本已变化'):
            self.service.export({'project_id': self.project['id'], 'confirm': True})

    def test_changed_confirmed_original_blocks_dependencies_reconfirmation_and_first_export(self):
        from core.artifacts import digest
        self.confirm(0)
        receipt = copy.deepcopy(self.job()['steps'][0]['project_confirmation'])
        image = Path(self.lib.get(self.asset['id'])['original'])
        self.assertEqual(receipt['image_sha256'], digest(image))
        Image.new('RGB', (400, 300), 'red').save(image)
        changed = image.read_bytes()
        with self.assertRaisesRegex(ValueError, '图片内容已变化'):
            self.service.dependencies(self.job(), self.job()['steps'][1])
        with self.assertRaisesRegex(ValueError, '图片内容已变化'):
            self.service.confirm({'job_id': self.job_id, 'index': 0, 'confirm': True})
        with self.assertRaisesRegex(ValueError, '图片内容已变化'):
            self.service.export({'project_id': self.project['id'], 'confirm': True})
        self.assertEqual(self.job()['steps'][0]['project_confirmation'], receipt)
        self.assertEqual(image.read_bytes(), changed)

    def test_changed_export_copy_blocks_downstream_cached_handoff_and_restart(self):
        from core.asset_refs import require_images
        self.confirmed_scene()
        exported = self.service.export({'project_id': self.project['id'], 'confirm': True})
        manifest = project_module.read(Path(exported['manifest']))
        asset = manifest['assets'][0]
        output = asset['confirmed_outputs'][0]
        request = asset['asset_id'] + '@' + output['output_id']
        receipt_path = Path(output['user_confirmation_ref'].partition('#')[0])
        receipt = receipt_path.read_bytes()
        original = Path(self.lib.get(self.asset['id'])['original'])
        before = original.read_bytes()
        path = Path(output['path'])
        self.assertTrue(require_images(self.root, [request])[0])
        Image.new('RGB', (400, 300), 'red').save(path)
        changed = path.read_bytes()
        self.assertFalse(require_images(self.root, [request])[0])
        for service in (self.service, library_module.Library(self.lib.root, vault=self.lib.vault).projects):
            with self.assertRaisesRegex(ValueError, '已导出确认图片内容已变化'):
                service.export({'project_id': self.project['id'], 'confirm': True})
            with self.assertRaisesRegex(ValueError, '已导出确认图片内容已变化'):
                service.continue_project(self.project['id'])
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(receipt_path.read_bytes(), receipt)
        self.assertEqual(path.read_bytes(), changed)

    def test_changed_vault_original_does_not_invalidate_an_intact_export_copy(self):
        from core.asset_refs import require_images
        self.confirmed_scene()
        exported = self.service.export({'project_id': self.project['id'], 'confirm': True})
        asset = project_module.read(Path(exported['manifest']))['assets'][0]
        output = asset['confirmed_outputs'][0]
        request = asset['asset_id'] + '@' + output['output_id']
        image = Path(self.lib.get(self.asset['id'])['original'])
        Image.new('RGB', (400, 300), 'red').save(image)
        self.assertTrue(require_images(self.root, [request])[0])
        with self.assertRaisesRegex(ValueError, '图片内容已变化'):
            self.service.export({'project_id': self.project['id'], 'confirm': True})

    def test_cached_export_requires_unchanged_registered_manifest(self):
        from core.artifacts import digest
        from core.asset_refs import require_images
        self.confirmed_scene()
        exported = self.service.export({'project_id': self.project['id'], 'confirm': True})
        path = Path(exported['manifest'])
        manifest = project_module.read(path)
        asset = manifest['assets'][0]
        request = asset['asset_id'] + '@' + asset['confirmed_outputs'][0]['output_id']
        entry = next(a for a in load_manifest(self.root)['artifacts'] if a['artifact_id'] == exported['artifact_id'])
        self.assertEqual(entry['checksum'], digest(path))
        # A stale unregistered historical directory must never rescue a
        # broken current manifest, even if its old image still exists.
        legacy = self.root / '04_资产/母版/R00_legacy/visual-asset-manifest.json'
        project_module.write(legacy, manifest)
        for fault in ('empty_assets', 'different_path_same_count', 'missing_file'):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(manifest)
                if fault == 'empty_assets':
                    changed['assets'] = []
                    project_module.write(path, changed)
                elif fault == 'different_path_same_count':
                    changed['assets'][0]['confirmed_outputs'][0]['path'] = asset['confirmed_outputs'][1]['path']
                    project_module.write(path, changed)
                else:
                    path.unlink()
                with self.assertRaisesRegex(ValueError, '导出清单内容已变化或缺失'):
                    self.service.export({'project_id': self.project['id'], 'confirm': True})
                with self.assertRaisesRegex(ValueError, '导出清单内容已变化或缺失'):
                    require_images(self.root, [request])
        # A checksum match alone cannot restore an invalidated adoption.
        project_module.write(path, manifest)
        registered = load_manifest(self.root)
        next(a for a in registered['artifacts'] if a['artifact_id'] == exported['artifact_id'])['status'] = 'stale'
        save_manifest(self.root, registered)
        with self.assertRaisesRegex(ValueError, '登记已失效'):
            self.service.export({'project_id': self.project['id'], 'confirm': True})

    def test_legacy_restored_graph_does_not_claim_new_basis_or_export_after_restart(self):
        self.confirmed_scene()
        job = self.job()
        job['steps'][0]['project_confirmation']['at'] = '2026-10-03T02:00:00+00:00'
        for step in job['steps'][1:]:
            step['project_confirmation']['at'] = '2026-10-03T01:00:00+00:00'
            step['project_confirmation'].pop('dependency_confirmations')
        # Old mode switching restored the graph after replacing the first image,
        # so matching dependency keys still cannot prove the old review inputs.
        self.lib.save(job, 'jobs')
        restarted = library_module.Library(self.lib.root, vault=self.lib.vault)
        with self.assertRaisesRegex(ValueError, '缺少前置图片版本依据'):
            restarted.projects.export({'project_id': self.project['id'], 'confirm': True})
        self.mode('four')
        self.assertEqual([s['status'] for s in self.job()['steps']], ['confirmed', 'waiting', 'waiting', 'waiting'])
        for step in self.job()['steps'][1:]:
            self.assertNotIn('dependency_confirmations', step['history'][-1]['project_confirmation'])

    def test_cached_legacy_mixed_export_and_continue_share_basis_validation(self):
        self.confirmed_scene()
        out = self.service.export({'project_id': self.project['id'], 'confirm': True})
        job = self.job()
        for step in job['steps'][1:]:
            step['project_confirmation'].pop('dependency_confirmations')
        self.lib.save(job, 'jobs')
        restarted = library_module.Library(self.lib.root, vault=self.lib.vault)
        with self.assertRaisesRegex(ValueError, '缺少前置图片版本依据'):
            restarted.projects.export({'project_id': self.project['id'], 'confirm': True})
        with patch.object(project_module.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, '缺少前置图片版本依据'):
                restarted.projects.continue_project(self.project['id'])
        command.assert_not_called()
        self.assertTrue(Path(out['manifest']).is_file())

    def test_upload_final_metadata_is_in_first_durable_record_and_survives_restart(self):
        source = self.base / 'desktop-input.png'
        Image.new('RGB', (1600, 900), 'blue').save(source)
        recognition = {'confidence': '高', 'category': '物品', 'focus_x': .5, 'focus_y': .5,
                       'critical_box': [0, 0, 1, 1], 'asset_name': '识别后的杯子名字', 'description': '杯子'}
        with patch.object(library_module.visual_asset_upload, 'recognize', return_value=recognition), \
                patch.object(library_module.management, 'write_index', wraps=library_module.management.write_index) as writer:
            result = self.lib.upload_local_file(source, category='物品')
        self.assertEqual(writer.call_count, 1)
        before = self.lib.get(result['id'])
        index = project_module.read(self.lib.vault / '资产库/资产索引.json')
        record = next(r['record'] for r in index['assets'] if r['library_asset_id'] == result['id'])
        for key in ('name', 'source', 'name_history', 'name_source', 'upload_recognition', 'upload_crop'):
            self.assertEqual(record[key], before[key])
        self.assertEqual(record['source'], str(source))
        self.assertTrue(source.is_file())
        restarted = library_module.Library(self.lib.root, vault=self.lib.vault)
        after = restarted.get(result['id'])
        for key in ('name', 'source', 'upload_recognition', 'upload_crop'):
            self.assertEqual(after[key], before[key])

    def test_export_uses_verified_project_copy_when_import_location_disappears(self):
        self.confirmed_scene()
        original_source = self.asset['source']
        Path(original_source).unlink()
        out = self.service.export({'project_id': self.project['id'], 'confirm': True})
        manifest = project_module.read(out['manifest'])
        for reference in manifest['assets'][0]['reference_paths']:
            self.assertTrue(Path(reference).is_file())
            self.assertTrue(Path(reference).is_relative_to(self.root))
        self.assertEqual(self.lib.get(self.asset['id'])['source'], original_source)

    def stale_director(self):
        path = self.root / 'director.md'
        path.write_text('旧导演产物', encoding='utf-8')
        manifest = load_manifest(self.root)
        manifest['artifacts'].append({'artifact_id': 'director-r01', 'logical_id': 'director.shot-plan',
            'type': 'shot-plan', 'revision': 'R01', 'status': 'current', 'user_approved': True,
            'path_or_url': str(path), 'checksum': project_module.digest(path),
            'depends_on': ['visual.asset.manifest']})
        save_manifest(self.root, manifest)
        state = load_project_state(self.root)
        state['current_artifacts'].append('director-r01')
        state['user_confirmations'].append({'artifact_ids': ['director-r01'], 'at': project_module.now()})
        state['next_stage'] = 'storyboard-planner'
        write_project_state(self.root, state)

    def test_export_removes_stale_downstream_ids_but_legacy_formal_source_blocks_handoff(self):
        self.confirmed_scene()
        self.stale_director()
        out = self.service.export({'project_id': self.project['id'], 'confirm': True})
        state = load_project_state(self.root)
        self.assertNotIn('director-r01', state['current_artifacts'])
        self.assertIn(out['artifact_id'], state['current_artifacts'])
        self.assertEqual(state['next_stage'], 'storyboard-planner')
        with self.assertRaisesRegex(StageContextError, 'story_paragraphs'):
            _confirmed_handoff_inputs(self.root, 'storyboard-planner')

    def test_reopen_clears_stale_export_selection_without_deleting_old_confirmation(self):
        self.confirmed_scene()
        out = self.service.export({'project_id': self.project['id'], 'confirm': True})
        self.stale_director()
        before = load_project_state(self.root)['user_confirmations']
        self.service.reopen({'job_id': self.job_id, 'index': 0})
        state = load_project_state(self.root)
        self.assertNotIn(out['artifact_id'], state['current_artifacts'])
        self.assertNotIn('director-r01', state['current_artifacts'])
        self.assertEqual(state['user_confirmations'], before)
        self.assertTrue(Path(out['manifest']).is_file())


if __name__ == '__main__':
    unittest.main()
