// Screenshot design boards: node web/tools/shoot-variants.cjs <outDir> [v1 v2 …]
// Needs the backend (or any static server) serving web/ at http://127.0.0.1:8000/.
const path = require('path'), os = require('os'), fs = require('fs');
function pw() { for (const t of [process.env.PLAYWRIGHT_PATH, 'playwright', path.join(os.homedir(), '.claude/skills/gstack/node_modules/playwright')].filter(Boolean)) { try { return require(t); } catch (e) {} } throw new Error('Playwright not found'); }
const { chromium } = pw();
const OUT = process.argv[2] || 'shots';
const only = process.argv.slice(3);
const BASE = process.env.BASE || 'http://127.0.0.1:8000/variants/';
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const files = fs.readdirSync(path.join(__dirname, '..', 'variants')).filter((f) => /^v\d.*\.html$/.test(f) && (!only.length || only.some((o) => f.startsWith(o))));
  const browser = await chromium.launch();
  for (const f of files) {
    for (const [tag, vp] of [['desk', { width: 1440, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
      const page = await browser.newPage({ viewport: vp, deviceScaleFactor: 1 });
      const errs = [];
      page.on('pageerror', (e) => errs.push(e.message));
      page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
      await page.goto(BASE + f + '?shot', { waitUntil: 'networkidle' });
      await page.waitForSelector('[data-sec]', { timeout: 15000 }).catch(() => errs.push('no sections rendered'));
      await page.waitForTimeout(500);
      const over = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      await page.screenshot({ path: path.join(OUT, f.replace('.html', `-${tag}.png`)), fullPage: true });
      console.log(f, tag, 'overflow', over, errs.length ? 'ERR ' + errs.join(' | ') : 'ok');
      await page.close();
    }
  }
  await browser.close();
})();
