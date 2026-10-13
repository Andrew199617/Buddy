import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage, outputDirectory } from './mobile-ui-preview.mjs';

const mobileSettingsSource = resolve(dirname(fileURLToPath(import.meta.url)), '../../web/mobile-settings.js');
const nativeSettingsPath = '/c/mobile-preview-chat-1?settings=general';
const results = [];

function deferred() {
  let resolvePromise;
  const promise = new Promise((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

async function awaitSaveStarted(save) {
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => reject(new Error('The explicit settings save did not reach its synthetic API')), 10000);
  });
  try {
    await Promise.race([save.started, deadline]);
  } finally {
    clearTimeout(timeout);
  }
}

async function createSaveSession(options = {}) {
  const session = await createMockedPage({
    ...options,
    urlPath: nativeSettingsPath,
    modelSelectorVisible: false
  });
  const stored = {
    ui: {
      textScale: 1,
      models: ['preview-default-model'],
      pinnedModels: [],
      showChatMenu: false,
      system: 'Synthetic original settings prompt'
    },
    keybindings: {}
  };
  const settingsWrites = [];
  let nextSave = null;
  await session.context.route('**/api/v1/users/user/settings**', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stored) });
      return;
    }
    const updated = JSON.parse(route.request().postData() ?? '{}');
    settingsWrites.push(updated);
    const save = nextSave;
    nextSave = null;
    if (save) {
      save.started.resolve();
      await save.released.promise;
      if (save.outcome === 'api-error') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Synthetic settings save failure' })
        });
        return;
      }
      if (save.outcome === 'transport-error') {
        await route.abort('failed');
        return;
      }
    }
    stored.ui = { ...stored.ui, ...updated.ui };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stored) });
  });

  async function openNativeSettings() {
    const url = new URL(nativeSettingsPath, session.page.url()).toString();
    await session.page.goto(url, { waitUntil: 'domcontentloaded' });
    await session.page.locator('#settings-tabs-container').waitFor({ state: 'attached' });
    await session.page.addScriptTag({ content: readFileSync(mobileSettingsSource, 'utf8') });
    if (options.colorScheme === 'dark') {
      await session.page.evaluate(() => {
        document.documentElement.classList.remove('light');
        document.documentElement.classList.add('dark');
      });
    }
    await session.page.waitForFunction(() => {
      const nav = document.querySelector('#settings-tabs-container');
      return innerWidth >= 768 || nav?.parentElement?.getAttribute('data-settings-view') === 'home';
    });
  }

  function holdNextSave(outcome = 'success') {
    assert.equal(nextSave, null, 'Only one explicit save may be queued at a time');
    const started = deferred();
    const released = deferred();
    nextSave = { outcome, started, released };
    return {
      started: started.promise,
      release() {
        released.resolve();
      }
    };
  }

  await openNativeSettings();
  return { ...session, stored, settingsWrites, holdNextSave, openNativeSettings };
}

async function runSaveScenario(name, options, exercise) {
  const session = await createSaveSession(options);
  try {
    await exercise(session);
  } catch (error) {
    await session.page.screenshot({ path: resolve(outputDirectory, 'settings-save-' + name + '-failure.png') });
    writeFileSync(resolve(outputDirectory, 'settings-save-' + name + '-failure.json'), JSON.stringify({
      error: error.stack ?? String(error),
      url: session.page.url(),
      writes: session.settingsWrites,
      requests: session.requests,
      errors: session.errors,
      body: (await session.page.locator('body').innerText()).slice(0, 6000)
    }, null, 2));
    throw error;
  } finally {
    await session.browser.close();
  }
}

async function selectCategory(page, id) {
  const row = page.locator('#settings-tabs-container button[role="tab"][aria-controls="tab-' + id + '"]');
  await row.click();
  await page.locator('#tab-' + id).waitFor({ state: 'visible' });
}

function saveButton(page, id) {
  return page.locator('#tab-' + id).getByRole('button', { name: /^(Save|Saving\.\.\.)$/ });
}

async function assertPending(page, id, mobile = true) {
  const button = saveButton(page, id);
  await page.waitForFunction((panelId) => {
    const panel = document.getElementById(panelId);
    return [...panel.querySelectorAll('button')].some((candidate) => candidate.innerText.trim() === 'Saving...' && candidate.disabled);
  }, 'tab-' + id);
  assert.equal(await button.innerText(), 'Saving...');
  assert.equal(await button.isDisabled(), true);
  assert.equal(await button.getAttribute('aria-busy'), 'true');
  assert.equal(await button.locator('svg').isVisible(), true, 'Saving must include a visible progress spinner');
  if (mobile) {
    assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail');
  }
}

