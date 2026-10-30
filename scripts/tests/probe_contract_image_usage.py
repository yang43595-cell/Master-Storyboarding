"""Read-only source audit; writes only isolated synthetic text/JSON fixtures. No real images or approvals."""
import sys,json,copy,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.core import schema_contracts as sc,artifacts as a,asset_refs as ar,quality_evidence as q,revision_control as rc,candidate_test as ct
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state
R=Path(__file__).parent
results={}
v={'schema_version':'1.0','project_id':'SYNTHETIC','request_id':'VS-001','status':'candidate','source_storyboard':{'artifact_id':'ART-D','logical_id':'storyboard.package','revision':'R01','path':'director.json','status':'confirmed'},'source_shot_ids':['S01-001'],'target_state':'synthetic face state','must_appear':['face'],'must_not_appear':[],'preserve':['identity'],'existing_references':[],'decision':'request_supplement','max_images':1,'return_upstream':{'required':False,'target':'none','reason':''},'gap':{'type':'critical_state','observed_evidence':'synthetic','why_existing_is_insufficient':'synthetic','downstream_consequence':'synthetic'}}
results['base_schema']=sc.validate_visual_supplement_request(v)
w=copy.deepcopy(v);w['creator_mask_face_effect']=True
results['creator_mask_field_errors']=sc.schema_errors(w,'visual-supplement-request.schema.json')
results['decision_status_matrix']={}
for status in ['candidate','ready','not_needed','blocked','confirmed','superseded','rejected']:
 w=copy.deepcopy(v);w['status']=status
 results['decision_status_matrix'][status]=not sc.schema_errors(w,'visual-supplement-request.schema.json')
with tempfile.TemporaryDirectory(prefix='SYNTHETIC_',dir=R) as t:
 root=Path(t);(root/'image.png').write_text('SYNTHETIC NONIMAGE BYTES; NO VISUAL CLAIM')
 write_json(root/'source.json',{'synthetic':True})
 sha=a.digest(root/'image.png');e={'status':'pass','observer':'SYNTHETIC ONLY','checked_at':'2026-10-30T00:00:00Z','checks':{k:{'result':'pass','observation':'synthetic assertion not real pixels','locator':'synthetic'} for k in q.IMAGE_ASPECTS},'image_sha256':sha,'source_files':[{'path':'source.json','sha256':a.digest(root/'source.json')}],'source_shot_ids':['S01-001'],'view':{'resolution':'original','locator':'synthetic'},'uses':[{'source_shot_ids':['S01-001'],'roles':['state_reference'],'entity_ids':['E'],'state_id':'STATE1'}]}
 entry={'artifact_id':'IMG','logical_id':'image.test','type':'visual-supplement-image','revision':'R01','path_or_url':'image.png','checksum':sha,'status':'current','user_approved':True,'depends_on':[],'confirmation_ref':'receipt.json','quality_evidence':e}
 write_json(root/'receipt.json',{'scope':'artifact','artifact_ids':['IMG'],'image_path':'image.png','image_sha256':sha,'usage':{'source_shot_ids':['S01-001']}})
 write_json(root/a.MANIFEST_PATH,{'artifacts':[entry]});s=new_project_state();s.update(current_artifacts=['IMG'],user_confirmations=[{'scope':'artifact','artifact_ids':['IMG'],'approval_ref':'SYNTHETIC ONLY'}]);write_json(root/'00_项目管理/project_state.json',s)
 results['receipt_with_scope']=ar.require_references(root,['IMG'],['S01-001'])
 results['receipt_no_shots']=ar.require_references(root,['IMG'])
 (root/'revision.png').write_bytes((root/'image.png').read_bytes())
 rc.open_change(root,change_id='SYNTHETIC',tier='T1',logical_ids=['image.test'],reason='synthetic scope retest')
 rc.confirm_impact(root,change_id='SYNTHETIC',approval_ref='SYNTHETIC ONLY')
 rc.record_candidate(root,change_id='SYNTHETIC',artifact_id='IMG2',logical_id='image.test',artifact_type='visual-supplement-image',candidate_path='revision.png',revision='R02')
 rc.record_review(root,change_id='SYNTHETIC',verdict='approved',report_ref='SYNTHETIC ONLY')
 try:
  rc.confirm_candidate(root,change_id='SYNTHETIC',approval_ref='SYNTHETIC ONLY',scope_description='only S01-001')
 except ValueError: pass
 else: raise AssertionError('prose must not substitute for explicit usage')
 rc.confirm_candidate(root,change_id='SYNTHETIC',approval_ref='SYNTHETIC ONLY',scope_description='only S01-001 state_reference',usage={'source_shot_ids':['S01-001']})
 rc.commit_change(root,change_id='SYNTHETIC')
 m=a.load_manifest(root);new=m['artifacts'][-1];new['quality_evidence']=copy.deepcopy(e);write_json(root/a.MANIFEST_PATH,m)
 results['revision_is_confirmed']=a.artifact_is_confirmed(new,ar.load_project_state(root),root)
 results['revision_with_scope']=ar.require_references(root,['IMG2'],['S01-001'])
 results['revision_entry_fields']=sorted(new)
 # Observation covers an extra shot, but explicit user permission still does not.
 new['quality_evidence']['source_shot_ids'].append('S99-999');write_json(root/a.MANIFEST_PATH,m)
 assert not ar.require_references(root,['IMG2'],['S99-999'])[0]
 state=ar.load_project_state(root)
 saved=copy.deepcopy(state)
 state['user_confirmations'][-1].pop('usage');write_json(root/'00_项目管理/project_state.json',state)
 assert not ar.require_references(root,['IMG2'],['S01-001'])[0]
 write_json(root/'00_项目管理/project_state.json',saved)
 (root/'source.json').write_text('changed')
 assert not ar.require_references(root,['IMG2'],['S01-001'])[0]
 write_json(root/'source.json',{'synthetic':True})
 (root/'revision.png').write_text('changed')
 assert not ar.require_references(root,['IMG2'],['S01-001'])[0]
 (root/'revision.png').write_bytes((root/'image.png').read_bytes())
 assert ar.require_references(root,['IMG2'],['S01-001'])[0]

 for mutation in ['fail','unknown','source_hash','image_hash','scope','locator']:
  bad=copy.deepcopy(e)
  if mutation in ['fail','unknown']:bad['checks']['product_phase']['result']=mutation
  elif mutation=='source_hash':bad['source_files'][0]['sha256']='0'*64
  elif mutation=='image_hash':bad['image_sha256']='0'*64
  elif mutation=='scope':bad['source_shot_ids']=['S99-999']
  else:bad['view']['locator']=''
  try:q.validate_image_evidence(root,root/'image.png',bad,['S01-001']);out='ACCEPTED'
  except ValueError as ex:out=str(ex)
  assert out != 'ACCEPTED', mutation
  results['quality_'+mutation]=out
assert not results['creator_mask_field_errors']
assert results['revision_with_scope'][0], results
assert results['receipt_with_scope'][0]
assert not results['receipt_no_shots'][0]
for flag in [False,True]:
 w=copy.deepcopy(v);w.update(creator_mask_face_effect=flag,source_paths=['method.md']);assert not sc.schema_errors(w,'visual-supplement-request.schema.json')
w['creator_mask_face_effect']='true';assert sc.schema_errors(w,'visual-supplement-request.schema.json')
print('PASS: SD01 schema variants; SD02 complete revision/adoption/consume; legacy receipts; scope and evidence drift rejection')
