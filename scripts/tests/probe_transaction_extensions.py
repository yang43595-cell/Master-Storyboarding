"""Synthetic offline transaction checks; never production state."""
import sys, tempfile, copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.core import revision_control as rc
from scripts.core.project_state import new_project_state
from scripts.core.io import read_json
with tempfile.TemporaryDirectory() as t:
 root=Path(t);target=root/'00_项目管理/project_state.json'
 state=new_project_state();state['custom_user_field']={'preserve':[1,2]}
 rc._commit_json_transaction_unlocked(root,{target:state},reason='synthetic',preserve_state_extensions=True)
 assert read_json(target)==state
 before=target.read_bytes()
 for field,bad in [('user_confirmations',None),('current_stage',17),('finalization','wrong'),('accepted_issues',False)]:
  invalid=copy.deepcopy(state);invalid.pop('schema_version');invalid[field]=bad
  try:rc._commit_json_transaction_unlocked(root,{target:invalid},reason='synthetic',preserve_state_extensions=True)
  except ValueError:pass
  else:raise AssertionError(field+' wrongly coerced')
  assert target.read_bytes()==before
 try:rc._commit_json_transaction_unlocked(root,{target:state},reason='synthetic')
 except ValueError:pass
 else:raise AssertionError('default unexpectedly accepts extension')
 # Simulated partial write recovers the same raw extended state without migration.
 changed=copy.deepcopy(state);changed['custom_user_field']['preserve'].append(3)
 try:rc._commit_json_transaction_unlocked(root,{target:changed},reason='synthetic',preserve_state_extensions=True,fail_after=1)
 except RuntimeError:pass
 rc.recover_transactions(root)
 assert read_json(target)==changed
print('PASS: extension preserved; known malformed fields rejected before writes; default strict; recovery preserves raw state')
