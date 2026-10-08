/** Synthetic production-browser fixture: API writes, providers, and media are blocked. */
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const origin = process.env.BUDDY_LAZY_TEST_ORIGIN || 'http://127.0.0.1:8082';
export const workspace = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
export const outputDirectory = resolve(workspace, 'local/tests/.qa/lazy-features');
const now = Math.floor(Date.now() / 1000);
const imageFixture = readFileSync(resolve(workspace, 'static/static/buddy-mark.svg'), 'utf8');
const model = {
	id: 'buddy-design-model',
	name: 'Buddy local model',
	object: 'model',
	owned_by: 'openai',
	connection_type: 'external',
	openai: { id: 'buddy-design-model' },
	info: {
		id: 'buddy-design-model',
		name: 'Buddy local model',
		params: {},
		meta: { capabilities: { vision: true, file_upload: true, tool_calling: true }, tags: [] }
	}
};
const user = {
	id: 'buddy-design-user',
	name: 'Alex',
	email: 'design-preview@example.invalid',
	role: 'admin',
	profile_image_url: '/static/buddy-mark.svg',
	expires_at: now + 86400,
	permissions: {
		chat: {
			controls: true,
			multiple_models: true,
			temporary: true,
			stt: true,
			call: true,
			file_upload: true
		},
		features: { notes: true, automations: true, calendar: true, code_interpreter: true },
		workspace: { models: true, knowledge: true, prompts: true, tools: true }
	}
};
const codeFence = String.fromCharCode(96).repeat(3);
const friendlyAnswer = [
	'Absolutely. Let’s make today feel a little lighter, one small step at a time.',
	'',
	'### A gentle plan',
	'',
	'- Pick one useful thing to finish before lunch.',
	'- Leave a little breathing room between tasks.',
	'- Keep a tiny list for the ideas you want to revisit.',
	'',
	'Here is a quick starting point you can adapt:',
	'',
	codeFence + 'python',
	'today = ["one focused task", "a short walk", "something just for you"]',
	'for idea in today:',
	'    print("Make room for:", idea)',
	codeFence,
	'',
	'You do not need to make the whole day perfect. A clear next step is enough.'
].join('\n');
const longAnswer =
	friendlyAnswer +
	'\n\n' +
	Array.from({ length: 8 }, (_, index) =>
		[
			'### A little space for idea ' + (index + 1),
			'',
			'Start with the part that matters to you. Write down the next small action, choose a comfortable pace, and let the rest wait until you are ready. We can shape the details together.',
			'',
			'1. Give this idea a useful name.',
			'2. Choose a first step you can finish in a few minutes.',
			'3. Keep the plan simple enough to change.'
		].join('\n')
	).join('\n\n');

