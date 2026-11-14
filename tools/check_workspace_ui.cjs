/* Actual browser acceptance; use an installed Playwright and Edge. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const base = process.env.WORKSPACE_URL || 'http://127.0.0.1:8876';
  const out = path.resolve(process.env.UI_OUTPUT_DIR || 'tools/out/workspace-ui'); fs.mkdirSync(out,{recursive:true});
  const browser = await chromium.launch({headless:true,executablePath:process.env.EDGE_EXE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage(), errors=[];
  page.on('pageerror',error=>errors.push(String(error)));
  try {
    await page.goto(base);await page.getByRole('heading',{name:'Что сделаем в проекте?'}).waitFor();
    await page.screenshot({path:path.join(out,'welcome.png'),fullPage:true});
    const bootstrap = await (await context.request.get(base+'/api/bootstrap')).json();
    assert.ok(bootstrap.projects.length && bootstrap.tasks.length,'Point this check at an owned completed acceptance workspace.');
    const chosen=bootstrap.tasks[0];
    await page.locator('.task-link').first().click();await page.locator('.event').first().waitFor();
    await page.getByRole('button',{name:'Изменения файлов',exact:true}).click();await page.locator('.diff .add').first().waitFor();
    await page.screenshot({path:path.join(out,'task-diff.png'),fullPage:true});
    await page.getByRole('button',{name:'Действия и результат',exact:true}).click();
    await page.locator('.event.answer').waitFor();
    await page.screenshot({path:path.join(out,'task-result.png'),fullPage:true});
    const scroll=page.locator('.activity-scroll');
    await scroll.evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'))});
    await page.waitForTimeout(1800);assert.ok(await scroll.evaluate(el=>el.scrollTop)<20,'History polling must preserve manual scroll');
    const second=await context.newPage();await second.goto(base);await second.getByRole('heading',{name:'Что сделаем в проекте?'}).waitFor();
    assert.equal(await second.evaluate(()=>sessionStorage.getItem('workspace-task')),'');
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('workspace-task')),chosen.id);
    await second.setViewportSize({width:950,height:720});await second.screenshot({path:path.join(out,'compact-window.png'),fullPage:true});
    assert.equal(await second.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Compact layout must fit the viewport');
    await page.getByRole('button',{name:'Провайдеры и модели'}).click();
    await page.getByRole('textbox',{name:'Адрес Runtime API'}).fill(process.env.RUNTIME_URL||'http://127.0.0.1:8875/v1');
    await page.getByRole('button',{name:'Проверить связь',exact:true}).click();await page.getByText('Связь установлена',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,errors,checks:['real agent task and diff','manual scroll preserved','two independent window selections','950px layout','Runtime HTTP probe']},null,2));
    console.log('Workspace browser acceptance passed');
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
