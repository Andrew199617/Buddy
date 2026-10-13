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
		default_workspace: 'Synthetic workspace; no directory access',
		// Signs in with the computer's own CLI sign-in; no token or key is stored.
		credential: { mode: 'machine', stored: {} }
	};
}

// What administrators set in Admin Settings → AI plans.
function policyFixture() {
	return {
		user_machines: 'all',
		machine_group_ids: [],
		plan_sign_in: true,
		api_keys: true,
		server_machine: true,
		desktop_download_url: '',
		owner_id: 'buddy-design-user'
	};
}

// The part of the policy every user's Settings → Machines reads.
function publicPolicyFixture() {
	const { plan_sign_in, api_keys, desktop_download_url } = policyFixture();
	return { plan_sign_in, api_keys, desktop_download_url };
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
	let heldCode = null;
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
			has_key: true,
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
			browser_url: null,
			has_key: true
		}
	];
	if (mode === 'keyless-machine') {
		// A config import keeps the machine but leaves out its Runner key.
		machineKeys.delete('runner-a');
		machines.find((machine) => machine.id === 'runner-a').has_key = false;
	}
	let checkingResolved = false;
	let checkCount = 0;
	let pollCount = 0;
	let signedIn = mode.startsWith('logout-');
	let loginState = 'idle';
	let startCount = 0;
	const waiting = {
		state: 'waiting',
		method: 'browser',
		url:
			mode === 'unsafe-link'
				? 'javascript:document.title="synthetic-unsafe-link"'
				: 'https://example.invalid/synthetic-sign-in',
		message: 'Synthetic pending sign-in; no CLI was started.',
		needs_code:
			mode === 'code-login' ||
			mode === 'remote-code-login' ||
			mode === 'code-held' ||
			mode === 'poll-terminal-code'
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
		const codexSignedIn = id === 'codex' && mode !== 'codex-signed-out';
		const provider = providerFixture(id, codexSignedIn || signedIn || mode === 'api-billing');
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
			has_key: Boolean(nextKey),
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

	// --- Chat folders' project folders and the chat's Project tab -------------
	const projectModes = new Set([
		'project-tab',
		'project-agents',
		'project-agents-server',
		'folder-chip',
		'folder-edit',
		'folder-allow',
		'folder-allow-stale',
		'folder-allow-beside',
		'folder-allow-homeless',
		'folder-no-plans'
	]);
	// Modes where the folder settings ask for something before the choices show.
	const askingModes = new Set([
		'folder-allow',
		'folder-allow-stale',
		'folder-allow-beside',
		'folder-allow-homeless',
		'folder-no-plans'
	]);
	// Modes where every plan that is on is Chat only.
	const chatOnlyModes = new Set(['folder-allow', 'folder-allow-stale']);
	const projectRelayBase = '/api/v1/runners/machines/pc-1/workspace';
	const workPc = {
		machine_id: 'pc-1',
		name: 'Work PC',
		kind: 'app',
		online: true,
		platform: 'win32',
		can_browse: true,
		recent_paths: ['C:\\code\\site']
	};
	let projectChoice = {
		folder_id: 'website',
		folder_name: 'Website',
		mode: 'folder',
		paths: { 'pc-1': { path: 'C:\\code\\site', problem: null } }
	};
	if (askingModes.has(mode)) {
		projectChoice = { folder_id: 'website', folder_name: 'Website', mode: 'auto', paths: {} };
	}
	if (mode === 'project-agents-server') {
		projectChoice = {
			folder_id: 'website',
			folder_name: 'Website',
			mode: 'folder',
			paths: { local: { path: 'C:\\baseball\\src', problem: null } }
		};
	}
	// The person's plans as the folder settings see them, changed by plan config saves.
	const folderPlans = [
		{
			id: 'claude',
			name: 'Claude',
			enable: true,
			access: chatOnlyModes.has(mode) ? 'chat' : 'full',
			machine: { id: 'pc-1', name: 'Work PC', revision: 'rev-pc-1' }
		},
		{
			id: 'codex',
			name: 'ChatGPT',
			enable: false,
			access: 'chat',
			machine: { id: 'local', name: 'This server', revision: 'local' }
		}
	];
	if (mode === 'folder-no-plans') {
		folderPlans[0].enable = false;
	}
	if (mode === 'project-agents-server') {
		folderPlans[0].machine = { id: 'local', name: 'This server', revision: 'local' };
	}
	if (mode === 'folder-allow-homeless') {
		// Claude has Full access on a This server the policy no longer allows;
		// ChatGPT is on Work PC but only chats.
		folderPlans[0].machine = null;
		folderPlans[1].enable = true;
		folderPlans[1].machine = { id: 'pc-1', name: 'Work PC', revision: 'rev-pc-1' };
	}
	if (mode === 'folder-allow-beside') {
		// Claude already opens folders on Work PC; ChatGPT is on there but only chats.
		folderPlans[1].enable = true;
		folderPlans[1].machine = { id: 'pc-1', name: 'Work PC', revision: 'rev-pc-1' };
	}
	// In the stale case the page first sees Work PC from before it was set up again.
	let showsOldRevision = mode === 'folder-allow-stale';
	const planConfigs = [];
	// The next read of the folder's project can be held open, or fail once.
	let holdProjectRead = false;
	let heldProjectRead = null;
	let failProjectRead = false;
	// The next plan config save can be held open, refused, or saved and then turned off.
	let holdPlanConfig = false;
	let heldPlanConfig = null;
	let refusePlanConfig = '';
	// The next save goes through but its answer is lost on the way back.
	let losePlanConfigAnswer = '';
	// Set when an administrator stops this person using computers.
	let machinesRevoked = false;
	let turnOffAfterPlanConfig = '';
	const projectGrant = {
		id: 'grant-code',
		name: 'code',
		path: 'C:\\code',
		capabilities: { read: true, write: true, execute: true },
		available: true,
		unavailableReason: null
	};
	const projectListings = {
		site: [
			{ name: 'src', path: 'site/src', type: 'directory' },
			{ name: 'README.md', path: 'site/README.md', type: 'file' }
		],
		'site/src': [{ name: 'app.ts', path: 'site/src/app.ts', type: 'file' }]
	};
	// The project's custom agents; .github/chatmodes is missing on purpose.
	const agentListings = {
		'site/.github/agents': [
			{ name: 'reviewer.agent.md', path: 'site/.github/agents/reviewer.agent.md', type: 'file' },
			{ name: 'helper.agent.md', path: 'site/.github/agents/helper.agent.md', type: 'file' },
			{ name: 'notes.txt', path: 'site/.github/agents/notes.txt', type: 'file' }
		],
		'site/.claude/agents': [
			{ name: 'tester.md', path: 'site/.claude/agents/tester.md', type: 'file' }
		]
	};
	const agentFiles = {
		'site/.github/agents/reviewer.agent.md':
			"---\r\nname: Reviewer\r\ndescription: 'Reviews changes for bugs'\r\ntools: [search]\r\n---\r\nReview the diff.\r\nList risks first.\r\n",
		'site/.github/agents/helper.agent.md': '---\nuser-invocable: false\n---\nOnly a subagent.',
		'site/.claude/agents/tester.md':
			'---\nname: Tester\ndescription: Writes tests\n---\nWrite tests.'
	};
	let readmeContent = '# Site\r\nHello\r\n';
	let readmeVersion = 'readme-1';
	const projectSaves = [];
	const projectRelayClients = new Set();
	let projectTerminal = null;

	function folderProject() {
		const plans = folderPlans.map((plan) => {
			if (plan.machine?.id === 'pc-1' && showsOldRevision) {
				return { ...plan, machine: { ...plan.machine, revision: 'rev-pc-1-old' } };
			}
			return plan;
		});
		const providers = folderPlans
			.filter((plan) => plan.enable && plan.access !== 'chat' && plan.machine?.id === 'pc-1')
			.map((plan) => ({ id: plan.id, name: plan.name, access: plan.access }));
		// Like the server: computers running a plan with files, and those the choice names.
		const computers = [];
		if (providers.length > 0 || projectChoice.paths['pc-1']) {
			computers.push({ ...workPc, providers });
		}
		if (mode === 'project-agents-server') {
			const serverProviders = folderPlans
				.filter((plan) => plan.enable && plan.access !== 'chat' && plan.machine?.id === 'local')
				.map((plan) => ({ id: plan.id, name: plan.name, access: plan.access }));
			computers.push({
				machine_id: 'local',
				name: 'This server',
				kind: 'server',
				online: true,
				platform: 'win32',
				can_browse: false,
				providers: serverProviders,
				recent_paths: []
			});
		}
		return {
			folder_id: 'website',
			folder_name: 'Website',
			own: projectChoice,
			inherited: null,
			shared_instructions: false,
			has_plans: folderPlans.some((plan) => plan.enable),
			plans,
			computers
		};
	}

	/** A plan's access saved from the folder settings, checked like the server checks it. */
	function savePlanConfig(providerId, body) {
		planConfigs.push(body);
		const plan = folderPlans.find((candidate) => candidate.id === providerId);
		assert.ok(plan, 'Only known plans change');
		if (!plan.machine) {
			return {
				status: 409,
				json: { detail: 'Verify the selected machine again, then refresh before trying again.' }
			};
		}
		assert.deepEqual(Object.keys(body).sort(), [
			'access',
			'expected_machine_id',
			'expected_machine_revision'
		]);
		assert.equal(body.expected_machine_id, plan.machine.id, 'The plan stays on its computer');
		if (refusePlanConfig) {
			const detail = refusePlanConfig;
			refusePlanConfig = '';
			return { status: 400, json: { detail } };
		}
		if (body.expected_machine_revision !== plan.machine.revision) {
			showsOldRevision = false;
			return {
				status: 409,
				json: { detail: 'The selected machine changed. Refresh before trying again.' }
			};
		}
		plan.access = body.access;
		if (losePlanConfigAnswer) {
			const detail = losePlanConfigAnswer;
			losePlanConfigAnswer = '';
			return { status: 502, json: { detail } };
		}
		if (turnOffAfterPlanConfig) {
			// Like the server when a running reply cannot be confirmed stopped.
			const detail = turnOffAfterPlanConfig;
			turnOffAfterPlanConfig = '';
			plan.enable = false;
			return { status: 400, json: { detail } };
		}
		return { id: plan.id, name: plan.name, settings: { enable: plan.enable, access: plan.access } };
	}

	function terminalFixture(status, output, exitCode = null) {
		projectTerminal = { id: 'term-1', workspaceId: 'work-1', status, output, exitCode };
		return { terminal: projectTerminal };
	}

	async function routeProjectRelay(route, request, url, relayPath) {
		const method = request.method();
		const headers = request.headers();
		assert.equal(
			headers.authorization,
			'Bearer buddy-design-fake-token',
			'The relay uses the Buddy sign-in'
		);
		assert.match(headers['x-buddy-relay-client'] || '', /^[A-Za-z0-9_-]{1,64}$/);
		projectRelayClients.add(headers['x-buddy-relay-client']);
		const body = request.postDataJSON();
		if (method === 'GET' && relayPath === '/v1/grants') {
			return { grants: [projectGrant] };
		}
		if (method === 'GET' && relayPath === '/v1/directories' && mode === 'project-agents') {
			assert.equal(url.searchParams.get('grantId'), 'grant-code');
			const listed = url.searchParams.get('path');
			assert.ok(
				['site/.github/agents', 'site/.claude/agents', 'site/.github/chatmodes'].includes(listed),
				'Only the agent folders inside the project folder are listed'
			);
			if (!agentListings[listed]) {
				return { status: 404, json: { error: 'The requested file or directory does not exist.' } };
			}
			return { path: listed, entries: agentListings[listed] };
		}
		if (method === 'GET' && relayPath === '/v1/files' && mode === 'project-agents') {
			const read = url.searchParams.get('path');
			assert.ok(agentFiles[read] !== undefined, 'Only agent files are read: ' + read);
			return { path: read, content: agentFiles[read], version: 'agent-1' };
		}
		if (method === 'GET' && relayPath === '/v1/directories') {
			assert.equal(url.searchParams.get('grantId'), 'grant-code');
			const listing = projectListings[url.searchParams.get('path')];
			assert.ok(listing, 'Browsing stays inside the project folder');
			return { path: url.searchParams.get('path'), entries: listing };
		}
		if (method === 'GET' && relayPath === '/v1/files') {
			assert.equal(url.searchParams.get('path'), 'site/README.md');
			return { path: 'site/README.md', content: readmeContent, version: readmeVersion };
		}
		if (method === 'PUT' && relayPath === '/v1/files') {
			projectSaves.push(body);
			if (projectSaves.length === 4) {
				return { status: 503, json: { detail: 'Work PC is offline.' } };
			}
			if (projectSaves.length === 2 && !body.overwrite) {
				return {
					status: 409,
					json: { error: 'This file changed on the host after it was opened.' }
				};
			}
			readmeContent = body.content;
			readmeVersion = 'readme-' + (projectSaves.length + 1);
			return { path: body.path, content: body.content, version: readmeVersion };
		}
		if (method === 'POST' && relayPath === '/v1/workspaces') {
			assert.deepEqual(body, { grantId: 'grant-code', path: 'site' });
			return {
				workspace: {
					id: 'work-1',
					name: 'site',
					hostId: 'pc-host',
					grantId: 'grant-code',
					path: 'C:\\code\\site'
				}
			};
		}
		if (method === 'POST' && relayPath === '/v1/terminals') {
			assert.deepEqual(body, { workspaceId: 'work-1' });
			return terminalFixture('ready', '');
		}
		if (method === 'POST' && relayPath === '/v1/terminals/term-1/commands') {
			assert.deepEqual(body, { executable: 'npm', args: ['run', 'test'] });
			return terminalFixture('running', '> test\n');
		}
		if (method === 'GET' && relayPath === '/v1/terminals/term-1') {
			return terminalFixture('exited', '> test\nok\n', 0);
		}
		if (method === 'DELETE' && relayPath === '/v1/terminals/term-1') {
			return terminalFixture('closed', projectTerminal?.output ?? '', 0);
		}
		if (method === 'DELETE' && relayPath === '/v1/session') {
			return { revoked: true };
		}
		throw new Error('Unexpected project relay request: ' + method + ' ' + relayPath);
	}

	/** Answers the folder and Project tab requests; returns false for anything else. */
	async function routeProjectFolders(route, request, url, path) {
		const method = request.method();
		let reply = null;
		if (path === '/api/v1/folders/website' && method === 'GET') {
			reply = {
				id: 'website',
				name: 'Website',
				user_id: 'buddy-design-user',
				parent_id: null,
				data: {},
				meta: {},
				items: { chats: [], files: [] },
				write_access: true,
				created_at: 1,
				updated_at: 1
			};
		} else if (path === '/api/v1/subscriptions/machines/local/agents' && method === 'GET') {
			assert.equal(
				url.searchParams.get('path'),
				'C:\\baseball\\src',
				"The chat folder's project folder is read"
			);
			requests.push({ method, path });
			reply = {
				files: [
					{
						path: '.github/agents/QuickPlan.agent.md',
						content: '---\ndescription: Plans quickly\n---\nWrite a short plan first.'
					},
					{ path: '.github/agents/ResearchFast.agent.md', content: 'Research fast.' }
				],
				left_out: 0
			};
		} else if (path === '/api/v1/subscriptions/folders/website/project') {
			requests.push({ method, path, body: request.postDataJSON() });
			if (machinesRevoked) {
				await route.fulfill({ status: 403, json: { detail: 'Synthetic machines not allowed' } });
				return true;
			}
			if (method === 'GET' && failProjectRead) {
				failProjectRead = false;
				await route.fulfill({ status: 503, json: { detail: 'Synthetic project read failure' } });
				return true;
			}
			if (method === 'GET' && holdProjectRead) {
				holdProjectRead = false;
				heldProjectRead = route;
				return true;
			}
			if (method === 'PUT') {
				const body = request.postDataJSON();
				const paths = {};
				for (const [machineId, path] of Object.entries(body.paths || {})) {
					paths[machineId] = { path, problem: null };
				}
				projectChoice = { folder_id: 'website', folder_name: 'Website', mode: body.mode, paths };
			}
			reply = folderProject();
		} else if (
			/^\/api\/v1\/subscriptions\/(claude|codex)\/config$/.test(path) &&
			method === 'POST'
		) {
			const body = request.postDataJSON();
			requests.push({ method, path, body });
			if (machinesRevoked) {
				await route.fulfill({ status: 403, json: { detail: 'Synthetic machines not allowed' } });
				return true;
			}
			if (holdPlanConfig) {
				holdPlanConfig = false;
				heldPlanConfig = { route, providerId: path.split('/')[4], body };
				return true;
			}
			reply = savePlanConfig(path.split('/')[4], body);
		} else if (path === '/api/v1/subscriptions/machines/pc-1/folders/check' && method === 'POST') {
			const body = request.postDataJSON();
			requests.push({ method, path, body });
			reply = { path: body.path };
		} else if (path.startsWith(projectRelayBase + '/')) {
			requests.push({ method, path });
			reply = await routeProjectRelay(route, request, url, path.slice(projectRelayBase.length));
		} else {
			return false;
		}
		if (reply && typeof reply.status === 'number' && reply.json) {
			await route.fulfill(reply);
		} else {
			await route.fulfill({ status: 200, json: reply });
		}
		return true;
	}

	let sessionOptions = {};
	if (mode === 'project-tab' || mode === 'project-agents' || mode === 'project-agents-server')
		sessionOptions = { folderId: 'website' };
	if (mode === 'folder-chip' || mode === 'folder-edit' || askingModes.has(mode))
		sessionOptions = { path: '/folders/website' };

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
		if (projectModes.has(mode) && (await routeProjectFolders(route, request, url, path))) {
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
		// The non-administrator case only checks that nothing asks; a member's own
		// per-user view is not modelled here.
		if (role !== 'admin') {
			await route.fulfill({ status: 403, json: { detail: 'Synthetic administrator guard.' } });
			return;
		}
		if (path === '/api/v1/subscriptions/admin/policy' && request.method() === 'GET') {
			await route.fulfill({ status: 200, json: policyFixture() });
			return;
		}
		if (path === '/api/v1/subscriptions/admin/machines' && request.method() === 'GET') {
			// No computer is connected through the Buddy app in these cases.
			await route.fulfill({ status: 200, json: { machines: [] } });
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
				json: {
					allowed: true,
					providers: [describeClaude(), describeProvider('codex')],
					machines,
					policy: publicPolicyFixture()
				}
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
			if (mode === 'code-held') {
				heldCode = route;
				return;
			}
			signedIn = true;
			loginState = 'success';
			await route.fulfill({ status: 200, json: { state: 'success' } });
			return;
		}
		if (path === '/api/v1/subscriptions/claude') {
			checkCount += 1;
			if (mode === 'checking-error' && !checkingResolved) {
				await route.fulfill({ status: 500, json: { detail: 'Synthetic status check failure' } });
				return;
			}
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
		sessionOptions,
		projectSaves,
		projectRelayClients,
		planConfigs,
		holdNextProjectRead() {
			holdProjectRead = true;
		},
		hasHeldProjectRead: () => Boolean(heldProjectRead),
		async releaseProjectRead() {
			const held = heldProjectRead;
			heldProjectRead = null;
			await held.fulfill({ status: 200, json: folderProject() });
		},
		failNextProjectRead() {
			failProjectRead = true;
		},
		holdNextPlanConfig() {
			holdPlanConfig = true;
		},
		hasHeldPlanConfig: () => Boolean(heldPlanConfig),
		async releasePlanConfig() {
			const { route, providerId, body } = heldPlanConfig;
			heldPlanConfig = null;
			const reply = savePlanConfig(providerId, body);
			if (typeof reply.status === 'number') await route.fulfill(reply);
			else await route.fulfill({ status: 200, json: reply });
		},
		revokeMachines() {
			machinesRevoked = true;
		},
		loseNextPlanConfigAnswer(detail) {
			losePlanConfigAnswer = detail;
		},
		refuseNextPlanConfig(detail) {
			refusePlanConfig = detail;
		},
		turnOffAfterNextPlanConfig(detail) {
			turnOffAfterPlanConfig = detail;
		},
		// Work PC removed elsewhere: like the server, its plans turn off and only chat.
		removeWorkPc() {
			for (const plan of folderPlans) {
				if (plan.machine?.id !== 'pc-1') continue;
				plan.enable = false;
				plan.access = 'chat';
				plan.machine = null;
			}
		},
		// A plan turned on elsewhere, such as in Settings → Machines.
		turnOnPlan(id, access) {
			const plan = folderPlans.find((candidate) => candidate.id === id);
			plan.enable = true;
			plan.access = access;
		},
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
		hasHeldCode: () => Boolean(heldCode),
		hasHeldCheck: () => Boolean(heldCheck),
		hasHeldMachineAction: () => Boolean(heldMachineAction),
		hasHeldConfig: () => Boolean(heldConfig),
		hasHeldLogout: () => Boolean(heldLogout),
		providerSettings: (id = 'claude') => ({ ...describeProvider(id).settings }),
		cancelLoginOnServer() {
			loginState = 'idle';
		},
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
		async releaseTerminalPoll() {
			assert.ok(heldPoll, 'A synthetic poll must be pending');
			const route = heldPoll;
			heldPoll = null;
			loginState = mode.slice('poll-terminal-'.length);
			await route.fulfill({
				status: 200,
				json: { state: loginState, message: 'Synthetic terminal login result' }
			});
		},
		async releaseCode() {
			assert.ok(heldCode, 'A synthetic code submission must be pending');
			const route = heldCode;
			heldCode = null;
			signedIn = true;
			loginState = 'success';
			await route.fulfill({ status: 200, json: { state: 'success' } });
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

// Each person's Claude and ChatGPT cards are in Settings → Machines; the older
// address-registered machines are in the administrators' AI plans tab.
const PROVIDERS_TAB = 'machines';
const ADMIN_PLANS_TAB = 'admin:plans';

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
	await setSettings(page, PROVIDERS_TAB);
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
	await setSettings(page, PROVIDERS_TAB);
	await page
		.getByRole('button', { name: 'Configure ' + name + ' subscription', exact: true })
		.click();
	await settingsDialog(page, name).waitFor();
}

async function openMachine(page, name = null) {
	await setSettings(page, ADMIN_PLANS_TAB);
	await page
		.getByRole('button', { name: name ? 'Configure ' + name : 'Add machine', exact: true })
		.click();
	await machineDialog(page).waitFor();
}

// Remove asks before deleting the machine; this returns that confirmation.
async function askToRemoveMachine(page, machineName) {
	await machineDialog(page).getByRole('button', { name: 'Remove', exact: true }).click();
	const confirmation = page.getByRole('dialog', {
		name: 'Remove ' + machineName + '?',
		exact: true
	});
	await confirmation.waitFor();
	return confirmation;
}

async function confirmMachineRemoval(confirmation) {
	await confirmation.getByRole('button', { name: 'Remove', exact: true }).click();
	await confirmation.waitFor({ state: 'hidden' });
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
	let result = { name: profile.name + '-' + name, passed: false };
	try {
		session = await createSession(browser, {
			mobile: profile.mobile,
			theme: profile.theme,
			reducedMotion: true,
			role,
			beforeNavigation: fixtures.beforeNavigation,
			...fixtures.sessionOptions
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
		await waitUntil(fixtures.hasHeldPoll, 'A slow login poll must be held');
		if (terminal === 'code') {
			await loginDialog(page).locator('#subscription-login-code').fill('synthetic-approval-code');
			await loginDialog(page).getByRole('button', { name: 'Finish', exact: true }).click();
		} else {
			await page.waitForTimeout(4500);
			assert.equal(fixtures.count('GET'), 1, 'A slow poll is never overlapped by the next one');
			await fixtures.releaseTerminalPoll();
		}
		await loginDialog(page)
			.getByRole('button', { name: 'Get sign-in link', exact: true })
			.waitFor();
		// The server's reason stays visible whichever way the sign-in ended.
		await loginDialog(page).getByText('Synthetic terminal login result', { exact: true }).waitFor();
		const polls = fixtures.count('GET');
		const modelReads = fixtures.count('GET', '/api/models');
		assert.equal(polls, 1);
		if (terminal === 'code') await fixtures.releaseWaitingPoll();
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

async function serverCancelCase(browser, profile) {
	await runCase(browser, profile, 'server-cancel', 'normal', async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		await loginDialog(page).getByText('Open Claude sign-in').waitFor();
		fixtures.cancelLoginOnServer();
		await loginDialog(page)
			.getByText(
				'This sign-in was stopped on the server, so its link or code no longer works. Start again.',
				{ exact: true }
			)
			.waitFor();
		assert.equal(
			await loginDialog(page).getByRole('button', { name: 'Get sign-in link' }).isVisible(),
			true
		);
		const polls = fixtures.count('GET');
		await page.waitForTimeout(2200);
		assert.equal(fixtures.count('GET'), polls, 'A sign-in stopped on the server stops polling');
		await dismiss(page, 'x');
		assert.equal(fixtures.count('DELETE'), 0, 'An idle dialog has nothing to cancel');
	});
}

async function unsafeLinkCase(browser, profile) {
	await runCase(browser, profile, 'unsafe-link', 'unsafe-link', async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		const dialog = loginDialog(page);
		await dialog
			.getByText(
				'Buddy did not open this sign-in address because it is not HTTPS. Check the CLI on This server.',
				{ exact: false }
			)
			.waitFor();
		assert.equal(await dialog.getByText('Open Claude sign-in').count(), 0);
		assert.equal(await dialog.locator('a[href^="javascript:" i]').count(), 0);
		assert.match(await dialog.innerText(), /javascript:document\.title/);
		await dismiss(page, 'x');
		await waitUntil(() => fixtures.count('DELETE') === 1, 'Dismissal still cancels the login');
	});
}

async function codexSignInTextCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'codex-sign-in-text',
		'codex-signed-out',
		async ({ page }, fixtures) => {
			await setSettings(page, PROVIDERS_TAB);
			await providerCard(page, 'ChatGPT')
				.getByRole('button', { name: 'Sign in', exact: true })
				.click();
			const dialog = page
				.getByRole('dialog')
				.filter({ has: page.getByRole('heading', { name: 'Sign in to ChatGPT' }) });
			await dialog.waitFor();
			await dialog
				.getByText(
					'The sign-in page returns to the computer running Codex (This server), so open the link in a browser on that computer. From any other device, use a device code instead.',
					{ exact: true }
				)
				.waitFor();
			assert.match(await dialog.innerText(), /on the computer it runs on \(This server\)/);
			assert.equal(
				await dialog.getByRole('button', { name: 'Use a device code instead' }).isVisible(),
				true
			);
			await captureScreenshot(page, profile.name + '-codex-sign-in');
			await dismissDialog(page, dialog, 'x');
			assert.equal(fixtures.count('POST', '/api/v1/subscriptions/codex/login'), 0);
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

async function codeLoginCase(browser, profile, remote = false) {
	const name = remote ? 'remote-code-login' : 'code-login';
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await openLogin(page);
		const machineName = remote ? 'Synthetic computer A' : 'This server';
		assert.ok(
			(await loginDialog(page).innerText()).includes('runs on (' + machineName + ')'),
			'The dialog names the computer that keeps the sign-in'
		);
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

async function heldCodeCase(browser, profile) {
	await runCase(browser, profile, 'code-held', 'code-held', async ({ page }, fixtures) => {
		await openLogin(page);
		await startLogin(page);
		await loginDialog(page).locator('#subscription-login-code').fill('synthetic-approval-code');
		await loginDialog(page).getByRole('button', { name: 'Finish', exact: true }).click();
		await waitUntil(fixtures.hasHeldCode, 'The code submission must be pending');
		const polls = fixtures.count('GET');
		await page.waitForTimeout(4500);
		assert.equal(
			fixtures.count('GET'),
			polls,
			'Polling pauses while the server is still checking the code'
		);
		await fixtures.releaseCode();
		await loginDialog(page).waitFor({ state: 'hidden' });
		await providerCard(page, 'Claude')
			.getByRole('button', { name: 'Sign out', exact: true })
			.waitFor();
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
			await setSettings(page, PROVIDERS_TAB);
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
		await setSettings(page, PROVIDERS_TAB);
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
		await setSettings(page, PROVIDERS_TAB);
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

async function failedCheckCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'checking-error',
		'checking-error',
		async ({ page }, fixtures) => {
			await setSettings(page, PROVIDERS_TAB);
			const card = providerCard(page, 'Claude');
			await card.getByText('Checking Claude Code…', { exact: true }).waitFor();
			await card
				.getByText('Could not check Claude Code: Synthetic status check failure', { exact: true })
				.waitFor();
			assert.equal(fixtures.count('GET', statusPath), 1);
			await page.waitForTimeout(3300);
			assert.equal(
				fixtures.count('GET', statusPath),
				1,
				'A failed check waits for Retry instead of asking every three seconds'
			);
			assert.equal(
				await page.getByText('Synthetic status check failure', { exact: true }).count(),
				0,
				'A failed automatic check is shown in the row, not toasted'
			);
			await captureScreenshot(page, profile.name + '-subscription-check-error');
			fixtures.markCheckingReady();
			const registryReads = fixtures.count('GET', '/api/v1/subscriptions');
			const retry = card.getByRole('button', { name: 'Retry', exact: true });
			if (profile.mobile) {
				await retry.click();
			} else {
				await retry.focus();
				await page.keyboard.press('Enter');
			}
			await card.getByRole('button', { name: 'Sign in', exact: true }).waitFor();
			assert.ok(
				fixtures.count('GET', '/api/v1/subscriptions') > registryReads,
				'Retry reloads the machine registry first'
			);
			assert.equal(await card.getByText(/Could not check/).count(), 0);
			if (!profile.mobile) {
				const statusLine = card.getByText('Not signed in', { exact: true });
				assert.equal(
					await statusLine.evaluate((element) => element === document.activeElement),
					true,
					'Retry hands keyboard focus to the status line instead of dropping it to the page'
				);
				await page.keyboard.press('Tab');
				assert.equal(
					await card
						.getByRole('button', { name: 'Sign in', exact: true })
						.evaluate((element) => element === document.activeElement),
					true,
					'The next Tab continues within the provider row'
				);
			}
		}
	);
}

async function pendingCheckUnmountCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'checking-timer-unmount',
		'checking-transition',
		async ({ page }, fixtures) => {
			await setSettings(page, PROVIDERS_TAB);
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
		await setSettings(page, PROVIDERS_TAB);
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
		await setSettings(page, PROVIDERS_TAB);
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
			0,
			'The Projects page link is gone; its tools are in each chat'
		);
		assert.equal(fixtures.count('POST'), 0, 'Browsing subscription settings initiates no login');
		await page.locator('#buddy-sidebar-close').click();
		await captureScreenshot(page, profile.name + '-subscription-navigation');
		// Old links to the Projects page go home.
		await page.locator('#chat-input').fill('');
		await page.goto(origin + '/companion', { waitUntil: 'domcontentloaded' });
		await page.waitForURL((url) => url.pathname === '/');
		assert.equal(
			fixtures.externalRequests.length,
			0,
			'Subscription identity grants no computer session'
		);
	});
}

async function permissionCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'non-admin',
		'normal',
		async ({ page }, fixtures) => {
			await page.goto(origin + '/c/buddy-design-chat?settings=admin%3Aplans', {
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
			await dialog.locator('#machine-browser-url').count(),
			0,
			'Computers are opened through Buddy, so no browser address is asked for'
		);
		await dialog.locator('#machine-name').fill('Synthetic new computer');
		await dialog.locator('#machine-key').fill('synthetic-runner-key-never-used');
		await dialog.getByRole('button', { name: 'Verify', exact: true }).click();
		await dialog
			.getByText('Connected to Synthetic verified computer A (fixture)', { exact: true })
			.waitFor();
		const verified = fixtures.requests.find((request) => request.path === machinesPath + '/verify');
		assert.equal(verified.body.url, 'https://host.docker.internal:8765');
		assert.equal(verified.body.browser_url, undefined);
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
			await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).isEnabled(),
			true,
			'Opening a machine with its saved address does not ask for a key'
		);
		assert.equal(
			await machineDialog(page).locator('#machine-key').getAttribute('placeholder'),
			'Leave empty to keep the saved key'
		);
		const edited = machineDialog(page);
		await edited.locator('#machine-url').fill('https://moved-runner.example.invalid');
		assert.equal(
			await edited.getByRole('button', { name: 'Save', exact: true }).isDisabled(),
			true,
			'A new address cannot reuse the saved key'
		);
		assert.equal(
			await edited.locator('#machine-key').getAttribute('placeholder'),
			'Enter the key for the new address'
		);
		await edited.getByText(/Buddy only sends the saved key to the saved address/).waitFor();
		await edited.locator('#machine-url').fill('https://HOST.docker.internal:8765/');
		assert.equal(
			await edited.getByRole('button', { name: 'Save', exact: true }).isEnabled(),
			true,
			'The same origin keeps the saved key'
		);
		await edited.locator('#machine-url').fill('https://host.docker.internal:8765');
		await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await machineDialog(page).waitFor({ state: 'hidden' });
		const updates = fixtures.requests.filter(
			(request) => request.method === 'POST' && request.path === machinesPath
		);
		assert.equal(updates.length, 2);
		assert.equal(updates[0].body.url, 'https://host.docker.internal:8765');
		assert.equal(updates[0].body.browser_url, undefined);
		assert.equal(updates[1].body.url, 'https://host.docker.internal:8765');
		assert.equal(updates[1].body.id, 'runner-new');
		assert.equal(
			updates[1].body.browser_url,
			undefined,
			'Saving leaves any browser address saved earlier alone'
		);
		assert.equal(updates[1].body.key, undefined, 'Blank edit preserves existing server key');
		await openMachine(page, 'Synthetic new computer');
		const confirmation = await askToRemoveMachine(page, 'Synthetic new computer');
		await confirmation.getByText(/forget this machine and its Runner key/).waitFor();
		assert.equal(
			await confirmation.getByText(/These subscriptions run on it/).count(),
			0,
			'No subscription is named when none runs on the machine'
		);
		await page.keyboard.press('Escape');
		await confirmation.waitFor({ state: 'hidden' });
		assert.equal(
			await machineDialog(page).isVisible(),
			true,
			'Escape cancels only the confirmation'
		);
		assert.equal(
			fixtures.count('DELETE', machinesPath + '/runner-new'),
			0,
			'Remove deletes nothing until it is confirmed'
		);
		await confirmMachineRemoval(await askToRemoveMachine(page, 'Synthetic new computer'));
		await machineDialog(page).waitFor({ state: 'hidden' });
		await page
			.getByRole('button', { name: 'Configure Synthetic new computer', exact: true })
			.waitFor({ state: 'hidden' });
		assert.equal(fixtures.count('DELETE', machinesPath + '/runner-new'), 1);
	});
}

async function keylessMachineCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'keyless-machine',
		'keyless-machine',
		async ({ page }, fixtures) => {
			await openMachine(page, 'Synthetic computer A');
			const dialog = machineDialog(page);
			assert.equal(
				await dialog.locator('#machine-key').getAttribute('placeholder'),
				'Enter the Runner key',
				'A machine imported without its key does not offer to keep a saved key'
			);
			await dialog.getByText(/No Runner key is saved for this machine/).waitFor();
			assert.equal(
				await dialog.getByRole('button', { name: 'Save', exact: true }).isDisabled(),
				true,
				'Save waits for the missing key'
			);
			await dialog.locator('#machine-key').fill('synthetic-restored-key-a');
			await dialog.getByRole('button', { name: 'Save', exact: true }).click();
			await dialog.waitFor({ state: 'hidden' });
			const saved = fixtures.requests.find(
				(request) => request.method === 'POST' && request.path === machinesPath
			);
			assert.equal(saved.body.key, 'synthetic-restored-key-a');
			await openMachine(page, 'Synthetic computer A');
			assert.equal(
				await machineDialog(page).locator('#machine-key').getAttribute('placeholder'),
				'Leave empty to keep the saved key',
				'Once a key is saved, a blank key keeps it'
			);
			assert.equal(
				await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).isEnabled(),
				true
			);
			await dismissDialog(page, machineDialog(page), 'x');
		}
	);
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
		}
		if (action === 'remove') {
			await confirmMachineRemoval(await askToRemoveMachine(page, 'Synthetic computer A'));
		} else {
			await dialog
				.getByRole('button', { name: action === 'save' ? 'Save' : 'Verify', exact: true })
				.click();
		}
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
			assert.equal(request.body.name, 'Synthetic renamed computer A');
		}
		if (action === 'remove' && !errorResponse) {
			assert.equal(fixtures.count('DELETE', machinesPath + '/runner-a'), 1);
		}
		await reopened.locator('#machine-name').fill('Synthetic preserved new draft');
		await page.waitForTimeout(150);
		assert.equal(
			await reopened.locator('#machine-name').inputValue(),
			'Synthetic preserved new draft'
		);
		await dismissDialog(page, reopened, 'x');
		if (action === 'remove' && !errorResponse) {
			await setSettings(page, PROVIDERS_TAB);
			await providerCard(page, 'Claude')
				.getByText('Selected machine is unavailable.', { exact: true })
				.waitFor({ state: 'attached' });
		}
	});
}

