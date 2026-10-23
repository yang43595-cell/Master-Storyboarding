'use strict';
// Collection folders are independent of production AST groups and scene-view constraints.
let openFolder=null, folderDragMode=true, folderGesture=null;
const baseCollectionRender=render;
const folderTools=document.createElement('div');folderTools.className='folder-toolbar';
folderTools.innerHTML='<div><button id="folder-back" class="hidden">← 全部文件夹</button> <strong id="folder-title"></strong></div><div><button id="folder-drag" aria-pressed="true">拖动归组：已开启</button> <button id="folder-new">将所选资产放入文件夹</button> <button id="folder-rename" class="hidden">重命名</button> <button id="folder-remove" class="hidden">移出所选资产</button> <button id="folder-cover" class="hidden">设为封面</button></div><p id="folder-hint" class="muted">拖到图片或文件夹可归组；拖到左侧分类可移出文件夹，拖到“全部资产”则保留原分类。勾选多项可一起拖，文件夹可拖动排序。</p>';
$('#gallery').before(folderTools);
const backupBox=document.createElement('div');backupBox.className='backup-summary';backupBox.innerHTML='<span id="backup-summary-text"></span><button id="backup-retry">重试待备份</button><details><summary>查看备份详情</summary><div id="backup-details"></div></details>';folderTools.before(backupBox);
function folderMembers(id){return state.assets.filter(a=>!a.trashed_at&&a.folder_id===id);}
function visibleFolderMembers(id){return filtered().filter(a=>a.folder_id===id);}
function collectionSelectionAssets(){return openFolder?visibleFolderMembers(openFolder):filtered();}
function folderCard(a){return `<article class="card ${selected.has(a.id)?'selected':''}"><input class="select-card" type="checkbox" data-select="${a.id}" aria-label="选择 ${esc(a.name)}" ${selected.has(a.id)?'checked':''}><button class="picture" data-detail="${a.id}" aria-label="查看 ${esc(a.name)}"><img draggable="false" src="/media/${a.id}/thumbnail" alt="${esc(a.name)}"></button><div class="card-body"><div class="card-title">${esc(a.name)}</div><p class="card-project">${esc(a.category)} · ${esc(a.project)}</p><span class="muted">${a.file_error?'原图缺失':a.status==='confirmed'?'已确认':'待核对'}</span></div></article>`;}
function legacySceneCard(g){return `<article class="card scene-card"><input class="select-card" type="checkbox" data-scene-select="${esc(g.id)}" aria-label="选择场景 ${esc(g.name)}" ${g.items.every(a=>selected.has(a.id))?'checked':''}><button class="picture" data-scene-open="${esc(g.id)}" aria-label="打开场景 ${esc(g.name)}"><img draggable="false" src="/media/${g.cover.id}/thumbnail" alt="${esc(g.name)}"><span class="scene-cover-label">${sceneView(g.cover)==='45°'?'45° 展示图':'待指定封面'}</span></button><div class="card-body"><strong>${esc(g.name)}</strong><p>${g.items.length} 张视图</p></div></article>`;}
render=function(){
 $('#select-all').onclick=()=>{const assets=collectionSelectionAssets();assets.forEach(a=>selected.add(a.id));render();toast(`已全选当前范围 ${assets.length} 张图片`);};
 const active=state.assets.filter(a=>!a.trashed_at),pending=active.filter(a=>a.file_error||a.backup?.status!=='saved');
 $('#backup-summary-text').textContent=`知识库备份：${active.length-pending.length}/${active.length} 项完成${pending.length?' · '+pending.length+' 项待备份':''}`;
 $('#backup-details').innerHTML=pending.map(a=>`<p>${esc(a.name)}：${esc(a.file_error||a.backup?.message||'等待备份')}</p>`).join('')||'<p>当前资产均已备份。</p>';
 $('#backup-retry').disabled=!pending.length;
 const anyFolder=filtered().some(a=>a.folder_id),isTrash=category==='trash';
 if(openFolder&&!folderMembers(openFolder).length)openFolder=null;
 folderTools.classList.toggle('hidden',isTrash);
 ['folder-back','folder-rename','folder-remove','folder-cover'].forEach(id=>$('#'+id).classList.toggle('hidden',!openFolder));
 $('#folder-title').textContent=openFolder?(folderMembers(openFolder)[0]?.folder_name||'文件夹'):'';
 if(isTrash||(category==='场景'&&sceneFolder&&!openFolder)||(!anyFolder&&!openFolder)){baseCollectionRender();return;}
 sceneFolder=null;document.querySelector('#scene-tools')?.remove();
  const matches=filtered(),entries=[];
  if(openFolder){entries.push(...visibleFolderMembers(openFolder).map(a=>({asset:a})));}
 else{const seen=new Set();for(const a of matches){if(!a.folder_id){if(category==='场景'){const key='scene:'+sceneKey(a);if(seen.has(key))continue;seen.add(key);const group=sceneGroups(active.filter(x=>x.category==='场景'&&!x.folder_id&&sceneKey(x)===sceneKey(a)))[0];entries.push({scene:group});}else entries.push({asset:a});continue;}if(seen.has(a.folder_id))continue;seen.add(a.folder_id);const members=visibleFolderMembers(a.folder_id),cover=members.find(x=>x.id===a.folder_cover)||members.find(x=>sceneView(x)==='45°')||members[0];entries.push({folder:a.folder_id,name:a.folder_name,members,cover});}}
 if(!openFolder)entries.sort((a,b)=>a.folder&&b.folder?(a.members[0].folder_order??9999)-(b.members[0].folder_order??9999):a.folder?-1:b.folder?1:0);
 page=Math.min(page,Math.max(0,Math.ceil(entries.length/pageSize)-1));
 $('#gallery').innerHTML=entries.slice(page*pageSize,(page+1)*pageSize).map(e=>e.scene?legacySceneCard(e.scene):e.asset?folderCard(e.asset):`<article class="card folder-card" data-folder-target="${e.folder}"><input class="select-card" type="checkbox" data-folder-select="${e.folder}" aria-label="选择文件夹 ${esc(e.name)}" ${e.members.every(a=>selected.has(a.id))?'checked':''}><button class="picture" data-folder-open="${e.folder}" aria-label="打开文件夹 ${esc(e.name)}"><img draggable="false" src="/media/${e.cover.id}/thumbnail" alt="${esc(e.name)}"><span class="scene-cover-label">文件夹 · ${e.members.length} 项</span></button><div class="card-body"><strong>${esc(e.name)}</strong><p class="muted">${new Set(e.members.map(a=>a.category)).size>1?'多种分类 · ':''}拖动排序或打开查看 →</p></div></article>`).join('')||'<div class="empty full">没有匹配的资产。</div>';
 $('#result-count').textContent=openFolder?`${folderMembers(openFolder)[0].folder_name} · ${entries.length} 项`:`${category||'全部资产'} · ${entries.length} 项（按当前筛选）`;
 $('#pager').innerHTML=entries.length>pageSize?`<button data-page="-1" ${page===0?'disabled':''}>上一页</button><span>${page+1}/${Math.ceil(entries.length/pageSize)}</span><button data-page="1" ${(page+1)*pageSize>=entries.length?'disabled':''}>下一页</button>`:'';
 renderSelection();
};
async function putFolder(ids,target){
 ids=[...new Set(ids)].filter(id=>state.assets.some(a=>a.id===id&&!a.trashed_at));if(!ids.length)throw Error('请先选择图片');
 if(target){await api('/api/folder',{ids,folder_id:target});selected.clear();await refresh();toast('已放入文件夹');return;}
 modal('新建资产文件夹',`<form id="folder-form"><p>将 ${ids.length} 项资产放在一起，分类与项目用途保持原记录。</p><label>文件夹名称<input id="folder-name" required maxlength="100" value="新建文件夹"></label><div class="form-footer"><button class="primary">创建文件夹</button></div></form>`);
 $('#folder-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{await api('/api/folder',{ids,name:$('#folder-name').value});$('#dialog').close();selected.clear();await refresh();toast('文件夹已创建');},e.submitter);};
}
$('#folder-new').onclick=()=>guarded(()=>putFolder([...selected]));
$('#folder-back').onclick=()=>{openFolder=null;selected.clear();page=0;render();};
$('#folder-drag').onclick=()=>{folderDragMode=!folderDragMode;$('#folder-drag').textContent=folderDragMode?'拖动归组：已开启':'拖动归组：已关闭';$('#folder-drag').setAttribute('aria-pressed',String(folderDragMode));$('#folder-hint').textContent=folderDragMode?'拖到图片或文件夹可归组；拖到左侧分类可移出文件夹，拖到“全部资产”则保留原分类。勾选多项可一起拖，文件夹可拖动排序。':'按住鼠标划过图片可多选；也可以勾选图片，再点击“将所选资产放入文件夹”。';};
$('#folder-remove').onclick=()=>guarded(async()=>{const ids=folderMembers(openFolder).filter(a=>selected.has(a.id)).map(a=>a.id);await api('/api/folder',{action:'remove',ids});selected.clear();await refresh();toast('图片已移出文件夹，返回各自分类');});
$('#folder-cover').onclick=()=>guarded(async()=>{const ids=folderMembers(openFolder).filter(a=>selected.has(a.id)).map(a=>a.id);if(ids.length!==1)throw Error('请选择一张封面图片');await api('/api/folder',{action:'cover',ids,folder_id:openFolder});await refresh();toast('已设置封面');});
$('#folder-rename').onclick=()=>{const members=folderMembers(openFolder);modal('重命名文件夹',`<form id="rename-folder-form"><label>名称<input id="rename-folder-name" value="${esc(members[0].folder_name)}" required maxlength="100"></label><button class="primary">保存</button></form>`);$('#rename-folder-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{await api('/api/folder',{action:'rename',folder_id:openFolder,ids:[members[0].id],name:$('#rename-folder-name').value});$('#dialog').close();await refresh();},e.submitter);};};
$('#backup-retry').onclick=e=>guarded(async()=>{await api('/api/backup-retry',{});await refresh();toast('已检查备份，未完成项请查看备份详情');},e.target);
function dragImageIds(element){
 const card=element?.closest('#gallery .card');if(!card)return [];
 const image=card.querySelector('[data-detail]');if(image)return [image.dataset.detail];
 const scene=card.querySelector('[data-scene-open]');if(scene)return state.assets.filter(a=>!a.trashed_at&&sceneKey(a)===scene.dataset.sceneOpen).map(a=>a.id);
 return [];
}
function folderCardAt(x,y){return document.elementFromPoint(x,y)?.closest('#gallery .card')||null;}
function categoryDropAt(x,y){
 const button=document.elementFromPoint(x,y)?.closest('#categories [data-category]');
 return button&&button.dataset.category!=='trash'?button:null;
}
function gestureAssetIds(g){return selected.has(g.id)?[...selected]:g.ids;}
function categoryDropAllowed(ids,target){
 return target===''||state.assets.filter(a=>ids.includes(a.id)).every(a=>(a.media_type==='video')===(target==='视频参考'));
}
async function dropAssetsToCategory(ids,target){
 const assets=state.assets.filter(a=>ids.includes(a.id)&&!a.trashed_at);
 if(!assets.length)throw Error('请选择有效资产');
 if(target!==''&&!categories.includes(target))throw Error('请选择有效分类');
 if(!categoryDropAllowed(assets.map(a=>a.id),target))throw Error('视频只能放在视频参考中，图片请拖到图片分类');
 const changed=target?assets.filter(a=>a.category!==target).map(a=>a.id):[];
 // Finish the file move before removing the group; a failed move preserves membership.
 try{
  if(changed.length)await api('/api/move',{ids:changed,category:target});
  await api('/api/folder',{action:'remove',ids:assets.map(a=>a.id)});
  openFolder=null;category=target;selected.clear();page=0;
 }finally{await refresh();}
 toast(`已移出文件夹，放回${target||'各自原分类'}（${assets.length} 项）`);
}
function clearFolderGesture(){folderGesture=null;$('#gallery').classList.remove('folder-dragging');document.querySelector('.folder-drop-target')?.classList.remove('folder-drop-target');document.querySelector('.folder-drag-source')?.classList.remove('folder-drag-source');}
// Capture grouping gestures before the original multi-selection handler; no native image drag.
$('#gallery').addEventListener('pointerdown',e=>{if(!folderDragMode||category==='trash'||e.button!==0||e.target.closest('input'))return;const card=e.target.closest('#gallery .card');if(!card)return;const folder=card.dataset.folderTarget,ids=dragImageIds(card);if(!ids.length&&!folder)return;e.stopImmediatePropagation();folderGesture={pointerId:e.pointerId,id:ids[0]||folder,ids,folder,x:e.clientX,y:e.clientY,moved:false,card};},true);
document.addEventListener('pointermove',e=>{const g=folderGesture;if(!g||e.pointerId!==g.pointerId)return;if(e.pointerType==='mouse'&&!(e.buttons&1)){clearFolderGesture();return;}if(!g.moved&&Math.hypot(e.clientX-g.x,e.clientY-g.y)<7)return;g.moved=true;e.preventDefault();$('#gallery').classList.add('folder-dragging');g.card.classList.add('folder-drag-source');document.querySelector('.folder-drop-target')?.classList.remove('folder-drop-target');const button=categoryDropAt(e.clientX,e.clientY);if(button&&!g.folder&&categoryDropAllowed(gestureAssetIds(g),button.dataset.category)){button.classList.add('folder-drop-target');return;}const card=folderCardAt(e.clientX,e.clientY);if(card&&card!==g.card)card.classList.add('folder-drop-target');},true);
document.addEventListener('pointerup',e=>{const g=folderGesture;if(!g||e.pointerId!==g.pointerId)return;const card=folderCardAt(e.clientX,e.clientY),button=categoryDropAt(e.clientX,e.clientY);clearFolderGesture();if(!g.moved)return;e.preventDefault();e.stopImmediatePropagation();suppressCardClick=true;setTimeout(()=>suppressCardClick=false,100);if(button&&!g.folder){guarded(()=>dropAssetsToCategory(gestureAssetIds(g),button.dataset.category));return;}if(!card||card===g.card)return;const target=card.dataset.folderTarget,other=dragImageIds(card);if(g.folder){if(target&&target!==g.folder)guarded(async()=>{const folders=[...new Map(state.assets.filter(a=>a.folder_id&&!a.trashed_at).map(a=>[a.folder_id,a])).values()].sort((a,b)=>(a.folder_order??9999)-(b.folder_order??9999)).map(a=>a.folder_id);const from=folders.indexOf(g.folder),to=folders.indexOf(target);folders.splice(from,1);folders.splice(to,0,g.folder);await api('/api/folder',{action:'reorder',folder_ids:folders});await refresh();toast('文件夹顺序已保存');});return;}let ids=gestureAssetIds(g);if(target)guarded(()=>putFolder(ids,target));else if(other.length&&!other.every(id=>ids.includes(id)))guarded(()=>putFolder([...ids,...other]));},true);
document.addEventListener('pointercancel',clearFolderGesture,true);
document.addEventListener('click',e=>{if(e.target.closest('#gallery,#categories')&&suppressCardClick){e.preventDefault();e.stopImmediatePropagation();return;}const b=e.target.closest('[data-folder-open]');if(b){openFolder=b.dataset.folderOpen;selected.clear();page=0;render();}},true);
document.addEventListener('change',e=>{const id=e.target.dataset.folderSelect;if(id){visibleFolderMembers(id).forEach(a=>e.target.checked?selected.add(a.id):selected.delete(a.id));renderSelection();}});
document.querySelector('#categories').addEventListener('click',()=>{openFolder=null;},true);
window.addEventListener('blur',clearFolderGesture);
render();
