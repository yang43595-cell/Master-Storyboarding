// Explicit-target structured CDP operations. Arbitrary JS is intentionally not
// accepted: an async expression cannot prove where its eventual writes land.
'use strict';
const {connectTarget, operationRequest, operationExpression} = require('./rhtv_target');
async function main() {
  if (process.argv[2] === '--about') {
    console.log('stdin JSON: {operation:"read|read-rich-editor|click|set-value",selector:"...",wait_ms:0,value:"..."}; 明确 RHTV_TARGET_ID 或 RHTV_CANVAS_URL');
    return;
  }
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  let parsed;
  try { parsed = JSON.parse(input); } catch { throw new Error('须提供结构化操作 JSON；旧任意 JS stdin 已停止支持'); }
  const request = operationRequest(parsed);
  const target = await connectTarget();
  let submitted = false;
  try {
    await target.verify();
    submitted = true;
    const result = await target.call('Runtime.evaluate', {
      expression: operationExpression(request, target.expectedUrl), returnByValue: true,
      awaitPromise: true, userGesture: !['read', 'read-rich-editor'].includes(request.operation),
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || '页面异常');
    await target.verify();
    console.log(JSON.stringify(result.result, null, 2));
  } catch (error) {
    if (submitted && !['read', 'read-rich-editor'].includes(request.operation)) error.message += '；操作已提交到页面，可能已有部分作用，勿自动重试';
    throw error;
  } finally { target.close(); }
}
main().catch(e => { console.error('ERR:', e.message); process.exitCode = 1; });
