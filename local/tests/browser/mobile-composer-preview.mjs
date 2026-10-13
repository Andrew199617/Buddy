/** Isolated production browser taps: APIs, writes, providers, and media are mocked. */
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createSession, workspace, outputDirectory as fixtureOutputDirectory } from './lazy-feature-fixture.mjs';

import { readProductionSourceMaps, resolveProductionExports } from './production-module-exports.mjs';

const capturePhase = process.argv.find((argument) => argument.startsWith('--capture-phase='))?.slice(16);
const outputDirectory = resolve(fixtureOutputDirectory, '../mobile-composer',
	capturePhase ? 'iphone12-pro-max-' + capturePhase : '.');
const modelTrigger = '.buddy-composer button[id^="model-selector-"][id$="-button"]';
const modelMenu = '[data-composer-menu="model-picker"]';
const thinkingTrigger = '#owui-reasoning-chip';
const thinkingMenu = '[data-composer-menu="thinking"]';
const results = [];
const webkitRequested = process.argv.includes('--webkit');
const diagnosticsOnly = process.argv.includes('--diagnostic');
const filter = process.argv.find((argument) => argument.startsWith('--only='));
const selectedCases = filter ? filter.slice(7).split(',') : null;
const { chromium, webkit, devices } = await import(process.env.BUDDY_PLAYWRIGHT_MODULE || 'playwright');
const engineName = webkitRequested ? 'webkit' : 'chromium';

async function recordEvents(context) {
	await context.addInitScript(() => {
		localStorage.setItem('owui-local:reasoning-effort', 'high');
		window.__qaComposerEvents = [];
		function describe(element) {
			if (!(element instanceof Element)) return String(element);
			return element.id || element.getAttribute('aria-label') ||
				element.getAttribute('data-composer-menu') || element.className;
		}
		function record(event) {
			window.__qaComposerEvents.push({
				type: event.type, target: describe(event.target), active: describe(document.activeElement),
				control: describe(event.target instanceof Element ? event.target.closest('button, [role="option"], input') : null),
				defaultPrevented: event.defaultPrevented, cancelable: event.cancelable,
				pointerType: event.pointerType, trusted: event.isTrusted,
				composerHeight: document.querySelector('.buddy-composer')?.getBoundingClientRect().height
			});
		}
		document.addEventListener('pointerdown', record);
		document.addEventListener('touchstart', record);
		document.addEventListener('touchend', record);
		document.addEventListener('mousedown', record);
		document.addEventListener('click', record);
		document.addEventListener('focusin', record);
		document.addEventListener('focusout', record);
	});
}

async function state(page) {
	return page.evaluate(() => {
		const composer = document.querySelector('.buddy-composer');
		const input = document.getElementById('chat-input');
		const settings = composer.querySelector('.buddy-composer-settings');
		return {
			focused: document.activeElement === input || input.contains(document.activeElement),
			draft: input.textContent,
			active: document.activeElement?.id || document.activeElement?.getAttribute('aria-label'),
			expanded: Boolean(settings && getComputedStyle(settings).display !== 'none'),
			composerHeight: composer.getBoundingClientRect().height,
			inputMounted: input === window.__qaOriginalComposer,
			modelOpen: Boolean(document.querySelector('[data-composer-menu="model-picker"]')),
			thinkingOpen: Boolean(document.querySelector('[data-composer-menu="thinking"]'))
		};
	});
}

async function focusDraft(page, draft = '') {
	await page.locator('#chat-input').tap();
	if (draft) await page.locator('#chat-input').fill(draft);
	await page.evaluate(() => {
		window.__qaOriginalComposer = document.getElementById('chat-input');
		Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 464 });
		visualViewport.dispatchEvent(new Event('resize'));
	});
	await page.locator(modelTrigger).waitFor({ state: 'visible' });
	await page.waitForTimeout(150);
	assert.equal((await state(page)).focused, true, 'A trusted draft tap focuses the editor');
	return (await state(page)).draft;
}

async function focused(page, draft, message) {
	const current = await state(page);
	assert.equal(current.focused, true, message);
	assert.equal(current.draft, draft, 'The draft survives control interaction');
	assert.equal(current.inputMounted, true, 'The original composer editor remains mounted');
	assert.equal(current.expanded, true, 'The composer remains expanded');
}

async function noDraftBlur(page) {
	const events = await page.evaluate(() => window.__qaComposerEvents);
	assert.equal(events.filter((event) => event.type === 'focusout' && event.target === 'chat-input').length,
		0, 'Control taps never temporarily blur the draft');
}

async function resetEvents(page) {
	await page.evaluate(() => { window.__qaComposerEvents = []; });
}

async function runCase(browser, name, action, options = {}) {
	if (selectedCases && !selectedCases.includes(name)) return;
	let sessionBrowser = browser;
	if (options.deviceDescriptor) {
		async function createDeviceContext(contextOptions) {
			return browser.newContext({ ...contextOptions, ...options.deviceDescriptor });
		}
		sessionBrowser = { newContext: createDeviceContext };
	}
	const session = await createSession(sessionBrowser, { mobile: true, beforeNavigation: recordEvents, ...options });
	session.page.setDefaultTimeout(8000);
	await session.page.evaluate(() => { window.__qaOriginalComposer = document.getElementById('chat-input'); });
	const label = engineName + '-' + name;
	try {
		await action(session);
		assert.equal(session.completions.length, 0, 'No completion request is made');
		await session.page.waitForTimeout(250);
		await session.page.screenshot({ path: resolve(outputDirectory, label + '.png') });
		results.push({ name: label, passed: true });
	} catch (error) {
		results.push({ name: label, passed: false, message: error.message });
		await session.page.screenshot({ path: resolve(outputDirectory, label + '-failure.png') });
	} finally {
		writeFileSync(resolve(outputDirectory, label + '-events.json'),
			JSON.stringify(await session.page.evaluate(() => window.__qaComposerEvents), null, 2));
		writeFileSync(resolve(outputDirectory, label + '-state.json'), JSON.stringify(await state(session.page), null, 2));
		await session.context.close();
	}
}

async function modelSelection(browser, draftText) {
	const suffix = draftText ? 'typed' : 'empty';
	await runCase(browser, 'model-selection-' + suffix, async ({ page }) => {
		const draft = await focusDraft(page, draftText);
		await resetEvents(page);
		await page.locator(modelTrigger).tap();
		await page.locator(modelMenu).waitFor({ state: 'visible', timeout: 5000 });
		await focused(page, draft, 'Opening the model picker preserves draft focus');
		await page.waitForTimeout(250);
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-model-picker-' + suffix + '.png') });
		await page.locator(modelMenu).getByRole('option', { name: 'Select Buddy local model model', exact: true }).tap();
		await page.locator(modelMenu).waitFor({ state: 'detached' });
		await focused(page, draft, 'Model selection preserves draft focus');
		await noDraftBlur(page);
	});
}

async function thinkingSelection(browser, draftText) {
	const suffix = draftText ? 'typed' : 'empty';
	await runCase(browser, 'thinking-selection-' + suffix, async ({ page }) => {
		const draft = await focusDraft(page, draftText);
		await resetEvents(page);
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'visible', timeout: 5000 });
		await focused(page, draft, 'Opening thinking options preserves draft focus');
		await page.waitForTimeout(250);
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-thinking-picker-' + suffix + '.png') });
		await page.locator(thinkingMenu).getByRole('menuitemradio', { name: 'Medium', exact: true }).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'detached' });
		assert.equal(await page.locator(thinkingTrigger).getAttribute('aria-label'), 'Thinking level: Medium');
		await focused(page, draft, 'Thinking selection preserves draft focus');
		await noDraftBlur(page);
	});
}

async function deliberateSearch(browser) {
	await runCase(browser, 'deliberate-model-search', async ({ page }) => {
		const draft = await focusDraft(page, 'An unsent thought');
		await page.locator(modelTrigger).tap();
		await page.locator(modelMenu).waitFor({ state: 'visible' });
		await page.locator('#model-search-input').tap();
		assert.equal(await page.locator('#model-search-input').evaluate((element) => element === document.activeElement), true);
		await page.locator('#model-search-input').fill('Buddy');
		await page.locator(modelMenu).getByRole('option', { name: 'Select Buddy local model model', exact: true }).tap();
		await page.locator(modelMenu).waitFor({ state: 'detached' });
		await focused(page, draft, 'Selecting a searched model restores draft focus');
	});
}

async function conversationDismissal(browser) {
	await runCase(browser, 'conversation-tap-dismisses-empty-composer', async ({ page }) => {
		await focusDraft(page);
		await page.getByText('A plain answer for optional panel tests.', { exact: true }).tap();
		await page.waitForTimeout(150);
		const current = await state(page);
		assert.equal(current.focused, false, 'A conversation tap dismisses editor focus');
		assert.equal(current.expanded, false, 'An empty composer collapses after a conversation tap');
	});
}


async function menuSwitching(browser) {
	await runCase(browser, 'menu-switching-and-toggle', async ({ page }) => {
		const draft = await focusDraft(page);
		await resetEvents(page);
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'visible' });
		await page.locator(modelTrigger).tap();
		await page.locator(modelMenu).waitFor({ state: 'visible' });
		assert.equal(await page.locator(thinkingMenu).count(), 0, 'Opening model choices closes thinking choices');
		await focused(page, draft, 'Switching from thinking to model preserves focus');
		await page.locator(modelMenu).tap({ position: { x: 3, y: 3 } });
		await focused(page, draft, 'Tapping model popup padding preserves focus');
		assert.equal((await state(page)).modelOpen, true, 'Popup padding does not close model choices');
		await page.locator(modelTrigger).tap();
		await page.locator(modelMenu).waitFor({ state: 'detached' });
		await focused(page, draft, 'Tapping the model trigger twice closes once');
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'visible' });
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'detached' });
		await focused(page, draft, 'Tapping the thinking trigger twice closes once');
		await noDraftBlur(page);
	});
}

