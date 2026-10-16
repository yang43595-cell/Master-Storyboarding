const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const ctx=vm.createContext({document:{addEventListener(){}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../visual_asset_ui/scenes.js'),'utf8'),ctx);
test('scene cover uses 45 degrees regardless of import order; project names do not merge',()=>{
  ctx.assets=[{id:'front',group:'卧室',project:'A',name:'正面'}, {id:'angle',group:'卧室',project:'A',name:'45°斜俯视'}, {id:'other',group:'卧室',project:'B',name:'正面'}];
  const groups=vm.runInContext('sceneGroups(assets)',ctx);
  assert.equal(groups.length,2);
  assert.equal(groups[0].cover.id,'angle');
  assert.equal(groups[0].items.length,2);
});
test('explicit view wins; generated job ids keep identically named scenes separate',()=>{
  ctx.assets=[{id:'a',group:'卧室',name:'背面',scene_view:'45°',provenance:{job_id:'one'}},{id:'b',group:'卧室',provenance:{job_id:'two',view:'90°俯瞰'}}];
  assert.equal(vm.runInContext('sceneGroups(assets).length',ctx),2);
  assert.equal(vm.runInContext('sceneView(assets[0])',ctx),'45°');
  assert.equal(vm.runInContext('sceneView(assets[1])',ctx),'俯瞰');
});

test('environment card exposes arbitrary-angle input separately from delivered images',()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  const c=vm.createContext({state:{assets:[{id:'top',name:'俯视布局'}]},workspaceBusy:()=>false,esc:String});
  vm.runInContext(code.slice(code.indexOf('function sceneReferencePanel('),code.indexOf('function openStep(')),c);
  const html=vm.runInContext("sceneReferencePanel({requirement:{asset_id:'AST-ROOM'},reference_ids:['top']})",c);
  assert.match(html,/任意视角/);
  assert.match(html,/输入参考不计作交付图/);
  assert.match(html,/data-w-scene-upload="AST-ROOM"/);
  assert.match(html,/\/media\/top\/thumbnail/);
});

test('reference picker works before a job exists and uses the reference action',()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  const elements=new Map(['#req-search','#req-show-all','#req-library-grid','#req-match-note','#req-folder-back','#req-folder-title'].map(k=>[k,{value:'',classList:{toggle(){}}}]));
  const c=vm.createContext({state:{assets:[{id:'top',name:'卧室俯视图',category:'场景'}]},
    requirementRow:()=>({requirement:{name:'卧室',asset_id:'AST-ROOM',category:'LOCATION'}}),
    stepCategories:{环境:'场景'},isAdvertisingProduct:()=>false,esc:String,modal(){},$:k=>elements.get(k)});
  vm.runInContext(code.slice(code.indexOf('function chooseStepImage('),code.indexOf('const generationDirections=')),c);
  vm.runInContext("chooseStepImage('', 'AST-ROOM')",c);
  assert.match(elements.get('#req-library-grid').innerHTML,/data-w-scene-reference-id="top"/);
  assert.doesNotMatch(elements.get('#req-library-grid').innerHTML,/data-w-pick-id=/);
});

test('asset picker keeps library folders and selects only eligible images inside them',()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  const elements=new Map(['#req-search','#req-show-all','#req-library-grid','#req-match-note','#req-folder-back','#req-folder-title'].map(k=>[k,{value:'',classList:{toggle(){}}}]));
  const grid=elements.get('#req-library-grid');
  const c=vm.createContext({state:{assets:[
    {id:'outfit-a',name:'睡衣',category:'人物1x4',folder_id:'princess',folder_name:'公主',folder_cover:'outfit-b',folder_order:2},
    {id:'outfit-b',name:'礼服',category:'人物1x4',folder_id:'princess',folder_name:'公主',folder_order:2},
    {id:'head',name:'脸',category:'大头照',folder_id:'princess',folder_name:'公主'},
    {id:'trashed',name:'已删礼服',category:'人物1x4',folder_id:'princess',folder_name:'公主',trashed_at:'now'},
    {id:'prince',name:'西装',category:'人物1x4',folder_id:'prince',folder_name:'王子',folder_order:1},
    {id:'other-project',name:'另一个公主',category:'人物1x4',folder_id:'other',folder_name:'公主',folder_order:3},
    {id:'loose',name:'未分组人物',category:'人物1x4'}]},
    lookupProjectSlot:()=>({row:{requirement:{category:'CHARACTER'}},step:{kind:'人物1x4'}}),
    requirementScene:'',stepCategories:{'人物1x4':'人物1x4'},isAdvertisingProduct:()=>false,esc:String,modal(){},$:k=>elements.get(k)});
  vm.runInContext(code.slice(code.indexOf('function chooseStepImage('),code.indexOf('const generationDirections=')),c);
  vm.runInContext("chooseStepImage('job:0')",c);
  assert.match(grid.innerHTML,/data-w-pick-folder="princess"/);
  assert.match(grid.innerHTML,/data-w-pick-folder="other"/);
  assert.ok(grid.innerHTML.indexOf('data-w-pick-folder="prince"')<grid.innerHTML.indexOf('data-w-pick-folder="princess"'));
  assert.match(grid.innerHTML,/\/media\/outfit-b\/thumbnail/);
  assert.match(grid.innerHTML,/data-w-pick-id="loose"/);
  assert.doesNotMatch(grid.innerHTML,/data-w-pick-id="outfit-a"/);
  grid.onclick({target:{closest:()=>({dataset:{wPickFolder:'princess'}})}});
  assert.equal(elements.get('#req-folder-title').textContent,'公主');
  assert.match(grid.innerHTML,/data-w-pick-id="outfit-a" data-key="job:0"/);
  assert.match(grid.innerHTML,/data-w-pick-id="outfit-b"/);
  assert.doesNotMatch(grid.innerHTML,/data-w-pick-id="(?:head|trashed|prince|other-project|loose)"/);
  elements.get('#req-folder-back').onclick();
  elements.get('#req-search').value='公主';elements.get('#req-search').oninput();
  assert.match(grid.innerHTML,/data-w-pick-folder="princess"/);
  assert.match(grid.innerHTML,/data-w-pick-folder="other"/);
  assert.doesNotMatch(grid.innerHTML,/data-w-pick-folder="prince"|data-w-pick-id="loose"/);
});