function mockChat(options = {}) {
	const prompt = {
		id: 'design-user-message',
		parentId: null,
		childrenIds: ['design-buddy-message'],
		role: 'user',
		content:
			'Buddy, can you help me plan a calmer day? A short plan and a tiny code example would be great.',
		timestamp: now - 60
	};
	const reply = {
		id: 'design-buddy-message',
		parentId: prompt.id,
		childrenIds: [],
		role: 'assistant',
		model: model.id,
		modelName: model.name,
		content: longAnswer,
		timestamp: now - 30,
		done: true
	};
	if (options.structured) {
		reply.content = 'A structured answer for the editor test.';
		reply.output = [
			{
				type: 'message',
				role: 'assistant',
				content: [{ type: 'output_text', text: reply.content }]
			}
		];
	} else if (!options.code) {
		reply.content = 'A plain answer for optional panel tests.';
	}
	if (options.terminalOutput) {
		const file = {
			type: 'file',
			source: 'open_terminal',
			path: '/readme.txt',
			name: 'readme.txt',
			terminal_selector: 'qa-terminal',
			displayed: true,
			exists: true
		};
		reply.content = '';
		reply.output = [
			{
				type: 'function_call',
				call_id: 'qa-file-call',
				name: 'display_file',
				arguments: JSON.stringify({ path: file.path }),
				status: 'completed'
			},
			{
				type: 'function_call_output',
				call_id: 'qa-file-call',
				output: [{ type: 'text', text: JSON.stringify(file) }]
			}
		];
	}
	return {
		id: 'buddy-design-chat',
		title: 'A little room to think',
		user_id: user.id,
		created_at: now - 60,
		updated_at: now,
		last_read_at: now,
		pinned: false,
		active: false,
		current_message_id: reply.id,
		chat: {
			title: 'A little room to think',
			models: [model.id],
			params: {},
			files: [],
			history: { currentId: reply.id, messages: { [prompt.id]: prompt, [reply.id]: reply } },
			messages: [prompt, reply]
		}
	};
}
function createConfig() {
	return {
		status: true,
		name: 'Buddy',
		version: '0.11.4',
		default_locale: 'en-US',
		i18n: {},
		onboarding: false,
		oauth: { providers: {} },
		features: {
			auth: true,
			enable_websocket: false,
			enable_direct_connections: false,
			enable_folders: true,
			enable_login_form: true,
			enable_signup: true,
			enable_ldap: false,
			enable_web_search: false,
			enable_image_generation: false,
			enable_code_interpreter: false,
			enable_code_execution: false,
			enable_channels: false,
			enable_calendar: true,
			enable_automations: true,
			enable_notes: true,
			enable_plugins: false,
			enable_user_status: false,
			enable_version_update_check: false
		},
		default_models: model.id,
		default_pinned_models: '',
		default_prompt_suggestions: [
			{ title: ['Make a plan', 'for a calmer day'], content: 'Help me plan a calmer day.' },
			{
				title: ['Explore an idea', 'with a little help'],
				content: 'Help me think through an idea.'
			},
			{ title: ['Write something', 'clear and kind'], content: 'Help me write a friendly message.' }
		],
		code: { engine: 'pyodide', interpreter_engine: 'pyodide' },
		audio: { tts: { engine: '', voice: '', split_on: 'punctuation' }, stt: { engine: 'web' } },
		file: { max_size: 25, max_count: 10, image_compression: { width: 1024, height: 1024 } },
		permissions: user.permissions,
		ui: { default_interface_settings: {}, response_watermark: '' },
		license_metadata: null
	};
}
function getResponse(url, options) {
	const path = url.pathname.replace(/\/$/, '');
	if (path === '/api/config') return createConfig();
	if (path === '/api/version') return { version: '0.11.4', deployment_id: 'buddy-design-fixture' };
	if (path === '/api/v1/auths') return { ...user, role: options.role || 'admin' };
	if (path === '/api/models' || path === '/api/models/base')
		return { object: 'list', data: [model] };
	if (path === '/api/v1/users/user/settings')
		return {
			ui: {
				models: [model.id],
				pinnedModels: [],
				showChatMenu: false,
				iframeSandboxAllowSameOrigin: true,
				showChatTitleInTab: false,
				showChangelog: false,
				version: '0.11.4',
				title: { auto: false },
				autoTags: false,
				autoFollowUps: false
			},
			keybindings: {}
		};
	if (path === '/api/v1/chats') return [];
	if (path === '/api/v1/chats/buddy-design-chat') return mockChat(options);
	if (path === '/api/v1/chats/config') return {};
	if (path === '/api/v1/chats/archived/count') return { count: 0 };
	if (path.startsWith('/api/tasks/chat/')) return { task_ids: [] };
	if (
		path === '/api/v1/knowledge/search' ||
		path === '/api/v1/notes/search' ||
		path === '/api/v1/automations/list' ||
		path === '/api/v1/models/list'
	)
		return { items: [], total: 0 };
	if (path === '/api/v1/models') return [model];
	if (path === '/api/v1/knowledge/search/files') return { items: [], total: 0 };
	if (path === '/api/v1/audio/voices') return { voices: [] };
	if (path === '/api/v1/terminals/qa-terminal/api/config')
		return { features: { terminal: false, filesystem: true } };
	if (path === '/api/v1/terminals/qa-terminal/files/cwd')
		return { cwd: '/', root: { path: '/', name: 'Test files', writable: true } };
	if (path === '/api/v1/terminals/qa-terminal/files/read')
		return { path: '/readme.txt', total_lines: 1, content: 'Isolated terminal preview content' };
	if (path === '/api/v1/terminals/qa-terminal/files/list')
		return { entries: [{ name: 'readme.txt', type: 'file', size: 12 }], writable: true };
	if (path.includes('/configs/connections'))
		return { OPENAI_API_BASE_URLS: [], OPENAI_API_KEYS: [], OPENAI_API_CONFIGS: {} };
	if (path.includes('/tool_permissions')) return { mode: 'allow', permissions: {} };
	return [];
}

