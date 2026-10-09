/** Integrated subscription UI checks. Every API, login, and provider response is synthetic. */
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createSession, origin, workspace } from './lazy-feature-fixture.mjs';
import {
	readProductionSourceMaps,
	resolveProductionExports
} from './production-module-exports.mjs';

const buildDirectory = process.env.BUDDY_UI_TEST_BUILD || resolve(workspace, 'build');
const outputDirectory = resolve(workspace, 'local/tests/.qa/subscription-integration');
const companionExpected = process.env.BUDDY_SUBSCRIPTION_TEST_COMPANION === '1';
const stores = resolveProductionExports(
	readProductionSourceMaps(buildDirectory),
	'/stores/index.ts',
	['showSettings']
);
const subscriptionModel = {
	id: 'codex.synthetic-subscription',
	name: 'Synthetic ChatGPT plan',
	object: 'model',
	owned_by: 'subscription',
	connection_type: 'external',
	subscription: {
		provider: 'codex',
		model: 'synthetic-model',
		description: 'Synthetic subscription UI fixture.',
		efforts: ['low', 'medium', 'high']
	}
};
const baseModel = {
	id: 'buddy-design-model',
	name: 'Buddy local model',
	object: 'model',
	owned_by: 'openai',
	openai: { id: 'buddy-design-model' },
	info: { id: 'buddy-design-model', name: 'Buddy local model', params: {}, meta: {} }
};

function providerFixture(id, signedIn = id === 'codex') {
	return {
		id,
		name: id === 'claude' ? 'Claude' : 'ChatGPT',
		settings: { enable: signedIn, access: 'chat', workspace: '', cli_path: '' },
		status: {
			installed: true,
			signed_in: signedIn,
			account: signedIn ? { email: 'fixture@example.invalid', plan: 'plus' } : {},
			usage: signedIn
				? {
						windows: [
							{ label: 'Unknown', used_percent: null, resets_at: null },
							{ label: 'Unused', used_percent: 0, resets_at: null },
							{ label: 'Busy', used_percent: 90, resets_at: 2000000000 }
						],
						limit_reached: false
					}
				: null
		},
		login: { state: 'idle' },
		models: signedIn ? [{ id: subscriptionModel.id, name: subscriptionModel.name }] : [],
		default_workspace: 'Synthetic workspace; no directory access'
	};
}