async function safariNativeFocusGap(browser, trigger, menu, label) {
	await runCase(browser, 'safari-touch-default-' + label, async ({ page }) => {
		const draft = await focusDraft(page);
		await resetEvents(page);
		await page.evaluate((selector) => {
			// Model Safari 26's reported native blur despite pointerdown cancellation.
			// A touchstart cancellation prevents that native touch default behavior.
			document.addEventListener('touchstart', (event) => {
				if (event.defaultPrevented || !(event.target instanceof Element)) return;
				if (!event.target.closest(selector)) return;
				document.getElementById('chat-input').blur();
			}, { passive: false });
		}, trigger);
		await page.locator(trigger).tap();
		await page.locator(menu).waitFor({ state: 'visible', timeout: 5000 });
		await focused(page, draft, 'The canceled touch default keeps an empty composer focused');
		await noDraftBlur(page);
	});
}

async function contextBubble(browser) {
	await runCase(browser, 'mobile-context-bubble', async ({ page }) => {
		await focusDraft(page);
		const badge = page.locator('#owui-context-info-button');
		await badge.waitFor({ state: 'visible' });
		const geometry = await badge.evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const container = element.closest('form') || element.closest('.buddy-composer-content') || document.querySelector('.buddy-composer');
			const rail = container.getBoundingClientRect();
			const style = getComputedStyle(element);
			return {
				width: rect.width, railWidth: rail.width, background: style.backgroundColor,
				shadow: style.boxShadow, radius: style.borderRadius
			};
		});
		writeFileSync(resolve(outputDirectory, engineName + '-context-geometry.json'), JSON.stringify(geometry, null, 2));
		assert.ok(geometry.width < geometry.railWidth - 32, 'Context bubble uses intrinsic width');
		assert.notEqual(geometry.background, 'rgba(0, 0, 0, 0)', 'Context bubble has its own surface');
		assert.notEqual(geometry.shadow, 'none', 'Context bubble has a shadow');
	});
}

async function desktopToolbar(browser) {
	await runCase(browser, 'desktop-toolbar-preserved', async ({ page }) => {
		await page.locator('#chat-input').click();
		await page.locator('#chat-input').fill('A desktop draft');
		assert.equal(await page.locator('.buddy-composer-settings').count(), 0, 'Desktop has no mobile settings row');
		const model = page.locator('.buddy-composer button[id^="model-selector-"][id$="-button"]');
		const location = await model.evaluate((element) => ({
			inToolbar: Boolean(element.closest('.buddy-composer-tools')),
			inSettings: Boolean(element.closest('.buddy-composer-settings'))
		}));
		assert.equal(location.inToolbar, true, 'The desktop model control remains in the bottom toolbar');
		assert.equal(location.inSettings, false);
		await model.click();
		await page.locator('#model-search-input').waitFor({ state: 'visible' });
		await page.waitForFunction(() => document.activeElement?.id === 'model-search-input');
		assert.equal(await page.locator('#model-search-input').evaluate((element) => element === document.activeElement),
			true, 'Desktop model search retains deliberate autofocus');
	}, { mobile: false });
}


async function thinkingMenuSwipe(browser) {
	await runCase(browser, 'thinking-menu-swipe', async ({ page, context }) => {
		const draft = await focusDraft(page);
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'visible' });
		// The compact grid fits the normal portrait viewport. A taller keyboard
		// constrains the open panel so this case exercises an actual scroll gesture.
		await page.evaluate(() => {
			Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 260 });
			visualViewport.dispatchEvent(new Event('resize'));
		});
		await page.waitForTimeout(150);
		const menu = page.locator(thinkingMenu);
		const dimensions = await menu.evaluate((element) => ({
			height: element.clientHeight, scrollHeight: element.scrollHeight
		}));
		assert.ok(dimensions.scrollHeight > dimensions.height, 'Thinking choices are scrollable above the keyboard');
		await swipePopup(page, context, thinkingMenu);
		assert.ok(await menu.evaluate((element) => element.scrollTop > 0), 'A trusted swipe scrolls the thinking list');
		assert.equal(await page.locator(thinkingTrigger).getAttribute('aria-label'), 'Thinking level: High',
			'Swiping the list does not select a level');
		await focused(page, draft, 'A thinking-list swipe preserves draft focus');
		await noDraftBlur(page);
	});
}


async function headerDismissal(browser, trigger, menu, label) {
	await runCase(browser, 'header-dismisses-empty-' + label + '-menu', async ({ page }) => {
		await focusDraft(page);
		await page.locator(trigger).tap();
		await page.locator(menu).waitFor({ state: 'visible' });
		const headerPoint = await page.locator('.buddy-chat-header').evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const x = rect.x + 90;
			const y = rect.y + 8;
			const hit = document.elementFromPoint(x, y);
			return {
				x,
				y,
				interactive: Boolean(hit?.closest('button, a[href], input, [role="button"], [contenteditable="true"]'))
			};
		});
		assert.equal(headerPoint.interactive, false, 'The observed header background hit is noninteractive');
		await page.touchscreen.tap(headerPoint.x, headerPoint.y);
		await page.locator(menu).waitFor({ state: 'detached' });
		await page.waitForTimeout(150);
		const current = await state(page);
		assert.equal(current.focused, false, 'A header background tap dismisses editor focus');
		assert.equal(current.expanded, false, 'An empty composer collapses after header dismissal');
	});
}


const plusTrigger = '#input-menu-button';
const toolsTrigger = '#integration-menu-button';
const plusMenu = '[data-composer-menu="attachments"]';
const toolsMenu = '[data-composer-menu="integrations"]';
const syntheticTool = {
	id: 'qa-harmless-tool',
	name: 'Preview helper',
	is_active: true,
	authenticated: true,
	meta: { description: 'Synthetic composer toggle. This tool is never executed.' }
};
let composerStoreReference;

function productionComposerStores() {
	if (!composerStoreReference) {
		const sourceMaps = readProductionSourceMaps(resolve(workspace, 'build'));
		composerStoreReference = resolveProductionExports(sourceMaps, '/stores/index.ts', [
			'config', 'tools', 'models'
		]);
	}
	return composerStoreReference;
}

async function enableComposerFixtures({ context, page }) {
	async function serveSyntheticTools(route) {
		assert.equal(route.request().method(), 'GET', 'Fixture tool requests only read synthetic data');
		await route.fulfill({ status: 200, json: [syntheticTool] });
	}
	await context.route('**/api/v1/tools/**', serveSyntheticTools);

	async function installSyntheticComposerOptions({ moduleReference, tool }) {
		const module = await import(moduleReference.url);
		module[moduleReference.aliases.tools].set([tool]);
		function enableComposerFeatures(current) {
			return {
				...current,
				features: {
					...current.features,
					enable_tool_permissions: true,
					enable_image_generation: true,
					enable_web_search: true
				}
			};
		}
		module[moduleReference.aliases.config].update(enableComposerFeatures);
		function enableModelToggles(items) {
			return items.map((model) => ({
				...model,
				info: {
					...model.info,
					meta: {
						...model.info?.meta,
						capabilities: {
							...model.info?.meta?.capabilities,
							image_generation: true,
							web_search: true
						}
					}
				}
			}));
		}
		module[moduleReference.aliases.models].update(enableModelToggles);
	}
	await page.evaluate(installSyntheticComposerOptions, {
		moduleReference: productionComposerStores(),
		tool: syntheticTool
	});
}

async function closeActionMenu(page, triggerSelector, menuSelector) {
	const hit = await page.locator(triggerSelector).evaluate((button, selector) => {
		const bounds = button.getBoundingClientRect();
		const target = document.elementFromPoint(
			bounds.x + bounds.width / 2,
			bounds.y + bounds.height / 2
		);
		const menu = document.querySelector(selector);
		return {
			triggerReceivesTap: Boolean(target && button.contains(target)),
			menuCoversTrigger: Boolean(target && menu?.contains(target))
		};
	}, menuSelector);
	if (hit.triggerReceivesTap) {
		await page.locator(triggerSelector).tap();
	} else {
		assert.equal(hit.menuCoversTrigger, true,
			'The focused action overlay covers the trigger while keeping its choices reachable');
		await page.keyboard.press('Escape');
	}
	await page.locator(menuSelector).waitFor({ state: 'detached' });
}

async function plusLifecycle(browser) {
	async function verifyPlusLifecycle(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page);
		await resetEvents(page);
		await page.locator(plusTrigger).tap();
		await page.waitForTimeout(250);
		await focused(page, draft, 'Opening Plus preserves the focused empty draft');
		const menu = page.locator(plusMenu);
		await menu.waitFor({ state: 'visible' });
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-plus-open.png') });
		await menu.getByRole('button', { name: /^Tool Permissions/ }).tap();
		await menu.getByRole('button', { name: 'Ask for approval', exact: true }).waitFor();
		await focused(page, draft, 'Opening a Plus child panel preserves draft focus');
		await menu.getByRole('button', { name: 'Ask for approval', exact: true }).tap();
		await menu.getByRole('button', { name: /^Tool Permissions.*Ask for approval/ }).waitFor();
		await focused(page, draft, 'Choosing a harmless tool approval setting preserves draft focus');
		await closeActionMenu(page, plusTrigger, plusMenu);
		await focused(page, draft, 'Closing Plus preserves draft focus');
		await page.locator(plusTrigger).tap();
		await menu.waitFor({ state: 'visible' });
		await page.keyboard.press('Escape');
		await menu.waitFor({ state: 'detached' });
		await focused(page, draft, 'Closing Plus with Escape preserves draft focus');
		await noDraftBlur(page);
	}
	await runCase(browser, 'plus-lifecycle-empty', verifyPlusLifecycle);
}