async function staleStatusSettingsCase(browser, profile, variant) {
	const name = 'settings-status-' + variant;
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await setSettings(page, PROVIDERS_TAB);
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

async function machineRoundTripCase(browser, profile) {
	const name = 'settings-machine-round-trip';
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await openProviderSettings(page);
		const dialog = settingsDialog(page);
		assert.equal(await dialog.locator('input[value="full"]').isChecked(), true);
		await dialog.locator('#subscription-machine').selectOption('runner-b');
		assert.equal(await dialog.locator('input[value="chat"]').isChecked(), true);
		assert.equal(await dialog.locator('#subscription-workspace').inputValue(), '');
		await dialog.getByText(/Changing machines disables this subscription/).waitFor();
		await dialog.locator('#subscription-machine').selectOption('local');
		assert.equal(
			await dialog.locator('input[value="full"]').isChecked(),
			true,
			'Returning to the saved machine restores its access'
		);
		assert.equal(await dialog.locator('#subscription-workspace').inputValue(), '/fixture/old');
		assert.equal(
			await dialog.locator('#subscription-cli-path').inputValue(),
			'/fixture/claude-old'
		);
		assert.equal(await dialog.getByText(/Changing machines disables this subscription/).count(), 0);
		await dialog.getByRole('button', { name: 'Save', exact: true }).click();
		await dialog.waitFor({ state: 'hidden' });
		const saved = fixtures.requests.find((request) => request.path === configPath);
		assert.equal(saved.body.machine_id, 'local');
		assert.equal(saved.body.access, 'full');
		assert.equal(saved.body.workspace, '/fixture/old');
		assert.equal(saved.body.cli_path, '/fixture/claude-old');
		assert.equal(fixtures.providerSettings().access, 'full');
	});
}