function createFixtures(mode = 'normal', role = 'admin') {
	const requests = [];
	const externalRequests = [];
	let heldStart = null;
	let heldPoll = null;
	let heldCheck = null;
	let checkingResolved = false;
	let checkCount = 0;
	let signedIn = false;
	let loginState = 'idle';
	let startCount = 0;
	const waiting = {
		state: 'waiting',
		method: 'browser',
		url: 'https://example.invalid/synthetic-sign-in',
		message: 'Synthetic pending sign-in; no CLI was started.'
	};

	function describeClaude() {
		const provider = providerFixture('claude', signedIn);
		if (mode.startsWith('checking-') && !checkingResolved) {
			provider.status = { checking: true };
		}
		if (mode === 'api-billing') {
			provider.status.api_billing = true;
			provider.status.message =
				'Synthetic API billing account; sign in with a Claude plan instead.';
		}
		return provider;
	}

	async function routeRequest(route) {
		const request = route.request();
		const url = new URL(request.url());
		if (url.origin !== origin) {
			externalRequests.push({ method: request.method(), origin: url.origin });
			await route.abort('blockedbyclient');
			return;
		}
		const path = url.pathname.replace(/\/$/, '');
		if (path === '/api/models' || path === '/api/models/base') {
			requests.push({ method: request.method(), path });
			const data = role === 'admin' ? [baseModel, subscriptionModel] : [baseModel];
			await route.fulfill({ status: 200, json: { object: 'list', data } });
			return;
		}
		if (path === '/openai/config') {
			await route.fulfill({
				status: 200,
				json: {
					ENABLE_OPENAI_API: false,
					OPENAI_API_BASE_URLS: [],
					OPENAI_API_KEYS: [],
					OPENAI_API_CONFIGS: {}
				}
			});
			return;
		}
		if (path === '/ollama/config') {
			await route.fulfill({
				status: 200,
				json: { ENABLE_OLLAMA_API: false, OLLAMA_BASE_URLS: [], OLLAMA_API_CONFIGS: {} }
			});
			return;
		}
		if (!path.startsWith('/api/v1/subscriptions')) {
			await route.fallback();
			return;
		}
		requests.push({ method: request.method(), path, time: Date.now() });
		if (role !== 'admin') {
			await route.fulfill({ status: 403, json: { detail: 'Synthetic administrator guard.' } });
			return;
		}
		if (path === '/api/v1/subscriptions') {
			await route.fulfill({
				status: 200,
				json: { providers: [describeClaude(), providerFixture('codex')] }
			});
			return;
		}
		if (path === '/api/v1/subscriptions/claude/login') {
			if (request.method() === 'POST') {
				startCount += 1;
				if (mode === 'delayed-start' && startCount === 1) {
					heldStart = route;
					return;
				}
				loginState = 'waiting';
				await route.fulfill({ status: 200, json: waiting });
				return;
			}
			if (request.method() === 'DELETE') {
				loginState = 'idle';
				await route.fulfill({ status: 200, json: { state: 'cancelled' } });
				return;
			}
			if (mode === 'stale-poll') {
				heldPoll = route;
				return;
			}
			if (mode === 'success') {
				signedIn = true;
				loginState = 'success';
			}
			await route.fulfill({
				status: 200,
				json: loginState === 'waiting' ? waiting : { state: loginState }
			});
			return;
		}
		if (path === '/api/v1/subscriptions/claude') {
			checkCount += 1;
			if (mode === 'checking-late' || mode === 'checking-late-error') {
				heldCheck = route;
				return;
			}
			if (mode === 'checking-transition' && checkCount >= 2) {
				checkingResolved = true;
			}
			await route.fulfill({ status: 200, json: describeClaude() });
			return;
		}
		throw new Error('Unexpected synthetic subscription action: ' + request.method() + ' ' + path);
	}

	return {
		requests,
		externalRequests,
		beforeNavigation: (context) => context.route('**/*', routeRequest),
		count(method, path = '/api/v1/subscriptions/claude/login') {
			return requests.filter((request) => request.method === method && request.path === path)
				.length;
		},
		hasHeldStart: () => Boolean(heldStart),
		hasHeldPoll: () => Boolean(heldPoll),
		hasHeldCheck: () => Boolean(heldCheck),
		markCheckingReady: () => {
			checkingResolved = true;
		},
		async releaseStart() {
			assert.ok(heldStart, 'A synthetic start request must be pending');
			const route = heldStart;
			heldStart = null;
			loginState = 'waiting';
			await route.fulfill({ status: 200, json: waiting });
		},
		async releasePoll() {
			assert.ok(heldPoll, 'A synthetic poll must be pending');
			const route = heldPoll;
			heldPoll = null;
			await route.fulfill({ status: 200, json: { state: 'success' } });
		},
		async releaseCheck() {
			assert.ok(heldCheck, 'A synthetic status check must be pending');
			const route = heldCheck;
			heldCheck = null;
			if (mode === 'checking-late-error') {
				await route.fulfill({ status: 500, json: { detail: 'Synthetic stale status failure' } });
			} else {
				const provider = providerFixture('claude', false);
				provider.status = { checking: true };
				await route.fulfill({ status: 200, json: provider });
			}
		}
	};
}

async function waitUntil(predicate, message) {
	const deadline = Date.now() + 10000;
	while (!(await predicate()) && Date.now() < deadline) {
		await new Promise((resolveWait) => setTimeout(resolveWait, 40));
	}
	assert.ok(await predicate(), message);
}

