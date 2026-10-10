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
const selectedCases = new Set(
	(process.argv.find((argument) => argument.startsWith('--only='))?.slice(7) || '')
		.split(',')
		.filter(Boolean)
);
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
		settings: {
			enable: signedIn,
			access: 'chat',
			workspace: '',
			cli_path: '',
			machine_id: 'local'
		},
		machine: { id: 'local', name: 'This server' },
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
	const webSocketRequests = [];
	const fixtureErrors = [];
	let heldStart = null;
	let heldPoll = null;
	let heldCheck = null;
	let heldCheckProvider = null;
	let heldMachineAction = null;
	let heldConfig = null;
	let heldLogout = null;
	let machineActionCount = 0;
	let configCount = 0;
	let revisionCount = 1;
	const settingsByProvider = {};
	const machineKeys = new Map([
		['runner-a', 'synthetic-saved-key-a'],
		['runner-b', 'synthetic-saved-key-b']
	]);
	let machines = [
		{ id: 'local', name: 'This server', url: null },
		{
			id: 'runner-a',
			revision: 'fixture-revision-a-1',
			name: 'Synthetic computer A',
			url: 'https://host.docker.internal:8765',
			browser_url: 'http://127.0.0.1:8765',
			host: {
				id: 'fixture-host-a',
				name: 'Synthetic computer A',
				platform: 'win32',
				version: 'fixture'
			},
			capabilities: {
				directories: true,
				files: true,
				workspaces: true,
				terminals: true,
				pty: true,
				providerExecution: true
			}
		},
		{
			id: 'runner-b',
			revision: 'fixture-revision-b-1',
			name: 'Synthetic computer B',
			url: 'https://runner-b.example.invalid',
			browser_url: null
		}
	];
	let checkingResolved = false;
	let checkCount = 0;
	let pollCount = 0;
	let signedIn = mode.startsWith('logout-');
	let loginState = 'idle';
	let startCount = 0;
	const waiting = {
		state: 'waiting',
		method: 'browser',
		url: 'https://example.invalid/synthetic-sign-in',
		message: 'Synthetic pending sign-in; no CLI was started.',
		needs_code:
			mode === 'code-login' || mode === 'remote-code-login' || mode === 'poll-terminal-code'
	};
	if (
		mode.includes('machine-remove') ||
		mode.includes('login-machine-change') ||
		mode.includes('revision') ||
		mode === 'checking-registry' ||
		mode === 'remote-code-login'
	) {
		settingsByProvider.claude = {
			...providerFixture('claude').settings,
			machine_id: 'runner-a',
			enable: true,
			access: 'full',
			workspace: '/fixture/a',
			cli_path: '/fixture/claude-a'
		};
	}
	if (mode.startsWith('settings-')) {
		settingsByProvider.claude = {
			...providerFixture('claude').settings,
			...settingsByProvider.claude,
			enable: true,
			access: 'full',
			workspace: '/fixture/old',
			cli_path: '/fixture/claude-old'
		};
	}

	function describeProvider(id) {
		const provider = providerFixture(id, id === 'codex' || signedIn || mode === 'api-billing');
		provider.settings = { ...provider.settings, ...settingsByProvider[id] };
		const machine = machines.find((machine) => machine.id === provider.settings.machine_id);
		provider.machine = {
			id: provider.settings.machine_id,
			name: machine?.name || 'Unavailable machine',
			revision: machine?.revision
		};
		provider.status.cli_path =
			provider.settings.cli_path || '/fixture/cli-' + provider.settings.machine_id;
		if (!machine) {
			provider.status = {
				installed: false,
				signed_in: false,
				message: 'Selected machine is unavailable.'
			};
			provider.models = [];
		}
		return provider;
	}

	function describeClaude() {
		const provider = describeProvider('claude');
		if (
			(mode.startsWith('checking-') || mode.startsWith('settings-status-')) &&
			!checkingResolved
		) {
			provider.status = { checking: true };
		}
		if (mode === 'api-billing') {
			provider.status.api_billing = true;
			provider.status.account = { email: 'fixture@example.invalid', auth_method: 'apiKey' };
			provider.status.usage = null;
			provider.models = [];
			provider.status.message =
				'Synthetic API billing account; sign in with a Claude plan instead.';
		}
		return provider;
	}

	function ownsCurrentMachine(request, id) {
		const provider = describeProvider(id);
		return (
			request.expectedMachineId === provider.settings.machine_id &&
			(provider.settings.machine_id === 'local' ||
				request.expectedMachineRevision === provider.machine.revision)
		);
	}

	function recoversMissingMachine(request, id) {
		const current = describeProvider(id).settings;
		return (
			request.method === 'POST' &&
			request.path.endsWith('/config') &&
			current.machine_id !== 'local' &&
			request.expectedMachineId === current.machine_id &&
			!machines.some((machine) => machine.id === current.machine_id) &&
			typeof request.body?.machine_id === 'string' &&
			request.body.machine_id.length > 0 &&
			request.body.machine_id !== current.machine_id
		);
	}

	function saveConfig(id, body) {
		const { expected_machine_id, expected_machine_revision, ...settings } = body;
		const current = describeProvider(id).settings;
		settingsByProvider[id] = { ...current, ...settings };
		if (settings.machine_id && settings.machine_id !== current.machine_id) {
			Object.assign(settingsByProvider[id], {
				enable: false,
				access: 'chat',
				workspace: '',
				cli_path: ''
			});
		}
		return id === 'claude' ? describeClaude() : describeProvider(id);
	}

	async function machineResponse(route, path, method, body) {
		if (path.endsWith('/verify')) {
			await route.fulfill({
				status: 200,
				json: {
					ok: true,
					version: 1,
					hostname: 'Synthetic verified computer A',
					platform: 'fixture',
					chat_dir: '/fixture/chat',
					default_workspace: '/fixture/work'
				}
			});
			return;
		}
		if (method === 'DELETE') {
			const id = decodeURIComponent(path.split('/').at(-1));
			machines = machines.filter((machine) => machine.id !== id);
			machineKeys.delete(id);
			for (const providerId of ['claude', 'codex']) {
				if (describeProvider(providerId).settings.machine_id === id) {
					settingsByProvider[providerId] = {
						...describeProvider(providerId).settings,
						enable: false,
						access: 'chat',
						workspace: '',
						cli_path: ''
					};
				}
			}
			await route.fulfill({ status: 200, json: { machines } });
			return;
		}
		const id = body.id || 'runner-new';
		const old = machines.find((machine) => machine.id === id);
		const normalizedUrl = body.url.replace(/\/+$/, '');
		const nextKey = body.key || machineKeys.get(id);
		const identityChanged = !old || old.url !== normalizedUrl || machineKeys.get(id) !== nextKey;
		machineKeys.set(id, nextKey);
		const machine = {
			...old,
			id,
			name: body.name,
			url: normalizedUrl,
			browser_url: body.browser_url ?? null,
			revision: identityChanged ? 'fixture-revision-write-' + ++revisionCount : old.revision
		};
		machines = [...machines.filter((machine) => machine.id !== id), machine];
		for (const providerId of ['claude', 'codex']) {
			if (identityChanged && describeProvider(providerId).settings.machine_id === id) {
				settingsByProvider[providerId] = {
					...describeProvider(providerId).settings,
					enable: false,
					access: 'chat',
					workspace: '',
					cli_path: ''
				};
				if (providerId === 'claude') {
					loginState = 'idle';
					signedIn = false;
				}
			}
		}
		await route.fulfill({ status: 200, json: { machine, machines } });
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
		const body = request.postDataJSON();
		const recordedRequest = {
			method: request.method(),
			path,
			time: Date.now(),
			body,
			expectedMachineId: body?.expected_machine_id ?? url.searchParams.get('expected_machine_id'),
			expectedMachineRevision:
				body?.expected_machine_revision ?? url.searchParams.get('expected_machine_revision')
		};
		requests.push(recordedRequest);
		if (role !== 'admin') {
			await route.fulfill({ status: 403, json: { detail: 'Synthetic administrator guard.' } });
			return;
		}
		const providerId = path.split('/')[4];
		if (providerId === 'claude' || providerId === 'codex') {
			const recovering = recoversMissingMachine(recordedRequest, providerId);
			assert.equal(
				typeof recordedRequest.expectedMachineId,
				'string',
				'Every provider action captures its selected machine'
			);
			if (recordedRequest.expectedMachineId !== 'local' && !recovering)
				assert.equal(
					typeof recordedRequest.expectedMachineRevision,
					'string',
					'Remote actions capture their machine revision'
				);
			if (!ownsCurrentMachine(recordedRequest, providerId) && !recovering) {
				recordedRequest.guardRejected = true;
				await route.fulfill({
					status: 409,
					json: { detail: 'Synthetic selected machine changed; refresh required.' }
				});
				return;
			}
		}
		if (path === '/api/v1/subscriptions') {
			await route.fulfill({
				status: 200,
				json: { providers: [describeClaude(), describeProvider('codex')], machines }
			});
			return;
		}
		if (path.startsWith('/api/v1/subscriptions/machines')) {
			if (request.method() === 'GET') {
				await route.fulfill({ status: 200, json: { machines } });
				return;
			}
			machineActionCount += 1;
			if (
				mode.startsWith('machine-') &&
				mode !== 'machine-crud' &&
				mode !== 'machine-remove' &&
				machineActionCount === 1
			) {
				heldMachineAction = { route, path, method: request.method(), body };
				return;
			}
			await machineResponse(route, path, request.method(), body);
			return;
		}
		if (path.endsWith('/config')) {
			configCount += 1;
			const id = path.split('/').at(-2);
			if (mode === 'settings-rejection' && configCount === 1) {
				await route.fulfill({
					status: 500,
					json: { detail: 'Synthetic recoverable settings failure' }
				});
				return;
			}
			if (
				(mode.startsWith('settings-delayed') || mode.startsWith('enable-delayed')) &&
				configCount === 1
			) {
				heldConfig = { route, id, body, recordedRequest };
				return;
			}
			await route.fulfill({ status: 200, json: saveConfig(id, body) });
			return;
		}
		if (path === '/api/v1/subscriptions/claude/logout') {
			const response = describeClaude();
			response.status = { installed: true, signed_in: false };
			response.models = [];
			if (mode.startsWith('logout-')) {
				heldLogout = { route, response, recordedRequest };
				return;
			}
			signedIn = false;
			await route.fulfill({ status: 200, json: response });
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
			if (mode.startsWith('poll-terminal-')) {
				pollCount += 1;
				if (pollCount === 1) {
					heldPoll = route;
					return;
				}
				loginState =
					mode === 'poll-terminal-code' ? 'waiting' : mode.slice('poll-terminal-'.length);
				await route.fulfill({
					status: 200,
					json: { state: loginState, message: 'Synthetic terminal login result' }
				});
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
		if (path === '/api/v1/subscriptions/claude/login/code') {
			assert.equal(
				body.code,
				'synthetic-approval-code',
				'Only fixture codes reach the intercepted API'
			);
			if (mode === 'poll-terminal-code') {
				loginState = 'error';
				await route.fulfill({
					status: 200,
					json: { state: 'error', message: 'Synthetic terminal login result' }
				});
				return;
			}
			signedIn = true;
			loginState = 'success';
			await route.fulfill({ status: 200, json: { state: 'success' } });
			return;
		}
		if (path === '/api/v1/subscriptions/claude') {
			checkCount += 1;
			if (
				(mode === 'checking-late' ||
					mode === 'checking-late-error' ||
					mode === 'checking-registry' ||
					mode.startsWith('settings-status-')) &&
				checkCount === 1
			) {
				heldCheck = route;
				heldCheckProvider = describeClaude();
				return;
			}
			if (
				(mode === 'checking-transition' ||
					mode.startsWith('settings-status-') ||
					mode === 'checking-registry') &&
				checkCount >= 2
			) {
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
		webSocketRequests,
		fixtureErrors,
		beforeNavigation: async (context) => {
			await context.routeWebSocket('**/*', (socket) => {
				webSocketRequests.push(socket.url());
				return socket.close({ code: 1008, reason: 'Synthetic fixture blocks socket transports' });
			});
			await context.route('**/*', async (route) => {
				try {
					await routeRequest(route);
				} catch (error) {
					fixtureErrors.push(error.stack || error.message);
					await route
						.fulfill({ status: 500, json: { detail: 'Synthetic fixture assertion failed' } })
						.catch(() => {});
				}
			});
		},
		count(method, path = '/api/v1/subscriptions/claude/login') {
			return requests.filter((request) => request.method === method && request.path === path)
				.length;
		},
		hasHeldStart: () => Boolean(heldStart),
		hasHeldPoll: () => Boolean(heldPoll),
		hasHeldCheck: () => Boolean(heldCheck),
		hasHeldMachineAction: () => Boolean(heldMachineAction),
		hasHeldConfig: () => Boolean(heldConfig),
		hasHeldLogout: () => Boolean(heldLogout),
		providerSettings: (id = 'claude') => ({ ...describeProvider(id).settings }),
		externalMachineSwitch() {
			loginState = 'idle';
			signedIn = false;
			settingsByProvider.claude = {
				...describeProvider('claude').settings,
				machine_id: 'runner-b',
				enable: false,
				access: 'chat',
				workspace: '',
				cli_path: ''
			};
		},
		externalRevisionReplacement() {
			const machine = machines.find((machine) => machine.id === 'runner-a');
			machine.revision = 'fixture-revision-a-2';
			machine.url = 'https://replacement-a.example.invalid';
			loginState = 'idle';
			signedIn = false;
			settingsByProvider.claude = {
				...describeProvider('claude').settings,
				enable: false,
				access: 'chat',
				workspace: '',
				cli_path: ''
			};
		},
		async releaseLogout(error = false) {
			assert.ok(heldLogout, 'A synthetic logout must be pending');
			const { route, response, recordedRequest } = heldLogout;
			heldLogout = null;
			if (!ownsCurrentMachine(recordedRequest, 'claude')) {
				recordedRequest.guardRejected = true;
				await route.fulfill({
					status: 409,
					json: { detail: 'Synthetic selected machine changed; refresh required.' }
				});
				return;
			}
			signedIn = false;
			if (error)
				await route.fulfill({ status: 500, json: { detail: 'Synthetic stale logout failure' } });
			else await route.fulfill({ status: 200, json: response });
		},
		async releaseMachineAction(error = false) {
			assert.ok(heldMachineAction, 'A synthetic machine action must be pending');
			const { route, path, method, body } = heldMachineAction;
			heldMachineAction = null;
			if (error)
				await route.fulfill({ status: 500, json: { detail: 'Synthetic stale machine failure' } });
			else await machineResponse(route, path, method, body);
		},
		async releaseConfig(error = false) {
			assert.ok(heldConfig, 'A synthetic settings save must be pending');
			const { route, id, body, recordedRequest } = heldConfig;
			heldConfig = null;
			if (
				!ownsCurrentMachine(recordedRequest, id) &&
				!recoversMissingMachine(recordedRequest, id)
			) {
				recordedRequest.guardRejected = true;
				await route.fulfill({
					status: 409,
					json: { detail: 'Synthetic selected machine changed; refresh required.' }
				});
				return;
			}
			if (error)
				await route.fulfill({ status: 500, json: { detail: 'Synthetic settings failure' } });
			else await route.fulfill({ status: 200, json: saveConfig(id, body) });
		},
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
		async releaseWaitingPoll() {
			assert.ok(heldPoll, 'An older synthetic waiting poll must be pending');
			const route = heldPoll;
			heldPoll = null;
			await route.fulfill({ status: 200, json: waiting });
		},
		async releaseCheck() {
			assert.ok(heldCheck, 'A synthetic status check must be pending');
			const route = heldCheck;
			heldCheck = null;
			if (mode === 'checking-late-error' || mode === 'settings-status-switch-error') {
				await route.fulfill({ status: 500, json: { detail: 'Synthetic stale status failure' } });
			} else {
				await route.fulfill({ status: 200, json: heldCheckProvider });
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

async function captureScreenshot(page, name) {
	// Svelte's opening transition can still be running after the dialog becomes visible.
	await page.waitForTimeout(200);
	await page.screenshot({ path: resolve(outputDirectory, name + '.png'), animations: 'disabled' });
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
	await dismissDialog(page, loginDialog(page), method);
}

async function dismissDialog(page, dialog, method) {
	if (method === 'x') {
		await dialog.getByRole('button', { name: 'Close modal', exact: true }).click();
	} else if (method === 'escape') {
		await page.keyboard.press('Escape');
	} else if (method === 'backdrop') {
		await dialog.click({ position: { x: 2, y: 2 } });
	} else if (method === 'unmount') {
		await setSettings(page, false);
	} else {
		throw new Error('Unknown dismissal method: ' + method);
	}
	await dialog.waitFor({ state: 'hidden' });
}

function settingsDialog(page, name = 'Claude') {
	return page
		.getByRole('dialog')
		.filter({ has: page.getByRole('heading', { name: name + ' subscription', exact: true }) });
}

function machineDialog(page) {
	return page
		.getByRole('dialog')
		.filter({ has: page.getByRole('heading', { name: /^(Add|Edit) machine$/ }) });
}

async function openProviderSettings(page, name = 'Claude') {
	await setSettings(page, 'admin:connections');
	await page
		.getByRole('button', { name: 'Configure ' + name + ' subscription', exact: true })
		.click();
	await settingsDialog(page, name).waitFor();
}

async function openMachine(page, name = null) {
	await setSettings(page, 'admin:connections');
	await page
		.getByRole('button', { name: name ? 'Configure ' + name : 'Add machine', exact: true })
		.click();
	await machineDialog(page).waitFor();
}

const machinesPath = '/api/v1/subscriptions/machines';
const configPath = '/api/v1/subscriptions/claude/config';
const statusPath = '/api/v1/subscriptions/claude';

const results = [];
async function runCase(browser, profile, name, mode, callback, role = 'admin') {
	if (
		selectedCases.size &&
		!selectedCases.has(name) &&
		!selectedCases.has(profile.name + '-' + name)
	) {
		return;
	}
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
		assert.deepEqual(fixtures.fixtureErrors, [], 'All API requests match the synthetic contract');
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
		result.blockedWebSockets = fixtures.webSocketRequests;
		result.fixtureErrors = fixtures.fixtureErrors;
		results.push(result);
		if (session) await session.context.close();
		console.log(JSON.stringify({ case: result.name, passed: result.passed }));
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

async function terminalPollCase(browser, profile, terminal) {
	const name = 'poll-terminal-' + terminal;
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		await waitUntil(fixtures.hasHeldPoll, 'An older login waiting response must be held');
		if (terminal === 'code') {
			await loginDialog(page).locator('#subscription-login-code').fill('synthetic-approval-code');
			await loginDialog(page).getByRole('button', { name: 'Finish', exact: true }).click();
		}
		await loginDialog(page)
			.getByRole('button', { name: 'Get sign-in link', exact: true })
			.waitFor();
		if (terminal === 'error' || terminal === 'code') {
			await loginDialog(page)
				.getByText('Synthetic terminal login result', { exact: true })
				.waitFor();
		}
		const polls = fixtures.count('GET');
		const modelReads = fixtures.count('GET', '/api/models');
		assert.equal(polls, terminal === 'code' ? 1 : 2);
		await fixtures.releaseWaitingPoll();
		await page.waitForTimeout(2200);
		assert.equal(
			await loginDialog(page).getByRole('button', { name: 'Get sign-in link' }).isVisible(),
			true
		);
		assert.equal(await loginDialog(page).getByText('Open Claude sign-in').count(), 0);
		assert.equal(
			fixtures.count('GET'),
			polls,
			'Late waiting responses cannot restart a completed polling flow'
		);
		assert.equal(fixtures.count('GET', '/api/models'), modelReads);
		assert.equal(fixtures.count('POST'), 1);
		await dismiss(page, 'x');
	});
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

async function codeLoginCase(browser, profile, remote = false) {
	const name = remote ? 'remote-code-login' : 'code-login';
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		await loginDialog(page).locator('#subscription-login-code').fill('synthetic-approval-code');
		await loginDialog(page).getByRole('button', { name: 'Finish', exact: true }).click();
		await loginDialog(page).waitFor({ state: 'hidden' });
		await providerCard(page, 'Claude')
			.getByRole('button', { name: 'Sign out', exact: true })
			.waitFor();
		const codeRequest = fixtures.requests.find((request) =>
			request.path.endsWith('/claude/login/code')
		);
		assert.equal(codeRequest.expectedMachineId, remote ? 'runner-a' : 'local');
		assert.equal(codeRequest.expectedMachineRevision, remote ? 'fixture-revision-a-1' : null);
		assert.notEqual(codeRequest.guardRejected, true, 'The captured owner accepts this code');
		if (remote) {
			const startRequest = fixtures.requests.find(
				(request) => request.method === 'POST' && request.path === statusPath + '/login'
			);
			assert.equal(startRequest.expectedMachineId, 'runner-a');
			assert.equal(startRequest.expectedMachineRevision, 'fixture-revision-a-1');
			assert.equal(fixtures.providerSettings().machine_id, 'runner-a');
		}
		assert.equal(fixtures.count('DELETE'), 0);
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
	await runCase(browser, profile, 'api-billing', 'api-billing', async ({ page }, fixtures) => {
		await setSettings(page, 'admin:connections');
		const card = providerCard(page, 'Claude');
		await card
			.getByText('Synthetic API billing account; sign in with a Claude plan instead.', {
				exact: true
			})
			.waitFor();
		assert.equal(
			await card.getByRole('button', { name: 'Sign out', exact: true }).isVisible(),
			true
		);
		assert.equal(
			await card.getByRole('button', { name: 'Sign in', exact: true }).count(),
			0,
			'Signed-in CLI credentials retain Sign out while API billing is disclosed'
		);
		assert.doesNotMatch(await card.innerText(), /Plus|% used|Synthetic ChatGPT plan/);
		assert.equal(await card.locator('[style*="width:"]').count(), 0);
		assert.equal(fixtures.count('POST'), 0, 'Showing an API-billing warning starts no login');
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
		const busyWindow = card.getByText('Busy limit', { exact: true }).locator('xpath=../..');
		await busyWindow.getByText(/^90% used\s*\u00b7\s*resets /).waitFor();
		assert.equal(await busyWindow.locator('[style*="width: 90%"]').count(), 1);
		await card.scrollIntoViewIfNeeded();
		await captureScreenshot(page, profile.name + '-subscription-usage');
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
		await captureScreenshot(page, profile.name + '-subscription-navigation');
		if (companionExpected) {
			await page.locator('#chat-input').fill('');
			await page.goto(origin + '/companion', { waitUntil: 'domcontentloaded' });
			await page.getByLabel('Host address', { exact: true }).waitFor();
			assert.equal(
				await page.getByLabel('Host address', { exact: true }).inputValue(),
				'http://127.0.0.1:8765'
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
			await page.goto(origin + '/c/buddy-design-chat?settings=admin%3Aconnections', {
				waitUntil: 'domcontentloaded'
			});
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
			await page.locator('#chat-input').click();
			await waitUntil(
				() =>
					page
						.locator('#message-input-container')
						.getAttribute('data-expanded')
						.then((value) => value === 'true'),
				'Normal composer interaction reveals the model picker'
			);
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

async function machineCrudCase(browser, profile) {
	await runCase(browser, profile, 'machine-crud', 'machine-crud', async ({ page }, fixtures) => {
		await openMachine(page);
		const dialog = machineDialog(page);
		assert.equal(
			await dialog.locator('#machine-url').inputValue(),
			'https://host.docker.internal:8765'
		);
		assert.equal(
			await dialog.locator('#machine-browser-url').inputValue(),
			'',
			'Backend address is never inferred as browser address'
		);
		await dialog.locator('#machine-name').fill('Synthetic new computer');
		await dialog.locator('#machine-browser-url').fill('http://127.0.0.1:8765');
		await dialog.locator('#machine-key').fill('synthetic-runner-key-never-used');
		await dialog.getByRole('button', { name: 'Verify', exact: true }).click();
		await dialog
			.getByText('Connected to Synthetic verified computer A (fixture)', { exact: true })
			.waitFor();
		const verified = fixtures.requests.find((request) => request.path === machinesPath + '/verify');
		assert.equal(verified.body.url, 'https://host.docker.internal:8765');
		assert.equal(verified.body.browser_url, 'http://127.0.0.1:8765');
		await dialog.getByRole('button', { name: 'Save', exact: true }).click();
		await dialog.waitFor({ state: 'hidden' });
		await page
			.getByRole('button', { name: 'Configure Synthetic new computer', exact: true })
			.waitFor();
		await openMachine(page, 'Synthetic new computer');
		assert.equal(
			await machineDialog(page).locator('#machine-key').inputValue(),
			'',
			'Registry never sends a saved key back'
		);
		assert.equal(
			await machineDialog(page).locator('#machine-browser-url').inputValue(),
			'http://127.0.0.1:8765'
		);
		await machineDialog(page).locator('#machine-browser-url').fill('');
		await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await machineDialog(page).waitFor({ state: 'hidden' });
		const updates = fixtures.requests.filter(
			(request) => request.method === 'POST' && request.path === machinesPath
		);
		assert.equal(updates.length, 2);
		assert.equal(updates[0].body.url, 'https://host.docker.internal:8765');
		assert.equal(updates[0].body.browser_url, 'http://127.0.0.1:8765');
		assert.equal(updates[1].body.url, 'https://host.docker.internal:8765');
		assert.equal(updates[1].body.id, 'runner-new');
		assert.equal(
			updates[1].body.browser_url,
			null,
			'Clearing optional browser address is explicit'
		);
		assert.equal(updates[1].body.key, undefined, 'Blank edit preserves existing server key');
		await openMachine(page, 'Synthetic new computer');
		await machineDialog(page).getByRole('button', { name: 'Remove', exact: true }).click();
		await machineDialog(page).waitFor({ state: 'hidden' });
		await page
			.getByRole('button', { name: 'Configure Synthetic new computer', exact: true })
			.waitFor({ state: 'hidden' });
		assert.equal(fixtures.count('DELETE', machinesPath + '/runner-new'), 1);
	});
}

async function delayedMachineCase(browser, profile, action, method, errorResponse = false) {
	const name = 'machine-' + action + '-' + method + (errorResponse ? '-error' : '');
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await openMachine(page, 'Synthetic computer A');
		const dialog = machineDialog(page);
		if (action === 'verify') {
			await dialog.locator('#machine-key').fill('synthetic-delayed-key');
		} else if (action === 'save') {
			await dialog.locator('#machine-name').fill('Synthetic renamed computer A');
			await dialog.locator('#machine-browser-url').fill('https://fixture-a.example.invalid');
		}
		await dialog
			.getByRole('button', {
				name: action === 'remove' ? 'Remove' : action === 'save' ? 'Save' : 'Verify',
				exact: true
			})
			.click();
		await waitUntil(fixtures.hasHeldMachineAction, 'Synthetic machine action must be pending');
		assert.equal(
			await dialog.locator('#machine-url').isDisabled(),
			true,
			'Endpoint cannot change under pending verification/write'
		);
		await dismissDialog(page, dialog, method);
		await openMachine(page, 'Synthetic computer B');
		const reopened = machineDialog(page);
		if (action !== 'verify' || method !== 'unmount') {
			assert.equal(
				await reopened.getByRole('button', { name: 'Save', exact: true }).isDisabled(),
				true,
				'A registry write cannot overlap across reopen/remount'
			);
		}
		if (method === 'unmount' && action === 'verify') {
			await reopened.locator('#machine-name').fill('Synthetic new verify draft');
		}
		await fixtures.releaseMachineAction(errorResponse);
		await waitUntil(
			() => reopened.getByRole('button', { name: 'Save', exact: true }).isEnabled(),
			'Busy state recovers when the pending action settles'
		);
		assert.equal(await reopened.isVisible(), true, 'Old completion cannot dismiss a new dialog');
		assert.equal(
			await reopened.locator('#machine-name').inputValue(),
			method === 'unmount' && action === 'verify'
				? 'Synthetic new verify draft'
				: 'Synthetic computer B'
		);
		assert.equal(
			await reopened.locator('#machine-url').inputValue(),
			'https://runner-b.example.invalid'
		);
		assert.equal(
			await reopened.getByText(/Connected to Synthetic verified/).count(),
			0,
			'Old verification is not shown for another endpoint'
		);
		assert.equal(
			await page.getByText('Synthetic stale machine failure', { exact: true }).count(),
			0,
			'Dismissed errors are ignored'
		);
		assert.equal(
			await page.getByText(/^(Saved|Removed) Synthetic/).count(),
			0,
			'Dismissed writes do not toast into another dialog'
		);
		if (action === 'save' && !errorResponse) {
			await page
				.getByRole('button', { name: 'Configure Synthetic renamed computer A', exact: true })
				.waitFor({ state: 'attached' });
			const request = fixtures.requests.find(
				(request) => request.method === 'POST' && request.path === machinesPath
			);
			assert.equal(request.body.id, 'runner-a');
			assert.equal(request.body.browser_url, 'https://fixture-a.example.invalid');
		}
		if (action === 'remove' && !errorResponse) {
			assert.equal(fixtures.count('DELETE', machinesPath + '/runner-a'), 1);
			await providerCard(page, 'Claude')
				.getByText('Selected machine is unavailable.', { exact: true })
				.waitFor({ state: 'attached' });
		}
		await reopened.locator('#machine-name').fill('Synthetic preserved new draft');
		await page.waitForTimeout(150);
		assert.equal(
			await reopened.locator('#machine-name').inputValue(),
			'Synthetic preserved new draft'
		);
		await dismissDialog(page, reopened, 'x');
	});
}

async function staleStatusSettingsCase(browser, profile, variant) {
	const name = 'settings-status-' + variant;
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await setSettings(page, 'admin:connections');
		await waitUntil(fixtures.hasHeldCheck, 'Old machine status must be in flight');
		await openProviderSettings(page);
		const dialog = settingsDialog(page);
		if (variant === 'workspace') {
			await dialog.locator('#subscription-workspace').fill('/fixture/new-workspace');
			await dialog.locator('#subscription-cli-path').fill('/fixture/new-cli');
		} else {
			await dialog.locator('#subscription-machine').selectOption('runner-b');
			assert.equal(await dialog.locator('input[value="chat"]').isChecked(), true);
			assert.equal(await dialog.locator('#subscription-workspace').inputValue(), '');
			assert.equal(await dialog.locator('#subscription-cli-path').inputValue(), '');
			assert.equal(
				await dialog.locator('input[value="full"]').isDisabled(),
				true,
				'Old authority cannot transfer to another machine'
			);
			await dialog.getByText(/Changing machines disables this subscription/).waitFor();
		}
		await dialog.getByRole('button', { name: 'Save', exact: true }).click();
		await dialog.waitFor({ state: 'hidden' });
		const card = providerCard(page, 'Claude');
		if (variant !== 'workspace') {
			await card.getByText(/via Claude Code on Synthetic computer B/).waitFor();
			assert.equal(
				await card.getByRole('switch').getAttribute('aria-checked'),
				'false',
				'Server reset requires a separate explicit enable'
			);
		}
		await fixtures.releaseCheck();
		await waitUntil(
			() => fixtures.count('GET', statusPath) === 2,
			'Current checking status schedules its own three-second retry'
		);
		await card.getByRole('button', { name: 'Sign in', exact: true }).waitFor();
		assert.equal(
			await page.getByText('Synthetic stale status failure', { exact: true }).count(),
			0
		);
		assert.equal(fixtures.count('POST'), 0, 'Host changes never begin a sign-in');
		await openProviderSettings(page);
		assert.equal(
			await settingsDialog(page).locator('#subscription-machine').inputValue(),
			variant === 'workspace' ? 'local' : 'runner-b'
		);
		assert.equal(
			await settingsDialog(page).locator('#subscription-workspace').inputValue(),
			variant === 'workspace' ? '/fixture/new-workspace' : ''
		);
		assert.equal(
			await settingsDialog(page).locator('#subscription-cli-path').inputValue(),
			variant === 'workspace' ? '/fixture/new-cli' : ''
		);
		await captureScreenshot(page, profile.name + '-' + name);
		await dismissDialog(page, settingsDialog(page), 'x');
	});
}

async function staleStatusRegistryCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'checking-registry',
		'checking-registry',
		async ({ page }, fixtures) => {
			await setSettings(page, 'admin:connections');
			await waitUntil(fixtures.hasHeldCheck, 'Old registry status must be pending');
			await openMachine(page, 'Synthetic computer A');
			await machineDialog(page).locator('#machine-name').fill('Synthetic updated computer A');
			await machineDialog(page)
				.locator('#machine-url')
				.fill('https://new-runner-a.example.invalid');
			await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
			await machineDialog(page).waitFor({ state: 'hidden' });
			await providerCard(page, 'Claude')
				.getByText(/via Claude Code on Synthetic updated computer A/)
				.waitFor();
			await fixtures.releaseCheck();
			await waitUntil(
				() => fixtures.count('GET', statusPath) === 2,
				'Registry reload invalidates old check and retains new checking retry'
			);
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign in', exact: true })
				.waitFor();
			assert.equal(
				await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
				'false'
			);
			assert.match(await providerCard(page, 'Claude').innerText(), /Synthetic updated computer A/);
		}
	);
}

async function delayedSettingsCase(browser, profile, method, errorResponse = false) {
	const name = 'settings-delayed-' + method + (errorResponse ? '-error' : '');
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await openProviderSettings(page);
		await settingsDialog(page).locator('#subscription-workspace').fill('/fixture/accepted-first');
		await settingsDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await waitUntil(fixtures.hasHeldConfig, 'Settings save must be pending');
		await dismissDialog(page, settingsDialog(page), method);
		await openProviderSettings(page);
		const reopened = settingsDialog(page);
		if (method.startsWith('unmount')) {
			await reopened.locator('#subscription-workspace').fill('/fixture/reopened-draft');
			await reopened.getByRole('button', { name: 'Save', exact: true }).click();
			assert.equal(
				fixtures.count('POST', configPath),
				1,
				'Pending config write cannot overlap across remount'
			);
		}
		const draft = await reopened.locator('#subscription-workspace').inputValue();
		const registryReads = fixtures.count('GET', '/api/v1/subscriptions');
		await fixtures.releaseConfig(errorResponse);
		await waitUntil(
			() => reopened.getByRole('button', { name: 'Save', exact: true }).isEnabled(),
			'Settings Save recovers after pending completion'
		);
		if (!errorResponse)
			await waitUntil(
				() => fixtures.count('GET', '/api/v1/subscriptions') > registryReads,
				'Accepted config synchronizes the current page across remount'
			);
		await page.waitForTimeout(150);
		assert.equal(
			await reopened.isVisible(),
			true,
			'Old settings save does not dismiss reopened dialog'
		);
		assert.equal(
			await reopened.locator('#subscription-workspace').inputValue(),
			draft,
			'Status/registry refresh preserves an open draft'
		);
		assert.equal(
			await page.getByText('Synthetic settings failure', { exact: true }).count(),
			0,
			'Dismissed config errors do not toast'
		);
		await reopened.locator('#subscription-workspace').fill('/fixture/explicit-second');
		await reopened.getByRole('button', { name: 'Save', exact: true }).click();
		await reopened.waitFor({ state: 'hidden' });
		assert.equal(
			fixtures.count('POST', configPath),
			2,
			'A later explicit save works after success or rejection'
		);
	});
}

async function delayedEnableCase(browser, profile, outcome = 'success') {
	const name = 'enable-delayed-' + outcome;
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await setSettings(page, 'admin:connections');
		const toggle = providerCard(page, 'Claude').getByRole('switch');
		assert.equal(await toggle.getAttribute('aria-checked'), 'false');
		await toggle.click();
		await waitUntil(fixtures.hasHeldConfig, 'The synthetic enable save must be pending');
		let expectedDraft = 'true';
		if (outcome === 'remount') {
			await setSettings(page, false);
			await providerCard(page, 'Claude').waitFor({ state: 'hidden' });
			await setSettings(page, 'admin:connections');
			await toggle.waitFor();
			expectedDraft = 'false';
		}
		assert.equal(
			await toggle.isDisabled(),
			true,
			'Pending writes disable this owner across remounts'
		);
		assert.equal(await toggle.getAttribute('aria-checked'), expectedDraft);
		await assert.rejects(toggle.click({ timeout: 250 }), { name: 'TimeoutError' });
		assert.equal(
			await toggle.getAttribute('aria-checked'),
			expectedDraft,
			'A busy click cannot change the draft'
		);
		assert.equal(
			fixtures.providerSettings().enable,
			false,
			'Held enable has not changed accepted settings'
		);
		assert.equal(
			fixtures.count('POST', configPath),
			1,
			'Rapid clicks never create a second config write'
		);
		await fixtures.releaseConfig(outcome === 'error');
		await waitUntil(
			async () => !(await toggle.isDisabled()),
			'Enable completes and releases its busy state'
		);
		const accepted = outcome !== 'error';
		await waitUntil(
			async () => (await toggle.getAttribute('aria-checked')) === String(accepted),
			'The switch must agree with the accepted config response'
		);
		assert.equal(fixtures.providerSettings().enable, accepted);
		assert.equal(fixtures.count('POST', configPath), 1);
		assert.equal(fixtures.count('POST'), 0, 'Enabling alone never starts CLI sign-in');
		if (outcome === 'success') {
			await loginDialog(page).waitFor();
			await dismiss(page, 'x');
		}
		if (accepted) {
			await toggle.click();
			await waitUntil(
				async () =>
					(await toggle.getAttribute('aria-checked')) === 'false' && !(await toggle.isDisabled()),
				'An explicit click after completion can disable the accepted subscription'
			);
			assert.equal(fixtures.providerSettings().enable, false);
			assert.equal(fixtures.count('POST', configPath), 2);
		}
	});
}

async function removedMachineCase(browser, profile, replacement = 'runner-b') {
	await runCase(
		browser,
		profile,
		replacement === 'local' ? 'machine-remove-to-local' : 'machine-remove',
		'machine-remove',
		async ({ page }, fixtures) => {
			await openMachine(page, 'Synthetic computer A');
			await machineDialog(page).getByRole('button', { name: 'Remove', exact: true }).click();
			await machineDialog(page).waitFor({ state: 'hidden' });
			await providerCard(page, 'Claude')
				.getByText('Selected machine is unavailable.', { exact: true })
				.waitFor();
			assert.equal(
				await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
				'false'
			);
			await openProviderSettings(page);
			const dialog = settingsDialog(page);
			assert.equal(
				await dialog.locator('#subscription-machine').inputValue(),
				'runner-a',
				'Deleted machine never silently falls back to local server'
			);
			assert.equal(await dialog.locator('option:checked').innerText(), 'Unavailable machine');
			assert.equal(await dialog.locator('input[value="chat"]').isChecked(), true);
			assert.equal(await dialog.locator('#subscription-workspace').inputValue(), '');
			assert.equal(await dialog.locator('#subscription-cli-path').inputValue(), '');
			assert.equal(fixtures.count('POST'), 0);
			assert.equal(
				fixtures.count('POST', configPath),
				0,
				'Removing a selected host never writes a local-server fallback config'
			);
			await dialog.locator('#subscription-machine').selectOption(replacement);
			await dialog.getByRole('button', { name: 'Save', exact: true }).click();
			await dialog.waitFor({ state: 'hidden' });
			const recovery = fixtures.requests.find((request) => request.path === configPath);
			assert.equal(recovery.body.machine_id, replacement);
			assert.equal(recovery.expectedMachineId, 'runner-a');
			assert.equal(
				recovery.expectedMachineRevision,
				null,
				'A deleted machine has no revision left to capture'
			);
			assert.equal(fixtures.providerSettings().machine_id, replacement);
			assert.equal(
				fixtures.providerSettings().enable,
				false,
				'Recovery requires separate enable approval'
			);
			assert.equal(fixtures.providerSettings().access, 'chat');
			await openProviderSettings(page);
			assert.equal(await dialog.locator('#subscription-machine').inputValue(), replacement);
			assert.equal(await dialog.locator('input[value="read"]').isDisabled(), false);
			assert.equal(await dialog.locator('#subscription-workspace').inputValue(), '');
			assert.equal(await dialog.locator('#subscription-cli-path').inputValue(), '');
			await dismissDialog(page, dialog, 'x');
			await providerCard(page, 'Claude').getByRole('switch').click();
			await loginDialog(page).waitFor();
			assert.equal(fixtures.providerSettings().enable, true);
			assert.equal(fixtures.count('POST', configPath), 2);
			assert.equal(
				fixtures.count('POST'),
				0,
				'Recovering and enabling never start sign-in automatically'
			);
			await dismiss(page, 'x');
		}
	);
}

async function delayedLogoutCase(browser, profile, method) {
	const name = 'logout-' + method;
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await setSettings(page, 'admin:connections');
		await providerCard(page, 'Claude')
			.getByRole('button', { name: 'Sign out', exact: true })
			.click();
		await waitUntil(fixtures.hasHeldLogout, 'Synthetic logout must be pending');
		if (method.startsWith('unmount')) {
			await setSettings(page, false);
			await providerCard(page, 'Claude').waitFor({ state: 'hidden' });
		}
		if (method === 'unmount-same') {
			await setSettings(page, 'admin:connections');
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign out', exact: true })
				.waitFor();
			const reads = fixtures.count('GET', '/api/v1/subscriptions');
			await fixtures.releaseLogout();
			await waitUntil(
				() => fixtures.count('GET', '/api/v1/subscriptions') > reads,
				'Accepted logout completion refreshes the newly mounted page'
			);
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign in', exact: true })
				.waitFor();
			assert.equal(await page.getByText('Signed out of Claude', { exact: true }).count(), 0);
			return;
		}
		await openProviderSettings(page);
		await settingsDialog(page).locator('#subscription-machine').selectOption('runner-b');
		await settingsDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await settingsDialog(page).waitFor({ state: 'hidden' });
		await providerCard(page, 'Claude')
			.getByText(/via Claude Code on Synthetic computer B/)
			.waitFor();
		const reads = fixtures.count('GET', '/api/v1/subscriptions');
		await fixtures.releaseLogout();
		const logout = fixtures.requests.find((request) => request.path === statusPath + '/logout');
		assert.equal(
			logout.guardRejected,
			true,
			'Old-host logout is rejected before any account action'
		);
		await page.waitForTimeout(150);
		assert.equal(
			fixtures.count('GET', '/api/v1/subscriptions'),
			reads,
			'Rejected logout emits no accepted-write completion'
		);
		assert.match(await providerCard(page, 'Claude').innerText(), /Synthetic computer B/);
		assert.equal(
			await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
			'false'
		);
		assert.equal(
			await page.getByText('Signed out of Claude', { exact: true }).count(),
			0,
			'Old-machine logout cannot toast into a new host context'
		);
		assert.equal(fixtures.count('POST', statusPath + '/logout'), 1);
		assert.equal(fixtures.count('POST'), 0);
	});
}