async function staleStatusRegistryCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'checking-registry',
		'checking-registry',
		async ({ page }, fixtures) => {
			await setSettings(page, PROVIDERS_TAB);
			await waitUntil(fixtures.hasHeldCheck, 'Old registry status must be pending');
			await openMachine(page, 'Synthetic computer A');
			await machineDialog(page).locator('#machine-name').fill('Synthetic updated computer A');
			await machineDialog(page)
				.locator('#machine-url')
				.fill('https://new-runner-a.example.invalid');
			await machineDialog(page).locator('#machine-key').fill('synthetic-new-runner-a-key');
			await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
			await machineDialog(page).waitFor({ state: 'hidden' });
			await setSettings(page, PROVIDERS_TAB);
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
		await setSettings(page, PROVIDERS_TAB);
		const toggle = providerCard(page, 'Claude').getByRole('switch');
		assert.equal(await toggle.getAttribute('aria-checked'), 'false');
		await toggle.click();
		await waitUntil(fixtures.hasHeldConfig, 'The synthetic enable save must be pending');
		let expectedDraft = 'true';
		if (outcome === 'remount') {
			await setSettings(page, false);
			await providerCard(page, 'Claude').waitFor({ state: 'hidden' });
			await setSettings(page, PROVIDERS_TAB);
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

async function enableDuringOtherReloadCase(browser, profile) {
	const name = 'enable-delayed-other-reload';
	await runCase(browser, profile, name, name, async ({ page }, fixtures) => {
		await setSettings(page, PROVIDERS_TAB);
		const claudeToggle = providerCard(page, 'Claude').getByRole('switch');
		const codexToggle = providerCard(page, 'ChatGPT').getByRole('switch');
		await claudeToggle.click();
		await waitUntil(fixtures.hasHeldConfig, 'The Claude enable save must be pending');
		const modelReads = fixtures.count('GET', '/api/models');
		await codexToggle.click();
		await waitUntil(
			() => fixtures.count('GET', '/api/models') > modelReads,
			"ChatGPT's accepted save reloads both rows before Claude's reply arrives"
		);
		await fixtures.releaseConfig();
		await loginDialog(page).waitFor();
		assert.equal(fixtures.providerSettings().enable, true);
		await waitUntil(
			async () => (await claudeToggle.getAttribute('aria-checked')) === 'true',
			'The row catches up with the accepted enable'
		);
		assert.equal(fixtures.providerSettings('codex').enable, false);
		assert.equal(fixtures.count('POST'), 0, 'Enabling alone never starts CLI sign-in');
		await dismiss(page, 'x');
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
			const confirmation = await askToRemoveMachine(page, 'Synthetic computer A');
			await confirmation.getByText(/These subscriptions run on it/).waitFor();
			assert.deepEqual(
				await confirmation.getByRole('listitem').allInnerTexts(),
				['Claude'],
				'The confirmation names the subscriptions that removal turns off'
			);
			if (replacement !== 'local') {
				await captureScreenshot(page, profile.name + '-machine-remove-confirm');
			}
			await confirmMachineRemoval(confirmation);
			await machineDialog(page).waitFor({ state: 'hidden' });
			await setSettings(page, PROVIDERS_TAB);
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
		await setSettings(page, PROVIDERS_TAB);
		await providerCard(page, 'Claude')
			.getByRole('button', { name: 'Sign out', exact: true })
			.click();
		await waitUntil(fixtures.hasHeldLogout, 'Synthetic logout must be pending');
		if (method.startsWith('unmount')) {
			await setSettings(page, false);
			await providerCard(page, 'Claude').waitFor({ state: 'hidden' });
		}
		if (method === 'unmount-same') {
			await setSettings(page, PROVIDERS_TAB);
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
			await setSettings(page, PROVIDERS_TAB);
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
				"Can run read-only commands. Codex's read-only sandbox does not limit reads to the start folder, so it can read any file your account can. No edits.",
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
				'Where it starts for chats outside a chat folder, and in folders that let the AI choose. A chat folder with a specific project folder starts there instead. This is CLI context, not a project or filesystem grant. Chat only uses an empty folder.',
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
			await setSettings(page, PROVIDERS_TAB);
			await providerCard(page, 'Claude')
				.getByRole('button', { name: 'Sign out', exact: true })
				.click();
			await waitUntil(fixtures.hasHeldLogout, 'Old instance logout must be pending');
		}
		fixtures.externalRevisionReplacement();
		await openMachine(page, 'Synthetic computer A');
		await machineDialog(page).locator('#machine-url').fill('https://replacement-a.example.invalid');
		await machineDialog(page).locator('#machine-key').fill('synthetic-replacement-key-a');
		await machineDialog(page).locator('#machine-name').fill('Synthetic replacement computer A');
		await machineDialog(page).getByRole('button', { name: 'Save', exact: true }).click();
		await machineDialog(page).waitFor({ state: 'hidden' });
		await setSettings(page, PROVIDERS_TAB);
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
		if (!metadataOnly) {
			await machineDialog(page)
				.locator('#machine-url')
				.fill('https://replacement-a.example.invalid');
			await machineDialog(page).locator('#machine-key').fill('synthetic-replacement-key-a');
		}
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

function chatPanel(page, profile) {
	if (profile.mobile) return page.locator('.modal');
	return page.locator('#controls-container');
}

async function projectTabCase(browser, profile) {
	await runCase(browser, profile, 'project-tab', 'project-tab', async ({ page }, fixtures) => {
		await page
			.locator('.buddy-chat-header')
			.getByRole('button', { name: 'Controls', exact: true })
			.click();
		const panel = chatPanel(page, profile);
		await panel.getByRole('button', { name: 'Project', exact: true }).click();
		await panel.getByText('C:\\code\\site', { exact: true }).waitFor();
		const up = panel.getByRole('button', { name: 'Up one folder', exact: true });
		await panel.getByRole('button', { name: 'README.md', exact: true }).waitFor();
		assert.equal(
			await up.isDisabled(),
			true,
			'Browsing starts at the project folder and stops there'
		);
		await panel.getByRole('button', { name: 'src', exact: true }).click();
		await panel.getByRole('button', { name: 'app.ts', exact: true }).waitFor();
		await up.click();
		await panel.getByRole('button', { name: 'README.md', exact: true }).click();

		const editor = panel.getByLabel('Contents of README.md', { exact: true });
		await editor.waitFor();
		assert.equal(await editor.inputValue(), '# Site\nHello\n');
		assert.equal(await editor.getAttribute('readonly'), '', 'Files open read-only');
		await panel.getByRole('button', { name: 'Edit', exact: true }).click();
		await editor.fill('# Site\nHello again\n');
		await panel.getByRole('button', { name: 'Save', exact: true }).click();
		await panel.getByText('Saved on Work PC', { exact: true }).waitFor();
		assert.equal(
			fixtures.projectSaves[0].content,
			'# Site\r\nHello again\r\n',
			'Saving keeps CRLF'
		);
		assert.equal(fixtures.projectSaves[0].baseVersion, 'readme-1');
		assert.equal(fixtures.projectSaves[0].overwrite, false);

		// The tab keeps its open file while the panel shows another tab or closes.
		await editor.fill('# Site\nKept while away\n');
		await panel.getByRole('button', { name: 'Controls', exact: true }).click();
		await panel.getByRole('button', { name: 'Project', exact: true }).click();
		assert.equal(await editor.inputValue(), '# Site\nKept while away\n');
		await panel.getByRole('button', { name: 'Close', exact: true }).first().click();
		await page
			.locator('.buddy-chat-header')
			.getByRole('button', { name: 'Controls', exact: true })
			.click();
		await editor.waitFor();
		assert.equal(
			await editor.inputValue(),
			'# Site\nKept while away\n',
			'Closing the panel keeps unsaved edits'
		);

		await editor.fill('# Site\nHello once more\n');
		await panel.getByRole('button', { name: 'Save', exact: true }).click();
		await panel.getByText(/README\.md changed on Work PC after you opened it/).waitFor();
		await panel.getByRole('button', { name: 'Overwrite', exact: true }).click();
		await panel.getByText('Saved on Work PC', { exact: true }).waitFor();
		assert.equal(fixtures.projectSaves.length, 3);
		assert.equal(
			fixtures.projectSaves[2].overwrite,
			true,
			'Overwrite is chosen only after a conflict'
		);
		assert.equal(fixtures.projectSaves[2].baseVersion, 'readme-2');

		await editor.fill('# Site\nOffline edit\n');
		await panel.getByRole('button', { name: 'Save', exact: true }).click();
		await panel.getByText('Not saved: Work PC is offline.', { exact: true }).waitFor();
		assert.equal(
			await editor.inputValue(),
			'# Site\nOffline edit\n',
			'A failed save keeps the editor and the edits'
		);
		// Going back with unsaved edits asks first.
		page.once('dialog', (dialog) => dialog.accept());
		await panel.getByRole('button', { name: '← Files', exact: true }).click();
		await panel.getByRole('button', { name: 'README.md', exact: true }).waitFor();

		await panel.getByRole('tab', { name: 'Commands', exact: true }).click();
		const start = panel.getByRole('button', { name: 'Start a command session', exact: true });
		assert.equal(await start.isDisabled(), true, 'Commands wait for the risk to be confirmed');
		await panel
			.getByLabel('I understand. Let me run commands on Work PC.', { exact: true })
			.check();
		await start.click();
		await panel.getByLabel('Program', { exact: true }).fill('npm');
		await panel.getByLabel('Arguments, one per line', { exact: true }).fill('run\ntest');
		await panel.getByRole('button', { name: 'Run', exact: true }).click();
		await panel.getByText('Finished with exit code 0', { exact: true }).waitFor();
		assert.match(await panel.getByLabel('Command output', { exact: true }).innerText(), /ok/);
		await panel.getByRole('button', { name: 'Stop session', exact: true }).click();
		await panel.getByText('The command session stopped.', { exact: true }).waitFor();
		assert.equal(await start.isEnabled(), true, 'A new session can start after stopping');
		assert.equal(fixtures.projectRelayClients.size, 1, 'One page session on the computer');
		await captureScreenshot(page, profile.name + '-project-tab');
	});
}

async function projectAgentsCase(browser, profile) {
	await runCase(browser, profile, 'project-agents', 'project-agents', async (session, fixtures) => {
		const { page } = session;
		const robot = page.locator('#agent-menu-button');
		assert.equal(await robot.isVisible(), false, 'The collapsed composer keeps its tools hidden');
		// The composer shows its tool buttons once the draft is in use.
		if (profile.mobile) await page.locator('#chat-input').tap();
		else await page.locator('#chat-input').click();
		await robot.waitFor();
		assert.equal(await robot.getAttribute('aria-label'), 'Choose an agent');
		await robot.click();

		const menu = page.getByRole('menu');
		const reviewer = menu.getByRole('menuitemradio', { name: /Reviewer/ });
		await reviewer.waitFor();
		await menu.getByRole('menuitemradio', { name: /Tester/ }).waitFor();
		assert.equal(
			await menu.getByRole('menuitemradio', { name: /helper/i }).count(),
			0,
			'Agents hidden from the picker stay out'
		);
		assert.equal(await menu.getByText('notes.txt').count(), 0, 'Only Markdown files are agents');
		assert.equal(
			await menu.getByRole('menuitemradio', { name: 'No agent' }).getAttribute('aria-checked'),
			'true'
		);
		await captureScreenshot(page, profile.name + '-project-agents-menu');
		await reviewer.click();

		await waitUntil(
			async () => (await robot.getAttribute('aria-label')) === 'Agent: Reviewer',
			'The button names the chosen agent'
		);
		assert.equal((await robot.innerText()).trim(), 'Reviewer');
		await captureScreenshot(page, profile.name + '-project-agents-chosen');
		await waitUntil(
			() => session.mutations.some((entry) => entry.body?.chat?.agent?.name === 'Reviewer'),
			'The chosen agent is saved with the chat'
		);
		const saved = session.mutations.find((entry) => entry.body?.chat?.agent);
		assert.deepEqual(saved.body.chat.agent, {
			name: 'Reviewer',
			description: 'Reviews changes for bugs',
			path: '.github/agents/reviewer.agent.md',
			instructions: 'Review the diff.\nList risks first.'
		});
		assert.ok(
			fixtures.requests.some(
				(entry) => entry.method === 'DELETE' && entry.path.endsWith('/v1/session')
			),
			'Reading the agents ends its session on the computer'
		);

		await page.locator('#chat-input').click();
		await page.keyboard.type('Check my change');
		await page.locator('#send-message-button').click();
		await waitUntil(() => session.completions.length === 1, 'The message is sent');
		const system = session.completions[0].messages?.find((message) => message.role === 'system');
		assert.ok(system, 'The request has a system prompt');
		assert.match(
			system.content,
			/The user chose the "Reviewer" custom agent, from \.github\/agents\/reviewer\.agent\.md in the project folder\. Follow its instructions:\n\nReview the diff\.\nList risks first\.$/
		);
		// This case sends one message on purpose; runCase still checks that nothing else was sent.
		session.completions.splice(0);

		if (profile.mobile) await page.locator('#chat-input').tap();
		else await page.locator('#chat-input').click();
		await robot.click();
		await menu.getByRole('menuitemradio', { name: 'No agent' }).click();
		await waitUntil(
			async () => (await robot.getAttribute('aria-label')) === 'Choose an agent',
			'The button goes back to the robot alone'
		);
		await waitUntil(
			() => session.mutations.some((entry) => entry.body?.chat?.agent === null),
			'Clearing the agent is saved too'
		);
		await captureScreenshot(page, profile.name + '-project-agents');
	});
}

async function projectAgentsServerCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'project-agents-server',
		'project-agents-server',
		async (session, fixtures) => {
			const { page } = session;
			const robot = page.locator('#agent-menu-button');
			if (profile.mobile) await page.locator('#chat-input').tap();
			else await page.locator('#chat-input').click();
			await robot.click();

			const menu = page.getByRole('menu');
			await menu.getByText('Agents in src').waitFor();
			await menu.getByRole('menuitemradio', { name: /ResearchFast/ }).waitFor();
			await menu.getByRole('menuitemradio', { name: /QuickPlan/ }).click();
			await waitUntil(
				async () => (await robot.getAttribute('aria-label')) === 'Agent: QuickPlan',
				'The button names the agent chosen from This server'
			);
			assert.ok(
				fixtures.requests.some((entry) => entry.path.endsWith('/machines/local/agents')),
				'Buddy reads This server itself'
			);
			assert.equal(
				fixtures.requests.filter((entry) => entry.path.includes('/runners/machines/')).length,
				0,
				'This server needs no relay'
			);

			await page.locator('#chat-input').click();
			await page.keyboard.type('Plan it');
			await page.locator('#send-message-button').click();
			await waitUntil(() => session.completions.length === 1, 'The message is sent');
			const system = session.completions[0].messages?.find((message) => message.role === 'system');
			assert.match(
				system?.content ?? '',
				/from \.github\/agents\/QuickPlan\.agent\.md in the project folder\. Follow its instructions:\n\nWrite a short plan first\.$/
			);
			// This case sends one message on purpose; runCase still checks that nothing else was sent.
			session.completions.splice(0);
			await captureScreenshot(page, profile.name + '-project-agents-server');
		}
	);
}

async function folderChipCase(browser, profile) {
	await runCase(browser, profile, 'folder-chip', 'folder-chip', async ({ page }, fixtures) => {
		const chip = page.getByRole('button', { name: 'Project folder: Work PC · site', exact: true });
		await chip.waitFor();
		await chip.click();
		const dialog = page.locator('.modal').filter({ hasText: 'Project folder for “Website”' });
		await dialog.getByText('Let the AI choose', { exact: true }).click();
		await dialog.getByRole('button', { name: 'Save', exact: true }).click();
		await dialog.waitFor({ state: 'hidden' });
		await page
			.getByRole('button', { name: 'Project folder: AI chooses where to work', exact: true })
			.waitFor();
		const saved = fixtures.requests.filter((request) => request.method === 'PUT');
		assert.equal(saved.length, 1);
		assert.deepEqual(saved[0].body, { mode: 'auto', paths: {} });
		await captureScreenshot(page, profile.name + '-folder-chip');
	});
}

async function folderEditCase(browser, profile) {
	await runCase(browser, profile, 'folder-edit', 'folder-edit', async ({ page }, fixtures) => {
		await page.locator('button[aria-label="Folder options"]').click();
		await page.getByRole('button', { name: 'Edit', exact: true }).click();
		const dialog = page.locator('.modal').filter({ hasText: 'Project folder' });
		await dialog.getByRole('button', { name: 'Change', exact: true }).click();
		const pathInput = dialog.getByLabel('Folder path', { exact: true });
		await pathInput.fill('C:\\code\\docs');
		await pathInput.press('Enter');
		await dialog.getByText('C:\\code\\docs', { exact: true }).waitFor();
		assert.equal(
			await dialog.isVisible(),
			true,
			'Enter in the path picker does not submit the dialog'
		);
		assert.equal(fixtures.requests.filter((request) => request.method === 'PUT').length, 0);
		await dialog.getByRole('button', { name: 'Save', exact: true }).click();
		await dialog.waitFor({ state: 'hidden' });
		const saved = fixtures.requests.filter((request) => request.method === 'PUT');
		assert.deepEqual(saved[0].body, {
			mode: 'folder',
			paths: { 'pc-1': 'C:\\code\\docs' }
		});
		await page
			.getByRole('button', { name: 'Project folder: Work PC · docs', exact: true })
			.waitFor();
	});
}

async function openFolderSettings(page) {
	await page.locator('button[aria-label="Folder options"]').click();
	await page.getByRole('button', { name: 'Edit', exact: true }).click();
	const dialog = page.locator('.modal').filter({ hasText: 'Project folder' });
	await dialog.waitFor();
	return dialog;
}

async function activeElementId(page) {
	return page.evaluate(() => document.activeElement?.id ?? '');
}

async function folderAllowCase(browser, profile) {
	await runCase(browser, profile, 'folder-allow', 'folder-allow', async ({ page }, fixtures) => {
		const dialog = await openFolderSettings(page);
		const request = dialog.getByRole('group', { name: 'Let a plan work with files', exact: true });
		await request.waitFor();
		assert.equal(await dialog.getByText('Let the AI choose', { exact: true }).count(), 0);

		// Each button says which plan it changes, and the question carries its warning.
		const allowFull = request.getByRole('button', { name: 'Allow full access', exact: true });
		assert.equal(await allowFull.getAttribute('aria-describedby'), 'plan-access-plan-claude');
		await allowFull.click();
		const question = request.getByRole('group', {
			name: 'Give Claude full access on Work PC?',
			exact: true
		});
		await question.waitFor();
		assert.deepEqual((await question.getAttribute('aria-describedby')).split(' '), [
			'plan-access-what-claude',
			'plan-access-warning-claude',
			'plan-access-scope-claude'
		]);
		await request
			.getByText(
				'Buddy can then run commands on that computer as your account there, and so can anyone who controls this Buddy server.',
				{ exact: true }
			)
			.waitFor();
		assert.equal(await activeElementId(page), 'plan-access-cancel-claude', 'Cancel is focused');
		assert.equal(fixtures.planConfigs.length, 0, 'Asking changes nothing');
		await captureScreenshot(page, profile.name + '-folder-allow-question');

		await request.getByRole('button', { name: 'Cancel', exact: true }).click();
		assert.equal(await activeElementId(page), 'plan-access-full-claude', 'Focus returns');
		assert.equal(fixtures.planConfigs.length, 0, 'Cancelling changes nothing');

		await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
		await request.getByText('Let Claude read files on Work PC?', { exact: true }).waitFor();
		await request.getByRole('button', { name: 'Allow', exact: true }).click();
		await dialog.getByText('Let the AI choose', { exact: true }).waitFor();
		assert.deepEqual(fixtures.planConfigs, [
			{ access: 'read', expected_machine_id: 'pc-1', expected_machine_revision: 'rev-pc-1' }
		]);
		await waitUntil(
			async () => (await page.evaluate(() => document.activeElement?.type)) === 'radio',
			'Focus moves to the folder choices'
		);
		assert.equal(await dialog.isVisible(), true, 'Allowing does not submit the folder dialog');
		assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
		await captureScreenshot(page, profile.name + '-folder-allow-done');
	});
}

async function folderAllowStaleCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-stale',
		'folder-allow-stale',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			await request.getByRole('button', { name: 'Allow full access', exact: true }).click();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await request
				.getByText('The selected machine changed. Refresh before trying again.', { exact: true })
				.waitFor();
			assert.equal(await activeElementId(page), 'plan-access-allow-claude', 'Focus is on Allow');
			// The question stays open, and asking again confirms the computer as it is now.
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await dialog.getByText('Let the AI choose', { exact: true }).waitFor();
			assert.deepEqual(
				fixtures.planConfigs.map((body) => body.expected_machine_revision),
				['rev-pc-1-old', 'rev-pc-1']
			);
		}
	);
}

