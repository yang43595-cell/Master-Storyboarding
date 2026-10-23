/* Offline integration candidate, derived from pinned v78. NOT the deployed renderer. */
/* One script page for immutable content packages and retained review versions.
   Adapters supply unchanged story markup and keep their original review stores. */
(function(global){
 'use strict';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function page(c,{navigation='',sourceVideo='',notice='',body='',history='',footer='当前仅你可见 · 意见与通过记录保留在各自版本',test=false}={}){
  return `<header class="case-heading script-review-heading" data-script-template="unified"><p class="case-kicker">案例 ${esc(c.id)}${test?' · 隔离测试':''}</p><h2>${esc(c.title)}</h2>${navigation}</header>${sourceVideo}${notice}${body}${history}<footer class="page-footer">${esc(footer)}</footer>`;
 }
 function legacyStory(scenes, version='') {
  return `<div class="script-text canonical-story" data-case01-version="${esc(version)}">${scenes.map(s=>`<div class="script-scene"><h4>${esc(s.heading)}</h4>${s.lines.join('\n').trim().split(/\n\s*\n/).filter(Boolean).map(p=>`<p${/^[^\n]+（DLG-(?:C\d+-)?\d+）：/.test(p)?' class="script-dialogue"':''}>${esc(p.replace(/（DLG-(?:C\d+-)?\d+）/g,''))}</p>`).join('')}</div>`).join('')}</div>`;
 }
 function paragraphs(text) {
  return text.trim().split(/\n\s*\n/).filter(Boolean).map(p=>`<p style="white-space:pre-wrap">${esc(p)}</p>`).join('');
 }
 function story(scenes, version='') {
  if(!scenes.some(s=>s.technical!==undefined))return legacyStory(scenes,version);
  return `<div class="script-text canonical-story" data-case01-version="${esc(version)}">${scenes.map(s=>`<div class="script-scene"${s.id?` data-scene-id="${esc(s.id)}"`:''}><h4>${esc(s.heading)}</h4>${paragraphs(s.lines.join('\n'))}${s.technical===undefined?'':`<details class="simple-details director-technical"${s.technicalOpen?' open':''}><summary>逐镜执行附录（同一 JSON）</summary>${paragraphs(s.technical)}</details>`}</div>`).join('')}</div>`;
 }
 // Retained delivery documents use the same reading view, but their review
 // coordinates remain the exact H4/P model used before this presentation.
 // No content head or review namespace is created by this projection.
 function fromArtifact(artifact,{version='',currentStory=false}={}) {
  if(!artifact?.textContent)return null;
  const sourceBlocks=[{id:'STP-001:heading',text:artifact.title+(currentStory?' · 原对白待核':' · 本批文字依据'),kind:'h4'}];
  let paragraph=0;
  for(const text of artifact.textContent.split(/\n\s*\n/)){
   const heading=/^#{1,6} /.test(text)&&!text.includes('\n');
   sourceBlocks.push({id:'STP-001:'+(heading?'heading':'p-'+String(++paragraph).padStart(3,'0')),text:heading?text.replace(/^#{1,6} /,'').replace(/^C0[234]-F\d+｜/,''):text,kind:heading?'h4':'p',scene:heading&&/^(?:先读完整故事|C\d+-F\d+｜|STP-(?:C\d+-)?\d+｜|段落\S+｜|广告单元\S+｜)/.test(text.replace(/^#{1,6} /,''))});
  }
  const groups=[{intro:true,views:[]}];let group=groups[0],sceneCount=0;
  sourceBlocks.forEach((block,blockIndex)=>{
   let offset=0;
   for(const line of block.text.split('\n')){
    const start=offset;offset+=line.length+1;if(!line.trim())continue;
    const heading=line.match(/^(#{1,6}) (.+)$/);
    const scene=block.scene||heading&&/^(?:先读完整故事|C\d+-F\d+｜|STP-(?:C\d+-)?\d+｜|段落\S+｜|广告单元\S+｜)/.test(heading[2]);
    if(scene){group={views:[]};groups.push(group);sceneCount++;}
    const auxiliary=group.intro||/^(?:视觉候选[｜|]|原片事件视觉候选[｜|]|原创广告融入段$|专属待定[：:]|衔接[：:]|依据[：:])/.test(line);
    group.views.push({blockIndex,start:start+(heading?heading[1].length+1:0),end:start+line.length,tag:heading||block.kind==='h4'?'h4':'p',auxiliary});
   }
  });
  // Unknown source formats stay in the lossless existing fallback.
  if(!sceneCount)return null;
  const view=v=>`<${v.tag} data-script-source-block="${v.blockIndex}" data-script-source-start="${v.start}" data-script-source-end="${v.end}">${esc(sourceBlocks[v.blockIndex].text.slice(v.start,v.end))}</${v.tag}>`;
  const html=groups.map((g,index)=>{
   if(g.intro)return `<details class="simple-details script-source-support" data-artifact-id="${esc(artifact.id)}"><summary>稿源与核对说明</summary>${g.views.map(view).join('')}</details>`;
   let body='',pending=[];const flush=()=>{if(pending.length){body+=`<details class="simple-details script-scene-support"><summary>衔接与待核说明</summary>${pending.map(view).join('')}</details>`;pending=[];}};
   for(const v of g.views){if(v.auxiliary)pending.push(v);else{flush();body+=view(v);}}flush();
   return `<div class="script-scene" data-script-reading-scene="${index}">${body}</div>`;
  }).join('');
  return {sourceBlocks,sceneCount,html:`<div class="script-text canonical-story" data-case01-version="${esc(version)}" data-script-source-model="${esc(JSON.stringify(sourceBlocks.map(({id,text})=>({id,text}))))}">${html}</div>`};
 }
 function fromPackage(content) {
  const isSource=b=>b.id==='canonical-source'||/^canonical-source-part-\d+$/.test(b.id);
  const isBinding=b=>b.id==='source-binding';
  // The adapter's original scene IDs are retained. No renumbering, stale-head
  // selection, source substitution or approval mutation takes place here.
  if(content.stage==='director') {
   const marker='【逐镜执行附录】';
   const sourceIds=new Set();
   try {
    const source=JSON.parse(content.blocks.filter(isSource).map(b=>b.text).join(''));
    (source.scenes||[]).forEach(s=>sourceIds.add(s.scene_id));
   } catch (_) { /* Unknown/partial source keeps the lossless generic fallback. */ }
   const reading=content.blocks.filter(b=>sourceIds.has(b.id));
   if(reading.length===sourceIds.size&&reading.length&&reading.every(b=>b.text.includes(marker))) {
    const scenes=reading.map(b=>{
     const index=b.text.indexOf(marker),body=b.text.slice(0,index).trim();
     return {id:b.id,heading:b.heading,lines:body.split('\n'),
      technical:b.text.slice(index+marker.length).trim(),technicalOpen:body.includes('阅读提示：当前正文未完整承载')};
    });
    const consumed=new Set(reading.map(b=>b.id));
    return {scenes,extras:content.blocks.filter(b=>b.id!=='benchmark-reference').flatMap(b=>!consumed.has(b.id)?[b]:b.media?.length?[{...b,text:''}]:[])};
   }
  }
  const scenes=[], storyBlocks=new Set();
  for (const block of content.blocks) {
   if(block.id==='benchmark-reference'||block.id==='source-binding')continue;
   if (/^STP-(?:C\d+-)?\d+/.test(block.heading)) { scenes.push({heading:block.heading.replace(/^STP-(?:C\d+-)?\d+｜/,''),lines:block.text.split('\n')}); storyBlocks.add(block.id); }
   else if (/^故事正文/.test(block.heading)) {
    let scene=null;
    for (const line of block.text.split('\n')) { if (/^(?:### )?STP-(?:C\d+-)?\d+｜/.test(line)) { scene={heading:line.replace(/^(?:### )?STP-(?:C\d+-)?\d+｜/,''),lines:[]};scenes.push(scene); } else if (scene) scene.lines.push(line); }
    if (scene) storyBlocks.add(block.id);
   }
  }
  // Unknown packages retain all source text rather than inventing story structure.
  if (!scenes.length) for (const b of content.blocks.filter(b=>b.id!=='benchmark-reference'&&!isBinding(b)&&!isSource(b))) { scenes.push({heading:b.heading,lines:b.text.split('\n')});storyBlocks.add(b.id); }
  return {scenes,extras:content.blocks.filter(b=>b.id!=='benchmark-reference').flatMap(b=>!storyBlocks.has(b.id)?[b]:b.media?.length?[{...b,text:''}]:[])};
 }
 global.ReviewScriptTemplate={page,story,fromPackage,fromArtifact};
 if(typeof module!=='undefined'&&module.exports)module.exports={page,story,fromPackage,fromArtifact};
})(typeof window!=='undefined'?window:globalThis);
