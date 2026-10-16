'use strict';
// Synthetic, dependency-free check of the restored real subscriber. No service requests.
// node benchmark-source-selection.cjs /absolute/path/to/restored/site-source
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]||'.');
const source=fs.readFileSync(path.join(root,'review-static/content-subscription.js'),'utf8');
const metadata=require(path.join(root,'lib/package-metadata.cjs'));
const media=(id,mime,char)=>({id,mime,sha256:char.repeat(64),url:'/api/content-media/'+char.repeat(64),bytes:123,caption:'synthetic'});
const binding={id:'source-binding',heading:'来源',text:JSON.stringify({schemaVersion:1,source_sha256:'c'.repeat(64),native_sha256:'d'.repeat(64)}),media:[]};
const benchmark={id:'benchmark-reference',heading:'对标',text:JSON.stringify({schemaVersion:1,sourceUrl:'https://www.douyin.com/video/123456789',sourceVideoId:'123456789',author:'测试',clipStartSeconds:0,clipEndSeconds:10,clipLabel:'测试片段',videoMediaId:'video',posterMediaId:'poster'}),media:[media('video','video/mp4','a'),media('poster','image/jpeg','b')]};
const fallback='<section class="benchmark-source"><video src="synthetic-validated-case05-registry"></video></section>';
async function render(stage,blocks,historical=false,caseId='05'){
 const content={schemaVersion:1,caseId,stage,revision:'synthetic',sourceScriptVersion:'e'.repeat(64),title:'测试',summary:'',reviewable:false,blocks:[{id:'body',heading:'测试',text:'Synthetic fixture',media:[]},...blocks]};
 const item={scope:'qa',caseId,stage,content,version:'f'.repeat(64),isCurrent:!historical,sourceStale:false,approval:'pending'};
 let mounted=null,html='';const stop=Error('stop after real generic render');
 const panel={set innerHTML(value){html=value;},querySelector(){throw stop;}};
 const mount=args=>{mounted=args;return {caseId,dispose(){}};};
 const window={location:{search:'?stage='+stage+'&content-test=1'+(historical?'&content-version='+item.version:''),hash:'#case-'+caseId,pathname:'/'},ReviewPackageMetadata:metadata,ReviewSubscribedAssets:{mount},ReviewSubscribedScript:{mount},CaseReviewConfigs:{},addEventListener(){},setInterval(){}};
 const context={window,URLSearchParams,fetch:async url=>({ok:true,json:async()=>url.includes('/heads?')?{heads:[{stage}]}:item})};
 vm.runInNewContext(source,context);await window.ReviewContentSubscription.load([caseId]);
 try{window.ReviewContentSubscription.render({id:caseId,title:'测试'},panel,fallback);}catch(e){if(e!==stop)throw e;}
 return mounted?.sourceVideo??html;
}
(async()=>{
 let checks=0;
 for(const stage of ['script','assets','director','white_model','storyboard','prompts'])for(const historical of [false,true]){
  assert((await render(stage,[],historical)).includes(fallback),stage+' legacy fallback');checks++;
  const embedded=await render(stage,[binding,benchmark],historical);
  assert(embedded.includes(benchmark.media[0].url));assert(embedded.includes(benchmark.media[1].url));assert(!embedded.includes('synthetic-validated-case05-registry'));assert(!embedded.includes('autoplay'));checks++;
  const missing=await render(stage,[binding],historical);
  assert(!missing.includes('synthetic-validated-case05-registry'));
  assert.match(missing,stage==='script'?/来源校验失败/:/对标原视频缺失/);checks++;
  const malformed=await render(stage,[{...benchmark,text:'{}'}],historical);assert.match(malformed,/来源校验失败/);assert(!malformed.includes(fallback));checks++;
 }
 console.log(JSON.stringify({passed:true,checks,fixture:'synthetic only',network:'in-memory doubles; no writes'}));
})().catch(error=>{console.error(error);process.exitCode=1;});
