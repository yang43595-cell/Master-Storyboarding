// Deterministic control-flow tests with DOM/GLTF substitutes, not browser/pixel QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../visual_asset_ui/previs.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace('export function openPrevis', 'function openPrevis');
const deferred = () => {let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve};};
const flush = () => new Promise(resolve => setImmediate(resolve));
const matrix = Array.from({length: 16}, (_, i) => +(i % 5 === 0));
const manifest = {duration_seconds: 1, fps: 24, resolution: [160, 90],
  frames: [0, 1].map(time => ({time, matrix_world: matrix, projection_matrix: matrix}))};
const detail = (id = 'one') => ({status: 'pending', manifest,
  glb_url: `/previs-media/A/${id}/preview.glb?manifest_sha256=fixed-identity`});
function element() {
  const node = new EventTarget();
  Object.assign(node, {value: '', hidden: false, open: true, options: [], dataset: {},
    setAttribute() {}, replaceChildren(...values) {this.options = values;},
    add(value) {this.options.push(value);}, remove() {this.removed = true;},
    showModal() {this.open = true;}, close() {this.open = false;},
    querySelector() {return element();}, querySelectorAll() {return [];}});
  return node;
}
function harness({fetch = async () => {throw Error('unexpected fetch');}, parse = async () => ({scene: {traverse() {}}})} = {}) {
  const context = vm.createContext({fetch, AbortController, URL, URLSearchParams, EventTarget,
    location: {href: 'http://127.0.0.1/', origin: 'http://127.0.0.1'},
    window: new EventTarget(), document: {activeElement: null, createElement: element, body: {append() {}}},
    Option: function(label, value) {this.label = label; this.value = value;},
    GLTFLoader: class {parseAsync(...args) {return parse(...args);}}});
  vm.runInContext(source + '\nthis.Session = PrevisSession; this.openPreview = openPrevis; this.active = () => activePreview;', context);
  Object.assign(context.Session.prototype, {
    clearViewer() {this.model = null;}, setControls(value) {this.controlsEnabled = value;},
    setStatus(value) {this.status = value;}, showMessage(title, message) {this.message = {title, message};},
    mountViewer(gltf, data) {this.mounted = gltf; this.model = gltf.scene; this.manifest = data;}, seek() {}});
  const session = id => {
    const s = new context.Session(id, id);
    s.selector = element(); s.dialog = element(); s.find = () => element();
    return s;
  };
  return {context, session};
}

test('pending detail becomes ready only after the validated model response and parse', async () => {
  const buffer = deferred(); const parsed = deferred(); let url;
  const {session} = harness({fetch: async requested => {
    url = String(requested); return {ok: true, arrayBuffer: () => buffer.promise};
  }, parse: () => parsed.promise});
  const s = session('A'); s.request = async () => detail();
  const loading = s.loadShot('one'); await flush();
  assert.equal(s.status, 'pending'); assert.equal(s.controlsEnabled, false);
  assert.match(url, /manifest_sha256=fixed-identity/);
  buffer.resolve(new ArrayBuffer(2)); await flush();
  assert.equal(s.status, 'pending'); assert.equal(s.mounted, undefined);
  parsed.resolve({scene: {traverse() {}}}); await loading;
  assert.equal(s.status, 'ready'); assert.equal(s.controlsEnabled, true);
});

test('a newer shot selection supersedes an older detail even if abort is ignored', async () => {
  const old = deferred(); const fetches = [];
  const {session} = harness({fetch: async url => {
    fetches.push(String(url)); return {ok: true, arrayBuffer: async () => new ArrayBuffer(1)};
  }});
  const s = session('A'); s.request = id => id === 'old' ? old.promise : Promise.resolve(detail(id));
  const previous = s.loadShot('old'); const current = s.loadShot('new');
  await current; const mounted = s.mounted; old.resolve(detail('old')); await previous;
  assert.equal(fetches.length, 1); assert.match(fetches[0], /\/new\//);
  assert.equal(s.mounted, mounted); assert.equal(s.status, 'ready');
});

test('an old GLTF parse result is disposed and never mounted after a newer selection', async () => {
  const oldParse = deferred(); let parses = 0, disposed = 0;
  const {session} = harness({fetch: async () => ({ok: true, arrayBuffer: async () => new ArrayBuffer(1)}),
    parse: () => ++parses === 1 ? oldParse.promise : Promise.resolve({scene: {traverse() {}}})});
  const s = session('A'); s.request = async id => detail(id);
  const previous = s.loadShot('old'); await flush();
  await s.loadShot('new'); const mounted = s.mounted;
  oldParse.resolve({scene: {traverse(callback) {callback({geometry: {dispose() {disposed++;}}});}}});
  await previous; assert.equal(disposed, 1); assert.equal(s.mounted, mounted);
});

test('repeated refresh ignores an older list and defaults to pending entries over errors', async () => {
  const old = deferred(); const {session} = harness(); const s = session('A'); let requests = 0;
  s.request = () => ++requests === 1 ? old.promise : Promise.resolve({previews: [
    {id: 'bad', status: 'error'}, {id: 'current', status: 'pending'}]});
  const selected = []; s.loadShot = async id => selected.push(id);
  const previous = s.loadList(); await s.loadList();
  old.resolve({previews: [{id: 'outdated', status: 'pending'}]}); await previous;
  assert.deepEqual(selected, ['current']); assert.equal(s.selector.value, 'current');
});

test('project change closes the session; delayed model bytes cannot enter the next project', async () => {
  const oldBuffer = deferred(); let parses = 0;
  const {context} = harness({fetch: async () => ({ok: true, arrayBuffer: () => oldBuffer.promise}),
    parse: async () => {parses++; return {scene: {traverse() {}}};}});
  context.Session.prototype.request = async id => id ? detail() : {previews: [{id: 'one', status: 'pending'}]};
  context.openPreview({projectId: 'A'}); const previous = context.active(); await flush();
  const changed = new Event('previs-project-change'); changed.detail = {projectId: 'B'};
  context.window.dispatchEvent(changed);
  assert.equal(previous.closed, true); assert.equal(previous.dialog.removed, true);
  context.Session.prototype.request = async () => ({previews: []});
  context.openPreview({projectId: 'B'}); const current = context.active();
  oldBuffer.resolve(new ArrayBuffer(1)); await flush();
  assert.equal(parses, 0); assert.equal(current.projectId, 'B'); assert.equal(current.mounted, undefined);
  current.close();
});

test('close during a pending detail cannot restore status or mount a model', async () => {
  const response = deferred(); const {session} = harness(); const s = session('A');
  s.request = () => response.promise; const loading = s.loadShot('one');
  s.close(); response.resolve(detail()); await loading;
  assert.equal(s.closed, true); assert.equal(s.mounted, undefined); assert.notEqual(s.status, 'ready');
});