async function toolsLifecycle(browser) {
	async function verifyToolsLifecycle(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page);
		await resetEvents(page);
		await page.locator(toolsTrigger).tap();
		await page.waitForTimeout(250);
		await focused(page, draft, 'Opening Tools preserves the focused empty draft');
		const menu = page.locator(toolsMenu);
		await menu.waitFor({ state: 'visible' });
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-tools-open.png') });
		await menu.getByRole('button', { name: /^Tools\s+1$/ }).tap();
		const option = menu.getByRole('button', { name: 'Preview helper', exact: true });
		await option.waitFor();
		await focused(page, draft, 'Opening Tools child choices preserves draft focus');
		await option.tap();
		await page.waitForFunction(() => {
			const option = document.querySelector('[data-composer-menu="integrations"] button[aria-pressed="true"]');
			return Boolean(option?.textContent.includes('Preview helper'));
		});
		await focused(page, draft, 'Selecting a synthetic tool preserves draft focus');
		await closeActionMenu(page, toolsTrigger, toolsMenu);
		await focused(page, draft, 'Closing Tools preserves draft focus');
		await page.locator(toolsTrigger).tap();
		await menu.waitFor({ state: 'visible' });
		await page.keyboard.press('Escape');
		await menu.waitFor({ state: 'detached' });
		await focused(page, draft, 'Closing Tools with Escape preserves draft focus');
		await noDraftBlur(page);
	}
	await runCase(browser, 'tools-lifecycle-empty', verifyToolsLifecycle);
}

async function composerPadding(browser) {
	async function verifyComposerPadding({ page }) {
		const draft = await focusDraft(page);
		await resetEvents(page);
		const point = await page.locator('.buddy-composer-tools').evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const x = rect.x + rect.width * 0.55;
			const y = rect.y + Math.min(8, rect.height / 2);
			const hit = document.elementFromPoint(x, y);
			return {
				x, y,
				insideComposer: Boolean(hit?.closest('.buddy-composer')),
				interactive: Boolean(hit?.closest('button, a[href], input, [role="button"], [contenteditable="true"]'))
			};
		});
		assert.equal(point.insideComposer, true, 'The observed blank point belongs to the composer');
		assert.equal(point.interactive, false, 'The observed blank point is composer spacing');
		await page.touchscreen.tap(point.x, point.y);
		await focused(page, draft, 'Tapping blank composer toolbar space preserves draft focus');
		await noDraftBlur(page);
	}
	await runCase(browser, 'composer-padding-empty', verifyComposerPadding);
}

async function extraSafariFocusGap(browser, trigger, menu, label) {
	async function verifySafariFocusGap(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page);
		await resetEvents(page);
		function simulateSafariTouchDefault(selector) {
			document.addEventListener('touchstart', (event) => {
				if (event.defaultPrevented || !(event.target instanceof Element)) return;
				if (!event.target.closest(selector)) return;
				document.getElementById('chat-input').blur();
			}, { passive: false });
		}
		await page.evaluate(simulateSafariTouchDefault, trigger);
		await page.locator(trigger).tap();
		await page.waitForTimeout(250);
		await focused(page, draft, 'The canceled touch default protects this composer control');
		await page.locator(menu).waitFor({ state: 'visible' });
		await noDraftBlur(page);
	}
	await runCase(browser, 'safari-touch-default-' + label, verifySafariFocusGap);
}


async function toolsChildSearch(browser) {
	async function verifyToolsChildSearch(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page, 'Keep my thought while choosing a tool');
		await page.locator(toolsTrigger).tap();
		const menu = page.locator(toolsMenu);
		await menu.waitFor({ state: 'visible' });
		await menu.getByRole('button', { name: /^Tools\s+1$/ }).tap();
		const search = menu.getByRole('textbox', { name: 'Search tools', exact: true });
		await search.tap();
		assert.equal(await search.evaluate((element) => element === document.activeElement), true,
			'Tool search receives focus only when deliberately tapped');
		assert.ok(await search.evaluate((element) => parseFloat(getComputedStyle(element).fontSize)) >= 16,
			'Mobile menu search avoids the iOS focus zoom');
		await search.fill('Preview');
		await menu.getByRole('button', { name: 'Preview helper', exact: true }).tap();
		assert.equal(await search.evaluate((element) => element === document.activeElement), true,
			'A tool option keeps an intentionally focused search keyboard');
		await page.keyboard.press('Escape');
		await menu.waitFor({ state: 'detached' });
		await focused(page, draft, 'Escape from tool search restores the original draft');
	}
	await runCase(browser, 'tools-deliberate-child-search', verifyToolsChildSearch);
}

async function inlineComposerToggle(browser) {
	async function verifyInlineComposerToggle(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page);
		await resetEvents(page);
		await page.locator(toolsTrigger).tap();
		const menu = page.locator(toolsMenu);
		await menu.waitFor({ state: 'visible' });
		const imageToggle = menu.getByRole('button', { name: 'Image', exact: true });
		await imageToggle.tap();
		assert.equal(await imageToggle.getAttribute('aria-pressed'), 'true',
			'A harmless composer flag can be selected in the menu');
		await focused(page, draft, 'Selecting a composer flag preserves draft focus');
		await closeActionMenu(page, toolsTrigger, toolsMenu);
		const inline = page.locator('.buddy-composer').getByRole('button', {
			name: 'Disable Image Generation', exact: true
		});
		await inline.tap();
		await inline.waitFor({ state: 'detached' });
		await focused(page, draft, 'Tapping an inline composer toggle preserves draft focus');

		await page.locator(toolsTrigger).tap();
		await menu.waitFor({ state: 'visible' });
		await imageToggle.tap();
		await closeActionMenu(page, toolsTrigger, toolsMenu);
		await inline.waitFor({ state: 'visible' });
		await focused(page, draft, 'Escape closes an owned menu without clearing the chosen control');
		await page.keyboard.press('Escape');
		await inline.waitFor({ state: 'detached' });
		await focused(page, draft, 'Escape without an open menu still clears the draft control');
		await noDraftBlur(page);
	}
	await runCase(browser, 'inline-composer-toggle-empty', verifyInlineComposerToggle);
}

async function transcriptDropdownDismissal(browser, trigger, menu, label) {
	async function verifyTranscriptDropdownDismissal(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		await focusDraft(page);
		await page.locator(trigger).tap();
		await page.locator(menu).waitFor({ state: 'visible' });
		const point = await page.locator('#messages-container').evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const x = rect.right - 4;
			const headerBottom = document.querySelector('.buddy-chat-header').getBoundingClientRect().bottom;
			const y = Math.max(rect.top + 16, headerBottom + 16);
			const hit = document.elementFromPoint(x, y);
			return {
				x, y,
				inTranscript: Boolean(hit?.closest('#messages-container')),
				interactive: Boolean(hit?.closest('button, a[href], input, [role="button"]'))
			};
		});
		assert.equal(point.inTranscript, true, 'The observed background tap reaches the transcript');
		assert.equal(point.interactive, false, 'The observed transcript background has no app action');
		await page.touchscreen.tap(point.x, point.y);
		await page.locator(menu).waitFor({ state: 'detached' });
		await page.waitForTimeout(150);
		const current = await state(page);
		assert.equal(current.focused, false, 'A transcript background tap dismisses the keyboard focus');
		assert.equal(current.expanded, false, 'The empty composer collapses after dismissing this popup');
	}
	await runCase(browser, 'transcript-dismisses-' + label + '-menu', verifyTranscriptDropdownDismissal);
}

async function editorPadding(browser) {
	async function verifyEditorPadding({ page }) {
		const draft = await focusDraft(page);
		await resetEvents(page);
		const point = await page.locator('.buddy-composer-editor').evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const x = rect.x + 1;
			const y = rect.y + Math.min(12, rect.height / 2);
			const hit = document.elementFromPoint(x, y);
			return {
				x, y,
				inComposer: Boolean(hit?.closest('.buddy-composer')),
				inEditor: Boolean(hit?.closest('[contenteditable="true"]'))
			};
		});
		assert.equal(point.inComposer, true);
		assert.equal(point.inEditor, false, 'The observed point is padding around the editor');
		await page.touchscreen.tap(point.x, point.y);
		await focused(page, draft, 'Editor padding belongs to the focused composer');
		await noDraftBlur(page);
	}
	await runCase(browser, 'editor-padding-empty', verifyEditorPadding);
}

async function popupMetrics(page, selector) {
	return page.locator(selector).evaluate((element) => {
		const rect = element.getBoundingClientRect();
		const style = getComputedStyle(element);
		return {
			x: rect.x,
			y: rect.y,
			right: rect.right,
			bottom: rect.bottom,
			width: rect.width,
			height: rect.height,
			background: style.backgroundColor,
			borderColor: style.borderTopColor,
			borderRadius: style.borderRadius,
			color: style.color,
			shadow: style.boxShadow,
			viewportWidth: visualViewport.width,
			viewportHeight: visualViewport.height,
			viewportLeft: visualViewport.offsetLeft,
			viewportTop: visualViewport.offsetTop
		};
	});
}

function assertPopupBounds(metrics) {
	assert.ok(metrics.x >= metrics.viewportLeft + 7, 'The popup retains a viewport edge gutter');
	assert.ok(metrics.right <= metrics.viewportLeft + metrics.viewportWidth - 7, 'The popup fits the viewport width');
	assert.ok(metrics.y >= metrics.viewportTop + 7, 'The popup fits above the keyboard viewport');
	assert.ok(metrics.bottom <= metrics.viewportTop + metrics.viewportHeight - 7, 'The popup stays above the keyboard');
}

async function pickerAppearance(browser, theme) {
	async function verifyPickerAppearance({ page }) {
		const draft = await focusDraft(page);
		await page.locator(modelTrigger).tap();
		await page.locator(modelMenu).waitFor({ state: 'visible' });
		await page.waitForTimeout(250);
		const model = await popupMetrics(page, modelMenu + ' .buddy-composer-picker');
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-model-style-' + theme + '.png') });
		await page.locator(modelTrigger).tap();
		await page.locator(modelMenu).waitFor({ state: 'detached' });
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'visible' });
		await page.waitForTimeout(250);
		const thinking = await popupMetrics(page, thinkingMenu);
		const composerTop = await page.locator('.buddy-composer').evaluate((element) => element.getBoundingClientRect().top);
		writeFileSync(resolve(outputDirectory, engineName + '-picker-style-' + theme + '.json'),
			JSON.stringify({ model, thinking, composerTop }, null, 2));
		assert.equal(thinking.background, model.background, 'Thinking and model share the surface color');
		assert.equal(thinking.borderColor, model.borderColor, 'Thinking and model share the border color');
		assert.equal(thinking.borderRadius, model.borderRadius, 'Thinking and model share the corner radius');
		assert.equal(thinking.color, model.color, 'Thinking and model share text color');
		assert.equal(thinking.shadow, model.shadow, 'Thinking and model share the shadow');
		assert.ok(Math.abs(thinking.x - model.x) <= 1, 'Both pickers anchor to the same composer edge');
		assert.ok(Math.abs(thinking.width - model.width) <= 1, 'Both pickers use the composer width');
		assert.ok(Math.abs(composerTop - model.bottom - 6) <= 1, 'Model picker opens six pixels above the composer');
		assert.ok(Math.abs(composerTop - thinking.bottom - 6) <= 1, 'Thinking picker opens six pixels above the composer');
		assertPopupBounds(model);
		assertPopupBounds(thinking);
		await focused(page, draft, 'Comparing picker panels preserves draft focus');
	}
	await runCase(browser, 'picker-appearance-' + theme, verifyPickerAppearance, { theme });
}