async function setSettings(page, value) {
	await page.evaluate(
		async ({ stores, value }) => {
			const module = await import(stores.url);
			module[stores.aliases.showSettings].set(value);
		},
		{ stores, value }
	);
}

function loginDialog(page) {
	return page
		.getByRole('dialog')
		.filter({ has: page.getByRole('heading', { name: 'Sign in to Claude' }) });
}

function providerCard(page, name) {
	return page
		.getByRole('button', { name: 'Configure ' + name + ' subscription', exact: true })
		.locator('xpath=ancestor::div[contains(@class,"flex-col") and contains(@class,"w-full")][1]');
}

async function openLogin(page) {
	await setSettings(page, 'admin:connections');
	await providerCard(page, 'Claude').getByRole('button', { name: 'Sign in', exact: true }).click();
	await loginDialog(page).waitFor();
}

async function startLogin(page) {
	await loginDialog(page).getByRole('button', { name: 'Get sign-in link', exact: true }).click();
}

async function dismiss(page, method) {
	if (method === 'x') {
		await loginDialog(page).getByRole('button', { name: 'Close modal', exact: true }).click();
	} else if (method === 'escape') {
		await page.keyboard.press('Escape');
	} else if (method === 'backdrop') {
		await loginDialog(page).click({ position: { x: 2, y: 2 } });
	} else if (method === 'unmount') {
		await setSettings(page, false);
	} else {
		throw new Error('Unknown dismissal method: ' + method);
	}
	await loginDialog(page).waitFor({ state: 'hidden' });
}

const results = [];
async function runCase(browser, profile, name, mode, callback, role = 'admin') {
	const fixtures = createFixtures(mode, role);
	let session;
	let result = { name: profile.name + '-' + name, passed: false, companionExpected };
	try {
		session = await createSession(browser, {
			mobile: profile.mobile,
			theme: profile.theme,
			reducedMotion: true,
			role,
			beforeNavigation: fixtures.beforeNavigation
		});
		await callback(session, fixtures);
		assert.deepEqual(session.errors, [], 'No uncaught page errors');
		assert.equal(fixtures.externalRequests.length, 0, 'No external/provider/companion calls');
		assert.equal(session.completions.length, 0, 'No model completions');
		const overflow = await session.page.evaluate(
			() => document.documentElement.scrollWidth > window.innerWidth + 1
		);
		assert.equal(overflow, false, 'No horizontal page overflow');
		result.passed = true;
	} catch (error) {
		result.error = error.stack || error.message;
		if (session) {
			await session.page.screenshot({
				path: resolve(outputDirectory, result.name + '-failure.png')
			});
			result.body = (await session.page.locator('body').innerText()).slice(0, 7000);
		}
	} finally {
		result.fixtureRequests = fixtures.requests;
		result.externalRequests = fixtures.externalRequests;
		results.push(result);
		if (session) await session.context.close();
	}
}

async function cancellationCase(browser, profile, method) {
	await runCase(browser, profile, 'dismiss-' + method, 'normal', async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		await loginDialog(page).getByText('Synthetic pending sign-in; no CLI was started.').waitFor();
		await dismiss(page, method);
		await waitUntil(
			() => fixtures.count('DELETE') === 1,
			'Dismissal must cancel the waiting login'
		);
		const pollsAfterClose = fixtures.count('GET');
		await page.waitForTimeout(2200);
		assert.equal(fixtures.count('GET'), pollsAfterClose, 'Polling stops when dismissed');
		await openLogin(page);
		await loginDialog(page).getByRole('button', { name: 'Get sign-in link' }).waitFor();
		assert.equal(await loginDialog(page).getByText('Open Claude sign-in').count(), 0);
		await dismiss(page, 'x');
		assert.equal(fixtures.count('DELETE'), 1, 'Closing an idle modal adds no cancellation');
	});
}

