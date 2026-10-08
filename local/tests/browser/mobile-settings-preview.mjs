import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage, mockChat, user } from './mobile-ui-preview.mjs';

const outputDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const sourcePath = resolve(outputDirectory, '../../web/mobile-settings.js');
const baseline = process.argv.includes('--baseline');
const results = [];
const usage = {
  totals: { lifetime_tokens: 0, input_tokens: 0, output_tokens: 0, peak_daily_tokens: 0, longest_chat_seconds: 0, current_streak: 0, longest_streak: 0, total_chats: 0, active_days: 0, models_used: 0, messages: 0, user_messages: 0, assistant_messages: 0 },
  heatmap: [], weekly_heatmap: [], cumulative_heatmap: [],
  insights: { most_used_model: null, average_tokens_per_chat: 0, average_messages_per_active_day: 0, user_message_share: 0, assistant_message_share: 0 },
  top_models: [], top_tools: [], period: { start_date: 0, end_date: 0, days: 365 }
};

async function createSettingsPage(options) {
  const session = await createMockedPage({ ...options, urlPath: '/c/mobile-preview-chat-1' });
  const stored = { ui: { textScale: options.textScale ?? 1, models: ['preview-default-model'], pinnedModels: [], showChatMenu: false }, keybindings: {} };
  const settingsWrites = [];
  await session.context.route('**/api/v1/users/user/settings**', async (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      const updated = JSON.parse(request.postData() ?? '{}');
      settingsWrites.push(updated);
      stored.ui = { ...stored.ui, ...updated.ui };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stored) });
  });
  await session.context.route('**/api/v1/users/usage?**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(usage) });
  });
  if (options.longChat) {
    await session.context.route('**/api/v1/chats/mobile-preview-chat-1', async (route) => {
      const chat = mockChat('mobile-preview-chat-1');
      const reply = chat.chat.history.messages['preview-assistant-message'];
      reply.content = Array.from({ length: 24 }, (_, index) => `Paragraph ${index + 1}. This synthetic conversation provides enough content to verify that opening and closing Settings keeps the existing reading position and unsent chat draft intact.`).join('\n\n');
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(chat) });
    });
  }
  if (options.apiKeys) {
    await session.context.route('**/api/v1/auths/', async (route) => {
      const previewUser = { ...user, permissions: { ...user.permissions, features: { ...user.permissions.features, api_keys: true } } };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(previewUser) });
    });
    await session.context.route('**/api/v1/auths/api_key', async (route) => {
      assert.equal(route.request().method(), 'GET', 'The API key fixture only permits reads');
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ api_key: 'preview-key-unused' }) });
    });
  }
  if (options.longChat || options.apiKeys) {
    await session.page.reload({ waitUntil: 'domcontentloaded' });
    await session.page.locator('#chat-input').waitFor({ state: 'visible' });
    await session.page.locator('#owui-chat-settings-button').waitFor({ state: 'visible' });
    await session.page.waitForTimeout(150);
  }
  if (options.dark) {
    await session.page.evaluate(() => {
      document.documentElement.classList.remove('light');
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    });
  }
  if (!baseline && existsSync(sourcePath)) await session.page.addScriptTag({ content: readFileSync(sourcePath, 'utf8') });
  return { ...session, settingsWrites, stored };
}

async function openSettings(page) {
  await page.locator('#owui-chat-settings-button').click();
  await page.locator('#settings-tabs-container').waitFor({ state: 'attached' });
  await page.waitForTimeout(300);
}

