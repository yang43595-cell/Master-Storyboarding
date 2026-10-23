const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const full=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
function setup(){
 const ctx=vm.createContext({state:{assets:[{id:'bowl',category:'物品',name:'碗'},{id:'room',category:'场景',name:'卧室俯视参考'},{id:'output',category:'场景',name:'已选图'},{id:'deleted',category:'场景',trashed_at:'now'}]},stepCategories:{环境:'场景'},esc:String,auditBusy:()=>false,projectKey:()=> 'job:0',statusName:{},assetStepLabel:s=>s.name});
 vm.runInContext(full.slice(full.indexOf('function stepAsset('),full.indexOf('function eligibleWorkspaceSlots(')),ctx);
 vm.runInContext(full.slice(full.indexOf('function compactStep('),full.indexOf('function characterModal(')),ctx);
 return ctx;
}
test('reference-only slot displays the matching reference but remains incomplete',()=>{
 const ctx=setup();ctx.s={kind:'环境',name:'45°',status:'waiting',reference_ids:['deleted','bowl','room']};ctx.j={steps:[ctx.s]};
 const html=vm.runInContext('compactStep(j,s)',ctx);
 assert.match(html,/\/media\/room\/thumbnail/);
 assert.match(html,/参考已放入 · 待补充/);
 assert.equal(vm.runInContext('stepAsset(s)',ctx),undefined);
 assert.equal(ctx.s.status,'waiting');
});
test('selected output takes priority over references and keeps confirmation label',()=>{
 const ctx=setup();ctx.s={kind:'环境',name:'45°',status:'confirmed',selected:'output',project_confirmation:{id:'confirmation'},reference_ids:['room']};ctx.j={steps:[ctx.s]};
 const html=vm.runInContext('compactStep(j,s)',ctx);
 assert.match(html,/\/media\/output\/thumbnail/);
 assert.match(html,/已确认/);
 assert.doesNotMatch(html,/参考已放入/);
});

test('project references outside the collection still appear as inputs',()=>{
 const ctx=setup();ctx.state.references=[{id:'project-ref',category:'场景',name:'项目环境参考'}];
 ctx.s={kind:'环境',name:'45°',status:'waiting',reference_ids:['project-ref']};ctx.j={steps:[ctx.s]};
 const html=vm.runInContext('compactStep(j,s)',ctx);
 assert.match(html,/\/media\/project-ref\/thumbnail/);
 assert.match(html,/参考已放入/);
 assert.equal(vm.runInContext('stepAsset(s)',ctx),undefined);
 assert.equal(ctx.state.assets.some(a=>a.id==='project-ref'),false);
});

test('new character final is an automatic preview instead of a manual generation slot',()=>{
 const ctx=setup();ctx.s={kind:'人物1x4',name:'最终人物1×4',status:'waiting',outfit_id:'outfit',generation_enabled:true,dependencies:['head','clothes'],reference_ids:['room']};
 ctx.j={character_workflow:{outfits:[{id:'outfit',route:'build'}]},steps:[ctx.s,{key:'head',status:'confirmed',project_confirmation:{}},{key:'clothes',status:'confirmed',project_confirmation:{}}]};
 const projects=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/projects.js'),'utf8');
 vm.runInContext(projects.slice(projects.indexOf('function projectGenerationReady('),projects.indexOf('function projectSlotList(')),ctx);
 assert.equal(vm.runInContext('projectGenerationReady(j,s)',ctx),false);
 const waiting=vm.runInContext('compactStep(j,s)',ctx);
 assert.match(waiting,/disabled/);assert.match(waiting,/确认大头照和服装后自动合成/);
 assert.doesNotMatch(waiting,/参考已放入|\/media\/room\/thumbnail/);
 ctx.s.selected='output';ctx.s.status='confirmed';ctx.s.project_confirmation={method:'已确认大头照与服装三视图自动拼版'};
 const completed=vm.runInContext('compactStep(j,s)',ctx);
 assert.match(completed,/\/media\/output\/thumbnail/);assert.match(completed,/已自动合成/);assert.doesNotMatch(completed,/请看图确认/);
});

test('step reference upload reaches its handler without selecting a finished asset',async()=>{
 const calls=[];
 const ctx=vm.createContext({guarded:fn=>fn(),lookupProjectSlot:()=>({job:{id:'job'},index:0}),
   projectApi:async(...args)=>{calls.push(args);return {message:'已保存参考图'};},
   toast(){},refresh:async()=>{},projectLoad:async()=>{},openStep:key=>calls.push(['open',key])});
 vm.runInContext(full.slice(full.indexOf('async function requirementClick('),full.indexOf("projectSection.addEventListener('click'")),ctx);
 ctx.event={target:{closest:selector=>selector.includes('[data-w-reference-upload]')?{dataset:{wReferenceUpload:'job:0'}}:null},preventDefault(){},stopImmediatePropagation(){}};
 vm.runInContext('requirementClick(event)',ctx);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.length,2);
 assert.equal(calls[0][0],'upload-reference');
 assert.equal(calls[0][1].job_id,'job');
 assert.equal(calls[0][1].index,0);
 assert.deepEqual(calls[1],['open','job:0']);
});

test('interrupted uploads remain visible and generated inputs do not appear twice',()=>{
 const app=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/app.js'),'utf8');
 const jobs={innerHTML:'',insertAdjacentHTML(position,html){this.innerHTML=html+this.innerHTML;}};
 const ctx=vm.createContext({$:()=>jobs,esc:s=>String(s).replace(/</g,'&lt;'),
   renderJobs(){jobs.innerHTML='existing production records';},state:{jobs:[{upload_source_id:'linked'}],references:[
     {id:'interrupted',name:'<用户上传>',record_scope:'upload_reference',provenance:{kind:'uploaded_source'},upload_recognition:{category:'大头照'}},
     {id:'linked',record_scope:'upload_reference',provenance:{kind:'uploaded_source'}},
     {id:'frame',record_scope:'upload_reference',provenance:{kind:'video_frame'}},
     {id:'project-input',record_scope:'project_reference',provenance:{kind:'uploaded_source'}}]}});
 vm.runInContext(app.slice(app.indexOf('const renderGenerationJobs=renderJobs;'),app.indexOf('const backgroundRefreshHandlers=')),ctx);
 vm.runInContext('renderJobs()',ctx);
 assert.match(jobs.innerHTML,/data-upload-choice="interrupted"/);
 assert.match(jobs.innerHTML,/识别结果已保存/);
 assert.match(jobs.innerHTML,/&lt;用户上传>/);
 assert.match(jobs.innerHTML,/existing production records/);
 assert.doesNotMatch(jobs.innerHTML,/data-upload-choice="(?:linked|frame|project-input)"/);
});
