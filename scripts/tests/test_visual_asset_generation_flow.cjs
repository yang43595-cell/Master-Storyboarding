const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const all=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8');
const source=all.slice(all.indexOf('const generationDirections='),all.indexOf('function openProjectTools('));
function render(steps,{busy=false,reset=true,selected=[],planning=false,batchMode='prepare'}={}){
 const slots=steps.map((step,i)=>({key:String(i),job:{title:'人物',steps},step}));let html;
 const ctx=vm.createContext({eligibleWorkspaceSlots:()=>slots.filter(x=>x.step.status==='waiting'&&x.step.generation_enabled!==false),projectSlotList:()=>slots,workspaceBusy:()=>busy,projectData:{planning:{status:planning?'running':'done'},batch:{mode:batchMode,status:busy?'running':'done',entries:[{status:busy?'running':'done'}]}},projectSelection:new Set(selected),projectDrafts:new Map(),modal:(title,s)=>html=s,esc:s=>String(s),assetStepLabel:s=>s.name,auditBusy:a=>['drafting','reviewing'].includes(a?.status),compactStep:()=>'<button>确认图片</button>'});
 vm.runInContext(source,ctx);vm.runInContext(`openBatchPanel(${reset})`,ctx);return html;
}
test('mixed prompt readiness offers one preparation action',()=>{
 const html=render([{status:'waiting',name:'甲',prompt:''},{status:'waiting',name:'乙',prompt:'已有稿',audit:{status:'passed',prompt:'已有稿'}}]);
 assert.match(html,/data-w-run="prepare"/);assert.doesNotMatch(html,/data-w-run="generate"/);
 assert.match(html,/>准备 1 项</);
 assert.match(html,/data-w-run="generate-ready">确认生成已准备的 1 张/);
});
test('prepared selection advances to explicit generation confirmation',()=>{
 const html=render([{status:'waiting',name:'甲',prompt:'当前稿',audit:{status:'passed',prompt:'当前稿'}}]);
 assert.match(html,/data-w-run="generate"/);assert.doesNotMatch(html,/data-w-run="prepare"/);
});
test('busy and image review states do not offer another submission',()=>{
 const busy=render([],{busy:true});assert.match(busy,/正在准备提示词/);assert.doesNotMatch(busy,/data-w-run/);
 const review=render([{status:'awaiting_review'}]);assert.match(review,/确认图片/);assert.doesNotMatch(review,/data-w-run/);
});
test('progress refresh preserves an empty selection',()=>{
 assert.doesNotMatch(render([{status:'waiting',name:'甲'}],{reset:false}),/data-w-run/);
});

test('direction is optional and escaped in each asset editor',()=>{
 const html=render([{status:'waiting',name:'甲',generation_direction:'米色家居服'}]);
 assert.match(html,/data-w-direction/);assert.match(html,/米色家居服/);assert.match(html,/生成方向/);
});

test('prop labels describe the asset instead of only the camera angle',()=>{
 const code=all.slice(all.indexOf('function assetStepLabel('),all.indexOf('function compactStep('));
 const ctx=vm.createContext({});vm.runInContext(code,ctx);
 assert.equal(vm.runInContext("assetStepLabel({kind:'物品',name:'45°',state:''})",ctx),'物品四视图');
 assert.equal(vm.runInContext("assetStepLabel({kind:'环境',name:'45°'})",ctx),'45°');
});

test('failed review stays at prompt step and offers edit or explicit rewrite, not resubmit',()=>{
 const html=render([{status:'waiting',name:'甲',prompt:'用户正文',audit:{status:'needs_changes',prompt:'用户正文'}}]);
 assert.match(html,/<li class="current">1 准备提示词/);
 assert.match(html,/Luna需修复提示词/);assert.match(html,/data-w-rewrite/);
 assert.match(html,/自动修复并准备/);assert.doesNotMatch(html,/data-w-run="generate"/);
});
test('running preparation remains at step two even when a completed item has passed',()=>{
 const html=render([{status:'waiting',name:'甲',prompt:'稿',audit:{status:'passed',prompt:'稿'}}],{busy:true});
 assert.match(html,/<li class="current">1 准备提示词/);assert.doesNotMatch(html,/data-w-run/);
});
test('blank failed drafts expose the reason and retry at prompt preparation',()=>{
 const html=render([{status:'waiting',name:'甲',prompt:'',audit:{status:'error',message:'本次连接未完成'}}]);
 assert.match(html,/<li class="current">1 准备提示词/);
 assert.match(html,/本次连接未完成/);assert.match(html,/data-w-rewrite="0"/);
 assert.doesNotMatch(html,/data-w-run="generate"/);
});
test('review issues are visible next to the affected image',()=>{
 const html=render([{status:'waiting',name:'甲',prompt:'稿',audit:{status:'needs_changes',issues:['服装与已确认身份冲突']}}]);
 assert.match(html,/<li>服装与已确认身份冲突<\/li>/);assert.match(html,/自动修复并准备/);
});
test('later character outputs name the unconfirmed prerequisites',()=>{
 const ctx=vm.createContext({assetStepLabel:s=>s.name});vm.runInContext(source,ctx);
 ctx.slot={step:{kind:'人物1x4',dependencies:['head','clothes']},job:{steps:[
  {key:'head',name:'共用大头照',status:'confirmed',project_confirmation:{at:'已确认'}},
  {key:'clothes',name:'庆典服装三视图',status:'waiting'}]}};
 assert.equal(vm.runInContext('waitingReason(slot)',ctx),'先生成并确认：庆典服装三视图');
});

