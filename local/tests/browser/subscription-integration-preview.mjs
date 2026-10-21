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
	['showSettings', 'socket']
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
// Ultra's chat model: a Claude plan model whose CLI can run workers, listed as the server lists it.
const ultraChatModel = {
	id: 'claude-code.synthetic-sonnet',
	name: 'Claude Synthetic Sonnet',
	object: 'model',
	created: 0,
	owned_by: 'subscription',
	connection_type: 'external',
	tags: [{ name: 'Claude plan' }],
	subscription: {
		provider: 'claude',
		model: 'synthetic-sonnet',
		description: 'Synthetic Ultra plan model.',
		efforts: ['low', 'medium', 'high'],
		ultra: true,
		ultra_max_workers: null
	}
};
// A second Claude plan model the workers can run on, with fewer thinking levels.
const ultraWorkerModel = {
	...ultraChatModel,
	id: 'claude-code.synthetic-haiku',
	name: 'Claude Synthetic Haiku',
	subscription: {
		...ultraChatModel.subscription,
		model: 'synthetic-haiku',
		efforts: ['low', 'medium']
	}
};

/** The chat the Ultra case opens: one finished reply from Ultra's chat model. */
function ultraChatFixture() {
	const now = Math.floor(Date.now() / 1000);
	const prompt = {
		id: 'ultra-user-message',
		parentId: null,
		childrenIds: ['ultra-first-reply'],
		role: 'user',
		content: 'Can you look at the parser?',
		timestamp: now - 60
	};
	const reply = {
		id: 'ultra-first-reply',
		parentId: prompt.id,
		childrenIds: [],
		role: 'assistant',
		model: ultraChatModel.id,
		modelName: ultraChatModel.name,
		content: 'A plain answer for optional panel tests.',
		timestamp: now - 30,
		done: true
	};
	return {
		id: 'buddy-design-chat',
		title: 'A little room to think',
		user_id: 'buddy-design-user',
		created_at: now - 60,
		updated_at: now,
		last_read_at: now,
		pinned: false,
		active: false,
		current_message_id: reply.id,
		folder_id: null,
		chat: {
			title: 'A little room to think',
			models: [ultraChatModel.id],
			params: {},
			files: [],
			history: { currentId: reply.id, messages: { [prompt.id]: prompt, [reply.id]: reply } },
			messages: [prompt, reply]
		}
	};
}

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
		if (mode === 'computer-sign-in') {
			// Claude is on and signed in on this server, but not yet on computer A.
			provider.settings.enable = true;
			provider.computers = [
				{ machine_id: 'local', name: 'This server', online: true, state: 'ready' },
				{ machine_id: 'runner-a', name: 'Synthetic computer A', online: true, state: 'signed_out' }
			];
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
	const runTargetModes = new Set(['run-target-new', 'run-target-bound']);
	const projectModes = new Set([
		'project-tab',
		'project-agents',
		'project-agents-server',
		'folder-chip',
		'folder-edit',
		'folder-no-plans',
		'folder-defaults',
		'folder-create',
		'folder-create-shared',
		'folder-no-access',
		'folder-browse',
		...runTargetModes
	]);
	// Modes that start from a folder choice with nothing picked yet.
	const askingModes = new Set(['folder-no-plans', 'folder-defaults']);
	const projectRelayBase = '/api/v1/runners/machines/pc-1/workspace';
	const workPc = {
		machine_id: 'pc-1',
		name: 'Work PC',
		kind: 'app',
		online: true,
		platform: 'win32',
		can_browse: true,
		folders: 'on',
		recent_paths: ['C:\\code\\site']
	};
	// Folders on Work PC, for browsing its whole disk.
	const workPcFolders = {
		'C:\\': [{ name: 'code', path: 'C:\\code', hidden: false, link: false, selectable: true }],
		'C:\\code': [
			{ name: 'docs', path: 'C:\\code\\docs', hidden: false, link: false, selectable: true },
			{ name: 'site', path: 'C:\\code\\site', hidden: false, link: false, selectable: true }
		]
	};
	let projectChoice = {
		folder_id: 'website',
		folder_name: 'Website',
		mode: 'folder',
		paths: { 'pc-1': { path: 'C:\\code\\site', problem: null } },
		machine_id: '',
		access: '',
		offered: {}
	};
	if (askingModes.has(mode)) {
		projectChoice = { ...projectChoice, mode: 'auto', paths: {} };
	}
	if (mode === 'project-agents-server') {
		projectChoice = {
			...projectChoice,
			paths: { local: { path: 'C:\\baseball\\src', problem: null } }
		};
	}
	// The person's plans: whether each is on, and the access new chats start with.
	const folderPlans = [
		{ id: 'claude', name: 'Claude', enable: true, access: 'full' },
		{ id: 'codex', name: 'ChatGPT', enable: false, access: 'chat' }
	];
	if (mode === 'folder-no-plans') {
		folderPlans[0].enable = false;
	}
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
		// Like the server: every computer, each with every plan that is on.
		const providers = folderPlans
			.filter((plan) => plan.enable)
			.map((plan) => ({ id: plan.id, name: plan.name, access: plan.access }));
		const computers = [{ ...workPc, providers }];
		if (mode === 'project-agents-server') {
			computers.push({
				machine_id: 'local',
				name: 'This server',
				kind: 'server',
				online: true,
				platform: 'win32',
				can_browse: false,
				folders: 'unsupported',
				providers,
				recent_paths: []
			});
		}
		return {
			folder_id: 'website',
			folder_name: 'Website',
			own: projectChoice,
			inherited: null,
			shared_instructions: false,
			others_can_edit: false,
			has_plans: folderPlans.some((plan) => plan.enable),
			computers
		};
	}

	/** A folder about to be made: under Website it follows Website's choice; at the top, nothing. */
	function newFolderProject(parentId) {
		let inherited = null;
		if (parentId === 'website') inherited = { ...projectChoice, folder_name: 'Website' };
		return {
			...folderProject(),
			folder_id: '',
			folder_name: '',
			own: null,
			inherited,
			// Someone else may edit Website, and so a folder made in it.
			others_can_edit: parentId === 'website' && mode === 'folder-create-shared'
		};
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
		} else if (path === '/api/v1/folders' && method === 'POST') {
			const body = request.postDataJSON();
			requests.push({ method, path, body });
			reply = {
				id: 'new-folder',
				name: body.name,
				parent_id: body.parent_id,
				user_id: 'buddy-design-user',
				data: body.data,
				meta: body.meta,
				items: { chats: [], files: [] },
				write_access: true,
				created_at: 2,
				updated_at: 2
			};
		} else if (path === '/api/v1/subscriptions/new-folder/project' && method === 'GET') {
			const parentId = url.searchParams.get('parent_id');
			requests.push({ method, path, parentId });
			reply = newFolderProject(parentId);
		} else if (path === '/api/v1/subscriptions/folders/new-folder/project' && method === 'PUT') {
			requests.push({ method, path, body: request.postDataJSON() });
			reply = newFolderProject();
		} else if (path === '/api/v1/subscriptions/folders/website/project') {
			requests.push({ method, path, body: request.postDataJSON() });
			if (mode === 'folder-no-access') {
				// This person may not use computers.
				await route.fulfill({ status: 403, json: { detail: 'Synthetic machines not allowed' } });
				return true;
			}
			if (method === 'PUT') {
				const body = request.postDataJSON();
				const paths = {};
				for (const [machineId, path] of Object.entries(body.paths || {})) {
					paths[machineId] = { path, problem: null };
				}
				// Like the server: the whole choice is replaced, every field included.
				const offered = {};
				for (const [machineId, entries] of Object.entries(body.offered || {})) {
					offered[machineId] = entries.map((entry) => ({ path: entry, problem: null }));
				}
				projectChoice = {
					folder_id: 'website',
					folder_name: 'Website',
					mode: body.mode,
					paths,
					machine_id: body.machine_id ?? '',
					access: body.access ?? '',
					offered
				};
			}
			reply = folderProject();
		} else if (
			path === '/api/v1/subscriptions/machines/pc-1/folders/suggested' &&
			method === 'GET'
		) {
			requests.push({ method, path });
			if (mode === 'run-target-new') {
				// Slow enough that the bar's folder menu opens before the suggestions arrive.
				await new Promise((resolve) => setTimeout(resolve, 600));
			}
			const day = 86400;
			reply = {
				state: 'ok',
				folders: [
					{
						path: 'C:\\code\\api',
						sources: ['claude', 'codex'],
						last_used: Math.floor(Date.now() / 1000 / day) * day - 2 * day
					}
				]
			};
		} else if (path === '/api/v1/subscriptions/machines/pc-1/folders' && method === 'GET') {
			const listed = url.searchParams.get('path') || '';
			requests.push({ method, path, listed });
			if (!listed) {
				reply = {
					path: '',
					parent: null,
					roots: [{ name: 'C:', path: 'C:\\', kind: 'drive' }],
					entries: [],
					truncated: false
				};
			} else {
				assert.ok(workPcFolders[listed], 'Browsing asks only for folders it was shown');
				let parent = null;
				if (listed === 'C:\\code') parent = 'C:\\';
				reply = {
					path: listed,
					parent,
					roots: [],
					entries: workPcFolders[listed],
					truncated: false
				};
			}
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

	// --- Where chats run: the composer's bar -----------------------------------
	// A bound chat opens with Work PC offline, until the case brings it online.
	let workPcOnline = mode !== 'run-target-bound';
	const runTargetComputer = {
		machine_id: 'pc-1',
		name: 'Work PC',
		kind: 'app',
		online: true,
		platform: 'win32',
		can_browse: true,
		folders: 'on',
		recent_paths: [],
		revision: 'rev-pc-1',
		providers: { codex: { state: 'unknown', message: null } }
	};

	function runTargetOptions() {
		// Other cases keep the bar hidden, as for someone the policy doesn't allow.
		if (!runTargetModes.has(mode)) return { allowed: false, has_plans: false };
		return {
			allowed: true,
			has_plans: true,
			plan_access: { codex: 'full' },
			computers: [{ ...runTargetComputer, online: workPcOnline }],
			recent: [{ machine_id: 'pc-1', path: 'C:\\code\\site', last_used: 1760000000 }],
			chat_folder: null,
			default: {
				kind: 'detached',
				machine_id: null,
				folder: null,
				access: 'chat',
				from: 'none',
				access_locked: false
			}
		};
	}

	async function routeRunTarget(route, request, url, path) {
		if (path === '/api/v1/subscriptions/run-target/options') {
			requests.push({ method: request.method(), path });
			await route.fulfill({ status: 200, json: runTargetOptions() });
			return true;
		}
		if (path === '/api/v1/subscriptions/run-target/readiness') {
			requests.push({
				method: request.method(),
				path,
				provider: url.searchParams.get('provider'),
				machineId: url.searchParams.get('machine_id')
			});
			await route.fulfill({ status: 200, json: { state: 'ready', message: null } });
			return true;
		}
		if (/^\/api\/v1\/subscriptions\/chats\/[^/]+\/run-target$/.test(path)) {
			requests.push({ method: request.method(), path });
			if (mode !== 'run-target-bound') {
				await route.fulfill({ status: 404, json: { detail: 'That chat no longer exists.' } });
				return true;
			}
			await route.fulfill({
				status: 200,
				json: {
					bound: true,
					kind: 'computer',
					machine_id: 'pc-1',
					computer: { name: 'Work PC', kind: 'app', online: true },
					folder_mode: 'path',
					path: 'C:\\code\\site',
					access: 'full',
					source: 'bar',
					note: null,
					problem: null
				}
			});
			return true;
		}
		return false;
	}

	// --- Ultra: the chat that uses it and the workers stopped one by one ------
	let ultraChat = null;
	if (mode === 'ultra') ultraChat = ultraChatFixture();
	const workerStops = [];

	/** Answers the Ultra chat's reads and saves; returns false for anything else. */
	async function routeUltraChat(route, request, path) {
		if (path !== '/api/v1/chats/buddy-design-chat') return false;
		const method = request.method();
		if (method === 'POST') {
			const body = request.postDataJSON();
			requests.push({ method, path, body });
			// Like the server, a save merges its fields into the saved chat.
			ultraChat = { ...ultraChat, chat: { ...ultraChat.chat, ...body.chat } };
		} else if (method !== 'GET') {
			return false;
		}
		await route.fulfill({ status: 200, json: ultraChat });
		return true;
	}

	let sessionOptions = {};
	if (mode === 'project-tab' || mode === 'project-agents' || mode === 'project-agents-server')
		sessionOptions = { folderId: 'website' };
	if (mode === 'run-target-new') sessionOptions = { path: '/' };
	if (
		mode === 'folder-chip' ||
		mode === 'folder-edit' ||
		mode === 'folder-browse' ||
		mode === 'folder-create' ||
		mode === 'folder-create-shared' ||
		mode === 'folder-no-access' ||
		askingModes.has(mode)
	) {
		sessionOptions = { path: '/folders/website' };
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
			let data = [baseModel];
			if (mode === 'ultra') {
				data = [baseModel, ultraChatModel, ultraWorkerModel];
			} else if (role === 'admin') {
				data = [baseModel, subscriptionModel];
			}
			await route.fulfill({ status: 200, json: { object: 'list', data } });
			return;
		}
		if (mode === 'ultra' && (await routeUltraChat(route, request, path))) {
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
		if (await routeRunTarget(route, request, url, path)) {
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
		const workerStop = path.match(
			/^\/api\/v1\/subscriptions\/chats\/([^/]+)\/workers\/([^/]+)\/stop$/
		);
		if (mode === 'ultra' && workerStop && request.method() === 'POST') {
			workerStops.push({
				chatId: decodeURIComponent(workerStop[1]),
				workerId: decodeURIComponent(workerStop[2])
			});
			await route.fulfill({ status: 200, json: { stopped: true } });
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
		let chosenMachineId = null;
		// A plan's settings name the computer it runs on; only sign-in actions name a chosen computer.
		if (!path.endsWith('/config')) {
			chosenMachineId = body?.machine_id ?? url.searchParams.get('machine_id');
		}
		if ((providerId === 'claude' || providerId === 'codex') && chosenMachineId) {
			// A sign-in action on a chosen computer names it, with the revision the page showed.
			const chosen = machines.find((machine) => machine.id === chosenMachineId);
			assert.ok(chosen, 'A chosen computer is one of the person’s');
			assert.equal(
				recordedRequest.expectedMachineId,
				null,
				'A chosen computer is not the plan’s guard'
			);
			assert.equal(
				recordedRequest.expectedMachineRevision,
				chosen.revision ?? 'local',
				'A chosen computer carries its revision'
			);
		} else if (providerId === 'claude' || providerId === 'codex') {
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
		workerStops,
		// A plan turned on elsewhere, such as in Settings → Machines.
		turnOnPlan(id, access) {
			const plan = folderPlans.find((candidate) => candidate.id === id);
			plan.enable = true;
			plan.access = access;
		},
		bringWorkPcOnline: () => {
			workPcOnline = true;
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
			// The chat page asks where its chat runs; nothing else about plans is asked.
			const planRequests = fixtures.requests.filter(
				(request) =>
					request.path.startsWith('/api/v1/subscriptions') &&
					!request.path.startsWith('/api/v1/subscriptions/run-target/') &&
					!request.path.endsWith('/run-target')
			);
			assert.equal(planRequests.length, 0);
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
				'New chats start with this, and each chat can choose its own above the message box. Model sign-in does not create project or file API grants.',
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

// --- Ultra: the composer button, its menu and a reply's workers card ----------

const ultraTrigger = '#ultra-menu-button';
const composerModelTrigger = '.buddy-composer button[id^="model-selector-"][id$="-button"]';

function ultraMenu(page) {
	return page.locator('[role="menu"]').filter({ has: page.locator('.buddy-ultra-menu') });
}

/** Waits for the menu, and for its opening animation, which scales it, to end. */
async function waitForMenu(menu) {
	await menu.waitFor();
	await menu.evaluate(async (element) => {
		// The animation starts on a later frame than the menu appears.
		await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
		const animations = element.getAnimations({ subtree: true });
		await Promise.all(animations.map((animation) => animation.finished));
	});
}

/** Taps on phones and clicks on desktop, as a person would. */
async function press(profile, locator) {
	if (profile.mobile) {
		await locator.tap();
	} else {
		await locator.click();
	}
}

async function draftHasFocus(page) {
	return page.evaluate(() => {
		const input = document.getElementById('chat-input');
		if (!input) return false;
		return input === document.activeElement || input.contains(document.activeElement);
	});
}

/** The visible height the phone's on-screen keyboard leaves; null for no keyboard. */
async function setVisibleHeight(page, height, top = 0) {
	await page.evaluate(
		({ height, top }) => {
			const visible = height ?? window.innerHeight;
			Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => visible });
			Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => top });
			visualViewport.dispatchEvent(new Event('resize'));
			visualViewport.dispatchEvent(new Event('scroll'));
		},
		{ height, top }
	);
	await page.waitForTimeout(300);
}

/** Where the composer's model, Thinking and Ultra controls sit, in the page and on screen. */
async function composerControlOrder(page) {
	return page.locator('.buddy-composer').evaluate((composer) => {
		const model = composer.querySelector('.buddy-composer-model');
		const thinking = composer.querySelector('#owui-reasoning-chip');
		const ultra = composer.querySelector('.buddy-composer-ultra');
		function box(element) {
			const rect = element.getBoundingClientRect();
			return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
		}
		if (!model || !thinking || !ultra) return { found: false };
		return {
			found: true,
			sameRow:
				model.parentElement === thinking.parentElement &&
				thinking.parentElement === ultra.parentElement,
			adjacent: model.nextElementSibling === thinking && thinking.nextElementSibling === ultra,
			rowClass: model.parentElement.className,
			rowLabel: model.parentElement.getAttribute('aria-label'),
			boxes: [box(model), box(thinking), box(ultra)]
		};
	});
}

function assertComposerOrder(order, profile) {
	assert.ok(order.found, 'The composer shows its model, Thinking and Ultra controls');
	assert.ok(order.sameRow, 'Model, Thinking and Ultra share one row');
	assert.ok(order.adjacent, 'The composer reads [Model][Thinking][Ultra]');
	const [model, thinking, ultra] = order.boxes;
	assert.ok(model.right <= thinking.left + 1, 'Thinking sits right of the model');
	assert.ok(thinking.right <= ultra.left + 1, 'Ultra sits right of Thinking');
	if (profile.mobile) {
		assert.match(order.rowClass, /buddy-composer-settings/, 'Phones show them in the settings row');
		assert.equal(order.rowLabel, 'Model, thinking and Ultra settings');
	} else {
		assert.match(order.rowClass, /buddy-composer-actions/, 'Desktop shows them with the actions');
	}
}

/**
 * The contrast ratio of an element's text against what shows behind it: its own
 * background, over its ancestors' where it is see-through.
 */
async function textContrast(locator) {
	return locator.evaluate(async (element) => {
		// A color change may still be easing in.
		await Promise.all(element.getAnimations().map((animation) => animation.finished));
		const canvas = document.createElement('canvas');
		canvas.width = 1;
		canvas.height = 1;
		const context = canvas.getContext('2d', { willReadFrequently: true });
		function pixel() {
			return Array.from(context.getImageData(0, 0, 1, 1).data);
		}
		function opaque(color) {
			context.clearRect(0, 0, 1, 1);
			context.fillStyle = color;
			context.fillRect(0, 0, 1, 1);
			return pixel()[3] === 255;
		}
		// The backgrounds from the element out to the first opaque one, painted back to front.
		const backgrounds = [];
		for (let node = element; node; node = node.parentElement) {
			const color = getComputedStyle(node).backgroundColor;
			backgrounds.push(color);
			if (opaque(color)) break;
		}
		context.fillStyle = '#ffffff';
		context.fillRect(0, 0, 1, 1);
		for (const color of backgrounds.reverse()) {
			context.fillStyle = color;
			context.fillRect(0, 0, 1, 1);
		}
		const background = pixel().slice(0, 3);
		context.fillStyle = getComputedStyle(element).color;
		context.fillRect(0, 0, 1, 1);
		const text = pixel().slice(0, 3);
		function luminance(channels) {
			const linear = channels.map((channel) => {
				const value = channel / 255;
				if (value <= 0.04045) return value / 12.92;
				return ((value + 0.055) / 1.055) ** 2.4;
			});
			return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
		}
		const lighter = Math.max(luminance(text), luminance(background));
		const darker = Math.min(luminance(text), luminance(background));
		return (lighter + 0.05) / (darker + 0.05);
	});
}

/**
 * On a narrow phone the Ultra chip shows only its icon, so the model chip has the
 * room, and it still takes taps across 44px.
 */
async function assertIconOnlyUltraChip(page) {
	const chip = await page.locator(ultraTrigger).evaluate((button) => {
		const rect = button.getBoundingClientRect();
		const y = rect.top + rect.height / 2;
		function takesTap(x) {
			const hit = document.elementFromPoint(x, y);
			return Boolean(hit && button.contains(hit));
		}
		return {
			width: rect.width,
			labelShown: button.querySelector('.buddy-ultra-label').getClientRects().length > 0,
			name: button.getAttribute('aria-label'),
			left: takesTap(rect.left - 3.5),
			right: takesTap(rect.right + 3.5)
		};
	});
	assert.equal(chip.labelShown, false, 'A narrow phone shows the Ultra chip as an icon');
	assert.ok(chip.width <= 44, 'The icon-only Ultra chip leaves the model room: ' + chip.width);
	assert.ok(
		chip.left && chip.right,
		'The icon-only chip takes taps either side of it: ' + JSON.stringify(chip)
	);
	assert.match(chip.name, /^Ultra: /, 'The icon-only chip keeps its name');
	const room = await page.locator(composerModelTrigger).evaluate((button) => {
		const label = button.querySelector('.truncate');
		const sample = label.cloneNode();
		sample.textContent = 'Claude Sonnet 4.5';
		sample.style.position = 'absolute';
		sample.style.visibility = 'hidden';
		label.parentElement.appendChild(sample);
		const needed = sample.scrollWidth;
		sample.remove();
		return { needed, available: label.clientWidth };
	});
	assert.ok(
		room.available >= room.needed,
		'The model chip has room for a plan model’s name: ' + JSON.stringify(room)
	);
}

/** The phone's settings-row chips look 36px tall but take taps across 44px. */
async function assertSettingsChipsTakeTaps(page) {
	const chips = await page.locator('.buddy-composer-settings').evaluate((row) => {
		const buttons = [
			row.querySelector('button[id^="model-selector-"]'),
			row.querySelector('#owui-reasoning-chip'),
			row.querySelector('#ultra-menu-button')
		];
		return buttons.map((button) => {
			const rect = button.getBoundingClientRect();
			const x = rect.left + rect.width / 2;
			function takesTap(y) {
				const hit = document.elementFromPoint(x, y);
				return Boolean(hit && button.contains(hit));
			}
			return {
				name: button.id,
				height: rect.height,
				above: takesTap(rect.top - 3.5),
				below: takesTap(rect.bottom + 3.5)
			};
		});
	});
	for (const chip of chips) {
		assert.ok(
			chip.above && chip.below,
			'Settings chips take taps across 44px: ' + JSON.stringify(chip)
		);
	}
}

/** Closes the Ultra menu as a person would: its button again, or Escape when the menu covers it. */
async function closeUltraMenu(page, profile) {
	const triggerReceivesTap = await page.locator(ultraTrigger).evaluate((button) => {
		const bounds = button.getBoundingClientRect();
		const hit = document.elementFromPoint(
			bounds.x + bounds.width / 2,
			bounds.y + bounds.height / 2
		);
		return Boolean(hit && button.contains(hit));
	});
	if (triggerReceivesTap) {
		await press(profile, page.locator(ultraTrigger));
	} else {
		await page.keyboard.press('Escape');
	}
	await ultraMenu(page).waitFor({ state: 'detached' });
}

/** Text in the menu that is cut short with an ellipsis. */
async function truncatedTexts(menu) {
	return menu.locator('.buddy-ultra-menu').evaluate((element) =>
		Array.from(element.querySelectorAll('.truncate'))
			.filter((text) => text.scrollWidth > text.clientWidth + 1)
			.map((text) => text.textContent.trim())
	);
}

async function radioNames(group) {
	return group
		.getByRole('menuitemradio')
		.evaluateAll((options) => options.map((option) => option.getAttribute('aria-label')));
}

/** Every row of an open phone menu is a full touch target; the switch inside a row is not one. */
async function assertTouchRows(menu) {
	const rows = await menu.locator('button:not([role="switch"])').evaluateAll((buttons) =>
		buttons.map((button) => ({
			name: button.textContent.trim(),
			height: button.getBoundingClientRect().height
		}))
	);
	assert.ok(rows.length > 0, 'The menu has rows');
	for (const row of rows) {
		assert.ok(row.height >= 44, 'Ultra menu rows are 44px touch targets: ' + JSON.stringify(row));
	}
}

/** A finger swipe up on an element, which scrolls it as a phone would. */
async function swipeUp(page, locator) {
	const box = await locator.boundingBox();
	const x = box.x + box.width / 2;
	const startY = box.y + box.height * 0.8;
	const endY = box.y + box.height * 0.2;
	const protocol = await page.context().newCDPSession(page);
	await protocol.send('Input.dispatchTouchEvent', {
		type: 'touchStart',
		touchPoints: [{ x, y: startY, id: 1 }]
	});
	for (let step = 1; step <= 5; step += 1) {
		await protocol.send('Input.dispatchTouchEvent', {
			type: 'touchMove',
			touchPoints: [{ x, y: startY + ((endY - startY) * step) / 5, id: 1 }]
		});
		await page.waitForTimeout(25);
	}
	await protocol.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
	await protocol.detach();
	await page.waitForTimeout(300);
}

/** The open menu's box stays inside what the keyboard leaves visible, with its rows reachable. */
async function assertMenuInView(menu) {
	const metrics = await menu.locator('.buddy-ultra-menu').evaluate((element) => {
		const rect = element.getBoundingClientRect();
		const viewport = window.visualViewport;
		const firstRow = element.querySelector('button');
		let firstRowReceivesTap = false;
		if (firstRow) {
			const row = firstRow.getBoundingClientRect();
			const hit = document.elementFromPoint(row.x + row.width / 2, row.y + row.height / 2);
			firstRowReceivesTap = Boolean(hit && firstRow.contains(hit));
		}
		const composer = document.querySelector('.buddy-composer').getBoundingClientRect();
		return {
			top: rect.top,
			bottom: rect.bottom,
			left: rect.left,
			right: rect.right,
			composerLeft: composer.left,
			composerRight: composer.right,
			viewportTop: viewport.offsetTop,
			viewportHeight: viewport.height,
			viewportWidth: viewport.width,
			firstRowReceivesTap
		};
	});
	assert.ok(metrics.top >= metrics.viewportTop - 1, 'The menu starts inside the visible area');
	assert.ok(
		metrics.bottom <= metrics.viewportTop + metrics.viewportHeight + 1,
		'The menu ends above the keyboard: ' + JSON.stringify(metrics)
	);
	assert.ok(
		metrics.left >= 0 && metrics.right <= metrics.viewportWidth + 1,
		'The menu fits across'
	);
	assert.ok(
		Math.abs(metrics.left - metrics.composerLeft) <= 1 &&
			Math.abs(metrics.right - metrics.composerRight) <= 1,
		'The phone menu spans the composer, as its Model and Thinking pickers do: ' +
			JSON.stringify(metrics)
	);
	assert.ok(metrics.firstRowReceivesTap, 'The menu’s first row takes taps');
}

/** Waits until the open menu stops moving: the dropdown places it again a frame after it changes. */
async function waitForMenuToSettle(menu) {
	await menu.evaluate(async (element) => {
		let last = '';
		let steadyFrames = 0;
		while (steadyFrames < 2) {
			await new Promise((resolve) => requestAnimationFrame(resolve));
			const rect = element.getBoundingClientRect();
			const now = [rect.top, rect.bottom, rect.left, rect.right].join();
			if (now === last) {
				steadyFrames += 1;
			} else {
				steadyFrames = 0;
			}
			last = now;
		}
	});
}

/**
 * The whole phone menu shows at once, its last line included, and ends above the
 * composer: it may rise over the chat's header for the room.
 */
async function assertMenuShowsWhole(menu) {
	await waitForMenuToSettle(menu);
	const metrics = await menu.locator('.buddy-ultra-menu').evaluate((element) => {
		const composer = document.querySelector('.buddy-composer').getBoundingClientRect();
		return {
			scrolls: element.scrollHeight > element.clientHeight + 1,
			bottom: element.getBoundingClientRect().bottom,
			composerTop: composer.top
		};
	});
	assert.equal(metrics.scrolls, false, 'The phone menu shows whole, without scrolling');
	assert.ok(
		metrics.bottom <= metrics.composerTop,
		'The phone menu ends above the composer: ' + JSON.stringify(metrics)
	);
}

/**
 * Rows and lines of the scrolling menu that its bottom edge cuts through. Each one
 * should show whole or not at all.
 */
async function rowsCutAtBottom(scroller) {
	return scroller.evaluate((element) => {
		const bottom = element.getBoundingClientRect().top + element.clientTop + element.clientHeight;
		const lines = Array.from(element.querySelectorAll('button:not([role="switch"]), p'));
		return lines
			.filter((line) => {
				const rect = line.getBoundingClientRect();
				return rect.top < bottom - 1 && rect.bottom > bottom + 1;
			})
			.map((line) => line.textContent.trim());
	});
}

/**
 * Opens the composer as a person would, by its draft. Closing the model picker from its
 * own button leaves an empty desktop composer at rest.
 */
async function openComposer(page, profile) {
	await press(profile, page.locator('#chat-input'));
	await waitUntil(
		async () => (await page.locator('.buddy-composer').getAttribute('data-expanded')) === 'true',
		'The composer opens'
	);
}

async function openModelPicker(page, profile) {
	const picker = page.locator(composerModelTrigger);
	await openComposer(page, profile);
	await press(profile, picker);
	await waitUntil(
		async () => (await picker.getAttribute('aria-expanded')) === 'true',
		'The model picker opens'
	);
}

async function closeModelPicker(page, profile) {
	const picker = page.locator(composerModelTrigger);
	await press(profile, picker);
	await waitUntil(
		async () => (await picker.getAttribute('aria-expanded')) === 'false',
		'The model picker closes'
	);
}

async function chooseModel(page, profile, modelName) {
	await openModelPicker(page, profile);
	await press(
		profile,
		page.getByRole('option', { name: 'Select ' + modelName + ' model', exact: true })
	);
	await waitUntil(
		async () =>
			(await page.locator(composerModelTrigger).getAttribute('aria-expanded')) === 'false',
		'Choosing a model closes the picker'
	);
}

/** Compare adds a second model to the chat, or goes back to the first one alone. */
async function toggleCompare(page, profile, addedModelName = null) {
	await openModelPicker(page, profile);
	await press(profile, page.getByRole('button', { name: 'Compare', exact: true }));
	if (addedModelName) {
		await press(
			profile,
			page.getByRole('option', { name: 'Select ' + addedModelName + ' model', exact: true })
		);
	}
	await closeModelPicker(page, profile);
}

async function expectUltraUnavailable(page, profile, reason) {
	const trigger = page.locator(ultraTrigger);
	await openComposer(page, profile);
	await waitUntil(
		async () => (await trigger.getAttribute('aria-label')) === 'Ultra: off',
		'Ultra shows off while it is unavailable'
	);
	await press(profile, trigger);
	const menu = ultraMenu(page);
	await waitForMenu(menu);
	const toggle = menu.getByRole('button', { name: /^Ultra/ });
	assert.equal(await toggle.isDisabled(), true, 'The switch is disabled: ' + reason);
	assert.equal(await toggle.getAttribute('aria-pressed'), 'false');
	assert.equal(await toggle.getAttribute('aria-describedby'), 'ultra-menu-unavailable');
	assert.equal((await menu.locator('#ultra-menu-unavailable').innerText()).trim(), reason);
	assert.equal(
		await menu.getByText('Worker model').count(),
		0,
		'No worker choices while Ultra is unavailable'
	);
	await closeUltraMenu(page, profile);
}

/** Sends a socket event for the chat's reply, as the server does, to the page's own handlers. */
async function sendChatEvent(page, messageId, type, data) {
	const event = { chat_id: 'buddy-design-chat', message_id: messageId, data: { type, data } };
	await page.evaluate(
		async ({ stores, event }) => {
			const module = await import(stores.url);
			let socket = null;
			const unsubscribe = module[stores.aliases.socket].subscribe((value) => {
				socket = value;
			});
			unsubscribe();
			if (!socket) throw new Error('The page has no socket to hear the event');
			for (const listener of socket.listeners('events')) {
				await listener(event, () => {});
			}
		},
		{ stores, event }
	);
}

/**
 * A chat:message:workers snapshot: a finished reader, a fixer and the test-writer it
 * started. All three run on the chosen worker model, at Medium.
 */
function ultraWorkersSnapshot(startedAt, states) {
	function ended(state, at) {
		if (state === 'running') return null;
		return startedAt + at;
	}
	const items = [
		{
			id: 'task-read',
			parent_id: null,
			depth: 1,
			label: 'Read the parser',
			state: 'done',
			model: ultraWorkerModel.name,
			effort: 'medium',
			activity: null,
			summary: 'The parser drops the last line.\nSee parse.ts:42.',
			tokens: 1800,
			started_at: startedAt,
			ended_at: startedAt + 40
		},
		{
			id: 'task-fix',
			parent_id: null,
			depth: 1,
			label: 'Fix the parser',
			state: states.fix,
			model: ultraWorkerModel.name,
			effort: 'medium',
			activity: 'Editing parse.ts',
			summary: states.fix === 'done' ? 'Kept the last line.' : null,
			tokens: 900,
			started_at: startedAt + 41,
			ended_at: ended(states.fix, 75)
		},
		{
			id: 'task-test',
			parent_id: 'task-fix',
			depth: 2,
			label: 'Write a failing test',
			state: states.test,
			model: ultraWorkerModel.name,
			effort: 'medium',
			activity: 'Running npm test',
			summary: null,
			tokens: 300,
			started_at: startedAt + 50,
			ended_at: ended(states.test, 60)
		}
	];
	return { items, running: items.filter((item) => item.state === 'running').length };
}

/** The chat's saved Ultra choice, from its last save that carried one. */
function lastUltraSave(fixtures) {
	const saves = fixtures.requests.filter(
		(request) =>
			request.method === 'POST' &&
			request.path === '/api/v1/chats/buddy-design-chat' &&
			request.body?.chat?.ultra
	);
	return saves.at(-1)?.body.chat.ultra ?? null;
}

const ultraDraft = 'Please fix the parser bug';
const sameAsChatModel = 'Same as chat (' + ultraChatModel.name + ')';
const workerModelNames = [sameAsChatModel, ultraChatModel.name, ultraWorkerModel.name];

/**
 * The resting composer hides its controls; the open one reads [Model][Thinking][Ultra].
 * Returns the resting composer's height, which Ultra must not change.
 */
async function checkUltraPlacement(page, profile) {
	const composer = page.locator('.buddy-composer');
	const input = page.locator('#chat-input');
	const trigger = page.locator(ultraTrigger);
	await waitUntil(
		async () => (await composer.getAttribute('data-expanded')) === 'false',
		'The composer starts at rest'
	);
	assert.equal(await trigger.isVisible(), false, 'The resting composer hides the Ultra button');
	assert.equal(await input.getAttribute('aria-label'), 'Message Buddy…');
	const restingHeight = await composer.evaluate((element) => element.offsetHeight);
	await press(profile, input);
	await trigger.waitFor();
	assertComposerOrder(await composerControlOrder(page), profile);
	assert.equal(await trigger.getAttribute('aria-label'), 'Ultra: off');
	return restingHeight;
}

/** Ultra needs exactly one plan model that can run workers. */
async function checkUltraUnavailable(page, profile) {
	await toggleCompare(page, profile, ultraWorkerModel.name);
	await expectUltraUnavailable(page, profile, 'Ultra works with one model at a time.');
	await toggleCompare(page, profile);
	await chooseModel(page, profile, baseModel.name);
	await expectUltraUnavailable(page, profile, 'Ultra works with Claude and ChatGPT plan models.');
	await chooseModel(page, profile, ultraChatModel.name);
}

/** Phones show each list in place, with Back, and keep the keyboard throughout. */
async function chooseWorkersOnPhone(page, menu) {
	const phone = { mobile: true };
	const modelRow = menu.locator('button[data-opens="model"]');
	const effortRow = menu.locator('button[data-opens="effort"]');
	assert.ok((await modelRow.innerText()).includes(sameAsChatModel));
	assert.deepEqual(await truncatedTexts(menu), [], 'The menu shows the worker model’s whole name');
	await press(phone, modelRow);
	const modelList = menu.getByRole('group', { name: 'Worker model', exact: true });
	await modelList.waitFor();
	assert.deepEqual(await radioNames(modelList), workerModelNames);
	const sameAsChat = modelList.getByRole('menuitemradio', { name: sameAsChatModel, exact: true });
	assert.equal(await sameAsChat.getAttribute('aria-checked'), 'true');
	assert.deepEqual(
		(await sameAsChat.innerText()).split('\n').map((line) => line.trim()),
		['Same as chat', ultraChatModel.name],
		'“Same as chat” names the chat’s model on a line of its own'
	);
	assert.deepEqual(await truncatedTexts(menu), [], 'The list shows each model’s whole name');
	assert.equal(await effortRow.count(), 0, 'The sub-view shows only the worker models');
	await assertTouchRows(menu);
	await assertMenuInView(menu);
	assert.equal(await draftHasFocus(page), true, 'The sub-view keeps the keyboard');
	await captureScreenshot(page, 'phone-ultra-worker-model');

	// Back changes nothing; choosing a model goes back to the menu, which names it.
	await press(phone, menu.getByRole('button', { name: /^Back/ }));
	await modelRow.waitFor();
	await press(phone, modelRow);
	const haiku = modelList.getByRole('menuitemradio', { name: ultraWorkerModel.name, exact: true });
	await press(phone, haiku);
	await waitUntil(
		async () => (await modelRow.innerText()).includes(ultraWorkerModel.name),
		'Choosing a worker model goes back to the menu, which names it'
	);
	assert.deepEqual(await truncatedTexts(menu), [], 'The menu shows the chosen model’s whole name');

	await press(phone, effortRow);
	const effortList = menu.getByRole('group', { name: 'Worker thinking', exact: true });
	await effortList.waitFor();
	assert.deepEqual(
		await radioNames(effortList),
		['Same as chat', 'Low', 'Medium'],
		"The thinking levels are the worker model's"
	);
	await press(phone, effortList.getByRole('menuitemradio', { name: 'Medium', exact: true }));
	await waitUntil(
		async () => (await effortRow.innerText()).includes('Medium'),
		'Choosing a thinking level goes back to the menu, which names it'
	);
	assert.equal(await draftHasFocus(page), true, 'Choosing keeps the keyboard');
}

/** Desktop shows both lists at once, and choosing keeps the menu open. */
async function chooseWorkersOnDesktop(menu) {
	const desktop = { mobile: false };
	const modelList = menu.getByRole('group', { name: 'Worker model', exact: true });
	const effortList = menu.getByRole('group', { name: 'Worker thinking', exact: true });
	assert.deepEqual(await radioNames(modelList), workerModelNames);
	assert.deepEqual(await radioNames(effortList), ['Same as chat', 'Low', 'Medium', 'High']);
	const haiku = modelList.getByRole('menuitemradio', { name: ultraWorkerModel.name, exact: true });
	await press(desktop, haiku);
	await waitUntil(
		async () => (await haiku.getAttribute('aria-checked')) === 'true',
		'The worker model is chosen'
	);
	await waitUntil(
		async () => (await radioNames(effortList)).join('|') === 'Same as chat|Low|Medium',
		"The thinking levels follow the worker model's"
	);
	const medium = effortList.getByRole('menuitemradio', { name: 'Medium', exact: true });
	await press(desktop, medium);
	await waitUntil(
		async () => (await medium.getAttribute('aria-checked')) === 'true',
		'The worker thinking level is chosen'
	);
	assert.equal(await menu.isVisible(), true, 'Choosing leaves the menu open');
}

/** The menu: the switch, the workers' model and thinking, and what the workers may do. */
async function checkUltraMenu(page, profile) {
	const input = page.locator('#chat-input');
	const trigger = page.locator(ultraTrigger);
	const menu = ultraMenu(page);
	await press(profile, input);
	await input.fill(ultraDraft);
	if (profile.mobile) await setVisibleHeight(page, 464);
	await press(profile, trigger);
	await waitForMenu(menu);
	if (profile.mobile) {
		assert.equal(await draftHasFocus(page), true, 'Opening Ultra keeps the draft and keyboard');
		await assertMenuInView(menu);
		await assertTouchRows(menu);
	}

	const toggle = menu.getByRole('button', { name: /^Ultra/ });
	assert.equal(await toggle.getAttribute('aria-pressed'), 'false', 'A chat starts with Ultra off');
	await press(profile, toggle);
	await waitUntil(
		async () => (await toggle.getAttribute('aria-pressed')) === 'true',
		'The switch turns Ultra on'
	);
	assert.equal(await menu.isVisible(), true, 'The switch leaves the menu open');
	assert.equal(await trigger.getAttribute('data-on'), 'true');
	const onContrast = await textContrast(trigger);
	assert.ok(onContrast >= 4.5, 'The tinted Ultra chip keeps its label readable: ' + onContrast);

	if (profile.mobile) {
		await chooseWorkersOnPhone(page, menu);
	} else {
		await chooseWorkersOnDesktop(menu);
	}

	await menu.getByText('Workers can only think.', { exact: true }).waitFor();
	await menu.getByText('No worker limit — Ultra uses your plan faster', { exact: true }).waitFor();
	const codexNote = menu.getByText("Codex workers share the chat's access.");
	assert.equal(await codexNote.count(), 0, 'Claude workers need no Codex note');
	const nextMessageNote = menu.getByText('Changes apply to your next message');
	assert.equal(await nextMessageNote.count(), 0, 'Nothing is generating yet');
	assert.equal(
		await trigger.getAttribute('aria-label'),
		'Ultra: on. Workers use Claude Synthetic Haiku. Thinking: Medium.'
	);
	if (profile.mobile) {
		await assertMenuInView(menu);
		await assertMenuShowsWhole(menu);
		await assertTouchRows(menu);
		assert.equal(await draftHasFocus(page), true, 'The open menu keeps the keyboard');
		await captureScreenshot(page, 'phone-ultra-menu');
	} else {
		await captureScreenshot(page, 'desktop-ultra-menu');
	}

	await closeUltraMenu(page, profile);
	assert.equal((await input.innerText()).trim(), ultraDraft, 'The draft survives the menu');
	if (profile.mobile) {
		assert.equal(await draftHasFocus(page), true, 'Closing the menu keeps the keyboard');
		await assertSettingsChipsTakeTaps(page);
		await assertIconOnlyUltraChip(page);
		await captureScreenshot(page, 'phone-ultra-settings-row');
	} else {
		await captureScreenshot(page, 'desktop-ultra-composer');
	}
}

/** The chat saves the choice, and the worker choices become the account's default. */
async function checkUltraSaves(session, fixtures) {
	await waitUntil(
		() => lastUltraSave(fixtures)?.worker_effort === 'medium',
		'The chat saves its Ultra choice'
	);
	assert.deepEqual(lastUltraSave(fixtures), {
		on: true,
		worker_model: ultraWorkerModel.id,
		worker_effort: 'medium'
	});
	function savesWorkerChoices(entry) {
		return (
			entry.path === '/api/v1/users/user/settings/update' &&
			entry.body?.ui?.ultra?.worker_effort === 'medium'
		);
	}
	await waitUntil(
		() => session.mutations.some(savesWorkerChoices),
		'The worker choices are saved as the account default'
	);
}

/** At rest with Ultra on, the composer says so without growing. */
async function checkRestingUltraComposer(page, profile, restingHeight) {
	const composer = page.locator('.buddy-composer');
	const input = page.locator('#chat-input');
	await input.fill('');
	await press(profile, page.getByText('A plain answer for optional panel tests.', { exact: true }));
	if (profile.mobile) await setVisibleHeight(page, null);
	await waitUntil(
		async () => (await composer.getAttribute('data-expanded')) === 'false',
		'The empty composer goes back to rest'
	);
	assert.equal(await composer.getAttribute('data-ultra'), 'true');
	await waitUntil(
		async () => (await input.getAttribute('aria-label')) === 'Message Buddy · Ultra',
		'The resting placeholder says Ultra is on'
	);
	const height = await composer.evaluate((element) => element.offsetHeight);
	assert.equal(height, restingHeight, 'Ultra leaves the resting composer’s height alone');
}

/**
 * A message sent with Ultra on carries exactly the workers' choices. Returns its reply's
 * id. While the reply is written, the menu says changes wait for the next message.
 */
async function sendUltraMessage(session, profile) {
	const { page } = session;
	const input = page.locator('#chat-input');
	const menu = ultraMenu(page);
	await press(profile, input);
	await input.fill(ultraDraft);
	await press(profile, page.locator('#send-message-button'));
	await waitUntil(() => session.completions.length === 1, 'The Ultra message is sent');
	const ultraTurn = session.completions[0];
	assert.equal(ultraTurn.model, ultraChatModel.id);
	assert.deepEqual(ultraTurn.ultra, {
		worker_model: ultraWorkerModel.id,
		worker_effort: 'medium'
	});
	assert.equal(typeof ultraTurn.id, 'string', 'The request names its reply');

	await press(profile, page.locator(ultraTrigger));
	await waitForMenu(menu);
	await menu.getByText('Changes apply to your next message', { exact: true }).waitFor();
	await closeUltraMenu(page, profile);
	return ultraTurn.id;
}

/** Phone rows are 44px and open a bottom sheet, even with the keyboard still up. */
async function stopWorkerOnPhone(page, card, fixtures) {
	const touchTargets = await card.locator('button').evaluateAll((buttons) =>
		buttons.map((button) => ({
			name: button.getAttribute('aria-label') || button.textContent.trim(),
			height: button.getBoundingClientRect().height
		}))
	);
	for (const target of touchTargets) {
		assert.ok(target.height >= 44, 'Workers card rows are 44px: ' + JSON.stringify(target));
	}
	await card.scrollIntoViewIfNeeded();
	await captureScreenshot(page, 'phone-ultra-workers');
	// The keyboard is still up from sending; the first tap opens the row.
	assert.equal(await draftHasFocus(page), true, 'The draft still has the keyboard');
	const row = card
		.locator('button[aria-haspopup="dialog"]')
		.filter({ hasText: 'Write a failing test' });
	await row.tap();
	const sheet = page.getByRole('dialog', { name: 'Write a failing test', exact: true });
	await sheet.waitFor();
	await sheet.getByText('Running npm test', { exact: true }).waitFor();
	await sheet.getByText('Synthetic Haiku · medium', { exact: true }).waitFor();
	const sheetStop = sheet.getByRole('button', { name: 'Stop worker', exact: true });
	const stopBox = await sheetStop.boundingBox();
	assert.ok(stopBox.height >= 44, "The sheet's Stop is a full touch target");
	await captureScreenshot(page, 'phone-ultra-workers-sheet');
	await sheetStop.tap();
	await waitUntil(() => fixtures.workerStops.length === 1, 'Stop asks the server to stop it');
	await sheet.getByRole('button', { name: 'Stopping…', exact: true }).waitFor();
	await sheet.getByRole('button', { name: 'Done', exact: true }).tap();
	await sheet.waitFor({ state: 'hidden' });
}

/** A desktop row opens in place to show its worker's report; Stop sits on the row. */
async function stopWorkerOnDesktop(page, card, fixtures) {
	const rows = card.getByRole('list', { name: 'Workers', exact: true });
	const readRow = rows.getByRole('button', { name: /Read the parser/ });
	assert.equal(await readRow.getAttribute('aria-expanded'), 'false');
	await readRow.click();
	const report = card.getByText(/See parse\.ts:42\./);
	await report.waitFor();
	assert.equal(await readRow.getAttribute('aria-expanded'), 'true');
	// The report lines up under the worker's name, on every line.
	assert.equal(await report.evaluate((element) => getComputedStyle(element).display), 'block');
	const reportBox = await report.boundingBox();
	const readLabel = await rows.getByText('Read the parser', { exact: true }).boundingBox();
	assert.ok(Math.abs(reportBox.x - readLabel.x) <= 2, 'The report is indented as one block');
	await card.getByRole('button', { name: 'Stop Write a failing test', exact: true }).click();
	await waitUntil(() => fixtures.workerStops.length === 1, 'Stop asks the server to stop it');
	await captureScreenshot(page, 'desktop-ultra-workers');
}

/** The workers card follows the reply's snapshots, stops one worker, and ends. */
async function checkWorkersCard(page, profile, fixtures, replyId) {
	const startedAt = Math.floor(Date.now() / 1000) - 75;
	const running = ultraWorkersSnapshot(startedAt, { fix: 'running', test: 'running' });
	await sendChatEvent(page, replyId, 'chat:message:workers', running);
	const card = page.locator('.buddy-ultra-workers');
	await card.waitFor();
	const header = card.locator('button[aria-expanded]').first();
	await waitUntil(
		async () => (await header.innerText()).includes('Ultra · 3 workers · 2 running'),
		'The card counts its workers and the running ones'
	);
	assert.equal(
		await header.getAttribute('aria-expanded'),
		'true',
		'The card is open while they run'
	);

	const rows = card.getByRole('list', { name: 'Workers', exact: true });
	assert.equal(await rows.locator(':scope > li').count(), 2, "One row per orchestrator's worker");
	const subWorkers = card.getByRole('list', { name: 'Started by Fix the parser', exact: true });
	assert.equal(await subWorkers.locator(':scope > li').count(), 1);
	const parentLabel = await rows.getByText('Fix the parser', { exact: true }).boundingBox();
	const subLabel = await subWorkers
		.getByText('Write a failing test', { exact: true })
		.boundingBox();
	assert.ok(subLabel.x >= parentLabel.x + 12, 'The sub-worker is indented under its parent');
	const pills = card.getByText('Synthetic Haiku · medium', { exact: true });
	if (profile.mobile) {
		assert.equal(await pills.count(), 1, 'Phone rows leave the pill they share to the header');
		assert.ok(
			(await header.innerText()).includes('Synthetic Haiku · medium'),
			'The header names the model and thinking every worker shares'
		);
		const statusLine = await rows
			.getByText('Editing parse.ts', { exact: true })
			.evaluate((line) => line.scrollWidth <= line.clientWidth + 1);
		assert.ok(statusLine, 'A phone row’s status line has room to show whole');
	} else {
		assert.equal(await pills.count(), 3, 'Each row has its model · effort pill');
	}
	const stopFix = card.getByRole('button', { name: 'Stop Fix the parser', exact: true });
	const stopTest = card.getByRole('button', { name: 'Stop Write a failing test', exact: true });
	const stopRead = card.getByRole('button', { name: 'Stop Read the parser', exact: true });
	assert.equal(await stopFix.count(), 1, 'A running worker has Stop');
	assert.equal(await stopTest.count(), 1, 'A running sub-worker has Stop');
	assert.equal(await stopRead.count(), 0, 'A finished worker has no Stop');

	if (profile.mobile) {
		await stopWorkerOnPhone(page, card, fixtures);
	} else {
		await stopWorkerOnDesktop(page, card, fixtures);
	}
	assert.deepEqual(fixtures.workerStops, [{ chatId: 'buddy-design-chat', workerId: 'task-test' }]);
	const stopPath = '/api/v1/subscriptions/chats/buddy-design-chat/workers/task-test/stop';
	assert.ok(
		fixtures.requests.some((request) => request.method === 'POST' && request.path === stopPath),
		'Stop posts to the worker’s stop route'
	);
	const stopping = card.getByRole('button', {
		name: 'Stopping Write a failing test…',
		exact: true
	});
	await stopping.waitFor();
	assert.equal(await stopping.isDisabled(), true, 'A worker being stopped can’t be stopped twice');

	// The reply ends while workers still run, then they end one by one.
	await sendChatEvent(page, replyId, 'chat:completion', {
		content: 'Two workers are still checking the fix.',
		done: true
	});
	await waitUntil(
		async () => (await header.innerText()).includes('Ultra · 3 workers · 2 still running'),
		'After the reply the card says how many still run'
	);
	assert.equal(await header.getAttribute('aria-expanded'), 'true', 'The card stays open');
	const oneStopped = ultraWorkersSnapshot(startedAt, { fix: 'running', test: 'stopped' });
	await sendChatEvent(page, replyId, 'chat:message:workers', oneStopped);
	await waitUntil(
		async () => (await header.innerText()).includes('Ultra · 3 workers · 1 still running'),
		'The stopped worker no longer counts as running'
	);
	const allEnded = ultraWorkersSnapshot(startedAt, { fix: 'done', test: 'stopped' });
	await sendChatEvent(page, replyId, 'chat:message:workers', allEnded);
	await waitUntil(
		async () => (await header.innerText()).includes('Used 3 workers · 1m 15s'),
		'With every worker done the card says what the reply used'
	);
	assert.equal(await header.getAttribute('aria-expanded'), 'false', 'The card collapses');
	await sendChatEvent(page, replyId, 'chat:active', { active: false });
}

/** Turning Ultra off is saved, and the next message has no ultra field. */
async function checkUltraOffMessage(session, profile, fixtures) {
	const { page } = session;
	const input = page.locator('#chat-input');
	const trigger = page.locator(ultraTrigger);
	const menu = ultraMenu(page);
	await press(profile, input);
	await press(profile, trigger);
	await waitForMenu(menu);
	const toggle = menu.getByRole('button', { name: /^Ultra/ });
	await press(profile, toggle);
	await waitUntil(
		async () => (await toggle.getAttribute('aria-pressed')) === 'false',
		'The switch turns Ultra off'
	);
	await closeUltraMenu(page, profile);
	await waitUntil(
		() => lastUltraSave(fixtures)?.on === false,
		'Turning Ultra off is saved with the chat'
	);
	assert.deepEqual(lastUltraSave(fixtures), {
		on: false,
		worker_model: ultraWorkerModel.id,
		worker_effort: 'medium'
	});
	assert.equal(await trigger.getAttribute('aria-label'), 'Ultra: off');

	await input.fill('Thanks');
	await press(profile, page.locator('#send-message-button'));
	await waitUntil(() => session.completions.length === 2, 'The second message is sent');
	const plainTurn = session.completions[1];
	assert.equal('ultra' in plainTurn, false, 'With Ultra off there is no ultra field');
	await sendChatEvent(page, plainTurn.id, 'chat:completion', {
		content: 'You are welcome.',
		done: true
	});
	await sendChatEvent(page, plainTurn.id, 'chat:active', { active: false });
	// This case sends two messages on purpose; runCase still checks that nothing else was sent.
	session.completions.splice(0);
}

/** Whether the composer's settings, draft and Send all show above the keyboard. */
async function composerAboveKeyboard(page) {
	return page.evaluate(() => {
		const top = visualViewport.offsetTop;
		const bottom = top + visualViewport.height;
		function shows(selector) {
			const rect = document.querySelector(selector).getBoundingClientRect();
			return rect.height > 0 && rect.top >= top - 1 && rect.bottom <= bottom + 1;
		}
		return {
			settings: shows('.buddy-composer-settings'),
			draft: shows('#chat-input-container'),
			send: shows('#send-message-button')
		};
	});
}

async function chatHeaderHeight(page) {
	return page.locator('.buddy-chat-header').evaluate((header) => header.offsetHeight);
}

/**
 * A phone on its side with the keyboard up: the draft and Send fit, the menu fits
 * too, and with less room the rest of the menu is a swipe away. The draft keeps the
 * keyboard throughout.
 */
async function checkLandscapeUltraMenu(page) {
	const phone = { mobile: true };
	const input = page.locator('#chat-input');
	const menu = ultraMenu(page);
	await page.setViewportSize({ width: 844, height: 390 });
	await page.reload({ waitUntil: 'domcontentloaded' });
	await input.waitFor();
	await page.evaluate(() => document.fonts.ready);
	await page.waitForTimeout(400);
	await input.tap();
	await input.fill('Keep this landscape draft');
	await setVisibleHeight(page, 240);
	// The chat's header and the bar above the composer step aside for the keyboard.
	assert.deepEqual(
		await composerAboveKeyboard(page),
		{ settings: true, draft: true, send: true },
		'The draft, its settings and Send fit above the landscape keyboard'
	);
	assert.equal(await chatHeaderHeight(page), 0, 'The header steps aside while the keyboard is up');
	assert.equal(
		await page.locator(ultraTrigger).locator('.buddy-ultra-label').isVisible(),
		true,
		'A phone on its side has room for the Ultra chip’s label'
	);
	await captureScreenshot(page, 'phone-ultra-landscape-draft');

	await page.locator(ultraTrigger).tap();
	await waitForMenu(menu);
	await assertMenuInView(menu);
	await assertTouchRows(menu);
	assert.equal(await draftHasFocus(page), true, 'The landscape menu keeps the keyboard');
	const scroller = menu.locator('.buddy-ultra-menu');
	const fitsWhole = await scroller.evaluate(
		(element) => element.scrollHeight <= element.clientHeight + 1
	);
	assert.ok(fitsWhole, 'With the header aside, the whole menu fits above the keyboard');
	await captureScreenshot(page, 'phone-ultra-landscape-keyboard');

	// A taller keyboard leaves less room: the rest of the menu is a swipe away, never
	// behind the keyboard.
	await setVisibleHeight(page, 180);
	await assertMenuInView(menu);
	const scrollable = await scroller.evaluate(
		(element) => element.scrollHeight > element.clientHeight + 1
	);
	assert.ok(scrollable, 'A menu taller than the room scrolls');
	assert.deepEqual(await rowsCutAtBottom(scroller), [], 'The menu ends between rows');
	assert.equal(
		await scroller.getAttribute('data-more-below'),
		'true',
		'A shadow shows there is more below'
	);
	await captureScreenshot(page, 'phone-ultra-landscape-scroll');
	for (let swipe = 0; swipe < 3; swipe += 1) {
		await swipeUp(page, scroller);
	}
	const atEnd = await scroller.evaluate(
		(element) => element.scrollTop + element.clientHeight >= element.scrollHeight - 1
	);
	assert.ok(atEnd, 'Swipes scroll the menu to its end');
	assert.equal(
		await scroller.getAttribute('data-more-below'),
		'false',
		'At the end, no shadow below'
	);
	assert.equal(await scroller.getAttribute('data-more-above'), 'true', 'A shadow shows more above');
	const lastLine = menu.getByText('No worker limit — Ultra uses your plan faster', {
		exact: true
	});
	const lastLineBox = await lastLine.boundingBox();
	const scrollerBox = await scroller.boundingBox();
	const visibleBottom = await page.evaluate(() => visualViewport.offsetTop + visualViewport.height);
	assert.ok(
		lastLineBox.y + lastLineBox.height <= scrollerBox.y + scrollerBox.height,
		'Swiping shows the menu’s last line'
	);
	assert.ok(
		scrollerBox.y + scrollerBox.height <= visibleBottom,
		'The menu ends above the keyboard'
	);
	assert.equal(await draftHasFocus(page), true, 'Swiping the menu keeps the keyboard');
	await closeUltraMenu(page, phone);
	assert.equal((await input.innerText()).trim(), 'Keep this landscape draft');
	await setVisibleHeight(page, null);
	assert.ok((await chatHeaderHeight(page)) > 0, 'The header comes back with the keyboard down');
}

async function ultraCase(browser, profile) {
	await runCase(browser, profile, 'ultra', 'ultra', async (session, fixtures) => {
		const { page } = session;
		const restingHeight = await checkUltraPlacement(page, profile);
		await checkUltraUnavailable(page, profile);
		await checkUltraMenu(page, profile);
		await checkUltraSaves(session, fixtures);
		await checkRestingUltraComposer(page, profile, restingHeight);
		const replyId = await sendUltraMessage(session, profile);
		await checkWorkersCard(page, profile, fixtures, replyId);
		await checkUltraOffMessage(session, profile, fixtures);
		if (profile.mobile) await checkLandscapeUltraMenu(page);
	});
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
		assert.deepEqual(saved[0].body, {
			mode: 'auto',
			paths: {},
			machine_id: '',
			access: '',
			offered: {}
		});
		await captureScreenshot(page, profile.name + '-folder-chip');
	});
}

