// Smoke test + screenshots for web/index.html.
// Usage: node web/tools/smoke.cjs [baseUrl] [outDir]
//   baseUrl defaults to http://127.0.0.1:8000/?seed=1 (uvicorn api.main:app); use …/index.html?mock=1&seed=1 for fixtures
// Needs Playwright (resolved from PLAYWRIGHT_PATH or the usual global locations).
const path = require('path');
const os = require('os');
const fs = require('fs');
const assert = require('node:assert/strict');
function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_PATH, 'playwright', path.join(os.homedir(), '.claude/skills/gstack/node_modules/playwright')].filter(Boolean);
  for (const t of tries) { try { return require(t); } catch (e) {} }
  throw new Error('Playwright not found; set PLAYWRIGHT_PATH');
}
const { chromium } = loadPlaywright();
const URL = process.argv[2] || 'http://127.0.0.1:8000/?seed=1';
const SUBMIT = process.env.SUBMIT === '1' || URL.includes('mock=1'); // never write test rows into the real leaderboard by default
const OUT = process.argv[3] || path.join(__dirname, '..', '..', 'docs', 'screenshots');
const SHOTS = process.env.SHOTS === '1';

async function checkPanels(page, tag) {
  await page.waitForSelector('#vGrade .grade-move', { timeout: 20000 });
  await page.waitForSelector('#vStress .stress-row', { timeout: 20000 });
  await page.waitForSelector('#vPromise .promise-hero .big', { timeout: 20000 });
  const expected = await page.evaluate(async () => {
    if (SOURCE === 'mock') {
      const [grade, stress, promise] = await Promise.all([fx('grade'), fx('stress'), fx('promise')]);
      return { grade, stress, promise: promise.price };
    }
    const catalog = await api('GET', '/api/promise/catalog');
    const promises = catalog.filter((row) => row.type === 'min_approval').flatMap((row) => row.promises);
    const [grade, stress, promise] = await Promise.all([
      api('POST', '/api/grade', S.plan), api('POST', '/api/stress', S.plan),
      api('POST', '/api/promise', { plan: S.plan, promises }),
    ]);
    return { grade, stress, promise };
  });
  const decimal = (n) => n.toFixed(2).replace('.', ',');
  assert.equal(await page.locator('#vGrade .grade-move').count(), expected.grade.moves.length);
  assert.equal(await page.locator('#vGrade .grade-accuracy .mid').textContent(), decimal(expected.grade.score));
  const symbols = await page.locator('#vGrade .grade-move [role="img"]').allTextContents();
  assert.deepEqual(symbols, expected.grade.moves.filter((move) => move.symbol).map((move) => move.symbol));
  assert.equal(await page.locator('#vStress .stress-row').count(), expected.stress.scenarios.length);
  for (const [index, scenario] of expected.stress.scenarios.entries()) {
    const row = await page.locator('#vStress .stress-row').nth(index).innerText();
    assert.ok(row.includes(scenario.title) && row.includes(decimal(scenario.score)), 'crisis title and engine score');
  }
  assert.equal(await page.locator('#vPromise .promise-hero .big').textContent(), decimal(expected.promise.price));
  assert.ok((await page.locator('#vPromise .notice').innerText()).includes(decimal(expected.promise.your_plan.score)));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  assert.ok(overflow <= 1, `verdict horizontal overflow: ${overflow}px`);
  console.log(tag, 'panels: grade', decimal(expected.grade.score), 'accuracy', decimal(expected.grade.accuracy),
    '| worst crisis', decimal(expected.stress.worst_score), '| promise price', decimal(expected.promise.price));
  return expected;
}

