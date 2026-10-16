'use strict';
// Small collection controls; production asset groups remain separate.
const originalCollectionRender = render;
render = function(){
  const products = category === '产品', trash = category === 'trash';
  $('#product-tools').classList.toggle('hidden', !products);
  $('#trash-tools').classList.toggle('hidden', !trash);
  if (!products) $('#product-filter').value = '';
  const current = $('#product-filter').value;
  $('#product-filter').innerHTML = '<option value="">全部产品</option>' +
    (state.product_categories || []).map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  $('#product-filter').value = current;
  originalCollectionRender();
  $('#result-count').textContent=$('#result-count').textContent.replace(/ 张$/, ' 项');
  for (const button of document.querySelectorAll('#gallery [data-detail]')) {
    const asset = state.assets.find(a => a.id === button.dataset.detail);
    if (asset?.media_type === 'video') {
      button.insertAdjacentHTML('beforeend', `<span class="video-mark">▶ ${Math.round(asset.duration || 0)} 秒</span>`);
    }
  }
};
$('#product-filter').onchange = () => {selected.clear(); page=0; render();};

function productCategoryChoices(value='') {
  return '<option value="">未细分</option>' + (state.product_categories || []).map(c =>
    `<option value="${esc(c.id)}" ${c.id===value?'selected':''}>${esc(c.name)}</option>`).join('');
}
function productCategoryManager() {
  modal('管理产品分类', `<p>按需要创建分类。资产留在“产品”总类中，细分可以随时调整。</p>
    <div class="category-list">${(state.product_categories||[]).map(c=>`<div><span>${esc(c.name)}</span><button data-product-rename="${esc(c.id)}">改名</button><button data-product-delete="${esc(c.id)}">删除</button></div>`).join('')||'<p class="muted">还没有手动分类。</p>'}</div>
    <form id="product-category-form"><label>新分类名称<input name="name" maxlength="60" required placeholder="按你的素材需要命名"></label><div class="form-footer"><button class="primary">新建分类</button></div></form>`);
  $('#product-category-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{
    await api('/api/product-category',{action:'create',name:new FormData(e.target).get('name')});
    await refresh(); productCategoryManager(); toast('产品分类已创建');
  },e.submitter);};
}
$('#product-category-new').onclick=productCategoryManager;
$('#product-category-manage').onclick=productCategoryManager;
$('#dialog-body').addEventListener('click',e=>{
  const rename=e.target.closest('[data-product-rename]'), remove=e.target.closest('[data-product-delete]');
  if(rename){const item=state.product_categories.find(c=>c.id===rename.dataset.productRename);
    modal('修改产品分类',`<form id="product-category-rename"><label>分类名称<input name="name" maxlength="60" required value="${esc(item.name)}"></label><div class="form-footer"><button class="primary">保存</button></div></form>`);
    $('#product-category-rename').onsubmit=ev=>{ev.preventDefault();guarded(async()=>{
      await api('/api/product-category',{action:'rename',id:item.id,name:new FormData(ev.target).get('name')});
      await refresh();productCategoryManager();toast('分类名称已更新');
    },ev.submitter);};
  }
  if(remove){const item=state.product_categories.find(c=>c.id===remove.dataset.productDelete);
    modal('删除产品分类',`<p>删除“${esc(item.name)}”分类。分类里仍有资产时会阻止删除。</p><button id="confirm-product-delete">删除分类</button>`);
    $('#confirm-product-delete').onclick=button=>guarded(async()=>{
      await api('/api/product-category',{action:'delete',id:item.id});
      await refresh();productCategoryManager();toast('分类已删除');
    },button.currentTarget);
  }
});