async function folderEditCase(browser, profile) {
	await runCase(browser, profile, 'folder-edit', 'folder-edit', async ({ page }, fixtures) => {
		const dialog = await openFolderSettings(page);
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
		await saveFolderSettings(dialog);
		await dialog.waitFor({ state: 'hidden' });
		const saved = fixtures.requests.filter((request) => request.method === 'PUT');
		assert.deepEqual(saved[0].body, {
			mode: 'folder',
			paths: { 'pc-1': 'C:\\code\\docs' },
			machine_id: '',
			access: '',
			offered: {}
		});
		await page
			.getByRole('button', { name: 'Project folder: Work PC · docs', exact: true })
			.waitFor();
	});
}

async function folderDialogCase(browser, profile) {
	await runCase(browser, profile, 'folder-dialog', 'folder-edit', async ({ page }) => {
		await page.locator('button[aria-label="Folder options"]').click();
		await page.getByRole('button', { name: 'Edit', exact: true }).click();
		const dialog = page.locator('.modal').filter({ hasText: 'Project folder' });
		await dialog.getByRole('heading', { name: 'Edit folder', exact: true }).waitFor();
		assert.equal(
			await dialog.locator('#folder-name').inputValue(),
			'Website',
			'The name is ready to edit'
		);

		// Both rows start closed; the Project folder row sums up the folder's defaults.
		const projectRow = dialog.getByRole('button', { name: /^Project folder/ });
		await waitUntil(
			async () =>
				(await projectRow.textContent()).includes('site · Automatic · Plan default (Full access)'),
			'The row sums up the project folder'
		);
		assert.equal(await dialog.getByText('Run on', { exact: true }).isVisible(), false);
		await captureScreenshot(page, profile.name + '-folder-dialog');

		const knowledgeRow = dialog.getByRole('button', { name: /^Knowledge/ });
		assert.match(await knowledgeRow.textContent(), /None yet/);
		const back = dialog.getByRole('button', { name: 'Back to folder', exact: true });
		if (profile.mobile) {
			// Phones open each row on its own screen; Back and Escape return to the folder.
			await knowledgeRow.click();
			const selectKnowledge = dialog.getByText('Select Knowledge', { exact: true });
			await selectKnowledge.waitFor();
			assert.equal(await activeElementId(page), 'folder-form-website-back', 'Back is focused');
			// The Knowledge controls are full touch targets on their own screen.
			const selectBox = await selectKnowledge.locator('..').boundingBox();
			assert.ok(selectBox && selectBox.height >= 44, 'Select Knowledge is a full touch target');
			const upload = dialog.getByRole('button', { name: 'Upload Files', exact: true });
			if (await upload.isVisible()) {
				const uploadBox = await upload.boundingBox();
				assert.ok(uploadBox && uploadBox.height >= 44, 'Upload is a full touch target');
			}
			await back.click();
			assert.equal(await activeElementId(page), 'folder-form-website-row-knowledge');
			await projectRow.click();
			await dialog.getByRole('button', { name: 'Change', exact: true }).click();
			await dialog.getByLabel('Folder path', { exact: true }).waitFor();
			await back.click();
			await projectRow.click();
			await dialog.getByText('Run on', { exact: true }).waitFor();
			assert.equal(
				await dialog.getByLabel('Folder path', { exact: true }).count(),
				0,
				'Back closed the picker too'
			);
			await page.keyboard.press('Escape');
			await dialog.locator('#folder-name').waitFor();
			assert.equal(await dialog.isVisible(), true, 'Escape leaves the screen, not the dialog');
			await captureScreenshot(page, profile.name + '-folder-dialog-main');
		} else {
			assert.equal(await projectRow.getAttribute('aria-expanded'), 'false');
			// Escape in an open list closes just the list.
			await projectRow.click();
			const runOn = dialog.locator('#project-run-on-website-trigger');
			await runOn.click();
			await dialog.locator('#project-run-on-website-list').waitFor();
			await page.keyboard.press('Escape');
			await dialog.locator('#project-run-on-website-list').waitFor({ state: 'detached' });
			assert.equal(await activeElementId(page), 'project-run-on-website-trigger');
			assert.equal(await dialog.isVisible(), true, 'Escape closed the list, not the dialog');
			await projectRow.click();
			await knowledgeRow.click();
			await dialog.getByText('Select Knowledge', { exact: true }).waitFor();
			await projectRow.click();
			await dialog.getByText('Run on', { exact: true }).waitFor();
			// Escape closes the folder picker before the dialog.
			await dialog.getByRole('button', { name: 'Change', exact: true }).click();
			await dialog.getByLabel('Folder path', { exact: true }).waitFor();
			await page.keyboard.press('Escape');
			await dialog.getByText('Run on', { exact: true }).waitFor();
			assert.equal(await dialog.isVisible(), true, 'Escape closes the picker, not the dialog');
		}
	});
}

