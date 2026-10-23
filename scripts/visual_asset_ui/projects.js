'use strict';
// Uses the same local session and library media routes as the collection UI.
const projectNav=document.createElement('button');projectNav.id='nav-project';projectNav.innerHTML='项目资产 <span>03</span>';
$('#nav-create').after(projectNav);
const projectSection=document.createElement('section');projectSection.id='project-view';projectSection.className='hidden';
projectSection.innerHTML=`<div class="intro"><div><div class="section-code">03 / PROJECT ASSETS</div><h1>从需求，到可用资产。</h1><p>读取项目需求，复用已有图片，按资产 Skill 批量制作与确认。</p></div></div>
 <div class="project-stages"><span>01 选择项目</span>→<span>02 选用或制作</span>→<span>03 逐项确认</span>→<span>04 整表交接</span></div>
 <div class="project-top"><select id="project-workspace" aria-label="已连接项目"><option value="">选择已连接项目</option></select><input id="project-path" placeholder="项目文件夹完整路径，例如 C:\\Users\\LIUYANG\\Desktop\\项目名" aria-label="项目文件夹"><button id="project-connect" class="primary">读取项目需求</button><button id="project-previs" type="button" disabled>白模预览</button></div>
 <div id="project-content"><div class="empty"><h2>连接已有项目</h2><p>读取已确认叙事包中的资产需求表。原需求不会被网页改写。</p></div></div>`;