async function staleConfigGuardCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'settings-delayed-host-guard',
		'settings-delayed-host-guard',
		async ({ page }, fixtures) => {
			await openProviderSettings(page);
			await settingsDialog(page).locator('#subscription-workspace').fill('/fixture/old-host-draft');
			await settingsDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
			await waitUntil(fixtures.hasHeldConfig, 'Old-host config save must be pending');
			await dismissDialog(page, settingsDialog(page), 'x');
			fixtures.externalMachineSwitch();
			await openMachine(page, 'Synthetic computer B');
			await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
			await machineDialog(page).waitFor({ state: 'hidden' });
			await providerCard(page, 'Claude')
				.getByText(/via Claude Code on Synthetic computer B/)
				.waitFor();
			await fixtures.releaseConfig();
			assert.equal(
				fixtures.requests.find((request) => request.path === configPath).guardRejected,
				true,
				'Old-machine config cannot apply to the new host'
			);
			await page.waitForTimeout(150);
			assert.equal(
				await page
					.getByText('Synthetic selected machine changed; refresh required.', { exact: true })
					.count(),
				0
			);
			await openProviderSettings(page);
			assert.equal(
				await settingsDialog(page).locator('#subscription-machine').inputValue(),
				'runner-b'
			);
			assert.equal(await settingsDialog(page).locator('#subscription-workspace').inputValue(), '');
			assert.equal(
				await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
				'false'
			);
			await dismissDialog(page, settingsDialog(page), 'x');
		}
	);
}

