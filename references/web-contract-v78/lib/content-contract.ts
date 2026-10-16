import { read as readPackageMetadata } from '../lib/package-metadata.cjs';
import { z } from 'zod';
export const Stage = z.enum(['script','assets','director','white_model','storyboard','prompts']);
export const CaseId = z.enum(['01','02','03','04','05','06']);
export const Hash = z.string().regex(/^[a-f0-9]{64}$/);
export const Scope = z.enum(['live','qa']);
const Id = z.string().regex(/^[A-Za-z0-9_-]{1,120}$/);
export const Media = z.object({
 id:Id,url:z.string().regex(/^\/api\/content-media\/[a-f0-9]{64}$/),
 sha256:Hash,mime:z.enum(['image/png','image/jpeg','image/webp','video/mp4']),bytes:z.number().int().min(1).max(32*1024*1024),caption:z.string().max(3000),
}).strict().refine(m=>!m.url.startsWith('/api/content-media/')||m.url.endsWith('/'+m.sha256),'Media URL must match its immutable hash');
export const ContentPackage = z.object({
 schemaVersion:z.literal(1),caseId:CaseId,stage:Stage,revision:z.string().trim().min(1).max(100),sourceScriptVersion:Hash,
 title:z.string().trim().min(1).max(200),summary:z.string().max(6000).default(''),reviewable:z.boolean().default(false),
 blocks:z.array(z.object({id:Id,heading:z.string().max(200),text:z.string().max(50000).default(''),media:z.array(Media).max(30).default([])}).strict()).min(1).max(300),
}).strict().superRefine((p,ctx)=>{
 try{readPackageMetadata(p);}catch(error){ctx.addIssue({code:'custom',message:error instanceof Error?error.message:'Invalid package metadata'});}
 const ids=p.blocks.map(x=>x.id);if(new Set(ids).size!==ids.length)ctx.addIssue({code:'custom',message:'Duplicate block IDs'});
 const media=p.blocks.flatMap(b=>b.media);if(media.length>200)ctx.addIssue({code:'custom',message:'At most 200 media items per stage package'});if(new Set(media.map(m=>m.id)).size!==media.length)ctx.addIssue({code:'custom',message:'Duplicate media IDs'});
});
export const PublishRequest=z.object({scope:Scope,expectedVersion:Hash.nullable(),content:ContentPackage}).strict().superRefine(({content},ctx)=>{if(content.stage==='assets'&&content.blocks.some(b=>b.id==='source-binding')&&!content.blocks.some(b=>b.id==='asset-layout'))ctx.addIssue({code:'custom',message:'New bound asset publication requires explicit asset-layout'});});
const ScriptAnnotation=z.object({
 id:z.string().regex(/^[a-zA-Z0-9_-]{1,159}$/),type:z.enum(['comment','replacement']),
 anchor:z.object({documentVersion:Hash,start:z.number().int().min(0),end:z.number().int().min(1),quote:z.object({exact:z.string().min(1).max(100000),prefix:z.string().max(40).optional(),suffix:z.string().max(40).optional()}).strict(),segments:z.array(z.object({blockId:z.string().max(160),start:z.number().int().min(0),end:z.number().int().min(0)}).strict()).max(1000).optional()}).strict(),
 comment:z.string().max(12000).optional(),replacement:z.string().max(12000).optional(),createdAt:z.string().max(50).optional(),updatedAt:z.string().max(50).optional(),
}).strict().refine(a=>typeof a[a.type]==='string');
const ScriptReview=z.object({overall:z.string().max(12000),annotations:z.array(ScriptAnnotation).max(500),assetReviews:z.array(z.object({id:z.string().regex(/^[a-zA-Z0-9_-]{8,160}$/),target:z.object({stage:z.literal('script'),kind:z.literal('stage'),title:z.string().max(240)}).strict(),comment:z.string().max(12000),attachments:z.array(z.object({id:z.string().regex(/^att_[a-f0-9]{32}$/),purpose:z.enum(['face','clothing','color','composition','other']),note:z.string().max(2000).optional()}).passthrough()).max(8)}).strict()).max(1)}).strict();
export const FeedbackRequest=z.object({
 id:z.string().regex(/^[A-Za-z0-9_-]{8,100}$/),scope:Scope,caseId:CaseId,stage:Stage,version:Hash,
 scriptReview:ScriptReview.optional(),
 decision:z.enum(['changes','approve']),comment:z.string().trim().max(12000),blockId:Id.nullable().default(null),mediaId:Id.nullable().default(null),region:z.object({x:z.number().min(0).max(1),y:z.number().min(0).max(1),width:z.number().gt(0).max(1),height:z.number().gt(0).max(1)}).strict().refine(r=>r.x+r.width<=1.000001&&r.y+r.height<=1.000001).nullable().default(null),selection:z.object({exact:z.string().min(1).max(12000),start:z.number().int().min(0),end:z.number().int().min(1)}).strict().nullable().default(null),
 attachments:z.array(z.object({id:z.string().regex(/^att_[a-f0-9]{32}$/),purpose:z.enum(['face','clothing','color','composition','other']).default('other'),note:z.string().max(2000).default('')}).strict()).max(8).default([]),
}).strict().superRefine((p,ctx)=>{
 const r=p.scriptReview;
 if(r){
  if(p.stage!=='script'||p.blockId||p.mediaId||p.selection||p.region)ctx.addIssue({code:'custom',message:'编导批注必须绑定本版本编导正文'});
  if(r.annotations.some(a=>a.anchor.documentVersion!==p.version))ctx.addIssue({code:'custom',message:'编导批注版本不匹配'});
  if(r.assetReviews.some(a=>!a.comment.trim()&&!a.attachments.length))ctx.addIssue({code:'custom',message:'参考图意见不能为空'});
  const refs=r.assetReviews.flatMap(a=>Array.isArray(a?.attachments)?a.attachments:[]);
  if(refs.length!==p.attachments.length||refs.some(a=>!p.attachments.some(b=>a.id===b.id&&a.purpose===b.purpose&&(a.note||'')===b.note)))ctx.addIssue({code:'custom',message:'参考图关联不匹配'});
 }
 const feedback=!!p.comment||p.attachments.length>0||!!(r&&(r.overall.trim()||r.annotations.length||r.assetReviews.length));
 if(p.decision==='changes'&&!feedback)ctx.addIssue({code:'custom',message:'请先填写修改意见或上传参考图'});
 if(p.decision==='approve'&&(feedback||p.blockId!==null||p.mediaId!==null||p.selection!==null||p.region!==null))ctx.addIssue({code:'custom',message:'仍有修改意见，不能同时通过整个阶段'});
 if(new Set(p.attachments.map(a=>a.id)).size!==p.attachments.length)ctx.addIssue({code:'custom',message:'Duplicate attachment IDs'});
});
export type Feedback = z.infer<typeof FeedbackRequest>;
export type Package = z.infer<typeof ContentPackage>;
export function canonicalJSON(value:unknown):string {
 if(value===null||typeof value!=='object')return JSON.stringify(value);
 if(Array.isArray(value))return '['+value.map(canonicalJSON).join(',')+']';
 return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonicalJSON((value as Record<string,unknown>)[k])).join(',')+'}';
}
export async function digest(value:string|Uint8Array<ArrayBuffer>):Promise<string>{
 const bytes=typeof value==='string'?new TextEncoder().encode(value):value;
 return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
}

