import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage } from './mobile-ui-preview.mjs';

const directory = resolve(dirname(fileURLToPath(import.meta.url)), '../.qa');
const source = readFileSync(resolve(directory, '../../web/chat-actions-menu.js'), 'utf8');
const results = [];

async function setViewport(page, width, height, top = 0, segments = null) {
 await page.evaluate(({ width, height, top, segments }) => {
  window.__chatActionsViewport = { width, height, offsetLeft:0, offsetTop:top, scale:1 };
  for (const name of ['width', 'height', 'offsetLeft', 'offsetTop', 'scale']) {
   Object.defineProperty(window.visualViewport, name, { configurable:true, get:() => window.__chatActionsViewport[name] });
  }
  if (segments) Object.defineProperty(window, 'viewport', { configurable:true, value:{ segments } });
  window.visualViewport.dispatchEvent(new Event('resize'));
 }, { width, height, top, segments });
 await page.waitForTimeout(100);
}

async function inspectPanel(page) {
 return page.locator('.owui-chat-menu-portal').evaluate((portal) => {
  const panel = portal.querySelector('.owui-chat-actions-panel');
  const rect = portal.getBoundingClientRect();
  const actions = Array.from(panel.querySelectorAll('.owui-chat-action')).map((button) => ({
   text:button.textContent.trim(), height:button.getBoundingClientRect().height,
   font:getComputedStyle(button).fontSize, color:getComputedStyle(button).color,
   icon:button.querySelector('svg')?.getBoundingClientRect().width ?? null,
   delete:button.classList.contains('owui-chat-delete')
  }));
  return {
   rect:{ left:rect.left, right:rect.right, top:rect.top, bottom:rect.bottom, width:rect.width, height:rect.height },
   heading:panel.querySelector('h2')?.textContent, title:panel.querySelector('.owui-chat-actions-heading p')?.textContent,
   background:getComputedStyle(panel).backgroundColor, radius:getComputedStyle(panel).borderRadius,
   scrollHeight:portal.scrollHeight, clientHeight:portal.clientHeight, actions
  };
 });
}