/** Saves the folder dialog; on phones Save is on the folder's own screen. */
async function saveFolderSettings(dialog, label = 'Save') {
	const back = dialog.getByRole('button', { name: 'Back to folder', exact: true });
	if (await back.isVisible()) await back.click();
	await dialog.getByRole('button', { name: label, exact: true }).click();
}

async function folderCreateTopCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-create-top',
		'folder-create',
		async ({ page }, fixtures) => {
			const newFolder = page.getByRole('button', { name: 'New Folder', exact: true });
			if (!(await newFolder.isVisible())) {
				await page.locator('#sidebar-toggle-button').click();
				await newFolder.waitFor();
			}
			await newFolder.click();
			const dialog = page.locator('.modal').filter({ has: page.locator('#folder-form-new') });
			await dialog.getByRole('heading', { name: 'New folder', exact: true }).waitFor();
			// Everything Edit folder has is there from the start, with the rows collapsed.
			const projectRow = dialog.locator('#folder-form-new-row-project');
			await dialog.getByRole('textbox', { name: 'Instructions', exact: true }).waitFor();
			await dialog.locator('#folder-form-new-row-knowledge').waitFor();
			await projectRow.waitFor();
			assert.match(await projectRow.textContent(), /Not set/);
			assert.equal(await dialog.getByText('Run on', { exact: true }).isVisible(), false);
			assert.equal(await dialog.getByText('Select Knowledge', { exact: true }).isVisible(), false);
			await captureScreenshot(page, profile.name + '-folder-create-top');

			// A folder that can't be made keeps the dialog, and what was typed, open.
			await dialog.getByRole('button', { name: 'Create', exact: true }).click();
			await page.getByText('Folder name cannot be empty.', { exact: true }).waitFor();
			assert.equal(await dialog.isVisible(), true, 'The dialog stays open');

			await dialog.locator('#folder-name').fill('Notes');
			await projectRow.click();
			if (!profile.mobile) {
				// A click lands with a list open, though closing the list re-centers the dialog.
				await dialog.locator('#project-run-on-new-trigger').click();
				await dialog.locator('#project-run-on-new-list').waitFor();
				await projectRow.click();
				assert.equal(
					await projectRow.getAttribute('aria-expanded'),
					'false',
					'The click reached the row'
				);
				await projectRow.click();
			}
			await pickChoice(dialog, 'project-run-on-new', /^Work PC/);
			await saveFolderSettings(dialog, 'Create');
			await dialog.waitFor({ state: 'hidden' });

			const created = fixtures.requests.filter(
				(entry) => entry.method === 'POST' && entry.path === '/api/v1/folders'
			);
			assert.equal(created.length, 1);
			assert.equal(created[0].body.name, 'Notes');
			assert.equal(created[0].body.parent_id, null);
			const reads = fixtures.requests.filter(
				(entry) => entry.path === '/api/v1/subscriptions/new-folder/project'
			);
			assert.deepEqual(
				reads.map((entry) => entry.parentId),
				[null],
				'A top-level folder has no parent to follow'
			);
			const saved = fixtures.requests.filter(
				(entry) =>
					entry.method === 'PUT' &&
					entry.path === '/api/v1/subscriptions/folders/new-folder/project'
			);
			assert.equal(saved.length, 1, 'The new folder’s project folder is saved once it exists');
			assert.equal(saved[0].body.machine_id, 'pc-1');
		}
	);
}