async function loginMachineChangeCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'login-machine-change',
		'machine-save-login-machine-change',
		async ({ page }, fixtures) => {
			await openMachine(page, 'Synthetic computer A');
			await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
			await waitUntil(fixtures.hasHeldMachineAction, 'Synthetic registry update must be pending');
			await dismissDialog(page, machineDialog(page), 'x');
			await openLogin(page);
			await startLogin(page);
			await loginDialog(page).getByText('Synthetic pending sign-in; no CLI was started.').waitFor();
			fixtures.externalMachineSwitch();
			await fixtures.releaseMachineAction();
			await loginDialog(page).waitFor({ state: 'hidden' });
			await providerCard(page, 'Claude')
				.getByText(/via Claude Code on Synthetic computer B/)
				.waitFor();
			await waitUntil(
				() => fixtures.count('DELETE') === 1,
				'Old login cleanup is issued for its original machine'
			);
			const cancelled = fixtures.requests.find(
				(request) => request.method === 'DELETE' && request.path.endsWith('/claude/login')
			);
			assert.equal(cancelled.expectedMachineId, 'runner-a');
			assert.equal(
				cancelled.guardRejected,
				true,
				'Old-host cancellation conflict is benign after transition killed its login'
			);
			const polls = fixtures.count('GET');
			await page.waitForTimeout(2200);
			assert.equal(fixtures.count('GET'), polls, 'Host change stops old login polling');
			assert.equal(fixtures.count('POST'), 1, 'New host never starts an automatic login');
			assert.equal(
				await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
				'false'
			);
			assert.equal(
				await page
					.getByText('Synthetic selected machine changed; refresh required.', { exact: true })
					.count(),
				0
			);
		}
	);
}