async function folderAllowBesideCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-beside',
		'folder-allow-beside',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', { name: 'Plans that only chat', exact: true });
			await request.waitFor();
			const offered = await request.innerText();
			assert.match(offered, /ChatGPT\s*·\s*Work PC\s*·\s*Chat only/);
			assert.doesNotMatch(offered, /Claude/, 'Claude already opens folders');

			// A folder that is just chat has no use for files.
			await dialog.getByText('No files, just chat', { exact: true }).click();
			await request.waitFor({ state: 'hidden' });
			// An unsaved choice survives the plans loading again.
			await dialog.getByText('A specific folder', { exact: true }).click();
			await request.waitFor();
			await captureScreenshot(page, profile.name + '-folder-allow-beside');

			await request.getByRole('button', { name: 'Allow full access', exact: true }).click();
			await request.getByText('Give ChatGPT full access on Work PC?', { exact: true }).waitFor();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await request.waitFor({ state: 'hidden' });
			assert.deepEqual(fixtures.planConfigs, [
				{ access: 'full', expected_machine_id: 'pc-1', expected_machine_revision: 'rev-pc-1' }
			]);
			const specificFolder = dialog.getByRole('radio', { name: /^A specific folder/ });
			assert.equal(await specificFolder.isChecked(), true, 'The unsaved choice is kept');
			await dialog.getByText('Claude, ChatGPT', { exact: false }).waitFor();
			assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
		}
	);
}

