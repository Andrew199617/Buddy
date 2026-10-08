import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage } from './mobile-ui-preview.mjs';

const outputDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const activitySource = readFileSync(resolve(outputDirectory, '../../web/model-activity.js'), 'utf8');
const responseSelector = '#message-preview-assistant-message #response-content-container';
const cursorSelector = `${responseSelector} > div > span.animate-pulse`;
const results = [];

function runtimeErrors(session) {
  return session.errors.filter((message) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(message));
}

async function startWaiting(options) {
  const session = await createMockedPage({
    ...options,
    activityFixture: 'waiting',
    urlPath: '/c/mobile-preview-chat-1'
  });
  const { page } = session;
  if (options.dark) {
    await page.evaluate(() => {
      document.documentElement.classList.remove('light');
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    });
  }
  await page.locator(cursorSelector).waitFor({ state: 'attached' });
  await page.addScriptTag({ content: activitySource });
  await page.locator('.owui-model-activity').waitFor({ state: 'visible' });
  return session;
}

async function activityScenario(name, options) {
  const session = await startWaiting(options);
  try {
    const { page } = session;
    const status = page.locator('.owui-model-activity');
    assert.match(await status.innerText(), /Waiting for response/i);
    assert.equal(await status.getAttribute('role'), 'status');
    assert.equal(await status.getAttribute('aria-live'), 'polite');
    const bounds = await status.boundingBox();
    assert.ok(bounds.width > 100 && bounds.height >= 20, 'Waiting status must be plainly visible');
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= options.viewport.width + 1, 'Status should fit the viewport');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: resolve(outputDirectory, `model-activity-${name}-waiting.png`) });
    if (name === 'phone-dark') {
      const mutationsBeforeMenu = session.blockedMutations.length;
      await page.locator('#chat-context-menu-button').click();
      await page.locator('.owui-chat-menu-portal').waitFor({ state: 'visible' });
      assert.equal(await status.isVisible(), true, 'The chat menu must preserve the activity indicator');
      await page.keyboard.press('Escape');
      await page.locator('.owui-chat-menu-portal').waitFor({ state: 'detached' });
      assert.equal(await status.isVisible(), true);
      assert.equal(session.blockedMutations.length, mutationsBeforeMenu, 'Opening and closing the chat menu must not send API writes');
    }
    await page.waitForTimeout(1150);
    const elapsedHidden = await status.locator('[aria-hidden="true"]').allTextContents();
    assert.ok(elapsedHidden.some((value) => /[1-9]/.test(value)), 'Elapsed wait must update outside the live region');
    await page.evaluate((selector) => {
      const response = document.querySelector(selector);
      const text = document.createElement('div');
      text.className = 'markdown-prose';
      text.textContent = 'Here is the first part of the response.';
      response.prepend(text);
    }, responseSelector);
    await page.waitForFunction(() => /Working/i.test(document.querySelector('.owui-model-activity')?.textContent ?? ''));
    await page.screenshot({ path: resolve(outputDirectory, `model-activity-${name}-working.png`) });
    await page.locator(cursorSelector).evaluate((cursor) => cursor.parentElement.remove());
    await page.locator('.owui-model-activity').waitFor({ state: 'detached' });
    await page.waitForTimeout(1150);
    assert.equal(await status.count(), 0, 'Completed response must not regain an activity indicator');
    assert.deepEqual(runtimeErrors(session), []);
    results.push({ test: name, waitingBounds: bounds, requests: session.requests.length });
  } finally {
    await session.browser.close();
  }
}

async function cancellationScenario() {
  const session = await startWaiting({ viewport: { width: 390, height: 844 }, dark: true });
  try {
    const { page } = session;
    const stop = page.getByRole('button', { name: 'Stop', exact: true });
    await stop.click();
    await page.locator(cursorSelector).waitFor({ state: 'detached' });
    await page.locator('.owui-model-activity').waitFor({ state: 'detached' });
    assert.ok(session.blockedMutations.some((request) => /tasks.*stop/.test(request)), 'Stop should exercise the native mocked stop endpoint');
    assert.deepEqual(runtimeErrors(session), []);
    results.push({ test: 'native-cancel', mutations: session.blockedMutations });
  } finally {
    await session.browser.close();
  }
}

