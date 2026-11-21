/* Browser acceptance with a fresh Workspace profile and local model/MCP fixtures. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const model = 'Демо-4B';
const answer = [
  'В ведомости указаны **три исполнителя**. Их роли приведены в таблице.',
  '', '| Исполнитель | Роль |', '| --- | --- |',
  '| Анна Волкова | Разработка |', '| Илья Орлов | Проверка |', '| Мария Белова | Утверждение |',
  '', '*Источник — приложенная ведомость.*',
].join('\n');
const rows = [['Анна Волкова','Разработка'], ['Илья Орлов','Проверка'], ['Мария Белова','Утверждение']];
const file = Buffer.from(rows.map((row) => row.join('\t')).join('\n'));
const toolKey = require('node:crypto').randomBytes(20).toString('hex');
let turns = 0, availability = 'loaded', fixtureError;
const toolCalls = [];
const extraFile = { name: 'Смета.txt', mimeType: 'text/plain', buffer: Buffer.from('budget 8317') };
const sharedFile = { name: 'Общий.txt', mimeType: 'text/plain', buffer: Buffer.from('shared 6024') };

async function checkImportedGroup(page, group, skills, shot) {
  const libraryButton = page.getByRole('button', { name: 'Настройки', exact: true });
  const pickerButton = page.getByRole('button', { name: 'Навыки диалога', exact: true });
  const dialog = page.getByRole('dialog');
  const options = dialog.getByRole('button', { name: 'Операции «' + group.name + '»' });
  const master = dialog.getByRole('checkbox', { name: 'Весь комплект «' + group.name + '»' });
  const op = name => dialog.getByRole('checkbox', { name, exact: true });
  await pickerButton.click();
  assert.equal(await options.getAttribute('aria-expanded'), 'false');
  assert.ok(await master.isChecked());
  assert.equal(await op(skills[0].name).count(), 0);
  await options.click();
  for (const skill of skills) assert.ok(await op(skill.name).isChecked());
  await op(skills[0].name).uncheck();
  assert.equal(await master.evaluate(el => el.indeterminate), true);
  await shot('workspace-group-operations');
  await page.keyboard.press('Escape');
  assert.ok((await pickerButton.innerText()).includes(group.name));

  await libraryButton.click();
  await dialog.getByRole('button', { name: 'Навыки', exact: true }).click();
  assert.equal(await options.getAttribute('aria-expanded'), 'false');
  assert.equal(await master.evaluate(el => el.indeterminate), true);
  await options.click();
  assert.ok(!await op('Навык «' + skills[0].name + '» в диалоге').isChecked());
  await master.click();
  for (const skill of skills) assert.ok(await op('Навык «' + skill.name + '» в диалоге').isChecked());
  await shot('workspace-group-library');
  await master.uncheck();
  await dialog.getByRole('button', { name: 'Готово' }).click();
  await pickerButton.click();
  assert.ok(!await master.isChecked());
  await master.check();
  await dialog.getByRole('button', { name: 'Готово' }).click();
  assert.equal((await pickerButton.innerText()).trim(), group.name);
}

async function serve(req, res) {
  const url = decodeURIComponent(req.url), buffers = [];
  for await (const chunk of req) buffers.push(chunk);
  const body = buffers.length ? JSON.parse(Buffer.concat(buffers)) : {};
  function json(value) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); }
  if (url === '/v1/models') return json({ data: availability === 'missing' ? [] : [{ id: model }] });
  if (url === '/v1/runtime/readiness') return json({ ready_model_ids: availability === 'loaded' ? [model] : [] });
  if (url.endsWith('/capabilities')) return json({ api_version: '1.1', loaded: true, max_output_tokens: 512 });
  if (url === '/v1/chat/completions') {
    if (!body.stream) {
      const options = JSON.parse(body.messages[1].content);
      const names = turns < 2 ? ['Разбор ведомости', 'Краткая сводка']
        : turns === 2 ? ['Проверка сметы'] : turns === 3 ? ['Разбор ведомости','Проверка сметы'] : [];
      const use = names.map(name => {
        const skill = options.skills.find(s => s.name === name);
        assert.ok(skill, name);
        return { skill: skill.id, files: skill.input === 'none' ? [] : 'all' };
      });
      return json({ choices: [{ finish_reason: 'stop', message: { content: JSON.stringify({use}) } }] });
    }
    turns++;
    const system = body.messages.filter((message) => message.role === 'system').map((message) => message.content).join('\n');
    assert.ok(!system.includes(toolKey));
    assert.equal(system.includes('RULE_BRIEF'), turns <= 2);
    assert.equal(system.includes('RULE_TABLE'), turns <= 2);
    assert.equal(system.includes('RULE_DATES'), false);
    assert.equal(system.includes('RULE_REGISTER'), turns <= 2 || turns === 4);
    assert.equal(system.includes('RULE_BUDGET'), turns === 3 || turns === 4);
    assert.equal(system.includes('Анна Волкова'), turns <= 2 || turns === 4);
    assert.equal(system.includes('BUDGET_FIXTURE'), turns === 3 || turns === 4);
    const content = [answer, 'Проверку выполнил **Илья Орлов**.', 'Смета проверена.', 'Общий файл обработан.', 'Обычный ответ.'][turns - 1];
    assert.ok(content, 'one inference per message');
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end('data: ' + JSON.stringify({ choices: [{ delta: { content }, finish_reason: 'stop' }] }) + '\n\ndata: [DONE]\n\n');
    return;
  }
  if (url === '/mcp' || url === '/budget/mcp') {
    const budget = url === '/budget/mcp', name = budget ? 'read_budget' : 'read_register';
    assert.equal(req.headers.authorization, budget ? undefined : 'Bearer ' + toolKey);
    let result;
    switch (body.method) {
      case 'initialize': result = { protocolVersion: '2025-11-25' }; break;
      case 'notifications/initialized': res.writeHead(202); res.end(); return;
      case 'tools/list': result = { tools: [{ name, annotations: { readOnlyHint: true } }] }; break;
      case 'tools/call': {
        assert.equal(body.params.name, name);
        const expected = turns === 3 ? sharedFile : budget ? extraFile : { name: 'Демо-ведомость.txt', buffer: file };
        assert.equal(body.params.arguments.name, expected.name);
        assert.equal(body.params.arguments.data, expected.buffer.toString('base64'));
        toolCalls.push([name, expected.name]);
        result = { structuredContent: budget ? { budget: 'BUDGET_FIXTURE' } : { исполнители: rows } };
        break;
      }
      default: throw Error('Unexpected MCP method');
    }
    return json({ jsonrpc: '2.0', id: body.id, result });
  }
  res.writeHead(404); res.end();
}

async function run() {
  const base = new URL(process.env.WORKSPACE_URL);
  assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(base.hostname), 'Use a disposable local Workspace');
  const token = process.env.WORKSPACE_UI_TOKEN;
  assert.ok(token, 'A disposable profile and operator token are required');
  const headers = { 'X-Workspace-UI-Token': token };
  const boot = await (await fetch(new URL('/api/bootstrap', base), { headers })).json();
  const groupName = process.env.WORKSPACE_TEST_GROUP;
  for (const key of ['tasks','projects','providers', ...(groupName ? [] : ['skills'])]) assert.equal(boot[key]?.length || 0, 0, 'Test requires an isolated profile');
  const fixture = http.createServer((req, res) => serve(req, res).catch((e) => { fixtureError = e; res.writeHead(500); res.end(); }));
  await new Promise((resolve) => fixture.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const local = 'http://127.0.0.1:' + fixture.address().port;
    browser = await chromium.launch({ headless: true, executablePath: process.env.EDGE_EXE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'ru-RU' });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const dialog = page.getByRole('dialog');
    const button = (name) => page.getByRole('button', { name, exact: true });
    const url = base.origin + '/#access=' + token;
    const shot = async (name) => {
      if (!process.env.UI_OUTPUT_DIR) return;
      await fs.mkdir(process.env.UI_OUTPUT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.UI_OUTPUT_DIR, name + '.png') });
    };
    page.setDefaultTimeout(9000);
    await page.goto(url);
    if (groupName) {
      const skills = boot.skills.filter(s => s.group?.name === groupName);
      assert.ok(skills.length >= 2 && skills.length === boot.skills.length, 'Import a single test bundle');
      assert.equal(new Set(skills.map(s => s.group.key)).size, 1);
      await checkImportedGroup(page, skills[0].group, skills, shot);
      assert.deepEqual(errors, []);
      console.log('PASS: complete group, mixed selection, individual operations and shared library/chat state.');
      return;
    }
    await button('Настройки').click();
    await dialog.getByLabel('Адрес Runtime API').fill(local + '/v1');
    await button('Проверить связь').click();
    await dialog.getByRole('status').filter({ hasText: 'Модель загружена' }).waitFor();
    await button('Сохранить подключение').click();
    await dialog.getByText('Подключение сохранено.').waitFor();
    await shot('workspace-runtime');
    for (const [state, label] of [['unloaded','Загрузится при запросе'], ['missing','В Runtime нет моделей'], ['loaded','Модель загружена']]) {
      availability = state;
      await button('Проверить связь').click();
      await dialog.getByRole('status').filter({ hasText: label }).waitFor();
      if (state === 'missing') assert.ok(await button('Сохранить подключение').isDisabled());
    }
    await dialog.getByRole('button', { name: 'Навыки', exact: true }).click();
    await dialog.getByText('Пока нет навыков').waitFor();
    const source = (name, desc, instructions, config) => ['---', 'name: ' + name, 'description: ' + desc, '---', instructions, '', ...(config ? ['```workspace-mcp', JSON.stringify(config), '```'] : [])].join('\n');
    const mapping = { filename: 'name', base64: 'data' };
    const entries = [
      ['summary.md', source('Краткая сводка', 'Собирает основные решения и договорённости.', [
        'RULE_BRIEF', '## Таблица <!-- option: table -->', 'RULE_TABLE', '## Даты <!-- option: dates -->', 'RULE_DATES',
      ].join('\n'))],
      ['register.md', source('Разбор ведомости', 'Извлекает исполнителей и их роли из документа.', 'RULE_REGISTER', { url: local + '/mcp', tool: 'read_register', file: mapping })],
      ['budget.md', source('Проверка сметы', 'Сверяет суммы по приложенной смете.', 'RULE_BUDGET', { url: local + '/budget/mcp', tool: 'read_budget', file: mapping })],
    ];
    async function chooseFiles(control, files) {
      const chooser = page.waitForEvent('filechooser');
      await control.click();
      await (await chooser).setFiles(files);
    }
    await chooseFiles(button('Добавить навыки'), entries.map(([name, data]) => ({ name, mimeType: 'text/markdown', buffer: Buffer.from(data) })));
    await dialog.getByText('summary.md', { exact: true }).waitFor();
    assert.equal(await dialog.locator('input[type=password]').count(), 2);
    await dialog.getByLabel('Ключ приложения для «register.md»').fill(toolKey);
    await button('Добавить в библиотеку').click();
    for (const title of ['Краткая сводка','Разбор ведомости']) await dialog.getByRole('checkbox', { name: 'Навык «' + title + '» в диалоге' }).check();
    await dialog.getByRole('checkbox', { name: 'Навык «Проверка сметы» в диалоге' }).uncheck();
    await shot('workspace-skills');
    await button('Готово').click();
    const menu = page.getByRole('dialog', { name: 'Выбор навыков' });
    async function openSkills() { await button('Навыки диалога').click(); await menu.waitFor(); }
    const check = (name) => menu.getByRole('checkbox', { name, exact: true });
    await openSkills();
    assert.ok(await check('Разбор ведомости').isChecked());
    assert.ok(await check('Краткая сводка').isChecked());
    assert.equal(await check('Проверка сметы').isChecked(), false);
    await menu.locator('.skill-sections summary').click();
    assert.ok(await check('Даты').isChecked());
    await check('Даты').uncheck();
    await shot('workspace-skill-menu');
    await page.keyboard.press('Escape');
    assert.ok(await button('Навыки диалога').evaluate((el) => el === document.activeElement));
    await page.getByRole('textbox', { name: 'Новое сообщение' }).fill('Кто участвовал в проекте? Покажи исполнителей и их роли.');
    assert.ok(await button('Отправить').isEnabled());
    async function attach() {
      const chooser = page.waitForEvent('filechooser');
      await button('Прикрепить файлы').click();
      await (await chooser).setFiles({ name: 'Демо-ведомость.txt', mimeType: 'text/plain', buffer: file });
    }
    await attach(); await button('Убрать Демо-ведомость.txt').click();
    assert.ok(await button('Отправить').isEnabled());
    await attach(); await button('Отправить').click();
    await page.getByRole('cell', { name: 'Анна Волкова' }).waitFor();
    assert.equal(await page.locator('.markdown-answer strong').first().innerText(), 'три исполнителя');
    assert.equal(await page.locator('.markdown-answer em').count(), 1);
    assert.equal(await page.getByRole('row').count(), 4);
    assert.equal(toolCalls.length, 1);
    await button('Демо-ведомость.txt').waitFor();
    await page.locator('.runtime-status.ready').waitFor({ timeout: 4500 });
    await page.locator('.sidebar .task-link .dot.completed').first().waitFor();
    await shot('workspace-dialog');
    await page.locator('.skill-report summary').click();
    assert.ok((await page.locator('.skill-report pre').innerText()).includes('Анна Волкова'));
    await page.locator('.skill-report summary').click();
    await page.goto(url);
    const composer = page.getByRole('textbox', { name: 'Сообщение', exact: true });
    const submitWithKeyboard = async () => {
      await button('Отправить').and(page.locator(':enabled')).waitFor();
      await composer.press('Control+Enter');
    };
    await button('Демо-ведомость.txt').waitFor();
    await composer.fill('Кто отвечал за проверку?');
    await submitWithKeyboard();
    await page.locator('.markdown-answer').filter({ hasText: 'Проверку выполнил' }).waitFor();
    assert.equal(toolCalls.length, 1, 'Follow-up reused the report');
    assert.equal(turns, 2);
    await page.locator('.sidebar .task-link .dot.completed').first().waitFor();
    for (const [width, height] of [[740,600], [1280,720], [2543,1360]]) {
      await page.setViewportSize({ width, height });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const toolbar = await page.locator('.composer-toolbar').boundingBox();
      const send = await button('Отправить').boundingBox();
      assert.ok(send.x >= toolbar.x && send.x + send.width <= toolbar.x + toolbar.width + 1 && send.y + send.height < height);
      if (width === 740) {
        assert.ok(toolbar.height < 55, 'Compact toolbar fits in a single row');
        await shot('workspace-compact');
      }
      await button('Настройки').click();
      await dialog.getByRole('button', { name: 'Навыки', exact: true }).click();
      assert.ok(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1));
      await page.keyboard.press('Escape');
      assert.ok(await button('Настройки').evaluate((el) => el === document.activeElement));
    }
    await openSkills();
    assert.ok(await check('Краткая сводка').isChecked());
    await menu.locator('.skill-sections summary').click();
    assert.ok(await check('Таблица').isChecked());
    assert.equal(await check('Даты').isChecked(), false);
    await check('Краткая сводка').uncheck();
    await check('Проверка сметы').check();
    await menu.getByRole('button', { name: 'Готово' }).click();
    await chooseFiles(button('Прикрепить файлы'), extraFile);
    await composer.fill('Проверь также смету.');
    await submitWithKeyboard();
    await page.locator('.markdown-answer').filter({ hasText: 'Смета проверена.' }).waitFor();
    assert.deepEqual(toolCalls.map(([tool]) => tool), ['read_register','read_budget']);
    await page.locator('.sidebar .task-link .dot.completed').first().waitFor();
    await chooseFiles(button('Прикрепить файлы'), sharedFile);
    await composer.fill('Обработай общий документ.');
    await submitWithKeyboard();
    await page.locator('.markdown-answer').filter({ hasText: 'Общий файл обработан.' }).waitFor();
    assert.deepEqual(toolCalls.slice(2), [['read_register', sharedFile.name], ['read_budget', sharedFile.name]]);
    await page.locator('.sidebar .task-link .dot.completed').first().waitFor();
    await composer.fill('Ответь в обычном режиме.');
    await submitWithKeyboard();
    await page.locator('.markdown-answer').filter({ hasText: 'Обычный ответ.' }).waitFor();
    assert.equal(turns, 5);
    assert.equal(toolCalls.length, 4);
    await page.locator('.sidebar .task-link .dot.completed').first().waitFor();
    await button('Новый чат').click();
    await openSkills();
    for (const name of ['Краткая сводка', 'Разбор ведомости', 'Проверка сметы']) assert.ok(await check(name).isChecked());
    await menu.getByRole('button', { name: 'Готово' }).click();
    assert.deepEqual(errors, []);
    if (fixtureError) throw fixtureError;
    console.log('PASS: Workspace browser, three imported skills, five turns with a fixture model, message attachments, automatic selection, cached follow-up, Markdown, keyboard and three window sizes.');
  } catch (error) {
    if (fixtureError) throw fixtureError;
    throw error;
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => fixture.close(resolve));
  }
}
run().catch((error) => {
  // Playwright navigation errors may include the temporary operator fragment.
  console.error(String(error.stack || error).replaceAll(process.env.WORKSPACE_UI_TOKEN || 'unused', '[token]').replaceAll(toolKey, '[key]'));
  process.exitCode = 1;
});
