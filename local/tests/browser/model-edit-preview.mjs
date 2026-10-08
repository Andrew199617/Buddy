import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage, user } from './mobile-ui-preview.mjs';

const directory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const sourcePath = resolve(directory, '../../web/model-edit-shortcut.js');
const baseline = process.argv.includes('--baseline');
const results = [];
const now = Math.floor(Date.now() / 1000);
const fixtures = [
  { id: 'preview-editable-model', name: 'Default Preview Model', write_access: true },
  { id: 'preview/provider model &draft', name: 'Reasoning Model with a Long Display Name', write_access: true },
  { id: 'preview-shared-readonly', name: 'Shared Read-only Model', write_access: false }
];

function fullModel(fixture, viewer) {
  return {
    ...fixture,
    write_access: viewer ? false : fixture.write_access,
    user_id: user.id,
    user: { id: user.id, name: user.name, email: user.email },
    base_model_id: 'preview-default-model', is_active: true,
    created_at: now - 86400, updated_at: now - 3600,
    params: { system: '' },
    meta: { description: 'Synthetic model for edit-shortcut UI verification.', capabilities: { vision: true, file_upload: true, tool_calling: true }, tags: [] },
    access_control: { read: { user_ids: ['*'], group_ids: [] }, write: { user_ids: [user.id], group_ids: [] } }
  };
}

async function createModelsPage(options) {
  const session = await createMockedPage({ ...options, urlPath: '/c/mobile-preview-chat-1' });
  const items = fixtures.map((fixture) => fullModel(fixture, options.viewer));
  const workspaceRequests = [];
  const syntheticWrites = [];
  const previewUser = { ...user, permissions: { ...user.permissions, workspace: { models: true, models_export: true, models_import: true } } };
  await session.context.route('**/api/v1/auths/', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(previewUser) });
  });
  await session.context.route('**/api/v1/models/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const id = url.searchParams.get('id');
    const model = items.find((item) => item.id === id);
    workspaceRequests.push({ method: request.method(), path: url.pathname, id });
    let response = [];
    if (request.method() !== 'GET') {
      syntheticWrites.push({ method: request.method(), path: url.pathname, id });
      if (url.pathname.endsWith('/toggle') && model) model.is_active = !model.is_active;
      response = model ?? { status: true };
    } else if (url.pathname.endsWith('/list')) {
      const query = (url.searchParams.get('query') ?? '').toLowerCase();
      const matching = items.filter((item) => item.name.toLowerCase().includes(query));
      response = { items: matching, total: matching.length };
    } else if (url.pathname.endsWith('/model')) {
      response = model ?? null;
    } else if (url.pathname.endsWith('/all')) {
      response = items;
    } else if (url.pathname.endsWith('/base')) {
      response = [{ id: 'preview-default-model', name: 'Default Preview Model' }];
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) });
  });
  async function emptySearch(route) {
    assert.equal(route.request().method(), 'GET');
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0 }) });
  }
  await session.context.route('**/api/v1/notes/search?**', emptySearch);
  await session.context.route('**/api/v1/knowledge/search?**', emptySearch);
  await session.context.route('**/api/v1/knowledge/search/files?**', emptySearch);
  await session.context.route('**/api/v1/files/search?**', emptySearch);
  if (baseline) await session.context.addInitScript(() => { window.__owuiModelEditShortcut = true; });
  await session.page.goto('http://localhost:8080/workspace/models', { waitUntil: 'domcontentloaded' });
  await session.page.locator('#model-list').waitFor({ state: 'visible' });
  await session.page.evaluate(() => document.fonts.ready);
  if (options.dark) {
    await session.page.evaluate(() => {
      document.documentElement.classList.remove('light');
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    });
  }
  if (!baseline && existsSync(sourcePath)) await session.page.addScriptTag({ content: readFileSync(sourcePath, 'utf8') });
  await session.page.waitForTimeout(180);
  return { ...session, items, workspaceRequests, syntheticWrites };
}

function modelRow(page, id) {
  return page.locator(`[id=${JSON.stringify(`model-item-${id}`)}]`);
}