const imageCollectionDetail=detail;
detail=function(id){
  const a=state.assets.find(x=>x.id===id); if(!a)return;
  if(a.file_error){modal(a.name,`<p class="project-warning">${esc(a.file_error)}</p><p>原有确认记录保留。恢复原文件后刷新页面，即可继续选用。</p>`);return;}
  if(a.media_type==='video'){
    if(a.trashed_at){
      modal(a.name,`<img class="detail-image" src="/media/${a.id}/thumbnail" alt="${esc(a.name)}"><p>自动清理时间：${esc(new Date(a.expires_at).toLocaleString())}</p><button id="restore-video">恢复视频</button>`);
      $('#restore-video').onclick=e=>guarded(async()=>{await api('/api/restore',{ids:[id]});$('#dialog').close();await refresh();toast('视频已恢复');},e.currentTarget);return;
    }
    modal(a.name,`<div class="detail-layout"><div><video class="detail-image" controls preload="metadata" poster="/media/${a.id}/thumbnail" src="/media/${a.id}/original">浏览器无法播放此视频，请在资产库文件夹查看原文件。</video><p class="muted">${Math.round(a.duration||0)} 秒 · ${a.width} × ${a.height} · ${(a.bytes/1048576).toFixed(1)} MB</p></div><form id="video-detail-form" class="detail-fields"><label>资产名称（可修改）<input name="name" value="${esc(a.name)}" required maxlength="200"></label><label>用途<select name="role">${options(roles,a.role)}</select></label><label>标签（逗号分隔）<input name="tags" value="${esc(a.tags.join('，'))}"></label><div class="form-footer"><button class="primary">保存修改</button></div></form></div>`);
    $('#video-detail-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{
      const data=Object.fromEntries(new FormData(e.target));data.id=id;data.tags=data.tags.split(/[,，]/).map(t=>t.trim()).filter(Boolean);
      await api('/api/update',data);await refresh();toast('视频信息已保存');
    },e.submitter);};
    return;
  }
  imageCollectionDetail(id);
  if(a.trashed_at)return;
  const form=$('#detail-form');
  form.elements.category.querySelector('option[value="视频参考"]')?.remove();
  form.querySelector('button[type="submit"]').textContent='保存修改';
  const product=document.createElement('label');product.id='detail-product-category';product.innerHTML=`产品手动分类<select name="product_category_id">${productCategoryChoices(a.product_category_id)}</select>`;
  form.querySelector('.form-grid').after(product);
  const sync=()=>product.classList.toggle('hidden',form.elements.category.value!=='产品');
  form.elements.category.addEventListener('change',sync);sync();
  form.elements.name.closest('label').firstChild.textContent='资产名称（可修改）';
};

const productAssignButton=document.createElement('button');productAssignButton.id='product-assign-selection';productAssignButton.textContent='产品分类';
$('#move-selection').before(productAssignButton);
const originalRenderSelection=renderSelection;
renderSelection=function(){originalRenderSelection();productAssignButton.classList.toggle('hidden',category!=='产品');};
productAssignButton.onclick=()=>{
  modal('给所选产品分类',`<form id="product-assign-form"><p>为选中的 ${selected.size} 项产品资产设置手动分类。</p><label>分类<select name="product_category_id">${productCategoryChoices()}</select></label><div class="form-footer"><button class="primary">保存分类</button></div></form>`);
  $('#product-assign-form').onsubmit=e=>{e.preventDefault();guarded(async()=>{
    await api('/api/product-assign',{ids:[...selected],product_category_id:new FormData(e.target).get('product_category_id')});
    selected.clear();$('#dialog').close();await refresh();toast('产品分类已保存');
  },e.submitter);};
};

function purgeDialog(ids,skipped=0){
  if(!ids.length){toast('垃圾桶里没有可清理的资产');return;}
  modal('永久清理垃圾桶',`<p>将永久删除 ${ids.length} 项资产在资产库及知识库中的文件。删除后不能从垃圾桶恢复。</p>${skipped?`<p>${skipped} 项已确认或被项目引用，已排除。</p>`:''}<p class="muted">电脑其他位置的原始文件不在本次清理范围。</p><button id="confirm-purge" class="primary">永久删除 ${ids.length} 项</button>`);
  $('#confirm-purge').onclick=e=>guarded(async()=>{
    const result=await api('/api/purge',{ids});selected.clear();$('#dialog').close();await refresh();toast(`已永久清理 ${result.count} 项资产`);
  },e.currentTarget);
}
$('#purge-selection').onclick=()=>purgeDialog([...selected]);
$('#purge-all').onclick=()=>{
  const trash=state.assets.filter(a=>a.trashed_at);
  const used=new Set(state.jobs.filter(j=>j.project_id).flatMap(j=>j.steps.flatMap(s=>s.outputs||[])));
  const ids=trash.filter(a=>a.status!=='confirmed'&&!a.project_confirmations?.length&&!used.has(a.id)).map(a=>a.id);
  const skipped=trash.length-ids.length;
  purgeDialog(ids,skipped);
};
