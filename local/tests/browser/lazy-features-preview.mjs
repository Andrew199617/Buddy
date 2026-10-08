/**
 * Production lazy-feature lifecycle regression checks with synthetic APIs only.
 * Build first, then run this against the matching production frontend:
 * BUDDY_PLAYWRIGHT_MODULE may point to the bundled Playwright module URL.
 * BUDDY_LAZY_TEST_ORIGIN defaults to http://127.0.0.1:8082.
 * No provider calls, media permissions, or live account writes are permitted.
 */
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
	readProductionSourceMaps,
	findProductionModule,
	resolveProductionExports
} from './production-module-exports.mjs';
import { createSession, origin, outputDirectory, workspace } from './lazy-feature-fixture.mjs';

const buildDirectory = resolve(workspace, 'build');
const sourceTargets = {
	settings: '/chat/SettingsModal.svelte',
	overview: '/chat/Overview.svelte',
	call: '/chat/MessageInput/CallOverlay.svelte',
	embeds: '/chat/ChatControls/Embeds.svelte',
	artifacts: '/chat/Artifacts.svelte',
	files: '/chat/FileNav.svelte',
	code: '/chat/Messages/CodeBlock.svelte',
	output: '/chat/Messages/OutputEditView.svelte',
	highlighting: '/highlight.js/lib/languages/abnf.js',
	terminalOutput: '/chat/Messages/TerminalOutputFile.svelte'
};

function readProductionModules() {
	const sourceMaps = readProductionSourceMaps(buildDirectory);
	const modules = {};
	for (const [feature, suffix] of Object.entries(sourceTargets)) {
		modules[feature] = findProductionModule(sourceMaps, suffix).url;
	}
	const stores = resolveProductionExports(sourceMaps, '/stores/index.ts', [
		'showSettings',
		'showControls'
	]);
	return { modules: modules, stores: stores };
}

const production = readProductionModules();

async function setStore(page, name, value) {
	await page.evaluate(
		async ({ stores, name, value }) => {
			const module = await import(stores.url);
			const store = module[stores.aliases[name]];
			if (!store?.set) throw new Error('Missing production store: ' + name);
			store.set(value);
		},
		{ stores: production.stores, name: name, value: value }
	);
}

async function getStore(page, name) {
	return page.evaluate(
		async ({ stores, name }) => {
			const module = await import(stores.url);
			let value;
			const unsubscribe = module[stores.aliases[name]].subscribe((current) => {
				value = current;
			});
			unsubscribe();
			return value;
		},
		{ stores: production.stores, name: name }
	);
}

function createModuleGate(feature) {
	let mode = 'hold';
	const heldRoutes = [];
	let requests = 0;
	async function routeModule(route) {
		requests += 1;
		if (mode === 'hold') {
			heldRoutes.push(route);
			return;
		}
		await route.continue();
	}
	async function release() {
		mode = 'continue';
		const routes = heldRoutes.splice(0);
		await Promise.all(routes.map((route) => route.continue()));
	}
	async function fail() {
		mode = 'continue';
		const routes = heldRoutes.splice(0);
		await Promise.all(routes.map((route) => route.abort('failed')));
	}
	return {
		install: (context) => context.route(origin + production.modules[feature] + '*', routeModule),
		release: release,
		fail: fail,
		requestCount: () => requests
	};
}

const results = [];
const caseFilter = process.argv.find((argument) => argument.startsWith('--only='));
let selectedCases = null;
if (caseFilter) selectedCases = caseFilter.slice('--only='.length).split(',');
async function runCase(browser, name, options, check) {
	if (selectedCases && !selectedCases.includes(name)) return;
	let session;
	try {
		session = await createSession(browser, options);
		await session.page.evaluate(() => {
			window.__qaOriginalComposer = document.getElementById('chat-input');
		});
		await check(session);
		assert.deepEqual(session.errors, [], 'No browser runtime errors should occur');
		assert.equal(session.completions.length, 0, 'No model completion should be requested');
		assert.equal(
			await session.page.evaluate(() => window.__qaMediaRequests),
			0,
			'No media access should be attempted'
		);
		results.push({ name: name, passed: true, blockedMutations: session.blockedMutations });
		console.log('PASS ' + name);
	} catch (error) {
		if (session) {
			await session.page.screenshot({ path: resolve(outputDirectory, name + '-failure.png') });
			writeFileSync(
				resolve(outputDirectory, name + '-failure.json'),
				JSON.stringify(
					{
						message: error.message,
						url: session.page.url(),
						errors: session.errors,
						body: await session.page.locator('body').innerText(),
						requests: session.requests
					},
					null,
					2
				)
			);
		}
		results.push({ name: name, passed: false, error: error.message });
		console.error('FAIL ' + name + ': ' + error.message);
	} finally {
		if (session) await session.context.close();
	}
}

