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
 def self_review(self, result, role='scene_master', **changes):
  request=dict(attempt_id=result['attempt_id'],dependency_role=role,review_kind='producer_self_check',
               wardrobe_set=result.get('wardrobe_set'),
               observer='synthetic-producer',visual_observation='Synthetic exact layout and source use inspected',
               image_sha256=result['image']['sha256'])
  return t.approve_dependency(self.root,{**request,**changes})
 def test_producer_self_check_scene_dag_without_independent_qa(self):
  # Historical master stage is explicit even when baseline_role was omitted.
  req={**self.request,'attempt_id':'MASTER','stage':'scene_master','baseline_role':None}
  t.preflight(self.root,req)
  master=t.finish(self.root,'MASTER',self.output(),{'passed':True,'notes':'Synthetic master layout'})
  path=self.root/'attempts/MASTER/request.json';before=path.read_bytes()
  approval=self.self_review(master)
  self.assertEqual(approval['review_kind'],'producer_self_check');self.assertNotIn('independent_qa',approval)
  self.assertNotIn('review_index_at_binding',approval);self.assertEqual(before,path.read_bytes())
  reverse=self.dag_image('scene_reverse',[master['image']['path']],approved=False)
  self.self_review(reverse,'scene_reverse')
  oblique=self.dag_image('scene_oblique',[master['image']['path']],approved=False)
  self.self_review(oblique,'scene_oblique')
  top=self.dag_image('scene_top',[r['image']['path'] for r in (master,reverse,oblique)],approved=False)
  self.assertEqual(top['dependency']['depth'],2);self.assertFalse(top['user_approved'])
 def test_producer_self_check_requires_observer_observation_hash_and_original_role(self):
  master=self.dag_image('scene_master',approved=False)
  for changes in [{'observer':''},{'observer':None},{'visual_observation':' '},
                  {'image_sha256':'wrong'},{'dependency_role':'scene_reverse'},
                  {'review_kind':'user_confirmation'}]:
   with self.assertRaises(ValueError): self.self_review(master,**changes)
  self.assertFalse((self.root/'attempts'/master['attempt_id']/'dependency-approval.json').exists())
 def test_producer_self_check_cannot_admit_failed_or_rejected_result(self):
  t.preflight(self.root,self.request)
  failed=t.finish(self.root,'A01',self.output(),{'passed':False,'notes':'Synthetic fail'})
  with self.assertRaises(ValueError): self.self_review(failed,'face')
  master=self.dag_image('scene_master',approved=False)
  t.create(self.root/'attempts'/master['attempt_id']/'review-overrides/rejected.json',
           dict(image_sha256=master['image']['sha256'],passed=False,notes='Synthetic rejected layout'))
  with self.assertRaises(ValueError): self.self_review(master)
 def test_self_check_binding_rechecks_source_methods_and_frozen_request(self):
  master=self.dag_image('scene_master',approved=False);self.self_review(master)
  request=self.root/'attempts'/master['attempt_id']/'request.json'
  for path in [request,self.method,Path(master['image']['path'])]:
   before=path.read_bytes()
   if path==request:
    value=t.read(path);value['visual_inspection']='changed';path.write_text(json.dumps(value))
   elif path==self.method: path.write_text('changed method')
   else: Image.new('RGB',(16,16),'green').save(path)
   with self.assertRaises(ValueError): self.dag_image('scene_reverse',[master['image']['path']],approved=False)
   path.write_bytes(before)
 def test_self_check_finish_rechecks_ancestor_approval_drift(self):
  master=self.dag_image('scene_master',approved=False);self.self_review(master)
  reverse=self.dag_image('scene_reverse',[master['image']['path']],approved=False)
  self.self_review(reverse,'scene_reverse')
  oblique=self.dag_image('scene_oblique',[master['image']['path']],approved=False)
  self.self_review(oblique,'scene_oblique')
  req={**self.request,'attempt_id':'TOP','baseline_role':None,'dependency_role':'scene_top',
       'reference_paths':[r['image']['path'] for r in (master,reverse,oblique)]}
  t.preflight(self.root,req)
  approval=self.root/'attempts'/master['attempt_id']/'dependency-approval.json'
  value=t.read(approval);value['self_check']['visual_observation']='Changed after derivative binding'
  approval.write_text(json.dumps(value))
  with self.assertRaisesRegex(ValueError,'Ancestor method dependency'):
   t.finish(self.root,'TOP',self.output(),{'passed':True,'notes':'Synthetic top'})
 def cross_trial_fixture(self, passed=True):
  # Queue identity is AST, while queue_id also contains the output/state.
  queue=t.read(self.queue)
  queue['items'][0].update(asset_id='AST-A',output_id='INITIAL-COMPOSITE',references=[])
  source_queue=self.base/'identity-queue.json';t.create(source_queue,queue)
  source_root=self.base/'identity-trial';t.initialize(source_root,source_queue,self.auth)
  source_req={**self.request,'baseline_role':None,'stage':'initial_composite_prompt_only_revision','reference_paths':[]}
  t.preflight(source_root,source_req)
  result=t.finish(source_root,'A01',self.output(),{'passed':passed,'notes':'Synthetic visible identity check'})
  queue['items'][0].update(queue_id='test:A-WARDROBE',output_id='VISIT-COMPOSITE')
  target_queue=self.base/'wardrobe-queue.json';t.create(target_queue,queue)
  auth=t.read(self.auth);auth.update(run_id='wardrobe',allowed_queue_ids=['test:A-WARDROBE'])
  target_auth=self.base/'wardrobe-auth.json';t.create(target_auth,auth)
  target=self.base/'wardrobe-trial';t.initialize(target,target_queue,target_auth)
  req={**self.request,'attempt_id':'W01','queue_id':'test:A-WARDROBE','baseline_role':None,
       'stage':'wardrobe_state','reference_paths':[result['image']['path']],
       'generated_reference_bindings':[dict(source_trial_root=str(source_root),attempt_id='A01',
         purpose='identity_for_wardrobe',visual_observation='Synthetic face identity inspected; discard prior clothing')]}
  return source_root,target,req,result
 def test_cross_trial_composite_identity_stays_generated_and_candidate(self):
  source,target,req,result=self.cross_trial_fixture()
  source_request=source/'attempts/A01/request.json';before=source_request.read_bytes()
  record=t.preflight(target,req)['request']
  edge=record['generated_reference_context'][0]
  self.assertEqual(edge['source_kind'],'generated_candidate')
  self.assertEqual(edge['image'],result['image']);self.assertEqual(edge['asset_id'],'AST-A')
  self.assertEqual(record['reference_amendments'],[])
  output=self.base/'wardrobe.png';Image.new('RGB',(16,16),'yellow').save(output)
  receipt=t.finish(target,'W01',output,{'passed':True,'notes':'Synthetic new wardrobe inspected'})
  self.assertEqual(receipt['generated_reference_context'],[edge])
  self.assertFalse(receipt['user_approved']);self.assertTrue(receipt['production_blocked'])
  self.assertEqual(source_request.read_bytes(),before)
  # Admission must not relabel the historical composite as a face baseline.
  with self.assertRaisesRegex(ValueError,'original generation role'):
   t.approve_dependency(source,dict(attempt_id='A01',dependency_role='face'))
 def test_cross_trial_failed_candidate_rejected(self):
  source,target,req,result=self.cross_trial_fixture(passed=False)
  with self.assertRaisesRegex(ValueError,'qualified producer'): t.preflight(target,req)
 def test_cross_trial_identity_rejects_wrong_asset_narrative_or_purpose(self):
  source,target,req,result=self.cross_trial_fixture()
  scope_path=target/'asset_generation_trial.json';scope=t.read(scope_path)
  for field,value in [('asset_id','AST-OTHER'),('source_narrative',str(self.method))]:
   changed=json.loads(json.dumps(scope));changed['items'][req['queue_id']][field]=value
   scope_path.write_text(json.dumps(changed))
   with self.assertRaisesRegex(ValueError,'same source project and AST'): t.preflight(target,req)
  scope_path.write_text(json.dumps(scope))
  for changes in [{'stage':'asset'},{'baseline_role':'face'},
                  {'generated_reference_bindings':[{**req['generated_reference_bindings'][0],'purpose':'original'}]},
                  {'generated_reference_bindings':[{**req['generated_reference_bindings'][0],'visual_observation':''}]}]:
   with self.assertRaises(ValueError): t.preflight(target,{**req,**changes})
 def test_cross_trial_native_bytes_only_no_copies_or_crops(self):
  source,target,req,result=self.cross_trial_fixture()
  copy=self.base/'copy.png';copy.write_bytes(Path(result['image']['path']).read_bytes())
  with self.assertRaisesRegex(ValueError,'native generated'): t.preflight(target,{**req,'reference_paths':[str(copy)]})
  Image.new('RGB',(16,16),'green').save(result['image']['path'])
  with self.assertRaisesRegex(ValueError,'native generated'): t.preflight(target,req)
 def test_cross_trial_source_narrative_drift_blocks_preflight(self):
  source,target,req,result=self.cross_trial_fixture()
  self.source.write_text('{}')
  with self.assertRaisesRegex(ValueError,'Source bytes changed'): t.preflight(target,req)
 def test_cross_trial_finish_rechecks_rejection_and_evidence_drift(self):
  source,target,req,result=self.cross_trial_fixture()
  t.preflight(target,req)
  path=source/'attempts/A01/result.json';before=path.read_bytes()
  changed=t.read(path);changed['assessment']['notes']='Edited after binding'
  path.write_text(json.dumps(changed))
  with self.assertRaisesRegex(ValueError,'evidence changed'): t.finish(target,'W01',self.output(),{'passed':True,'notes':'Synthetic'})
  path.write_bytes(before)
  t.create(source/'attempts/A01/review-overrides/later.json',dict(image_sha256=result['image']['sha256'],passed=False,notes='Synthetic later identity rejection'))
  with self.assertRaisesRegex(ValueError,'qualified producer'): t.finish(target,'W01',self.output(),{'passed':True,'notes':'Synthetic'})
  self.assertFalse((target/'attempts/W01/result.json').exists())
 def test_cross_trial_finish_rechecks_source_request_methods_and_image(self):
  source,target,req,result=self.cross_trial_fixture()
  t.preflight(target,req)
  paths=[source/'attempts/A01/request.json',self.method,Path(result['image']['path']),self.source]
  for path in paths:
   before=path.read_bytes()
   if path.suffix=='.png': Image.new('RGB',(16,16),'green').save(path)
   elif path.name=='request.json':
    changed=t.read(path);changed['visual_inspection']='Changed source observation';path.write_text(json.dumps(changed))
   else: path.write_text('{}')
   with self.assertRaises(ValueError): t.finish(target,'W01',self.output(),{'passed':True,'notes':'Synthetic'})
   path.write_bytes(before)
 def test_cross_trial_composite_cannot_hide_image_parents(self):
  source,target,req,result=self.cross_trial_fixture()
  path=source/'attempts/A01/request.json';changed=t.read(path);changed['references_before']=[t.snap(self.ref)]
  path.write_text(json.dumps(changed))
  with self.assertRaisesRegex(ValueError,'without generated/image parents'): t.preflight(target,req)
 def reading_revalidation_fixture(self):
  source,target,req,result=self.cross_trial_fixture()
  packet=self.base/'historical/method_reading_packet.md';packet.parent.mkdir();packet.write_text('Synthetic unchanged method body')
  ledger=packet.parent/'method_reading_evidence.json';t.create(ledger,{'old':'synthetic reading declaration'})
  expected=t.snap(ledger)
  # Simulate a pre-freezing historical request; never modify a real project.
  source_request=source/'attempts/A01/request.json';original=t.read(source_request)
  original['methods']=[t.snap(packet),expected];source_request.write_text(json.dumps(original))
  ledger.write_text(json.dumps({'new':'later reading declaration'}))
  context=self.base/'current-preparation';context.mkdir()
  current_packet=context/'method_reading_packet.md';current_packet.write_bytes(packet.read_bytes())
  current_ledger=context/'method_reading_evidence.json'
  t.create(current_ledger,dict(skill='asset-image-assistant',context_id='synthetic-revalidation',
      reading_reuse_key='synthetic-key',actor_id='synthetic-producer',attestation='agent_reported_complete_reading',
      packet_sha256=t.snap(packet)['sha256'],method_packet_sha256=t.snap(packet)['sha256']))
  basis=context/'task_basis.json';t.create(basis,dict(skill='asset-image-assistant',context_id='synthetic-revalidation',
      reading_reuse_key='synthetic-key',input_snapshot={'sources':[t.snap(self.source)]},
      task_request={'image_targets':['AST-A@VISIT-COMPOSITE']}))
  evidence=self.base/'new-reading-revalidation.json'
  t.create(evidence,dict(source_request=t.snap(source_request),source_result=t.snap(source/'attempts/A01/result.json'),
      image=result['image'],historical_ledger=expected,current_ledger=t.snap(ledger),methods_re_read=[t.snap(packet)],
      observer='synthetic-producer',method_observation='Synthetic unchanged packet read again; historical declaration unavailable',
      visual_observation='Synthetic exact native identity inspected for wardrobe use',
      current_preparation=dict(basis=t.snap(basis),ledger=t.snap(current_ledger),packet=t.snap(current_packet))))
  req['generated_reference_bindings'][0]['source_method_revalidation']=str(evidence)
  return source,target,req,result,evidence,packet,ledger
 def test_historical_reading_ledger_drift_requires_explicit_revalidation(self):
  source,target,req,result,evidence,packet,ledger=self.reading_revalidation_fixture()
  req['generated_reference_bindings'][0].pop('source_method_revalidation')
  with self.assertRaisesRegex(ValueError,'Source bytes changed'): t.preflight(target,req)
 def test_exact_current_reading_revalidation_admits_without_rewriting_history(self):
  source,target,req,result,evidence,packet,ledger=self.reading_revalidation_fixture()
  path=source/'attempts/A01/request.json';before=path.read_bytes()
  record=t.preflight(target,req)['request'];edge=record['generated_reference_context'][0]
  self.assertTrue(edge['historical_method_drift'])
  self.assertEqual(edge['source_method_revalidation']['revalidation'],t.snap(evidence))
  self.assertEqual(before,path.read_bytes());self.assertFalse(record['user_approved'])
  output=self.base/'new-wardrobe.png';Image.new('RGB',(16,16),'yellow').save(output)
  receipt=t.finish(target,'W01',output,{'passed':True,'notes':'Synthetic current wardrobe inspected'})
  self.assertTrue(receipt['generated_reference_context'][0]['historical_method_drift'])
 def test_reading_revalidation_rejects_missing_or_inexact_evidence(self):
  source,target,req,result,evidence,packet,ledger=self.reading_revalidation_fixture()
  valid=t.read(evidence)
  for field,value in [('observer',''),('method_observation',' '),('visual_observation',''),
                      ('current_preparation',{}),('methods_re_read',[]),('historical_ledger',t.snap(ledger)),
                      ('source_request',t.snap(self.source)),('image',t.snap(self.ref))]:
   evidence.write_text(json.dumps({**valid,field:value}))
   with self.assertRaises(ValueError): t.preflight(target,req)
 def test_reading_revalidation_cannot_bypass_method_body_change(self):
  source,target,req,result,evidence,packet,ledger=self.reading_revalidation_fixture()
  packet.write_text('Changed actual method body')
  with self.assertRaisesRegex(ValueError,'Source bytes changed'): t.preflight(target,req)
 def test_reading_revalidation_requires_actual_current_preparation(self):
  source,target,req,result,evidence,packet,ledger=self.reading_revalidation_fixture()
  record=t.read(evidence);current=Path(record['current_preparation']['ledger']['path'])
  value=t.read(current);value['actor_id']='different-observer';current.write_text(json.dumps(value))
  record['current_preparation']['ledger']=t.snap(current);evidence.write_text(json.dumps(record))
  with self.assertRaisesRegex(ValueError,'Current preparation'): t.preflight(target,req)
 def test_revalidated_finish_rechecks_ledger_evidence_and_later_rejection(self):
  source,target,req,result,evidence,packet,ledger=self.reading_revalidation_fixture()
  t.preflight(target,req)
  for path in [ledger,evidence,packet,Path(t.read(evidence)['current_preparation']['ledger']['path'])]:
   before=path.read_bytes();path.write_text('{}')
   with self.assertRaises(ValueError): t.finish(target,'W01',self.output(),{'passed':True,'notes':'Synthetic'})
   path.write_bytes(before)
  t.create(source/'attempts/A01/review-overrides/later.json',dict(image_sha256=result['image']['sha256'],passed=False,notes='Synthetic later rejection'))
  with self.assertRaisesRegex(ValueError,'qualified producer'): t.finish(target,'W01',self.output(),{'passed':True,'notes':'Synthetic'})
 def test_future_attempt_freezes_only_reading_ledger_with_original_provenance(self):
  ledger=self.base/'method_reading_evidence.json';t.create(ledger,{'reading':'synthetic original declaration'})
  req={**self.request,'method_evidence':[str(self.method),str(ledger)]}
  record=t.preflight(self.root,req)['request']
  frozen=record['methods'][1]
  self.assertNotEqual(frozen['path'],str(ledger));self.assertEqual(record['methods'][0],t.snap(self.method))
  self.assertEqual(record['reading_ledger_sources'],[dict(source=t.snap(ledger),frozen=frozen)])
  ledger.write_text(json.dumps({'reading':'later context declaration'}))
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic unchanged method pass'})
  t.preflight(self.root,{**self.request,'attempt_id':'A02','baseline_role':None,'reference_paths':[result['image']['path']]})
  self.method.write_text('Changed actual method')
  with self.assertRaises(ValueError): t.finish(self.root,'A02',self.output(),{'passed':True,'notes':'Synthetic'})
 def layout_revision_fixture(self):
  queue=t.read(self.queue);queue['items'][0]['asset_id']='AST-SCENE'
  queue['items'].append({**queue['items'][0],'queue_id':'test:OTHER','asset_id':'AST-OTHER'})
  q=self.base/'scene-queue.json';t.create(q,queue)
  auth=t.read(self.auth);auth['allowed_queue_ids']=['test:A','test:OTHER']
  a=self.base/'scene-auth.json';t.create(a,auth)
  self.root=self.base/'layout-trial';t.initialize(self.root,q,a)
  method=self.base/'source-method.md';method.write_text('Synthetic original scene layout method')
  request={**self.request,'attempt_id':'MASTER','stage':'scene_master','baseline_role':None,
           'method_evidence':[str(method)]}
  t.preflight(self.root,request)
  master=t.finish(self.root,'MASTER',self.output(),{'passed':True,'notes':'Synthetic original layout inspected'})
  revision={**self.request,'attempt_id':'REVISION','stage':'scene_master_revision','baseline_role':'scene_master',
            'reference_paths':[master['image']['path']],
            'scene_layout_revision':dict(source_attempt_id='MASTER',image_sha256=master['image']['sha256'],
                observer='synthetic-producer',edit_purpose='Add one chair while retaining existing furniture',
                visual_observation='Synthetic root layout inspected; five seats and clear aisle')}
  return master,revision,method
 def finish_layout_revision(self, request):
  t.preflight(self.root,request)
  output=self.base/'new-layout.png';Image.new('RGB',(16,16),'green').save(output)
  return t.finish(self.root,request['attempt_id'],output,{'passed':True,'notes':'Synthetic revised layout inspected'})
 def test_one_layout_revision_preserves_lineage_and_scene_top_depth_two(self):
  master,request,method=self.layout_revision_fixture()
  before=(self.root/'attempts/MASTER/request.json').read_bytes()
  result=self.finish_layout_revision(request)
  self.assertEqual(result['source_kind'],'scene_layout_revision')
  lineage=result['scene_layout_revision_context']
  self.assertEqual(lineage['lineage_depth'],1);self.assertEqual(lineage['image'],master['image'])
  self.assertEqual(t.check_reference_reviews(self.root,[result['image']])[result['image']['path']],0)
  self.self_review(result)
  reverse=self.dag_image('scene_reverse',[result['image']['path']],approved=False)
  self.self_review(reverse,'scene_reverse')
  oblique=self.dag_image('scene_oblique',[result['image']['path']],approved=False)
  self.self_review(oblique,'scene_oblique')
  top=self.dag_image('scene_top',[r['image']['path'] for r in (result,reverse,oblique)],approved=False)
  self.assertEqual(top['dependency']['depth'],2)
  self.assertEqual(before,(self.root/'attempts/MASTER/request.json').read_bytes())
  self.assertTrue(top['production_blocked']);self.assertFalse(top['user_approved'])
 def test_layout_revision_rejects_wrong_asset_hash_missing_observation_and_second_edit(self):
  master,request,method=self.layout_revision_fixture()
  for changes in [{'queue_id':'test:OTHER'},
                  {'scene_layout_revision':{**request['scene_layout_revision'],'image_sha256':'wrong'}},
                  {'scene_layout_revision':{**request['scene_layout_revision'],'visual_observation':''}},
                  {'baseline_role':None}]:
   with self.assertRaises(ValueError): t.preflight(self.root,{**request,**changes})
  result=self.finish_layout_revision(request)
  second={**request,'attempt_id':'SECOND','reference_paths':[result['image']['path']],
          'scene_layout_revision':{**request['scene_layout_revision'],'source_attempt_id':'REVISION',
                                  'image_sha256':result['image']['sha256']}}
  with self.assertRaisesRegex(ValueError,'never a second revision'): t.preflight(self.root,second)
 def test_layout_revision_failed_or_revoked_source_is_rejected(self):
  master,request,method=self.layout_revision_fixture()
  path=self.root/'attempts/MASTER/result.json';before=path.read_bytes()
  value=t.read(path);value.update(outcome='failed_candidate',assessment={'passed':False,'notes':'Synthetic fail'})
  path.write_text(json.dumps(value))
  with self.assertRaises(ValueError): t.preflight(self.root,request)
  path.write_bytes(before)
  t.create(self.root/'attempts/MASTER/review-overrides/reject.json',dict(image_sha256=master['image']['sha256'],passed=False,notes='Synthetic revoked original layout'))
  with self.assertRaises(ValueError): t.preflight(self.root,request)
 def test_layout_revision_finish_and_later_consumption_recheck_original_lineage(self):
  master,request,method=self.layout_revision_fixture()
  t.preflight(self.root,request)
  folder=self.root/'attempts/MASTER'
  for path in [method,Path(master['image']['path']),folder/'request.json',folder/'result.json',self.ref]:
   before=path.read_bytes()
   if path.suffix=='.png': Image.new('RGB',(16,16),'yellow').save(path)
   elif path.suffix=='.json':
    value=t.read(path);value['changed']='Synthetic later drift';path.write_text(json.dumps(value))
   else: path.write_text('Changed actual source method')
   with self.assertRaises(ValueError): t.finish(self.root,'REVISION',self.output(),{'passed':True,'notes':'Synthetic'})
   path.write_bytes(before)
  output=self.base/'new-layout.png';Image.new('RGB',(16,16),'green').save(output)
  result=t.finish(self.root,'REVISION',output,{'passed':True,'notes':'Synthetic revised layout'})
  self.self_review(result)
  before=method.read_bytes();method.write_text('Changed original method')
  with self.assertRaises(ValueError): self.dag_image('scene_reverse',[result['image']['path']],approved=False)
  method.write_bytes(before)
  t.create(folder/'review-overrides/reject.json',dict(image_sha256=master['image']['sha256'],passed=False,notes='Synthetic later source revocation'))
  with self.assertRaises(ValueError): self.dag_image('scene_reverse',[result['image']['path']],approved=False)
 def test_layout_revision_does_not_reset_an_ordinary_direction_graph(self):
  master,request,method=self.layout_revision_fixture()
  result=self.finish_layout_revision(request);self.self_review(result)
  reverse=self.dag_image('scene_reverse',[result['image']['path']],approved=False);self.self_review(reverse,'scene_reverse')
  # The bounded layout provenance cannot relabel an existing direction as a root.
  invalid={**request,'attempt_id':'DIRECTION-RESET','reference_paths':[reverse['image']['path']],
           'scene_layout_revision':{**request['scene_layout_revision'],'source_attempt_id':reverse['attempt_id'],
                                   'image_sha256':reverse['image']['sha256']}}
  with self.assertRaises(ValueError): t.preflight(self.root,invalid)
