"""Synthetic offline reference branches; no browser, models or media generation."""
import copy
import tempfile
import unittest
from pathlib import Path

from scripts.canvas_readback import compare, digest, validate_submission
from scripts.core import artifacts, quality_evidence as q, revision_control as rc
from scripts.core.delivery_files import discover_dependencies
from scripts.core.io import write_json
from scripts.core.media_types import MEDIA_EXTENSIONS
from scripts.core.native_references import reference_readback_status
from scripts.core.project_state import new_project_state, write_project_state


def text_delivery(root):
    write_json(root/'source.json', {'synthetic': 'source only, not a user approval'})
    sha = artifacts.digest(root/'source.json')
    source = dict(artifact_id='SRC', revision='R1', sha256=sha)
    entry = dict(**source, logical_id='fixture.source', path_or_url='source.json',
                 checksum=sha, status='current', user_approved=True, depends_on=[])
    write_json(root/artifacts.MANIFEST_PATH, {'artifacts':[entry]})
    state = new_project_state()
    state.update(current_artifacts=['SRC'], user_confirmations=[
        dict(scope='artifact', artifact_ids=['SRC'], approval_ref='SYNTHETIC ONLY')])
    write_project_state(root,state)
    row = dict(node_id='D', prompt='A simple abstract color scene.', parameters={'duration':4},
               duration_bounds=[2,8], shot_durations=[4], source_shot_ids=['S1'],
               ordered_media=[], reference_checks=[], required_entities=[],
               required_reference_roles={}, continuity={'event_id':'E'})
    row['quality_evidence'] = dict(status='pass',observer='SYNTHETIC ONLY',checked_at='2026-10-30T10:00:00Z',
        checks={k:dict(result='pass',observation='Synthetic assertion, not pixels',locator='fixture') for k in q.DRAFT_ASPECTS},
        input_sha256=q.fingerprint(q.draft_binding(row)))
    frozen=dict(target_url='https://fixture.invalid/text-only',revision='R2',source_artifacts=[source],drafts=[row])
    observed=copy.deepcopy(frozen)
    observed.update(phase='after_refresh',frozen_sha256=digest(frozen))
    observed['drafts'][0]['native_mentions']=[]
    return frozen,observed


class ReferenceDeliveryContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='SYNTHETIC-ref-delivery-')
        self.root=Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_text_only_preflight_and_actual_empty_readback(self):
        f,o=text_delivery(self.root)
        self.assertTrue(validate_submission(f,self.root)['ok'])
        report=compare(f,o,self.root)
        self.assertTrue(report['ok'])
        self.assertEqual(report['native_reference_checks'],{'D':'not_applicable'})
        for bad in (None,[dict(node_id='M',media_id='I')]):
            x=copy.deepcopy(o)
            if bad is None:x['drafts'][0].pop('native_mentions')
            else:x['drafts'][0]['native_mentions']=bad
            self.assertFalse(compare(f,x,self.root)['ok'])
    def test_nonempty_native_still_requires_exact_type_identity_order(self):
        media=[dict(node_id='M',media_id='I'),dict(node_id='N',media_id='J')]
        mentions=[dict(**x,entity_type='chip',native_noneditable_entity=True) for x in media]
        self.assertEqual(reference_readback_status(media,mentions,'chip'),'passed')
        for value,type_ in [(mentions,None),(mentions,'wrong'),(list(reversed(mentions)),'chip'),([], 'chip')]:
            self.assertEqual(reference_readback_status(media,value,type_),'failed')
        self.assertEqual(reference_readback_status([],None),'failed')
    def test_nonempty_draft_cannot_omit_type(self):
        f,_=text_delivery(self.root);row=f['drafts'][0]
        row['ordered_media']=[dict(node_id='M',media_id='I')]
        row['reference_checks']=[f['source_artifacts'][0]]
        row['quality_evidence']['input_sha256']=q.fingerprint(q.draft_binding(row))
        with self.assertRaisesRegex(ValueError,'native entity'):
            validate_submission(f,self.root)
    def test_each_supported_extension_discovered_or_rejected_missing(self):
        for ext in sorted(set().union(*MEDIA_EXTENSIONS.values())):
            with self.subTest(extension=ext):
                name='reference'+ext
                self.assertFalse(discover_dependencies(self.root,payload={'references':[name]})['ok'])
                (self.root/name).write_bytes(b'SYNTHETIC bytes not actual media')
                report=discover_dependencies(self.root,payload={'references':[name]})
                self.assertTrue(report['ok'])
                self.assertIn(str(self.root/name),report['checked_files'])
    def test_legacy_compare_does_not_upgrade_missing_native_readback(self):
        f,o=text_delivery(self.root)
        f.pop('revision');o.pop('revision');o['frozen_sha256']=digest(f)
        o['drafts'][0].pop('native_mentions')
        report=compare(f,o)
        self.assertEqual(report['native_reference_checks'],{'D':'not_checked'})
        self.assertEqual(report['submission_checks'],'not_checked')
    def test_text_only_review_confirm_commit_uses_applicable_report(self):
        f,o=text_delivery(self.root)
        write_json(self.root/'old.json',{'synthetic':'old'})
        manifest=artifacts.load_manifest(self.root)
        manifest['artifacts'].append(dict(artifact_id='OLD',logical_id='fixture.canvas',revision='R1',
            status='current',user_approved=True,path_or_url='old.json',checksum=artifacts.digest(self.root/'old.json'),depends_on=[]))
        write_json(self.root/artifacts.MANIFEST_PATH,manifest)
        state=new_project_state();state.update(current_artifacts=['SRC','OLD'],user_confirmations=[
            dict(scope='artifact',artifact_ids=['SRC','OLD'],approval_ref='SYNTHETIC ONLY')]);write_project_state(self.root,state)
        rc.open_change(self.root,change_id='SYNTHETIC',tier='T1',logical_ids=['fixture.canvas'],reason='synthetic')
        rc.confirm_impact(self.root,change_id='SYNTHETIC',approval_ref='SYNTHETIC ONLY')
        write_json(self.root/'new.json',f)
        rc.record_candidate(self.root,change_id='SYNTHETIC',artifact_id='NEW',logical_id='fixture.canvas',
                            artifact_type='json',candidate_path='new.json',revision='R2')
        report=compare(f,o,self.root);write_json(self.root/'report.json',report)
        rc.record_review(self.root,change_id='SYNTHETIC',verdict='approved',report_ref='report.json')
        rc.confirm_candidate(self.root,change_id='SYNTHETIC',approval_ref='SYNTHETIC ONLY')
        rc.commit_change(self.root,change_id='SYNTHETIC')
        item=next(x for x in artifacts.load_manifest(self.root)['artifacts'] if x['artifact_id']=='NEW')
        self.assertEqual(item['status'],'current')

if __name__=='__main__': unittest.main()
