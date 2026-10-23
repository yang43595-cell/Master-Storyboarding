const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/app.js'),'utf8').split('function options(')[0];
test('drafting and reviewing clear old text and disable prompt approval',()=>{
  const full=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/app.js'),'utf8');
  for(const status of ['drafting','reviewing']){
    const elements=new Map();
    const ctx=vm.createContext({document:{querySelector(selector){if(!elements.has(selector))elements.set(selector,{value:'旧模板',checked:true});return elements.get(selector);}}});
    vm.runInContext("const $=s=>document.querySelector(s);"+full.slice(full.indexOf('function auditBusy('),full.indexOf('function auditLabel(')),ctx);
    vm.runInContext(`showPendingPrompt({status:'${status}'})`,ctx);
    assert.equal(elements.get('#prompt-editor').value,'');
    assert.match(elements.get('#prompt-editor').placeholder,/完成后/);
    for(const selector of ['#prompt-editor','#generate-step','#approve-prompt','#authorize-step'])assert.equal(elements.get(selector).disabled,true);
    assert.equal(elements.get('#approve-prompt').checked,false);
  }
});
function context(fetch){
  const elements=new Map();
  const document={querySelector(selector){if(!elements.has(selector))elements.set(selector,{hidden:true,open:true,classList:{add(){}}});return elements.get(selector);}};
  const ctx=vm.createContext({fetch,document,setTimeout:()=>0,clearTimeout(){}});
  vm.runInContext(source,ctx);
  return {ctx,elements};
}
test('restart refreshes token and retries only the rejected request',async()=>{
  const calls=[];
  const {ctx}=context(async(url,options)=>{calls.push({url,options});return calls.length===1?{status:403,ok:false,json:async()=>({code:'session_expired'})}:calls.length===2?{ok:true,json:async()=>({token:'new-token'})}:{ok:true,json:async()=>({count:1})};});
  assert.equal((await vm.runInContext("api('/api/trash',{ids:['one']})",ctx)).count,1);
  assert.equal(calls.length,3);
  assert.equal(calls[1].url,'/api/state');
  assert.equal(calls[2].options.headers['X-Library-Token'],'new-token');
  assert.equal(calls[0].options.body,calls[2].options.body);
});
test('operation errors stay visible inside the confirmation dialog; no retry',async()=>{
  let requests=0;
  const {ctx,elements}=context(async()=>{requests++;return {ok:false,status:400,json:async()=>({error:'目标目录已存在'})};});
  await vm.runInContext("guarded(()=>api('/api/move',{ids:['one']}))",ctx);
  assert.equal(requests,1);
  assert.equal(elements.get('#dialog-message').hidden,false);
  assert.equal(elements.get('#dialog-message').textContent,'目标目录已存在');
});
test('network uncertainty is never automatically retried',async()=>{
  let requests=0;
  const {ctx}=context(async()=>{requests++;throw Error('network failure');});
  await assert.rejects(vm.runInContext("api('/api/trash',{ids:['one']})",ctx),/network failure/);
  assert.equal(requests,1);
});

function categoryDropContext(failMove=false){
  const folders=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/folders.js'),'utf8');
  const calls=[];
  const ctx=vm.createContext({calls,async api(route,payload){
    calls.push({route,payload});if(failMove&&route==='/api/move')throw Error('move failed');
  },async refresh(){calls.push({route:'refresh'});},toast(){}});
  vm.runInContext(`const categories=['产品','物品','视频参考'];
    let openFolder='folder',category='产品',page=2,selected=new Set(['one','two']);
    const state={assets:[{id:'one',category:'产品',media_type:'image',folder_id:'folder'},
      {id:'two',category:'产品',media_type:'image',folder_id:'folder'},
      {id:'video',category:'视频参考',media_type:'video',folder_id:'folder'}]};`+
    folders.slice(folders.indexOf('function categoryDropAt('),folders.indexOf('function clearFolderGesture(')),ctx);
  return {ctx,calls};
}

test('folder selection uses the same filters and never includes another folder',()=>{
  const folders=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/folders.js'),'utf8');
  const assets=[{id:'visible',folder_id:'one'},{id:'filtered-out',folder_id:'one'},{id:'other-folder',folder_id:'two'}];
  const ctx=vm.createContext({state:{assets},openFolder:'one',filtered:()=>[assets[0],assets[2]]});
  vm.runInContext(folders.slice(folders.indexOf('function folderMembers('),folders.indexOf('function folderCard(')),ctx);
  assert.deepEqual(Array.from(vm.runInContext('collectionSelectionAssets().map(a=>a.id)',ctx)),['visible']);
  ctx.openFolder=null;
  assert.deepEqual(Array.from(vm.runInContext('collectionSelectionAssets().map(a=>a.id)',ctx)),['visible','other-folder']);
});
test('dragging selected folder images to their category removes the group without moving files',async()=>{
  const {ctx,calls}=categoryDropContext();
  await vm.runInContext("dropAssetsToCategory(gestureAssetIds({id:'one',ids:['one']}),'产品')",ctx);
  assert.equal(calls[0].route,'/api/folder');
  assert.equal(calls[0].payload.action,'remove');
  assert.deepEqual(Array.from(calls[0].payload.ids),['one','two']);
  assert.equal(vm.runInContext('openFolder',ctx),null);
  assert.equal(vm.runInContext('selected.size',ctx),0);
});
test('drag to another category moves before ungrouping and failed moves preserve membership',async()=>{
  const {ctx,calls}=categoryDropContext();
  await vm.runInContext("dropAssetsToCategory(['one'],'物品')",ctx);
  assert.deepEqual(calls.map(c=>c.route),['/api/move','/api/folder','refresh']);
  const failed=categoryDropContext(true);
  await assert.rejects(vm.runInContext("dropAssetsToCategory(['one'],'物品')",failed.ctx),/move failed/);
  assert.deepEqual(failed.calls.map(c=>c.route),['/api/move','refresh']);
  assert.equal(vm.runInContext('openFolder',failed.ctx),'folder');
});
test('all assets preserves categories while incompatible media and trash drops do not mutate',async()=>{
  const {ctx,calls}=categoryDropContext();
  await vm.runInContext("dropAssetsToCategory(['one','video'],'')",ctx);
  assert.equal(calls[0].route,'/api/folder');
  calls.length=0;
  for(const command of ["dropAssetsToCategory(['one'],'视频参考')","dropAssetsToCategory(['video'],'产品')","dropAssetsToCategory(['one'],'trash')"]){
    await assert.rejects(vm.runInContext(command,ctx));
  }
  assert.equal(calls.length,0);
});