async function geometry(page) {
  return page.evaluate(() => {
    function size(element) {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, bottom: rect.bottom, fontSize: style.fontSize, overflow: style.overflow, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight };
    }
    const nav = document.querySelector('#settings-tabs-container');
    const panel = nav.parentElement;
    return {
      viewport: { width: innerWidth, height: visualViewport.height, top: visualViewport.offsetTop },
      portal: size(panel.parentElement), panel: size(panel), nav: size(nav),
      classes: { panel: panel.className, portal: panel.parentElement.className },
      search: size(document.querySelector('#search-input-settings-modal')),
      tabs: [...nav.querySelectorAll('button[role="tab"]')].map((button) => ({ name: button.textContent.trim(), controls: button.getAttribute('aria-controls'), selected: button.getAttribute('aria-selected'), ...size(button) })),
      visiblePanel: [...panel.querySelectorAll('[id^="tab-"]')].map((element) => ({ id: element.id, ...size(element) })),
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth
    };
  });
}

function categoryPanel(page, id) {
  if (id === 'tab-data-controls') return page.locator('#tab-chats');
  if (id === 'tab-usage') return page.locator('h2').filter({ hasText: 'Usage' }).first().locator('..').locator('..');
  return page.locator(`#${id}`);
}

async function nativeCategory(page, id) {
  const tab = page.locator(`#settings-tabs-container button[role="tab"][aria-controls="${id}"]`);
  await tab.click();
  await categoryPanel(page, id).waitFor({ state: 'visible' });
  assert.equal(await tab.getAttribute('aria-selected'), 'true');
}

async function baselineScenario() {
  const session = await createSettingsPage({ viewport: { width: 390, height: 844 } });
  try {
    const { page } = session;
    await openSettings(page);
    const initial = await geometry(page);
    await page.screenshot({ path: resolve(outputDirectory, 'mobile-settings-baseline.png') });
    const categories = await page.locator('#settings-tabs-container button[role="tab"]').evaluateAll((buttons) => buttons.map((button) => button.getAttribute('aria-controls')));
    const panels = [];
    for (const id of categories) {
      await nativeCategory(page, id);
      panels.push({ id, text: (await categoryPanel(page, id).innerText()).slice(0, 180) });
    }
    await nativeCategory(page, 'tab-general');
    await page.locator('#tab-general textarea').first().fill('Synthetic settings QA prompt');
    await page.locator('#tab-general').getByRole('button', { name: 'Save', exact: true }).click();
    await page.waitForFunction(() => document.body.innerText.includes('Settings saved successfully'));
    assert.equal(session.settingsWrites.at(-1).ui.system, 'Synthetic settings QA prompt');
    const errors = session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
    assert.deepEqual(errors, []);
    const result = { test: 'native-settings-baseline', initial, categories: panels, settingsWrites: session.settingsWrites, errors };
    results.push(result);
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await session.browser.close();
  }
}

async function assertMobileHome(page) {
  const panel = page.locator('.owui-settings-panel');
  await panel.waitFor({ state: 'visible' });
  assert.equal(await panel.getAttribute('data-settings-view'), 'home');
  await page.waitForFunction(() => {
    const tabs = [...document.querySelectorAll('#settings-tabs-container button[role="tab"]')];
    return tabs.length > 0 && tabs.every((tab) => tab.classList.contains('owui-settings-category-row'));
  });
  assert.equal(await page.locator('.owui-mobile-settings').count(), 1);
  const value = await geometry(page);
  assert.ok(Math.abs(value.panel.x) <= 1 && Math.abs(value.panel.width - value.viewport.width) <= 1);
  assert.ok(value.panel.y >= value.viewport.top - 1 && value.panel.bottom <= value.viewport.top + value.viewport.height + 1);
  assert.equal(value.horizontalOverflow, false);
  assert.ok(parseFloat(value.search.fontSize) >= 16);
  for (const row of value.tabs) {
    assert.ok(row.height >= 44, `${row.name} should have a comfortable tap target`);
    assert.ok(row.x >= 0 && row.x + row.width <= value.viewport.width + 1);
  }
  return value;
}

