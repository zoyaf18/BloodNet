const { chromium } = require('@playwright/test');
(async()=>{
  const browser = await chromium.launch({args:['--headless']});
  const page = await browser.newPage();
  const requestFailures = [];
  const httpErrors = [];
  const consoleErrors = [];
  const pageErrors = [];

  page.on('requestfailed', (request) => {
    const failure = request.failure();
    requestFailures.push({url: request.url(), msg: failure && failure.message, status: failure && failure.code});
  });

  page.on('response', (response) => {
    if (response.status() >= 400) httpErrors.push({status: response.status(), url: response.url()});
  });

  page.on('pageerror', (error) => {
    pageErrors.push(error && error.message);
  });

  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('https://bloodnet-app.web.app/', {waituntil: 'load', timeout: 10000});
  await page.getByLabel('Email').fill('bank@bloodnet.local');
  await page.getByLabel('Password').fill('BN-local-lDvdQZ02-_3_OYLz_n8IOg!');
  await page.getByRole('button', {name: 'Sign in'}).click();
  await page.waitForTimeout(4500);

  console.log('title:', await page.title());
  console.log('body-start:', (await page.locator('body').innerText()).slice(0,900));
  console.log('requestFailures:', JSON.stringify(requestFailures, null, 2));
  console.log('httpErrors:', JSON.stringify(httpErrors, null, 2));
  console.log('consoleErrors:', JSON.stringify(consoleErrors, null, 2));
  console.log('pageErrors:', JSON.stringify(pageErrors, null, 2));

  await browser.close();
})();
