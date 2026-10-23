#!/usr/bin/env python3
"""Repair assertions: real HTTP Handler -> Projects.get -> preview -> check_binding.
The project is an isolated, explicitly synthetic confirmed TEST fixture with real
GLB bytes and real registered scene binding. No validation function is stubbed.
Path.open is transparently observed to count actual binary reads, never replaced
with synthetic file data. Run after the Blender project's preview was exported.
"""
import sys, os, json, shutil, threading, hashlib, urllib.request, urllib.error, collections, tempfile
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
REPO = Path(__file__).resolve().parents[2]
HERE = Path(os.environ.get('MSB_TEST_OUTPUT', str(REPO / '.repair-evidence/preview')))
HERE.mkdir(parents=True, exist_ok=True)
sys.path[:0]=[str(REPO),str(REPO/'scripts')]
import visual_asset_previs as p
import visual_asset_library as app
from visual_asset_projects import Projects
if len(sys.argv) != 2:
 raise SystemExit('Pass the existing native Blender project fixture as the only argument.')
SOURCE = Path(sys.argv[1]).resolve()
WORK = Path(tempfile.mkdtemp(prefix='http-', dir=HERE))
ROOT = WORK / 'project'
shutil.copytree(SOURCE,ROOT)
previews=list((ROOT/p.PREVIEW_FOLDER).glob('*/preview_manifest.json'))
if not previews:raise SystemExit('No native exported preview yet; rerun after export, do not substitute a stub.')
folder=previews[0].parent;preview_id=folder.name
manifest=json.loads(previews[0].read_text());real=p.inspect_preview(ROOT,preview_id)
assert real['status']=='ready',real
# Same exported bytes, different list entry: valid inspect currently does not bind
# folder id to shot+revision; this is a read-volume fixture, not a second shot.
second=folder.with_name('SECOND_VIEW');shutil.copytree(folder,second)
LIB=WORK/'library';LIB.mkdir(exist_ok=True)
lib=SimpleNamespace(root=LIB);lib.projects=Projects(lib,None,None)
ID='aaaaaaaaaaaaaaaa';EMPTY_ID='bbbbbbbbbbbbbbbb'
lib.projects.save({'id':ID,'path':str(ROOT),'name':'TEST-only preview'})
empty=WORK/'empty_project';empty.mkdir(exist_ok=True)
lib.projects.save({'id':EMPTY_ID,'path':str(empty),'name':'TEST-only empty'})
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);server.library=lib
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}'
original_open=Path.open;events=[];after_read=None
class Observed:
 def __init__(self,f,path,mode):self.f,self.path,self.mode=f,str(path),mode
 def __getattr__(self,k):return getattr(self.f,k)
 def __enter__(self):self.f.__enter__();return self
 def __exit__(self,*a):return self.f.__exit__(*a)
 def read(self,*a):
  global after_read
  value=self.f.read(*a)
  if value and after_read is not None:
   callback, target = after_read
   if self.path == str(target):
    after_read = None
    callback()
  if value:events.append({'path':self.path,'mode':self.mode,'bytes':len(value) if isinstance(value,bytes) else len(value.encode('utf-8'))})
  return value
 def __iter__(self):return iter(self.f)
def observed_open(path,mode='r',*a,**kw):
 f=original_open(path,mode,*a,**kw);events.append({'path':str(path),'mode':mode,'bytes':0,'open':True});return Observed(f,path,mode)
Path.open=observed_open
routes={'list':f'/api/previs?project_id={ID}','detail':f'/api/previs?project_id={ID}&preview_id={preview_id}','model':f'/previs-media/{ID}/{preview_id}/preview.glb'}
def get(route,headers=None):
 request=urllib.request.Request(url+route,headers=headers or {})
 try:
  with urllib.request.urlopen(request) as response:body=response.read();status=response.status;ctype=response.headers.get('Content-Type')
 except urllib.error.HTTPError as error:body=error.read();status=error.code;ctype=error.headers.get('Content-Type')
 return {'status':status,'content_type':ctype,'body_sha256':hashlib.sha256(body).hexdigest(),'json':json.loads(body) if 'json' in (ctype or '') else None}