async function landscapeThinking(browser) {
	async function verifyLandscapeThinking({ page }) {
		await page.setViewportSize({ width: 844, height: 390 });
		const draft = await focusDraft(page);
		await page.evaluate(() => {
			Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 240 });
			visualViewport.dispatchEvent(new Event('resize'));
		});
		await page.waitForTimeout(250);
		const initialLayout = await page.locator(thinkingTrigger).evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const ancestors = [];
			let ancestor = element.parentElement;
			while (ancestor) {
				const bounds = ancestor.getBoundingClientRect();
				const style = getComputedStyle(ancestor);
				ancestors.push({
					name: ancestor.id || ancestor.className,
					x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
					overflow: style.overflow, paddingBottom: style.paddingBottom
				});
				ancestor = ancestor.parentElement;
			}
			return {
				innerWidth, innerHeight,
				viewport: { width: visualViewport.width, height: visualViewport.height, offsetTop: visualViewport.offsetTop },
				ancestors,
				hits: document.elementsFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
					.map((hit) => hit.id || hit.className)
			};
		});
		writeFileSync(resolve(outputDirectory, engineName + '-landscape-initial-layout.json'),
			JSON.stringify(initialLayout, null, 2));
		await page.locator(thinkingTrigger).tap();
		await page.locator(thinkingMenu).waitFor({ state: 'visible' });
		await page.waitForTimeout(250);
		const panel = await popupMetrics(page, thinkingMenu);
		const options = await page.locator(thinkingMenu).evaluate((element) =>
			Array.from(element.querySelectorAll('[role="menuitemradio"]')).map((option) => {
				const rect = option.getBoundingClientRect();
				const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
				return {
					name: option.getAttribute('aria-label'),
					receivesTap: Boolean(hit && option.contains(hit))
				};
			})
		);
		writeFileSync(resolve(outputDirectory, engineName + '-landscape-thinking-geometry.json'),
			JSON.stringify({ panel, options }, null, 2));
		assertPopupBounds(panel);
		assert.ok(options.some((option) => option.receivesTap), 'A constrained landscape panel leaves thinking choices tappable');
		await focused(page, draft, 'The landscape thinking panel preserves draft focus');
	}
	await runCase(browser, 'landscape-thinking-keyboard', verifyLandscapeThinking);
}

async function swipePopup(page, context, selector) {
	const rect = await page.locator(selector).boundingBox();
	const x = rect.x + rect.width / 2;
	const startY = rect.y + rect.height * 0.8;
	const endY = rect.y + rect.height * 0.3;
	const protocol = await context.newCDPSession(page);
	await protocol.send('Input.dispatchTouchEvent', {
		type: 'touchStart', touchPoints: [{ x, y: startY, id: 1 }]
	});
	for (let step = 1; step <= 5; step += 1) {
		await protocol.send('Input.dispatchTouchEvent', {
			type: 'touchMove', touchPoints: [{ x, y: startY + (endY - startY) * step / 5, id: 1 }]
		});
		await page.waitForTimeout(25);
	}
	await protocol.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
	await protocol.detach();
	await page.waitForTimeout(150);
}

async function dismissKeyboard(page) {
	await page.getByText('A plain answer for optional panel tests.', { exact: true }).tap();
	await page.evaluate(() => {
		Object.defineProperty(visualViewport, 'height', {
			configurable: true,
			get: () => window.innerHeight
		});
		visualViewport.dispatchEvent(new Event('resize'));
	});
	await page.waitForTimeout(250);
	assert.equal((await state(page)).focused, false, 'The transcript dismisses the draft keyboard');
}

async function actionMenuMetrics(page, menu, rowName) {
	const panel = await popupMetrics(page, menu);
	const details = await page.locator(menu).evaluate((element) => {
		const style = getComputedStyle(element);
		const portal = element;
		const bounds = portal.getBoundingClientRect();
		return {
			frostedClass: portal.matches('.buddy-action-menu'),
			backdropFilter: style.backdropFilter || style.webkitBackdropFilter || 'none',
			backgroundImage: style.backgroundImage,
			overflowX: style.overflowX,
			overflowY: style.overflowY,
			innerPadding: getComputedStyle(element.querySelector('.app-dropdown-menu')).padding,
			portal: { x: bounds.x, y: bounds.y, right: bounds.right, bottom: bounds.bottom }
		};
	});
	const row = await page.locator(menu).getByRole('button', { name: rowName }).evaluate((element) => {
		const bounds = element.getBoundingClientRect();
		const style = getComputedStyle(element);
		const circle = element.querySelector('.buddy-action-icon');
		let iconWell = null;
		if (circle) {
			const rect = circle.getBoundingClientRect();
			const circleStyle = getComputedStyle(circle);
			iconWell = {
				width: rect.width,
				height: rect.height,
				radius: circleStyle.borderRadius,
				background: circleStyle.backgroundColor
			};
		}
		return {
			height: bounds.height,
			fontSize: parseFloat(style.fontSize),
			fontOffset: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--buddy-font-size-offset')) || 0,
			iconWell
		};
	});
	return { panel, details, row };
}

function assertFrostedMenu(metrics) {
	assert.equal(metrics.details.frostedClass, true, 'Only the owned mobile action menu opts into frosted styling');
	assert.ok(parseFloat(metrics.panel.borderRadius) >= 29, 'The mobile panel has broad rounded corners');
	assert.ok(metrics.details.backdropFilter.includes('blur('), 'The mobile panel blurs its background');
	const colorComponents = metrics.panel.background.match(/[\d.]+/g)?.map(Number) || [];
	const alpha = colorComponents.length === 4 ? colorComponents[3] : 1;
	assert.ok(alpha > 0 && alpha < 1, 'The mobile panel surface is translucent');
	assert.notEqual(metrics.panel.shadow, 'none', 'The mobile panel has depth above the transcript');
	assert.equal(metrics.details.overflowX, 'hidden', 'The rounded action panel clips horizontal overflow');
	assert.equal(metrics.details.overflowY, 'auto', 'The rounded action panel owns vertical scrolling');
	assert.equal(metrics.details.innerPadding, '8px', 'The mobile action panel retains spacious inner padding');
	assert.ok(metrics.row.height >= 67, 'Root actions have spacious touch rows');
	assert.equal(metrics.row.fontSize, 18 + metrics.row.fontOffset,
		'Root action labels retain the intended size and application font scaling');
	assert.ok(metrics.row.iconWell, 'A root action icon has a circular well');
	assert.ok(metrics.row.iconWell.width >= 43 && metrics.row.iconWell.height >= 43,
		'Root action icons retain their forty-four-pixel wells');
	assert.equal(metrics.row.iconWell.radius, '50%', 'Root action icon wells have circular corners');
	assert.ok(Math.abs(metrics.row.iconWell.width - metrics.row.iconWell.height) <= 1,
		'Root action icon wells stay circular');
	assertPopupBounds(metrics.panel);
}

function assertActionMenuAnchor(panel, anchor, focused = false) {
	let preferredTop = anchor.y - panel.height - 8;
	if (focused) {
		preferredTop = anchor.y + anchor.height / 2 - panel.height / 2;
	}
	const maximumTop = panel.viewportTop + panel.viewportHeight - panel.height - 8;
	const expectedTop = Math.max(panel.viewportTop + 8, Math.min(preferredTop, maximumTop));
	assert.ok(Math.abs(panel.y - expectedTop) <= 1,
		'The action menu stays near its trigger and clamps within the keyboard viewport');
	const expectedLeft = Math.max(panel.viewportLeft + 8, Math.min(anchor.x,
		panel.viewportLeft + panel.viewportWidth - panel.width - 8));
	assert.ok(Math.abs(panel.x - expectedLeft) <= 1, 'The panel aligns with its anchor inside viewport gutters');
}

async function collapsedPlusAnchor(browser) {
	async function verifyCollapsedPlusAnchor(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		await dismissKeyboard(page);
		const composer = page.locator('.buddy-composer');
		assert.equal(await composer.getAttribute('data-expanded'), 'false', 'The idle empty composer starts collapsed');
		const before = await page.locator(plusTrigger).boundingBox();
		await page.locator(plusTrigger).tap();
		await page.locator(plusMenu).waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		const after = await page.locator(plusTrigger).boundingBox();
		const metrics = await actionMenuMetrics(page, plusMenu, 'Upload Files');
		writeFileSync(resolve(outputDirectory, engineName + '-collapsed-plus-geometry.json'),
			JSON.stringify({ before, after, metrics }, null, 2));
		assert.equal(await composer.getAttribute('data-expanded'), 'false', 'Opening idle Plus keeps the composer collapsed');
		assert.equal((await state(page)).focused, false, 'Opening idle Plus does not summon the draft keyboard');
		assert.ok(Math.abs(before.y - after.y) <= 1, 'The idle Plus button does not move on open');
		assertActionMenuAnchor(metrics.panel, after);
		assertFrostedMenu(metrics);
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-plus-collapsed-open.png') });
		await page.locator(plusTrigger).tap();
		await page.locator(plusMenu).waitFor({ state: 'detached' });
		assert.equal(await composer.getAttribute('data-expanded'), 'false', 'Closing idle Plus retains the collapsed draft');
		assert.equal((await state(page)).focused, false);
	}
	await runCase(browser, 'collapsed-plus-anchor', verifyCollapsedPlusAnchor);
}