async function assertHome(page, id) {
  await page.waitForFunction(() => document.querySelector('.owui-settings-panel')?.getAttribute('data-settings-view') === 'home');
  assert.equal(await page.locator('#settings-tabs-container').isVisible(), true);
  assert.equal(await page.locator('#tab-' + id).isVisible(), false);
  await page.waitForFunction((panelId) => {
    const row = document.querySelector('#settings-tabs-container button[aria-controls="' + panelId + '"]');
    return document.activeElement === row;
  }, 'tab-' + id);
}

async function assertSaveReady(page, id) {
  const button = saveButton(page, id);
  await page.waitForFunction((panelId) => {
    const panel = document.getElementById(panelId);
    return [...panel.querySelectorAll('button')].some((candidate) => candidate.innerText.trim() === 'Save' && !candidate.disabled);
  }, 'tab-' + id);
  assert.equal(await button.getAttribute('aria-busy'), 'false');
}

async function generalSuccess(session) {
  const { page } = session;
  await selectCategory(page, 'general');
  const prompt = page.locator('#tab-general textarea').first();
  await prompt.fill('Synthetic saved prompt from the phone regression');
  const save = session.holdNextSave();
  const button = saveButton(page, 'general');
  const bounds = await button.boundingBox();
  await page.touchscreen.tap(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await awaitSaveStarted(save);
  await assertPending(page, 'general');
  await page.touchscreen.tap(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.waitForTimeout(100);
  assert.equal(session.settingsWrites.length, 1, 'A second press during persistence must not create another write');
  await page.screenshot({ path: resolve(outputDirectory, 'settings-save-phone-saving.png') });
  save.release();
  await assertHome(page, 'general');
  await page.screenshot({ path: resolve(outputDirectory, 'settings-save-phone-home.png') });
  assert.equal(session.stored.ui.system, 'Synthetic saved prompt from the phone regression');
  await selectCategory(page, 'general');
  assert.equal(await prompt.inputValue(), session.stored.ui.system);

  await session.openNativeSettings();
  await selectCategory(page, 'general');
  assert.equal(await prompt.inputValue(), session.stored.ui.system, 'Refreshing the native Settings route must load the saved field');
  await page.screenshot({ path: resolve(outputDirectory, 'settings-save-phone-saved.png') });
  results.push({ test: 'phone-general-save', writes: session.settingsWrites.length, savedPrompt: session.stored.ui.system });
}

async function failureAndRetry(session, outcome) {
  const { page } = session;
  await selectCategory(page, 'general');
  const prompt = page.locator('#tab-general textarea').first();
  const draft = 'Synthetic draft survives ' + outcome;
  await prompt.fill(draft);
  const failedSave = session.holdNextSave(outcome);
  await saveButton(page, 'general').click();
  await awaitSaveStarted(failedSave);
  await assertPending(page, 'general');
  failedSave.release();
  await assertSaveReady(page, 'general');
  assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail');
  assert.equal(await prompt.inputValue(), draft);
  assert.equal(session.stored.ui.system, 'Synthetic original settings prompt');
  if (outcome === 'api-error') {
    await page.getByText('Synthetic settings save failure', { exact: true }).waitFor({ state: 'visible' });
  } else {
    await page.getByText('Error: Failed to save settings. Please try again.', { exact: true }).waitFor({ state: 'visible' });
  }
  assert.equal(await page.getByText('Settings saved successfully!', { exact: true }).count(), 0);
  const retry = session.holdNextSave();
  await saveButton(page, 'general').click();
  await awaitSaveStarted(retry);
  await assertPending(page, 'general');
  retry.release();
  await assertHome(page, 'general');
  assert.equal(session.settingsWrites.length, 2);
  assert.equal(session.stored.ui.system, draft);
  results.push({ test: outcome + '-retry', writes: session.settingsWrites.length, draftPreserved: true });
}

async function asyncCategory(session, id) {
  const { page } = session;
  await selectCategory(page, id);
  if (id === 'audio') {
    await page.locator('#tab-audio input[type="number"]').fill('1.5');
  }
  const save = session.holdNextSave();
  await saveButton(page, id).click();
  await awaitSaveStarted(save);
  await assertPending(page, id);
  await page.waitForTimeout(500);
  await assertPending(page, id);
  assert.equal(await page.getByText('Settings saved successfully!', { exact: true }).count(), 0);
  save.release();
  await assertHome(page, id);
  assert.equal(session.settingsWrites.length, 1);
  if (id === 'audio') {
    assert.equal(session.stored.ui.audio.tts.playbackRate, 1.5);
    await selectCategory(page, id);
    const automaticPlayback = page.locator('#tab-audio').getByRole('switch').first();
    await automaticPlayback.click();
    await page.waitForFunction(() => document.querySelector('#tab-audio [role="switch"]')?.getAttribute('aria-checked') === 'true');
    await page.waitForTimeout(100);
    assert.equal(session.settingsWrites.length, 2);
    assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail', 'Automatic changes must stay in the current category');
  } else {
    assert.deepEqual(session.stored.ui.models, ['preview-default-model']);
  }
  results.push({ test: id + '-awaits-persistence', writes: session.settingsWrites.length, returnsHome: true });
}

async function desktopSave(session) {
  const { page } = session;
  await selectCategory(page, 'general');
  await page.locator('#tab-general textarea').first().fill('Synthetic desktop settings prompt');
  const save = session.holdNextSave();
  await saveButton(page, 'general').click();
  await awaitSaveStarted(save);
  await assertPending(page, 'general', false);
  save.release();
  await assertSaveReady(page, 'general');
  await page.getByText('Settings saved successfully!', { exact: true }).waitFor({ state: 'visible' });
  assert.equal(await page.locator('#tab-general').isVisible(), true);
  assert.equal(await page.locator('#settings-tabs-container button[aria-controls="tab-general"]').getAttribute('aria-selected'), 'true');
  assert.equal(await page.locator('.owui-mobile-settings').count(), 0);
  assert.equal(session.settingsWrites.length, 1);
  results.push({ test: 'desktop-save-stays-general', writes: session.settingsWrites.length });
}

async function apiFailureRetry(session) {
  await failureAndRetry(session, 'api-error');
}

async function transportFailureRetry(session) {
  await failureAndRetry(session, 'transport-error');
}

async function interfaceSave(session) {
  await asyncCategory(session, 'interface');
}

async function audioSave(session) {
  await asyncCategory(session, 'audio');
}

async function pendingNavigation(session) {
  const { page } = session;
  await selectCategory(page, 'general');
  const prompt = page.locator('#tab-general textarea').first();
  await prompt.fill('Synthetic prompt saved after leaving its details page');
  const save = session.holdNextSave();
  await saveButton(page, 'general').click();
  await awaitSaveStarted(save);
  await assertPending(page, 'general');
  await page.getByRole('button', { name: 'Back to Settings', exact: true }).click();
  await assertHome(page, 'general');
  await selectCategory(page, 'general');
  await assertPending(page, 'general');
  save.release();
  await assertSaveReady(page, 'general');
  await page.waitForTimeout(100);
  assert.equal(await page.locator('.owui-settings-panel').getAttribute('data-settings-view'), 'detail', 'Completion from an earlier visit must not leave the reopened category');
  assert.equal(await prompt.inputValue(), 'Synthetic prompt saved after leaving its details page');
  assert.equal(session.stored.ui.system, await prompt.inputValue());
  assert.equal(session.settingsWrites.length, 1);
  results.push({ test: 'pending-back-and-reenter', writes: session.settingsWrites.length, staysDetail: true });
}

try {
  await runSaveScenario('phone-general-save', { colorScheme: 'dark' }, generalSuccess);
  if (!process.argv.includes('--general')) {
    await runSaveScenario('api-error-retry', {}, apiFailureRetry);
    await runSaveScenario('transport-error-retry', {}, transportFailureRetry);
    await runSaveScenario('interface', {}, interfaceSave);
    await runSaveScenario('audio', {}, audioSave);
    await runSaveScenario('pending-navigation', {}, pendingNavigation);
    await runSaveScenario('desktop', { viewport: { width: 1280, height: 900 }, hasTouch: false, isMobile: false }, desktopSave);
  }
  writeFileSync(resolve(outputDirectory, 'settings-save-result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} catch (error) {
  writeFileSync(resolve(outputDirectory, 'settings-save-result.json'), JSON.stringify({ completed: results, error: error.stack ?? String(error) }, null, 2));
  console.error(error.stack ?? error);
  process.exitCode = 1;
}