document.querySelector('main').append(projectSection);
let projectData=null, projectId=localStorage.getItem('asset-workspace')||'', projectSelection=new Set(), projectLoading=null;
const projectDrafts=new Map();
function projectKey(job,index){return `${job.id}:${index}`;}
function projectGenerationReady(job,step){return !(typeof automaticCharacterFinal==='function'&&automaticCharacterFinal(job,step))&&step.generation_enabled!==false&&step.required!==false&&projectReady(job,step)&&(!job.character_workflow||step.kind!=='服装'||job.steps.some(s=>s.key==='shared_head'&&s.status==='confirmed'&&s.project_confirmation));}
function projectReady(job,step){return step.dependencies.every(k=>job.steps.some(s=>s.key===k&&s.project_confirmation&&s.status==='confirmed'));}
function projectSlotList(){return (projectData?.rows||[]).flatMap(r=>(r.job?.steps||[]).map((s,i)=>({row:r,job:r.job,step:s,index:i,key:projectKey(r.job,i)})).filter(x=>x.step.required!==false));}
function projectHasEdits(){return [...projectDrafts].some(([key,value])=>projectSlotList().some(x=>x.key===key&&x.step.prompt!==value));}
async function projectApi(action,p={}){return api('/api/project',{action,project_id:projectId,...p});}
async function projectJobApi(path,p={}){return api(path,{...p,project_id:projectId});}
function projectOptions(){const list=state.projects||[];$('#project-workspace').innerHTML='<option value="">选择已连接项目</option>'+list.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');$('#project-workspace').value=projectId;}
async function projectLoad(){
 if(projectLoading)return projectLoading;
 if(!projectId)return;
 projectLoading=(async()=>{
  while(projectId){
   const requestedId=projectId;
   try{
    const data=await projectApi('detail',{project_id:requestedId});
    if(projectId!==requestedId)continue;
    projectData=data;
    if(!$('#project-path').value.trim())$('#project-path').value=data.path;
    projectRender();return;
   }catch(error){if(projectId===requestedId)throw error;}
  }
 })().finally(()=>{projectLoading=null;});
 return projectLoading;
}
projectNav.onclick=()=>{view('project');projectOptions();guarded(projectLoad);};
$('#project-workspace').onchange=e=>{if(projectHasEdits()&&!confirm('当前有未保存的提示词，切换项目会保留本页草稿；确定切换？')){e.target.value=projectId;return;}projectId=e.target.value;localStorage.setItem('asset-workspace',projectId);projectData=null;$('#project-path').value='';projectSelection.clear();projectRender();guarded(projectLoad);};
$('#project-connect').onclick=e=>guarded(async()=>{const data=await api('/api/project',{action:'bind',path:($('#project-path').value.trim()||projectData?.path||'').replace(/^"|"$/g,'')});projectId=data.id;localStorage.setItem('asset-workspace',projectId);projectData=data;projectSelection.clear();await refresh();projectOptions();projectRender();},e.target);
$('#project-previs').onclick=e=>{e.stopPropagation();guarded(async()=>{const requestedId=projectId;const preview=await import('/previs.js');if(!requestedId||requestedId!==projectId)return;preview.openPrevis({projectId:requestedId,projectName:projectData?.name||'当前项目'});},e.target);};

function requirementDetails(r){
 const labels={image_decision:'处理结论',image_reason:'判断依据',asset_id:'需求编号',entity_id:'关联对象',character_type:'角色类型',wardrobe_states:'服装状态及依据',category:'类别',name:'名称',narrative_purpose:'用途',source_paragraph_ids:'故事原文段落',source_event_ids:'历史来源事件',identity_constraints:'身份与外观',required_states:'所需状态',interactions:'交互',reference_paths:'参考资料',unknowns:'待明确事项'};
 const cats={CHARACTER:'人物',LOCATION:'场景',PROP:'物品',REAL_PRODUCT:'广告产品',WARDROBE:'服装',STYLE_REFERENCE:'风格',OTHER:'其他'};
 const textValue=v=>Array.isArray(v)?(v.length?v.map(x=>typeof x==='object'?(x.name&&x.evidence?`${x.name}（依据：${x.evidence}）`:JSON.stringify(x)):x).join('；'):'无'):typeof v==='object'?JSON.stringify(v):String(v??'未填写');
 return '<dl class="requirement-fields">'+Object.entries(r).map(([k,v])=>`<dt>${esc(labels[k]||k)}</dt><dd>${esc(k==='category'?(cats[v]||v):k==='character_type'?({HUMAN:'人类',HUMANOID:'人形角色',ANIMAL:'动物',NON_HUMANOID:'非人形角色'}[v]||v):textValue(v))}</dd>`).join('')+'</dl>';
}

function projectRender(){
 $('#project-previs').disabled=!projectId;
 window.dispatchEvent(new CustomEvent('previs-project-change',{detail:{projectId}}));
 if(projectData)renderRequirementWorkspace();
 else $('#project-content').innerHTML='<div class="empty"><h2>选择项目</h2><p>选择已连接项目，或填写项目文件夹读取需求。</p></div>';
}

function lookupProjectSlot(key){const x=projectSlotList().find(x=>x.key===key);if(!x)throw Error('制作项已变化，请刷新');return x;}
async function saveProjectPrompt(x){const prompt=projectDrafts.get(x.key)??x.step.prompt;if(!prompt?.trim())throw Error('先填写提示词或点击批量写提示词');await projectJobApi('/api/prompt',{id:x.job.id,index:x.index,prompt});projectDrafts.delete(x.key);}
async function batchProject(mode){
 const selectedSlots=projectSlotList().filter(x=>projectSelection.has(x.key));if(!selectedSlots.length)throw Error('先勾选制作格');
 if(mode==='write'&&selectedSlots.some(x=>projectDrafts.has(x.key))&&!confirm('选中项有未保存的修改；重新撰写会替换这些提示词，是否继续？'))return;
 if(mode!=='write'){for(const x of selectedSlots)if(projectDrafts.has(x.key))await saveProjectPrompt(x);await refresh();await projectLoad();}
 if(mode==='generate'){
  const tasks=projectSlotList().filter(x=>projectSelection.has(x.key));
  if(tasks.some(x=>x.step.audit?.status!=='passed'||x.step.audit.prompt!==x.step.prompt))throw Error('本批有未审查通过或修改后的提示词，请先批量审查');
  modal('确认本批生成',`<p>本批 ${tasks.length} 张独立图片，最多 3 张同时生成。失败不自动重试。</p><label>生成工具<select id="project-provider"><option value="imagegen">ImageGen · Codex 内置模型</option><option value="dreamina">即梦 CLI</option></select></label><label id="project-model-label" class="hidden">即梦模型<select id="project-model">${options(['5.0','5.0Pro','4.7','4.6','4.5','4.1','4.0'],'5.0')}</select></label>${tasks.map(x=>`<details><summary>${esc(x.job.title)} · ${esc(x.step.name)} · ${esc(x.step.ratio)}</summary><pre>${esc(x.step.prompt)}</pre></details>`).join('')}<label class="check-label"><input id="project-authorize" type="checkbox">我已核对本批提示词与参考，确认所选工具、模型及参考上传和账号额度消耗。</label><div class="form-footer"><button id="project-generate-now" class="primary" disabled>授权生成 ${tasks.length} 张</button></div>`);
  $('#project-provider').onchange=()=>$('#project-model-label').classList.toggle('hidden',$('#project-provider').value!=='dreamina');
  $('#project-authorize').onchange=e=>$('#project-generate-now').disabled=!e.target.checked;
  $('#project-generate-now').onclick=e=>guarded(async()=>{const data=await projectApi('batch',{mode,provider:$('#project-provider').value,model:$('#project-model').value,authorize:$('#project-authorize').checked,tasks:tasks.map(x=>({job_id:x.job.id,index:x.index,prompt:x.step.prompt}))});$('#dialog').close();toast(data.message);await projectLoad();if(typeof openBatchPanel==='function')openBatchPanel(false);},e.target);return;
 }
 const response=await projectApi('batch',{mode,tasks:selectedSlots.map(x=>({job_id:x.job.id,index:x.index,generation_direction:typeof directionFor==='function'?directionFor(x):(x.step.generation_direction||'')}))});
 if(mode==='write')for(const x of selectedSlots)projectDrafts.delete(x.key);
 toast(response.message);await projectLoad();
}

function projectPicker(title,onPick,initialCategory=''){
 modal(title,`<div class="toolbar"><input type="search" id="project-pick-search" class="search" placeholder="搜索名称、项目、标签"><select id="project-pick-category"><option value="">全部分类</option>${options(categories,'')}</select></div><div id="project-pick-grid" class="project-pick-grid"></div><p id="project-pick-count" class="muted"></p>`);
 const render=()=>{const query=$('#project-pick-search').value.toLowerCase(),cat=$('#project-pick-category').value;const all=state.assets.filter(a=>!a.trashed_at&&(!cat||a.category===cat)&&[a.name,a.project,...(a.tags||[])].join(' ').toLowerCase().includes(query));$('#project-pick-grid').innerHTML=all.slice(0,150).map(a=>`<button data-project-pick="${a.id}"><img draggable="false" src="/media/${a.id}/thumbnail" alt="${esc(a.name)}"><span>${esc(a.name)}</span><span>${esc(a.category)} · ${a.status==='confirmed'?'库内已确认':'待核对'}</span></button>`).join('');$('#project-pick-count').textContent=`${all.length} 张，显示前 150 张；可通过搜索缩小范围。项目适用性需要再次确认。`;};
 $('#project-pick-category').value=initialCategory;$('#project-pick-search').oninput=render;$('#project-pick-category').onchange=render;
 $('#project-pick-grid').onclick=e=>{const b=e.target.closest('[data-project-pick]');if(b)guarded(async()=>{await onPick(b.dataset.projectPick);$('#dialog').close();await projectLoad();},b);};render();
}

projectSection.addEventListener('input',e=>{if(e.target.dataset.pprompt)projectDrafts.set(e.target.dataset.pprompt,e.target.value);});
projectSection.addEventListener('change',e=>{if(e.target.dataset.psel){const key=e.target.dataset.psel;e.target.checked?projectSelection.add(key):projectSelection.delete(key);e.target.closest('.project-slot').classList.toggle('is-selected',e.target.checked);$('#project-selected').textContent=projectSelection.size;}});
projectSection.addEventListener('click',e=>{
 const b=e.target.closest('button,[data-pimage]');if(!b)return;
 guarded(async()=>{
  if(b.dataset.pimage){modal('查看资产图',`<img class="detail-image" style="height:70vh" src="/media/${b.dataset.pimage}/original" alt="资产原图">`);return;}
  if(b.dataset.pjump){document.getElementById(`project-slot-${b.dataset.pjump}-0`)?.scrollIntoView({behavior:'smooth',block:'center'});return;}
  if(b.dataset.pref){const ast=b.dataset.pref;projectPicker('为此需求选择参考图',id=>projectApi('references',{ast_id:ast,ids:[...projectData.rows.find(r=>r.requirement.asset_id===ast).reference_ids,id]}));return;}
  if(b.dataset.pchoose){const x=lookupProjectSlot(b.dataset.pchoose);projectPicker(b.dataset.mode==='reference'?'添加生成参考':'选用已有资产',id=>projectApi('select',{job_id:x.job.id,index:x.index,asset_id:id,as_reference:b.dataset.mode==='reference'}),b.dataset.mode==='reference'?'':({'大头照':'大头照','服装':'服装三视图','人物1x4':'人物1x4'}[x.step.kind]||''));return;}
  if(b.dataset.pclearRefs){const x=lookupProjectSlot(b.dataset.pclearRefs);await projectApi('clear-references',{job_id:x.job.id,index:x.index});await projectLoad();return;}
  if(b.dataset.presolve){const j=state.jobs.find(j=>j.id===b.dataset.presolve);modal('补充需求缺口',`<p>${esc(j.blockers.join('；'))}</p><label>请说明实际补充的参考或已核实事实<textarea id="project-resolution" rows="4"></textarea></label><p class="muted">不能用猜测代替产品或人物事实。保存后仍需 Codex 审查提示词。</p><button id="project-save-resolution">保存补充说明</button>`);$('#project-save-resolution').onclick=e=>guarded(async()=>{await projectApi('resolve',{job_id:j.id,explanation:$('#project-resolution').value});$('#dialog').close();await projectLoad();},e.target);return;}
  if(b.dataset.psave){await saveProjectPrompt(lookupProjectSlot(b.dataset.psave));await projectLoad();toast('已保存；修改后的提示词需要重新审查');return;}
  if(b.dataset.puseOutput){await projectApi('select',{job_id:b.dataset.job,index:Number(b.dataset.index),asset_id:b.dataset.puseOutput});await projectLoad();return;}
  if(b.dataset.pconfirm){const x=lookupProjectSlot(b.dataset.pconfirm),id=x.step.selected||x.step.outputs[0];modal('确认此图片满足项目需求',`<img class="detail-image" style="height:45vh" src="/media/${id}/original" alt="待确认资产"><p><strong>${esc(x.job.title)} · ${esc(x.step.name)}</strong></p><p>${esc(x.row.requirement.narrative_purpose)}</p><p>${esc(x.step.purpose)} · ${esc(x.step.state)}</p><pre>${esc(JSON.stringify(x.row.requirement,null,2))}</pre>${x.job.blockers.length?`<p class="project-warning">还需核实：${esc(x.job.blockers.join('；'))}</p>`:''}<label class="check-label"><input type="checkbox" id="project-visual-ok">我已查看图片，确认身份、外观、状态、视角及项目约束正确，列出的需求缺口已实际解决。</label><div class="form-footer"><button id="project-confirm-now" class="primary" disabled>确认此制作项</button></div>`);$('#project-visual-ok').onchange=e=>$('#project-confirm-now').disabled=!e.target.checked;$('#project-confirm-now').onclick=e=>guarded(async()=>{await projectApi('confirm',{job_id:x.job.id,index:x.index,asset_id:id,confirm:true,resolve_blockers:true});$('#dialog').close();await refresh();await projectLoad();},e.target);return;}
  if(b.dataset.preopen){const x=lookupProjectSlot(b.dataset.preopen);if(!confirm('保留旧图，重新准备此项及依赖它的后续图，之后需要重新确认。继续？'))return;await projectApi('reopen',{job_id:x.job.id,index:x.index});await projectLoad();return;}
  if(b.dataset.preject){const x=lookupProjectSlot(b.dataset.preject);if(!confirm('将这个制作项的未确认生成结果移入垃圾桶，30天内可恢复？'))return;await projectJobApi('/api/reject-result',{id:x.job.id,index:x.index,redo:true});await refresh();await projectLoad();return;}
  if(b.dataset.preset){const x=lookupProjectSlot(b.dataset.preset);if(!confirm('请先核实上次工具记录，确认可以重新准备。此按钮不会生成图片。'))return;await projectJobApi('/api/imagegen-reset',{id:x.job.id,index:x.index});await projectLoad();return;}
  if(b.dataset.pquery){const x=lookupProjectSlot(b.dataset.pquery);await projectJobApi('/api/query',{id:x.job.id,index:x.index});await projectLoad();return;}
  const action=b.dataset.paction;if(!action)return;
  if(['write','review','generate'].includes(action)){await batchProject(action);return;}
  if(action==='all'){projectSlotList().filter(x=>x.step.generation_enabled!==false&&x.step.status==='waiting'&&projectGenerationReady(x.job,x.step)&&!auditBusy(x.step.audit)&&!x.job.blockers.length).forEach(x=>projectSelection.add(x.key));projectRender();return;}
  if(action==='clear'){projectSelection.clear();projectRender();return;}
  if(action==='refresh'){await refresh();await projectLoad();return;}
  if(action==='backup'){await api('/api/backup-retry',{});await refresh();await projectLoad();return;}
  if(action==='export'){if(!confirm('确认整张资产需求表已完整且选图正确，并导出新版本到此项目文件夹？'))return;const result=await projectApi('export',{confirm:true});toast(`已导出 ${result.count} 张图片到项目 ${result.revision}`);await projectLoad();return;}
  if(action==='continue'){const result=await projectApi('continue');modal('回到 Codex 继续项目',`<p class="project-warning">${esc(result.warning)}</p><p class="paths">${esc(result.handoff)}</p><details open><summary>打开交接内容</summary><pre>${esc(result.content)}</pre></details><label>复制到项目的 Codex 对话<textarea id="project-continue-text" rows="5" readonly>${esc(result.instruction)}</textarea></label><button id="project-copy-continue">复制继续指令</button>`);$('#project-copy-continue').onclick=e=>guarded(async()=>{await navigator.clipboard.writeText(result.instruction);toast('已复制，粘贴到项目的 Codex 对话即可');},e.target);return;}
  const response=await projectApi(action);if(response.message)toast(response.message);await refresh();await projectLoad();
 },b);
});
registerBackgroundRefresh(()=>!projectSection.classList.contains('hidden')&&!$('#dialog').open&&!document.activeElement?.matches('#project-view input,#project-view textarea,#project-view select')&&(projectData?.product_reference_check?.status==='running'||projectData?.batch?.status==='running'||projectSlotList().some(x=>['submitting','querying','generating'].includes(x.step.status)||auditBusy(x.step.audit))),projectLoad);
