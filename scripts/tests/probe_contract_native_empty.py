"""Offline synthetic native evidence: no web access or media generation."""
import ast,copy,hashlib,json,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.core import quality_evidence as q
BODY='Synthetic action, not real project content.'
source=Path(__file__).with_name('test_native_binding_prompt_aliases.py')
node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='verified_payload')
exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'))
with tempfile.TemporaryDirectory() as t:
 root=Path(t);payload=verified_payload(root,{'prompt':BODY});q.validate_native_binding_claims(payload,root)
 row=payload['generation_segments'][0];row['ordered_media']=[];row.pop('native_entity_type')
 path=root/'SYNTHETIC-readback.json';observed=json.loads(path.read_text());actual=observed['drafts'][0];actual['ordered_media']=[];actual['native_mentions']=[]
 row['native_binding']['input_sha256']=q.fingerprint(q.native_input_binding(row))
 def save():
  path.write_text(json.dumps(observed));row['native_binding']['evidence_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 save();q.validate_native_binding_claims(payload,root)
 for mentions in [None,[{'node_id':'unexpected','media_id':'unexpected'}]]:
  actual['native_mentions']=mentions;save()
  try:q.validate_native_binding_claims(payload,root)
  except ValueError:pass
  else:raise AssertionError('missing/unexpected empty-media evidence accepted')
print('PASS: nonempty strict and empty native evidence; missing/unexpected mentions rejected')
