const {chromium}=require('C:/Users/coolm/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[], api=[], failedResources=[];
 page.on('response',r=>{if(r.url().startsWith(base)&&r.status()>=400)failedResources.push(`${r.status()} ${r.url()}`)});
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().includes('/api/'))api.push(r.url())});
 const base=process.env.PREVIEW_URL||'http://127.0.0.1:3001/';
 const nav=async text=>page.getByRole('navigation').getByRole('button',{name:text,exact:true}).click();
 const shot=async name=>{await page.waitForTimeout(300);await page.screenshot({path:`redesign-v2-${name}.png`,fullPage:true})};
 const noOverflow=async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Horizontal overflow');
 try{
  await page.goto(base);await page.locator('h1').waitFor();await page.evaluate(()=>document.fonts.ready);await shot('overview-en');
  assert.equal(await page.getByRole('navigation').getByRole('button').first().innerText(),'Developer tools');
  await nav('Sample explorer');assert.equal(await page.locator('tbody tr').count(),10);
  await page.getByRole('button',{name:'Next page',exact:true}).click();assert((await page.locator('tbody').innerText()).includes('MS-SIM-0011'));await page.getByRole('button',{name:'Previous page',exact:true}).click();
  await page.getByLabel('Category',{exact:true}).selectOption('Legionella');assert((await page.locator('.pagination').innerText()).includes('20'));
  await page.getByLabel('Search samples').fill('MS-SIM-0001');assert.equal(await page.locator('tbody tr').count(),1);
  const dl=page.waitForEvent('download');await page.getByRole('button',{name:'Export results'}).click();const downloaded=await dl;const csv=fs.readFileSync(await downloaded.path(),'utf8');assert(csv.includes('MS-SIM-0001'));assert(csv.includes('SYNTHETIC / NOT VERIFIED'));
  await page.getByRole('button',{name:'MS-SIM-0001',exact:true}).click();assert(await page.locator('dialog').isVisible());assert((await page.locator('dialog').innerText()).includes('Image file not supplied'));await page.keyboard.press('Escape');assert(!(await page.locator('dialog').isVisible()));
  await page.getByLabel('Search samples').fill('no-such-record');await page.getByRole('heading',{name:'No matching samples'}).waitFor();await page.getByRole('button',{name:'Clear filters'}).click();await shot('samples-en');
  await nav('Developer tools');await page.getByRole('button',{name:/Elevated risk indicator/}).click();await page.getByRole('button',{name:'Show on overview'}).click();assert((await page.locator('.risk-panel').innerText()).includes('High'));
  await nav('Developer tools');await page.getByRole('button',{name:/Safety condition triggered/}).click();await page.getByRole('button',{name:'Show on overview'}).click();assert((await page.locator('.evidence-strip').innerText()).includes('Fault condition'));
  await nav('Evidence library');await shot('evidence-en');for(const a of await page.locator('a[download]').all()){const href=await a.getAttribute('href');assert((await page.request.get(new URL(href,base).href)).ok())}
  await nav('How it works');await shot('workflow-en');
  await nav('Overview');await page.getByRole('button',{name:'التبديل إلى العربية'}).click();assert.equal(await page.locator('html').getAttribute('dir'),'rtl');await shot('overview-ar');await page.reload();assert.equal(await page.locator('html').getAttribute('lang'),'ar');
  await nav('استعراض العينات');await page.getByRole('button',{name:'MS-SIM-0001',exact:true}).click();await shot('detail-ar');await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844});await noOverflow();await shot('samples-mobile-ar');
  for(const name of ['نظرة عامة','مكتبة الأدلة','كيف تعمل المنصة','أدوات المطوّر']){await page.getByRole('button',{name:'فتح القائمة'}).click();await nav(name);await noOverflow();await shot(name==='نظرة عامة'?'overview-mobile-ar':name==='أدوات المطوّر'?'developer-mobile-ar':'mobile-ar-'+name)}
  await page.getByRole('button',{name:'Switch to English'}).click();await noOverflow();await shot('developer-mobile-en');
  await page.getByRole('button',{name:'Open navigation'}).click();await nav('Overview');await noOverflow();await shot('overview-mobile-en');
  await page.setViewportSize({width:320,height:740});await noOverflow();
  assert.deepEqual(errors,[]);assert.deepEqual(api,[]);assert.deepEqual(failedResources,[]);
  console.log('Passed: source downloads, filtering, pagination, CSV, modal, scenarios, Arabic persistence/RTL, mobile layouts, no backend calls or browser errors.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