async function folderCreateCase(browser, profile) {
	await runCase(browser, profile, 'folder-create', 'folder-create', async ({ page }, fixtures) => {
		await page.locator('button[aria-label="Folder options"]').click();
		await page.getByRole('button', { name: 'Create Folder', exact: true }).click();
		// Found by its form: on a phone the heading changes with each screen.
		const dialog = page.locator('.modal').filter({ has: page.locator('#folder-form-new') });
		await dialog.getByRole('heading', { name: 'New folder', exact: true }).waitFor();
		await dialog.locator('#folder-name').fill('Docs');

		// The rows start collapsed; the new folder follows Website until this person chooses.
		const projectRow = dialog.locator('#folder-form-new-row-project');
		await projectRow.waitFor();
		assert.match(await projectRow.textContent(), /Same as “Website”/);
		assert.equal(await dialog.getByText('Run on', { exact: true }).isVisible(), false);
		await captureScreenshot(page, profile.name + '-folder-create');

		await dialog.getByRole('textbox', { name: 'Instructions', exact: true }).fill('Be brief.');

		// The new folder starts from Website's choice; this person picks their own.
		await projectRow.click();
		if (profile.mobile) {
			const inherited = dialog.getByRole('radio', { name: /^Same as “Website”/ });
			await inherited.waitFor();
			assert.equal(await inherited.getAttribute('aria-checked'), 'true');
		} else {
			await dialog
				.locator('#project-run-on-new-trigger')
				.getByText('Same as “Website”', { exact: true })
				.waitFor();
		}
		await pickChoice(dialog, 'project-run-on-new', /^Work PC/);
		await pickChoice(dialog, 'project-access-new', /^Read files/);
		assert.equal(fixtures.requests.filter((entry) => entry.method === 'POST').length, 0);

		await saveFolderSettings(dialog, 'Create');
		await dialog.waitFor({ state: 'hidden' });
		const created = fixtures.requests.filter(
			(entry) => entry.method === 'POST' && entry.path === '/api/v1/folders'
		);
		assert.equal(created.length, 1);
		assert.equal(created[0].body.name, 'Docs');
		assert.equal(created[0].body.parent_id, 'website');
		assert.equal(created[0].body.data.system_prompt, 'Be brief.');
		const reads = fixtures.requests.filter(
			(entry) => entry.path === '/api/v1/subscriptions/new-folder/project'
		);
		assert.deepEqual(
			reads.map((entry) => entry.parentId),
			['website'],
			'Asks once what a folder under Website starts with'
		);
		const saved = fixtures.requests.filter(
			(entry) =>
				entry.method === 'PUT' && entry.path === '/api/v1/subscriptions/folders/new-folder/project'
		);
		assert.deepEqual(saved[0].body, {
			mode: 'auto',
			paths: {},
			machine_id: 'pc-1',
			access: 'read',
			offered: {}
		});
	});
}

