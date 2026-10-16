// Local browser smoke: real project page, CSP, vendored Three, GLB and controls.
// /api fixture responses are synthetic; production state is never changed.
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const [url, fixtureDir] = process.argv.slice(2);
const browser = await chromium.launch({channel: 'msedge', headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1280, height: 960}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const project = {id: '0123456789abcdef', name: '白模预览 · 隔离测试', path: fixtureDir,
    rows: [], narrative: {asset_requirements: []}};
  await page.route('**/api/state', route => route.fulfill({json: {
    assets: [], references: [], jobs: [], projects: [project], token: 'test-only',
    product_categories: [], skill_profile: {}, management_ready: true, root: fixtureDir, vault: fixtureDir}}));
  await page.route('**/api/project', route => route.fulfill({json: project}));
  await page.route('**/api/previs?*', async route => {
    const id = new URL(route.request().url()).searchParams.get('preview_id');
    if (!id) return route.fulfill({json: {previews: ['landscape', 'portrait', 'stale', 'failed'].map(id => ({
      id, shot_id: id, revision: 'R01', duration_seconds: 3,
      status: id === 'stale' ? 'stale' : id === 'failed' ? 'error' : 'ready'}))}});
    if (id === 'stale' || id === 'failed') return route.fulfill({json: {status: id === 'stale' ? 'stale' : 'error',
      error: id === 'stale' ? '来源文件已变化，请重新导出预览' : '实际骨骼导出失败'}});
    const manifest = JSON.parse(await readFile(`${fixtureDir}/${id}/preview_manifest.json`, 'utf8'));
    return route.fulfill({json: {manifest, status: 'ready', error: '', glb_url: `/fixture/${id}/preview.glb`}});
  });
  await page.goto(url);
  await page.locator('#nav-project').click();
  await page.locator('#project-workspace').selectOption(project.id);
  await page.locator('#project-previs').click();
  const timeline = page.locator('[data-previs-timeline]');
  await page.waitForFunction(() => !document.querySelector('[data-previs-play]')?.disabled);
  if (await page.locator('[data-previs-paths]').isChecked()) throw Error('paths enabled by default');
  const start = await page.locator('#previs-dialog canvas').screenshot();
  await page.locator('[data-previs-jump="end"]').click();
  if (!(await page.locator('[data-previs-time]').innerText()).startsWith('3.00')) throw Error('end jump');
  const end = await page.locator('#previs-dialog canvas').screenshot();
  if (start.equals(end)) throw Error('motion did not change image');
  await page.locator('[data-previs-jump="start"]').click();
  await timeline.evaluate(el => {el.value = '15'; el.dispatchEvent(new Event('input', {bubbles: true}));});
  if (!(await page.locator('[data-previs-time]').innerText()).startsWith('1.50')) throw Error('seek');
  await page.locator('[data-previs-view="free"]').click();
  await page.locator('[data-previs-view="top"]').click();
  await page.locator('[data-previs-paths]').check();
  await page.locator('[data-previs-paths]').uncheck();
  await page.locator('[data-previs-view="director"]').click();
  await page.locator('#previs-dialog').screenshot({path: `${fixtureDir}/ui-landscape.png`});
  await page.locator('[data-previs-play]').click();
  await page.waitForFunction(() => document.querySelector('[data-previs-time]').textContent.startsWith('3.00') &&
    document.querySelector('[data-previs-play]').textContent === '播放', {timeout: 6000});
  await page.locator('[data-previs-jump="start"]').click();
  if (!(await page.locator('[data-previs-time]').innerText()).startsWith('0.00')) throw Error('reverse after end');
  await page.locator('[data-previs-shot]').selectOption('portrait');
  await page.waitForFunction(() => !document.querySelector('[data-previs-play]')?.disabled);
  await page.locator('#previs-dialog').screenshot({path: `${fixtureDir}/ui-portrait.png`});
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#previs-dialog').screenshot({path: `${fixtureDir}/ui-mobile.png`});
  const dialog = await page.locator('#previs-dialog').boundingBox();
  if (dialog.x < 0 || dialog.x + dialog.width > 391) throw Error('mobile dialog overflow');
  for (const id of ['stale', 'failed']) {
    await page.locator('[data-previs-shot]').selectOption(id);
    await page.waitForFunction(() => !document.querySelector('[data-previs-message]').hidden);
    if (!(await page.locator('[data-previs-play]').isDisabled())) throw Error('bad preview enabled playback');
  }
  await page.locator('[data-previs-close]').click();
  if (await page.locator('#previs-dialog').count()) throw Error('close retained dialog');
  if (errors.length) throw Error(JSON.stringify(errors));
  const result = {ok: true, interactions: ['project entry', 'start/end jump', 'reverse seek', 'play/end hold',
    'three views', 'optional paths', 'portrait', 'mobile', 'stale/error', 'cleanup'], pageErrors: errors};
  await writeFile(`${fixtureDir}/ui-result.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {await browser.close();}