async function quietAutofocusPlusAnchor(browser) {
	async function verifyQuietAutofocusPlusAnchor(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const composer = page.locator('.buddy-composer');
		assert.equal(await composer.getAttribute('data-expanded'), 'false',
			'The first-load empty composer has not entered editing');
		assert.equal((await state(page)).focused, true, 'The fixture exercises quiet initial editor autofocus');
		const before = await page.locator(plusTrigger).boundingBox();
		await page.locator(plusTrigger).tap();
		await page.locator(plusMenu).waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		const after = await page.locator(plusTrigger).boundingBox();
		const metrics = await actionMenuMetrics(page, plusMenu, 'Upload Files');
		writeFileSync(resolve(outputDirectory, engineName + '-quiet-autofocus-plus-geometry.json'),
			JSON.stringify({ before, after, metrics, state: await state(page) }, null, 2));
		assert.equal(await composer.getAttribute('data-expanded'), 'false',
			'Quiet initial autofocus does not expand the composer when Plus opens');
		assert.equal((await state(page)).focused, false,
			'The first idle Plus popup does not retain the quiet draft autofocus');
		assert.ok(Math.abs(before.y - after.y) <= 1, 'The first Plus tap preserves its anchor position');
		assertActionMenuAnchor(metrics.panel, after);
		assertFrostedMenu(metrics);
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-quiet-autofocus-plus-open.png') });
		await page.locator(plusTrigger).tap();
		await page.locator(plusMenu).waitFor({ state: 'detached' });
		assert.equal((await state(page)).focused, false, 'Closing the first idle popup does not summon the keyboard');
		assert.equal(await composer.getAttribute('data-expanded'), 'false');
	}
	await runCase(browser, 'quiet-autofocus-plus-anchor', verifyQuietAutofocusPlusAnchor);
}

async function unfocusedToolsAnchor(browser) {
	async function verifyUnfocusedToolsAnchor(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page, 'Keep this draft while the keyboard is closed');
		await dismissKeyboard(page);
		await page.locator(toolsTrigger).tap();
		await page.locator(toolsMenu).waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		const trigger = await page.locator(toolsTrigger).boundingBox();
		const metrics = await actionMenuMetrics(page, toolsMenu, /^Tools\s+1$/);
		writeFileSync(resolve(outputDirectory, engineName + '-unfocused-tools-geometry.json'),
			JSON.stringify({ trigger, metrics }, null, 2));
		assertActionMenuAnchor(metrics.panel, trigger);
		assertFrostedMenu(metrics);
		const current = await state(page);
		assert.equal(current.focused, false, 'Unfocused Tools does not summon the draft keyboard');
		assert.equal(current.draft, draft, 'The keyboard-closed draft survives');
	}
	await runCase(browser, 'unfocused-tools-anchor', verifyUnfocusedToolsAnchor);
}

async function toolsLabelMetrics(page) {
	return page.locator(toolsMenu).getByRole('button', { name: 'Web Search', exact: true })
		.evaluate((button) => {
			const labels = button.querySelectorAll('.truncate');
			const label = labels[labels.length - 1];
			const style = getComputedStyle(label);
			return {
				text: label.textContent.trim(),
				width: label.clientWidth,
				scrollWidth: label.scrollWidth,
				height: label.clientHeight,
				scrollHeight: label.scrollHeight,
				whiteSpace: style.whiteSpace,
				fontSize: style.fontSize,
				rowHeight: button.getBoundingClientRect().height
			};
		});
}

function assertFullToolsLabel(label) {
	assert.equal(label.text, 'Web Search');
	assert.ok(label.scrollWidth <= label.width + 1, 'The complete tool label fits its available width');
	assert.ok(label.scrollHeight <= label.height + 1, 'The complete tool label fits its available height');
}

async function verifyToolsLargeText(page, theme, normalLabel) {
	const original = await page.evaluate(() => {
		const style = document.documentElement.style;
		const property = '--buddy-font-size-offset';
		const previous = { value: style.getPropertyValue(property), priority: style.getPropertyPriority(property) };
		style.setProperty(property, '8px');
		return previous;
	});
	try {
		await page.waitForTimeout(350);
		const largeLabel = await toolsLabelMetrics(page);
		assertFullToolsLabel(largeLabel);
		assert.ok(parseFloat(largeLabel.fontSize) > parseFloat(normalLabel.fontSize),
			'The fixture actually raises the mobile text size');
		assert.ok(largeLabel.rowHeight >= normalLabel.rowHeight, 'A tool label can grow its row to fit larger text');
		assertPopupBounds(await popupMetrics(page, toolsMenu));
		assert.equal((await state(page)).focused, true, 'Larger tool labels preserve draft keyboard focus');
		await page.screenshot({ path: resolve(outputDirectory,
			engineName + '-frosted-tools-' + theme + '-large-text.png') });
		return largeLabel;
	} finally {
		await page.evaluate(({ value, priority }) => {
			const style = document.documentElement.style;
			const property = '--buddy-font-size-offset';
			if (value) {
				style.setProperty(property, value, priority);
			} else {
				style.removeProperty(property);
			}
		}, original);
		await page.waitForTimeout(350);
	}
}

async function frostedActionMenu(browser, trigger, menu, rowName, label, theme) {
	async function verifyFrostedActionMenu(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		const draft = await focusDraft(page);
		await resetEvents(page);
		await page.locator(trigger).tap();
		await page.locator(menu).waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		const composer = await page.locator('.buddy-composer').boundingBox();
		const button = await page.locator(trigger).boundingBox();
		const metrics = await actionMenuMetrics(page, menu, rowName);
		let toolLabel = null;
		let largeToolLabel = null;
		if (label === 'tools') {
			toolLabel = await toolsLabelMetrics(page);
			assertFullToolsLabel(toolLabel);
			await page.screenshot({ path: resolve(outputDirectory,
				engineName + '-frosted-tools-' + theme + '-normal.png') });
			largeToolLabel = await verifyToolsLargeText(page, theme, toolLabel);
		}
		writeFileSync(resolve(outputDirectory, engineName + '-frosted-' + label + '-' + theme + '-geometry.json'),
			JSON.stringify({ composer, button, metrics, toolLabel, largeToolLabel }, null, 2));
		assertFrostedMenu(metrics);
		assertActionMenuAnchor(metrics.panel, button, true);
		await focused(page, draft, 'The frosted action menu preserves draft keyboard focus');
		await noDraftBlur(page);
	}
	await runCase(browser, 'frosted-' + label + '-' + theme, verifyFrostedActionMenu, { theme });
}

async function constrainedActionMenu(browser, trigger, menu, label) {
	async function verifyConstrainedActionMenu(session) {
		await enableComposerFixtures(session);
		const { page, context } = session;
		const draft = await focusDraft(page);
		await page.locator(trigger).tap();
		await page.locator(menu).waitFor({ state: 'visible' });
		let constrainedHeight = 320;
		if (label === 'tools') constrainedHeight = 190;
		await setSyntheticViewport(page, constrainedHeight);
		await page.waitForTimeout(350);
		const panel = await popupMetrics(page, menu);
		const hits = await page.locator(menu).evaluate((element) => {
			return Array.from(element.querySelectorAll('button:not([role="switch"])')).map((button) => {
				const rect = button.getBoundingClientRect();
				const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
				return { label: button.textContent.trim(), receivesTap: Boolean(hit && button.contains(hit)) };
			});
		});
		writeFileSync(resolve(outputDirectory, engineName + '-constrained-' + label + '-geometry.json'),
			JSON.stringify({ panel, hits }, null, 2));
		assertPopupBounds(panel);
		assert.ok(hits.some((hit) => hit.receivesTap), 'A larger keyboard leaves action rows reachable');
		const scroll = await page.locator(menu).evaluate((element) => ({
			height: element.clientHeight, scrollHeight: element.scrollHeight,
			overflow: getComputedStyle(element).overflowY
		}));
		assert.ok(scroll.scrollHeight > scroll.height, 'The constrained action list requires scrolling');
		assert.equal(scroll.overflow, 'auto', 'The outer rounded panel owns action list scrolling');
		if (!webkitRequested) {
			await swipePopup(page, context, menu);
		} else {
			await page.locator(menu).evaluate((element) => { element.scrollTop = element.scrollHeight; });
			await page.waitForTimeout(150);
		}
		assert.ok(await page.locator(menu).evaluate((element) => element.scrollTop > 0),
			'Action scrolling stays inside the constrained panel');
		assertPopupBounds(await popupMetrics(page, menu));
		await focused(page, draft, 'Viewport resizing and action scrolling preserve the draft keyboard focus');
	}
	await runCase(browser, 'constrained-' + label + '-keyboard', verifyConstrainedActionMenu);
}