async function folderCreateSharedCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-create-shared',
		'folder-create-shared',
		async ({ page }, fixtures) => {
			await page.locator('button[aria-label="Folder options"]').click();
			await page.getByRole('button', { name: 'Create Folder', exact: true }).click();
			const dialog = page.locator('.modal').filter({ has: page.locator('#folder-form-new') });
			await dialog.getByRole('heading', { name: 'New folder', exact: true }).waitFor();
			await dialog.locator('#folder-name').fill('Shared notes');
			const back = dialog.getByRole('button', { name: 'Back to folder', exact: true });
			const projectRow = dialog.locator('#folder-form-new-row-project');
			const notice = dialog.getByText(/^Someone else can change this folder’s instructions/);
			const followOption = dialog
				.locator('#project-run-on-new-list')
				.getByRole('radio', { name: /^Same as “Website”/ });

			async function openRunOnList() {
				const trigger = dialog.locator('#project-run-on-new-trigger');
				if (
					(await trigger.count()) > 0 &&
					(await trigger.getAttribute('aria-expanded')) !== 'true'
				) {
					await trigger.click();
				}
				await dialog.locator('#project-run-on-new-list').waitFor();
			}

			async function openProjectSettings() {
				if (await projectRow.isVisible()) await projectRow.click();
				await dialog.getByText('Run on', { exact: true }).waitFor();
			}

			async function leaveProjectSettings() {
				if (profile.mobile) {
					await back.click();
					return;
				}
				// The list is open: closing it shrinks the centered dialog, after the click lands.
				await projectRow.click();
				assert.equal(
					await projectRow.getAttribute('aria-expanded'),
					'false',
					'A click on the row with a list open reaches the row'
				);
			}

			// With no instructions yet, the folder can follow Website. The row shows once
			// the project folder has loaded.
			await projectRow.waitFor();
			await openProjectSettings();
			await openRunOnList();
			assert.equal(await followOption.count(), 1);
			assert.equal(await notice.count(), 0);
			await leaveProjectSettings();

			// Instructions someone else can change: this person chooses where chats run.
			const instructions = dialog.getByRole('textbox', { name: 'Instructions', exact: true });
			await instructions.fill('Be brief.');
			await openProjectSettings();
			await notice.waitFor();
			await openRunOnList();
			assert.equal(await followOption.count(), 0, 'Following Website is not offered');
			assert.equal(
				await dialog.locator('#project-run-on-new-list [aria-checked="true"]').count(),
				0,
				'Nothing is chosen yet'
			);
			assert.match(await projectRow.textContent(), /Waiting for your choice/);
			await captureScreenshot(page, profile.name + '-folder-create-shared');
			await leaveProjectSettings();

			// Without them, following Website is back.
			await instructions.fill('');
			await openProjectSettings();
			await openRunOnList();
			await followOption.waitFor();
			assert.equal(await notice.count(), 0);
			await leaveProjectSettings();

			// Made with the instructions and no choice: nothing is saved for the folder yet.
			await instructions.fill('Be brief.');
			await saveFolderSettings(dialog, 'Create');
			await dialog.waitFor({ state: 'hidden' });
			const created = fixtures.requests.filter(
				(entry) => entry.method === 'POST' && entry.path === '/api/v1/folders'
			);
			assert.equal(created.length, 1);
			assert.equal(created[0].body.data.system_prompt, 'Be brief.');
			const saved = fixtures.requests.filter(
				(entry) =>
					entry.method === 'PUT' &&
					entry.path === '/api/v1/subscriptions/folders/new-folder/project'
			);
			assert.equal(saved.length, 0);
		}
	);
}

