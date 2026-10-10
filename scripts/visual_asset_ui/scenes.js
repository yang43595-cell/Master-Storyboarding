'use strict';
let sceneFolder=null;
const sceneViews=['正面','反面','45°','俯瞰'];
function sceneView(asset){
  if(asset.scene_view)return asset.scene_view;
  const label=asset.provenance?.view||asset.name||'';
  if(/45[°度]?|斜俯/.test(label))return '45°';
  if(/90[°度]?|俯瞰|俯视|鸟瞰/.test(label))return '俯瞰';
  if(/反向|反面|背面/.test(label))return '反面';
  if(/正面/.test(label))return '正面';
  return '';
}
function sceneKey(a){return a.scene_id|| (a.provenance?.job_id?'job:'+a.provenance.job_id+(a.provenance.state?':state:'+a.provenance.state:''):a.group?'group:'+JSON.stringify([a.project,a.group]):'single:'+a.id);}
function sceneGroups(assets){
  const groups=new Map();
  for(const a of assets){const key=sceneKey(a);if(!groups.has(key))groups.set(key,{id:key,name:a.group||a.name,items:[]});groups.get(key).items.push(a);}
  return [...groups.values()].map(g=>({...g,cover:g.items.find(a=>sceneView(a)==='45°'&&a.status==='confirmed')||g.items.find(a=>sceneView(a)==='45°')||g.items[0]}));
}
function visibleSceneAssets(){
  const matches=new Set(filtered().map(sceneKey));
  return state.assets.filter(a=>!a.trashed_at&&a.category==='场景'&&(sceneFolder?sceneKey(a)===sceneFolder:matches.has(sceneKey(a))));
}
function renderScenes(){
  let tools=document.querySelector('#scene-tools');
  if(!tools){tools=document.createElement('div');tools.id='scene-tools';tools.className='scene-tools';$('#gallery').before(tools);}
  const all=state.assets.filter(a=>!a.trashed_at&&a.category==='场景');
  const group=sceneGroups(all).find(g=>g.id===sceneFolder);
  if(!group)sceneFolder=null;
  tools.innerHTML=`${group?'<button id="scene-back">← 全部场景</button>':'<span class="muted">同一空间的多张视图，收进一个场景。</span>'}<button id="scene-group">${group?'整理本场景视图':'将勾选图片组合为场景'}</button>`;
  if(group){
    $('#result-count').textContent=`${group.name} · ${group.items.length} 张视图`;
    $('#gallery').innerHTML=[...group.items].sort((a,b)=>sceneViews.indexOf(sceneView(a))-sceneViews.indexOf(sceneView(b))).map(a=>`<article class="card"><input class="select-card" type="checkbox" data-select="${a.id}" aria-label="选择 ${esc(a.name)}" ${selected.has(a.id)?'checked':''}><button class="picture" data-scene-image="${a.id}" aria-label="放大 ${esc(sceneView(a)||a.name)}"><img src="/media/${a.id}/thumbnail" alt="${esc(a.name)}"></button><div class="card-body"><strong>${esc(sceneView(a)||'未指定视角')}</strong><div class="card-project">${esc(a.name)}</div><button data-detail="${a.id}">图片信息</button></div></article>`).join('');
    $('#pager').innerHTML='';
    $('#scene-back').onclick=()=>{sceneFolder=null;selected.clear();render();};
  }else{
    // Search finds a scene through any member, while its cover and contents stay complete.
    const matching=new Set(filtered().map(sceneKey));
    const groups=sceneGroups(all).filter(g=>matching.has(g.id));
    page=Math.min(page,Math.max(0,Math.ceil(groups.length/pageSize)-1));
    $('#result-count').textContent=`场景 · ${groups.length} 组 · ${groups.reduce((n,g)=>n+g.items.length,0)} 张图片`;
    $('#gallery').innerHTML=groups.length?groups.slice(page*pageSize,(page+1)*pageSize).map(g=>`<article class="card scene-card"><input class="select-card" type="checkbox" data-scene-select="${esc(g.id)}" aria-label="选择场景 ${esc(g.name)}" ${g.items.every(a=>selected.has(a.id))?'checked':''}><button class="picture" data-scene-open="${esc(g.id)}" aria-label="打开场景 ${esc(g.name)}"><img src="/media/${g.cover.id}/thumbnail" alt="${esc(g.name)}"><span class="scene-cover-label">${sceneView(g.cover)==='45°'?'45° 展示图':'暂用封面 · 待指定 45°'}</span></button><div class="card-body"><div class="card-title">▱ ${esc(g.name)}</div><div class="card-meta"><span>${g.items.length} 张视图</span><span>打开场景 →</span></div></div></article>`).join(''):'<div class="empty full"><h2>还没有匹配的场景</h2><p>先将图片移动到“场景”，再勾选图片组合为一个场景。</p></div>';
    $('#pager').innerHTML=groups.length>pageSize?`<button data-page="-1" ${page===0?'disabled':''}>上一页</button><span>${page+1} / ${Math.ceil(groups.length/pageSize)}</span><button data-page="1" ${(page+1)*pageSize>=groups.length?'disabled':''}>下一页</button>`:'';
  }
  $('#scene-group').onclick=()=>editScene(group);
  renderSelection();
  $('#select-all').onclick=()=>{(category==='场景'?visibleSceneAssets():filtered()).forEach(a=>selected.add(a.id));render();};
}
function editScene(group){
  const items=group?group.items:state.assets.filter(a=>selected.has(a.id)&&a.category==='场景'&&!a.trashed_at);
  if(!items.length||items.length>4){toast('请勾选同一场景的 1 至 4 张图片，再进行组合',true);return;}
  modal('组合场景',`<form id="scene-form"><label>场景名称<input name="scene-name" value="${esc(group?.name||'')}" required maxlength="200" placeholder="例如：公主卧室"></label><p class="muted">每个视角对应一张图片，45° 图将自动成为文件夹封面。</p><div class="scene-view-editor">${items.map((a,i)=>`<label><img src="/media/${a.id}/thumbnail" alt="${esc(a.name)}"><span>${esc(a.name)}</span><select data-scene-view="${a.id}" aria-label="${esc(a.name)}的视角">${options(sceneViews,sceneView(a)||sceneViews[i])}</select></label>`).join('')}</div><div class="form-footer"><button type="submit" class="primary">保存场景</button></div></form>`);
  $('#scene-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{const result=await api('/api/scene',{name:e.target.elements['scene-name'].value,views:[...e.target.querySelectorAll('[data-scene-view]')].map(s=>({id:s.dataset.sceneView,view:s.value}))});sceneFolder=result.scene_id;selected.clear();$('#dialog').close();await refresh();toast('场景已保存');},e.submitter);};
}
document.addEventListener('click',e=>{
  const open=e.target.closest('[data-scene-open]');
  if(open){sceneFolder=open.dataset.sceneOpen;selected.clear();render();}
  const image=e.target.closest('[data-scene-image]');
  if(image){const a=state.assets.find(a=>a.id===image.dataset.sceneImage);modal(`${a.group||a.name} · ${sceneView(a)||'场景图片'}`,`<img class="scene-large" src="/media/${a.id}/original" alt="${esc(a.name)}">`);}
});
document.addEventListener('change',e=>{
  const key=e.target.dataset.sceneSelect;
  if(key){state.assets.filter(a=>!a.trashed_at&&a.category==='场景'&&sceneKey(a)===key).forEach(a=>e.target.checked?selected.add(a.id):selected.delete(a.id));renderSelection();}
});
document.addEventListener('input',e=>{if(['search','project-filter','role-filter','status-filter'].includes(e.target.id))sceneFolder=null;},true);
