/* Reserved immutable package blocks; shared by browser and publication validation. */
(function(global){
 'use strict';
 const reserved=id=>id==='benchmark-reference'||id==='source-binding'||id==='asset-layout';
 const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
 function parse(block,keys){let value;try{value=JSON.parse(block.text);}catch{throw Error(block.id+' must contain valid JSON');}if(!value||Array.isArray(value)||typeof value!=='object'||Object.keys(value).some(k=>!keys.includes(k))||keys.some(k=>!(k in value))||value.schemaVersion!==1)throw Error(block.id+' has invalid metadata');return value;}
 const id=v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,120}$/.test(v);
 const text=v=>typeof v==='string'&&v.trim().length>0&&v.length<=3000;
 const chinese=v=>text(v)&&/[\u3400-\u9fff]/.test(v);
 const strings=v=>Array.isArray(v)&&v.length<=100&&v.every(text);
 function exact(value,keys){return value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));}
 function assetLayout(content){
  const entries=(content.blocks||[]).filter(b=>b.id==='asset-layout');if(!entries.length)return null;
  if(entries.length!==1||content.stage!=='assets'||entries[0].media.length)throw Error('asset-layout requires one empty-media metadata block on assets');
  const layout=parse(entries[0],['schemaVersion','groups','blockers']);
  if(!Array.isArray(layout.groups)||!layout.groups.length||layout.groups.length>100||!strings(layout.blockers))throw Error('asset-layout requires groups and string blockers');
  const all=content.blocks.filter(b=>!reserved(b.id)).flatMap(b=>b.media.map(m=>[b.id,m.id])),seen=new Set(),groups=new Set();
  for(const g of layout.groups){
   if(!exact(g,['id','kind','title','items'])||!id(g.id)||groups.has(g.id)||!['character','wardrobe','setting','product','action','continuity','reference'].includes(g.kind)||!chinese(g.title)||!Array.isArray(g.items)||!g.items.length)throw Error('asset-layout invalid group');groups.add(g.id);
   for(const m of g.items){
    if(!exact(m,['blockId','mediaId','title','role','origin','status','usageScope','exclusions'])||!id(m.blockId)||!id(m.mediaId)||!chinese(m.title)||!chinese(m.role)||!['user','generated','source_frame','source_clip','reference'].includes(m.origin)||!['reference','candidate'].includes(m.status)||!text(m.usageScope)||!strings(m.exclusions))throw Error('asset-layout invalid media purpose fields');
    const key=m.blockId+'::'+m.mediaId;if(seen.has(key)||!all.some(([b,i])=>b===m.blockId&&i===m.mediaId))throw Error('asset-layout invalid or duplicate media target');seen.add(key);
   }
  }
  if(seen.size!==all.length)throw Error('asset-layout must include every production media exactly once');
  if(layout.blockers.length&&content.reviewable)throw Error('asset-layout unresolved blockers cannot be reviewable');
  return layout;
 }
 function read(content){
  const blocks=content.blocks||[],bindings=blocks.filter(b=>b.id==='source-binding'),refs=blocks.filter(b=>b.id==='benchmark-reference');
  if(bindings.length>1||refs.length>1)throw Error('Duplicate reserved package blocks');
  let binding=null,benchmark=null;
  if(bindings.length){binding=parse(bindings[0],['schemaVersion','source_sha256','native_sha256']);if(!hash(binding.source_sha256)||!hash(binding.native_sha256)||bindings[0].media.length)throw Error('source-binding has invalid hashes or media');}
  if(refs.length){const b=refs[0],r=parse(b,['schemaVersion','sourceUrl','sourceVideoId','author','clipStartSeconds','clipEndSeconds','clipLabel','videoMediaId','posterMediaId']);
   if(typeof r.sourceVideoId!=='string'||!/^\d+$/.test(r.sourceVideoId)||r.sourceUrl!=='https://www.douyin.com/video/'+r.sourceVideoId||typeof r.author!=='string'||!r.author.trim()||typeof r.clipLabel!=='string'||!r.clipLabel.trim()||!Number.isFinite(r.clipStartSeconds)||r.clipStartSeconds<0||!Number.isFinite(r.clipEndSeconds)||r.clipEndSeconds<=r.clipStartSeconds)throw Error('benchmark-reference has invalid source or clip range');
   const video=b.media.find(m=>m.id===r.videoMediaId),poster=b.media.find(m=>m.id===r.posterMediaId);
   if(b.media.length!==2||!video||!poster||video===poster||video.mime!=='video/mp4'||!['image/png','image/jpeg','image/webp'].includes(poster.mime))throw Error('benchmark-reference requires its exact video and poster');
   for(const m of [video,poster])if(!hash(m.sha256)||m.url!=='/api/content-media/'+m.sha256||!Number.isInteger(m.bytes)||m.bytes<1||m.bytes>32*1024*1024)throw Error('benchmark-reference media integrity metadata invalid');
   benchmark={...r,video,poster};
  }
  if(binding&&content.stage==='script'&&!benchmark)throw Error('Bound script package requires benchmark-reference');
  return {binding,benchmark,assetLayout:assetLayout(content)};
 }
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function render(benchmark,caseId){if(!benchmark)return '';const r=benchmark;return `<section class="benchmark-source" aria-label="案例${esc(caseId)}对标原视频" data-package-benchmark><div class="benchmark-source-head"><h3>${esc(r.clipLabel)} <small>${esc(r.author)}</small></h3><a href="${esc(r.sourceUrl)}" target="_blank" rel="noopener noreferrer">原链接 ↗</a></div><video controls playsinline preload="metadata" poster="${esc(r.poster.url)}" src="${esc(r.video.url)}" aria-label="案例${esc(caseId)}对标原视频 · ${esc(r.author)}"></video><p class="c01-source-caption">所用片段审阅压缩版 · 原音轨保留</p></section>`;}
 const api={read,render,reserved};global.ReviewPackageMetadata=api;if(typeof module!=='undefined'&&module.exports){module.exports.read=read;module.exports.render=render;module.exports.reserved=reserved;}
})(typeof window!=='undefined'?window:globalThis);
