"""Focused stdlib regression tests; no production approvals or real project writes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.core.candidate_test import configure, context, require_production
from scripts.core.project_state import load_project_state, write_project_state
from scripts.init_desktop_project import initialize


class CandidateStageEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = initialize(Path(self.tmp.name) / 'isolated')

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/stage_context_cli.py'),
                               *args, '--project', str(self.project)],
                              capture_output=True, text=True)

    def configure(self, **kwargs):
        return configure(self.project, 'fixture-run', 'fixture explicit test authorization', [], **kwargs)

    def snapshot(self):
        return {str(p.relative_to(self.project)): p.read_bytes()
                for p in self.project.rglob('*') if p.is_file()}

    def test_explicit_cli_stage_and_handoff_never_approve(self):
        result = self.cli('candidate-test', '--run-id', 'fixture-run', '--authorization-ref',
                          'fixture explicit test authorization', '--skill', 'creative-director')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = load_project_state(self.project)
        self.assertEqual(state['current_stage'], 'creative-director')
        self.assertEqual(state['current_artifacts'], [])
        self.assertEqual(state['user_confirmations'], [])
        self.assertIsNone(state['next_stage'])
        status = self.cli('status')
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        report = json.loads(status.stdout)
        self.assertTrue(report['candidate_test_allowed'])
        self.assertFalse(report['production_allowed'])
        before = load_project_state(self.project)
        handoff = self.cli('handoff', '--candidate-test', '--to-skill', 'storyboard-planner')
        self.assertEqual(handoff.returncode, 0, handoff.stdout + handoff.stderr)
        self.assertTrue(json.loads(handoff.stdout)['production_blocked'])
        self.assertEqual(before, load_project_state(self.project))
        self.assertNotEqual(self.cli('handoff', '--to-skill', 'storyboard-planner').returncode, 0)
        with self.assertRaises(ValueError): require_production(self.project)
        self.configure(current_stage='storyboard-planner')
        self.assertEqual(context(self.project)['run_id'], 'fixture-run')
        self.assertIsNone(load_project_state(self.project)['next_stage'])

    def test_omitting_stage_does_not_guess(self):
        self.configure()
        self.assertEqual(load_project_state(self.project)['current_stage'], '')
        report = json.loads(self.cli('status').stdout)
        self.assertFalse(report['candidate_test_allowed'])
        self.assertIn('候选测试 current_stage 缺失；不能生成隔离交接', report['blockers'])

    def test_invalid_stage_rejected_without_writes(self):
        for skill in ('made-up-stage', '', '../skills/creative-director', '/tmp', ' creative-director'):
            with self.subTest(skill=skill):
                before = self.snapshot()
                with self.assertRaises(ValueError): self.configure(current_stage=skill)
                self.assertEqual(before, self.snapshot())

    def test_unknown_existing_stage_not_silently_repaired(self):
        state = load_project_state(self.project)
        state['current_stage'] = 'unknown-stage'
        write_project_state(self.project, state)
        before = self.snapshot()
        with self.assertRaises(ValueError): self.configure(current_stage='creative-director')
        self.assertEqual(before, self.snapshot())

    def test_existing_production_state_rejected_without_writes(self):
        for field, value in (('current_artifacts', ['formal-artifact']),
                             ('user_confirmations', [{'status': 'user-confirmed'}]),
                             ('finalization', {'record_path': 'formal'})):
            with self.subTest(field=field):
                state = load_project_state(self.project)
                state[field] = value
                write_project_state(self.project, state)
                before = self.snapshot()
                with self.assertRaises(ValueError): self.configure(current_stage='creative-director')
                self.assertEqual(before, self.snapshot())
                state[field] = [] if field != 'finalization' else None
                write_project_state(self.project, state)

    def test_identity_authorization_and_input_rejections(self):
        self.configure(current_stage='creative-director')
        for run, auth, ids in (('other-run', 'fixture explicit test authorization', []),
                               ('fixture-run', 'different authorization', []),
                               ('fixture-run', '', []), ('', 'authorization', []),
                               ('fixture-run', 'fixture explicit test authorization', ['unknown'])):
            with self.subTest(run=run, auth=auth, ids=ids):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    configure(self.project, run, auth, ids, current_stage='storyboard-planner')
                self.assertEqual(before, self.snapshot())
        self.assertNotEqual(self.cli('candidate-test', '--run-id', 'fixture-run',
                                    '--skill', 'creative-director').returncode, 0)

    def test_registered_input_identity_and_candidate_flags_preserved(self):
        from scripts.core.artifacts import register_workspace_artifact, load_manifest
        from scripts.core.candidate_test import resolve_inputs
        self.configure(current_stage='creative-director')
        (self.project / 'source.json').write_text('{"status":"candidate","revision":"r1"}')
        register_workspace_artifact(self.project, {
            'artifact_id': 'source-r1', 'logical_id': 'source.fixture', 'type': 'source.evidence',
            'path_or_url': 'source.json', 'revision': 'r1'})
        configure(self.project, 'fixture-run', 'fixture explicit test authorization',
                  ['source-r1'], current_stage='storyboard-planner')
        self.assertEqual(context(self.project)['input_artifact_ids'], ['source-r1'])
        self.assertEqual(resolve_inputs(self.project)[0]['artifact_id'], 'source-r1')
        item = load_manifest(self.project)['artifacts'][0]
        self.assertEqual(item['status'], 'candidate')
        self.assertEqual(item['review_status'], 'unreviewed')
        self.assertTrue(item['production_blocked'])
        self.assertFalse(item['user_approved'])
        self.assertEqual(load_project_state(self.project)['current_artifacts'], [])

    def test_next_stage_preserved_and_mismatched_target_rejected(self):
        state = load_project_state(self.project)
        state['next_stage'] = 'storyboard-planner'
        write_project_state(self.project, state)
        self.configure(current_stage='creative-director')
        self.assertEqual(load_project_state(self.project)['next_stage'], 'storyboard-planner')
        self.assertNotEqual(self.cli('handoff', '--candidate-test', '--to-skill',
                                    'voiceover-to-video-script').returncode, 0)
        self.assertNotEqual(self.cli('handoff', '--candidate-test', '--to-skill',
                                    'made-up-stage').returncode, 0)


if __name__ == '__main__':
    unittest.main()