function currentProductionLoader() {
	const patchesSource = readFileSync(resolve(workspace, 'local/owui_local_patches.py'), 'utf8');
	const declaration = patchesSource.match(/WEB_SCRIPT(?:_PATHS|S)\s*=\s*\[([\s\S]*?)\]/);
	if (!declaration) throw new Error('Production loader script declaration is missing');
	const names = Array.from(
		declaration[1].matchAll(/WEB_DIR\s*\/\s*'([^']+\.js)'/g),
		(match) => match[1]
	);
	const marker = '/* --- local/web scripts (added by owui_local_patches.py) --- */';
	const base = readFileSync(resolve(workspace, 'static/static/loader.js'), 'utf8')
		.split(marker)[0]
		.trimEnd();
	const scripts = names.map((name) => readFileSync(resolve(workspace, 'local/web', name), 'utf8'));
	return { names, source: [base, marker, ...scripts].join('\n\n').trimStart() + '\n' };
}

export async function createSession(browser, options = {}) {
	mkdirSync(outputDirectory, { recursive: true });
	const context = await browser.newContext({
		viewport: options.mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
		hasTouch: Boolean(options.mobile),
		isMobile: Boolean(options.mobile),
		deviceScaleFactor: 1,
		locale: 'en-US',
		colorScheme: options.theme || 'dark',
		reducedMotion: options.reducedMotion ? 'reduce' : 'no-preference',
		serviceWorkers: 'block'
	});
	const requests = [];
	const blockedMutations = [];
	const completions = [];
	const mutations = [];
	let currentChat = mockChat(options);
	const loaderScripts = [];
	const errors = [];
	await context.addInitScript(({ theme, sidebarStored, mobile, touchPoints }) => {
		if (window !== window.top) return;
		let maxTouchPoints = 0;
		if (mobile) maxTouchPoints = 1;
		if (Number.isInteger(touchPoints)) maxTouchPoints = touchPoints;
		Object.defineProperty(navigator, 'maxTouchPoints', {
			value: maxTouchPoints,
			configurable: true
		});
		localStorage.setItem('token', 'buddy-design-fake-token');
		localStorage.setItem('locale', 'en-US');
		localStorage.setItem('theme', theme || 'dark');
		let storedSidebar = 'false';
		if (sidebarStored) storedSidebar = 'true';
		localStorage.setItem('sidebar', storedSidebar);
		localStorage.setItem('sidebarWidth', '245');
		localStorage.setItem('changelog', '0.11.4');
		window.__qaMediaRequests = 0;
		if (navigator.mediaDevices) {
			navigator.mediaDevices.getUserMedia = async () => {
				window.__qaMediaRequests += 1;
				throw new Error('Media access is blocked in isolated QA');
			};
		}
		window.open = () => {
			throw new Error('External windows are blocked in isolated QA');
		};
	}, options);
	await context.route('**/*', async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		if (url.origin !== origin) {
			await route.abort('blockedbyclient');
			return;
		}
		if (url.pathname === '/static/loader.js') {
			const loader = currentProductionLoader();
			loaderScripts.splice(0, loaderScripts.length, ...loader.names);
			await route.fulfill({
				status: 200,
				contentType: 'application/javascript',
				body: loader.source
			});
			return;
		}

		if (url.pathname.startsWith('/ws/')) {
			await route.abort('blockedbyclient');
			return;
		}
		if (url.pathname.startsWith('/ollama/')) {
			await route.fulfill({ status: 200, json: { version: '0.0.0', models: [] } });
			return;
		}
		if (url.pathname.startsWith('/openai/')) {
			await route.fulfill({ status: 200, json: {} });
			return;
		}
		if (!url.pathname.startsWith('/api/')) {
			if (request.method() !== 'GET' && request.method() !== 'HEAD') {
				blockedMutations.push(request.method() + ' ' + url.pathname);
				await route.fulfill({ status: 200, json: {} });
				return;
			}
			await route.continue();
			return;
		}
		requests.push(request.method() + ' ' + url.pathname);
		if (request.method() !== 'GET') {
			blockedMutations.push(request.method() + ' ' + url.pathname);
			mutations.push({
				method: request.method(),
				path: url.pathname,
				body: request.postDataJSON()
			});
			if (url.pathname === '/api/chat/completions') {
				const body = request.postDataJSON();
				completions.push(body);
				await route.fulfill({
					status: 200,
					json: {
						status: true,
						task_id: 'design-preview-task',
						task_ids: ['design-preview-task'],
						chat_id: body.chat_id || 'buddy-design-chat'
					}
				});
				return;
			}
			if (/^\/api\/v1\/chats\/buddy-design-chat\/?$/.test(url.pathname)) {
				const body = request.postDataJSON();
				currentChat = { ...currentChat, chat: body.chat };
				await route.fulfill({ status: 200, json: currentChat });
				return;
			}
			await route.fulfill({ status: 200, json: { status: true, ...user } });
			return;
		}
		if (url.pathname.endsWith('/profile/image')) {
			await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: imageFixture });
			return;
		}
		if (url.pathname.replace(/\/$/, '') === '/api/v1/chats/buddy-design-chat') {
			await route.fulfill({ status: 200, json: currentChat });
			return;
		}
		await route.fulfill({ status: 200, json: getResponse(url, options) });
	});
	if (options.beforeNavigation) await options.beforeNavigation(context);
	const page = await context.newPage();
	page.on('pageerror', (error) => errors.push(error.stack || error.message));
	await page.goto(origin + (options.path || '/c/buddy-design-chat'), {
		waitUntil: 'domcontentloaded'
	});
	try {
		await page.locator('#chat-input').waitFor({ timeout: 90000 });
		await page.evaluate(() => document.fonts.ready);
		await page.waitForTimeout(400);
		assert.equal(
			loaderScripts.length,
			9,
			'All current local production loader scripts must be served'
		);
		const loaderFeatures = await page.evaluate(() => ({
			reasoning: window.__owuiReasoningChip,
			mobileLayout: window.__owuiMobileChatLayout,
			mobilePolish: window.__owuiMobileUiPolish,
			usage: window.__owuiChatUsageInfo,
			activity: window.__owuiModelActivity,
			actions: window.__owuiChatActionsMenu,
			header: window.__owuiChatHeader,
			settings: window.__owuiMobileSettings,
			modelEdit: window.__owuiModelEditShortcut
		}));
		assert.ok(
			Object.values(loaderFeatures).every((loaded) => loaded === true),
			'Current local loader scripts must initialize'
		);
	} catch (error) {
		await page.screenshot({ path: resolve(outputDirectory, 'startup-failure.png') });
		writeFileSync(
			resolve(outputDirectory, 'startup-failure.json'),
			JSON.stringify(
				{
					message: error.message,
					url: page.url(),
					errors,
					requests,
					body: await page.locator('body').innerText()
				},
				null,
				2
			)
		);
		await context.close();
		throw error;
	}
	return {
		context,
		page,
		requests,
		blockedMutations,
		mutations,
		completions,
		loaderScripts,
		errors
	};
}