async function mobileBack(page) {
  await page.getByRole('button', { name: 'Back to Settings', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.owui-settings-panel')?.dataset.settingsView === 'home');
}

async function closeSettings(page) {
  await page.getByRole('button', { name: 'Close Settings', exact: true }).click();
  await page.locator('#settings-tabs-container').waitFor({ state: 'detached' });
}

async function compareVariants(page) {
  await page.screenshot({ path: resolve(outputDirectory, 'mobile-settings-phone-grouped.png') });
  const rows = page.locator('.owui-settings-category-row');
  const flatStyles = await rows.evaluateAll((buttons) => buttons.map((button) => {
    const originalStyle = button.getAttribute('style');
    button.style.setProperty('transition', 'none', 'important');
    button.style.setProperty('background', 'transparent', 'important');
    button.style.setProperty('border-radius', '0', 'important');
    button.style.setProperty('box-shadow', 'none', 'important');
    return originalStyle;
  }));
  await page.screenshot({ path: resolve(outputDirectory, 'mobile-settings-phone-flat.png') });
  await rows.evaluateAll((buttons, originals) => {
    for (let index = 0; index < buttons.length; index += 1) {
      const originalStyle = originals[index];
      if (originalStyle === null) buttons[index].removeAttribute('style');
      else buttons[index].setAttribute('style', originalStyle);
    }
  }, flatStyles);
  const inset = await page.addStyleTag({ content: `
    .owui-mobile-settings { padding:16px !important; background:rgba(0,0,0,.32) !important; }
    .owui-mobile-settings .owui-settings-panel { width:100% !important; height:100% !important; max-height:100% !important; margin:0 !important; border-radius:24px !important; box-shadow:0 16px 60px rgba(0,0,0,.3) !important; }
  ` });
  await page.screenshot({ path: resolve(outputDirectory, 'mobile-settings-phone-inset.png') });
  await inset.evaluate((element) => element.remove());
}

async function mobileScenario(name, options) {
  const session = await createSettingsPage({ ...options, longChat: true });
  try {
    const { page } = session;
    await page.locator('#chat-input').fill('Unsent chat draft stays here');
    await page.locator('#messages-container').evaluate((element) => { element.scrollTop = 300; });
    await page.waitForTimeout(100);
    const priorScroll = await page.locator('#messages-container').evaluate((element) => element.scrollTop);
    let expectedScroll = priorScroll;
    await openSettings(page);
    const home = await assertMobileHome(page);
    if (name === 'phone-dark') await compareVariants(page);
    const categories = home.tabs.map((tab) => tab.controls);
    for (const id of categories) {
      await nativeCategory(page, id);
      assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail');
      const panel = categoryPanel(page, id);
      assert.ok((await panel.innerText()).trim().length > 0);
      assert.equal(await page.getByRole('button', { name: 'Back to Settings', exact: true }).isVisible(), true);
      await mobileBack(page);
      await assertMobileHome(page);
    }
    const search = page.locator('#search-input-settings-modal');
    await search.fill('Audio');
    const matching = await page.locator('.owui-settings-category-row').allTextContents();
    assert.ok(matching.some((text) => text.includes('Audio')));
    assert.ok(matching.length < categories.length);
    await search.press('Enter');
    await page.locator('#tab-audio').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail');
    await mobileBack(page);
    assert.equal(await search.inputValue(), 'Audio');
    await search.focus();
    await search.press('Escape');
    assert.equal(await search.inputValue(), '');
    await assertMobileHome(page);
    await search.fill('zzzzsettingsmissing');
    assert.equal(await page.locator('.owui-settings-category-row').count(), 0);
    assert.match(await page.locator('.owui-settings-panel').innerText(), /No matches/);
    await search.fill('');
    await assertMobileHome(page);
    await nativeCategory(page, 'tab-general');
    await page.locator('#tab-general textarea').first().fill('Synthetic settings QA prompt');
    await mobileBack(page);
    await nativeCategory(page, 'tab-general');
    assert.equal(await page.locator('#tab-general textarea').first().inputValue(), 'Synthetic settings QA prompt');
    if (name === 'phone-dark') {
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.waitForTimeout(180);
      assert.equal(await page.locator('.owui-mobile-settings').count(), 0);
      assert.equal(await page.locator('#settings-tabs-container > button').first().innerText(), 'Back');
      const nativeSize = await geometry(page);
      assert.ok(nativeSize.panel.x > 0 && nativeSize.panel.width < 1280);
      assert.ok(nativeSize.nav.width < nativeSize.panel.width / 2);
      assert.equal(await page.locator('#tab-general textarea').first().inputValue(), 'Synthetic settings QA prompt');
      await page.setViewportSize(options.viewport);
      await page.waitForTimeout(180);
      await assertMobileHome(page);
      await nativeCategory(page, 'tab-general');
      assert.equal(await page.locator('#tab-general textarea').first().inputValue(), 'Synthetic settings QA prompt');
      // Native chat reflow changes its scroll offset during width changes. Close must preserve this new position.
      expectedScroll = await page.locator('#messages-container').evaluate((element) => element.scrollTop);
    }
    if (options.keyboard) {
      await page.evaluate(() => {
        Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 390 });
        Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 12 });
        visualViewport.dispatchEvent(new Event('resize'));
      });
      await page.waitForTimeout(180);
    }
    const fields = await page.locator('#tab-general select').evaluateAll((selects) => selects.map((element) => {
      const row = element.closest('.flex.items-center.justify-between');
      const label = row?.firstElementChild;
      const rect = element.getBoundingClientRect();
      return { name: element.getAttribute('aria-label'), x: rect.x, right: rect.right, width: rect.width, height: rect.height, label: label?.textContent.trim(), labelWidth: label?.clientWidth, labelScrollWidth: label?.scrollWidth, labelHeight: label?.clientHeight, labelLineHeight: label ? parseFloat(getComputedStyle(label).lineHeight) : null };
    }));
    for (const field of fields) {
      assert.ok(field.x >= 0 && field.right <= options.viewport.width + 1, `${field.name} control should fit`);
      assert.ok(field.height >= 44);
      assert.ok(field.labelScrollWidth <= field.labelWidth + 1, `${field.name} label should fit without clipping`);
      assert.ok(field.labelHeight <= field.labelLineHeight + 1, `${field.name} label should fit on one line`);
    }
    const save = page.locator('#tab-general').getByRole('button', { name: 'Save', exact: true });
    const panelBounds = await page.locator('.owui-settings-panel').boundingBox();
    const saveBounds = await save.boundingBox();
    assert.ok(saveBounds.height >= 44);
    assert.ok(saveBounds.y >= panelBounds.y && saveBounds.y + saveBounds.height <= panelBounds.y + panelBounds.height + 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: resolve(outputDirectory, `mobile-settings-${name}-general.png`) });
    await save.click();
    await page.waitForFunction(() => document.body.innerText.includes('Settings saved successfully'));
    assert.equal(session.settingsWrites.at(-1).ui.system, 'Synthetic settings QA prompt');
    await mobileBack(page);
    if (options.keyboard) {
      const keyboardHome = await assertMobileHome(page);
      assert.ok(keyboardHome.panel.bottom <= 403);
      await page.screenshot({ path: resolve(outputDirectory, `mobile-settings-${name}-search-keyboard.png`) });
    }
    await closeSettings(page);
    await page.waitForTimeout(150);
    const closeFocus = await page.evaluate(() => ({ tag: document.activeElement?.tagName, id: document.activeElement?.id }));
    assert.equal(await page.locator('#chat-input').innerText(), 'Unsent chat draft stays here');
    assert.equal(await page.locator('#messages-container').evaluate((element) => element.scrollTop), expectedScroll);
    await openSettings(page);
    await nativeCategory(page, 'tab-general');
    assert.equal(await page.locator('#tab-general textarea').first().inputValue(), 'Synthetic settings QA prompt');
    await closeSettings(page);
    const errors = session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
    assert.deepEqual(errors, []);
    results.push({ test: name, home, categories, panelBounds, saveBounds, fields, priorScroll, expectedScroll, closeFocus, settingsWrites: session.settingsWrites, errors });
    console.log(JSON.stringify({ test: name, categories: categories.length, panelBounds, saveBounds, saved: true, errors }, null, 2));
  } finally {
    await session.browser.close();
  }
}