async function delayedStartCase(browser, profile, method) {
	await runCase(
		browser,
		profile,
		'delayed-start-' + method,
		'delayed-start',
		async ({ page }, fixtures) => {
			await openLogin(page);
			await startLogin(page);
			await waitUntil(fixtures.hasHeldStart, 'Synthetic login start must be pending');
			await dismiss(page, method);
			await openLogin(page);
			assert.equal(
				await loginDialog(page).getByRole('button', { name: 'Get sign-in link' }).isDisabled(),
				true
			);
			await fixtures.releaseStart();
			await waitUntil(
				() => fixtures.count('DELETE') === 1,
				'A late waiting start must be cancelled'
			);
			await waitUntil(
				() => loginDialog(page).getByRole('button', { name: 'Get sign-in link' }).isEnabled(),
				'Cleanup must finish before a new login can start'
			);
			assert.equal(
				await loginDialog(page).getByRole('button', { name: 'Get sign-in link' }).isEnabled(),
				true
			);
			assert.equal(await loginDialog(page).getByText('Open Claude sign-in').count(), 0);
			await startLogin(page);
			await loginDialog(page).getByText('Open Claude sign-in').waitFor();
			await dismiss(page, 'x');
			await waitUntil(
				() => fixtures.count('DELETE') === 2,
				'The new session cancels independently'
			);
		}
	);
}

async function stalePollCase(browser, profile, method) {
	await runCase(
		browser,
		profile,
		'stale-poll-' + method,
		'stale-poll',
		async ({ page }, fixtures) => {
			await openLogin(page);
			await startLogin(page);
			await waitUntil(fixtures.hasHeldPoll, 'Synthetic login poll must be pending');
			await dismiss(page, method);
			await waitUntil(() => fixtures.count('DELETE') === 1, 'Waiting login must cancel');
			await openLogin(page);
			const modelReads = fixtures.count('GET', '/api/models');
			await fixtures.releasePoll();
			await page.waitForTimeout(2200);
			assert.equal(
				await loginDialog(page).isVisible(),
				true,
				'A stale success cannot close the new modal'
			);
			assert.equal(
				fixtures.count('GET', '/api/models'),
				modelReads,
				'A stale success cannot refresh models'
			);
			assert.equal(fixtures.count('GET'), 1, 'No polling continues after cancellation');
			assert.equal(await page.getByText('Signed in to Claude', { exact: true }).count(), 0);
			await dismiss(page, 'x');
		}
	);
}

async function successCase(browser, profile) {
	await runCase(browser, profile, 'success', 'success', async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		await loginDialog(page).waitFor({ state: 'hidden', timeout: 10000 });
		await providerCard(page, 'Claude')
			.getByRole('button', { name: 'Sign out', exact: true })
			.waitFor();
		assert.equal(fixtures.count('DELETE'), 0, 'Successful login closure must not cancel');
	});
}

async function checkingTransitionCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'checking-transition',
		'checking-transition',
		async ({ page }, fixtures) => {
			await setSettings(page, 'admin:connections');
			await providerCard(page, 'Claude')
				.getByText('Checking Claude Code…', { exact: true })
				.waitFor();
			await waitUntil(
				() => fixtures.count('GET', '/api/v1/subscriptions/claude') === 2,
				'Checking providers retry until the CLI status arrives'
			);
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign in', exact: true })
				.waitFor();
			const checks = fixtures.requests.filter(
				(request) => request.path === '/api/v1/subscriptions/claude'
			);
			assert.ok(
				checks[1].time - checks[0].time >= 2500,
				'Checking retries keep the three-second cadence'
			);
			await page.waitForTimeout(3300);
			assert.equal(
				fixtures.count('GET', '/api/v1/subscriptions/claude'),
				2,
				'Checking stops once status is known'
			);
		}
	);
}