async function runScenario(options) {
 const session = await createMockedPage({ ...options, urlPath:'/c/mobile-preview-chat-1' });
 const { page, context } = session;
 try {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.addScriptTag({ content:source });
  if (options.dark) await page.evaluate(() => document.documentElement.classList.add('dark'));
  if (options.keyboard) await setViewport(page, options.viewport.width, 430);
  if (options.segments) await setViewport(page, options.viewport.width, options.viewport.height, 0, options.segments);
  await page.locator('#chat-context-menu-button').click();
  await page.locator('.owui-chat-menu-portal .owui-chat-actions-heading').waitFor();
  await page.waitForTimeout(250);
  const before = await inspectPanel(page);
  assert.equal(before.heading, 'Chat actions');
  assert.match(before.title, /Preview Project Planning/);
  assert.equal(before.radius, '20px');
  const touch = options.hasTouch !== false;
  assert.ok(before.actions.every((action) => action.height >= (touch ? 44 : 38) - 0.1));
  assert.ok(before.actions.some((action) => action.delete));
  assert.ok(before.rect.left >= 11);
  assert.ok(before.rect.right <= options.viewport.width - 11);
  assert.ok(before.rect.top >= 11);
  assert.ok(before.rect.bottom <= (options.keyboard ? 430 : options.viewport.height) - 10);
  if (options.segments) assert.ok(before.rect.right <= 540 - 11);
  await page.screenshot({ path:resolve(directory, `${options.name}-menu.png`) });

  const nativeActions = await page.locator('.owui-chat-menu-portal').evaluate((portal) => Array.from(portal.querySelectorAll('button')).filter((button) => button.classList.contains('owui-chat-action')).map((button) => button.textContent.trim()));
  assert.ok(nativeActions.includes('Share'));
  assert.ok(nativeActions.includes('Copy'));
  assert.ok(nativeActions.includes('Archive'));
  assert.ok(nativeActions.includes('Delete'));
  assert.ok(await page.locator('.owui-chat-menu-portal input').count() > 0, 'Native tags input is retained');

  const download = page.locator('.owui-chat-menu-portal .owui-chat-submenu-trigger').filter({ hasText:'Download' });
  await download.click();
  await page.waitForTimeout(350);
  await page.locator('.owui-chat-submenu-portal').waitFor({ timeout:5000 });
  await page.waitForTimeout(200);
  const submenu = await page.locator('.owui-chat-submenu-portal').evaluate((portal) => {
   const rect = portal.getBoundingClientRect();
   return { parentOwned:!!portal.parentElement.closest('.owui-chat-menu-portal'), left:rect.left, right:rect.right, bottom:rect.bottom, labels:Array.from(portal.querySelectorAll('button')).map((button) => button.textContent.trim()), widths:Array.from(portal.querySelectorAll('button')).map((button) => button.getBoundingClientRect().width) };
  });
  assert.equal(submenu.parentOwned, true);
  assert.equal(submenu.labels.length, 3);
  assert.ok(submenu.labels.includes('Plain text (.txt)'));
  assert.ok(submenu.left >= 11 && submenu.right <= options.viewport.width - 11);
  assert.ok(submenu.bottom <= (options.keyboard ? 430 : options.viewport.height) - 10);
  await page.screenshot({ path:resolve(directory, `${options.name}-download.png`) });
  const downloadEvent = page.waitForEvent('download');
  await page.locator('.owui-chat-submenu-portal button').filter({ hasText:'Plain text (.txt)' }).click();
  const file = await downloadEvent;
  assert.match(file.suggestedFilename(), /\.txt$/);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.owui-chat-menu-portal').count(), 1, 'Escape from submenu preserves the parent');
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains('owui-chat-submenu-trigger')), true);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'chat-context-menu-button', 'Main Escape returns focus to native trigger');

  await page.locator('#chat-context-menu-button').click();
  await page.locator('.owui-chat-menu-portal').waitFor();
  await page.waitForTimeout(200);
  await page.locator('.owui-chat-menu-portal').focus();
  await page.keyboard.press('ArrowDown');
  const focusedAction = await page.evaluate(() => document.activeElement.classList.contains('owui-chat-action'));
  assert.equal(focusedAction, true);
  await page.locator('#chat-copy-button').click();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  assert.match(clipboard, /mobile app/);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
  await page.locator('#chat-context-menu-button').click();
  await page.locator('.owui-chat-menu-portal').waitFor();
  await page.locator('#chat-share-button').click();
  await page.waitForTimeout(200);
  assert.ok(await page.getByText('Share Chat', { exact:true }).count() > 0 || await page.getByText('Share chat', { exact:true }).count() > 0, 'Native share modal still opens');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
  results.push({ name:options.name, before, submenu, focusedAction, blockedMutations:session.blockedMutations, errors:session.errors.filter((error) => !error.includes('net::ERR_BLOCKED_BY_CLIENT')) });
 } finally { await session.browser.close(); }
}

