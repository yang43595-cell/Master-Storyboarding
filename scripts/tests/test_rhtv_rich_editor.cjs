'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readRichEditor} = require('../rhtv_rich_editor');
const {operationRequest, operationExpression} = require('../rhtv_target');

// Minimal DOM fixture derived from observed attribute names only. Text, UUIDs,
// thumbnail URLs and nesting variants are synthetic, NOT a captured outerHTML
// or proof of online end-to-end binding. P/BR are generic negative/format cases.
function element(tag, attrs = {}, children = []) {
  const node = {nodeType: 1, localName: tag, attrs, childNodes: children,
    get textContent() { return this.childNodes.map(n=>n.nodeType===3?n.data:n.textContent).join(''); },
    getAttribute(k) { return this.attrs[k] ?? null; },
    hasAttribute(k) { return Object.hasOwn(this.attrs, k); },
    querySelectorAll(selector) {
      const match = n => selector === '[data-reference-id]' ? n.hasAttribute('data-reference-id')
        : n.getAttribute('data-set-richtype') === 'richCustom';
      const result = [];
      function visit(n) { for (const c of n.childNodes || []) {
        if (c.nodeType === 1) { if (match(c)) result.push(c); visit(c); }
      }}
      visit(this); return result;
    },
    closest() { let n = this; while(n) { if(n.getAttribute('contenteditable') === 'false') return n; n = n.parent; } return null; }
  };
  for(const c of children) c.parent = node;
  return node;
}
const text = value => ({nodeType: 3, data: value});
function reference(n, nested = true) {
  const ref = element('span', {'data-reference-id': encodeURIComponent(JSON.stringify(['SYNTHETIC-NODE-'+n, 'image'])),
    'data-value': '{{ref '+n+'}}'}, [element('img', {src: 'blob:https://fixture.invalid/volatile-'+n})]);
  return element('span', {'data-set-richtype': 'richCustom', contenteditable: 'false', class: 'chat-tag-wrap'},
    [nested ? element('span', {style: 'display: inline-block;'}, [ref]) : ref, text('参考 '+n)]);
}
const root = children => element('div', {'data-set-richtype':'richWrap',contenteditable:'true',role:'textbox'},children);
const fixture = nested => root([text('第一段\n\n\uFEFF引用： '),
  ...Array.from({length:8},(_,i)=>[text('图片'+(i+1)+' '),reference(i+1,nested),text('； ')]).flat()]);

test('observed reference attributes yield ordered partial native_mentions, never media IDs',()=>{
  const result = readRichEditor(fixture(true));
  assert.equal(result.coverage.serialization,'collected');
  assert.equal(result.native_mentions.length,8);
  assert.deepEqual(result.native_mentions.map(x=>x.node_id),Array.from({length:8},(_,i)=>'SYNTHETIC-NODE-'+(i+1)));
  assert.equal(result.status,'unchecked'); assert.equal(result.production_ready,false);
  assert.equal(result.coverage.media_identity,'unchecked'); assert.equal(result.coverage.refresh,'unchecked');
  assert.ok(result.native_mentions.every(x=>!('media_id' in x) && !('sha256' in x)));
  assert.ok(!JSON.stringify(result).includes('blob:')); assert.ok(!('prompt' in result));
});
test('rendered reference decoration, blob changes and nesting do not alter serialized prose',()=>{
  const a=fixture(true), b=fixture(false);
  a.innerText='synthetic rendering with\nreference newline'; b.innerText='synthetic rendering without newline';
  const ref=b.querySelectorAll('[data-reference-id]')[0]; ref.childNodes[0].attrs.src='blob:https://fixture.invalid/changed';
  assert.deepEqual(readRichEditor(a),readRichEditor(b));
  assert.deepEqual(readRichEditor(a),readRichEditor(a));
});
test('real whitespace, FEFF, text edits, order, node and token changes remain visible',()=>{
  const original=readRichEditor(fixture(true)).tokens;
  for(const mutation of [r=>r.childNodes[0].data+=' ',r=>r.childNodes[0].data=r.childNodes[0].data.replace('\uFEFF',''),
    r=>r.childNodes[0].data+='新字',r=>{[r.childNodes[2],r.childNodes[5]]=[r.childNodes[5],r.childNodes[2]];},
    r=>r.querySelectorAll('[data-reference-id]')[0].attrs['data-reference-id']=encodeURIComponent('["NEW-NODE","image"]'),
    r=>r.querySelectorAll('[data-reference-id]')[0].attrs['data-value']='{{ref 9}}']) {
    const r=fixture(true);mutation(r);assert.notDeepEqual(readRichEditor(r).tokens,original);
  }
});
test('text splitting merges losslessly; explicit block and BR boundaries are not deleted',()=>{
  assert.deepEqual(readRichEditor(root([text('a '),text('\n\uFEFFb')])).tokens,
    readRichEditor(root([text('a \n\uFEFFb')])).tokens);
  assert.deepEqual(readRichEditor(root([element('p',{},[text('a'),element('br'),text('b')])])).tokens,
    [{kind:'open',tag:'p'},{kind:'text',value:'a'},{kind:'break'},{kind:'text',value:'b'},{kind:'close',tag:'p'}]);
});
test('reference display text edits are visible and unknown descendants are unchecked',()=>{
  const before=readRichEditor(fixture(true)).tokens;
  for(const extra of ['参考 2','额外正文','\n']) {
    const r=fixture(true);r.childNodes[2].childNodes.push(text(extra));
    assert.notDeepEqual(readRichEditor(r).tokens,before);
  }
  const r=fixture(true);r.childNodes[2].childNodes.push(element('button',{},[text('unknown')]));
  const result=readRichEditor(r);assert.equal(result.coverage.serialization,'unchecked');
  assert.deepEqual(result.tokens,[]);
});
test('malformed, conflicting, editable, unowned and unknown entities fail without partial output',()=>{
  for(const mutation of [r=>r.attrs.role='button',r=>r.childNodes.push(element('script')),
    r=>r.querySelectorAll('[data-reference-id]')[0].attrs['data-reference-id']='%broken',
    r=>r.querySelectorAll('[data-reference-id]')[0].attrs['data-value']='reference 1',
    r=>r.childNodes[2].attrs.contenteditable='true',
    r=>r.childNodes.push(element('span',{'data-reference-id':'["x","image"]'})),
    r=>r.childNodes[2].childNodes.push(element('span',{'data-reference-id':'["other","image"]'}))]) {
    const r=fixture(true);mutation(r);const result=readRichEditor(r);
    assert.equal(result.coverage.serialization,'unchecked'); assert.ok(result.errors.length);
    assert.deepEqual(result.tokens,[]);assert.deepEqual(result.native_mentions,[]);
  }
});
test('structured operation embeds only fixed reader and protects exact target',async()=>{
  const url='https://fixture.invalid/exact'; const doc={querySelectorAll:()=>[fixture(true)]};
  const request=operationRequest({operation:'read-rich-editor',selector:'#explicit-editor'});
  const expression=operationExpression(request,url);
  const result=await vm.runInNewContext(expression,{document:doc,location:{href:url},URL});
  assert.equal(result.value.native_mentions.length,8);
  await assert.rejects(vm.runInNewContext(expression,{document:doc,location:{href:url+'/changed'},URL}),/导航/);
  for(const extra of [{all:true},{value:'new'},{attributes:['data-value']},{expression:'write()'}])
    assert.throws(()=>operationRequest({...request,...extra}));
});
