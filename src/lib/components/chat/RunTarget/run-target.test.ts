import { describe, expect, it } from 'vitest';

import type { RunTargetComputer, RunTargetOptions } from '$lib/apis/subscriptions';
import {
	chooseComputer,
	computerPlace,
	computerReadiness,
	defaultAccess,
	draftFromDefault,
	draftFromTarget,
	folderLabel,
	planProvidersFor,
	providerNeedingAttention,
	refreshUntouched,
	requestFromDraft
} from './run-target';

function computer(overrides: Partial<RunTargetComputer> = {}): RunTargetComputer {
	return {
		machine_id: 'pc-1',
		name: 'Work PC',
		kind: 'app',
		online: true,
		platform: 'win32',
		can_browse: true,
		folders: 'on',
		recent_paths: [],
		revision: 'rev-1',
		providers: { claude: { state: 'ready', message: null } },
		...overrides
	};
}

function options(overrides: Partial<RunTargetOptions> = {}): RunTargetOptions {
	return {
		allowed: true,
		has_plans: true,
		plan_access: { claude: 'full', codex: 'read' },
		computers: [
			computer(),
			computer({ machine_id: 'laptop', name: 'Laptop', revision: 'rev-2' }),
			computer({ machine_id: 'local', name: 'This server', kind: 'server', revision: 'local' })
		],
		recent: [],
		chat_folder: null,
		default: {
			kind: 'detached',
			machine_id: null,
			folder: null,
			access: 'chat',
			from: 'none',
			access_locked: false
		},
		...overrides
	};
}

const models = [
	{ id: 'claude-code.opus', owned_by: 'subscription', subscription: { provider: 'claude' } },
	{ id: 'codex.gpt', owned_by: 'subscription', subscription: { provider: 'codex' } },
	{ id: 'gpt-4o', owned_by: 'openai' }
];

describe('the plans behind the selected models', () => {
	it('keeps only Claude and ChatGPT models, once each', () => {
		expect(planProvidersFor(['gpt-4o', 'claude-code.opus', 'claude-code.opus'], models)).toEqual([
			'claude'
		]);
		expect(planProvidersFor(['codex.gpt', 'claude-code.opus'], models)).toEqual([
			'codex',
			'claude'
		]);
		expect(planProvidersFor(['missing'], models)).toEqual([]);
	});
});

describe('the default access', () => {
	it('is the lowest of the selected plans, or of every plan that is on', () => {
		expect(defaultAccess(options(), ['claude'])).toBe('full');
		expect(defaultAccess(options(), ['claude', 'codex'])).toBe('read');
		expect(defaultAccess(options(), [])).toBe('read');
		expect(defaultAccess(options({ plan_access: {} }), ['claude'])).toBe('chat');
	});
});

describe('the draft a new chat starts with', () => {
	it('is Detached when the server says so', () => {
		const draft = draftFromDefault(options(), ['claude']);
		expect(draft.kind).toBe('detached');
		expect(requestFromDraft(draft)).toEqual({ kind: 'detached', access: 'chat' });
	});

	it('takes the chat folder’s computer, folder and the plan’s access', () => {
		const chosen = options({
			default: {
				kind: 'computer',
				machine_id: 'laptop',
				folder: { mode: 'path', path: 'C:\\code\\site' },
				access: null,
				from: 'chat_folder',
				access_locked: false
			}
		});
		const draft = draftFromDefault(chosen, ['claude']);
		expect(requestFromDraft(draft)).toEqual({
			kind: 'computer',
			machine_id: 'laptop',
			expected_revision: 'rev-2',
			folder: { mode: 'path', path: 'C:\\code\\site' },
			access: 'full',
			acknowledged_instructions_folder_id: null
		});
	});

	it('starts chat only while shared instructions are not acknowledged', () => {
		const locked = options({
			default: {
				kind: 'computer',
				machine_id: 'pc-1',
				folder: { mode: 'auto' },
				access: 'full',
				from: 'chat_folder',
				access_locked: true
			}
		});
		expect(draftFromDefault(locked, ['claude']).access).toBe('chat');
	});

	it('falls back to Detached when the default computer is gone', () => {
		const gone = options({
			default: {
				kind: 'computer',
				machine_id: 'removed',
				folder: { mode: 'auto' },
				access: null,
				from: 'plan',
				access_locked: false
			}
		});
		expect(draftFromDefault(gone, ['claude']).kind).toBe('detached');
	});
});