async function settingsRejectionCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'settings-rejection',
		'settings-rejection',
		async ({ page }, fixtures) => {
			await openProviderSettings(page);
			const dialog = settingsDialog(page);
			await dialog.locator('#subscription-workspace').fill('/fixture/retry-draft');
			await dialog.getByRole('button', { name: 'Save', exact: true }).click();
			await page.getByText('Synthetic recoverable settings failure', { exact: true }).waitFor();
			assert.equal(await dialog.isVisible(), true, 'Rejected save leaves the current draft open');
			await waitUntil(
				() => dialog.getByRole('button', { name: 'Save', exact: true }).isEnabled(),
				'Rejected save always releases busy state'
			);
			assert.equal(
				await dialog.locator('#subscription-workspace').inputValue(),
				'/fixture/retry-draft'
			);
			await dialog.getByRole('button', { name: 'Save', exact: true }).click();
			await dialog.waitFor({ state: 'hidden' });
			assert.equal(fixtures.count('POST', configPath), 2);
		}
	);
}

async function permissionDisclosureCase(browser, profile) {
	await runCase(browser, profile, 'permission-disclosure', 'normal', async ({ page }, fixtures) => {
		await openProviderSettings(page, 'ChatGPT');
		const dialog = settingsDialog(page, 'ChatGPT');
		assert.equal(await dialog.locator('input[value="chat"]').isChecked(), true);
		await dialog
			.getByText(
				"Can run read-only commands. Codex's read-only sandbox does not limit reads to the working folder, so it can read any file your account can. No edits.",
				{ exact: true }
			)
			.waitFor();
		await dialog
			.getByText(
				'CLI permissions apply on the selected computer. Model sign-in does not create project or file API grants.',
				{ exact: true }
			)
			.waitFor();
		await dialog
			.getByText(
				'The working folder is CLI context, not a project or filesystem grant. Chat only uses an empty folder.',
				{ exact: true }
			)
			.waitFor();
		await dialog.locator('input[value="read"]').check();
		await dialog.locator('#subscription-workspace').fill('/fixture/unsaved-context');
		await dismissDialog(page, dialog, 'x');
		await openProviderSettings(page, 'ChatGPT');
		assert.equal(
			await settingsDialog(page, 'ChatGPT').locator('input[value="chat"]').isChecked(),
			true
		);
		assert.equal(
			await settingsDialog(page, 'ChatGPT').locator('#subscription-workspace').inputValue(),
			''
		);
		assert.equal(
			fixtures.requests.filter((request) => request.method !== 'GET').length,
			0,
			'Permission drafts and model identity grant no file or provider action'
		);
		await dismissDialog(page, settingsDialog(page, 'ChatGPT'), 'x');
	});
}

