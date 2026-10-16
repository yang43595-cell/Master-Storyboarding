// RHTV 无限画布 — 专用配置浏览器 + 底层 CDP 启动器
// 用法: 见 scripts/rhtv_canvas.cmd（或 bash: NODE_PATH=... node scripts/rhtv_canvas.js）
// 依赖: 全局 Playwright（NODE_PATH 指向全局 npm node_modules），驱动本机 Edge，无需另下 Chromium。
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

// ---------- 可配置项（环境变量可覆盖） ----------
const {normalizedUrl} = require('./rhtv_target');
if (!process.env.RHTV_CANVAS_URL) throw new Error('请明确设置 RHTV_CANVAS_URL；没有默认生产画布');
const CANVAS_URL = normalizedUrl(process.env.RHTV_CANVAS_URL);
// 专用配置（独立浏览器资料夹，登录态在此延续）。放在仓库外，避免被 git 追踪。
const PROFILE_DIR =
  process.env.RHTV_PROFILE_DIR ||
  path.join(os.homedir(), '.claude', 'rhtv-profile');
const DEBUG_PORT = process.env.RHTV_DEBUG_PORT || '9222';
const STATUS_MS = 15000; // 状态刷新间隔

fs.mkdirSync(PROFILE_DIR, { recursive: true });

console.log('[rhtv] Edge 启动中');
console.log('[rhtv] canvas URL   :', CANVAS_URL);
console.log('[rhtv] 专用配置目录  :', PROFILE_DIR);
console.log('[rhtv] CDP 底层端口  :', DEBUG_PORT);

async function main() {
  const { chromium } = require('playwright');
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: 'msedge',
    headless: false, // 必须有窗口，方便你登录
    viewport: null,  // 用浏览器自然窗口大小
    args: [`--remote-debugging-port=${DEBUG_PORT}`],
  });

  const page = context.pages()[0] || (await context.newPage());
  await page.goto(CANVAS_URL, { waitUntil: 'domcontentloaded' });

  let lastUrl = '';
  async function snap() {
    const title = await page.title().catch(() => '?');
    const url = page.url();
    const changed = url !== lastUrl;
    lastUrl = url;
    // 登录后 URL 可能不变（SPA 弹登录框），所以附上 cookie 判定
    const cookieState = await context.cookies().then((cs) =>
      cs.length ? 'cookies=' + cs.length : 'cookies=none'
    );
    console.log(
      `[rhtv] ${changed ? '⇢ 地址已变 ' : '状态      '} title=${title} url=${url} ${cookieState}`
    );
  }

  await snap();

  // 周期汇报，直到手动停止
  const timer = setInterval(snap, STATUS_MS);

  const shutdown = async () => {
    clearInterval(timer);
    try { await context.close(); } catch {}
    console.log('[rhtv] 已关闭');
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  console.log('[rhtv] 就绪。请在打开的 Edge 窗口里登录；看到画布后告诉我，或直接说“下一步”。');
  console.log(`[rhtv] 底层调试口: http://127.0.0.1:${DEBUG_PORT}`);

  await new Promise(() => {}); // 保持进程存活（浏览器不关）
}

main().catch((e) => {
  console.error('[rhtv] 启动失败:', e.message);
  console.error(e.stack || '');
  process.exit(1);
});