async function assertComposerPreserved(page) {
	assert.equal(
		await page.evaluate(
			() => document.getElementById('chat-input') === window.__qaOriginalComposer
		),
		true,
		'Optional feature lifecycle must preserve mounted composer'
	);
}

async function assertSelectedSettingsTab(page, id) {
	const tab = page.locator('[role="tab"][aria-controls="tab-' + id + '"]');
	await tab.waitFor({ state: 'visible', timeout: 30000 });
	assert.equal(await tab.getAttribute('aria-selected'), 'true');
}

async function settingsPendingClose(browser) {
	const gate = createModuleGate('settings');
	await runCase(
		browser,
		'settings-close-while-pending',
		{ beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			await setStore(page, 'showSettings', 'interface');
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			assert.match(await status.innerText(), /Loading Settings/);
			assert.equal(await getStore(page, 'showSettings'), 'interface');
			await status.getByRole('button', { name: 'Close', exact: true }).click();
			assert.equal(await getStore(page, 'showSettings'), false);
			await gate.release();
			await page.waitForTimeout(500);
			assert.equal(await page.locator('#search-input-settings-modal').count(), 0);
			await assertComposerPreserved(page);
			await setStore(page, 'showSettings', 'interface');
			await assertSelectedSettingsTab(page, 'interface');
			assert.equal(gate.requestCount(), 1, 'Reopening should reuse the resolved constructor');
		}
	);
}

async function settingsObjectRequest(browser) {
	const gate = createModuleGate('settings');
	await runCase(
		browser,
		'settings-admin-object-request',
		{ beforeNavigation: gate.install },
		async (session) => {
			const request = { tab: 'admin:models', state: { id: 'buddy-design-model' } };
			await setStore(session.page, 'showSettings', request);
			await session.page.locator('.lazy-feature-status').waitFor();
			assert.deepEqual(await getStore(session.page, 'showSettings'), request);
			await gate.release();
			await assertSelectedSettingsTab(session.page, 'admin-models');
			await session.page.waitForFunction(() =>
				Array.from(document.querySelectorAll('input')).some(
					(input) => input.value === 'Buddy local model'
				)
			);
		}
	);
}

async function settingsPermissions(browser) {
	await runCase(
		browser,
		'settings-user-admin-deeplink',
		{ role: 'user', path: '/c/buddy-design-chat?settings=admin:general' },
		async (session) => {
			await assertSelectedSettingsTab(session.page, 'general');
			assert.equal(
				await session.page.locator('[role="tab"][aria-controls^="tab-admin-"]').count(),
				0
			);
			assert.equal(new URL(session.page.url()).searchParams.has('settings'), false);
		}
	);
	await runCase(
		browser,
		'settings-string-deeplink',
		{ path: '/c/buddy-design-chat?settings=interface' },
		async (session) => {
			await assertSelectedSettingsTab(session.page, 'interface');
			assert.equal(new URL(session.page.url()).searchParams.has('settings'), false);
		}
	);
}

async function settingsRetry(browser) {
	const gate = createModuleGate('settings');
	await runCase(
		browser,
		'settings-network-failure-recovery',
		{ beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			const draft = 'Unsent draft survives an explicit recovery reload';
			await page.locator('#chat-input').fill(draft);
			await page.waitForFunction((expected) => {
				const value = sessionStorage.getItem('chat-input-buddy-design-chat');
				return value && JSON.parse(value).prompt === expected;
			}, draft);
			await setStore(page, 'showSettings', 'interface');
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			await gate.fail();
			await status.getByRole('alert').waitFor();
			assert.match(await status.innerText(), /Could not load Settings/);
			await status.getByRole('button', { name: 'Retry', exact: true }).click();
			await status.getByRole('alert').waitFor();
			assert.equal(gate.requestCount(), 1, 'The failed native import is cached in this document');
			const reload = status.getByRole('button', { name: 'Reload Buddy', exact: true });
			await reload.waitFor();
			const target = await reload.boundingBox();
			assert.ok(target.height >= 44, 'Explicit recovery target must be accessible');
			await reload.click();
			await page.locator('#chat-input').waitFor({ timeout: 30000 });
			await page.waitForFunction(
				(expected) => document.getElementById('chat-input')?.textContent === expected,
				draft
			);
			assert.equal(
				await page.locator('.lazy-feature-status').count(),
				0,
				'Recovery must not open a feature automatically'
			);
			await setStore(page, 'showSettings', 'interface');
			await assertSelectedSettingsTab(page, 'interface');
			assert.equal(gate.requestCount(), 2, 'Reload must allow the missing chunk to load');
		}
	);
}