async function inspectRows(page) {
  return page.locator('#model-list [id^="model-item-"]').evaluateAll((rows) => rows.map((row) => {
    function geometry(element) {
      const rect = element.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right };
    }
    return {
      id: row.id, tabindex: row.getAttribute('tabindex'), ...geometry(row),
      controls: [...row.querySelectorAll('button,a,[role="switch"]')].map((element) => ({ tag: element.tagName, role: element.getAttribute('role'), label: element.getAttribute('aria-label'), href: element.getAttribute('href'), className: element.className, ...geometry(element) })),
      html: row.outerHTML,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth
    };
  }));
}

async function baselineScenario() {
  const session = await createModelsPage({ viewport: { width: 1280, height: 900 }, isMobile: false, hasTouch: false, dark: true });
  try {
    const rows = await inspectRows(session.page);
    await modelRow(session.page, fixtures[0].id).locator('button').first().click();
    await session.page.locator('[role="menu"]:visible').waitFor({ state: 'visible' });
    const menu = await session.page.locator('[role="menu"]:visible').innerText();
    await session.page.keyboard.press('Escape');
    await session.page.screenshot({ path: resolve(directory, 'model-edit-native-baseline.png') });
    results.push({ test: 'native-baseline', rows, menu, errors: session.errors, workspaceRequests: session.workspaceRequests });
    console.log(JSON.stringify({ test: 'native-baseline', rows, menu }, null, 2));
  } finally {
    await session.browser.close();
  }
}

async function waitForShortcuts(page, expected) {
  await page.waitForFunction((count) => document.querySelectorAll('#model-list .owui-model-edit-shortcut').length === count, expected);
}

async function directEditScenario(name, options) {
  const session = await createModelsPage(options);
  try {
    const { page } = session;
    await waitForShortcuts(page, 2);
    const sizes = [];
    for (const fixture of fixtures) {
      const row = modelRow(page, fixture.id);
      const editable = row.locator('.owui-model-edit-shortcut');
      if (!fixture.write_access) {
        assert.equal(await editable.count(), 0);
        assert.equal(await row.locator('[role="switch"]').count(), 0);
        continue;
      }
      assert.equal(await editable.count(), 1);
      assert.equal(await editable.getAttribute('aria-label'), `Edit ${fixture.name}`);
      assert.equal(await editable.getAttribute('title'), `Edit ${fixture.name}`);
      const size = await editable.boundingBox();
      const glyph = await editable.locator('svg').boundingBox();
      const menuSize = await row.locator('.owui-model-edit-menu').boundingBox();
      const titleSize = await row.locator('a[href^="/?model="]').boundingBox();
      const touch = options.hasTouch !== false;
      const target = touch ? 44 : 32;
      assert.equal(size.width, target);
      assert.equal(size.height, target);
      assert.equal(menuSize.width, target);
      assert.equal(menuSize.height, target);
      assert.equal(glyph.width, touch ? 20 : 18);
      assert.ok(size.x + size.width <= menuSize.x, 'Edit should appear before More without overlap');
      assert.ok(menuSize.x + menuSize.width <= options.viewport.width);
      assert.ok(titleSize.width >= 80, 'The model name should retain useful visible width');
      const rowSize = await row.boundingBox();
      assert.ok(size.x >= rowSize.x && size.x + size.width <= rowSize.x + rowSize.width);
      assert.ok(menuSize.x >= rowSize.x && menuSize.x + menuSize.width <= rowSize.x + rowSize.width);
      assert.equal(await editable.evaluate((button) => button.nextElementSibling?.getAttribute('aria-haspopup')), 'true');
      sizes.push({ id: fixture.id, size, glyph, menuSize, titleSize });
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: resolve(directory, `model-edit-${name}.png`) });
    await page.addScriptTag({ content: readFileSync(sourcePath, 'utf8') });
    await waitForShortcuts(page, 2);
    const row = modelRow(page, fixtures[0].id);
    const listUrl = page.url();
    await row.locator('.owui-model-edit-menu').click();
    const menu = page.locator('[role="menu"]:visible');
    await menu.waitFor({ state: 'visible' });
    assert.match(await menu.innerText(), /Edit/);
    await page.keyboard.press('Escape');
    await page.locator('[role="menu"]:visible').waitFor({ state: 'detached' });
    assert.equal(page.url(), listUrl);
    const toggle = row.locator('[role="switch"]');
    assert.equal(await toggle.getAttribute('aria-checked'), 'true');
    await toggle.click();
    await page.waitForFunction(() => document.querySelector('[id="model-item-preview-editable-model"] [role="switch"]')?.getAttribute('aria-checked') === 'false');
    assert.equal(page.url(), listUrl);
    const toggleWrites = session.syntheticWrites.filter((request) => request.path.endsWith('/toggle'));
    assert.equal(toggleWrites.length, 1);
    assert.equal(toggleWrites[0].id, fixtures[0].id);
    const search = page.getByRole('textbox', { name: 'Search Models', exact: true });
    await search.fill('Reasoning');
    await page.waitForFunction(() => document.querySelectorAll('#model-list [id^="model-item-"]').length === 1);
    await waitForShortcuts(page, 1);
    await search.fill('');
    await page.waitForFunction(() => document.querySelectorAll('#model-list [id^="model-item-"]').length === 3);
    await waitForShortcuts(page, 2);
    const encodedRow = modelRow(page, fixtures[1].id);
    const edit = encodedRow.locator('.owui-model-edit-shortcut');
    if (name === 'desktop-dark') {
      await edit.focus();
      await page.keyboard.press('Enter');
    } else await edit.click();
    await page.waitForURL((url) => url.pathname === '/workspace/models/edit' && url.searchParams.get('id') === fixtures[1].id);
    const modelName = page.getByPlaceholder('Model Name', { exact: true });
    await modelName.waitFor({ state: 'visible' });
    assert.equal(await modelName.inputValue(), fixtures[1].name);
    assert.ok(session.workspaceRequests.some((request) => request.path.endsWith('/model') && request.id === fixtures[1].id));
    assert.equal(session.syntheticWrites.filter((request) => !request.path.endsWith('/toggle')).length, 0);
    const errors = session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
    assert.deepEqual(errors, []);
    const result = { test: name, sizes, directEditorId: new URL(page.url()).searchParams.get('id'), nativeMenu: true, nativeToggle: true, filteredRows: true, syntheticWrites: session.syntheticWrites, errors };
    results.push(result);
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await session.browser.close();
  }
}