/** Opens the folder's Edit folder dialog with its Project folder row open. */
async function openFolderSettings(page) {
	await page.locator('button[aria-label="Folder options"]').click();
	await page.getByRole('button', { name: 'Edit', exact: true }).click();
	const dialog = page.locator('.modal').filter({ hasText: 'Project folder' });
	await dialog.waitFor();
	const projectRow = dialog.getByRole('button', { name: /^Project folder/ });
	if ((await projectRow.getAttribute('aria-expanded')) !== 'true') await projectRow.click();
	return dialog;
}

async function activeElementId(page) {
	return page.evaluate(() => document.activeElement?.id ?? '');
}

/** Picks an option from a ChoicePicker: phones show its list; wider screens open it first. */
async function pickChoice(dialog, pickerId, optionName) {
	const trigger = dialog.locator('#' + pickerId + '-trigger');
	if ((await trigger.count()) > 0 && (await trigger.getAttribute('aria-expanded')) !== 'true') {
		await trigger.click();
	}
	await dialog
		.locator('#' + pickerId + '-list')
		.getByRole('radio', { name: optionName })
		.click();
}

async function folderDefaultsCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-defaults',
		'folder-defaults',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			await pickChoice(dialog, 'project-run-on-website', /^Work PC/);
			await dialog.getByRole('radio', { name: 'A specific folder', exact: true }).click();
			await dialog.getByRole('button', { name: 'Choose folder', exact: true }).click();
			const pathInput = dialog.getByLabel('Folder path', { exact: true });
			await pathInput.fill('C:\\code\\docs');
			await pathInput.press('Enter');
			await dialog.getByText('C:\\code\\docs', { exact: true }).waitFor();

			await dialog.getByRole('button', { name: 'Add folder…', exact: true }).click();
			await pathInput.fill('C:\\code\\api');
			await pathInput.press('Enter');
			await dialog.getByRole('button', { name: 'Remove C:\\code\\api', exact: true }).waitFor();

			await pickChoice(dialog, 'project-access-website', /^Full access/);
			await dialog.getByText(/^A project folder is where the AI starts, not a fence\./).waitFor();
			assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
			await captureScreenshot(page, profile.name + '-folder-defaults');

			await saveFolderSettings(dialog);
			await dialog.waitFor({ state: 'hidden' });
			const saved = fixtures.requests.filter((request) => request.method === 'PUT');
			assert.deepEqual(saved[0].body, {
				mode: 'folder',
				paths: { 'pc-1': 'C:\\code\\docs' },
				machine_id: 'pc-1',
				access: 'full',
				offered: { 'pc-1': ['C:\\code\\api'] }
			});
		}
	);
}

