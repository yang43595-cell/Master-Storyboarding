"""Synthetic offline probes; repository source unmodified, no external effects."""
import ast,sys,json,copy,hashlib,tempfile
from pathlib import Path
BASE=Path(__file__).resolve().parent
SRC=BASE.parents[1]
sys.path.insert(0,str(SRC))
from scripts.core import artifacts,revision_control as rc
from scripts.core.io import write_json
from scripts.core.project_state import new_project_state,write_project_state
from scripts.check_video_prompt_focal_intent import _json_segments
ns=globals()
def fixture(file,names):
 tree=ast.parse((SRC/'scripts/tests'/file).read_text())
 for n in tree.body:
  if isinstance(n,ast.FunctionDef) and n.name in names:
   exec(compile(ast.Module(body=[n],type_ignores=[]),file,'exec'),ns)
fixture('test_storyboard_planner_contract.py',['sample_package'])
fixture('test_story_paragraph_sources.py',['narrative'])
fixture('test_director_compact.py',['compact_package'])
fixture('test_formal_package_submission.py',['confirmed_narrative','board_candidate','video_project','register'])

from scripts.core.quality_evidence import source_shot_ids
from contextlib import contextmanager
@contextmanager
def rejects():
 try: yield
 except ValueError: pass
 else: raise AssertionError("expected rejection")
for bad in ({'source_shots':['A','A']}, {'source_shots':['A'],'source_shot_ids':['B']}, {'source_shots':[]}):
 with rejects(): source_shot_ids(bad)
assert source_shot_ids({'source_shots':[{'shot_id':'A'}],'source_shot_ids':['A']}) == ['A']
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);old=video_project(root)
 old['generation_segments'][0]['source_shots']=['S01-010']
 write_json(root/'P-OLD.json',old)
 manifest=artifacts.load_manifest(root)
 record=next(x for x in manifest['artifacts'] if x['artifact_id']=='P-OLD')
 record['checksum']=artifacts.digest(root/'P-OLD.json');write_json(root/artifacts.MANIFEST_PATH,manifest)
 candidate=copy.deepcopy(old);candidate['revision']='R02'
 candidate['generation_segments'][0]['source_shots']=['NONEXISTENT-SHOT']
 with rejects(): rc._validate_segment_reconciliation(root,record,candidate)
 candidate['segment_reconciliation']={'baseline':{'artifact_id':'P-OLD','sha256':record['checksum']},'changes':[{'action':'coverage','segment_id':'G-01','field':'source_shots','before':['S01-010'],'after':['NONEXISTENT-SHOT'],'reason':'synthetic'}]}
 # Reconciliation does not by itself prove upstream membership.
 rc._validate_segment_reconciliation(root,record,candidate)
 entry=register(root,'P-NEW','video.prompt',candidate,['storyboard.package'])
 with rejects(): artifacts.validate_candidate_submission(root,entry)
 # Switching aliases preserves identical mapping; same shot across segments remains legal.
 candidate=copy.deepcopy(old);candidate['revision']='R02'
 candidate['generation_segments'][0]['source_shot_ids']=candidate['generation_segments'][0].pop('source_shots')
 candidate['generation_segments'][1]['source_shots']=['S01-010']
 candidate['segment_reconciliation']={'baseline':{'artifact_id':'P-OLD','sha256':record['checksum']},'changes':[{'action':'coverage','segment_id':'G-02','field':'source_shot_ids','before':None,'after':['S01-010'],'reason':'same long shot continuous next subrange'}]}
 entry=register(root,'P-LEGAL','video.prompt',candidate,['storyboard.package'])
 artifacts.validate_candidate_submission(root,entry)
 rc.open_change(root,change_id='SYNTHETIC',tier='T1',logical_ids=['video.prompt'],reason='synthetic')
 rc.confirm_impact(root,change_id='SYNTHETIC',approval_ref='synthetic')
 rc.record_candidate(root,change_id='SYNTHETIC',artifact_id='P-COMMIT',logical_id='video.prompt',artifact_type='video.prompt',candidate_path='P-LEGAL.json',revision='R02')
 rc.record_review(root,change_id='SYNTHETIC',verdict='approved',report_ref='synthetic')
 rc.confirm_candidate(root,change_id='SYNTHETIC',approval_ref='synthetic')
 rc.commit_change(root,change_id='SYNTHETIC')
 assert next(x['status'] for x in artifacts.load_manifest(root)['artifacts'] if x['artifact_id']=='P-COMMIT') == 'current'
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);old=video_project(root);bad=copy.deepcopy(old);bad['revision']='R02'
 bad['generation_segments'][0]['source_shots']=['NONEXISTENT-SHOT']
 prior=next(x for x in artifacts.load_manifest(root)['artifacts'] if x['artifact_id']=='P-OLD')
 bad['segment_reconciliation']={'baseline':{'artifact_id':'P-OLD','sha256':prior['checksum']},'changes':[{'action':'coverage','segment_id':'G-01','field':'source_shot_ids','before':None,'after':['NONEXISTENT-SHOT'],'reason':'synthetic explained but unknown'}]}
 write_json(root/'bad.json',bad)
 rc.open_change(root,change_id='BAD',tier='T1',logical_ids=['video.prompt'],reason='synthetic')
 rc.confirm_impact(root,change_id='BAD',approval_ref='synthetic')
 rc.record_candidate(root,change_id='BAD',artifact_id='BAD',logical_id='video.prompt',artifact_type='video.prompt',candidate_path='bad.json',revision='R02')
 rc.record_review(root,change_id='BAD',verdict='approved',report_ref='synthetic')
 with rejects():rc.confirm_candidate(root,change_id='BAD',approval_ref='synthetic')
 with rejects():rc.commit_change(root,change_id='BAD')
 assert next(x['status'] for x in artifacts.load_manifest(root)['artifacts'] if x['artifact_id']=='BAD') == 'candidate'
print('PASS: aliases/conflicts/unknown upstream rejection; explained coverage; cross-segment long-shot legal full commit')
