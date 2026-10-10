// Explicit-target screenshot. Navigation invalidates the result before saving.
'use strict';
const fs = require('node:fs');
const {connectTarget} = require('./rhtv_target');
async function main() {
  const out = process.argv[2] || 'rhtv_shot.jpg';
  const target = await connectTarget();
  try {
    await target.verify();
    const shot = await target.call('Page.captureScreenshot', {format: 'jpeg', quality: 55, fromSurface: true});
    await target.verify();
    if (!shot.data) throw new Error('截图无数据');
    fs.writeFileSync(out, Buffer.from(shot.data, 'base64'));
    console.log('截图已保存:', out, `(${Math.round(fs.statSync(out).size / 1024)} KB)`);
  } finally { target.close(); }
}
main().catch(e => { console.error('ERR:', e.message); process.exitCode = 1; });