async function folderNeedsFolderCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'folder-needs-folder',
		'folder-defaults',
		async ({ page }, fixtures) => {
			const dialog = await openFolderSettings(page);
			await pickChoice(dialog, 'project-run-on-website', /^Work PC/);
			await dialog.getByRole('radio', { name: 'A specific folder', exact: true }).click();
			const reason = dialog.getByText('Choose a folder on Work PC, or let the AI choose.', {
				exact: true
			});
			// Each refused Save shows why, where the choice is made; nothing is sent.
			for (const attempt of [1, 2]) {
				await saveFolderSettings(dialog);
				await reason.waitFor();
				assert.equal(
					await dialog.getByText('Run on', { exact: true }).isVisible(),
					true,
					`Try ${attempt}`
				);
			}
			assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
		}
	);
}

async function folderNoAccessCase(browser, profile) {
	await runCase(browser, profile, 'folder-no-access', 'folder-no-access', async ({ page }) => {
		await page.locator('button[aria-label="Folder options"]').click();
		await page.getByRole('button', { name: 'Edit', exact: true }).click();
		const dialog = page.locator('.modal').filter({ has: page.locator('#folder-form-website') });
		await dialog.locator('#folder-name').waitFor();
		await dialog.getByRole('button', { name: /^Knowledge/ }).waitFor();
		// Nothing to set for someone who may not use computers: no empty row.
		await page.waitForTimeout(300);
		assert.equal(await dialog.getByRole('button', { name: /^Project folder/ }).isVisible(), false);
	});
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
			await page.keyboard.press('Escape');
			if (!profile.mobile) {
				// Escape closes Settings, the top dialog; open it again to go on.
				await settings.waitFor({ state: 'hidden' });
				await dialog.getByRole('button', { name: 'Open Settings → Machines', exact: true }).click();
				await settings.waitFor();
			}
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

			// Still on the project settings: Escape didn't step the folder dialog back.
			await dialog.getByText('Run on', { exact: true }).waitFor();
			// Back in the folder settings, which load the plans again; the folder chip
			// and Project tab follow the change too.
			await dialog
				.getByText(
					'No AI plan is turned on. Turn one on in Settings → Machines to let the AI work in a folder on your computer.',
					{ exact: true }
				)
				.waitFor({ state: 'detached' });
			const readsAfter = projectReads();
			assert.ok(readsAfter > readsBefore, 'The plans load again');
			await page.waitForTimeout(500);
			assert.equal(projectReads(), readsAfter, 'Loading settles');
			await waitUntil(
				async () => await dialog.evaluate((element) => element.contains(document.activeElement)),
				'Focus stays in the folder settings after the button that had it went away'
			);
			assert.equal(await dialog.isVisible(), true, 'The folder settings stay open');
			assert.equal(fixtures.requests.filter((entry) => entry.method === 'PUT').length, 0);
		}
	);
}