if __name__=='__main__': unittest.main(verbosity=2)

class MethodBodyRevalidationTests(unittest.TestCase):
 setUp = TrialTests.setUp
 tearDown = TrialTests.tearDown
 output = TrialTests.output
 def review(self, image):
  folder=self.root/'attempts/A01'; request=t.read(folder/'request.json')
  return dict(attempt_id='A01',review_id='CURRENT-R01',image_path=str(image),passed=True,
   observer='synthetic-observer',authorization_ref='synthetic-explicit-revalidation',
   method_observation='Synthetic current changed method was read',
   visual_observation='Synthetic exact image still meets current method',
   historical_methods=request['methods'],current_methods=[t.snap(r['path']) for r in request['methods']])
 def test_pending_output_finishes_after_explicit_new_review_without_rewriting_request(self):
  t.preflight(self.root,self.request); folder=self.root/'attempts/A01'
  before=(folder/'request.json').read_bytes(); image=self.output()
  self.method.write_text('changed current method')
  with self.assertRaises(ValueError): t.finish(self.root,'A01',image,{'passed':True,'notes':'check'})
  t.revalidate_methods(self.root,self.review(image))
  result=t.finish(self.root,'A01',image,{'passed':True,'notes':'current visual check'})
  self.assertEqual(before,(folder/'request.json').read_bytes()); self.assertTrue(result['production_blocked'])
 def test_qualified_ancestor_can_be_reused_but_new_drift_or_image_change_still_blocks(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':True,'notes':'Synthetic pass'})
  image=Path(result['image']['path']); self.method.write_text('changed method')
  t.revalidate_methods(self.root,self.review(image))
  t.check_reference_reviews(self.root,[result['image']])
  self.method.write_text('changed again')
  with self.assertRaises(ValueError): t.check_reference_reviews(self.root,[result['image']])
 def test_failed_output_and_missing_review_details_are_rejected(self):
  t.preflight(self.root,self.request)
  result=t.finish(self.root,'A01',self.output(),{'passed':False,'notes':'Synthetic failed image'})
  self.method.write_text('changed method'); review=self.review(Path(result['image']['path']))
  with self.assertRaises(ValueError): t.revalidate_methods(self.root,review)
  with self.assertRaises(ValueError): t.revalidate_methods(self.root,{**review,'observer':''})
 def test_review_does_not_approve_changed_original_reference(self):
  t.preflight(self.root,self.request); self.method.write_text('changed method')
  Image.new('RGB',(16,16),'green').save(self.ref)
  with self.assertRaises(ValueError): t.revalidate_methods(self.root,self.review(self.output()))