async function folderAllowRaceCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-race',
		'folder-allow-beside',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', { name: 'Plans that only chat', exact: true });
			await request.getByRole('button', { name: 'Allow full access', exact: true }).click();
			fixtures.holdNextProjectRead();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await waitUntil(() => fixtures.hasHeldProjectRead(), 'The plans are loading again');

			// The question stays, busy, until the plans show the change.
			const question = dialog.getByRole('group', {
				name: 'Give ChatGPT full access on Work PC?',
				exact: true
			});
			const allow = question.getByRole('button', { name: 'Allow', exact: true });
			assert.equal(await allow.isDisabled(), true, 'Allow is busy');
			const toast = page.getByText('ChatGPT now has full access on Work PC.', { exact: true });
			assert.equal(await toast.count(), 0, 'No success before the plans show it');

			// Choices made meanwhile are kept, and the busy question survives being hidden.
			await dialog.getByRole('radio', { name: /^No files, just chat/ }).check();
			await question.waitFor({ state: 'hidden' });
			const specificFolder = dialog.getByRole('radio', { name: /^A specific folder/ });
			await specificFolder.check();
			await question.waitFor();
			assert.equal(await allow.isDisabled(), true, 'Still busy after being hidden');

			// The person moves on while the plans load; focus stays with them.
			const folderName = page.locator('#folder-name');
			await folderName.focus();
			await fixtures.releaseProjectRead();
			await toast.waitFor();
			assert.equal(await specificFolder.isChecked(), true, 'The choice made meanwhile is kept');
			assert.equal(
				await folderName.evaluate((input) => input === document.activeElement),
				true,
				'Focus stays where the person put it'
			);
			assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
		}
	);
}

