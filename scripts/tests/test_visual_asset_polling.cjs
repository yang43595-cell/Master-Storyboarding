const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const app=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/app.js'),'utf8');
const scheduler=app.slice(app.indexOf('const backgroundRefreshHandlers='),app.indexOf('function auditBusy('));
// Load the actual scheduler; UI rendering is supplied by each caller.
test('hidden and idle views do not fetch; active handlers share one request',async()=>{
 let requests=0,updates=0;
 const ctx=vm.createContext({document:{hidden:true},refresh:async()=>{requests++;},toast:()=>{}});
 // Bound extraction at the end of pollBackground, independent of subsequent UI helpers.
 vm.runInContext(scheduler.slice(0,scheduler.indexOf('\n}',scheduler.indexOf('async function pollBackground('))+2),ctx);
 ctx.active=false;ctx.update=()=>{updates++;};
 vm.runInContext('registerBackgroundRefresh(()=>active,update);registerBackgroundRefresh(()=>active,update);',ctx);
 await vm.runInContext('pollBackground()',ctx);assert.equal(requests,0);
 ctx.document.hidden=false;await vm.runInContext('pollBackground()',ctx);assert.equal(requests,0);
 ctx.active=true;await vm.runInContext('pollBackground()',ctx);assert.equal(requests,1);assert.equal(updates,2);
});
test('slow background requests cannot overlap and resume after completion',async()=>{
 let requests=0,release;
 const ctx=vm.createContext({document:{hidden:false},refresh:()=>{requests++;return new Promise(r=>release=r);},toast:()=>{}});
 vm.runInContext(scheduler.slice(0,scheduler.indexOf('\n}',scheduler.indexOf('async function pollBackground('))+2),ctx);
 vm.runInContext('registerBackgroundRefresh(()=>true);',ctx);
 const first=vm.runInContext('pollBackground()',ctx);
 await vm.runInContext('pollBackground()',ctx);assert.equal(requests,1);
 release();await first;
 const next=vm.runInContext('pollBackground()',ctx);assert.equal(requests,2);release();await next;
});

const projects=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/projects.js'),'utf8');
const projectLoader=projects.slice(projects.indexOf('async function projectLoad('),projects.indexOf('projectNav.onclick='));
function loader(){
 const requests=[],renders=[],input={value:''};
 const ctx=vm.createContext({
  projectApi:(_,payload)=>new Promise((resolve,reject)=>requests.push({id:payload.project_id,resolve,reject})),
  $:()=>input,projectRender:()=>renders.push(vm.runInContext('projectData.id',ctx))
 });
 vm.runInContext('let projectId="A",projectData=null,projectLoading=null;'+projectLoader,ctx);
 return {ctx,requests,renders,input};
}
test('project refreshes merge while a slow request is in flight',async()=>{
 const {ctx,requests,renders}=loader();
 const first=vm.runInContext('projectLoad()',ctx),second=vm.runInContext('projectLoad()',ctx);
 assert.equal(requests.length,1);
 requests[0].resolve({id:'A',path:'path-A'});await Promise.all([first,second]);
 assert.deepEqual(renders,['A']);assert.equal(requests.length,1);
});
test('a project switch discards the old response and loads the latest project without overlap',async()=>{
 const {ctx,requests,renders,input}=loader();
 const first=vm.runInContext('projectLoad()',ctx);
 vm.runInContext('projectId="B"',ctx);const switched=vm.runInContext('projectLoad()',ctx);
 assert.equal(requests.length,1);
 requests[0].resolve({id:'A',path:'path-A'});await new Promise(setImmediate);
 assert.deepEqual(renders,[]);assert.equal(input.value,'');assert.equal(requests[1].id,'B');
 vm.runInContext('projectId="C"',ctx);requests[1].resolve({id:'B',path:'path-B'});await new Promise(setImmediate);
 assert.deepEqual(renders,[]);assert.equal(requests[2].id,'C');
 requests[2].resolve({id:'C',path:'path-C'});await Promise.all([first,switched]);
 assert.deepEqual(renders,['C']);assert.equal(input.value,'path-C');
});
test('a late failure from another project does not prevent the current project from loading',async()=>{
 const {ctx,requests,renders}=loader();
 const pending=vm.runInContext('projectLoad()',ctx);vm.runInContext('projectId="B"',ctx);
 requests[0].reject(Error('old project unavailable'));await new Promise(setImmediate);
 assert.equal(requests[1].id,'B');requests[1].resolve({id:'B',path:'path-B'});await pending;
 assert.deepEqual(renders,['B']);
});
test('clearing the project selection never renders an old response',async()=>{
 const {ctx,requests,renders}=loader();
 const pending=vm.runInContext('projectLoad()',ctx);vm.runInContext('projectId=""',ctx);
 requests[0].resolve({id:'A',path:'path-A'});await pending;
 assert.deepEqual(renders,[]);assert.equal(requests.length,1);
});

test('a cached prompt control uses the selected project and retains its draft when ownership fails',async()=>{
 const sent=[];
 const ctx=vm.createContext({api:async(path,payload)=>{
  sent.push({path,...payload});
  if(payload.project_id!=='A')throw Error('制作项不属于当前项目');
 }});
 const jobApi=projects.slice(projects.indexOf('async function projectJobApi('),projects.indexOf('function projectOptions('));
 const savePrompt=projects.slice(projects.indexOf('async function saveProjectPrompt('),projects.indexOf('\n',projects.indexOf('async function saveProjectPrompt(')));
 vm.runInContext('let projectId="B";const projectDrafts=new Map([["JA:0","新提示词"]]);'+jobApi+savePrompt,ctx);
 const control={job:{id:'JA'},key:'JA:0',index:0,step:{prompt:'原提示词'}};
 ctx.control=control;
 await assert.rejects(vm.runInContext('saveProjectPrompt(control)',ctx),/制作项不属于当前项目/);
 assert.equal(sent[0].project_id,'B');
 assert.equal(vm.runInContext('projectDrafts.get("JA:0")',ctx),'新提示词');
 vm.runInContext('projectId="A"',ctx);
 await vm.runInContext('saveProjectPrompt(control)',ctx);
 assert.equal(sent[1].project_id,'A');
 assert.equal(sent[1].prompt,'新提示词');
 assert.equal(vm.runInContext('projectDrafts.has("JA:0")',ctx),false);
});