def counts():
 grouped=collections.defaultdict(lambda:{'read_chunks':0,'open_count':0,'bytes':0})
 for e in events:
  if e['mode']=='rb':
   grouped[e['path']]['open_count' if e.get('open') else 'read_chunks']+=1;grouped[e['path']]['bytes']+=e['bytes']
 return dict(grouped)
results=[]
try:
 for kind,route in routes.items():
  events.clear();response=get(route);results.append({'case':'normal_'+kind,'route':route,'response':response,'binary_reads':counts()});assert response['status']==200
  if kind == 'list':
   assert next(item for item in response['json']['previews'] if item['id'] == preview_id)['status'] == 'pending'
   assert all(item['status'] in {'pending', 'error'} for item in response['json']['previews'])
  if kind == 'detail':
   assert response['json']['status'] == 'pending'
   routes['model'] = response['json']['glb_url']
  if kind in {'list', 'detail'}:
   assert all(Path(path).name in {'preview_manifest.json', 'export_error.json'} or not Path(path).is_relative_to(ROOT) for path in counts()), counts()
 def check(name,route,change=None,restore=None,headers=None):
  if change:change()
  events.clear();response=get(route,headers);results.append({'case':name,'response':response,'binary_reads':counts()})
  if restore:restore()
  return response
 for source in manifest['sources']:
  path=ROOT/source['path'];saved=path.read_bytes()
  r=check('changed_'+source['role'],routes['model'],lambda p=path,s=saved:p.write_bytes(s+b' '),lambda p=path,s=saved:p.write_bytes(s))
  assert r['status'] >= 400
 glb=folder/'preview.glb';saved=glb.read_bytes()
 assert check('changed_glb',routes['model'],lambda:glb.write_bytes(saved+b'tamper'),lambda:glb.write_bytes(saved))['status']>=400
 # Current upstream selected/confirmed data are verified independently of export manifest.
 binding=json.loads((ROOT/next(s['path'] for s in manifest['sources'] if s['role']=='binding')).read_text())
 text=ROOT/binding['source_director_text']['path'];saved_text=text.read_bytes()
 assert check('changed_current_director',routes['model'],lambda:text.write_bytes(saved_text+b'changed'),lambda:text.write_bytes(saved_text))['status'] >= 400
 state=ROOT/'00_项目管理/project_state.json';saved_state=state.read_bytes();data=json.loads(saved_state);data['user_confirmations']=[]
 assert check('revoked_current_confirmation',routes['model'],lambda:state.write_text(json.dumps(data)),lambda:state.write_bytes(saved_state))['status'] >= 400
 assert check('empty_other_project',f'/api/previs?project_id={EMPTY_ID}')['json']=={'previews':[]}
 assert check('cross_project_model',f'/previs-media/{EMPTY_ID}/{preview_id}/preview.glb')['status']>=400
 assert check('unconnected_project','/api/previs?project_id=cccccccccccccccc')['status']>=400
 assert check('path_traversal',f'/api/previs?project_id={ID}&preview_id=..%2Foutside')['status']>=400
 assert check('foreign_origin',routes['list'],headers={'Origin':'https://foreign.invalid'})['status']==403
 manifest_path=folder/'preview_manifest.json';saved_manifest=manifest_path.read_bytes();bad=json.loads(saved_manifest);bad['sources'][0]['path']='../../outside'
 assert check('source_traversal',routes['detail'],lambda:manifest_path.write_text(json.dumps(bad)),lambda:manifest_path.write_bytes(saved_manifest))['json']['status']=='error'
 # Unrelated metadata does not invalidate actual preview (contrast reading cache).
 d=json.loads(saved_state);d.setdefault('history',[]).append({'test':'unrelated'})
 assert check('unrelated_history',routes['model'],lambda:state.write_text(json.dumps(d)),lambda:state.write_bytes(saved_state))['status'] == 200
 # Manifest identity binds selected camera metadata to the later model request.
 changed = json.loads(saved_manifest); changed['fps'] += 1
 assert check('manifest_changed_after_detail', routes['model'],
              lambda: manifest_path.write_text(json.dumps(changed)),
              lambda: manifest_path.write_bytes(saved_manifest))['status'] >= 400
 # No persistent mtime/size trust: same size + original mtime still rejects.
 stat = glb.stat(); tampered = bytes([saved[0] ^ 1]) + saved[1:]
 def same_stat_change():
  glb.write_bytes(tampered); os.utime(glb, ns=(stat.st_atime_ns, stat.st_mtime_ns))
 assert check('same_size_mtime_model_change', routes['model'], same_stat_change,
              lambda: glb.write_bytes(saved))['status'] >= 400
 # Mutation immediately after the actual model read cannot substitute new bytes.
 after_read = (lambda: glb.write_bytes(tampered), glb)
 race = check('model_replaced_after_read', routes['model'], restore=lambda: glb.write_bytes(saved))
 assert race['status'] == 200 and race['body_sha256'] == hashlib.sha256(saved).hexdigest()
 assert results[-1]['binary_reads'][str(glb)]['open_count'] == 1
 assert results[-1]['binary_reads'][str(glb)]['bytes'] == len(saved)
 # Authority is checked after the model buffer has been acquired.
 after_read = (lambda: state.write_text(json.dumps(data)), glb)
 assert check('confirmation_revoked_during_model_read', routes['model'],
              restore=lambda: state.write_bytes(saved_state))['status'] >= 400
 # Scene authority and confirmed asset bytes are still dependencies.
 catalog = ROOT / '00_项目管理/artifact_manifest.json'; saved_catalog = catalog.read_bytes()
 entries = json.loads(saved_catalog)
 for entry in entries['artifacts']:
  if entry['artifact_id'] == binding['scene_base']['artifact_id']: entry['status'] = 'stale'
 assert check('scene_base_no_longer_current', routes['model'],
              lambda: catalog.write_text(json.dumps(entries)),
              lambda: catalog.write_bytes(saved_catalog))['status'] >= 400
 base = json.loads((ROOT / binding['scene_base']['path']).read_text())
 asset = ROOT / base['appearance_references'][0]['path']; saved_asset = asset.read_bytes()
 assert check('confirmed_asset_bytes_changed', routes['model'],
              lambda: asset.write_bytes(saved_asset + b'changed'),
              lambda: asset.write_bytes(saved_asset))['status'] >= 400
finally:
 Path.open=original_open;server.shutdown();server.server_close();thread.join()
normal=[r for r in results if r['case'].startswith('normal_')]
selected_reads=sum(r['binary_reads'].get(str(glb),{}).get('open_count',0) for r in normal)
other_reads=sum(r['binary_reads'].get(str(second/'preview.glb'),{}).get('open_count',0) for r in normal)
assert selected_reads == 1, selected_reads
assert other_reads == 0, other_reads
assert sum(r['binary_reads'].get(str(glb), {}).get('bytes', 0) for r in normal) == glb.stat().st_size
summary={'cases':len(results),'selected_glb_read_calls':selected_reads,'selected_glb_bytes':sum(r['binary_reads'].get(str(glb),{}).get('bytes',0) for r in normal),'selected_glb_size':glb.stat().st_size,'other_list_glb_read_calls':other_reads,'all_test_assertions_passed':True,'real_http':True,'real_binding_check':True,'renderer_not_exercised_by_http_test':True}
(HERE/'preview_http_repair_results.json').write_text(json.dumps({'summary':summary,'source_fixture':str(SOURCE),'preview':preview_id,'results':results},ensure_ascii=False,indent=2));print(json.dumps(summary))

shutil.rmtree(WORK)