async function folderAllowReloadErrorCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-reload-error',
		'folder-allow',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			const failure = dialog.getByText(
				'The project folder could not be loaded: Synthetic project read failure',
				{ exact: true }
			);
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			fixtures.failNextProjectRead();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await failure.waitFor();

			// Asking again loads the plans, and the old failure goes away.
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await dialog.getByText('Let the AI choose', { exact: true }).waitFor();
			assert.equal(await failure.count(), 0, 'The reload failure is cleared');
		}
	);
}

async function folderAllowGoneCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-gone',
		'folder-allow',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			fixtures.removeWorkPc();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();

			// Claude is no longer offered, so the section says why the Allow did not happen.
			await dialog
				.getByText(
					'Claude was not given access: Verify the selected machine again, then refresh before trying again.',
					{ exact: true }
				)
				.waitFor();
			await request.waitFor({ state: 'detached' });
			const openSettings = dialog.getByRole('button', {
				name: 'Open Settings → Machines',
				exact: true
			});
			await openSettings.waitFor();
			assert.equal(
				await openSettings.evaluate((button) => button === document.activeElement),
				true,
				'Focus stays in the section'
			);
		}
	);
}

async function folderAllowHiddenRefusalCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-hidden-refusal',
		'folder-allow-beside',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', { name: 'Plans that only chat', exact: true });
			await request.getByRole('button', { name: 'Allow full access', exact: true }).click();
			fixtures.refuseNextPlanConfig('The start folder does not exist on Work PC: C:\\old');
			fixtures.holdNextProjectRead();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await waitUntil(
				() => fixtures.hasHeldProjectRead(),
				'The plans load again after the refusal'
			);

			// The request hides before it can say why; the section says it instead.
			await dialog.getByRole('radio', { name: /^No files, just chat/ }).check();
			await fixtures.releaseProjectRead();
			await dialog
				.getByText(
					'ChatGPT was not given access: The start folder does not exist on Work PC: C:\\old',
					{ exact: true }
				)
				.waitFor();
		}
	);
}