test('legacy single-image jobs are not shown as already having four outputs',()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  const c=vm.createContext({rowSceneSteps:r=>r.job.steps});
  vm.runInContext(code.slice(code.indexOf('function sceneOutputMode('),code.indexOf('function sceneReferencePanel(')),c);
  assert.equal(vm.runInContext("sceneOutputMode({job:{steps:[{view:'45°'}]}})",c),'single');
  assert.equal(vm.runInContext("sceneOutputMode({job:{steps:[{view:'45°'},{view:'正面'}]}})",c),'coverage');
});

test('environment reference buttons reach the click handler',async()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  const calls=[];
  const c=vm.createContext({guarded:fn=>fn(),chooseStepImage:(...args)=>calls.push(args),
    projectApi:async(...args)=>calls.push(args),reloadRequirement:async()=>{},toast(){}});
  vm.runInContext(code.slice(code.indexOf('function requirementClick('),code.indexOf("projectSection.addEventListener('click'")),c);
  for(const [attribute,dataset] of [['data-w-scene-pick',{wScenePick:'AST-ROOM'}],
      ['data-w-scene-reference-id',{wSceneReferenceId:'top',ast:'AST-ROOM'}]]){
    c.event={target:{closest:selector=>selector.includes('['+attribute+']')?{dataset}:null},preventDefault(){},stopImmediatePropagation(){}};
    vm.runInContext('requirementClick(event)',c);
    await new Promise(resolve=>setImmediate(resolve));
  }
  assert.equal(calls[0][1],'AST-ROOM');
  assert.equal(calls[1][0],'scene-reference');
  assert.equal(calls[1][1].asset_id,'top');
});

test('human card offers single-image route before legacy missing-outfit block',()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  let html='';
  const c=vm.createContext({requirementModal:null,requirementScene:'',esc:String,
    requirementRow:()=>({requirement:{asset_id:'AST-PERSON',name:'一次配角'},job:null,uses_wardrobe:true,outfit_error:'缺少服装状态'}),
    unresolvedScene:()=>false,modal:(title,body)=>html=body});
  vm.runInContext(code.slice(code.indexOf('function characterModeButtons('),code.indexOf('function assetStepLabel(')),c);
  vm.runInContext("openRequirement('AST-PERSON')",c);
  assert.match(html,/data-w-character-mode="single"/);
  assert.match(html,/data-w-character-mode="wardrobe"/);
  assert.doesNotMatch(html,/缺少服装状态/);
});

test('single character choice dispatches the new route endpoint without generating',async()=>{
  const code=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
  const calls=[];
  const c=vm.createContext({requirementRow:()=>({job:null}),projectData:{},
    projectApi:async(action,payload)=>{calls.push({action,payload});return {};},
    reloadRequirement:async ast=>calls.push({reload:ast}),guarded:async fn=>await fn()});
  const start=code.indexOf('async function requirementClick(');
  const end=code.indexOf('\nprojectSection.addEventListener',start);
  vm.runInContext(code.slice(start,end<0?undefined:end),c);
  c.event={target:{closest:()=>({dataset:{wCharacterMode:'single',ast:'AST-PERSON'}})},preventDefault(){},stopImmediatePropagation(){}};
  await vm.runInContext('requirementClick(event)',c);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[0].action,'character-output-mode');
  assert.equal(calls[0].payload.mode,'single');
  assert.equal(calls[1].reload,'AST-PERSON');
  assert.equal(calls.length,2);
});