async function viewerScenario() {
  const session = await createModelsPage({ viewport: { width: 390, height: 844 }, viewer: true });
  try {
    const { page } = session;
    assert.equal(await page.locator('#model-list .owui-model-edit-shortcut').count(), 0);
    assert.equal(await page.locator('#model-list [role="switch"]').count(), 0);
    const row = modelRow(page, fixtures[0].id);
    assert.equal(await row.getAttribute('tabindex'), '-1');
    const previousUrl = page.url();
    await row.click({ position: { x: 3, y: 3 } });
    assert.equal(page.url(), previousUrl);
    await row.locator('.owui-model-edit-menu').click();
    const menu = page.locator('[role="menu"]:visible');
    await menu.waitFor({ state: 'visible' });
    assert.equal(await menu.getByRole('button', { name: 'Edit', exact: true }).count(), 0);
    assert.equal(await menu.getByRole('button', { name: 'Copy Link', exact: true }).isVisible(), true);
    await page.keyboard.press('Escape');
    await page.screenshot({ path: resolve(directory, 'model-edit-viewer.png') });
    assert.deepEqual(session.syntheticWrites, []);
    const errors = session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
    assert.deepEqual(errors, []);
    results.push({ test: 'read-only-viewer', noEditShortcuts: true, nativeMenu: true, syntheticWrites: [], errors });
    console.log(JSON.stringify({ test: 'read-only-viewer', noEditShortcuts: true, nativeMenu: true, errors }));
  } finally {
    await session.browser.close();
  }
}

try {
  if (baseline) await baselineScenario();
  else if (process.argv.includes('--narrow')) {
    await directEditScenario('narrow320-large', { viewport: { width: 320, height: 740 }, textScale: 1.25 });
  } else {
    await directEditScenario('desktop-dark', { viewport: { width: 1280, height: 900 }, isMobile: false, hasTouch: false, dark: true });
    await directEditScenario('mobile390', { viewport: { width: 390, height: 844 } });
    await directEditScenario('narrow320-large', { viewport: { width: 320, height: 740 }, textScale: 1.25 });
    await viewerScenario();
  }
  writeFileSync(resolve(directory, `model-edit-${baseline ? 'baseline' : 'result'}.json`), JSON.stringify(results, null, 2));
} catch (error) {
  console.error(error.stack ?? error);
  process.exitCode = 1;
}