async function continuationScenario() {
  const session = await startWaiting({ viewport: { width: 390, height: 844 } });
  try {
    const { page } = session;
    await page.evaluate((selector) => {
      const response = document.querySelector(selector);
      const previous = document.createElement('div');
      previous.className = 'markdown-prose';
      previous.textContent = 'Previous answer that is now being continued.';
      response.prepend(previous);
    }, responseSelector);
    await page.waitForFunction(() => /Working/i.test(document.querySelector('.owui-model-activity')?.textContent ?? ''));
    await page.locator(cursorSelector).evaluate((cursor) => {
      const next = cursor.cloneNode(true);
      cursor.replaceWith(next);
    });
    await page.waitForFunction(() => /Waiting for response/i.test(document.querySelector('.owui-model-activity')?.textContent ?? ''));
    assert.equal(await page.locator('.owui-model-activity').count(), 1, 'Continuation must replace the activity state');
    assert.equal(await page.locator('.owui-model-activity-elapsed').innerText(), '', 'Continuation starts a fresh elapsed wait');
    await page.evaluate((selector) => {
      const thinking = document.createElement('div');
      thinking.className = 'shimmer';
      thinking.textContent = 'Thinking…';
      document.querySelector(selector).prepend(thinking);
    }, responseSelector);
    await page.waitForFunction(() => /Thinking/i.test(document.querySelector('.owui-model-activity')?.textContent ?? ''));
    await page.screenshot({ path: resolve(outputDirectory, 'model-activity-thinking.png') });
    await page.locator(`${responseSelector} .shimmer`).evaluate((element) => element.remove());
    await page.waitForFunction(() => /Waiting for response/i.test(document.querySelector('.owui-model-activity')?.textContent ?? ''));
    await page.evaluate(() => {
      const buttons = document.createElement('div');
      buttons.className = 'buttons';
      const copy = document.createElement('button');
      copy.className = 'copy-response-button';
      buttons.append(copy);
      document.querySelector('#message-preview-assistant-message').append(buttons);
    });
    await page.locator('.owui-model-activity').waitFor({ state: 'detached' });
    assert.equal(await page.locator('#message-preview-assistant-message').getAttribute('aria-busy'), null);
    assert.equal(await page.locator('.owui-model-activity-cursor').count(), 0);
    assert.deepEqual(runtimeErrors(session), []);
    results.push({ test: 'continuation-thinking-completed-footer' });
  } finally {
    await session.browser.close();
  }
}

async function navigationScenario() {
  const session = await startWaiting({ viewport: { width: 390, height: 844 } });
  try {
    const { page } = session;
    await page.locator('button[aria-label="New Chat"]:visible').first().click();
    await page.waitForURL('http://localhost:8080/');
    await page.locator('.owui-model-activity').waitFor({ state: 'detached' });
    await page.waitForTimeout(1150);
    assert.equal(await page.locator('.owui-model-activity').count(), 0);
    assert.deepEqual(runtimeErrors(session), []);
    results.push({ test: 'native-navigation' });
  } finally {
    await session.browser.close();
  }
}

async function completedScenario() {
  const session = await createMockedPage({ urlPath: '/c/mobile-preview-chat-1' });
  try {
    await session.page.addScriptTag({ content: activitySource });
    await session.page.waitForTimeout(150);
    assert.equal(await session.page.locator('.owui-model-activity').count(), 0);
    assert.deepEqual(runtimeErrors(session), []);
    results.push({ test: 'completed-history' });
  } finally {
    await session.browser.close();
  }
}

async function keyboardScenario() {
  const session = await startWaiting({ viewport: { width: 390, height: 844 } });
  try {
    const { page } = session;
    await page.locator('#chat-input').fill('My draft stays visible');
    await page.evaluate(() => {
      Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 390 });
      Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 12 });
      visualViewport.dispatchEvent(new Event('resize'));
    });
    await page.waitForTimeout(200);
    const composer = await page.locator('#message-input-container').boundingBox();
    assert.ok(composer.y >= 12 && composer.y + composer.height <= 403);
    assert.equal(await page.locator('.owui-model-activity').isVisible(), true);
    assert.equal(await page.locator('#chat-input').innerText(), 'My draft stays visible');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: resolve(outputDirectory, 'model-activity-keyboard.png') });
    assert.deepEqual(runtimeErrors(session), []);
    results.push({ test: 'keyboard', composer });
  } finally {
    await session.browser.close();
  }
}

try {
  await activityScenario('phone-dark', { viewport: { width: 390, height: 844 }, dark: true });
  await activityScenario('narrow-large', { viewport: { width: 320, height: 740 }, textScale: 1.25 });
  await activityScenario('desktop', { viewport: { width: 1280, height: 900 }, hasTouch: false, isMobile: false });
  await continuationScenario();
  await cancellationScenario();
  await keyboardScenario();
  await navigationScenario();
  await completedScenario();
  writeFileSync(resolve(outputDirectory, 'model-activity-result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} catch (error) {
  console.error(error.stack ?? error);
  process.exitCode = 1;
}
