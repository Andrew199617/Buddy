import assert from 'node:assert/strict';
import { resolve } from 'node:path';
import { createMockedPage, mockChat, outputDirectory } from './mobile-ui-preview.mjs';

const chatPath = '/c/mobile-preview-chat-1';
const results = [];
function runtimeErrors(session) {
 return session.errors.filter((error) => !error.includes('ERR_BLOCKED_BY_CLIENT'));
}
async function phoneCheck(width, textScale = 1, dark = false) {
 const session = await createMockedPage({ viewport: { width, height: 844 }, textScale, urlPath: chatPath, contextCapacity: 32768, compactionThreshold: 8000 });
 const { page } = session;
 try {
  if (dark) await page.evaluate(() => document.documentElement.classList.add('dark'));
  await page.locator('.owui-run-footer').waitFor({ state: 'visible' });
  assert.match(await page.locator('.owui-run-footer').innerText(), /14\.2K tokens/);
  assert.match(await page.locator('#owui-context-info-button').innerText(), /5\.2K.*32\.8K.*16%/);
  await page.locator('#chat-input').fill('A draft that must remain visible after opening and closing details.');
  const initial = await page.evaluate(() => ({
   bodyOverflow: document.documentElement.scrollWidth > innerWidth,
   editorFont: parseFloat(getComputedStyle(document.getElementById('chat-input')).fontSize),
   footerFont: parseFloat(getComputedStyle(document.querySelector('.owui-run-footer')).fontSize),
   actions: Array.from(document.querySelectorAll('#messages-container .buttons button')).filter((button) => button.getClientRects().length).map((button) => ({ width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height }))
  }));
  assert.equal(initial.bodyOverflow, false);
  assert.ok(initial.editorFont >= 16);
  assert.ok(initial.footerFont >= 13);
  assert.ok(initial.actions.length > 0);
  assert.ok(initial.actions.every((action) => action.width >= 40 && action.height >= 40));
  await page.locator('#owui-context-info-button').click();
  const contextText = await page.locator('.owui-usage-panel').innerText();
  assert.match(contextText, /Context capacity\s+32,768 tokens/);
  assert.match(contextText, /Compaction trigger\s+8,000 tokens/);
  const bounds = await page.locator('.owui-usage-panel').boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 845);
  assert.equal(await page.locator('.app').evaluate((app) => app.inert), true);
  await page.screenshot({ path: resolve(outputDirectory, `final-context-${width}-${textScale}${dark ? '-dark' : ''}.png`) });
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.app').evaluate((app) => app.inert), false);
  assert.equal(await page.locator('#chat-input').innerText(), 'A draft that must remain visible after opening and closing details.');
  await page.locator('.owui-run-footer').click();
  const runText = await page.locator('.owui-usage-panel').innerText();
  assert.match(runText, /Input\s+13,000 tokens/);
  assert.match(runText, /Output\s+1,200 tokens/);
  assert.match(runText, /Total for this run\s+14,200 tokens/);
  assert.match(runText, /Duration\s+7 s/);
  await page.locator('button[aria-label="Close details"]').click();
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('owui-run-footer')), true);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('.owui-run-footer').waitFor({ state: 'visible' });
  assert.match(await page.locator('.owui-run-footer').innerText(), /14\.2K tokens/);
  assert.deepEqual(runtimeErrors(session), []);
  results.push({ test: 'phone', width, textScale, dark, initial });
 } finally { await session.browser.close(); }
}

async function keyboardCheck() {
 const session = await createMockedPage({ urlPath: chatPath });
 try {
  const { page } = session;
  await page.locator('#chat-input').fill('First line\nSecond line\nThird line\nFourth line\nFifth line\nSixth line\nSeventh line\nEighth line\nNinth line\nTenth line');
  await page.evaluate(() => {
   Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 390 });
   Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 12 });
   visualViewport.dispatchEvent(new Event('resize'));
  });
  await page.waitForTimeout(150);
  const composer = await page.locator('#message-input-container').boundingBox();
  assert.ok(composer.y >= 12 && composer.y + composer.height <= 403);
  assert.match(await page.locator('#chat-input').innerText(), /Tenth line/);
  await page.locator('#owui-context-info-button').click();
  const panel = await page.locator('.owui-usage-panel').boundingBox();
  assert.ok(panel.y >= 12 && panel.y + panel.height <= 403);
  await page.keyboard.press('Escape');
  assert.match(await page.locator('#chat-input').innerText(), /Tenth line/);
  await page.locator('#model-selector-model-button').click();
  await page.locator('.owui-model-picker-phone').waitFor({ state: 'visible' });
  const picker = await page.locator('.owui-model-picker-panel').boundingBox();
  assert.ok(picker.y >= 12 && picker.y + picker.height <= 403);
  await page.locator('.owui-model-picker-header button').last().click();
  assert.match(await page.locator('#chat-input').innerText(), /Tenth line/);
  assert.deepEqual(runtimeErrors(session), []);
  results.push({ test: 'keyboard', composer, panel, picker });
 } finally { await session.browser.close(); }
}