test('finished generation displays images even while separate asset planning is running',()=>{
 const html=render([{status:'awaiting_review'}],{planning:true,batchMode:'generate'});
 assert.match(html,/<li class="current">3 确认图片/);
 assert.match(html,/1 张图片待确认/);
 assert.doesNotMatch(html,/正在生成图片/);
 assert.doesNotMatch(html,/data-w-run=/);
});
test('an active generation batch still stays at stage three',()=>{
 const html=render([{status:'awaiting_review'}],{busy:true,planning:true,batchMode:'generate'});
 assert.match(html,/<li class="current">2 确认生成/);
 assert.match(html,/正在生成图片/);
 assert.doesNotMatch(html,/data-w-run=/);
});


test('images awaiting confirmation do not hide other ready prompts',()=>{
 const html=render([{status:'awaiting_review',name:'已出图'},{status:'waiting',name:'另一项',prompt:'稿',audit:{status:'passed',prompt:'稿'}}]);
 assert.match(html,/1 张图片待确认/);assert.match(html,/另一项/);assert.match(html,/data-w-run="generate"/);
});
test('unknown generation outcomes and missing reused images stay actionable',()=>{
 const html=render([{status:'unknown',name:'卧室正面',message:'上次结果待核实'},{status:'waiting',name:'已有衣服',generation_enabled:false}]);
 assert.match(html,/需要处理的缺项/);assert.match(html,/上次结果待核实/);assert.match(html,/尚未选用已有图片/);assert.match(html,/data-w-step=/);assert.doesNotMatch(html,/data-w-run="generate"/);
});
test('repeated output names include the actual prerequisite purpose',()=>{
 const ctx=vm.createContext({assetStepLabel:s=>s.name});vm.runInContext(source,ctx);
 ctx.slot={step:{dependencies:['original']},job:{steps:[{key:'original',name:'四视图',purpose:'吃剩状态母版',status:'waiting'},{key:'eaten',name:'四视图',purpose:'吃掉后的结果',status:'waiting'}]}};
 assert.match(vm.runInContext('waitingReason(slot)',ctx),/吃剩状态母版/);
});
test('workspace dock keeps recovery accessible when only unresolved items remain',()=>{
 const code=all.slice(all.indexOf('function projectExportReady('),all.indexOf('function renderProductUsage('));
 let html;const ctx=vm.createContext({projectData:{id:'p',name:'项目',rows:[{job:{blockers:[]},requirement:{}}],narrative:{}},workspaceBusy:()=>false,projectSlotList:()=>[{step:{status:'unknown'}}],eligibleWorkspaceSlots:()=>[],deliverables:()=>[{status:'unknown'}],projectDrafts:new Map(),requirementCategory:()=>'',requirementFilter:'全部',esc:x=>x,renderProductUsage:()=>'',requirementCard:()=>'',pendingRequirementChoice:null,$:()=>({set innerHTML(v){html=v;}})});
 vm.runInContext(code,ctx);vm.runInContext('renderRequirementWorkspace()',ctx);
 assert.match(html,/data-w-batch >查看缺图原因/);
});

