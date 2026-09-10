// Real browser -> Firebase Hosting -> Identity Platform -> API Gateway -> Cloud Run.
// No credentials, cookies, screenshots, or browser storage are written to disk.
const { chromium } = require('../web/app/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const email = process.env.BLOODNET_VERIFY_EMAIL;
  const password = process.env.BLOODNET_VERIFY_PASSWORD;
  const role = process.env.BLOODNET_VERIFY_ROLE;
  delete process.env.BLOODNET_VERIFY_EMAIL;
  delete process.env.BLOODNET_VERIFY_PASSWORD;
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const report = { role, started_at: new Date().toISOString(), origin: process.env.BLOODNET_VERIFY_ORIGIN || 'https://project-bae56d7f-3ee2-48fc-bdd.web.app', steps: [], api: [], request_failures: [], page_errors: 0 };
  page.on('pageerror', () => { report.page_errors += 1; });
  page.on('requestfailed', request => {
    const url = new URL(request.url());
    if (url.hostname.endsWith('.gateway.dev')) report.request_failures.push({path: url.pathname, error: request.failure()?.errorText});
  });
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.hostname.endsWith('.gateway.dev')) report.api.push({ path: url.pathname, status: response.status() });
  });
  try {
    await page.goto(report.origin, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.locator('input[type="email"]').first().fill(email, { timeout: 30000 });
    await page.locator('input[type="password"]').first().fill(password);
    await page.locator('form').filter({ has: page.locator('input[type="password"]') }).locator('button[type="submit"]').click();
    await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor({ timeout: 45000 });
    report.steps.push({ name: 'browser_login_and_workspace', passed: true });
    await page.reload({ waitUntil: 'networkidle', timeout: 45000 });
    await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor({ timeout: 30000 });
    report.steps.push({ name: 'session_survives_refresh', passed: true });
    if (role === 'bank_admin') {
      await page.getByRole('button', { name: 'Inventory', exact: true }).click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(2000);
      report.steps.push({ name: 'inventory_surface', tab_labels: await page.getByRole('button').allTextContents().then(items => items.filter(item => /^(Inventory|Reservations|Expiry|Queue)/.test(item.trim()))) });
    }
    report.steps.push({ name: 'forms_and_navigation', buttons: await page.getByRole('button').allTextContents().then(items => items.filter(item => /inventory|profile|request|reservation|notification|dashboard/i.test(item)).map(item => item.trim()).slice(0, 20)) });
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await page.locator('input[type="email"]').first().waitFor({ timeout: 20000 });
    report.steps.push({ name: 'logout', passed: true });
  } catch (error) {
    report.steps.push({ name: 'browser_flow', passed: false, error_type: error.name });
  } finally {
    await browser.close();
    const prefix = process.env.BLOODNET_VERIFY_REPORT_PREFIX || 'production';
    fs.writeFileSync(path.join(__dirname, `../docs/${prefix}-browser-${role}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  }
})();