async function idleAttachmentSearch(browser, finishWithEscape) {
	async function verifyIdleAttachmentSearch(session) {
		await enableComposerFixtures(session);
		const { context, page } = session;
		const attachment = {
			id: 'qa-synthetic-attachment',
			filename: 'Preview attachment.txt',
			meta: { content_type: 'text/plain', size: 12 }
		};
		async function serveSyntheticAttachment(route) {
			assert.equal(route.request().method(), 'GET', 'The attachment fixture only reads synthetic metadata');
			const pathname = new URL(route.request().url()).pathname;
			const response = pathname.endsWith('/search') ? [attachment] : attachment;
			await route.fulfill({ status: 200, json: response });
		}
		await context.route('**/api/v1/files/**', serveSyntheticAttachment);
		await dismissKeyboard(page);
		await page.locator(plusTrigger).tap();
		const menu = page.locator(plusMenu);
		await menu.waitFor({ state: 'visible' });
		await menu.getByRole('button', { name: 'Attach Files', exact: true }).tap();
		const search = menu.getByRole('textbox', { name: 'Search Files', exact: true });
		await search.waitFor({ state: 'visible' });
		assert.equal((await state(page)).focused, false, 'The idle child panel does not focus the draft');
		assert.equal(await page.locator('.buddy-composer').getAttribute('data-expanded'), 'false');
		await search.tap();
		await search.fill('Preview');
		assert.equal(await search.evaluate((element) => element === document.activeElement), true,
			'An idle attachment search receives focus only when tapped');
		await page.evaluate(() => {
			Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 464 });
			visualViewport.dispatchEvent(new Event('resize'));
		});
		await page.waitForTimeout(350);
		assertPopupBounds(await popupMetrics(page, plusMenu));
		await page.screenshot({ path: resolve(outputDirectory,
			engineName + '-idle-attachment-search-' + (finishWithEscape ? 'escape' : 'select') + '.png') });
		if (finishWithEscape) {
			await page.keyboard.press('Escape');
			await menu.waitFor({ state: 'detached' });
			assert.equal((await state(page)).focused, false, 'Escape from an idle search restores idle focus');
			assert.equal(await page.locator('.buddy-composer').getAttribute('data-expanded'), 'false');
			return;
		}
		await resetEvents(page);
		function replaySafariChildPointerGap() {
			// Scrollable lists retain native touch defaults. Replay Safari's blur after
			// the helper captures pointer ownership but before touchstart restores it.
			document.addEventListener('pointerdown', (event) => {
				if (!(event.target instanceof Element)) return;
				if (!event.target.closest('[data-composer-menu] button[data-selected]')) return;
				const input = document.activeElement;
				if (input instanceof HTMLInputElement) input.blur();
			}, { capture: true });
		}
		await page.evaluate(replaySafariChildPointerGap);
		await menu.getByRole('button', { name: 'Preview attachment.txt', exact: true }).tap();
		await menu.waitFor({ state: 'detached' });
		await page.locator('.buddy-composer').getByText('Preview attachment.txt', { exact: true }).waitFor();
		const touchStarts = await page.evaluate(() => window.__qaComposerEvents.filter((event) =>
			event.type === 'touchstart'));
		assert.ok(touchStarts.length > 0, 'The safe result uses a trusted touch gesture');
		assert.ok(touchStarts.every((event) => event.active === 'Search Files'),
			'The deliberate search is restored before the scrollable option receives touchstart');
		assert.equal((await state(page)).draft, '', 'Attaching synthetic metadata does not write a draft');
	}
	const name = finishWithEscape ? 'idle-attachment-search-escape' : 'idle-attachment-search-select';
	await runCase(browser, name, verifyIdleAttachmentSearch);
}

async function setSyntheticViewport(page, height, top = 0) {
	await page.evaluate(({ height, top }) => {
		Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => height });
		Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => top });
		visualViewport.dispatchEvent(new Event('resize'));
		visualViewport.dispatchEvent(new Event('scroll'));
	}, { height, top });
	await page.waitForTimeout(350);
}

async function landscapeActionViewportLifecycle(browser) {
	async function verifyLandscapeActionViewportLifecycle(session) {
		const { page } = session;
		await page.setViewportSize({ width: 844, height: 390 });
		// Reload at landscape width so mobile props arrive from coarse-pointer
		// detection after the children mount, as on a freshly opened landscape phone.
		await page.reload({ waitUntil: 'domcontentloaded' });
		await page.locator('#chat-input').waitFor();
		await page.evaluate(() => document.fonts.ready);
		await page.waitForTimeout(400);
		await enableComposerFixtures(session);
		assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), true);
		await page.locator('#chat-input').tap();
		await page.locator('#chat-input').fill('Keep this landscape draft');
		await page.evaluate(() => { window.__qaOriginalComposer = document.getElementById('chat-input'); });
		await page.locator(modelTrigger).waitFor({ state: 'visible' });
		await page.waitForTimeout(150);
		const measurements = [];
		async function verifyMenuViewportChanges(trigger, menu, label) {
			await setSyntheticViewport(page, 390);
			await page.locator(trigger).tap();
			await page.locator(menu).waitFor({ state: 'visible' });
			assert.equal(await page.locator(menu).evaluate((element) => element.matches('.buddy-action-menu')), true,
				'Fresh landscape controls opt into the mobile action surface');
			await setSyntheticViewport(page, 240);
			const keyboard = await popupMetrics(page, menu);
			assertPopupBounds(keyboard);
			assert.equal(await page.locator('.buddy-dock').getAttribute('aria-hidden'), 'true',
				'The landscape shrink activates the real keyboard layout');
			await setSyntheticViewport(page, 220, 12);
			const panned = await popupMetrics(page, menu);
			assertPopupBounds(panned);
			await focused(page, 'Keep this landscape draft', 'Viewport-only pan and resize retain draft focus');
			await page.screenshot({ path: resolve(outputDirectory,
				engineName + '-landscape-' + label + '-panned.png') });
			await page.keyboard.press('Escape');
			await page.locator(menu).waitFor({ state: 'detached' });
			await setSyntheticViewport(page, 390);
			await page.locator(trigger).tap();
			await page.locator(menu).waitFor({ state: 'visible' });
			assert.equal(await page.locator(menu).evaluate((element) => element.matches('.buddy-action-menu')), true,
				'Fresh landscape controls opt into the mobile action surface');
			await setSyntheticViewport(page, 240);
			const reopened = await popupMetrics(page, menu);
			assertPopupBounds(reopened);
			await page.keyboard.press('Escape');
			await page.locator(menu).waitFor({ state: 'detached' });
			measurements.push({ label, keyboard, panned, reopened });
		}
		await verifyMenuViewportChanges(plusTrigger, plusMenu, 'plus');
		await verifyMenuViewportChanges(toolsTrigger, toolsMenu, 'tools');
		writeFileSync(resolve(outputDirectory, engineName + '-landscape-action-viewports.json'),
			JSON.stringify(measurements, null, 2));
	}
	await runCase(browser, 'landscape-action-viewport-lifecycle', verifyLandscapeActionViewportLifecycle);
}

async function desktopActionMenus(browser) {
	async function verifyDesktopActionMenus(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		await page.locator('#chat-input').click();
		await page.locator('#chat-input').fill('A desktop draft');
		await page.locator(plusTrigger).click();
		const panel = page.locator('[role="menu"] .app-dropdown-menu');
		await panel.waitFor({ state: 'visible' });
		const plus = await popupMetrics(page, '[role="menu"] .app-dropdown-menu');
		assert.equal(await page.locator('.buddy-action-menu').count(), 0, 'Desktop Plus does not opt into mobile styling');
		assert.ok(parseFloat(plus.borderRadius) < 20, 'Desktop Plus retains its existing corner style');
		await page.locator(plusTrigger).click();
		await panel.waitFor({ state: 'detached' });
		await page.locator(toolsTrigger).click();
		await panel.waitFor({ state: 'visible' });
		const tools = await popupMetrics(page, '[role="menu"] .app-dropdown-menu');
		assert.equal(await page.locator('.buddy-action-menu').count(), 0, 'Desktop Tools does not opt into mobile styling');
		assert.ok(parseFloat(tools.borderRadius) < 20, 'Desktop Tools retains its existing corner style');
		const rowHeight = await panel.getByRole('button', { name: /^Tools\s+1$/ }).evaluate((element) =>
			element.getBoundingClientRect().height);
		assert.ok(rowHeight < 44, 'Desktop rows retain their existing compact size');
		assert.equal(await page.locator('#chat-input').textContent(), 'A desktop draft');
	}
	await runCase(browser, 'desktop-action-menus-preserved', verifyDesktopActionMenus, { mobile: false });
}

const iphone12ProMax = devices['iPhone 12 Pro Max'];
assert.deepEqual(iphone12ProMax.viewport, { width: 428, height: 746 });
assert.deepEqual(iphone12ProMax.screen, { width: 428, height: 926 });
assert.equal(iphone12ProMax.deviceScaleFactor, 3);

async function iphoneNewChatGeometry(page, menuSelector) {
	return page.evaluate((selector) => {
		function bounds(element) {
			if (!element) return null;
			const rect = element.getBoundingClientRect();
			const style = getComputedStyle(element);
			return {
				x: rect.x, y: rect.y, width: rect.width, height: rect.height,
				right: rect.right, bottom: rect.bottom, display: style.display,
				overflowX: style.overflowX, overflowY: style.overflowY,
				position: style.position, zIndex: style.zIndex, opacity: style.opacity
			};
		}
		const portal = document.querySelector(selector);
		const composer = document.querySelector('.buddy-composer');
		const composerAncestors = [];
		for (let node = composer; node; node = node.parentElement) {
			const style = getComputedStyle(node);
			composerAncestors.push({ tag: node.tagName, className: node.className, bounds: bounds(node),
				minHeight: style.minHeight, maxHeight: style.maxHeight, flexShrink: style.flexShrink,
				paddingTop: style.paddingTop, paddingBottom: style.paddingBottom });
		}
		const triggers = ['input-menu-button', 'integration-menu-button'].map((id) => {
			const button = document.getElementById(id);
			const rect = button?.getBoundingClientRect();
			const hit = rect && document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
			return { id, bounds: bounds(button), hit: Boolean(hit && (hit === button || button.contains(hit))),
				hitTarget: hit?.id || hit?.className || hit?.tagName };
		});
		const integrations = document.querySelector('.buddy-composer-integrations');
		const toolbarScroll = integrations && { bounds: bounds(integrations),
			scrollWidth: integrations.scrollWidth, clientWidth: integrations.clientWidth, scrollLeft: integrations.scrollLeft };
		const ancestors = [];
		let ancestor = portal?.parentElement;
		while (ancestor) {
			ancestors.push({ tag: ancestor.tagName, className: ancestor.className, bounds: bounds(ancestor) });
			ancestor = ancestor.parentElement;
		}
		const rows = Array.from(portal?.querySelectorAll('button') || []).map((button) => {
			const rect = button.getBoundingClientRect();
			const x = Math.min(rect.right - 2, Math.max(rect.x + 2, rect.x + rect.width / 2));
			const y = Math.min(rect.bottom - 2, Math.max(rect.y + 2, rect.y + rect.height / 2));
			const stack = document.elementsFromPoint(x, y);
			return {
				text: button.textContent.trim(), bounds: bounds(button),
				hit: stack.some((element) => element === button || button.contains(element)),
				hitStack: stack.slice(0, 5).map((element) => element.id || element.className || element.tagName)
			};
		});
		return {
			url: location.pathname,
			device: { innerWidth, innerHeight, screenWidth: screen.width, screenHeight: screen.height, dpr: devicePixelRatio },
			viewport: { width: visualViewport.width, height: visualViewport.height,
				top: visualViewport.offsetTop, left: visualViewport.offsetLeft, scale: visualViewport.scale },
			active: document.activeElement?.id || document.activeElement?.tagName,
			messageCount: document.querySelectorAll('[id^="message-"]').length,
			composer: bounds(document.querySelector('.buddy-composer')),
			shell: bounds(document.querySelector('.buddy-shell')),
			chat: bounds(document.querySelector('.buddy-chat')),
			portal: bounds(portal), ancestors, composerAncestors, triggers, toolbarScroll, rows,
			dock: document.querySelector('.buddy-dock')?.getAttribute('aria-hidden')
		};
	}, menuSelector);
}