/** Cheap ISO-BMFF structural validation, not a promise of browser codec support.
 * Full first-frame decoding happens in the publication helper before upload.
 * Accepts non-fast-start, 64-bit box lengths and fragmented MP4 video tracks.
 */
export function validMP4Structure(bytes:Uint8Array):boolean {
 type Box={type:string,start:number,end:number};
 const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 const tag=(at:number)=>String.fromCharCode(...bytes.subarray(at,at+4));
 let budget=100000;
 const boxes=(from:number,to:number):Box[]=>{
  const result:Box[]=[];
  for(let at=from;at<to;){
   if(--budget<0||to-at<8)throw new Error('Invalid box header');
   let size=view.getUint32(at),header=8;const type=tag(at+4);
   if(size===1){
    if(to-at<16)throw new Error('Missing extended box size');
    size=view.getUint32(at+8)*4294967296+view.getUint32(at+12);header=16;
    if(!Number.isSafeInteger(size))throw new Error('Unsafe box size');
   }else if(size===0)size=to-at;
   if(size<header||size>to-at)throw new Error('Invalid box boundary');
   result.push({type,start:at+header,end:at+size});at+=size;
  }
  return result;
 };
 const cache=new Map<Box,Box[]>();
 const children=(box:Box)=>{let result=cache.get(box);if(!result){result=boxes(box.start,box.end);cache.set(box,result);}return result;};
 const containers=new Set(['moov','trak','mdia','minf','stbl','mvex','moof','traf','edts','dinf']);
 const traverse=(list:Box[],depth=0)=>{if(depth>12)throw new Error('Excessive box nesting');for(const box of list)if(containers.has(box.type))traverse(children(box),depth+1);};
 const one=(list:Box[],type:string)=>list.find(box=>box.type===type);
 const hasCount=(box:Box|undefined,offset:number,min=1)=>!!box&&box.end-box.start>=offset+4&&view.getUint32(box.start+offset)>=min;
 try{
  const top=boxes(0,bytes.length),ftyp=one(top,'ftyp'),moov=one(top,'moov');traverse(top);
  if(!ftyp||!moov||ftyp.end-ftyp.start<8||(ftyp.end-ftyp.start)%4!==0||top.filter(b=>b.type==='ftyp').length!==1||top.filter(b=>b.type==='moov').length!==1)return false;
  const brands=[tag(ftyp.start)];for(let at=ftyp.start+8;at<ftyp.end;at+=4)brands.push(tag(at));
  if(!brands.some(brand=>/^(?:isom|iso[2-9]|mp4[12]|avc1|hvc1|hev1|dash|cmf[cs]|M4V |MSNV)$/.test(brand)))return false;
  if(!top.some(box=>box.type==='mdat'&&box.end>box.start))return false;
  const movie=children(moov),mvhd=one(movie,'mvhd');
  if(!mvhd||mvhd.end-mvhd.start<20)return false;
  const tracks=movie.filter(box=>box.type==='trak').map(trak=>{
   const track=children(trak),tkhd=one(track,'tkhd'),mdia=one(track,'mdia');
   if(!tkhd||!mdia)return null;
   const version=bytes[tkhd.start],trackOffset=version===0?12:version===1?20:-1;
   if(trackOffset<0||tkhd.end-tkhd.start<(version===0?84:96))return null;
   const id=view.getUint32(tkhd.start+trackOffset),media=children(mdia),hdlr=one(media,'hdlr'),minf=one(media,'minf');
   if(!id||!hdlr||hdlr.end-hdlr.start<12||tag(hdlr.start+8)!=='vide'||!minf)return null;
   const stbl=one(children(minf),'stbl');if(!stbl)return null;
   const table=children(stbl),stsd=one(table,'stsd');
   if(!hasCount(stsd,4)||!stsd)return null;
   const entries=boxes(stsd.start+8,stsd.end);
   if(entries.length!==view.getUint32(stsd.start+4)||!entries.some(entry=>entry.end-entry.start>=78))return null;
   const tableCount=(type:string,entryBytes:number)=>{
    const box=one(table,type);return !!box&&hasCount(box,4)&&box.end-box.start>=8+view.getUint32(box.start+4)*entryBytes;
   };
   const stsz=one(table,'stsz'),stz2=one(table,'stz2');
   const samples=(!!stsz&&hasCount(stsz,8)&&(view.getUint32(stsz.start+4)>0||stsz.end-stsz.start>=12+view.getUint32(stsz.start+8)*4))
    ||(!!stz2&&hasCount(stz2,8)&&[4,8,16].includes(bytes[stz2.start+7])&&stz2.end-stz2.start>=12+Math.ceil(view.getUint32(stz2.start+8)*bytes[stz2.start+7]/8));
   const regular=samples&&tableCount('stts',8)&&tableCount('stsc',12)&&(tableCount('stco',4)||tableCount('co64',8));
   return {id,regular};
  }).filter((track):track is {id:number,regular:boolean}=>track!==null);
  if(tracks.some(track=>track.regular))return true;
  const mvex=one(movie,'mvex');if(!mvex)return false;
  const defaults=children(mvex).filter(box=>box.type==='trex'&&box.end-box.start>=24).map(box=>view.getUint32(box.start+4));
  let fragmentedVideo=false;
  for(const moof of top.filter(box=>box.type==='moof')){
   const fragment=children(moof),mfhd=one(fragment,'mfhd');if(!mfhd||mfhd.end-mfhd.start<8)return false;
   for(const traf of fragment.filter(box=>box.type==='traf')){
    const track=children(traf),tfhd=one(track,'tfhd');if(!tfhd||tfhd.end-tfhd.start<8)return false;
    const id=view.getUint32(tfhd.start+4);
    for(const run of track.filter(box=>box.type==='trun')){
     if(run.end-run.start<8)return false;
     const flags=view.getUint32(run.start)&0xffffff,count=view.getUint32(run.start+4);
     const optional=((flags&1)?4:0)+((flags&4)?4:0),perSample=[0x100,0x200,0x400,0x800].filter(flag=>flags&flag).length*4;
     if(run.end-run.start<8+optional+count*perSample)return false;
     if(count>0&&defaults.includes(id)&&tracks.some(t=>t.id===id))fragmentedVideo=true;
    }
   }
  }
  return fragmentedVideo;
 }catch{return false;}
}
