import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage, mockChat } from './mobile-ui-preview.mjs';
const directory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const source = readFileSync(resolve(directory, '../../web/chat-header.js'), 'utf8');
const results = [];

async function runScenario(options) {
 const session = await createMockedPage({ ...options, urlPath:'/c/mobile-preview-chat-1' });
 const { page, context } = session;
 try {
  const savedChat = mockChat('mobile-preview-chat-1', options);
  const savedReply = savedChat.chat.history.messages['preview-assistant-message'];
  savedReply.content += '\n\n' + Array.from({ length:30 }, (_, index) => `- Planning step ${index + 1}: preserve this long response and the place where the reader stopped.`).join('\n');
  await context.route('**/api/v1/chats/mobile-preview-chat-1', async (route) => {
   await route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(savedChat) });
  });
  await page.reload({ waitUntil:'domcontentloaded' });
  await page.locator('#chat-input').waitFor();
  await page.locator('#message-preview-assistant-message .markdown-prose').waitFor({ state:'attached' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  await page.waitForFunction(() => window.__owuiChatHeader === true, null, { timeout:5000 });
  await page.addScriptTag({ content:source });
  if (options.dark) await page.evaluate(() => document.documentElement.classList.add('dark'));
  if (options.keyboard || options.segments) await page.evaluate(({ keyboard, segments, width, height }) => {
   const state = { width, height:keyboard ? 390 : height, offsetTop:0, offsetLeft:0, scale:1 };
   for (const name of Object.keys(state)) Object.defineProperty(window.visualViewport, name, { configurable:true, get:() => state[name] });
   if (segments) Object.defineProperty(window, 'viewport', { configurable:true, value:{ segments } });
   window.visualViewport.dispatchEvent(new Event('resize'));
  }, { ...options, ...options.viewport });
  await page.locator('.owui-chat-header').waitFor();
  await page.locator('#chat-input').fill('Keep this unsent draft while opening Settings.');
  await page.locator('.owui-header-title').evaluate((title) => { title.textContent = 'Planning a very long mobile conversation with a detailed project name'; });
  await page.waitForTimeout(150);
  const geometry = await page.locator('.owui-chat-header').evaluate((header) => {
   const rect = header.getBoundingClientRect();
   const controls = Array.from(header.querySelectorAll('.owui-header-control')).filter((control) => getComputedStyle(control).display !== 'none').map((control) => {
    const box = control.getBoundingClientRect();
    return { name:control.getAttribute('aria-label'), left:box.left, right:box.right, top:box.top, bottom:box.bottom, width:box.width, height:box.height, icon:control.querySelector('svg')?.getBoundingClientRect().width };
   });
   const title = header.querySelector('.owui-header-title');
   const titleRect = title.getBoundingClientRect();
   const messages = document.getElementById('messages-container');
   return { width:rect.width, height:rect.height, top:rect.top, bottom:rect.bottom, controls, background:getComputedStyle(header).backgroundColor, backdrop:getComputedStyle(header).backdropFilter, title:{ left:titleRect.left, right:titleRect.right, top:titleRect.top, bottom:titleRect.bottom, width:titleRect.width, overflow:getComputedStyle(title).textOverflow }, messagesTop:messages.getBoundingClientRect().top, measured:document.documentElement.style.getPropertyValue('--owui-chat-header-height'), hiddenDelete:getComputedStyle(document.getElementById('delete-chat-button')).display, horizontalOverflow:document.documentElement.scrollWidth > innerWidth };
  });
  assert.equal(geometry.hiddenDelete, 'none');
  assert.equal(geometry.horizontalOverflow, false);
  assert.equal(geometry.title.overflow, 'ellipsis');
  assert.ok(geometry.messagesTop >= geometry.bottom - 1, 'Messages scroll below the header');
  assert.ok(geometry.controls.every((control) => control.top >= geometry.top && control.bottom <= geometry.bottom + 1));
  assert.ok(geometry.controls.every((control) => control.left >= 0 && control.right <= options.viewport.width + 1));
  const touch = options.hasTouch !== false;
  assert.ok(geometry.controls.every((control) => control.width >= (touch ? 44 : 36)));
  assert.ok(geometry.controls.every((control) => control.icon >= (touch ? 22 : 20)));
  const gear = geometry.controls.find((control) => control.name === 'Settings');
  const advanced = geometry.controls.find((control) => control.name === 'Controls');
  assert.ok(gear.left > advanced.left, 'Settings stays to the right of Advanced');
  assert.ok(geometry.height <= (touch ? 58 : 48), 'Narrow header stays one row');
  await page.screenshot({ path:resolve(directory, `${options.name}.png`) });

  const scrollBefore = await page.locator('#messages-container').evaluate((messages) => {
   messages.scrollTop = Math.min(500, messages.scrollHeight - messages.clientHeight - 100);
   messages.dispatchEvent(new Event('scroll'));
   return messages.scrollTop;
  });
  assert.ok(scrollBefore > 100, 'The saved long reply has a meaningful reading position');
  await page.waitForTimeout(200);
  await page.locator('#owui-chat-settings-button').click();
  await page.locator('#search-input-settings-modal').waitFor({ state:'visible', timeout:5000 });
  await page.waitForFunction(() => !new URL(location.href).searchParams.has('settings'), null, { timeout:5000 });
  assert.equal(new URL(page.url()).pathname, '/c/mobile-preview-chat-1');
  assert.match(await page.locator('#chat-input').innerText(), /Keep this unsent draft/);
  const scrollDuring = await page.locator('#messages-container').evaluate((messages) => messages.scrollTop);
  assert.ok(Math.abs(scrollDuring - scrollBefore) <= 1, 'Settings preserves the message reading position while open');
  await page.keyboard.press('Escape');
  await page.locator('#search-input-settings-modal').waitFor({ state:'detached' });
  await page.waitForTimeout(200);
  const scrollAfter = await page.locator('#messages-container').evaluate((messages) => messages.scrollTop);
  assert.ok(Math.abs(scrollAfter - scrollBefore) <= 1, 'Closing Settings preserves the message reading position');
  assert.match(await page.locator('#chat-input').innerText(), /Keep this unsent draft/);

  const controlsButton = page.locator('.owui-chat-header button[aria-label="Controls"]');
  await controlsButton.click();
  if (options.viewport.width < 1024) {
   await page.locator('.modal').waitFor({ state:'visible' });
   assert.ok(await page.locator('.modal').getByText('Controls', { exact:true }).count() > 0);
   await page.locator('.modal button[aria-label="Close"]').click();
   await page.locator('.modal').waitFor({ state:'detached' });
  } else {
   await page.locator('#controls-container').waitFor({ state:'visible' });
   await page.locator('#controls-container button[aria-label="Close"]').click();
   await page.locator('#controls-container').waitFor({ state:'detached' });
  }
  if (options.viewport.width < 768) {
   await page.locator('.owui-chat-header #sidebar-toggle-button').click();
   await page.locator('#sidebar-chat-item').first().waitFor();
   await page.waitForTimeout(150);
   const sidebar = await page.locator('#sidebar').boundingBox();
   assert.ok(sidebar.x < 1, 'Native sidebar opens');
   await page.locator('#sidebar button[aria-label="Close Sidebar"]').first().click();
   await page.locator('.owui-chat-header #sidebar-toggle-button').waitFor();
  }
  const errors = session.errors.filter((error) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(error));
  assert.deepEqual(errors, []);
  results.push({ name:options.name, geometry, nativeSettings:true, nativeAdvanced:true, draftPreserved:true, scrollBefore, scrollDuring, scrollAfter, errors });
 } finally { await session.browser.close(); }
}

await runScenario({ name:'chat-header-phone-dark', viewport:{ width:390, height:844 }, dark:true });
await runScenario({ name:'chat-header-narrow-large-keyboard', viewport:{ width:320, height:740 }, textScale:1.25, keyboard:true });
await runScenario({ name:'chat-header-desktop', viewport:{ width:1280, height:900 }, hasTouch:false, isMobile:false });
await runScenario({ name:'chat-header-duo', viewport:{ width:1114, height:720 }, segments:[{ left:0,top:0,right:540,bottom:720,width:540,height:720 }, { left:574,top:0,right:1114,bottom:720,width:540,height:720 }] });
writeFileSync(resolve(directory, 'chat-header-results.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify({ passed:results.length, results }, null, 2));