async function folderAllowTurnedOffCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-turned-off',
		'folder-allow',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			fixtures.turnOffAfterNextPlanConfig(
				'An old provider process could not confirm that it stopped.'
			);
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await dialog
				.getByText(
					'Claude was given the access but then turned off: An old provider process could not confirm that it stopped.',
					{ exact: true }
				)
				.waitFor();
			await dialog.getByRole('button', { name: 'Open Settings → Machines', exact: true }).waitFor();
		}
	);
}

async function folderAllowClosedCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-closed',
		'folder-allow',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			fixtures.holdNextPlanConfig();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			await waitUntil(() => fixtures.hasHeldPlanConfig(), 'The Allow is saving');

			// The dialog closes before the answer; a refusal still reaches the person.
			await dialog.getByRole('button', { name: 'Close', exact: true }).first().click();
			await dialog.waitFor({ state: 'hidden' });
			fixtures.refuseNextPlanConfig('Synthetic refusal after the dialog closed');
			await fixtures.releasePlanConfig();
			await page
				.getByText('Claude was not given access: Synthetic refusal after the dialog closed', {
					exact: true
				})
				.waitFor();
		}
	);
}

async function folderAllowRevokedCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-revoked',
		'folder-allow',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			// An administrator stops this person using computers meanwhile.
			fixtures.revokeMachines();
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			// The section goes, so the reason comes as a toast.
			await page
				.getByText('Claude was not given access: Synthetic machines not allowed', { exact: true })
				.waitFor();
			await page.locator('#project-folder-heading').waitFor({ state: 'detached' });
		}
	);
}