async function revisionReplacementCase(browser, profile, action) {
	const name = action + '-revision-guard';
	const mode = action === 'settings' ? 'settings-delayed-revision' : 'logout-revision';
	await runCase(browser, profile, name, mode, async ({ page }, fixtures) => {
		if (action === 'settings') {
			await openProviderSettings(page);
			await settingsDialog(page)
				.locator('#subscription-workspace')
				.fill('/fixture/obsolete-instance');
			await settingsDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
			await waitUntil(fixtures.hasHeldConfig, 'Old instance config must be pending');
			await dismissDialog(page, settingsDialog(page), 'x');
		} else {
			await setSettings(page, 'admin:connections');
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign out', exact: true })
				.click();
			await waitUntil(fixtures.hasHeldLogout, 'Old instance logout must be pending');
		}
		fixtures.externalRevisionReplacement();
		await openMachine(page, 'Synthetic computer A');
		await machineDialog(page).locator('#machine-url').fill('https://replacement-a.example.invalid');
		await machineDialog(page).locator('#machine-name').fill('Synthetic replacement computer A');
		await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await machineDialog(page).waitFor({ state: 'hidden' });
		await providerCard(page, 'Claude')
			.getByText(/via Claude Code on Synthetic replacement computer A/)
			.waitFor();
		if (action === 'settings') await fixtures.releaseConfig();
		else await fixtures.releaseLogout();
		const request = fixtures.requests.find(
			(request) => request.path === (action === 'settings' ? configPath : statusPath + '/logout')
		);
		assert.equal(request.expectedMachineId, 'runner-a');
		assert.equal(request.expectedMachineRevision, 'fixture-revision-a-1');
		assert.equal(
			request.guardRejected,
			true,
			'Same id with a replacement revision rejects old action before any account/config effect'
		);
		await page.waitForTimeout(150);
		assert.equal(
			await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
			'false'
		);
		assert.equal(
			await page
				.getByText('Synthetic selected machine changed; refresh required.', { exact: true })
				.count(),
			0
		);
		assert.equal(await page.getByText('Signed out of Claude', { exact: true }).count(), 0);
		await openProviderSettings(page);
		assert.equal(
			await settingsDialog(page).locator('#subscription-machine').inputValue(),
			'runner-a'
		);
		assert.equal(await settingsDialog(page).locator('#subscription-workspace').inputValue(), '');
		assert.equal(await settingsDialog(page).locator('input[value="chat"]').isChecked(), true);
		await dismissDialog(page, settingsDialog(page), 'x');
	});
}