async function overviewRace(browser, mobile) {
	const gate = createModuleGate('overview');
	let name = 'overview-desktop-race';
	if (mobile) name = 'overview-mobile-race';
	await runCase(
		browser,
		name,
		{ mobile: mobile, beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			await page
				.locator('.buddy-chat-header')
				.getByRole('button', { name: 'Controls', exact: true })
				.click();
			await page.getByRole('button', { name: 'Overview', exact: true }).click();
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			await status.getByRole('button', { name: 'Close', exact: true }).click();
			assert.equal(await getStore(page, 'showControls'), false);
			await gate.release();
			await page.waitForTimeout(300);
			assert.equal(await page.locator('.svelte-flow').count(), 0);
			await assertComposerPreserved(page);
			await page
				.locator('.buddy-chat-header')
				.getByRole('button', { name: 'Controls', exact: true })
				.click();
			await page.locator('.svelte-flow').waitFor({ timeout: 30000 });
			assert.equal(gate.requestCount(), 1);
		}
	);
}

async function callCancellation(browser) {
	const gate = createModuleGate('call');
	await runCase(
		browser,
		'call-cancel-before-component-mount',
		{ beforeNavigation: gate.install },
		async (session) => {
			await setStore(session.page, 'showCallOverlay', true);
			await setStore(session.page, 'showControls', true);
			const status = session.page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			assert.match(await status.innerText(), /Loading Call/);
			await status.getByRole('button', { name: 'Close', exact: true }).click();
			assert.equal(await getStore(session.page, 'showCallOverlay'), false);
			await gate.release();
			await session.page.waitForTimeout(500);
			assert.equal(await getStore(session.page, 'showControls'), false);
			await assertComposerPreserved(session.page);
		}
	);
}

async function artifactRace(browser) {
	const gate = createModuleGate('artifacts');
	await runCase(
		browser,
		'artifacts-delayed-close-and-reopen',
		{ beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			await setStore(page, 'artifactContents', [
				{ type: 'iframe', content: '<p>Isolated artifact</p>' }
			]);
			await setStore(page, 'showArtifacts', true);
			await setStore(page, 'showControls', true);
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			await status.getByRole('button', { name: 'Close', exact: true }).click();
			await gate.release();
			await page.waitForTimeout(300);
			assert.equal(await page.locator('#artifacts-container').count(), 0);
			await setStore(page, 'showArtifacts', true);
			await setStore(page, 'showControls', true);
			await page.locator('#artifacts-container iframe').waitFor();
			assert.equal(gate.requestCount(), 1);
			await assertComposerPreserved(page);
		}
	);
}

async function embedRace(browser) {
	const gate = createModuleGate('embeds');
	async function installFixture(context) {
		await gate.install(context);
		await context.route(origin + '/__qa_embed*', (route) =>
			route.fulfill({ status: 200, contentType: 'text/html', body: '<p>Isolated embed</p>' })
		);
	}
	await runCase(
		browser,
		'embed-delayed-close-and-reopen',
		{ beforeNavigation: installFixture },
		async (session) => {
			const page = session.page;
			await setStore(page, 'embed', { url: origin + '/__qa_embed', title: 'Isolated embed' });
			await setStore(page, 'showEmbeds', true);
			await setStore(page, 'showControls', true);
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			await status.getByRole('button', { name: 'Close', exact: true }).click();
			await gate.release();
			await page.waitForTimeout(300);
			assert.equal(await page.getByRole('button', { name: 'Close embed', exact: true }).count(), 0);
			await setStore(page, 'showEmbeds', true);
			await setStore(page, 'showControls', true);
			await page.getByRole('button', { name: 'Close embed', exact: true }).waitFor();
			await page.getByRole('button', { name: 'Close embed', exact: true }).click();
			assert.equal(await getStore(page, 'showEmbeds'), false);
			assert.equal(gate.requestCount(), 1);
			await assertComposerPreserved(page);
		}
	);
}

