const { chromium } = require('C:/Users/coolm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = 'http://127.0.0.1:3000';
  const post = async (route, body) => {
    const res = await page.request.post(base + route, { data: { device_id: 'MISTSAFE-SIM-01', ...body } });
    assert(res.ok(), await res.text());
    return res.json();
  };
  await post('/api/simulator/telemetry-control', { mode: 'resume' });
  await page.goto(base);
  await page.locator('select').first().selectOption('MISTSAFE-SIM-01');
  const cylinder = page.locator('figure.water-cylinder');
  if (process.argv.includes('--layout')) {
    await page.waitForFunction(() => document.querySelector('figure.water-cylinder svg text')?.textContent === '50%', null, { timeout: 25000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: 'cylinder-desktop.png', fullPage: true });
    await cylinder.screenshot({ path: 'cylinder-50.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile horizontal overflow');
    await page.screenshot({ path: 'cylinder-mobile.png', fullPage: true });
    await browser.close();
    console.log('Desktop/mobile layout passed at 1440px and 390px');
    return;
  }
  for (const level of ['0', '25', '50', '75', '100']) {
    await post('/api/simulator/water-level', { mode: level });
    await page.waitForFunction(level => document.querySelector('figure.water-cylinder svg text')?.textContent === `${level}%`, level, { timeout: 25000 });
    await page.waitForTimeout(800);
    await cylinder.screenshot({ path: `cylinder-${level}.png` });
  }
  await page.screenshot({ path: 'cylinder-desktop.png', fullPage: true });
  await post('/api/simulator/telemetry-control', { mode: 'pause' });
  await page.waitForFunction(() => document.querySelector('figure.water-cylinder')?.textContent.includes('Stale'), { timeout: 30000 });
  assert(await cylinder.locator('svg text').first().textContent() === '100%');
  await post('/api/simulator/telemetry-control', { mode: 'resume' });
  for (const mode of ['missing', 'fault']) {
    await post('/api/simulator/water-level', { mode });
    await page.waitForFunction(() => document.querySelector('figure.water-cylinder')?.textContent.includes('Level unavailable'), { timeout: 25000 });
  }
  await post('/api/simulator/water-level', { mode: '50' });
  await page.waitForFunction(() => document.querySelector('figure.water-cylinder svg text')?.textContent === '50%', { timeout: 25000 });
  await page.reload();
  await page.locator('select').first().selectOption('MISTSAFE-SIM-01');
  await page.waitForFunction(() => document.querySelector('figure.water-cylinder svg text')?.textContent === '50%', { timeout: 25000 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'cylinder-mobile.png', fullPage: true });
  await page.locator('select').first().selectOption('MISTSAFE-ESP32-01');
  await page.waitForFunction(() => document.querySelector('figure.water-cylinder')?.textContent.includes('Level unavailable'), { timeout: 25000 });
  const primary = await (await page.request.get(base + '/api/status?device_id=MISTSAFE-ESP32-01')).json();
  assert.equal(primary.calibration, null);
  assert.equal(primary.state, null);
  assert.deepEqual(errors, []);
  fs.writeFileSync('cylinder-browser-results.json', JSON.stringify({ levels: [0,25,50,75,100], stale: true, missing: true, fault: true, reload: true, isolatedPrimary: true, pageErrors: errors }, null, 2));
  await browser.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