(async () => {
  if (SHOTS) fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const errors = [];
  const run = async (viewport, tag) => {
    const page = await browser.newPage({ viewport, deviceScaleFactor: tag === 'phone' ? 2 : 1.5 });
    page.on('pageerror', (e) => errors.push(`[${tag}] pageerror: ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`[${tag}] console: ${m.text()}`); });
    await page.goto(URL, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-mid]', { timeout: 20000 });
    const cards = await page.$$eval('[data-mid]', (els) => els.length);
    console.log(tag, 'measure cards:', cards);
    await page.click('#btnExample');
    await page.waitForFunction(() => !document.querySelector('#btnSubmit').disabled, null, { timeout: 15000 });
    console.log(tag, 'rail msg:', (await page.textContent('#rail [aria-live]')).trim());
    if (SHOTS && tag === 'desktop') { await page.waitForTimeout(2800); await page.screenshot({ path: path.join(OUT, 'cabinet.png') }); }
    await page.click('#btnSubmit');
    await page.waitForSelector('#vScore .big', { timeout: 20000 });
    await page.waitForSelector('#vRegret [data-regret]', { timeout: 20000 });
    await page.waitForSelector('#vCouncil .council-card', { timeout: 120000 });
    await page.waitForSelector('#vAI #applyRec, #vAI li', { timeout: 120000 });
    await checkPanels(page, tag);
    await page.waitForTimeout(900);
    console.log(tag, 'score:', (await page.textContent('#vScore .big')).trim(), '| regret:', (await page.textContent('#vRegret [data-regret]')).trim());
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'verdict.png'), fullPage: true });
    await page.click('#crisisCta');
    await page.waitForSelector('#modal [data-rm]', { timeout: 15000 });
    console.log(tag, 'crisis:', (await page.textContent('#modal .opp h3')).trim());
    await page.click('#modal [data-rm="M12"]');
    await page.click('#modal [data-add="M4"]');
    await page.selectOption('#modal [data-dist]', 'Сарыарка');
    await page.waitForFunction(() => { const b = document.querySelector('#crGo'); return b && !b.disabled; }, null, { timeout: 15000 });
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'crisis.png') });
    await page.click('#crGo');
    await page.waitForSelector('#crVerdict', { timeout: 20000 });
    console.log(tag, 'crisis comment:', (await page.textContent('#modal [data-comment]')).trim().slice(0, 200));
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'crisis-result.png') });
    await page.click('#crPaper');
    await page.waitForSelector('#newspaper', { timeout: 120000 });
    await page.waitForTimeout(700);
    console.log(tag, 'headline:', (await page.textContent('#newspaper .hl')).trim());
    if (SHOTS) {
      await page.addStyleTag({ content: 'header{display:none!important}' });
      const el = await page.$('#newspaper');
      await el.screenshot({ path: path.join(OUT, tag === 'desktop' ? 'hero-newspaper.png' : 'phone-newspaper.png') });
      await page.addStyleTag({ content: 'header{display:block!important}' });
    }
    await page.click('[data-go="board"]');
    await page.waitForSelector('#lbTable table, #lbTable .text-center', { timeout: 15000 });
    if (SUBMIT) {
      await page.fill('#team', 'Smoke ' + tag);
      await page.click('#lbForm button');
      await page.waitForFunction(() => document.querySelector('#lbMsg').textContent.includes('Готово'), null, { timeout: 15000 });
    }
    await page.click('#btnReveal');
    await page.waitForSelector('#lbTable tr[data-pin="opt"]', { timeout: 15000 });
    await page.waitForTimeout(400);
    console.log(tag, 'leaderboard rows:', await page.$$eval('#lbTable tbody tr', (r) => r.length));
    if (SHOTS && tag === 'desktop') await page.screenshot({ path: path.join(OUT, 'leaderboard.png') });
    // horizontal overflow check
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    console.log(tag, 'horizontal overflow px:', overflow);
    // Exercise both panel callbacks and remounts after applying a different plan.
    if (tag === 'desktop') {
      await page.click('[data-go="verdict"]');
      let panels = await checkPanels(page, tag + ' after crisis');
      for (const kind of ['promise', 'stress']) {
        const plan = kind === 'promise' ? panels.promise.best.plan : panels.stress.crisis_proof_plan.plan;
        await page.click(kind === 'promise' ? '#vPromise [data-apply]' : '#vStress [data-stress-apply]');
        await page.waitForSelector('#tab-cabinet:not([hidden])');
        assert.deepEqual(await page.evaluate(() => planOf()), plan, kind + ' loads cabinet plan');
        await page.waitForFunction(() => !document.querySelector('#btnSubmit').disabled);
        await page.click('#btnSubmit');
        panels = await checkPanels(page, tag + ' applied ' + kind);
      }
    }
    await page.close();
  };
  await run({ width: 1440, height: 900 }, 'desktop');
  await run({ width: 390, height: 844 }, 'phone');
  await browser.close();
  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no browser errors');
  assert.equal(errors.length, 0, 'browser errors');
})().catch((e) => { console.error('FAIL', e); process.exit(1); });
