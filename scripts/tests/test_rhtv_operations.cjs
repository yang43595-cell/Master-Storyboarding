'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {operationRequest, operationExpression} = require('../rhtv_target');
const URL_VALUE = 'https://fixture.invalid/canvas/exact';
function page(options = {}) {
  const effects = [];
  class HTMLElement { click() { effects.push('click'); } }
  class HTMLInputElement extends HTMLElement {
    get value() { return this._value || ''; }
    set value(v) { this._value = v; effects.push('set:'+v); }
  }
  class HTMLTextAreaElement extends HTMLInputElement {}
  class HTMLSelectElement extends HTMLInputElement {}
  const el = new HTMLInputElement();
  Object.assign(el, {localName:'input',type:'text',textContent:'native text',isConnected:true,
    getAttribute: name => name==='data-media-id' ? 'media-17' : null,
    dispatchEvent: event => { effects.push(event.type); if (options.onEvent) options.onEvent(event, context); }});
  let current = options.wait ? [] : [el];
  const document = {querySelectorAll: () => current, querySelector: () => current[0]};
  const context = {URL, Date, Promise, Object, Array, JSON, Error, Math,
    location:{href:URL_VALUE}, document, HTMLElement,HTMLInputElement,HTMLTextAreaElement,HTMLSelectElement,
    Event:class {constructor(type){this.type=type;}},
    setTimeout: callback => { current=[el]; if(options.onWait) options.onWait(context); callback(); }};
  return {context,effects,el,setMatches: x=>{current=x;}};
}
async function execute(fixture, request) {
  return vm.runInNewContext(operationExpression(operationRequest(request), URL_VALUE), fixture.context);
}
test('structured read supports async wait and preserves media identity without writes', async()=>{
  const f=page({wait:true});const r=await execute(f,{operation:'read',selector:'#draft',wait_ms:100,attributes:['data-media-id']});
  assert.equal(r.values[0].attributes['data-media-id'],'media-17'); assert.deepEqual(f.effects,[]);
});
test('navigation while waiting refuses click before any effect', async()=>{
  const f=page({wait:true,onWait:c=>{c.location.href='https://fixture.invalid/other';}});
  await assert.rejects(execute(f,{operation:'click',selector:'#draft',wait_ms:100}),/导航/);assert.deepEqual(f.effects,[]);
});
test('document replacement while waiting refuses set-value before effect', async()=>{
  const f=page({wait:true,onWait:c=>{c.document={querySelectorAll:()=>[]};}});
  await assert.rejects(execute(f,{operation:'set-value',selector:'#draft',value:'new',wait_ms:100}),/导航/);assert.deepEqual(f.effects,[]);
});
test('same target receives exactly one synchronous click',async()=>{
  const f=page();await execute(f,{operation:'click',selector:'#draft'});assert.deepEqual(f.effects,['click']);
});
test('set-value only invokes native setter and guarded input/change',async()=>{
  const f=page();await execute(f,{operation:'set-value',selector:'#draft',value:'new'});assert.deepEqual(f.effects,['set:new','input','change']);
});
test('input-triggered navigation blocks later change event and does not undo prior effect',async()=>{
  const f=page({onEvent:(e,c)=>{if(e.type==='input')c.location.href='https://fixture.invalid/other';}});
  await assert.rejects(execute(f,{operation:'set-value',selector:'#draft',value:'new'}),/导航/);
  assert.deepEqual(f.effects,['set:new','input']);
});
test('ambiguous selector fails closed',async()=>{
  const f=page();f.setMatches([f.el,f.el]);await assert.rejects(execute(f,{operation:'click',selector:'.draft'}),/唯一/);assert.deepEqual(f.effects,[]);
});
test('arbitrary code, async source, unknown fields and invalid operations are rejected',()=>{
  for(const value of ['async ()=>{}',{operation:'evaluate',selector:'body'},{operation:'read',selector:'body',expression:'sideEffect()'},
    {operation:'click',selector:'body',all:true},{operation:'read',selector:'body',wait_ms:999999}]) assert.throws(()=>operationRequest(value));
});
test('file inputs cannot be changed',async()=>{
  const f=page();f.el.type='file';await assert.rejects(execute(f,{operation:'set-value',selector:'#file',value:'/private'}),/文件/);assert.deepEqual(f.effects,[]);
});
test('legacy arbitrary async stdin is rejected before opening a target',()=>{
  const {spawnSync}=require('node:child_process');const path=require('node:path');
  const p=spawnSync(process.execPath,[path.join(__dirname,'../rhtv_cdp.js')],{
    input:'(async()=>{await pause();performOneWrite()})()',encoding:'utf8',
    env:{...process.env,RHTV_DEBUG_PORT:'1',RHTV_CANVAS_URL:URL_VALUE}});
  assert.equal(p.status,1);assert.match(p.stderr,/旧任意 JS stdin 已停止支持/);assert.doesNotMatch(p.stderr,/fetch failed/);
});