async function wideScenario(name, options) {
  const session = await createSettingsPage(options);
  try {
    const { page } = session;
    await openSettings(page);
    const initial = await geometry(page);
    assert.equal(await page.locator('.owui-mobile-settings').count(), 0);
    assert.ok(initial.panel.x > 0 && initial.panel.width < options.viewport.width);
    assert.ok(initial.nav.width < initial.panel.width / 2);
    await nativeCategory(page, 'tab-interface');
    const form = page.locator('#tab-interface');
    await form.getByRole('button', { name: 'Save', exact: true }).click();
    await page.waitForFunction(() => document.body.innerText.includes('Settings saved successfully'));
    assert.ok(session.settingsWrites.length > 0);
    await page.screenshot({ path: resolve(outputDirectory, `mobile-settings-${name}.png`) });
    await page.locator('#settings-tabs-container > button').first().click();
    await page.locator('#settings-tabs-container').waitFor({ state: 'detached' });
    const errors = session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
    assert.deepEqual(errors, []);
    results.push({ test: name, initial, nativeInterfaceSave: true, errors });
    console.log(JSON.stringify({ test: name, panel: initial.panel, nativeInterfaceSave: true, errors }, null, 2));
  } finally {
    await session.browser.close();
  }
}

async function nestedMenuScenario() {
  const session = await createSettingsPage({ viewport: { width: 390, height: 844 }, apiKeys: true });
  try {
    const { page } = session;
    await openSettings(page);
    await nativeCategory(page, 'tab-account');
    await page.locator('#tab-account').getByRole('button', { name: 'Show', exact: true }).click();
    await page.locator('#tab-account button[aria-label="More"]').click();
    await page.locator('[role="menu"]:visible').waitFor({ state: 'visible' });
    const before = session.blockedMutations.length;
    await page.keyboard.press('Escape');
    await page.locator('[role="menu"]:visible').waitFor({ state: 'detached' });
    assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail');
    assert.equal(await page.locator('#tab-account').isVisible(), true);
    assert.equal(session.blockedMutations.length, before);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('.owui-settings-panel')?.dataset.settingsView === 'home');
    await closeSettings(page);
    results.push({ test: 'native-menu-escape', actions: 0 });
    console.log(JSON.stringify({ test: 'native-menu-escape', actions: 0 }));
  } finally {
    await session.browser.close();
  }
}

try {
  if (baseline) await baselineScenario();
  else if (process.argv.includes('--phone')) {
    await mobileScenario('phone-dark', { viewport: { width: 390, height: 844 }, dark: true });
  } else if (process.argv.includes('--remaining')) {
    await nestedMenuScenario();
    await mobileScenario('phone-dark', { viewport: { width: 390, height: 844 }, dark: true });
  } else {
    await mobileScenario('narrow-large-keyboard', { viewport: { width: 320, height: 740 }, textScale: 1.25, keyboard: true });
    await wideScenario('desktop', { viewport: { width: 1280, height: 900 }, hasTouch: false, isMobile: false });
    await wideScenario('duo', { viewport: { width: 860, height: 720 } });
    await nestedMenuScenario();
    await mobileScenario('phone-dark', { viewport: { width: 390, height: 844 }, dark: true });
  }
  writeFileSync(resolve(outputDirectory, `mobile-settings-${baseline ? 'baseline' : 'result'}.json`), JSON.stringify(results, null, 2));
} catch (error) {
  console.error(error.stack ?? error);
  process.exitCode = 1;
}
