const {chromium}=require('C:/Users/coolm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100},reducedMotion:'reduce'});
 const base=process.env.PREVIEW_URL||'http://127.0.0.1:3001/mmistsafe-ai/';
 const errors=[],bad=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.url().startsWith(base)&&r.status()>=400)bad.push(r.url())});page.on('request',r=>requests.push(r.url()));
 const nav=async name=>page.getByRole('navigation').getByRole('button',{name,exact:true}).click();
 const shot=async name=>{await page.waitForTimeout(300);await page.screenshot({path:`redesign-v3-${name}.png`,fullPage:true})};
 const overflow=async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflow');
 try{
 await page.goto(base);await page.locator('.hero-score').waitFor();await page.evaluate(()=>document.fonts.ready);await page.getByRole('button',{name:'Pause replay',exact:true}).click();
 assert.equal(await page.getByRole('navigation').getByRole('button').count(),7);await shot('overview-en');
 assert(!(await page.locator('body').innerText()).match(/workbook|simulated|demo|hackathon|presentation|scenario|story|recorded|\bSIM\b|illustrat|localhost|\/api|127\.0\.0\.1/i));
 await page.getByRole('button',{name:'Collapse sidebar'}).click();assert(await page.locator('.product').evaluate(el=>el.classList.contains('collapsed')));await shot('collapsed-en');await page.getByRole('button',{name:'Expand sidebar'}).click();
 await nav('Samples');assert.equal(await page.locator('tbody tr').count(),12);await page.getByLabel('Sort samples').selectOption('id');await page.getByRole('button',{name:'Next page',exact:true}).click();assert((await page.locator('tbody').innerText()).includes('MS-0013'));await page.getByRole('button',{name:'Previous page',exact:true}).click();
 await page.getByLabel('Category',{exact:true}).selectOption('Legionella');assert((await page.locator('.pagination').innerText()).includes('20'));await page.getByLabel('Search samples').fill('MS-0001');assert.equal(await page.locator('tbody tr').count(),1);
 const dl=page.waitForEvent('download');await page.getByRole('button',{name:'Export',exact:true}).click();assert(fs.readFileSync(await (await dl).path(),'utf8').includes('MS-0001'));
 await page.getByRole('button',{name:'MS-0001',exact:true}).click();assert(await page.locator('.detail-drawer').isVisible());await shot('drawer-en');await page.keyboard.press('Escape');assert(!(await page.locator('.detail-drawer').isVisible()));
 await page.getByLabel('Search samples').fill('missing-entry');await page.getByRole('heading',{name:'No samples match these filters'}).waitFor();await page.getByLabel('Search samples').fill('');await shot('samples-en');
 await nav('Sensor AI');await page.getByRole('heading',{name:'Feature importance',exact:true}).waitFor();await shot('sensor-en');
 await nav('Microscopy AI');assert(await page.getByRole('button',{name:'Zoom in',exact:true}).isDisabled());await shot('microscopy-en');
 await nav('Alerts');await page.getByRole('button',{name:'Acknowledge',exact:true}).first().click();await page.getByRole('button',{name:'Verify & resolve',exact:true}).first().click();assert(await page.getByRole('button',{name:'Log verification & resolve',exact:true}).isDisabled());await page.getByLabel('Verification note',{exact:true}).fill('Reviewed the supplied safety flag and documented research-only closure.');await page.getByRole('button',{name:'Log verification & resolve',exact:true}).click();assert((await page.locator('.event-list').innerText()).includes('Verification logged'));await page.reload();await page.locator('.event-list').waitFor();assert((await page.locator('.event-list').innerText()).includes('Verification logged'));await shot('alerts-en');
 await nav('Performance');await page.locator('.performance-hero').waitFor();assert((await page.locator('.hero-score').innerText()).includes('70'));const actual=JSON.parse(fs.readFileSync('public/analysis/performance.json','utf8'));assert.equal(actual.matrix.tp+actual.matrix.tn+actual.matrix.fp+actual.matrix.fn,actual.testCount);assert(!actual.predictions.some(r=>actual.trainingIds.includes(r.id)));await shot('performance-en');
 await nav('Configuration');await page.getByLabel('Moderate starts at').fill('80');assert(await page.getByRole('button',{name:'Save settings'}).isDisabled());await page.getByLabel('Moderate starts at').fill('35');await page.getByLabel('High starts at').fill('70');await page.getByLabel('Temperature units').selectOption('F');await page.getByLabel('Refresh interval').selectOption('5');await page.getByRole('button',{name:'Save settings'}).click();await page.reload();await page.getByLabel('Moderate starts at').waitFor();assert.equal(await page.getByLabel('Moderate starts at').inputValue(),'35');assert.equal(await page.getByLabel('Temperature units').inputValue(),'F');await shot('configuration-en');
 await nav('Overview');assert((await page.locator('.readings').innerText()).includes('°F'));const before=await page.getByLabel('Selected sample').inputValue();await page.getByRole('button',{name:'Resume replay',exact:true}).click();await page.waitForTimeout(5500);assert.notEqual(await page.getByLabel('Selected sample').inputValue(),before);await page.getByRole('button',{name:'Pause replay',exact:true}).click();
 await page.getByRole('button',{name:'التبديل إلى العربية'}).click();assert.equal(await page.locator('html').getAttribute('dir'),'rtl');await shot('overview-ar');
 for(const [name,key] of [['العينات','samples'],['ذكاء الحساسات','sensor'],['ذكاء الصور المجهرية','microscopy'],['التنبيهات','alerts'],['الأداء','performance'],['الإعدادات','configuration']]){await nav(name);await shot(key+'-ar');await overflow()}
 await page.setViewportSize({width:390,height:844});
 for(const [name,key] of [['نظرة عامة','overview'],['العينات','samples'],['ذكاء الحساسات','sensor'],['ذكاء الصور المجهرية','microscopy'],['التنبيهات','alerts'],['الأداء','performance'],['الإعدادات','configuration']]){await page.getByRole('button',{name:'فتح القائمة',exact:true}).click();await nav(name);await overflow();await shot(key+'-mobile-ar')}
 await page.getByRole('button',{name:'Switch to English'}).click();await page.getByRole('button',{name:'Open menu'}).click();await nav('Overview');await shot('overview-mobile-en');await page.setViewportSize({width:320,height:740});await overflow();
 assert.deepEqual(errors,[]);assert.deepEqual(bad,[]);assert(!requests.some(u=>/\/api\//.test(u)));
 await page.evaluate(()=>localStorage.clear());
 console.log('PASS: seven routes, collapsed sidebar, labels, search/filter/sort/pagination, drawer, export, alert verification/persistence, computed hold-out metrics, settings validation/persistence, unit conversion, synchronized replay, Arabic and mobile layouts.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
