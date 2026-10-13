import { describe, expect, it } from 'vitest';

import type { RunTargetOptions } from '$lib/apis/subscriptions';
import { buildFolderSections, looksLikePath } from './folder-menu';

function options(): RunTargetOptions {
	return {
		allowed: true,
		has_plans: true,
		plan_access: { claude: 'full' },
		computers: [
			{
				machine_id: 'pc-1',
				name: 'Work PC',
				kind: 'app',
				online: true,
				platform: 'win32',
				can_browse: true,
				folders: 'on',
				recent_paths: ['C:\\code\\notes', 'C:\\Code\\Site'],
				revision: 'rev-1',
				providers: {}
			}
		],
		recent: [
			{ machine_id: 'pc-1', path: 'C:\\code\\site', last_used: 3 },
			{ machine_id: 'laptop', path: 'D:\\one', last_used: 2 },
			{ machine_id: 'laptop', path: 'D:\\two', last_used: 2 },
			{ machine_id: 'server', path: '/srv/three', last_used: 1 },
			{ machine_id: 'laptop', path: 'D:\\four', last_used: 1 }
		],
		chat_folder: {
			id: 'website',
			name: 'Website',
			mode: 'folder',
			paths: { 'pc-1': 'C:\\code\\site' },
			offered: { 'pc-1': ['C:\\code\\docs'] },
			shared_instructions: false
		},
		default: undefined
	};
}

const suggested = [
	{ path: 'C:\\code\\api', sources: ['claude' as const], last_used: 1 },
	{ path: 'c:\\code\\docs', sources: ['codex' as const], last_used: 1 }
];

describe('the folder menu’s sections', () => {
	it('lists each folder once, the chat folder’s first, ignoring case on Windows', () => {
		const sections = buildFolderSections({
			options: options(),
			suggested,
			machineId: 'pc-1',
			query: '',
			platform: 'win32'
		});
		expect(sections.inChatFolder).toEqual([
			{ path: 'C:\\code\\site', isDefault: true },
			{ path: 'C:\\code\\docs', isDefault: false }
		]);
		expect(sections.recentHere).toEqual(['C:\\code\\notes']);
		expect(sections.fromAgents.map((suggestion) => suggestion.path)).toEqual(['C:\\code\\api']);
		// At most three from other computers.
		expect(sections.recentElsewhere).toEqual([
			{ machineId: 'laptop', path: 'D:\\one' },
			{ machineId: 'laptop', path: 'D:\\two' },
			{ machineId: 'server', path: '/srv/three' }
		]);
	});

	it('filters every section by what was typed', () => {
		const sections = buildFolderSections({
			options: options(),
			suggested,
			machineId: 'pc-1',
			query: 'DOC',
			platform: 'win32'
		});
		expect(sections.inChatFolder.map((entry) => entry.path)).toEqual(['C:\\code\\docs']);
		expect(sections.recentHere).toEqual([]);
		expect(sections.fromAgents).toEqual([]);
		expect(sections.recentElsewhere).toEqual([]);
	});
});

describe('a typed full path', () => {
	it('matches the computer’s platform', () => {
		expect(looksLikePath('C:\\code', 'win32')).toBe(true);
		expect(looksLikePath('/home/me', 'win32')).toBe(false);
		expect(looksLikePath('~/code', 'linux')).toBe(true);
		expect(looksLikePath('code', 'linux')).toBe(false);
		expect(looksLikePath('D:/x', null)).toBe(true);
		expect(looksLikePath('   ', null)).toBe(false);
	});
});
