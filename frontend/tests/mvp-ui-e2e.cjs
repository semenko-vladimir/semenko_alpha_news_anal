/* Actual local UI -> production API. No LIVE transport or positive-route mocks.
 * Run from any directory: node frontend/tests/mvp-ui-e2e.cjs
 * Fresh production build required: npm run build (frontend).
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { capturePosts } = require('./e2e-posts.cjs');
const root = path.resolve(__dirname, '../..');
const area = path.join(root, '.tmp/ui-tests');
const out = path.join(area, new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(out, { recursive: true });
const result = { started_at: new Date().toISOString(), mode: 'mvp_offline', model: 'fake',
  positive_route_mocks: false, cases: [], status: 'running', artifacts: path.relative(root, out) };
const children = [];
let browser;
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const inputs = ['data/raw/clients/df_Uid_data_hack.csv', 'data/raw/clients/df_Uid_payment_hack.csv',
  'data/raw/clients/df_Fid_Uid_link_hack.csv', 'data/processed/news/news.sqlite3',
  'data/processed/bank/index.sqlite3'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function persist() {
  result.runner_sha256 = hash(__filename);
  result.post_capture_sha256 = hash(path.join(__dirname, 'e2e-posts.cjs'));
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(area, 'e2e-result.json'), JSON.stringify(result, null, 2));
}
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer(); server.on('error', reject);
    server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)); });
  });
}
function start(command, args, label, cwd = root) {
  const log = fs.openSync(path.join(out, `${label}.log`), 'w');
  try {
    const child = spawn(command, args, { cwd, stdio: ['ignore', log, log], windowsHide: true });
    child.on('error', error => { child.startError = error; }); children.push(child); return child;
  } finally { fs.closeSync(log); }
}
async function ready(url, child) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.startError) throw child.startError;
    if (child.exitCode !== null) throw new Error(`Server exited ${child.exitCode}: ${url}`);
    try { const response = await fetch(url, { signal: AbortSignal.timeout(1500) }); if (response.ok) return; } catch {}
    await sleep(150);
  }
  throw new Error(`Startup timeout: ${url}`);
}
async function visible(locator) { await locator.first().waitFor({ state: 'visible', timeout: 15000 }); }
async function textVisible(page, text) { await visible(page.getByText(text, { exact: false })); }
async function noOverflow(page) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'horizontal overflow');
}
async function positive(base, viewport, companyIndex) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const network = capturePosts(page, base);
  const expected = [];
  try {
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#company')?.options.length === 5);
    const companies = await page.locator('#company option:not([disabled])').evaluateAll(options => options.map(o => o.value));
    assert.equal(companies.length, 4);
    await page.selectOption('#company', companies[companyIndex]);
    await textVisible(page, 'Пока нет инсайтов');
    await textVisible(page, 'единицы выручки требуют проверки');
    for (const role of ['CFO', 'COO']) {
      // Real user role/company selection and real browser fetch; no route interception.
      await page.locator(`.role-option:has(input[value="${role}"])`).click();
      const runResponse = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/analysis-runs'));
      await page.getByRole('button', { name: 'Запустить анализ' }).click();
      const response = await runResponse; assert.equal(response.status(), 202);
      const run = await response.json();
      assert.equal(run.company_id, companies[companyIndex]); assert.equal(run.role_id, role);
      assert.equal(run.execution_mode, 'mvp_offline'); assert.equal(run.status, 'succeeded');
      await visible(page.locator('.feed-card'));
      await textVisible(page, 'тестовая модель, без живой LLM');
      const detailResponse = page.waitForResponse(r => r.request().method() === 'GET' && /\/insights\/[^/]+$/.test(new URL(r.url()).pathname));
      await page.locator('.feed-card').first().click();
      const card = await (await detailResponse).json();
      assert.equal(card.company_id, companies[companyIndex]); assert.equal(card.role_id, role);
      assert.equal(card.synthetic, false); assert.ok(card.question?.question_id);
      assert.ok(card.sources.length && card.sources.every(s => s.synthetic === false && s.locator));
      const refs = new Set(card.sources.map(s => s.source_id));
      assert.ok(card.blocks.every(b => b.evidence_refs.every(ref => refs.has(ref))));
      await visible(page.locator('#exposure-answer'));
      await textVisible(page, 'Реальный локальный материал');
      await textVisible(page, 'Что пока неизвестно');
      await textVisible(page, 'Место в источнике:');
      await textVisible(page, 'Дата документа:');
      const bankLabels = { historical: 'Историческая позиция', found: 'Найдена в датированном документе', not_found: 'Подходящий документ не найден', not_checked: 'Не проверялась' };
      await textVisible(page, bankLabels[card.bank_position_status]);
      await page.locator('#exposure-answer').fill('Связь пока не подтверждена пользователем');
      const answerResponse = page.waitForResponse(r => r.url().endsWith('/questions') && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Сохранить уточнение' }).click();
      const answer = await answerResponse; assert.equal(answer.status(), 200); assert.equal((await answer.json()).status, 'succeeded');
      await textVisible(page, 'Ответ получен');
      assert.equal(await page.locator('#exposure-answer').count(), 0);
      await page.locator('#insight-message').fill('Какие ограничения есть у этого вывода?');
      const messageResponse = page.waitForResponse(r => r.url().endsWith('/messages') && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Отправить вопрос' }).click();
      const message = await messageResponse; assert.equal(message.status(), 200); assert.equal((await message.json()).status, 'succeeded');
      await visible(page.getByRole('region', { name: 'Ответ по инсайту' }));
      await page.waitForFunction(() => document.querySelector('#insight-message')?.value === '');
      await noOverflow(page);
      expected.push(
        { operation: 'run', path: `/api/v1/companies/${encodeURIComponent(card.company_id)}/analysis-runs`, role },
        { operation: 'answer', path: `/api/v1/insights/${encodeURIComponent(card.insight_id)}/questions`, questionId: card.question.question_id },
        { operation: 'message', path: `/api/v1/insights/${encodeURIComponent(card.insight_id)}/messages` },
      );
      network.assertOperations(expected);
      result.cases.push({ name: `${viewport.width}-${role}`, status: 'passed', company_id: card.company_id,
        bank_position_status: card.bank_position_status, sources: card.sources.length, synthetic: false,
        posts: network.businessPosts.length });
    }
    assert.equal(network.businessPosts.length, 6); assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(out, `${viewport.width}.png`), fullPage: true });
  } finally {
    // Persist diagnostics even when an assertion stops the positive scenario.
    (result.network ??= []).push({ viewport: viewport.width, ...network.snapshot() });
    await context.close();
  }
}
async function failureCases(base) {
  // Only these failure cases use explicit response mocks; not positive evidence.
  for (const scenario of ['empty', 'error', 'pending-unknown-run', 'unknown-dialogue']) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    try {
      if (scenario === 'empty' || scenario === 'error') {
        await page.route('**/api/v1/companies', route => route.fulfill({ status: scenario === 'error' ? 503 : 200,
          contentType: 'application/json', body: JSON.stringify(scenario === 'error' ? { error: { message: 'Сервис временно недоступен' } } : { items: [] }) }));
        await page.goto(base);
        await textVisible(page, scenario === 'empty' ? 'Нет доступных демонстрационных компаний' : 'Сервис временно недоступен');
        assert.ok(await page.getByRole('button', { name: 'Запустить анализ' }).isDisabled());
      } else {
        await page.goto(base); await page.waitForFunction(() => document.querySelector('#company')?.value);
        if (scenario === 'pending-unknown-run') {
          let release, count = 0;
          const held = new Promise(resolve => { release = resolve; });
          await page.route('**/analysis-runs', async route => { count++; await held; await route.fulfill({ json: { status: 'unknown_outcome' } }); });
          await page.getByRole('button', { name: 'Запустить анализ' }).click();
          await textVisible(page, 'Анализируем');
          assert.ok(await page.getByRole('button', { name: 'Анализируем' }).isDisabled());
          release(); await textVisible(page, 'результат остаётся неизвестным');
          assert.ok(await page.getByRole('button', { name: 'Запуск недоступен' }).isDisabled()); assert.equal(count, 1);
        } else {
          await page.getByRole('button', { name: 'Запустить анализ' }).click(); await visible(page.locator('.feed-card'));
          await page.locator('.feed-card').first().click(); await visible(page.locator('#insight-message'));
          let count = 0, release;
          const held = new Promise(resolve => { release = resolve; });
          await page.route('**/messages', async route => { count++; await held; await route.abort('failed'); });
          await page.locator('#insight-message').fill('Проверка неизвестного результата');
          await page.getByRole('button', { name: 'Отправить вопрос' }).click(); await textVisible(page, 'Отправляем…');
          assert.ok(await page.getByRole('button', { name: 'Отправить вопрос' }).isDisabled());
          await page.getByRole('button', { name: 'Закрыть карточку' }).click();
          await page.locator('.feed-card').first().click(); await visible(page.locator('#insight-message'));
          release(); await textVisible(page, 'результат отправки неизвестен');
          assert.ok(await page.getByRole('button', { name: 'Отправить вопрос' }).isDisabled()); assert.equal(count, 1);
        }
      }
      await noOverflow(page); result.cases.push({ name: scenario, response_mocks: true, status: 'passed' });
    } finally { await context.close(); }
  }
}
async function cleanup() {
  if (browser) await browser.close().catch(() => {});
  for (const child of children.reverse()) {
    if (child.exitCode !== null || child.startError) continue;
    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill(); await Promise.race([exited, sleep(3000)]);
    if (child.exitCode === null) { child.kill('SIGKILL'); await Promise.race([exited, sleep(1000)]); }
  }
  result.processes = children.map(child => ({ pid: child.pid, exit_code: child.exitCode, signal: child.signalCode,
    stopped: Boolean(child.startError || child.exitCode !== null || child.signalCode) }));
}
let timedOut = false;
const watchdog = setTimeout(() => {
  timedOut = true; result.status = 'failed'; result.timed_out = true;
  result.error = 'Global E2E timeout (180s)'; result.finished_at = new Date().toISOString(); persist();
  const forceExit = setTimeout(() => process.exit(124), 5000);
  void cleanup().finally(() => { clearTimeout(forceExit); persist(); process.exit(124); });
}, 180000);
(async () => {
  try {
    result.inputs_before = Object.fromEntries(inputs.map(p => [p, hash(path.join(root, p))]));
    assert.ok(fs.existsSync(path.join(root, 'frontend/dist/index.html')), 'Run npm run build first');
    result.bundle_sha256 = hash(path.join(root, 'frontend/dist/index.html'));
    const backendPort = await freePort(), uiPort = await freePort();
    // Named non-secret operator settings only. No environment enumeration/secret reads.
    Object.assign(process.env, { ALPHA_ANALYSIS_ENGINE: 'mvp_offline', ALPHA_MVP_LOCAL_ACCESS: '1',
      ALPHA_MVP_ALLOW_LIVE: '0', ALPHA_MVP_ALLOW_CLIENT_DATA: '0', ALPHA_DB_PATH: path.join(out, 'app.sqlite3'),
      ALPHA_MVP_COMPANIES_PATH: path.join(root, inputs[0]), ALPHA_MVP_PAYMENTS_PATH: path.join(root, inputs[1]),
      ALPHA_MVP_LINKS_PATH: path.join(root, inputs[2]), ALPHA_MVP_NEWS_PATH: path.join(root, inputs[3]),
      ALPHA_MVP_BANK_PATH: path.join(root, inputs[4]), ALPHA_UI_API_TARGET: `http://127.0.0.1:${backendPort}`,
      PYTHONDONTWRITEBYTECODE: '1' });
    const backend = start((process.env.ALPHA_TEST_PYTHON || 'python'),
      ['-B', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(backendPort), '--no-access-log'], 'backend');
    await ready(`http://127.0.0.1:${backendPort}/api/v1/health`, backend);
    const health = await (await fetch(`http://127.0.0.1:${backendPort}/api/v1/health`)).json();
    assert.equal(health.mode, 'mvp_offline'); assert.equal(health.model, 'fake'); assert.equal(health.blocker, null);
    result.backend_health = health;
    const vite = start(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), 'preview', '--port', String(uiPort), '--strictPort'], 'vite', path.join(root, 'frontend'));
    const base = `http://127.0.0.1:${uiPort}`; await ready(base, vite);
    const { chromium } = require(process.env.ALPHA_PLAYWRIGHT_MODULE || 'playwright-core');
    browser = await chromium.launch({ executablePath: process.env.ALPHA_CHROME_PATH, headless: true, chromiumSandbox: true });
    await positive(base, { width: 1440, height: 1000 }, 0);
    await positive(base, { width: 390, height: 844 }, 1);
    await failureCases(base); result.status = 'passed';
  } catch (error) {
    result.status = 'failed'; result.error = String(error.stack || error); process.exitCode = 1;
  } finally {
    await cleanup(); clearTimeout(watchdog);
    try { result.inputs_after = Object.fromEntries(inputs.map(p => [p, hash(path.join(root, p))]));
      assert.deepEqual(result.inputs_after, result.inputs_before); result.inputs_unchanged = true;
    } catch (error) { result.inputs_unchanged = false; result.integrity_error = String(error); result.status = 'failed'; process.exitCode = 1; }
    result.finished_at = new Date().toISOString(); result.timed_out = timedOut;
    persist();
    console.log(JSON.stringify({ status: result.status, cases: result.cases, error: result.error, artifacts: result.artifacts }));
  }
})();