async function duoCheck() {
 const session = await createMockedPage({ viewport: { width: 860, height: 720 }, usage: false });
 try {
  const { page } = session;
  await page.evaluate(() => {
   window.viewport = { segments: [
    { left: 0, top: 0, right: 410, bottom: 720, width: 410, height: 720 },
    { left: 450, top: 0, right: 860, bottom: 720, width: 410, height: 720 }
   ] };
  });
  await page.locator('#model-selector-model-button').click();
  await page.locator('.owui-model-picker-panel').waitFor({ state: 'visible' });
  await page.waitForTimeout(100);
  const panel = await page.locator('.owui-model-picker-panel').boundingBox();
  assert.equal(await page.locator('.owui-model-picker-phone').count(), 0);
  assert.equal(await page.locator('.app').evaluate((app) => app.inert), false);
  assert.ok(panel.x >= 450 && panel.x + panel.width <= 860);
  assert.deepEqual(runtimeErrors(session), []);
  results.push({ test: 'duo', panel });
 } finally { await session.browser.close(); }
}

async function desktopCheck() {
 const session = await createMockedPage({ viewport: { width: 1440, height: 1000 }, hasTouch: false, isMobile: false, urlPath: chatPath });
 try {
  const { page } = session;
  await page.locator('.owui-run-footer').waitFor({ state: 'visible' });
  await page.locator('.owui-run-footer').hover();
  await page.locator('.owui-usage-panel').waitFor({ state: 'visible' });
  assert.equal(await page.locator('.owui-usage-sheet').count(), 0);
  assert.match(await page.locator('.owui-usage-panel').innerText(), /13,000 tokens/);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Open Sidebar', exact: true }).click();
  await page.waitForTimeout(250);
  const firstChat = page.locator('#sidebar-chat-item').first();
  await firstChat.hover();
  await page.locator('.owui-usage-preview').waitFor({ state: 'visible' });
  assert.match(await page.locator('.owui-usage-preview').innerText(), /14\.2K/);
  assert.match(await page.locator('.owui-usage-preview').innerText(), /input.*output/i);
  assert.deepEqual(runtimeErrors(session), []);
  results.push({ test: 'desktop hover' });
 } finally { await session.browser.close(); }
}

async function continuationCheck() {
 const session = await createMockedPage({ urlPath: chatPath });
 try {
  const { page } = session;
  await page.locator('.owui-run-footer').waitFor({ state: 'visible' });
  const updated = mockChat('mobile-preview-chat-1');
  const reply = updated.chat.history.messages['preview-assistant-message'];
  Object.assign(reply.meta.local_run, { input_tokens: 12000, output_tokens: 777, total_tokens: 12777, run_id: 'continued-preview-run' });
  Object.assign(reply.usage, { input_tokens: 12000, output_tokens: 777, total_tokens: 12777 });
  await page.route('**/api/v1/chats/mobile-preview-chat-1', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(updated) }));
  await page.waitForTimeout(1100);
  await page.evaluate(() => {
   const actions = document.querySelector('#message-preview-assistant-message .buttons');
   window.__doneControls = Array.from(actions.querySelectorAll('time, button[id^="info-"]')).map((element) => ({ element, parent: element.parentNode }));
   for (const item of window.__doneControls) item.element.remove();
  });
  await page.waitForTimeout(100);
  assert.equal(await page.locator('.owui-run-footer').count(), 0);
  await page.evaluate(() => {
   for (const item of window.__doneControls) item.parent.appendChild(item.element);
  });
  await page.waitForTimeout(850);
  assert.match(await page.locator('.owui-run-footer').innerText(), /12\.8K tokens/);
  assert.deepEqual(runtimeErrors(session), []);
  results.push({ test: 'continuation refresh' });
 } finally { await session.browser.close(); }
}

if (!process.argv.includes('--remaining')) {
 await phoneCheck(320, 1.25);
 await phoneCheck(390, 1, true);
 await phoneCheck(540);
 await keyboardCheck();
 await duoCheck();
}
await desktopCheck();
await continuationCheck();
console.log(JSON.stringify({ passed: results.length, results }, null, 2));
