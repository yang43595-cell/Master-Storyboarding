"""Source metadata is not a business package; candidate boundaries still apply."""
import tempfile
import unittest
from pathlib import Path

from scripts.core import artifacts, candidate_test as mode
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state, write_project_state


class CandidateSourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        write_project_state(self.project, new_project_state(current_stage='creative-director'))
        mode.configure(self.project, 'test-run', 'synthetic explicit test authorization', [])

    def add(self, ident, kind, payload, depends_on=()):
        path = self.project / (ident + '.json')
        write_json(path, payload)
        artifacts.register_workspace_artifact(self.project, dict(
            artifact_id=ident, logical_id=kind, type=kind, path_or_url=path.name,
            revision='R01', depends_on=list(depends_on)))
        selected = mode.context(self.project)['input_artifact_ids'] + [ident]
        mode.configure(self.project, 'test-run', 'synthetic explicit test authorization', selected)
        return path

    def evidence(self):
        return {'status': 'candidate_unreviewed',
                'source_narrative': {'path': 'historical/R02.json', 'sha256': 'historical-reference'},
                'source_storyboard': {'path': 'historical/director.json'}}

    def test_source_metadata_is_not_a_candidate_dependency(self):
        self.add('E', 'source.evidence', self.evidence())
        result = mode.resolve_inputs(self.project)
        self.assertEqual([a['artifact_id'] for a in result], ['E'])
        self.assertTrue(result[0]['production_blocked'])
        with self.assertRaises(ValueError):
            mode.require_production(self.project)

    def test_business_status_and_revision_dependencies_remain_strict(self):
        path = self.add('B', 'storyboard.package', self.evidence())
        with self.assertRaisesRegex(ValueError, '不可宣称确认'):
            mode.resolve_inputs(self.project)
        write_json(path, {'status': 'candidate', 'source_narrative': {'path': 'historical.json'}})
        with self.assertRaisesRegex(ValueError, '准确 revision'):
            mode.resolve_inputs(self.project)

    def test_evidence_cannot_claim_confirmation(self):
        self.add('E', 'source.evidence', {'status': 'confirmed'})
        with self.assertRaisesRegex(ValueError, '不可宣称确认'):
            mode.resolve_inputs(self.project)

    def test_explicit_evidence_dependencies_still_resolve(self):
        self.add('N', 'narrative.package', {'status': 'candidate', 'revision': 'R01'})
        self.add('E', 'source.evidence', self.evidence(), ['narrative.package'])
        self.assertEqual([a['artifact_id'] for a in mode.resolve_inputs(self.project, ['E'])], ['N', 'E'])
        manifest = artifacts.load_manifest(self.project)
        manifest['artifacts'][0]['review_status'] = 'approved'
        artifacts.save_manifest(self.project, manifest)
        with self.assertRaisesRegex(ValueError, '未确认候选'):
            mode.resolve_inputs(self.project, ['E'])

    def test_evidence_bytes_and_presence_are_checked(self):
        path = self.add('E', 'source.evidence', self.evidence())
        manifest = artifacts.load_manifest(self.project)
        manifest['artifacts'][0]['checksum'] = artifacts.digest(path)
        artifacts.save_manifest(self.project, manifest)
        mode.resolve_inputs(self.project)
        write_json(path, {'status': 'candidate_unreviewed', 'changed': True})
        with self.assertRaisesRegex(ValueError, '偏离登记字节'):
            mode.resolve_inputs(self.project)
        path.unlink()
        with self.assertRaisesRegex(ValueError, '缺失'):
            mode.resolve_inputs(self.project)


if __name__ == '__main__':
    unittest.main()