async function computerSignInCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'computer-sign-in',
		'computer-sign-in',
		async ({ page }, fixtures) => {
			await setSettings(page, PROVIDERS_TAB);
			const row = page
				.getByRole('list', { name: 'Plans on Synthetic computer A' })
				.getByRole('listitem')
				.filter({ hasText: 'Claude' });
			assert.match(await row.innerText(), /Not signed in/);
			await row.getByRole('button', { name: 'Sign in here', exact: true }).click();
			await loginDialog(page).waitFor();
			await startLogin(page);
			await waitUntil(() => fixtures.count('POST') === 1, 'One sign-in starts');
			const start = fixtures.requests.find(
				(request) =>
					request.method === 'POST' && request.path === '/api/v1/subscriptions/claude/login'
			);
			// The chosen computer is named apart from the plan's own computer.
			assert.equal(start.body.machine_id, 'runner-a');
			assert.equal(start.body.expected_machine_revision, 'fixture-revision-a-1');
			assert.equal(start.body.expected_machine_id, undefined);
			await captureScreenshot(page, `computer-sign-in-${profile.name}`);
		}
	);
}

function runTargetBar(page) {
	return page.locator('[role="group"][aria-label="Where this chat runs"]');
}

// Dropdowns wrap their trigger in a role="button" span, so chips are found by their own class.
function runTargetChip(bar, text) {
	return bar.locator('button.buddy-run-target-chip').filter({ hasText: text });
}

async function runTargetNewCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'run-target-new',
		'run-target-new',
		async ({ page }, fixtures) => {
			const bar = runTargetBar(page);
			await bar.waitFor();
			const readiness = () =>
				fixtures.requests.filter(
					(request) => request.path === '/api/v1/subscriptions/run-target/readiness'
				);
			if (profile.mobile) {
				// One summary chip opens a sheet with every choice.
				const summary = bar.locator('.buddy-run-target-summary');
				assert.match(await summary.innerText(), /Detached/);
				const box = await summary.boundingBox();
				assert.ok(box && box.height >= 44, 'The summary chip is a full touch target');
				await summary.click();
				const sheet = page.getByRole('dialog', { name: 'Where this chat runs' });
				await sheet.waitFor();
				await sheet.getByRole('menuitemradio', { name: /Work PC/ }).click();
				await waitUntil(() => readiness().length === 1, 'Picking a computer checks it again');
				await sheet.getByRole('option', { name: /site C:\\code\\site/ }).click();
				await sheet.waitFor({ state: 'hidden' });
				await waitUntil(
					async () => /Work PC · site · Full/.test(await summary.innerText()),
					'The summary shows the computer, folder and access'
				);
				// The sheet keeps focus inside while open and gives it back when it closes.
				await summary.click();
				await sheet.waitFor();
				for (let press = 0; press < 30; press += 1) await page.keyboard.press('Tab');
				assert.equal(
					await sheet.evaluate((node) => node.contains(document.activeElement)),
					true,
					'Tab stays inside the sheet'
				);
				await sheet.getByRole('button', { name: 'Done', exact: true }).click();
				await sheet.waitFor({ state: 'hidden' });
				await waitUntil(
					() => summary.evaluate((node) => node === document.activeElement),
					'Focus returns to the summary chip'
				);
			} else {
				await runTargetChip(bar, 'Detached').click();
				await page.getByRole('menuitemradio', { name: /Work PC/ }).click();
				await waitUntil(() => readiness().length === 1, 'Picking a computer checks it again');
				assert.equal(readiness()[0].provider, 'codex');
				await runTargetChip(bar, 'Remote · Work PC').waitFor();
				await runTargetChip(bar, 'Full access').waitFor();
				await runTargetChip(bar, 'AI chooses').click();
				// A folder ChatGPT worked in, checked on the computer before it is used.
				await page.getByRole('option', { name: /^api C:\\code\\api/ }).click();
				await runTargetChip(bar, /^\s*api\s*$/).waitFor();
				// Going from one open menu straight to another chip opens that one at once,
				// since the composer stays put under the pointer.
				await runTargetChip(bar, /^\s*api\s*$/).click();
				await page.getByRole('option', { name: /^api C:\\code\\api/ }).waitFor();
				const folderPopup = page.getByRole('dialog', { name: 'Folder', exact: true });
				assert.equal(
					await folderPopup
						.locator('[role="listbox"] :is(button, input):not([role="option"])')
						.count(),
					0,
					'The folder list holds only folder options'
				);
				await runTargetChip(bar, 'Full access').click();
				await page.getByRole('menuitemradio', { name: /Read files/ }).waitFor();
				await page.keyboard.press('Escape');
				const checks = fixtures.requests.filter((request) =>
					request.path.endsWith('/folders/check')
				);
				assert.deepEqual(
					checks.map((request) => request.body),
					[{ path: 'C:\\code\\api' }]
				);
			}
			await captureScreenshot(page, `run-target-new-${profile.name}`);
			if (profile.mobile) {
				// A phone turned sideways is wider than the phone breakpoint, but still a touch screen.
				await page.setViewportSize({ width: 844, height: 390 });
				await waitUntil(
					async () => (await page.evaluate(() => window.innerWidth)) === 844,
					'The page sees the wider screen'
				);
				assert.equal(await bar.locator('.buddy-run-target-summary').count(), 1);
				assert.equal(await bar.locator('button.buddy-run-target-chip').count(), 0);
			}
		}
	);
}

async function runTargetBoundCase(browser, profile) {
	await runCase(
		browser,
		profile,
		'run-target-bound',
		'run-target-bound',
		async ({ page }, fixtures) => {
			const bar = runTargetBar(page);
			await bar.waitFor();

			// The chat runs in an exact folder, so its Project tab opens that folder, with no Change.
			await page
				.locator('.buddy-chat-header')
				.getByRole('button', { name: 'Controls', exact: true })
				.click();
			const panel = chatPanel(page, profile);
			await panel.getByRole('button', { name: 'Project', exact: true }).click();
			await panel.getByText('C:\\code\\site', { exact: true }).waitFor();
			await panel.getByText('Work PC', { exact: true }).first().waitFor();
			// Check again asks about the chat's own computer, which is offline at first.
			await panel.getByText(/Work PC is offline/).waitFor();
			fixtures.bringWorkPcOnline();
			await panel.getByRole('button', { name: 'Check again', exact: true }).click();
			await panel.getByRole('button', { name: 'README.md', exact: true }).waitFor();
			assert.equal(
				await panel.getByRole('button', { name: 'Change', exact: true }).count(),
				0,
				'A started chat’s folder is not changed from its Project tab'
			);
			await panel.getByRole('button', { name: 'Close', exact: true }).first().click();

			if (profile.mobile) {
				await bar
					.locator('.buddy-run-target-summary')
					.filter({ hasText: 'Work PC · site · Full' })
					.click();
			} else {
				await runTargetChip(bar, 'Work PC · site · Full').click();
			}
			await page.getByText('This chat runs on Work PC, in site, with Full access.').waitFor();
			await page.getByRole('button', { name: 'New chat here' }).waitFor();
			await captureScreenshot(page, `run-target-bound-${profile.name}`);
		}
	);
}

async function folderBrowseCase(browser, profile) {
	await runCase(browser, profile, 'folder-browse', 'folder-browse', async ({ page }, fixtures) => {
		const dialog = await openFolderSettings(page);

		// A folder Claude Code and Codex worked in, suggested by the computer.
		await dialog.getByRole('button', { name: 'Change', exact: true }).click();
		const suggestion = dialog.getByRole('button', { name: /^api C:\\code\\api/ });
		await suggestion.waitFor();
		assert.match(await suggestion.innerText(), /Claude Code and Codex · 2 days ago/);
		await suggestion.click();
		await dialog.getByText('C:\\code\\api', { exact: true }).waitFor();

		// Any folder on the computer, not only shared ones.
		await dialog.getByRole('button', { name: 'Change', exact: true }).click();
		await dialog.getByRole('button', { name: 'Browse Work PC…', exact: true }).click();
		await dialog.getByRole('button', { name: 'C:', exact: true }).click();
		await dialog.getByText('Choose a folder, not a whole drive.', { exact: true }).waitFor();
		await dialog.getByRole('button', { name: 'code', exact: true }).click();
		await dialog.getByRole('button', { name: 'Use “code”', exact: true }).click();
		await dialog.getByText('C:\\code', { exact: true }).waitFor();
		await saveFolderSettings(dialog);
		await dialog.waitFor({ state: 'hidden' });
		const saved = fixtures.requests.filter((request) => request.method === 'PUT');
		assert.deepEqual(saved[0].body, {
			mode: 'folder',
			paths: { 'pc-1': 'C:\\code' },
			machine_id: '',
			access: '',
			offered: {}
		});
		const listed = fixtures.requests.filter((request) => request.listed !== undefined);
		assert.deepEqual(
			listed.map((request) => request.listed),
			['', 'C:\\', 'C:\\code']
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
		await projectTabCase(browser, profile);
		await projectAgentsCase(browser, profile);
		await projectAgentsServerCase(browser, profile);
		await ultraCase(browser, profile);
		await folderChipCase(browser, profile);
		await folderEditCase(browser, profile);
		await folderDialogCase(browser, profile);
		await folderCreateCase(browser, profile);
		await folderCreateSharedCase(browser, profile);
		await folderCreateTopCase(browser, profile);
		await folderDefaultsCase(browser, profile);
		await folderNeedsFolderCase(browser, profile);
		await folderNoAccessCase(browser, profile);
		await folderNoPlansCase(browser, profile);
		await folderBrowseCase(browser, profile);
		await runTargetNewCase(browser, profile);
		await runTargetBoundCase(browser, profile);
		await computerSignInCase(browser, profile);
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