async function folderAllowLostAnswerCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-lost-answer',
		'folder-allow',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			await request.getByRole('button', { name: 'Allow reading files', exact: true }).click();
			fixtures.loseNextPlanConfigAnswer('Synthetic gateway timeout');
			await request.getByRole('button', { name: 'Allow', exact: true }).click();
			// The access was saved, so the reload shows it: a success, not a refusal.
			await page.getByText('Claude can now read files on Work PC.', { exact: true }).waitFor();
			await dialog.getByText('Let the AI choose', { exact: true }).waitFor();
			assert.equal(
				await dialog.getByText(/was not given access/).count(),
				0,
				'No refusal is shown'
			);
		}
	);
}

async function folderAllowHomelessCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-allow-homeless',
		'folder-allow-homeless',
		async ({ page }) => {
			const dialog = await openFolderSettings(page);
			const request = dialog.getByRole('group', {
				name: 'Let a plan work with files',
				exact: true
			});
			// Only ChatGPT is called Chat only; Claude's missing computer is named too.
			await request
				.getByText(
					'ChatGPT is set to Chat only, so it can’t open a project folder. Allowing files changes it for all your chats, not just this folder.',
					{ exact: true }
				)
				.waitFor();
			await dialog
				.getByText('Claude has no computer it can use. Choose one in Settings → Machines.', {
					exact: true
				})
				.waitFor();
			await dialog.getByRole('button', { name: 'Open Settings → Machines', exact: true }).waitFor();
			assert.equal(await request.getByText('Claude', { exact: true }).count(), 0);
		}
	);
}

async function folderNoPlansCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-no-plans',
		'folder-no-plans',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			await dialog
				.getByText(
					'No AI plan is turned on. Turn one on in Settings → Machines to let the AI work in a folder on your computer.',
					{ exact: true }
				)
				.waitFor();
			const projectReads = () =>
				fixtures.requests.filter(
					(entry) =>
						entry.method === 'GET' && entry.path === '/api/v1/subscriptions/folders/website/project'
				).length;
			const readsBefore = projectReads();
			await captureScreenshot(page, profile.name + '-folder-no-plans');

			await dialog.getByRole('button', { name: 'Open Settings → Machines', exact: true }).click();
			const settings = page.locator('.modal').filter({ hasText: 'AI plans' });
			await settings.waitFor();
			fixtures.turnOnPlan('claude', 'full');
			// Phones show Settings as a page with its own close button.
			let closeSettings = page.locator('#settings-tabs-container').getByRole('button', {
				name: 'Back',
				exact: true
			});
			if (profile.mobile) {
				closeSettings = settings.getByRole('button', { name: 'Close Settings', exact: true });
			}
			await closeSettings.click();
			await settings.waitFor({ state: 'hidden' });

			// Back in the folder settings, which load the plans again; the folder chip
			// and Project tab follow the change too.
			await dialog.getByText('Let the AI choose', { exact: true }).waitFor();
			const readsAfter = projectReads();
			assert.ok(readsAfter > readsBefore, 'The plans load again');
			await page.waitForTimeout(500);
			assert.equal(projectReads(), readsAfter, 'Loading settles');
			await waitUntil(
				async () => (await page.evaluate(() => document.activeElement?.type)) === 'radio',
				'Focus moves to the folder choices that replaced the button'
			);
			assert.equal(await dialog.isVisible(), true, 'The folder settings stay open');
			assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
		}
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
		await projectTabCase(browser, profile);
		await projectAgentsCase(browser, profile);
		await projectAgentsServerCase(browser, profile);
		await folderChipCase(browser, profile);
		await folderEditCase(browser, profile);
		await folderAllowCase(browser, profile);
		await folderAllowStaleCase(browser, profile);
		await folderAllowBesideCase(browser, profile);
		await folderAllowRaceCase(browser, profile);
		await folderAllowReloadErrorCase(browser, profile);
		await folderAllowGoneCase(browser, profile);
		await folderAllowHiddenRefusalCase(browser, profile);
		await folderAllowTurnedOffCase(browser, profile);
		await folderAllowClosedCase(browser, profile);
		await folderAllowRevokedCase(browser, profile);
		await folderAllowLostAnswerCase(browser, profile);
		await folderAllowHomelessCase(browser, profile);
		await folderNoPlansCase(browser, profile);
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
		await serverCancelCase(browser, profile);
		await unsafeLinkCase(browser, profile);
		await codexSignInTextCase(browser, profile);
		await successCase(browser, profile);
		await codeLoginCase(browser, profile);
		await codeLoginCase(browser, profile, true);
		await heldCodeCase(browser, profile);
		await checkingTransitionCase(browser, profile);
		await lateCheckCase(browser, profile, false);
		await lateCheckCase(browser, profile, true);
		await failedCheckCase(browser, profile);
		await pendingCheckUnmountCase(browser, profile);
		await apiBillingCase(browser, profile);
		await usageNavigationCase(browser, profile);
		await permissionCase(browser, profile);
		await machineCrudCase(browser, profile);
		await keylessMachineCase(browser, profile);
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
		await machineRoundTripCase(browser, profile);
		await delayedSettingsCase(browser, profile, 'x');
		await delayedSettingsCase(browser, profile, 'unmount');
		await delayedSettingsCase(browser, profile, 'x', true);
		await delayedEnableCase(browser, profile);
		await delayedEnableCase(browser, profile, 'error');
		await delayedEnableCase(browser, profile, 'remount');
		await enableDuringOtherReloadCase(browser, profile);
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
