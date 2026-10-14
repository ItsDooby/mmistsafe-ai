const { chromium } = require('C:/Users/coolm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = 'http://127.0.0.1:3000';
    const api = async (route, body = {}) => {
      const res = await page.request.post(base + route, { data: { device_id: 'MISTSAFE-SIM-01', ...body } });
      assert(res.ok(), `${route}: ${await res.text()}`);
      const data = await res.json();
      assert(!data.error && data.success !== false, JSON.stringify(data));
      return data;
    };
    const nav = async name => page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name, exact: true }).click();
    const mobileCheck = async name => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(200);
      await page.screenshot({ path: `redesign-${name}-mobile.png`, fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: mobile overflow`);
      await page.setViewportSize({ width: 1440, height: 1100 });
    };
    const waitTitle = async text => page.waitForFunction(text => document.querySelector('.operating-summary h2')?.textContent.includes(text), text, { timeout: 30000 });
    await api('/api/simulator/telemetry-control', { mode: 'resume' });
    await api('/api/simulator/clear-faults');
    await api('/api/simulator/water-level', { mode: '50' });
    await page.goto(base);
    await page.waitForResponse(res => res.url().includes('/api/status?') && res.ok());
    await waitTitle('Waiting for device');
    assert.equal(await page.locator('.origin-badge').textContent(), 'REAL DEVICE');
    assert.equal(await page.locator('.measurement-value').first().textContent(), '—');
    await page.screenshot({ path: 'redesign-primary.png', fullPage: true });
    await page.locator('#device-select').selectOption('MISTSAFE-SIM-01');
    await page.waitForFunction(() => document.querySelector('figure svg text')?.textContent === '50%', null, { timeout: 25000 });
    assert.equal(await page.locator('.origin-badge').textContent(), 'SIMULATED');
    await page.waitForTimeout(900);
    await page.screenshot({ path: 'redesign-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'redesign-mobile.png', fullPage: true });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile horizontal overflow');
    await page.setViewportSize({ width: 1440, height: 1100 });
    if (process.argv.includes('--capture')) { console.log('Initial desktop, primary and mobile screenshots captured'); return; }
    // Explicit stop -> reset leaves stopped -> separate start.
    await page.getByRole('button', { name: 'Request shutdown for selected device' }).click();
    await page.waitForFunction(() => document.querySelector('.operating-summary')?.textContent.includes('stop latch'), null, { timeout: 25000 });
    await page.getByRole('button', { name: 'Reset test device', exact: true }).click();
    await waitTitle('Stopped');
    await page.getByRole('button', { name: 'Start simulated operation', exact: true }).click();
    await waitTitle('Running');

    // Developer tools retained, active faults visible on Overview.
    await nav('Developer tools');
    await page.getByRole('button', { name: /Inject UV Fault/ }).click();
    await nav('Overview');
    await waitTitle('System stopped');
    assert(await page.locator('.attention-list').textContent().then(t => /UV/i.test(t)));
    assert(await page.getByRole('button', { name: 'Start simulated operation', exact: true }).isDisabled());
    await page.screenshot({ path: 'redesign-fault.png', fullPage: true });
    await nav('Developer tools');
    await page.getByRole('button', { name: 'Clear Simulator Faults', exact: true }).click();
    await nav('Overview');
    // Preserve the existing recovery path without changing backend semantics.
    await api('/api/shutdown');
    await api('/api/simulator/reset');
    await api('/api/simulator/start');
    await waitTitle('Running');

    await nav('Trends');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /Export loaded records/ }).click();
    const download = await downloadPromise;
    await download.saveAs('redesign-export.csv');
    assert(fs.readFileSync('redesign-export.csv', 'utf8').includes('MISTSAFE-SIM-01'));

    // Input and selected file survive polling; saved metadata/image survive reload.
    await nav('Samples');
    const sampleId = `UI-REFERENCE-${Date.now()}`;
    await page.getByLabel('Sample ID', { exact: true }).fill(sampleId);
    await page.getByLabel('Sampling Location', { exact: true }).fill('UI test only — no physical sample');
    await page.getByLabel('Imaging Method', { exact: true }).selectOption({ label: 'Other / Not applicable' });
    await page.getByLabel('Data Origin (Safety Classification)', { exact: true }).selectOption('REFERENCE');
    await page.locator('#sample-image').setInputFiles('cylinder-50.png');
    await page.waitForFunction(() => document.querySelector('.sample-file-info')?.textContent.includes('Ready to save'));
    await page.waitForTimeout(5500);
    assert.equal(await page.getByLabel('Sample ID', { exact: true }).inputValue(), sampleId);
    await page.getByRole('button', { name: 'Save sample', exact: true }).click();
    await page.waitForFunction(id => document.querySelector('main')?.textContent.includes(`${id} saved and available`), sampleId, { timeout: 25000 });
    await page.screenshot({ path: 'redesign-samples.png', fullPage: true });
    await page.reload();
    await page.locator('#device-select').selectOption('MISTSAFE-SIM-01');
    await nav('Samples');
    await page.getByRole('img', { name: sampleId, exact: true }).waitFor();
    await page.waitForFunction(id => [...document.querySelectorAll('img')].some(img => img.alt === id && img.complete && img.naturalWidth > 0), sampleId);
    await mobileCheck('samples');
    // Duplicate save must retain the chosen file and display an error.
    await page.getByLabel('Sample ID', { exact: true }).fill(sampleId);
    await page.locator('#sample-image').setInputFiles('cylinder-50.png');
    await page.waitForFunction(() => document.querySelector('.sample-file-info')?.textContent.includes('Ready to save'));
    await page.getByRole('button', { name: 'Save sample', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'already exists' }).waitFor();
    assert(await page.locator('.sample-file-info').textContent().then(t => t.includes('cylinder-50.png')));

    await nav('AI analysis');
    await mobileCheck('ai');
    await page.getByRole('button', { name: /Analyse readings/ }).click();
    await page.waitForFunction(() => document.querySelector('.action-feedback')?.textContent.includes('unavailable'), null, { timeout: 30000 });
    assert(!(await page.locator('.action-feedback').textContent()).includes('successfully'));

    await nav('Developer tools');
    await mobileCheck('developer');
    assert(await page.locator('main').textContent().then(t => t.includes('Not run')));
    await page.getByRole('button', { name: 'Execute Pytest Suite', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('38 passed'), null, { timeout: 30000 });
    await page.getByRole('button', { name: /Pause readings/ }).click();
    await nav('Overview');
    await waitTitle('Connection lost');
    assert(await page.locator('figure').textContent().then(t => t.includes('Stale')));
    await page.screenshot({ path: 'redesign-stale.png', fullPage: true });
    await api('/api/simulator/telemetry-control', { mode: 'resume' });
    await waitTitle('Running');
    await nav('Activity');
    await mobileCheck('activity');
    await nav('Overview');
    assert.deepEqual(errors, []);
    fs.writeFileSync('redesign-browser-results.json', JSON.stringify({ deviceIsolation: true, shutdownResetStart: true, uvFaultVisible: true, blockedStart: true, trendsCsv: true, samplePollingAndReload: true, duplicateRetainsFile: true, honestAiUnavailable: true, realTestExecution: true, pausedStaleCylinder: true, mobileNoOverflow: true, pageErrors: errors }, null, 2));
    console.log('Redesign browser regression checks passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
