'use strict';
// One explicitly selected page per invocation. Never infer a canvas from tab order.
function normalizedUrl(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('目标必须是不含凭据的 HTTP(S) URL');
  }
  return url.href;
}
async function connectTarget() {
  const id = (process.env.RHTV_TARGET_ID || '').trim();
  const configured = (process.env.RHTV_CANVAS_URL || '').trim();
  if (!id && !configured) throw new Error('须明确设置 RHTV_TARGET_ID 或 RHTV_CANVAS_URL；不按标签顺序猜目标');
  const url = configured ? normalizedUrl(configured) : null;
  const port = process.env.RHTV_DEBUG_PORT || '9222';
  if (!/^\d+$/.test(port)) throw new Error('RHTV_DEBUG_PORT 必须为端口号');
  const res = await fetch(`http://127.0.0.1:${port}/json`);
  if (!res.ok) throw new Error(`读取 CDP 目标失败: ${res.status}`);
  const targets = await res.json();
  const matches = targets.filter(t => t.type === 'page' && (!id || t.id === id)
    && (!url || (() => { try { return normalizedUrl(t.url) === url; } catch { return false; } })()));
  if (matches.length !== 1) throw new Error(`明确目标匹配数为 ${matches.length}；已停止`);
  const page = matches[0];
  const expectedUrl = url || normalizedUrl(page.url);
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, {once: true});
    ws.addEventListener('error', () => reject(new Error('CDP WebSocket 连接失败')), {once: true});
  });
  let nextId = 0;
  async function call(method, params = {}) {
    const requestId = ++nextId;
    return new Promise((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); ws.removeEventListener('message', onMessage); ws.removeEventListener('close', onClose); };
      const onClose = () => { cleanup(); reject(new Error('CDP 连接在响应前关闭')); };
      const timer = setTimeout(() => { cleanup(); reject(new Error(`CDP ${method} 超时`)); }, 25000);
      const onMessage = event => {
        let data; try { data = JSON.parse(event.data); } catch { return; }
        if (data.id !== requestId) return;
        cleanup();
        if (data.error) reject(new Error(`CDP ${method}: ${data.error.message}`));
        else resolve(data.result);
      };
      ws.addEventListener('message', onMessage); ws.addEventListener('close', onClose);
      ws.send(JSON.stringify({id: requestId, method, params}));
    });
  }
  async function verify() {
    const result = await call('Target.getTargetInfo', {targetId: page.id});
    const current = result.targetInfo;
    if (!current || current.targetId !== page.id || current.type !== 'page'
        || normalizedUrl(current.url) !== expectedUrl) {
      throw new Error('目标身份或地址已改变；已停止，请重新确认目标');
    }
  }
  return {call, verify, close: () => ws.close(), expectedUrl, targetId: page.id};
}
module.exports = {connectTarget, normalizedUrl};

function operationRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('须提供结构化操作 JSON；不执行任意 JS');
  const allowed = new Set(['operation', 'selector', 'wait_ms', 'value', 'attributes', 'all']);
  if (Object.keys(value).some(k => !allowed.has(k))) throw new Error('存在不支持的操作字段');
  if (!['read', 'read-rich-editor', 'click', 'set-value'].includes(value.operation)) throw new Error('operation 仅支持 read、read-rich-editor、click、set-value');
  if (typeof value.selector !== 'string' || !value.selector.trim()) throw new Error('须给准确 CSS selector');
  if (value.wait_ms !== undefined && (!Number.isInteger(value.wait_ms) || value.wait_ms < 0 || value.wait_ms > 10000)) throw new Error('wait_ms 须为 0..10000 整数');
  if (value.operation === 'set-value' && typeof value.value !== 'string') throw new Error('set-value 须给字符串 value');
  if (value.operation !== 'set-value' && value.value !== undefined) throw new Error('仅 set-value 接受 value');
  if (value.all !== undefined && (typeof value.all !== 'boolean' || value.operation !== 'read')) throw new Error('all 仅用于 read');
  if (value.attributes !== undefined && (value.operation !== 'read' || !Array.isArray(value.attributes)
      || value.attributes.some(x => typeof x !== 'string' || !x || x.length > 100))) throw new Error('attributes 须为 read 的属性名数组');
  return value;
}
function operationExpression(request, expectedUrl) {
  // User input is data, never executable source. Async waiting and final effect
  // are separated: every effect is preceded by a same-turn target/DOM check.
  return `(async () => {
    const request = ${JSON.stringify(request)}, expectedUrl = ${JSON.stringify(expectedUrl)};
    const startingDocument = document;
    function check() {
      if (new URL(location.href).href !== expectedUrl || document !== startingDocument) throw new Error('目标页面已导航，停止操作');
    }
    function nodes() { check(); return Array.from(document.querySelectorAll(request.selector)); }
    const deadline = Date.now() + (request.wait_ms || 0);
    let matches = nodes();
    while (!matches.length && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, Math.min(50, deadline - Date.now())));
      matches = nodes();
    }
    check();
    matches = nodes();
    if (!matches.length || (request.all !== true && matches.length !== 1) || matches.length > 100) throw new Error('selector 未匹配唯一目标，或读取范围过大');
    if (request.operation === 'read-rich-editor') {
      const value = (${require('./rhtv_rich_editor').readRichEditor.toString()})(matches[0]);
      check(); return {operation: 'read-rich-editor', value};
    }
    if (request.operation === 'read') {
      const values = matches.map(el => ({tag: el.localName, text: el.textContent,
        value: ['input','textarea','select'].includes(el.localName) ? el.value : null,
        attributes: Object.fromEntries((request.attributes || []).map(name => [name, el.getAttribute(name)]))}));
      if (JSON.stringify(values).length > 100000) throw new Error('读取范围过大，请缩小 selector');
      check(); return {operation: 'read', values};
    }
    const el = matches[0];
    check();
    if (request.operation === 'click') {
      HTMLElement.prototype.click.call(el);
      return {operation: 'click', effect_submitted: true};
    }
    const classes = {input: HTMLInputElement, textarea: HTMLTextAreaElement, select: HTMLSelectElement};
    const cls = classes[el.localName];
    if (!cls || (el.localName === 'input' && el.type === 'file')) throw new Error('set-value 仅支持普通 input、textarea、select，不支持文件或任意编辑器');
    const setter = Object.getOwnPropertyDescriptor(cls.prototype, 'value').set;
    check(); setter.call(el, request.value);
    for (const type of ['input','change']) {
      check();
      if (!el.isConnected || document.querySelectorAll(request.selector).length !== 1 || document.querySelector(request.selector) !== el) throw new Error('控件已被替换，停止后续事件');
      el.dispatchEvent(new Event(type, {bubbles: true}));
    }
    return {operation: 'set-value', effect_submitted: true};
  })()`;
}
module.exports.operationRequest = operationRequest;
module.exports.operationExpression = operationExpression;