test('all no-image requirements may hand off, pending decisions and missing originals may not',()=>{
 const code=all.slice(all.indexOf('function projectExportReady('),all.indexOf('function renderRequirementWorkspace('));
 const ctx=vm.createContext({workspaceBusy:()=>false,projectSlotList:()=>[],stepAsset:s=>s.asset});
 vm.runInContext(code,ctx);
 ctx.projectData={rows:[],narrative:{asset_requirements:[{image_decision:'无需独立图'}]}};
 assert.equal(vm.runInContext('projectExportReady()',ctx),true);
 ctx.projectData.narrative.asset_requirements.push({image_decision:'待定'});
 assert.equal(vm.runInContext('projectExportReady()',ctx),false);
 ctx.projectData.narrative.asset_requirements.pop();
 ctx.projectSlotList=()=>[{step:{status:'confirmed',project_confirmation:{},asset:{backup:{status:'saved'},file_error:'missing'}}}];
 assert.equal(vm.runInContext('projectExportReady()',ctx),false);
});

test('explicitly excluding every image requirement allows an empty handoff with normal gates',()=>{
 const code=all.slice(all.indexOf('function projectExportReady('),all.indexOf('function renderRequirementWorkspace('));
 const ctx=vm.createContext({workspaceBusy:()=>false,projectSlotList:()=>[],stepAsset:s=>s.asset});
 vm.runInContext(code,ctx);
 ctx.projectData={rows:[],narrative:{asset_requirements:[]}};
 assert.equal(vm.runInContext('projectExportReady()',ctx),false);
 ctx.projectData.narrative._asset_choices={excluded:{'AST-CUP':{name:'杯子'}}};
 assert.equal(vm.runInContext('projectExportReady()',ctx),true);
 ctx.projectData.source_error='上游已变化';assert.equal(vm.runInContext('projectExportReady()',ctx),false);
 ctx.projectData.source_error='';ctx.workspaceBusy=()=>true;assert.equal(vm.runInContext('projectExportReady()',ctx),false);
 ctx.workspaceBusy=()=>false;ctx.projectData.narrative.asset_requirements.push({image_decision:'待定'});
 assert.equal(vm.runInContext('projectExportReady()',ctx),false);
});

test('character final joins batch preparation and generation only after confirmed inputs',()=>{
 const head={key:'shared_head',name:'共用大头照',kind:'大头照',status:'confirmed',project_confirmation:{},dependencies:[]};
 const clothes={key:'outfit_wardrobe',name:'服装三视图',kind:'服装',status:'waiting',dependencies:[]};
 const final={key:'outfit_final',name:'最终人物1×4',kind:'人物1x4',status:'waiting',generation_enabled:true,dependencies:['shared_head','outfit_wardrobe']};
 const job={id:'job',title:'人物',blockers:[],character_workflow:{outfits:[{id:'outfit',route:'build'}]},steps:[head,clothes,final]};
 let html;
 const ctx=vm.createContext({projectData:{rows:[{job}],batch:{status:'done'}},projectKey:(j,i)=>`${j.id}:${i}`,projectSelection:new Set(),projectDrafts:new Map(),workspaceBusy:()=>false,auditBusy:()=>false,modal:(title,s)=>html=s,esc:String,assetStepLabel:s=>s.name,compactStep:()=>'<button>确认图片</button>'});
 const projects=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/projects.js'),'utf8');
 vm.runInContext(projects.slice(projects.indexOf('function projectGenerationReady('),projects.indexOf('function projectHasEdits(')),ctx);
 vm.runInContext(all.slice(all.indexOf('function eligibleWorkspaceSlots('),all.indexOf('function rowSceneSteps('))+source,ctx);
 vm.runInContext('openBatchPanel()',ctx);
 assert.equal(vm.runInContext("projectSelection.has('job:2')",ctx),false);
 assert.match(html,/先生成并确认：服装三视图/);
 clothes.status='confirmed';clothes.project_confirmation={};
 vm.runInContext('openBatchPanel()',ctx);
 assert.equal(vm.runInContext("projectSelection.has('job:2')",ctx),true);
 assert.match(html,/data-w-batch-check="job:2" checked/);assert.match(html,/data-w-run="prepare"/);
 final.prompt='图一服装，图二脸，生成人物1×4';final.audit={status:'passed',prompt:final.prompt};
 vm.runInContext('openBatchPanel()',ctx);
 assert.match(html,/data-w-run="generate">下一步：确认生成 1 张/);
 final.generation_enabled=false;final.dependencies=[];job.character_workflow.outfits[0].route='reuse';
 vm.runInContext('openBatchPanel()',ctx);
 assert.equal(vm.runInContext("projectSelection.has('job:2')",ctx),false);
 assert.doesNotMatch(html,/data-w-run="generate"/);
});