async function runOwnedMenuChecks() {
 const session = await createMockedPage({ viewport:{ width:320, height:740 }, textScale:1.25, urlPath:'/c/mobile-preview-chat-1' });
 const { page, context } = session;
 try {
  const folders = [{ id:'preview-folder-1', name:'App planning', parent_id:null, items:[], data:{}, meta:{}, created_at:1, updated_at:1 }];
  await context.route('**/api/v1/folders/**', async (route) => {
   if (new URL(route.request().url()).pathname === '/api/v1/folders/') {
    await route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(folders) });
   } else await route.fallback();
  });
  await page.reload({ waitUntil:'domcontentloaded' });
  await page.locator('#chat-input').waitFor();
  await page.addScriptTag({ content:source });
  await setViewport(page, 320, 430);
  await page.locator('#chat-context-menu-button').click();
  await page.locator('.owui-chat-menu-portal').waitFor();
  await page.waitForTimeout(250);
  const move = page.locator('.owui-chat-menu-portal .owui-chat-submenu-trigger').filter({ hasText:'Move' });
  await move.focus();
  await page.keyboard.press('ArrowRight');
  await page.locator('.owui-chat-submenu-portal').waitFor();
  await page.waitForTimeout(250);
  const folder = page.locator('.owui-chat-submenu-portal .owui-chat-action').filter({ hasText:'App planning' });
  await folder.focus();
  await page.keyboard.press('Enter');
  await page.waitForTimeout(250);
  assert.ok(session.blockedMutations.some((request) => request.includes('/folder')), 'Native Move callback uses the mocked folder API');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.owui-chat-menu-portal').count(), 1);
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains('owui-chat-submenu-trigger')), true);
  await page.keyboard.press('Escape');
  await page.locator('.owui-chat-menu-portal').waitFor({ state:'detached', timeout:3000 });
  await page.waitForFunction(() => document.activeElement.id === 'chat-context-menu-button', null, { timeout:3000 });
  assert.equal(await page.evaluate(() => document.activeElement.id), 'chat-context-menu-button');

  // Move success toasts pause their dismissal while the pointer remains over them.
  await page.mouse.move(319, 739);
  await page.waitForTimeout(4200);
  await page.locator('#sidebar-toggle-button').click();
  await page.locator('#sidebar-chat-item-menu').first().waitFor();
  const sidebarTrigger = page.locator('#sidebar-chat-item-menu').first().locator('button').first();
  await sidebarTrigger.click();
  await page.locator('.owui-chat-menu-portal').waitFor();
  await page.waitForTimeout(250);
  const panel = await inspectPanel(page);
  assert.ok(panel.actions.some((action) => action.text === 'Chat details'));
  assert.ok(panel.actions.some((action) => action.text === 'Rename'));
  assert.ok(panel.actions.some((action) => action.text === 'Mark as unread'));
  assert.ok(panel.actions.every((action) => action.height >= 44));
  assert.ok(panel.actions.some((action) => action.delete));
  await page.locator('.owui-chat-menu-portal').focus();
  await page.keyboard.press('End');
  await page.waitForTimeout(150);
  const focused = await page.evaluate(() => {
   const button = document.activeElement;
   const portal = button.closest('.owui-chat-menu-portal');
   const item = button.getBoundingClientRect();
   const bounds = portal.getBoundingClientRect();
   return { text:button.textContent.trim(), visible:item.top >= bounds.top - 1 && item.bottom <= bounds.bottom + 1, scrollTop:portal.scrollTop, item:{ top:item.top, bottom:item.bottom }, bounds:{ top:bounds.top, bottom:bounds.bottom }, scrollHeight:portal.scrollHeight, clientHeight:portal.clientHeight };
  });
  await page.screenshot({ path:resolve(directory, 'chat-actions-sidebar-keyboard.png') });
  assert.equal(focused.text, 'Delete');
  assert.equal(focused.visible, true, 'Keyboard focus scrolls the last action into the available viewport');
  await page.screenshot({ path:resolve(directory, 'chat-actions-sidebar-keyboard.png') });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(() => !!document.activeElement.closest('#sidebar-chat-item-menu')), true);
  await page.evaluate(() => {
   const portal = document.createElement('div');
   portal.id = 'unrelated-menu-preview';
   portal.setAttribute('role', 'menu');
   const panel = document.createElement('div');
   panel.className = 'app-dropdown-menu';
   panel.textContent = 'Unrelated model menu';
   portal.appendChild(panel);
   document.body.appendChild(portal);
  });
  await page.waitForTimeout(150);
  assert.equal(await page.locator('#unrelated-menu-preview').evaluate((portal) => portal.classList.contains('owui-chat-menu-portal')), false, 'Closed chat trigger cannot capture an unrelated later menu');
  await page.locator('#unrelated-menu-preview').evaluate((portal) => portal.remove());
  const runtimeErrors = session.errors.filter((error) => /TypeError|ReferenceError|SyntaxError|RangeError/.test(error));
  assert.deepEqual(runtimeErrors, []);
  results.push({ name:'chat-actions-sidebar-move-keyboard', panel, focused, blockedMutations:session.blockedMutations, errors:runtimeErrors });
 } finally { await session.browser.close(); }
}

if (process.argv.includes('--targeted')) await runOwnedMenuChecks();
else {
 await runScenario({ name:'chat-actions-phone-dark', viewport:{ width:390, height:844 }, dark:true });
 await runScenario({ name:'chat-actions-narrow-keyboard', viewport:{ width:320, height:740 }, keyboard:true, textScale:1.25 });
 await runScenario({ name:'chat-actions-desktop', viewport:{ width:1280, height:900 }, hasTouch:false, isMobile:false });
 await runScenario({ name:'chat-actions-duo', viewport:{ width:1114, height:720 }, segments:[{ left:0, top:0, right:540, bottom:720, width:540, height:720 }, { left:574, top:0, right:1114, bottom:720, width:540, height:720 }] });
}
writeFileSync(resolve(directory, process.argv.includes('--targeted') ? 'chat-actions-targeted-results.json' : 'chat-actions-results.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify({ passed:results.length, results }, null, 2));