function assertIphoneComposerInsideViewport(geometry) {
	const viewportBottom = geometry.viewport.top + geometry.viewport.height;
	assert.ok(geometry.composer.bottom <= viewportBottom,
		'The entire focused new-chat composer fits above the keyboard');
	for (const trigger of geometry.triggers) {
		assert.ok(trigger.bounds.y >= geometry.viewport.top && trigger.bounds.bottom <= viewportBottom,
			'The full ' + trigger.id + ' touch target fits inside the keyboard viewport');
		assert.equal(trigger.hit, true, 'The center of ' + trigger.id + ' receives the trusted tap');
	}
}

async function verifyLastIphoneActionRow(session, menu, name) {
	const { page, context } = session;
	const portal = page.locator(menu);
	if (webkitRequested) {
		await portal.evaluate((element) => { element.scrollTop = element.scrollHeight; });
		await page.waitForTimeout(150);
	} else {
		const scrolling = await portal.evaluate((element) => ({
			visibleHeight: element.clientHeight,
			contentHeight: element.scrollHeight
		}));
		// Short keyboard viewports require more swipes than the ordinary phone layout.
		const minimumSwipeDistance = Math.max(1, scrolling.visibleHeight * 0.5 - 32);
		const maximumAttempts = Math.min(30,
			Math.ceil(scrolling.contentHeight / minimumSwipeDistance) + 2);
		for (let attempt = 0; attempt < maximumAttempts; attempt += 1) {
			const geometry = await iphoneNewChatGeometry(page, menu);
			const rows = geometry.rows.filter((row) => row.text);
			const last = rows[rows.length - 1];
			if (last.hit && last.bounds.bottom <= geometry.portal.bottom) break;
			const previousScrollTop = await portal.evaluate((element) => element.scrollTop);
			await swipePopup(page, context, menu);
			const nextScrollTop = await portal.evaluate((element) => element.scrollTop);
			assert.ok(nextScrollTop > previousScrollTop,
				'A trusted swipe advances the action list toward its last option');
		}
	}
	const geometry = await iphoneNewChatGeometry(page, menu);
	const rows = geometry.rows.filter((row) => row.text);
	const last = rows[rows.length - 1];
	assert.equal(last.hit, true, 'The last action option can receive a tap after panel scrolling');
	assert.ok(last.bounds.y >= geometry.portal.y && last.bounds.bottom <= geometry.portal.bottom,
		'The last action row is fully visible inside the rounded menu');
	writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-last-row-geometry.json'), JSON.stringify(geometry, null, 2));
	await page.screenshot({ path: resolve(outputDirectory, engineName + '-' + name + '-last-row.png') });
	await focused(page, '', 'Scrolling all iPhone action choices preserves draft focus');
}

async function iphoneNewChatAction(browser, trigger, menu, label, keyboardHeight = 430, variant = 'screen') {
	let deviceDescriptor = iphone12ProMax;
	let deviceName = 'iphone12';
	if (variant === 'screen') {
		deviceDescriptor = { ...iphone12ProMax, viewport: iphone12ProMax.screen };
	} else {
		deviceName += '-safari';
	}
	let suffix = '';
	if (keyboardHeight === 320) suffix = '-tight';
	if (keyboardHeight === 280) suffix = '-very-tight';
	const name = deviceName + '-new-chat-' + label + suffix;
	async function verifyIphoneNewChat(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		assert.equal(new URL(page.url()).pathname, '/', 'The test starts in an actual empty new chat');
		await page.locator('#chat-input').tap();
		await page.evaluate((height) => {
			window.__qaOriginalComposer = document.getElementById('chat-input');
			Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => height });
			visualViewport.dispatchEvent(new Event('resize'));
		}, keyboardHeight);
		await page.locator(trigger).waitFor({ state: 'visible' });
		await page.waitForTimeout(250);
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-' + name + '-focused.png') });
		const focusedGeometry = await iphoneNewChatGeometry(page, menu);
		writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-focused-geometry.json'),
			JSON.stringify(focusedGeometry, null, 2));
		assertIphoneComposerInsideViewport(focusedGeometry);
		await resetEvents(page);
		await page.locator(trigger).tap();
		await page.waitForTimeout(350);
		const geometry = await iphoneNewChatGeometry(page, menu);
		writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-geometry.json'), JSON.stringify(geometry, null, 2));
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-' + name + '-opened.png') });
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-' + name + '-visible-viewport.png'),
			clip: { x: 0, y: 0, width: 428, height: keyboardHeight } });
		assert.ok(geometry.portal, 'The first tap creates the action menu');
		assert.ok(geometry.rows.some((row) => row.hit), 'The action menu has visible, hittable options');
		assert.ok(geometry.portal.y >= 7 && geometry.portal.bottom <= keyboardHeight - 7,
			'Action options stay inside the keyboard viewport');
		await focused(page, '', 'An empty iPhone new-chat menu tap retains the draft keyboard');
		await noDraftBlur(page);
		await verifyLastIphoneActionRow(session, menu, name);
		if (label === 'tools') {
			const option = page.locator(menu).getByRole('button', { name: 'Image', exact: true });
			await option.tap();
			assert.equal(await option.getAttribute('aria-pressed'), 'true', 'A harmless Tools option can be selected');
			await focused(page, '', 'Selecting an iPhone Tools option retains the empty draft keyboard');
			await noDraftBlur(page);
		}
	}
	await runCase(browser, name, verifyIphoneNewChat, { path: '/', deviceDescriptor });
}

async function iphoneScrollableToolsFocus(browser) {
	const name = 'iphone12-scrollable-tools-safari-blur';
	async function verifyScrollableToolsFocus(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		await page.locator('#chat-input').tap();
		await setSyntheticViewport(page, 430);
		await page.locator(toolsTrigger).tap();
		const menu = page.locator(toolsMenu);
		await menu.waitFor({ state: 'visible' });
		await menu.getByRole('button', { name: 'Web Search', exact: true }).tap();
		await menu.getByRole('button', { name: 'Image', exact: true }).tap();
		await menu.getByRole('button', { name: /^Tools\s+1$/ }).tap();
		await menu.getByRole('button', { name: 'Preview helper', exact: true }).tap();
		await page.keyboard.press('Escape');
		await menu.waitFor({ state: 'detached' });
		await page.locator('.buddy-composer-integrations').evaluate((element) => {
			// Constrain existing native controls for explicit scroll-row event coverage.
			element.style.setProperty('width', '30px', 'important');
			element.style.setProperty('max-width', '30px', 'important');
			element.style.setProperty('flex', 'none', 'important');
		});
		const crowded = await iphoneNewChatGeometry(page, toolsMenu);
		writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-focused-geometry.json'), JSON.stringify(crowded, null, 2));
		assert.ok(crowded.toolbarScroll.scrollWidth > crowded.toolbarScroll.clientWidth,
			'The explicitly constrained native toolbar genuinely requires horizontal scrolling');
		await page.evaluate(() => {
			window.__qaOriginalComposer = document.getElementById('chat-input');
			document.addEventListener('touchstart', (event) => {
				if (event.defaultPrevented || !(event.target instanceof Element)) return;
				if (!event.target.closest('#integration-menu-button')) return;
				// Replay Safari keyboard dismissal from an uncanceled toolbar touch default.
				document.getElementById('chat-input').blur();
			}, { passive: false });
		});
		await resetEvents(page);
		await page.locator(toolsTrigger).tap();
		await page.waitForTimeout(350);
		writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-geometry.json'),
			JSON.stringify(await iphoneNewChatGeometry(page, toolsMenu), null, 2));
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-' + name + '-opened.png') });
		await menu.waitFor({ state: 'visible' });
		await focused(page, '', 'A crowded Tools trigger keeps the original empty draft focused');
		await noDraftBlur(page);
	}
	await runCase(browser, name, verifyScrollableToolsFocus, {
		path: '/', deviceDescriptor: { ...iphone12ProMax, viewport: iphone12ProMax.screen }
	});
}