async function fileRace(browser) {
	const gate = createModuleGate('files');
	await runCase(
		browser,
		'file-browser-delayed-close-and-reopen',
		{ beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			await setStore(page, 'terminalServers', [
				{
					id: 'qa-terminal',
					name: 'Test files',
					url: origin + '/api/v1/terminals/qa-terminal',
					contexts: { chat: { context_id: 'chat_id' } },
					config: {}
				}
			]);
			await setStore(page, 'selectedTerminalId', 'qa-terminal');
			await setStore(page, 'showControls', true);
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			assert.match(await status.innerText(), /Loading Files/);
			await status.getByRole('button', { name: 'Close', exact: true }).click();
			await gate.release();
			await page.waitForTimeout(300);
			assert.equal(await getStore(page, 'showControls'), false);
			await setStore(page, 'showControls', true);
			await page.getByText('readme.txt', { exact: true }).waitFor({ timeout: 30000 });
			assert.equal(gate.requestCount(), 1);
			await assertComposerPreserved(page);
		}
	);
}

async function codeWrapper(browser) {
	const gate = createModuleGate('code');
	await runCase(
		browser,
		'code-wrapper-fallback-and-tools',
		{ code: true, beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			assert.match(await status.innerText(), /Loading Code tools/);
			assert.match(
				await page.locator('#message-design-buddy-message pre').first().innerText(),
				/today =/
			);
			await gate.release();
			const copy = page.locator('#message-design-buddy-message .copy-code-button');
			await copy.waitFor({ timeout: 30000 });
			assert.equal(
				await page.locator('#message-design-buddy-message .run-code-button').count(),
				0,
				'Disabled code execution stays disabled'
			);
			await page.getByRole('button', { name: 'Collapse', exact: true }).click();
			await page.getByRole('button', { name: 'Expand', exact: true }).click();
			const editor = page.locator('#message-design-buddy-message .cm-content');
			assert.match(await editor.innerText(), /today =/);
			await editor.fill('print("Updated isolated code")');
			await page.locator('#message-design-buddy-message .save-code-button').click();
			await page.getByText('print("Updated isolated code")', { exact: true }).waitFor();
			await assertComposerPreserved(page);
		}
	);
}

async function structuredEditor(browser) {
	const gate = createModuleGate('output');
	await runCase(
		browser,
		'structured-editor-lazy-cancel-and-save',
		{ structured: true, beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			const response = page.locator('#message-design-buddy-message');
			await response.getByRole('button', { name: 'Edit', exact: true }).click();
			const status = page.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			assert.match(await status.innerText(), /Loading Message editor/);
			await page.locator('#close-edit-message-button').click();
			await gate.release();
			await page.waitForTimeout(300);
			assert.equal(await page.getByPlaceholder('Message text...').count(), 0);
			await response.getByRole('button', { name: 'Edit', exact: true }).click();
			const text = page.getByPlaceholder('Message text...');
			await text.waitFor({ timeout: 30000 });
			await text.fill('Updated visual output');
			await page.getByRole('button', { name: 'Visual', exact: true }).click();
			const editor = response.locator('.cm-content');
			await editor.waitFor();
			assert.match(await editor.innerText(), /Updated visual output/);
			await editor.fill('invalid JSON');
			await response.getByText('Invalid JSON', { exact: true }).waitFor();
			const output = [
				{
					type: 'message',
					role: 'assistant',
					content: [{ type: 'output_text', text: 'Updated JSON output' }]
				}
			];
			await editor.fill(JSON.stringify(output, null, 2));
			await page.getByRole('button', { name: 'JSON', exact: true }).click();
			assert.equal(await text.inputValue(), 'Updated JSON output');
			await page.locator('#confirm-edit-message-button').click();
			await page.waitForFunction(() => !document.getElementById('confirm-edit-message-button'));
			await response.getByText('Updated JSON output', { exact: true }).waitFor();
			await assertComposerPreserved(page);
			assert.equal(gate.requestCount(), 1);
			assert.ok(
				session.mutations.some((request) => request.path === '/api/v1/chats/buddy-design-chat'),
				'Saving must reach the mocked persistence API'
			);
		}
	);
}