describe('changing the draft', () => {
	it('keeps what the person chose when the defaults change', () => {
		const start = draftFromDefault(options(), ['claude']);
		const chosen = chooseComputer(start, options(), ['claude'], 'pc-1');
		expect(chosen.touched.computer).toBe(true);
		expect(chosen.access).toBe('full');
		const moved = options({
			default: {
				kind: 'computer',
				machine_id: 'laptop',
				folder: { mode: 'auto' },
				access: null,
				from: 'chat_folder',
				access_locked: false
			}
		});
		expect(refreshUntouched(chosen, moved, ['claude']).machineId).toBe('pc-1');
		expect(refreshUntouched(start, moved, ['claude']).machineId).toBe('laptop');
	});

	it('resets the folder to the chat folder’s default on the new computer', () => {
		const inFolder = options({
			chat_folder: {
				id: 'website',
				name: 'Website',
				mode: 'folder',
				paths: { laptop: 'D:\\site' },
				offered: {},
				shared_instructions: false
			}
		});
		const start = draftFromDefault(inFolder, ['claude']);
		expect(chooseComputer(start, inFolder, ['claude'], 'laptop').folder).toEqual({
			mode: 'path',
			path: 'D:\\site'
		});
		expect(chooseComputer(start, inFolder, ['claude'], 'pc-1').folder).toEqual({ mode: 'auto' });
		expect(chooseComputer(start, inFolder, ['claude'], null).kind).toBe('detached');
	});

	it('starts a new chat where a started one runs', () => {
		const draft = draftFromTarget(
			{
				kind: 'computer',
				machine_id: 'laptop',
				computer: { name: 'Laptop', kind: 'app', online: true },
				folder_mode: 'path',
				path: 'D:\\site',
				access: 'read',
				source: 'bar',
				note: null,
				problem: null
			},
			options()
		);
		expect(requestFromDraft(draft)).toMatchObject({
			machine_id: 'laptop',
			folder: { mode: 'path', path: 'D:\\site' },
			access: 'read'
		});
	});
});

describe('labels and readiness', () => {
	it('calls only this PC Local, and Buddy’s own server Buddy server', () => {
		expect(computerPlace(computer(), 'pc-1')).toBe('local');
		expect(computerPlace(computer(), null)).toBe('remote');
		expect(computerPlace(computer({ machine_id: 'local', kind: 'server' }), 'local')).toBe(
			'server'
		);
		expect(computerPlace(null, 'pc-1')).toBe('detached');
	});

	it('shows the least ready of the selected plans', () => {
		const mixed = computer({
			providers: {
				claude: { state: 'ready', message: null },
				codex: { state: 'sign_in', message: 'Sign in to ChatGPT here.' }
			}
		});
		expect(computerReadiness(mixed, ['claude'])).toBe('ready');
		expect(computerReadiness(mixed, ['claude', 'codex'])).toBe('sign_in');
		expect(computerReadiness(mixed, [])).toBe('sign_in');
		expect(providerNeedingAttention(mixed, ['claude', 'codex'])).toBe('codex');
	});

	it('names a folder by its last part', () => {
		const draft = draftFromDefault(options(), []);
		expect(folderLabel(draft)).toEqual({ kind: 'none' });
		const inFolder = {
			...draft,
			kind: 'computer' as const,
			folder: { mode: 'path' as const, path: 'C:\\code\\open-webui\\' }
		};
		expect(folderLabel(inFolder)).toEqual({
			kind: 'path',
			name: 'open-webui',
			path: 'C:\\code\\open-webui\\'
		});
		expect(folderLabel({ ...inFolder, folder: { mode: 'auto' } })).toEqual({ kind: 'auto' });
	});
});
