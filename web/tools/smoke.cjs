// Smoke test + screenshots for web/index.html.
// Usage: node web/tools/smoke.cjs [baseUrl] [outDir]
//   baseUrl defaults to http://127.0.0.1:8765/index.html?mock=1
// Needs Playwright (resolved from PLAYWRIGHT_PATH or the usual global locations).
const path = require('path');
const os = require('os');
const fs = require('fs');
function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_PATH, 'playwright', path.join(os.homedir(), '.claude/skills/gstack/node_modules/playwright')].filter(Boolean);
  for (const t of tries) { try { return require(t); } catch (e) {} }
  throw new Error('Playwright not found; set PLAYWRIGHT_PATH');
}
const { chromium } = loadPlaywright();
const URL = process.argv[2] || 'http://127.0.0.1:8765/index.html?mock=1';
const OUT = process.argv[3] || path.join(__dirname, '..', '..', 'docs', 'screenshots');
const SHOTS = process.env.SHOTS === '1';

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const errors = [];
  const run = async (viewport, tag) => {
    const page = await browser.newPage({ viewport, deviceScaleFactor: tag === 'phone' ? 2 : 1.5 });
    page.on('pageerror', (e) => errors.push(`[${tag}] pageerror: ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`[${tag}] console: ${m.text()}`); });
    await page.goto(URL, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-mid]', { timeout: 15000 });
    const cards = await page.$$eval('[data-mid]', (els) => els.length);
    console.log(tag, 'measure cards:', cards);
    await page.click('#btnExample');
    await page.waitForFunction(() => !document.querySelector('#btnSubmit').disabled, null, { timeout: 10000 });
    console.log(tag, 'rail msg:', (await page.textContent('#rail [aria-live]')).trim());
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'cabinet.png') });
    // overrun check: add nothing (full) — try clicking an unselected enabled card should toast
    await page.click('#btnSubmit');
    await page.waitForSelector('#chPareto', { timeout: 15000 });
    await page.waitForSelector('#council .rounded-full', { timeout: 15000 });
    await page.waitForTimeout(900);
    const aqls = await page.textContent('#tab-verdict .text-7xl');
    console.log(tag, 'verdict AQLS:', aqls.trim());
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'verdict.png'), fullPage: true });
    // crisis
    await page.click('#crisisCta');
    await page.waitForSelector('#modal [data-rm]', { timeout: 10000 });
    await page.click('#modal [data-rm="1"]');
    await page.click('#modal [data-add="m13"]');
    await page.selectOption('#modal [data-dist]', 'nura');
    await page.waitForFunction(() => { const b = document.querySelector('#crGo'); return b && !b.disabled; }, null, { timeout: 10000 });
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'crisis.png') });
    await page.click('#crGo');
    await page.waitForSelector('#crVerdict', { timeout: 10000 });
    console.log(tag, 'crisis comment:', (await page.textContent('#modal p')).trim().slice(0, 140));
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'crisis-result.png') });
    await page.click('#crPaper');
    await page.waitForSelector('#newspaper', { timeout: 15000 });
    await page.waitForTimeout(600);
    console.log(tag, 'headline:', (await page.textContent('#newspaper h2')).trim());
    if (SHOTS) {
      const el = await page.$('#newspaper');
      await el.screenshot({ path: path.join(OUT, tag === 'desktop' ? 'hero-newspaper.png' : 'phone-newspaper.png') });
    }
    await page.click('[data-go="board"]');
    await page.waitForSelector('#lbTable table', { timeout: 10000 });
    await page.fill('#team', 'Smoke ' + tag);
    await page.click('#lbForm button');
    await page.waitForFunction(() => document.querySelector('#lbMsg').textContent.includes('Готово'), null, { timeout: 10000 });
    const rows = await page.$$eval('#lbTable tbody tr', (r) => r.length);
    console.log(tag, 'leaderboard rows:', rows);
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'leaderboard.png') });
    // horizontal overflow check
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    console.log(tag, 'horizontal overflow px:', overflow);
    await page.close();
  };
  await run({ width: 1440, height: 900 }, 'desktop');
  await run({ width: 390, height: 844 }, 'phone');
  await browser.close();
  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no browser errors');
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