async function lateCheckCase(browser, profile, errorResponse) {
	const mode = errorResponse ? 'checking-late-error' : 'checking-late';
	await runCase(browser, profile, mode, mode, async ({ page }, fixtures) => {
		await setSettings(page, 'admin:connections');
		await providerCard(page, 'Claude')
			.getByText('Checking Claude Code…', { exact: true })
			.waitFor();
		await waitUntil(fixtures.hasHeldCheck, 'A synthetic status check must be in flight');
		if (!errorResponse) {
			await page.waitForTimeout(3300);
			assert.equal(
				fixtures.count('GET', '/api/v1/subscriptions/claude'),
				1,
				'A slow check cannot overlap another retry'
			);
		}
		await setSettings(page, false);
		await providerCard(page, 'Claude').waitFor({ state: 'hidden' });
		fixtures.markCheckingReady();
		await setSettings(page, 'admin:connections');
		await providerCard(page, 'Claude')
			.getByRole('button', { name: 'Sign in', exact: true })
			.waitFor();
		await fixtures.releaseCheck();
		await page.waitForTimeout(3300);
		assert.equal(
			fixtures.count('GET', '/api/v1/subscriptions/claude'),
			1,
			'An unmounted check must not reschedule requests'
		);
		assert.equal(
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign in', exact: true })
				.isVisible(),
			true
		);
		assert.equal(
			await page.getByText('Synthetic stale status failure', { exact: true }).count(),
			0,
			'An unmounted check must not toast errors'
		);
	});
}

async function pendingCheckUnmountCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'checking-timer-unmount',
		'checking-transition',
		async ({ page }, fixtures) => {
			await setSettings(page, 'admin:connections');
			await providerCard(page, 'Claude')
				.getByText('Checking Claude Code…', { exact: true })
				.waitFor();
			await setSettings(page, false);
			await providerCard(page, 'Claude').waitFor({ state: 'hidden' });
			await page.waitForTimeout(3300);
			assert.equal(
				fixtures.count('GET', '/api/v1/subscriptions/claude'),
				0,
				'Unmount clears pending checking timers'
			);
		}
	);
}

async function apiBillingCase(browser, profile) {
	await runCase(browser, profile, 'api-billing', 'api-billing', async ({ page }) => {
		await setSettings(page, 'admin:connections');
		const card = providerCard(page, 'Claude');
		await card
			.getByText('Synthetic API billing account; sign in with a Claude plan instead.', {
				exact: true
			})
			.waitFor();
		assert.equal(
			await card.getByRole('button', { name: 'Sign in', exact: true }).isVisible(),
			true
		);
		assert.equal(
			await card.getByRole('button', { name: 'Sign out', exact: true }).count(),
			0,
			'API billing is not shown as a signed-in subscription'
		);
	});
}

