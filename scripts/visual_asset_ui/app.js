'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const categories=['大头照','服装三视图','人物1x4','宠物','场景','物品','产品','风格','视频参考','其他'];
const roles=['母版','候选','参考','分镜','历史','待整理'];
let state={assets:[],jobs:[]}, category='', page=0, sourceId=null, selected=new Set(), toastTimer, dragSelect=null, suppressCardClick=false;
const pageSize=36;
function toast(message,error=false){$('#toast').textContent=message;$('#toast').className='toast'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.add('hidden'),error?10000:6500);}
async function api(path,payload){
  const send=()=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Library-Token':state.token},body:JSON.stringify(payload)});
  let response=await send(),data=await response.json();
  // Only retry an explicit token rejection: the server has not performed the action.
  if(response.status===403&&data.code==='session_expired'){
    const fresh=await fetch('/api/state');
    if(!fresh.ok)throw Error('资产库连接失败，请稍后重试');
    state.token=(await fresh.json()).token;
    response=await send();data=await response.json();
  }
  if(!response.ok)throw Error(data.error||'操作失败');return data;
}
async function guarded(action,button){const message=$('#dialog-message');if(message)message.hidden=true;if(button)button.disabled=true;try{return await action();}catch(e){if($('#dialog').open&&message){message.textContent=e.message;message.hidden=false;}toast(e.message,true);}finally{if(button)button.disabled=false;}}
function modal(title,html){$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=html;$('#dialog-message').hidden=true;if(!$('#dialog').open)$('#dialog').showModal();}
function options(items,value){return items.map(i=>`<option ${i===value?'selected':''}>${esc(i)}</option>`).join('');}
let refreshPending=null;
function refresh(){if(refreshPending)return refreshPending;refreshPending=loadLibraryState().finally(()=>{refreshPending=null;});return refreshPending;}
async function loadLibraryState(){const r=await fetch('/api/state');if(!r.ok)throw Error('资产库连接失败');state=await r.json();syncSkillUI();const prev=$('#project-filter').value;const projects=[...new Set(state.assets.map(a=>a.project))].sort();$('#project-filter').innerHTML='<option value="">全部来源项目</option>'+options(projects,prev);$('#total').textContent=state.assets.filter(a=>!a.trashed_at).length;$('#project-count').textContent=projects.length;$('#storage-caption').title=state.vault||state.root;renderCategories();render();renderJobs();$('#cleanup-warning').textContent=!state.management_ready?'移动和垃圾桶功能等待重启资产库服务后启用。':state.cleanup_error?'垃圾桶自动清理暂停：'+state.cleanup_error:'';}
function renderCategories(){$('#categories').innerHTML=[['','全部资产'],...categories.map(c=>[c,c]),['trash','垃圾桶']].map(([id,name])=>`<button data-category="${id}" class="${category===id?'active':''}">${name}<span>${state.assets.filter(a=>id==='trash'?!!a.trashed_at:!a.trashed_at&&(!id||a.category===id)).length}</span></button>`).join('');}
function filtered(){const term=$('#search').value.trim().toLocaleLowerCase();const project=$('#project-filter').value,role=$('#role-filter').value,status=$('#status-filter').value,product=$('#product-filter')?.value||'';return state.assets.filter(a=>(category==='trash'?!!a.trashed_at:!a.trashed_at&&(!category||a.category===category))&&(!project||a.project===project)&&(!role||a.role===role)&&(!status||a.status===status)&&(!product||a.product_category_id===product)&&(!term||[a.name,a.project,a.source,a.group,...a.tags].join(' ').toLocaleLowerCase().includes(term)));}
function render(){if(category==='场景'){renderScenes();return;}sceneFolder=null;document.querySelector('#scene-tools')?.remove();const assets=filtered();page=Math.min(page,Math.max(0,Math.ceil(assets.length/pageSize)-1));$('#result-count').textContent=`${category==='trash'?'垃圾桶':category||'全部资产'} · ${assets.length} 张`;
$('#gallery').innerHTML=assets.length?assets.slice(page*pageSize,(page+1)*pageSize).map(a=>`<article class="card ${selected.has(a.id)?'selected':''}"><input class="select-card" type="checkbox" data-select="${a.id}" aria-label="选择 ${esc(a.name)}" ${selected.has(a.id)?'checked':''}><button class="picture" data-detail="${a.id}" aria-label="查看 ${esc(a.name)}"><img src="/media/${a.id}/thumbnail" alt="${esc(a.name)}" loading="lazy" draggable="false"></button><div class="card-body"><div class="card-title" title="${esc(a.name)}">${esc(a.name)}</div><div class="card-project">${esc(a.project||'未标注来源')}</div><div class="card-meta"><span class="pill">${esc(a.category)} · ${esc(a.role)}</span><span>${a.trashed_at?'将在 '+new Date(a.expires_at).toLocaleDateString()+' 自动删除':a.status==='confirmed'?'已确认':'待核对'} · ${a.width}×${a.height}</span></div></div></article>`).join(''):'<div class="empty full"><h2>这里还没有匹配的资产</h2><p>添加图片，或调整搜索和筛选条件。</p></div>';
$('#pager').innerHTML=assets.length>pageSize?`<button data-page="-1" ${page===0?'disabled':''}>上一页</button><span class="muted">${page+1} / ${Math.ceil(assets.length/pageSize)}</span><button data-page="1" ${(page+1)*pageSize>=assets.length?'disabled':''}>下一页</button>`:'';renderSelection();}
function renderSelection(){for(const id of ['move-selection','trash-selection','restore-selection','purge-selection']){$('#'+id).disabled=!state.management_ready;$('#'+id).title=state.management_ready?'':'重启资产库服务后可用';}for(const id of ['move-selection','trash-selection','export-selection'])$('#'+id).classList.toggle('hidden',category==='trash');for(const id of ['restore-selection','purge-selection'])$('#'+id).classList.toggle('hidden',category!=='trash');$('#selection-bar').classList.toggle('hidden',!selected.size);$('#selected-count').textContent=`已选择 ${selected.size} 项`;}
function view(which){document.querySelector('#project-view')?.classList.toggle('hidden',which!=='project');document.querySelector('#nav-project')?.classList.toggle('active',which==='project');if(which==='project')$('#selection-bar').classList.add('hidden');$('#library-view').classList.toggle('hidden',which!=='library');$('#create-view').classList.toggle('hidden',which!=='create');$('#nav-library').classList.toggle('active',which==='library');$('#nav-create').classList.toggle('active',which==='create');}
function detail(id){const a=state.assets.find(a=>a.id===id);if(!a)return;if(a.trashed_at){modal(a.name,`<img class="detail-image" src="/media/${a.id}/original" alt="${esc(a.name)}"><p>原分类：${esc(a.restore_category||a.category)}</p><p>自动删除时间：${esc(new Date(a.expires_at).toLocaleString())}</p><button id="restore-one">恢复图片</button>`);$('#restore-one').onclick=e=>guarded(async()=>{await api('/api/restore',{ids:[id]});$('#dialog').close();await refresh();toast('已恢复到原分类');},e.currentTarget);return;}modal(a.name,`<div class="detail-layout"><div><img class="detail-image" src="/media/${a.id}/original" alt="${esc(a.name)}"><p class="muted">${a.width} × ${a.height} · ${(a.bytes/1048576).toFixed(1)} MB</p></div><form id="detail-form" class="detail-fields"><input type="hidden" name="id" value="${a.id}"><label>名称<input name="name" value="${esc(a.name)}" required></label><div class="form-grid"><label>分类<select name="category">${options(categories,a.category)}</select></label><label>用途<select name="role">${options(roles,a.role)}</select></label></div><label>标签（逗号分隔）<input name="tags" value="${esc(a.tags.join('，'))}" placeholder="夏季，粉色，王室"></label><label>所属资产组<input name="group" value="${esc(a.group)}" placeholder="例如：公主粉色睡袍"></label><div class="form-footer"><button type="button" data-confirm="${a.id}" ${a.status==='confirmed'?'disabled':''}>确认此图片</button><button type="submit">保存标签</button></div><button type="button" class="primary" data-use="${a.id}">用这张照片制作资产</button></form></div>`);
$('#detail-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{const p=Object.fromEntries(new FormData(e.target));p.tags=p.tags.split(/[,，]/).map(t=>t.trim()).filter(Boolean);await api('/api/update',p);await refresh();toast('已保存');},e.submitter);};}
function sourceImages(){return state.assets.concat((state.references||[]).filter(a=>a.record_scope==='upload_reference'));}
function useSource(id){const a=sourceImages().find(a=>a.id===id);if(a?.media_type==='video'){toast('视频请从上传文件入口抽帧制作',true);return;}if(!a)return;sourceId=id;$('#dialog').close();view('create');$('#source-preview').innerHTML=`<img src="/media/${id}/thumbnail" alt="${esc(a.name)}"><div><strong>${esc(a.name)}</strong><p class="muted">${esc(a.project)} · 当前参考照片</p></div>`;$('#subject').value=a.name;window.scrollTo({top:0,behavior:'smooth'});}
function syncSkillUI(){
 const recipes=state.skill_profile?.recipes;if(!recipes)return;
 const previous=$('#kind').value;
 const signature=JSON.stringify(recipes);
 if($('#kind').dataset.profile!==signature){$('#kind').innerHTML=Object.entries(recipes).map(([key,r])=>`<option value="${esc(key)}">${esc(r.label)}</option>`).join('');$('#kind').value=recipes[previous]?previous:Object.keys(recipes)[0];$('#kind').dataset.profile=signature;}
 const secondary=$('#secondary-source').value;
 $('#secondary-source').innerHTML='<option value="">请选择已确认大头照</option>'+state.assets.filter(a=>!a.trashed_at&&a.category==='大头照'&&a.status==='confirmed').map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
 $('#secondary-source').value=secondary;recipe();
}
function recipe(){const r=state.skill_profile?.recipes?.[$('#kind').value];if(!r)return;$('#recipe-caption').textContent='资产图 Skill · '+r.ratio+' · '+r.description;$('#secondary-label').classList.toggle('hidden',!r.secondary_role);$('#choose-source').textContent=r.primary_role?'① 选择'+r.primary_role:'① 选择参考照片';}

const statusName={discarded:'已移入垃圾桶',waiting:'等待制作',generating:'ImageGen生成中…',submitting:'正在提交',submitted:'已提交',querying:'正在查询',unknown:'提交结果待核实',awaiting_review:'等待你确认',confirmed:'已确认'};
function renderJobs(){const libraryJobs=state.jobs.filter(j=>!j.project_id);$('#jobs').innerHTML=libraryJobs.length?libraryJobs.map(j=>`<article class="job"><div class="job-head"><strong>${esc(j.title)}</strong><span class="muted">${j.kind} · ${j.provider==='imagegen'?'ImageGen':('即梦 '+esc(j.model))}</span><button data-delete-job="${j.id}" ${j.steps.some(s=>['generating','submitting','submitted','querying'].includes(s.status)||auditBusy(s.audit))?'disabled title="任务处理中，完成后可删除"':''}>删除记录</button></div><div class="steps">${j.steps.map((s,i)=>{let ready=i===0||j.steps[0].status==='confirmed';if(j.workflow_version>=2)ready=j.steps.slice(0,i).every(s=>s.status==='confirmed');else if(j.kind==='环境'&&i===3)ready=j.steps.slice(0,3).every(s=>s.status==='confirmed');return `<div class="step"><strong>${i+1}. ${s.name}</strong><div class="step-status">${auditBusy(s.audit)?auditLabel(s.audit):statusName[s.status]||s.status}</div>${s.message?`<div class="job-message">${esc(s.message)}</div>`:''}${s.outputs.map(id=>`<img src="/media/${id}/thumbnail" data-detail="${id}" alt="${s.name}候选"><p class="muted">${esc(state.assets.find(a=>a.id===id)?.technical_check?.message||'')}</p>`).join('')}${s.status==='awaiting_review'?`<button data-reject-result="${j.id}" data-index="${i}">不满意 · 移入垃圾桶</button>`:''}${['awaiting_review','discarded'].includes(s.status)?`<button data-redo-result="${j.id}" data-index="${i}">修改提示词重做</button>`:''}${s.status==='waiting'&&ready?`<button data-step="${j.id}" data-index="${i}">编辑提示词并生成</button>`:''}${['waiting','unknown','awaiting_review'].includes(s.status)&&ready&&!auditBusy(s.audit)?`<button data-attach="${j.id}" data-index="${i}">导入生成结果</button>`:''}${s.status==='unknown'&&j.provider==='imagegen'&&!s.outputs.length?`<button data-reprepare="${j.id}" data-index="${i}">重新准备（不生图）</button>`:''}${s.status==='submitted'?`<button data-query="${j.id}" data-index="${i}">查询结果</button>`:''}${s.status==='waiting'&&!ready?'<p class="muted">先确认前置视图</p>':''}</div>`;}).join('')}</div></article>`).join(''):'<div class="empty"><h2>还没有生成记录</h2><p>选择照片，建立你的第一组大头照、服装或环境资产。</p></div>';}
let auditPoll=null;
const renderGenerationJobs=renderJobs;
renderJobs=function(){
 renderGenerationJobs();
 const pending=(state.references||[]).filter(a=>a.record_scope==='upload_reference'&&a.provenance?.kind==='uploaded_source'&&!state.jobs.some(j=>j.upload_source_id===a.id));
 if(pending.length)$('#jobs').insertAdjacentHTML('afterbegin',pending.map(a=>`<article class="job"><div class="job-head"><strong>${esc(a.name)}</strong><span class="muted">参考已保存 · 待继续处理</span></div><p>上传原文件已保留。${a.upload_recognition?'识别结果已保存，可继续准备资产。':'上次处理未完成，可指定主体后继续。'}</p><button data-upload-choice="${esc(a.id)}">继续处理这份参考</button></article>`).join(''));
};
const backgroundRefreshHandlers=[];
let backgroundRefreshing=false;
function registerBackgroundRefresh(active,update=async()=>{}){backgroundRefreshHandlers.push({active,update});}
async function pollBackground(){
 if(document.hidden||backgroundRefreshing)return;
 const handlers=backgroundRefreshHandlers.filter(h=>h.active());if(!handlers.length)return;
 backgroundRefreshing=true;
 try{await refresh();for(const h of handlers){if(h.active())await h.update();}}
 catch(e){toast(e.message,true);}finally{backgroundRefreshing=false;}
}
function auditBusy(a){return ['drafting','reviewing'].includes(a?.status);}
function showPendingPrompt(a){
 if(!auditBusy(a))return;
 $('#prompt-editor').value='';
 $('#prompt-editor').placeholder='Codex正在处理，完成后将在这里显示本次提示词。';
 $('#prompt-editor-label').textContent='③ 提示词 · 等待本次处理完成';
 for(const selector of ['#prompt-editor','#step-provider','#step-model','#save-prompt','#generate-step','#write-prompt','#review-prompt','#approve-prompt','#authorize-step'])$(selector).disabled=true;
 $('#approve-prompt').checked=false;$('#authorize-step').checked=false;
}
function auditLabel(a){return {drafting:'正在准备提示词…',reviewing:'正在审查提示词…',passed:'审查通过 · 等待你确认',needs_changes:'需要修改',stale:'已修改 · 需要重新审查',error:'准备未完成 · 可以重试',idle:'提示词尚未准备'}[a?.status||'idle'];}
function auditHtml(a){return `<strong>${esc(auditLabel(a))}</strong>${a?.skill_execution?`<details><summary>资产图 Skill · 本次规范来源</summary><p>已准备知识包；采用情况以逐项审查为准。</p><p>${esc(a.skill_execution.required_rules.join('、'))}</p><ul>${a.skill_execution.references.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></details>`:''}${a?.message?`<p>${esc(a.message)}</p>`:''}${['observations','checks','uncertainties','issues'].map((key,i)=>a?.[key]?.length?`<details ${key==='issues'?'open':''}><summary>${['看图记录','审查结论','不确定的部分','需要解决的问题'][i]}</summary><ul>${a[key].map(t=>`<li>${esc(t)}</li>`).join('')}</ul></details>`:'').join('')}`;}
function pollAudit(id,index){auditPoll={id,index};}
registerBackgroundRefresh(()=>!!auditPoll&&$('#dialog').open&&$('#prompt-editor')?.dataset.key===auditPoll.id+':'+auditPoll.index,async()=>{
 const {id,index}=auditPoll;const audit=state.jobs.find(j=>j.id===id)?.steps[index]?.audit;
 if(auditBusy(audit)){$('#audit-result').innerHTML=auditHtml(audit);showPendingPrompt(audit);}else await previewStep(id,index);
});
async function previewStep(id,index){
 auditPoll=null;
 const p=await api('/api/prepare',{id,index});const job=state.jobs.find(j=>j.id===id);
 const images=p.reference_paths.map(path=>state.assets.concat(state.references||[]).find(a=>a.original===path)).filter(Boolean);
 modal(`${p.view} · 编辑提示词`,`<div class="refs">${images.map(a=>`<img src="/media/${a.id}/thumbnail" alt="${esc(a.name)}">`).join('')}</div>
 <p class="muted">知识库模板：${esc((p.template_source||'历史生成记录').split(/[\\/]/).pop())} · 输出 ${p.count} 张 / ${p.ratio}</p>
 <div id="audit-result" class="notice">${auditHtml(p.audit)}</div><label><span id="prompt-editor-label">③ 提示词 · 可直接修改</span><textarea id="prompt-editor" data-key="${id}:${index}" rows="15" maxlength="20000">${auditBusy(p.audit)?'':esc(p.prompt)}</textarea></label>
 <div class="form-grid generation-tools"><label>④ 生成工具<select id="step-provider"><option value="imagegen" ${p.provider==='imagegen'?'selected':''}>ImageGen · 网页直接生成</option><option value="dreamina" ${p.provider==='dreamina'?'selected':''}>即梦 CLI · 直接生成</option></select></label>
 <label id="step-model-label">即梦模型<select id="step-model">${options(['5.0','5.0Pro','4.7','4.6','4.5','4.1','4.0'],job.model)}</select></label></div>
 <p id="tool-caption" class="notice"></p>
 <label id="step-authorization" class="check-label"><input id="authorize-step" type="checkbox"><span id="authorize-caption"></span></label>
 <label class="check-label"><input id="approve-prompt" type="checkbox">我已核对当前提示词及审查意见，确认使用它生成。</label>
 <div class="form-footer"><button id="write-prompt">Codex看图重写并审查</button><button id="review-prompt">审查当前提示词</button><button id="save-prompt">保存提示词</button><button id="generate-step" class="primary"></button></div>
 `);
 const syncTool=()=>{const dream=$('#step-provider').value==='dreamina';$('#step-model-label').classList.toggle('hidden',!dream);$('#authorize-step').checked=false;$('#generate-step').textContent=dream?'即梦生成图片 · 1张':'ImageGen生成图片 · 1张';$('#tool-caption').textContent=dream?'提交到即梦，生成结果可在资产组内查询。':'使用已登录Codex的内置ImageGen生成，完成后图片自动显示在当前资产组。大图会生成压缩参考副本，原图保留。具体模型由Codex管理。';$('#authorize-caption').textContent=dream?'同意将以上参考图与提示词提交到即梦，并使用所选模型生成1张，消耗本次额度。':'同意将以上参考图与提示词交给内置ImageGen生成1张，使用当前Codex账号额度。';};
 const invalidateRequest=()=>{$('#authorize-step').checked=false;$('#approve-prompt').checked=false;};
 $('#step-provider').onchange=()=>{invalidateRequest();syncTool();};$('#step-model').onchange=invalidateRequest;$('#prompt-editor').oninput=()=>{invalidateRequest();$('#audit-result').textContent='提示词已修改，请重新审查。';};syncTool();
 const save=(approve=false)=>api('/api/prompt',{id,index,prompt:$('#prompt-editor').value,provider:$('#step-provider').value,model:$('#step-model').value,approve});
 const startAudit=mode=>guarded(async()=>{await save();await api('/api/codex',{id,index,mode});const pending={status:mode==='write'?'drafting':'reviewing'};showPendingPrompt(pending);$('#audit-result').innerHTML=auditHtml(pending);pollAudit(id,index);await refresh();await previewStep(id,index);},$('#'+(mode==='write'?'write-prompt':'review-prompt')));
 $('#write-prompt').onclick=()=>startAudit('write');$('#review-prompt').onclick=()=>startAudit('review');
 if(auditBusy(p.audit)){showPendingPrompt(p.audit);pollAudit(id,index);}
 $('#save-prompt').onclick=e=>guarded(async()=>{await save();toast('提示词已保存');},e.currentTarget);
 $('#generate-step').onclick=e=>guarded(async()=>{if(!$('#approve-prompt').checked)throw Error('请先核对并确认当前提示词');if(!$('#authorize-step').checked)throw Error('请先同意本次生成');const dream=$('#step-provider').value==='dreamina';await save(true);const result=await api(dream?'/api/submit':'/api/imagegen',{id,index,authorize:true});$('#dialog').close();await refresh();toast(result.message);},e.currentTarget);

}
$('#close-dialog').onclick=()=>$('#dialog').close();$('#nav-library').onclick=()=>view('library');$('#nav-create').onclick=()=>view('create');$('#categories').onclick=e=>{const b=e.target.closest('[data-category]');if(b){category=b.dataset.category;selected.clear();page=0;view('library');renderCategories();render();}};
$('#role-filter').innerHTML='<option value="">全部用途</option>'+options(roles,'');for(const selector of ['#search','#project-filter','#role-filter','#status-filter'])$(selector).addEventListener('input',()=>{page=0;render();});
document.addEventListener('click',e=>{const d=e.target.closest('[data-detail]');if(d)detail(d.dataset.detail);const use=e.target.closest('[data-use]');if(use)useSource(use.dataset.use);const confirm=e.target.closest('[data-confirm]');if(confirm)guarded(async()=>{await api('/api/confirm',{id:confirm.dataset.confirm});await refresh();detail(confirm.dataset.confirm);toast('已记录你的确认');},confirm);const step=e.target.closest('[data-step]');if(step)guarded(()=>previewStep(step.dataset.step,Number(step.dataset.index)),step);const query=e.target.closest('[data-query]');if(query)guarded(async()=>{const r=await api('/api/query',{id:query.dataset.query,index:Number(query.dataset.index)});toast(r.message);await refresh();},query);const attach=e.target.closest('[data-attach]');if(attach){const id=attach.dataset.attach,index=Number(attach.dataset.index);modal('导入这个视图的生成结果','<form id="attach-form"><label>生成图片的完整路径（每行一张）<textarea id="attach-paths" rows="4" required placeholder="C:\\...\\图片.png"></textarea></label><p class="muted">图片将复制进资产库，作为此视图的候选保存。</p><div class="form-footer"><button class="primary">导入候选</button></div></form>');$('#attach-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{await api('/api/attach',{id,index,paths:$('#attach-paths').value.split('\n').map(p=>p.trim().replace(/^"|"$/g,'')).filter(Boolean)});$('#dialog').close();await refresh();toast('已导入候选，点击图片核对并确认');},e.submitter);};}});
$('#gallery').addEventListener('change',e=>{const id=e.target.dataset.select;if(id){e.target.checked?selected.add(id):selected.delete(id);e.target.closest('.card').classList.toggle('selected',e.target.checked);renderSelection();}});
function dragCard(card){const checkbox=card?.querySelector('[data-select]');if(!checkbox)return;selected.add(checkbox.dataset.select);checkbox.checked=true;card.classList.add('selected');}
$('#gallery').addEventListener('pointerdown',e=>{
  suppressCardClick=false;
  if(e.pointerType!=='mouse'||e.button!==0||e.target.closest('input,select,a,button:not(.picture)'))return;
  const card=e.target.closest('.card');if(!card?.querySelector('[data-select]'))return;
  dragSelect={moved:false,pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,x:e.clientX,y:e.clientY,card};
});
document.addEventListener('pointermove',e=>{
  const drag=dragSelect;if(!drag||e.pointerId!==drag.pointerId)return;
  if(!(e.buttons&1)){finishDrag();return;}
  if(!drag.moved&&Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)<5)return;
  drag.moved=true;$('#gallery').classList.add('dragging');e.preventDefault();dragCard(drag.card);
  // Sample the path so fast movement also selects the cards between events.
  const steps=Math.max(1,Math.ceil(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)/10));
  for(let i=1;i<=steps;i++)dragCard(document.elementFromPoint(drag.x+(e.clientX-drag.x)*i/steps,drag.y+(e.clientY-drag.y)*i/steps)?.closest('#gallery .card'));
  drag.x=e.clientX;drag.y=e.clientY;renderSelection();
});
function finishDrag(){if(!dragSelect)return;suppressCardClick=dragSelect.moved;dragSelect=null;$('#gallery').classList.remove('dragging');setTimeout(()=>{suppressCardClick=false;},0);}
document.addEventListener('pointerup',finishDrag);
document.addEventListener('pointercancel',finishDrag);
window.addEventListener('blur',finishDrag);
$('#gallery').addEventListener('dragstart',e=>{if(e.target.closest('img,.picture'))e.preventDefault();});
$('#gallery').addEventListener('click',e=>{if(suppressCardClick){e.preventDefault();e.stopPropagation();suppressCardClick=false;}},true);
$('#pager').onclick=e=>{const b=e.target.closest('[data-page]');if(b){page+=Number(b.dataset.page);render();window.scrollTo({top:0,behavior:'smooth'});}};$('#clear-selection').onclick=()=>{const count=selected.size;selected.clear();render();toast(`已取消选择 ${count} 张图片`);};$('#select-all').onclick=()=>{const assets=filtered();assets.forEach(a=>selected.add(a.id));render();toast(`已全选当前筛选结果 ${assets.length} 张图片`);};
$('#export-selection').onclick=e=>guarded(async()=>{const r=await api('/api/export',{ids:[...selected]});modal('选用包已保存',`<p>已保存 ${r.count} 项资产及来源清单，可供不同短剧项目选用。视频在清单中标为视频参考。</p><pre>${esc(r.folder)}</pre><div class="notice">这是一份参考选用包。项目资产助理仍需关联当前剧本的资产需求并确认适用范围。</div><button id="copy-export">复制文件夹路径</button>`);$('#copy-export').onclick=()=>guarded(()=>navigator.clipboard.writeText(r.folder).then(()=>toast('已复制')));},e.currentTarget);
function uploadFiles(sourceId=''){
 const recipes=state.skill_profile?.recipes||{};
 modal(sourceId?'指定提取主体':'上传文件',`<form id="file-upload-form"><p>支持图片和 MP4、MOV、WEBM 视频。源文件始终保留，上传内容只作参考，生成后的规范图片才进入收藏。</p><label>提取类型<select name="kind"><option value="">自动识别</option>${Object.entries(recipes).filter(([key])=>key!=='人物1x4').map(([key,r])=>`<option value="${esc(key)}">${esc(r.label)}</option>`).join('')}</select></label><label>要提取的主体（可选）<input name="subject" maxlength="200" placeholder="多人画面请写明，例如：左侧穿蓝色外套的人物"></label><p class="muted">人物自动制作无衣服的大头照，服装制作无头无脸人台三视图。视频抽取 3 个代表画面；不能确定主体时会提示选择。提示词准备完成后，核对并确认生成工具与额度，再自动生成、规范尺寸并入库。</p><div class="form-footer"><button class="primary">${sourceId?'准备制作':'选择文件并准备制作'}</button></div></form>`);
 $('#file-upload-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{
   const fields=Object.fromEntries(new FormData(e.target));
   const progress=document.createElement('p');progress.className='notice';progress.setAttribute('role','status');
   progress.textContent=sourceId?'正在使用已保存的参考，识别主体并准备制作…':'等待选择文件。选择后将识别主体并准备制作，完成会自动打开结果；原文件保留。';
   e.target.querySelector('.form-footer').before(progress);
   try{
   toast('正在保存参考、识别内容并准备制作，请稍候…');clearTimeout(toastTimer);
   const response=await api(sourceId?'/api/upload-prepare':'/api/upload-files',{...fields,multiple:true,source_id:sourceId});
   if(response.cancelled){toast('已取消选择文件');return;}
   await refresh();const rows=sourceId?[response]:response.results||[];view('create');
   if(rows.length===1&&rows[0].job_id){await previewStep(rows[0].job_id,0);toast(rows[0].upload_processing.message);return;}
   modal('文件处理结果',`<p>源文件全部保留。上传参考不进入收藏；生成结果按类别入库，等待你确认。</p><ul>${rows.map(a=>`<li>${esc(a.name||'文件')}：${esc(a.upload_processing?.message||'未完成')}${a.job_id?` <button data-step="${esc(a.job_id)}" data-index="0">查看制作</button>`:a.id?` <button data-upload-choice="${esc(a.id)}">指定主体或重试</button>`:''}</li>`).join('')}</ul>`);
   }finally{progress.remove();}
 },e.submitter);};
 const saved=(state.references||[]).find(a=>a.id===sourceId)?.upload_options;
 if(saved){$('#file-upload-form').elements.kind.value=saved.kind;$('#file-upload-form').elements.subject.value=saved.subject;}
}
$('#upload').onclick=()=>uploadFiles();
document.addEventListener('click',e=>{const b=e.target.closest('[data-upload-choice]');if(b)uploadFiles(b.dataset.uploadChoice);});
$('#import-folder').onclick=()=>{modal('导入已有资产文件夹','<form id="import-form"><label>文件夹完整路径<input id="import-path" required placeholder="C:\\Users\\...\\资产文件夹"></label><div class="notice">保存图片副本、来源路径和缩略图，原文件保留。重复导入同一路径且未变化的文件会跳过。</div><div class="form-footer"><button class="primary">开始导入</button></div></form>');$('#import-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{toast('正在导入，请稍候…');const r=await api('/api/import',{source:$('#import-path').value.trim().replace(/^"|"$/g,'')});await refresh();modal('导入完成',`<p>新增 ${r.added} 张，跳过 ${r.existing} 张已有图片，失败 ${r.errors.length} 张。</p>${r.errors.length?'<pre>'+esc(r.errors.map(e=>e.path+'\n'+e.error).join('\n\n'))+'</pre>':''}`);},e.submitter);};};
$('#kind').onchange=recipe;$('#refresh-jobs').onclick=e=>guarded(refresh,e.currentTarget);
$('#choose-source').onclick=()=>{modal('选择参考照片',`<input id="source-search" type="search" placeholder="搜索照片名称"><div id="source-choices" class="source-choices"></div>`);const draw=()=>{$('#source-choices').innerHTML=sourceImages().filter(a=>!a.trashed_at&&a.media_type!=='video'&&a.name.toLowerCase().includes($('#source-search').value.toLowerCase())).slice(0,60).map(a=>`<button type="button" data-use="${a.id}"><img src="/media/${a.id}/thumbnail" alt="${esc(a.name)}" loading="lazy"><span>${esc(a.name)}</span></button>`).join('')||'<p>没有匹配的照片，可用页面顶部的“上传文件”准备。</p>';};$('#source-search').oninput=draw;draw();};
$('#create-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{if(!sourceId)throw Error('请先选择一张参考照片');const job=await api('/api/job',{source_id:sourceId,subject:$('#subject').value,kind:$('#kind').value,notes:$('#notes').value,secondary_id:$('#secondary-source').value});await refresh();await previewStep(job.id,0);await api('/api/codex',{id:job.id,index:0,mode:'write'});await refresh();await previewStep(job.id,0);},e.submitter);};
registerBackgroundRefresh(()=>!$('#dialog').open&&state.jobs.some(j=>j.steps.some(s=>['submitting','querying','generating'].includes(s.status)||auditBusy(s.audit))));
recipe();guarded(refresh);setInterval(pollBackground,2500);

async function batchAction(action,extra={}){const r=await api('/api/'+action,{ids:[...selected],...extra});selected.clear();$('#dialog').close();await refresh();toast(action==='trash'?`已将 ${r.count} 项资产移入垃圾桶，30 天内可恢复`:action==='restore'?`已恢复 ${r.count} 项资产`:`已移动 ${r.count} 项资产`);}
$('#move-selection').onclick=()=>{modal('移动图片',`<form id="move-form"><p>将选中的 ${selected.size} 张图片移动到：</p><label>目标分类<select id="move-category">${options(categories,category)}</select></label><div class="form-footer"><button type="submit" class="primary">移动</button></div></form>`);$('#move-form').onsubmit=e=>{e.preventDefault();guarded(()=>batchAction('move',{category:$('#move-category').value}),e.submitter);};};
$('#trash-selection').onclick=()=>{modal('移入垃圾桶',`<p>将选中的 ${selected.size} 项资产移入垃圾桶，30 天内可恢复。之后自动清理资产库副本；已确认或被项目引用的资产受保护。</p><button id="confirm-trash" class="primary">移入垃圾桶</button>`);$('#confirm-trash').onclick=e=>guarded(()=>batchAction('trash'),e.currentTarget);};
$('#restore-selection').onclick=e=>guarded(()=>batchAction('restore'),e.currentTarget);

document.addEventListener('click',e=>{const b=e.target.closest('[data-reprepare]');if(!b)return;modal('重新准备生成','<p>上次请求未返回图片。若你已在其他位置找到生成结果，请先导入；重新提交可能再次消耗额度。</p><p>此按钮只恢复编辑，之后仍需你确认提示词并点击生成。</p><button id="reprepare-confirm">重新准备（不生图）</button>');$('#reprepare-confirm').onclick=event=>guarded(async()=>{await api('/api/imagegen-reset',{id:b.dataset.reprepare,index:Number(b.dataset.index)});await refresh();await previewStep(b.dataset.reprepare,Number(b.dataset.index));},event.currentTarget);});

document.addEventListener('click',e=>{
 const b=e.target.closest('[data-reject-result],[data-redo-result]');if(!b)return;
 const redo=!!b.dataset.redoResult,id=b.dataset.redoResult||b.dataset.rejectResult,index=Number(b.dataset.index);
 const step=state.jobs.find(j=>j.id===id).steps[index];
 modal(redo?'修改提示词重做':'移入垃圾桶',`<p>${step.outputs.length?`本视图的 ${step.outputs.length} 张未确认结果将移入资产库垃圾桶，30天内可手动恢复。`:'旧图已移入垃圾桶。'}</p><p>${redo?'接下来编辑原提示词并重新审查；你再次确认后才生成，不会立即消耗生图额度。':'生成记录保留，你也可以稍后选择重做。'}</p><button id="reject-result-confirm">${redo?'移入垃圾桶并编辑提示词':'移入垃圾桶'}</button>`);
 $('#reject-result-confirm').onclick=event=>guarded(async()=>{const r=await api('/api/reject-result',{id,index,redo});await refresh();if(redo)await previewStep(id,index);else $('#dialog').close();toast(r.message);},event.currentTarget);
});

document.addEventListener('click',e=>{
 const b=e.target.closest('[data-delete-job]');if(!b)return;
 const job=state.jobs.find(j=>j.id===b.dataset.deleteJob);
 modal('删除生成记录',`<p>从生成列表移除「${esc(job.title)}」这条记录。</p><p>资产库中的图片不会删除，垃圾桶图片仍可按原规则恢复。后台保留来源记录供追溯。</p>${job.steps.some(s=>s.status==='unknown')?'<p>本次生成结果尚未核实，删除记录不会取消外部生成任务。</p>':''}<button id="delete-job-confirm">删除这条记录</button>`);
 $('#delete-job-confirm').onclick=event=>guarded(async()=>{const r=await api('/api/job-delete',{id:job.id});$('#dialog').close();await refresh();toast(r.message);},event.currentTarget);
});