async function loginRevisionCase(browser, profile, metadataOnly = false, equivalentTarget = false) {
	let name = 'login-revision-guard';
	if (metadataOnly) name = 'metadata-keeps-login-context';
	if (equivalentTarget) name = 'metadata-equivalent-target-keeps-login-context';
	const mode = metadataOnly ? 'machine-save-metadata-revision' : 'machine-save-login-revision';
	await runCase(browser, profile, name, mode, async ({ page }, fixtures) => {
		await openMachine(page, 'Synthetic computer A');
		await machineDialog(page).locator('#machine-name').fill('Synthetic renamed computer A');
		await machineDialog(page)
			.locator('#machine-browser-url')
			.fill('https://browser-a.example.invalid');
		if (!metadataOnly)
			await machineDialog(page)
				.locator('#machine-url')
				.fill('https://replacement-a.example.invalid');
		if (equivalentTarget) {
			await machineDialog(page).locator('#machine-url').fill('https://host.docker.internal:8765/');
			await machineDialog(page).locator('#machine-key').fill('synthetic-saved-key-a');
		}
		await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await waitUntil(fixtures.hasHeldMachineAction, 'Registry mutation must be pending');
		await dismissDialog(page, machineDialog(page), 'x');
		await openLogin(page);
		await startLogin(page);
		await loginDialog(page).getByText('Synthetic pending sign-in; no CLI was started.').waitFor();
		await fixtures.releaseMachineAction();
		await providerCard(page, 'Claude')
			.getByText(/via Claude Code on Synthetic renamed computer A/)
			.waitFor();
		if (metadataOnly) {
			assert.equal(
				await loginDialog(page).isVisible(),
				true,
				'Name/browser-only change preserves the same login owner'
			);
			assert.equal(fixtures.count('DELETE'), 0);
			const pollsBeforeMetadataReload = fixtures.count('GET');
			await waitUntil(
				() => fixtures.count('GET') > pollsBeforeMetadataReload,
				'Polling continues for unchanged revision'
			);
			await dismiss(page, 'x');
			await waitUntil(
				() => fixtures.count('DELETE') === 1,
				'Explicit dismissal still cancels the unchanged owner'
			);
			const cancelled = fixtures.requests.find(
				(request) => request.method === 'DELETE' && request.path.endsWith('/claude/login')
			);
			assert.equal(cancelled.expectedMachineId, 'runner-a');
			assert.equal(cancelled.expectedMachineRevision, 'fixture-revision-a-1');
			assert.notEqual(
				cancelled.guardRejected,
				true,
				'Unchanged revision accepts its owner cleanup'
			);
			await openProviderSettings(page);
			assert.equal(
				await settingsDialog(page).locator('input[value="full"]').isChecked(),
				true,
				'Metadata alone does not reset existing permissions'
			);
			assert.equal(
				await settingsDialog(page).locator('#subscription-workspace').inputValue(),
				'/fixture/a'
			);
			assert.equal(
				await settingsDialog(page).locator('#subscription-cli-path').inputValue(),
				'/fixture/claude-a'
			);
			await dismissDialog(page, settingsDialog(page), 'x');
		} else {
			await loginDialog(page).waitFor({ state: 'hidden' });
			await waitUntil(
				() => fixtures.count('DELETE') === 1,
				'Replacement closes and cleans up old instance login'
			);
			const cancelled = fixtures.requests.find(
				(request) => request.method === 'DELETE' && request.path.endsWith('/claude/login')
			);
			assert.equal(cancelled.expectedMachineRevision, 'fixture-revision-a-1');
			assert.equal(cancelled.guardRejected, true);
			const polls = fixtures.count('GET');
			await page.waitForTimeout(2200);
			assert.equal(fixtures.count('GET'), polls);
			assert.equal(
				await providerCard(page, 'Claude').getByRole('switch').getAttribute('aria-checked'),
				'false'
			);
		}
		assert.equal(
			fixtures.count('POST'),
			1,
			'Replacement or metadata edit never starts another login'
		);
		assert.equal(
			await page
				.getByText('Synthetic selected machine changed; refresh required.', { exact: true })
				.count(),
			0
		);
	});
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
		await terminalPollCase(browser, profile, 'error');
		await terminalPollCase(browser, profile, 'cancelled');
		await terminalPollCase(browser, profile, 'idle');
		await terminalPollCase(browser, profile, 'code');
		await successCase(browser, profile);
		await codeLoginCase(browser, profile);
		await codeLoginCase(browser, profile, true);
		await checkingTransitionCase(browser, profile);
		await lateCheckCase(browser, profile, false);
		await lateCheckCase(browser, profile, true);
		await pendingCheckUnmountCase(browser, profile);
		await apiBillingCase(browser, profile);
		await usageNavigationCase(browser, profile);
		await permissionCase(browser, profile);
		await machineCrudCase(browser, profile);
		await delayedMachineCase(browser, profile, 'verify', 'x');
		await delayedMachineCase(browser, profile, 'verify', 'unmount', true);
		await delayedMachineCase(browser, profile, 'verify', 'escape');
		await delayedMachineCase(browser, profile, 'verify', 'backdrop');
		await delayedMachineCase(browser, profile, 'save', 'x');
		await delayedMachineCase(browser, profile, 'save', 'unmount');
		await delayedMachineCase(browser, profile, 'remove', 'unmount');
		await staleStatusSettingsCase(browser, profile, 'switch');
		await staleStatusSettingsCase(browser, profile, 'switch-error');
		await staleStatusSettingsCase(browser, profile, 'workspace');
		await staleStatusRegistryCase(browser, profile);
		await delayedSettingsCase(browser, profile, 'x');
		await delayedSettingsCase(browser, profile, 'unmount');
		await delayedSettingsCase(browser, profile, 'x', true);
		await delayedEnableCase(browser, profile);
		await delayedEnableCase(browser, profile, 'error');
		await delayedEnableCase(browser, profile, 'remount');
		await removedMachineCase(browser, profile);
		await removedMachineCase(browser, profile, 'local');
		await delayedLogoutCase(browser, profile, 'switch');
		await delayedLogoutCase(browser, profile, 'unmount');
		await delayedLogoutCase(browser, profile, 'unmount-same');
		await staleConfigGuardCase(browser, profile);
		await loginMachineChangeCase(browser, profile);
		await settingsRejectionCase(browser, profile);
		await permissionDisclosureCase(browser, profile);
		await revisionReplacementCase(browser, profile, 'settings');
		await revisionReplacementCase(browser, profile, 'logout');
		await loginRevisionCase(browser, profile);
		await loginRevisionCase(browser, profile, true);
		await loginRevisionCase(browser, profile, true, true);
	}
} finally {
	await browser.close();
	writeFileSync(resolve(outputDirectory, 'results.json'), JSON.stringify(results, null, 2));
}
const failures = results.filter((result) => !result.passed);
assert.ok(results.length > 0, 'The selected case filter must run at least one scenario');
console.log(
	JSON.stringify(
		{ passed: results.length - failures.length, failed: failures.length, results },
		null,
		2
	)
);
if (failures.length) process.exitCode = 1;
