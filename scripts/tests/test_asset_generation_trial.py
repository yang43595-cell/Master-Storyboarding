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
 def amendment_fixture(self):
  donor=self.base/'donor.png'; Image.new('RGB',(16,16),'green').save(donor)
  queue=t.read(self.queue)
  queue['items'].append(dict(queue_id='test:B',source_manifest=str(self.source),source_narrative=str(self.source),references=[dict(resolved_path=str(donor),exists=True,sha256=t.artifacts.digest(donor))]))
  queuepath=self.base/'queue-amend.json'; t.create(queuepath,queue)
  auth=t.read(self.auth);auth['allowed_queue_ids']=['test:A','test:B']
  authpath=self.base/'auth-amend.json';t.create(authpath,auth)
  root=self.base/'amend-trial';t.initialize(root,queuepath,authpath)
  amendment=dict(amendment_id='synthetic-ref',target_queue_id='test:A',donor_queue_id='test:B',reference=t.snap(donor),visual_purpose='Visible full-body wardrobe',visual_observation='Synthetic green square inspected',authorization_ref='synthetic scope correction')
  return root,amendment
 def test_reference_amendment_allows_frozen_donor_without_scope_mutation(self):
  root,amendment=self.amendment_fixture()
  before=(root/'asset_generation_trial.json').read_bytes()
  t.amend_reference(root,amendment)
  req={**self.request,'reference_paths':[amendment['reference']['path']]}
  result=t.preflight(root,req)
  self.assertEqual(before,(root/'asset_generation_trial.json').read_bytes())
  self.assertEqual(len(result['request']['reference_amendments']),1)
  with self.assertRaises(ValueError): candidate_test.require_production(root)
  with self.assertRaises(FileExistsError): t.amend_reference(root,amendment)
 def test_reference_amendment_rejects_unbound_path_and_wrong_target(self):
  root,amendment=self.amendment_fixture()
  for changes in ({'reference':t.snap(self.ref)},{'target_queue_id':'unknown'},{'visual_observation':''}):
   with self.assertRaises(ValueError): t.amend_reference(root,{**amendment,**changes})
 def test_reference_amendment_wrong_scope_and_cross_case_rejected(self):
  root,amendment=self.amendment_fixture()
  record=t.amend_reference(root,amendment)
  scope=t.read(root/'asset_generation_trial.json')
  with self.assertRaises(ValueError): t.validate_reference_amendment(root,scope,{**record,'run_id':'other'})
  scope['items']['test:B']['case_id']='other-case'
  with self.assertRaises(ValueError): t.validate_reference_amendment(root,scope,record)
 def test_reference_amendment_changed_bytes_rejected(self):
  root,amendment=self.amendment_fixture();t.amend_reference(root,amendment)
  Image.new('RGB',(16,16),'yellow').save(amendment['reference']['path'])
  with self.assertRaises(ValueError): t.preflight(root,self.request)
 def test_reference_amendment_changed_during_generation_rejected(self):
  root,amendment=self.amendment_fixture();t.amend_reference(root,amendment)
  t.preflight(root,{**self.request,'reference_paths':[amendment['reference']['path']]})
  path=root/'reference-amendments/synthetic-ref.json';value=t.read(path);value['visual_purpose']='Changed purpose'
  path.write_text(json.dumps(value))
  with self.assertRaises(ValueError): t.finish(root,'A01',self.output(),{'passed':True,'notes':'Synthetic'})
 def dag_image(self, role, references=None, wardrobe='red', approved=True):
  n=getattr(self,'dag_counter',0)+1;self.dag_counter=n;ident='D'+str(n)
  baselines={'face':'face','wardrobe_front':'wardrobe','scene_master':'scene_master'}
  req={**self.request,'attempt_id':ident,'baseline_role':baselines.get(role),
       'reference_paths':references or [str(self.ref)],'wardrobe_set':wardrobe,
       'dependency_role':None if role in baselines else role}
  t.preflight(self.root,req)
  output=self.base/(ident+'.png');Image.new('RGB',(16,16),(n,22,33)).save(output)
  result=t.finish(self.root,ident,output,{'passed':True,'notes':'Synthetic role check'})
  if approved:
   index=self.base/(ident+'-qa.json')
   t.create(index,{'items':[dict(attempt_id=ident,reviewed_path=result['image']['path'],image_sha256=result['image']['sha256'],status='passed')]})
   t.approve_dependency(self.root,dict(attempt_id=ident,dependency_role=role,wardrobe_set=wardrobe,independent_single_view=True,role_review='Synthetic exact role visual check',review_index=str(index)))
  return result
 def wardrobe_dag(self):
  face=self.dag_image('face');front=self.dag_image('wardrobe_front')
  side=self.dag_image('wardrobe_side',[front['image']['path']])
  back=self.dag_image('wardrobe_back',[front['image']['path']])
  return face,front,side,back
 def test_finite_wardrobe_dag_board_depth_two(self):
  sources=self.wardrobe_dag()
  board=self.dag_image('identity_board',[r['image']['path'] for r in sources],approved=False)
  self.assertEqual(board['dependency']['depth'],2)
  self.assertIsNone(board['baseline_role'])
 def test_dag_board_requires_all_roles_and_same_set(self):
  face,front,side,back=self.wardrobe_dag()
  with self.assertRaises(ValueError): self.dag_image('identity_board',[r['image']['path'] for r in (face,front,side)],approved=False)
  blue=self.dag_image('wardrobe_front',wardrobe='blue')
  with self.assertRaises(ValueError): self.dag_image('identity_board',[r['image']['path'] for r in (face,blue,side,back)],approved=False)
 def test_scene_top_dag_requires_master_reverse_oblique(self):
  master=self.dag_image('scene_master')
  reverse=self.dag_image('scene_reverse',[master['image']['path']])
  oblique=self.dag_image('scene_oblique',[master['image']['path']])
  top=self.dag_image('scene_top',[r['image']['path'] for r in (master,reverse,oblique)],approved=False)
  self.assertEqual(top['dependency']['depth'],2)
 def test_dag_ancestor_rejection_propagates(self):
  face,front,side,back=self.wardrobe_dag()
  board=self.dag_image('identity_board',[r['image']['path'] for r in (face,front,side,back)],approved=False)
  folder=self.root/'attempts'/front['attempt_id']
  t.create(folder/'review-overrides/later.json',dict(image_sha256=front['image']['sha256'],passed=False,notes='Synthetic later rejection'))
  with self.assertRaises(ValueError): t.check_reference_reviews(self.root,[board['image']])
 def test_dag_self_check_is_not_independent_qa(self):
  front=self.dag_image('wardrobe_front',approved=False)
  with self.assertRaises(ValueError): self.dag_image('wardrobe_side',[front['image']['path']])
 def test_terminal_board_cannot_reenter_graph(self):
  board=self.dag_image('identity_board',[r['image']['path'] for r in self.wardrobe_dag()],approved=False)
  with self.assertRaises(ValueError): self.dag_image('wardrobe_side',[board['image']['path']])
 def legacy_scene_result(self, stage, parent):
  ident='legacy-'+stage
  request={**self.request,'attempt_id':ident,'stage':stage,'baseline_role':None,'reference_paths':[parent['image']['path']]}
  t.preflight(self.root,request)
  result=t.finish(self.root,ident,self.output(),{'passed':True,'notes':'Synthetic scene view'})
  index=self.base/(ident+'-review.json')
  t.create(index,{'items':[dict(attempt_id=ident,reviewed_path=result['image']['path'],image_sha256=result['image']['sha256'],status='passed')]})
  return ident,index
 def test_exact_legacy_scene_aliases_bind_without_mutating_request(self):
  master=self.dag_image('scene_master')
  for stage,role in [('reverse','scene_reverse'),('high45','scene_oblique')]:
   ident,index=self.legacy_scene_result(stage,master)
   path=self.root/'attempts'/ident/'request.json';before=path.read_bytes()
   result=t.approve_dependency(self.root,dict(attempt_id=ident,dependency_role=role,role_review='Synthetic exact scene view',review_index=str(index)))
   self.assertEqual(result['dependency_role'],role);self.assertEqual(before,path.read_bytes())
 def test_legacy_scene_alias_does_not_guess_suffix_or_accept_wardrobe_parent(self):
  master=self.dag_image('scene_master')
  ident,index=self.legacy_scene_result('reverse-extra',master)
  with self.assertRaises(ValueError):
   t.approve_dependency(self.root,dict(attempt_id=ident,dependency_role='scene_reverse',role_review='Synthetic',review_index=str(index)))
  front=self.dag_image('wardrobe_front')
  ident,index=self.legacy_scene_result('high45',front)
  with self.assertRaises(ValueError):
   t.approve_dependency(self.root,dict(attempt_id=ident,dependency_role='scene_oblique',role_review='Synthetic',review_index=str(index)))
 def test_source_change_rejected(self):
  self.source.write_text('{}')
  with self.assertRaises(ValueError): t.preflight(self.root,self.request)
if __name__=='__main__': unittest.main(verbosity=2)