async function replayPortalTouchDefault(session, option) {
	const { page, context } = session;
	// Manual touch sequences need the same stable target that locator.tap waits for.
	await page.waitForTimeout(350);
	const point = await option.evaluate((element) => {
		window.__qaNativeOption = element;
		const rect = element.getBoundingClientRect();
		window.__qaPortalStartBounds = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
		return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
	});
	await page.evaluate(() => {
		window.__qaPortalReplay = { injected: false, recoveredImmediately: false, expandedViewport: false,
			startBounds: window.__qaPortalStartBounds };
		document.addEventListener('touchstart', (event) => {
			if (!(event.target instanceof Element) || !window.__qaNativeOption.contains(event.target)) return;
			window.__qaPortalReplay.startWasCanceled = event.defaultPrevented;
			if (event.defaultPrevented) return;
			window.__qaPortalReplay.injected = true;
			const editor = document.getElementById('chat-input');
			editor.blur();
			window.__qaPortalReplay.recoveredImmediately = document.activeElement === editor;
			if (document.activeElement !== editor) {
				window.__qaPortalReplay.expandedViewport = true;
				Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => innerHeight });
				visualViewport.dispatchEvent(new Event('resize'));
			}
		}, { passive: false });
	});
	if (webkitRequested) {
		// Desktop WebKit has no trusted touch-start API. Replay the same exact sequence
		// through WebKit DOM events; the ordinary iPhone cases use trusted locator taps.
		await page.evaluate(({ x, y }) => {
			const target = window.__qaNativeOption;
			target.dispatchEvent(new PointerEvent('pointerdown', {
				bubbles: true, cancelable: true, pointerType: 'touch', isPrimary: true,
				pointerId: 71, button: 0, clientX: x, clientY: y
			}));
			const touch = { identifier: 71, target, clientX: x, clientY: y };
			window.__qaPortalTouch = touch;
			const event = new Event('touchstart', { bubbles: true, cancelable: true });
			Object.defineProperties(event, {
				touches: { value: [touch] }, targetTouches: { value: [touch] }, changedTouches: { value: [touch] }
			});
			target.dispatchEvent(event);
		}, point);
		await page.waitForTimeout(100);
		await page.evaluate(() => {
			const touch = window.__qaPortalTouch;
			const option = window.__qaNativeOption;
			const rect = option.getBoundingClientRect();
			const hit = document.elementFromPoint(touch.clientX, touch.clientY);
			window.__qaPortalReplay.endBounds = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
			window.__qaPortalReplay.endHitSame = hit === option || option.contains(hit);
			const event = new Event('touchend', { bubbles: true, cancelable: true });
			Object.defineProperties(event, {
				touches: { value: [] }, targetTouches: { value: [] }, changedTouches: { value: [window.__qaPortalTouch] }
			});
			window.__qaNativeOption.dispatchEvent(event);
		});
	} else {
		const protocol = await context.newCDPSession(page);
		await protocol.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...point, id: 71 }] });
		await page.waitForTimeout(100);
		await protocol.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
		await protocol.detach();
	}
	await page.waitForTimeout(250);
	return page.evaluate(() => window.__qaPortalReplay);
}

async function iphonePortalNativeBlur(browser, label) {
	const name = 'iphone12-' + label + '-portal-native-blur';
	async function verifyPortalNativeBlur(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		await page.locator('#chat-input').tap();
		await setSyntheticViewport(page, 430);
		await page.evaluate(() => { window.__qaOriginalComposer = document.getElementById('chat-input'); });
		let menuSelector = toolsMenu;
		let trigger = toolsTrigger;
		if (label === 'plus') {
			menuSelector = plusMenu;
			trigger = plusTrigger;
		}
		await page.locator(trigger).tap();
		const menu = page.locator(menuSelector);
		await menu.waitFor({ state: 'visible' });
		let option = menu.getByRole('button', { name: 'Web Search', exact: true });
		if (label === 'plus') {
			await menu.getByRole('button', { name: /^Tool Permissions/ }).tap();
			option = menu.getByRole('button', { name: 'Ask for approval', exact: true });
		}
		await option.waitFor();
		await resetEvents(page);
		const replay = await replayPortalTouchDefault(session, option);
		writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-replay.json'), JSON.stringify(replay, null, 2));
		writeFileSync(resolve(outputDirectory, engineName + '-' + name + '-geometry.json'),
			JSON.stringify(await iphoneNewChatGeometry(page, menuSelector), null, 2));
		await page.screenshot({ path: resolve(outputDirectory, engineName + '-' + name + '-replayed.png') });
		assert.equal(replay.startWasCanceled, false, 'Portal options retain native list scrolling');
		assert.equal(replay.injected, true, 'The native-default blur replay was exercised');
		assert.equal(replay.recoveredImmediately, true, 'Owned input focus recovers before the keyboard viewport moves');
		assert.equal(replay.expandedViewport, false, 'An owned option gesture keeps the keyboard viewport stable');
		if (label === 'plus') {
			await menu.getByRole('button', { name: /^Tool Permissions.*Ask for approval/ }).waitFor();
		} else {
			assert.equal(await option.getAttribute('aria-pressed'), 'true', 'The original touch selects Web Search once');
		}
		await focused(page, '', 'A portal option survives native-default blur with the empty draft focused');
	}
	await runCase(browser, name, verifyPortalNativeBlur, {
		path: '/', deviceDescriptor: { ...iphone12ProMax, viewport: iphone12ProMax.screen }
	});
}

async function iphoneDeliberateDialogFocus(browser) {
	async function verifyDeliberateDialogFocus(session) {
		await enableComposerFixtures(session);
		const { page } = session;
		await page.locator('#chat-input').tap();
		await setSyntheticViewport(page, 430);
		await page.locator(plusTrigger).tap();
		await page.locator(plusMenu).getByRole('button', { name: 'Attach Webpage', exact: true }).tap();
		const url = page.locator('#webpage-url');
		await url.waitFor({ state: 'visible' });
		await url.tap();
		await url.fill('https://example.invalid');
		await page.waitForTimeout(250);
		assert.equal(await url.evaluate((element) => document.activeElement === element), true,
			'A deliberate dialog field keeps native focus without being restored to the draft');
		assert.equal((await page.locator('#chat-input').innerText()).trim(), '', 'The dialog leaves the blank draft intact');
		await page.getByRole('button', { name: 'Close modal', exact: true }).tap();
		await url.waitFor({ state: 'detached' });
	}
	await runCase(browser, 'iphone12-deliberate-dialog-focus', verifyDeliberateDialogFocus, {
		path: '/', deviceDescriptor: { ...iphone12ProMax, viewport: iphone12ProMax.screen }
	});
}

mkdirSync(outputDirectory, { recursive: true });
const browser = await (webkitRequested ? webkit : chromium).launch({ headless: true });
try {
	await iphoneNewChatAction(browser, plusTrigger, plusMenu, 'plus');
	await iphoneNewChatAction(browser, toolsTrigger, toolsMenu, 'tools');
	await iphoneNewChatAction(browser, plusTrigger, plusMenu, 'plus', 320);
	await iphoneNewChatAction(browser, toolsTrigger, toolsMenu, 'tools', 320);
	await iphoneNewChatAction(browser, plusTrigger, plusMenu, 'plus', 430, 'safari');
	await iphoneNewChatAction(browser, toolsTrigger, toolsMenu, 'tools', 430, 'safari');
	await iphoneNewChatAction(browser, plusTrigger, plusMenu, 'plus', 320, 'safari');
	await iphoneNewChatAction(browser, toolsTrigger, toolsMenu, 'tools', 320, 'safari');
	await iphoneNewChatAction(browser, plusTrigger, plusMenu, 'plus', 280);
	await iphoneNewChatAction(browser, toolsTrigger, toolsMenu, 'tools', 280);
	await iphoneNewChatAction(browser, plusTrigger, plusMenu, 'plus', 280, 'safari');
	await iphoneNewChatAction(browser, toolsTrigger, toolsMenu, 'tools', 280, 'safari');
	await iphoneScrollableToolsFocus(browser);
	await iphonePortalNativeBlur(browser, 'plus');
	await iphonePortalNativeBlur(browser, 'tools');
	await iphoneDeliberateDialogFocus(browser);
	await modelSelection(browser, '');
	await thinkingSelection(browser, '');
	if (!diagnosticsOnly) {
		await modelSelection(browser, 'Keep this draft');
		await thinkingSelection(browser, 'Keep this draft');
		await deliberateSearch(browser);
		await conversationDismissal(browser);
		await menuSwitching(browser);
		await safariNativeFocusGap(browser, modelTrigger, modelMenu, 'model');
		await safariNativeFocusGap(browser, thinkingTrigger, thinkingMenu, 'thinking');
		await contextBubble(browser);
		await desktopToolbar(browser);
		await headerDismissal(browser, modelTrigger, modelMenu, 'model');
		await headerDismissal(browser, thinkingTrigger, thinkingMenu, 'thinking');
		if (!webkitRequested) await thinkingMenuSwipe(browser);
		await plusLifecycle(browser);
		await toolsLifecycle(browser);
		await composerPadding(browser);
		await extraSafariFocusGap(browser, plusTrigger, plusMenu, 'plus');
		await extraSafariFocusGap(browser, toolsTrigger, toolsMenu, 'tools');
		await toolsChildSearch(browser);
		await inlineComposerToggle(browser);
		await transcriptDropdownDismissal(browser, plusTrigger, plusMenu, 'plus');
		await transcriptDropdownDismissal(browser, toolsTrigger, toolsMenu, 'tools');
		await editorPadding(browser);
		await pickerAppearance(browser, 'dark');
		await pickerAppearance(browser, 'light');
		await landscapeThinking(browser);
		await collapsedPlusAnchor(browser);
		await quietAutofocusPlusAnchor(browser);
		await unfocusedToolsAnchor(browser);
		await frostedActionMenu(browser, plusTrigger, plusMenu, 'Upload Files', 'plus', 'dark');
		await frostedActionMenu(browser, plusTrigger, plusMenu, 'Upload Files', 'plus', 'light');
		await frostedActionMenu(browser, toolsTrigger, toolsMenu, /^Tools\s+1$/, 'tools', 'dark');
		await frostedActionMenu(browser, toolsTrigger, toolsMenu, /^Tools\s+1$/, 'tools', 'light');
		await constrainedActionMenu(browser, plusTrigger, plusMenu, 'plus');
		await constrainedActionMenu(browser, toolsTrigger, toolsMenu, 'tools');
		await desktopActionMenus(browser);
		await idleAttachmentSearch(browser, false);
		await idleAttachmentSearch(browser, true);
		await landscapeActionViewportLifecycle(browser);
	}
} finally {
	await browser.close();
	writeFileSync(resolve(outputDirectory, engineName + '-results.json'), JSON.stringify(results, null, 2));
}
const failures = results.filter((result) => !result.passed);
console.log(JSON.stringify({ passed: results.length - failures.length, failed: failures.length, results }, null, 2));
if (failures.length) process.exitCode = 1;