async function usageNavigationCase(browser, profile) {
	await runCase(browser, profile, 'usage-navigation', 'normal', async ({ page }, fixtures) => {
		const draft = 'Synthetic unsent subscription draft';
		await page.locator('#chat-input').fill(draft);
		await setSettings(page, 'admin:connections');
		const card = providerCard(page, 'ChatGPT');
		await card.getByText('Usage unavailable', { exact: true }).waitFor();
		const unknown = card.getByText('Unknown limit', { exact: true }).locator('xpath=../..');
		assert.equal(
			await unknown.locator('[style*="width:"]').count(),
			0,
			'Unknown usage has no numeric meter'
		);
		assert.doesNotMatch(await unknown.innerText(), /0% used/);
		await card.getByText('0% used', { exact: true }).waitFor();
		await card.getByText('90% used', { exact: true }).waitFor();
		await page.screenshot({
			path: resolve(outputDirectory, profile.name + '-subscription-usage.png')
		});
		await setSettings(page, false);
		assert.equal(
			await page.locator('#chat-input').innerText(),
			draft,
			'Settings preserves the unsent draft'
		);
		await page.locator('#model-selector-model-button').click();
		await page
			.getByRole('option', { name: 'Select Synthetic ChatGPT plan model', exact: true })
			.click();
		assert.match(
			await page.locator('#model-selector-model-button').innerText(),
			/Synthetic ChatGPT plan/
		);
		assert.equal(
			await page.locator('#chat-input').innerText(),
			draft,
			'Selecting a subscription model preserves the draft'
		);
		assert.equal(await page.locator('.buddy-dock a[href="/notes"]').count(), 1);
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor();
		assert.equal(
			await page.locator('#sidebar-companion-button').count(),
			companionExpected ? 1 : 0
		);
		assert.equal(fixtures.count('POST'), 0, 'Browsing subscription settings initiates no login');
		await page.locator('#buddy-sidebar-close').click();
		await page.screenshot({
			path: resolve(outputDirectory, profile.name + '-subscription-navigation.png')
		});
		if (companionExpected) {
			await page.locator('#chat-input').fill('');
			await page.goto(origin + '/companion', { waitUntil: 'domcontentloaded' });
			await page.getByLabel('Host address', { exact: true }).waitFor();
			assert.equal(
				await page.getByLabel('Host address', { exact: true }).inputValue(),
				'http://127.0.0.1:8083'
			);
			assert.equal(
				await page.getByRole('button', { name: 'Pair host', exact: true }).isDisabled(),
				true
			);
			assert.equal(
				fixtures.externalRequests.length,
				0,
				'Subscription identity grants no companion session'
			);
		}
	});
}

async function permissionCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'non-admin',
		'normal',
		async ({ page }, fixtures) => {
			await page.goto(origin + '/?settings=admin%3Aconnections', { waitUntil: 'domcontentloaded' });
			await page.locator('#chat-input').waitFor();
			await page.waitForTimeout(500);
			assert.equal(
				await page
					.getByRole('button', { name: 'Configure Claude subscription', exact: true })
					.count(),
				0
			);
			assert.equal(
				fixtures.requests.filter((request) => request.path.startsWith('/api/v1/subscriptions'))
					.length,
				0
			);
			await setSettings(page, false);
			await page.locator('#model-selector-model-button').click();
			await page.locator('#model-search-input').waitFor();
			assert.equal(
				await page
					.getByRole('option', { name: 'Select Synthetic ChatGPT plan model', exact: true })
					.count(),
				0
			);
		},
		'user'
	);
}

const { chromium } = await import(process.env.BUDDY_PLAYWRIGHT_MODULE || 'playwright');
mkdirSync(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
	for (const profile of [
		{ name: 'desktop', mobile: false, theme: 'light' },
		{ name: 'phone', mobile: true, theme: 'dark' }
	]) {
		await cancellationCase(browser, profile, 'x');
		await cancellationCase(browser, profile, 'escape');
		await cancellationCase(browser, profile, 'backdrop');
		await cancellationCase(browser, profile, 'unmount');
		await delayedStartCase(browser, profile, 'escape');
		await delayedStartCase(browser, profile, 'unmount');
		await stalePollCase(browser, profile, 'escape');
		await stalePollCase(browser, profile, 'unmount');
		await successCase(browser, profile);
		await checkingTransitionCase(browser, profile);
		await lateCheckCase(browser, profile, false);
		await lateCheckCase(browser, profile, true);
		await pendingCheckUnmountCase(browser, profile);
		await apiBillingCase(browser, profile);
		await usageNavigationCase(browser, profile);
		await permissionCase(browser, profile);
	}
} finally {
	await browser.close();
	writeFileSync(resolve(outputDirectory, 'results.json'), JSON.stringify(results, null, 2));
}
const failures = results.filter((result) => !result.passed);
console.log(
	JSON.stringify(
		{ passed: results.length - failures.length, failed: failures.length, results },
		null,
		2
	)
);
if (failures.length) process.exitCode = 1;
