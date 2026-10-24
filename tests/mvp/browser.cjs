/* Offline browser against production HTTP routes. Never changes live policy. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const [base, root, out] = process.argv.slice(2);
const result = { status: 'running', started_at: new Date().toISOString(), model: 'fake',
  positive_route_mocks: false, contexts: [], external_requests_blocked: 0 };
let browser;
const sha = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const save = () => fs.writeFileSync(path.join(out, 'browser.json'), JSON.stringify(result, null, 2));
const watchdog = setTimeout(async () => {
  result.status = 'failed'; result.error = 'Browser acceptance deadline (40s)';
  if (browser) await browser.close().catch(() => {});
  result.finished_at = new Date().toISOString(); save(); process.exit(124);
}, 40000);
(async () => {
  try {
    const { chromium } = require(process.env.ALPHA_PLAYWRIGHT_MODULE || 'playwright-core');
    browser = await chromium.launch({ executablePath: process.env.ALPHA_CHROME_PATH,
      headless: true, chromiumSandbox: true });
    const companies = await (await fetch(base + '/api/v1/companies')).json();
    const ids = ['TRADE', 'TRANSPORT'].map(category => companies.items.find(p => p.category === category).company_id);
    for (const [index, company] of ids.entries()) {
      const context = await browser.newContext({ viewport: index ? { width: 390, height: 844 } : { width: 1440, height: 1000 } });
      // Positive API requests are untouched. Deny only external traffic.
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin === base) return route.continue();
        result.external_requests_blocked++; return route.abort();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(8000);
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      const posts = [];
      page.on('request', request => {
        if (request.method() === 'POST' && new URL(request.url()).origin === base && new URL(request.url()).pathname.startsWith('/api/'))
          posts.push({ path: new URL(request.url()).pathname, body: request.postDataJSON() });
      });
      await page.goto(base);
      await page.waitForFunction(() => document.querySelector('#company')?.options.length === 5);
      await page.selectOption('#company', company);
      for (const role of ['CFO', 'COO']) {
        await page.locator(`.role-option:has(input[value="${role}"])`).click();
        const runResponse = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/analysis-runs'));
        await page.getByRole('button', { name: 'Запустить анализ' }).click();
        const rr = await runResponse, run = await rr.json();
        assert.equal(rr.status(), 202); assert.equal(run.status, 'succeeded');
        assert.equal(run.company_id, company); assert.equal(run.role_id, role);
        const detail = page.waitForResponse(r => r.request().method() === 'GET' && /\/insights\/[^/]+$/.test(new URL(r.url()).pathname));
        await page.locator('.feed-card').first().click();
        const card = await (await detail).json();
        assert.equal(card.company_id, company); assert.equal(card.role_id, role);
        assert.equal(card.synthetic, false); assert.ok(card.sources.every(s => s.synthetic === false && s.locator));
        const refs = new Set(card.sources.map(s => s.source_id));
        assert.ok(card.blocks.every(b => b.evidence_refs.every(id => refs.has(id))));
        await page.getByText('тестовая модель, без живой LLM', { exact: false }).first().waitFor();
        await page.locator('#exposure-answer').fill('Synthetic browser check: связь неизвестна');
        const answerResponse = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/questions'));
        await page.getByRole('button', { name: 'Сохранить уточнение' }).click();
        const ar = await answerResponse; assert.equal(ar.status(), 200); assert.equal((await ar.json()).status, 'succeeded');
        await page.waitForFunction(() => !document.querySelector('#exposure-answer'));
        await page.locator('#insight-message').fill('Какие ограничения?');
        const messageResponse = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/messages'));
        await page.getByRole('button', { name: 'Отправить вопрос' }).click();
        const mr = await messageResponse; assert.equal(mr.status(), 200); assert.equal((await mr.json()).status, 'succeeded');
        await page.waitForFunction(() => document.querySelector('#insight-message')?.value === '');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(out, `browser-${index}-${role}.png`) });
        result.contexts.push({ company_id: company, role, viewport: index ? 'mobile' : 'desktop', status: 'passed',
          posts: posts.slice(-3).map(p => ({ path: p.path, role: p.body.role_id || null })) });
        save();
        await page.getByRole('button', { name: 'Закрыть карточку' }).click();
      }
      assert.equal(posts.length, 6); assert.deepEqual(errors, []);
      assert.deepEqual(posts.map(p => p.path.split('/').pop()), ['analysis-runs', 'questions', 'messages', 'analysis-runs', 'questions', 'messages']);
      await context.close();
    }
    result.status = 'passed';
  } catch (error) {
    result.status = 'failed'; result.error = String(error.stack || error); process.exitCode = 1;
  } finally {
    clearTimeout(watchdog);
    if (browser) await browser.close();
    result.finished_at = new Date().toISOString(); result.runner_sha256 = sha(__filename); save();
    console.log(JSON.stringify({ status: result.status, contexts: result.contexts.length, error: result.error }));
  }
})();