async function composerHighlighting(browser) {
	const gate = createModuleGate('highlighting');
	await runCase(
		browser,
		'composer-highlighting-draft-and-history',
		{ mobile: true, theme: 'light', path: '/', beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			assert.equal(
				gate.requestCount(),
				0,
				'Full language grammars must stay out of an empty composer'
			);
			const input = page.locator('#chat-input');
			await input.focus();
			await input.evaluate((node) => {
				const data = new DataTransfer();
				const code = 'def hello():\n    print("Buddy")';
				data.setData('text/plain', code);
				data.setData('text/html', '<pre><code class="language-python">' + code + '</code></pre>');
				const paste = new ClipboardEvent('paste', {
					clipboardData: data,
					bubbles: true,
					cancelable: true
				});
				node.dispatchEvent(paste);
			});
			await input.locator('pre code').waitFor();
			const status = page.getByText('Loading code highlighting…', { exact: true });
			await status.waitFor();
			assert.match(await input.textContent(), /def hello/);
			await page.keyboard.type(' # still editing');
			const whileLoading = await input.textContent();
			assert.match(whileLoading, /still editing/);
			await gate.release();
			await input.locator('.hljs-keyword').first().waitFor({ timeout: 30000 });
			await status.waitFor({ state: 'detached' });
			assert.equal(
				await input.textContent(),
				whileLoading,
				'Highlighting must retain the exact draft'
			);
			await page.keyboard.press('Control+z');
			assert.notEqual(
				await input.textContent(),
				whileLoading,
				'Undo history must survive highlighting'
			);
			await page.keyboard.press('Control+Shift+z');
			assert.equal(await input.textContent(), whileLoading, 'Redo must restore the exact draft');
			await assertComposerPreserved(page);
		}
	);
}

async function terminalOutputPreview(browser) {
	const gate = createModuleGate('terminalOutput');
	await runCase(
		browser,
		'terminal-output-file-lazy-preview',
		{ terminalOutput: true, beforeNavigation: gate.install },
		async (session) => {
			const page = session.page;
			const response = page.locator('#message-design-buddy-message');
			const status = response.locator('.lazy-feature-status');
			await status.getByRole('status').waitFor();
			assert.match(await status.innerText(), /Loading File preview/);
			await response.getByText('readme.txt', { exact: true }).waitFor();
			await setStore(page, 'terminalServers', [
				{
					id: 'qa-terminal',
					name: 'Test files',
					url: origin + '/api/v1/terminals/qa-terminal',
					contexts: { chat: { context_id: 'chat_id' } },
					config: {}
				}
			]);
			await setStore(page, 'selectedTerminalId', 'qa-terminal');
			await gate.release();
			await response
				.getByText('Isolated terminal preview content', { exact: true })
				.waitFor({ timeout: 30000 });
			const toggle = response.getByRole('button', { name: 'readme.txt', exact: true });
			assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
			await toggle.click();
			assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
			assert.equal(
				await response.getByText('Isolated terminal preview content', { exact: true }).count(),
				0
			);
			await toggle.click();
			await response.getByText('Isolated terminal preview content', { exact: true }).waitFor();
			assert.equal(gate.requestCount(), 1);
			await assertComposerPreserved(page);
			assert.equal(
				session.requests.filter(
					(request) => request === 'GET /api/v1/terminals/qa-terminal/files/read'
				).length,
				1,
				'Collapsing and reopening the file must retain its native preview data'
			);
		}
	);
}

const playwrightSpecifier = process.env.BUDDY_PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(playwrightSpecifier);
mkdirSync(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
	await settingsPendingClose(browser);
	await settingsObjectRequest(browser);
	await settingsPermissions(browser);
	await settingsRetry(browser);
	await overviewRace(browser, false);
	await overviewRace(browser, true);
	await callCancellation(browser);
	await artifactRace(browser);
	await embedRace(browser);
	await fileRace(browser);
	await codeWrapper(browser);
	await structuredEditor(browser);
	await composerHighlighting(browser);
	await terminalOutputPreview(browser);
} finally {
	await browser.close();
	writeFileSync(resolve(outputDirectory, 'results.json'), JSON.stringify(results, null, 2));
}
const failures = results.filter((result) => !result.passed);
console.log(
	JSON.stringify(
		{ passed: results.length - failures.length, failed: failures.length, results: results },
		null,
		2
	)
);
if (failures.length) process.exitCode = 1;
