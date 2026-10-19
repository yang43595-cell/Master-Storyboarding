"""Local synthetic asset trial regression checks; never calls a generator."""
import sys,tempfile,json,unittest
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import asset_generation_trial as t
from scripts.core import candidate_test

class TrialTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
  self.ref=self.base/'raw.png'; Image.new('RGB',(16,16),'blue').save(self.ref)
  self.source=self.base/'source.json'; t.create(self.source,{'status':'candidate'})
  self.method=self.base/'method.md'; self.method.write_text('synthetic method')
  self.queue=self.base/'queue.json'; t.create(self.queue,{'items':[{'queue_id':'test:A','source_manifest':str(self.source),'source_narrative':str(self.source),'references':[dict(resolved_path=str(self.ref),exists=True,sha256=t.artifacts.digest(self.ref))]}]})
  self.auth=self.base/'auth.json'; t.create(self.auth,dict(run_id='synthetic',message_id='synthetic-only',quote='Synthetic authorized image trial',tool='image_gen',model_disclosure=t.MODEL,allowed_queue_ids=['test:A']))
  self.root=self.base/'trial'; t.initialize(self.root,self.queue,self.auth)
  self.request=dict(baseline_role='face',attempt_id='A01',queue_id='test:A',prompt='Synthetic image',reference_paths=[str(self.ref)],method_evidence=[str(self.method)],visual_inspection='Synthetic blue square inspected')
 def tearDown(self): self.tmp.cleanup()
 def output(self):
  path=self.base/'generated.png'; Image.new('RGB',(16,16),'red').save(path); return path
 def test_success_stays_candidate_and_blocked(self):
  t.preflight(self.root,self.request)
  receipt=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic visual check'})
  self.assertEqual(receipt['outcome'],'qualified_candidate'); self.assertTrue(receipt['production_blocked'])
  self.assertEqual(receipt['references_after'],[t.snap(self.ref)])
  with self.assertRaises(ValueError): candidate_test.require_production(self.root)
 def test_duplicates_and_copy_are_rejected(self):
  t.preflight(self.root,self.request)
  with self.assertRaises(FileExistsError): t.preflight(self.root,self.request)
  with self.assertRaises(ValueError): t.finish(self.root,'A01',self.ref,{'passed':True,'notes':'copied'})
 def test_unbound_reference_rejected(self):
  self.request['reference_paths']=[str(self.output())]
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
 def test_changed_ref_rejected_before_result(self):
  t.preflight(self.root,self.request); Image.new('RGB',(16,16),'green').save(self.ref)
  with self.assertRaises(ValueError): t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'check'})
 def test_failed_baseline_rejected(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':False,'notes':'Synthetic visual failure'})
  self.request.update(baseline_role=None,attempt_id='A02',reference_paths=[result['image']['path']])
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
 def test_qualified_same_trial_baseline_accepted(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic visual pass'})
  self.request.update(baseline_role=None,attempt_id='A02',reference_paths=[result['image']['path']])
  t.preflight(self.root,self.request)
 def test_wrong_tool_asset_and_path_rejected(self):
  for change in ({'tool':'paid-api'},{'queue_id':'test:OTHER'},{'attempt_id':'../escape'}):
   with self.assertRaises(ValueError): t.preflight(self.root,{**self.request,**change})
 def test_symlink_output_directory_rejected(self):
  outside=self.base/'outside'; outside.mkdir()
  (self.root/'attempts').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
 def test_baseline_cannot_inherit_generated_baseline(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic pass'})
  self.request.update(attempt_id='A02',reference_paths=[result['image']['path']])
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
 def reject(self, result):
  t.create(self.root/'attempts/A01/review-overrides/qa.json',dict(image_sha256=result['image']['sha256'],passed=False,notes='Synthetic clothing contamination'))
 def test_later_review_blocks_preflight_without_rewriting_result(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic producer pass'})
  original=(self.root/'attempts/A01/result.json').read_bytes(); self.reject(result)
  self.request.update(baseline_role=None,attempt_id='A02',reference_paths=[result['image']['path']])
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
  self.assertEqual(original,(self.root/'attempts/A01/result.json').read_bytes())
 def test_later_review_blocks_inflight_derivative_finish(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic producer pass'})
  self.request.update(baseline_role=None,attempt_id='A02',reference_paths=[result['image']['path']])
  t.preflight(self.root,self.request); self.reject(result)
  with self.assertRaisesRegex(ValueError,'later visual review'):
   t.finish(self.root,'A02',self.output(),{'passed':True,'notes':'Synthetic derivative'})
 def test_later_review_allows_new_original_based_attempt(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic producer pass'})
  self.reject(result); self.request['attempt_id']='A02'
  t.preflight(self.root,self.request)
 def test_source_change_rejected(self):
  self.source.write_text('{}')
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
if __name__=='__main__': unittest.main(verbosity=2)
