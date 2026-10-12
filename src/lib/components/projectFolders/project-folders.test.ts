import { describe, expect, it } from 'vitest';

import type { FolderPlan, FolderProject, ProjectFolderComputer } from '$lib/apis/subscriptions';
import {
	chipState,
	claudeReadHint,
	choiceFromDraft,
	draftFromProject,
	effectiveChoice,
	emptyDraft,
	fileAccessNeed,
	grantContaining,
	joinSharedPath,
	lastSegment,
	lastUsedAgo,
	needsAttention,
	parentWithinRoot,
	plansToAllow,
	plansWithoutComputer,
	sameDraft,
	suggestionTools,
	waitsForOwnChoice
} from './project-folders';

const workPc: ProjectFolderComputer = {
	machine_id: 'pc-1',
	name: 'Work PC',
	kind: 'app',
	online: true,
	platform: 'win32',
	can_browse: true,
	folders: 'on',
	providers: [{ id: 'claude', name: 'Claude', access: 'full' }],
	recent_paths: []
};

const claudePlan: FolderPlan = {
	id: 'claude',
	name: 'Claude',
	enable: true,
	access: 'full',
	machine: { id: 'pc-1', name: 'Work PC', revision: 'rev-1' }
};

const chatGptPlan: FolderPlan = {
	id: 'codex',
	name: 'ChatGPT',
	enable: false,
	access: 'chat',
	machine: null
};

function project(overrides: Partial<FolderProject> = {}): FolderProject {
	return {
		folder_id: 'website',
		folder_name: 'Website',
		own: null,
		inherited: null,
		shared_instructions: false,
		has_plans: true,
		plans: [claudePlan, chatGptPlan],
		computers: [workPc],
		...overrides
	};
}

function choice(mode: 'auto' | 'folder' | 'chat', paths = {}, folderName = 'Website') {
	return {
		folder_id: 'f',
		folder_name: folderName,
		mode,
		paths,
		machine_id: '',
		access: '' as const,
		offered: {}
	};
}

describe('the effective choice', () => {
	it('is the folder’s own, then its parent’s, then Let the AI choose', () => {
		expect(effectiveChoice(project()).mode).toBe('auto');
		const inherited = project({ inherited: choice('chat', {}, 'Work') });
		expect(effectiveChoice(inherited)).toEqual({ mode: 'chat', paths: {}, fromFolderName: 'Work' });
		const own = project({ own: choice('auto'), inherited: choice('chat', {}, 'Work') });
		expect(effectiveChoice(own).fromFolderName).toBe('');
	});
});

describe('a folder whose instructions others can change', () => {
	it('stays chat-only until the person makes their own choice', () => {
		const shared = project({ shared_instructions: true, inherited: choice('auto', {}, 'Work') });
		expect(waitsForOwnChoice(shared)).toBe(true);
		expect(effectiveChoice(shared).mode).toBe('chat');
		// Nothing is selected, so choosing "No files, just chat" is a change that gets saved.
		expect(draftFromProject(shared).mode).toBe('unchosen');
		expect(sameDraft(draftFromProject(shared), emptyDraft('chat'))).toBe(false);
		const chosen = project({ shared_instructions: true, own: choice('auto') });
		expect(waitsForOwnChoice(chosen)).toBe(false);
		expect(effectiveChoice(chosen).mode).toBe('auto');
	});
});

describe('the chip', () => {
	it('names the computer and the folder', () => {
		const chosen = project({
			own: choice('folder', { 'pc-1': { path: 'C:\\code\\site', problem: null } })
		});
		expect(chipState(chosen)).toEqual({
			label: 'Work PC · site',
			tone: 'normal',
			fromFolderName: ''
		});
	});

	it('asks for attention when a computer was set up again or has no folder', () => {
		const changed = project({
			own: choice('folder', { 'pc-1': { path: 'C:\\code\\site', problem: 'computer_changed' } })
		});
		expect(chipState(changed).tone).toBe('attention');
		const otherComputer = project({
			own: choice('folder', { 'pc-2': { path: 'D:\\site', problem: null } })
		});
		expect(needsAttention(otherComputer)).toBe(true);
	});

	it('asks for attention when every computer with a folder was removed', () => {
		const removed = project({ own: choice('folder', {}), computers: [] });
		expect(chipState(removed)).toEqual({
			label: 'Project folder needs attention',
			tone: 'attention',
			fromFolderName: ''
		});
	});

	it('describes the other modes and where they come from', () => {
		expect(chipState(project()).label).toBe('AI chooses where to work');
		const inherited = chipState(project({ inherited: choice('chat', {}, 'Work') }));
		expect(inherited).toEqual({
			label: 'Just chat, no files',
			tone: 'normal',
			fromFolderName: 'Work'
		});
	});
});

describe('hints', () => {
	it('warns that Read files keeps Claude inside its start folder', () => {
		const reading = project({
			computers: [{ ...workPc, providers: [{ id: 'claude', name: 'Claude', access: 'read' }] }]
		});
		expect(claudeReadHint(reading)).toBe(true);
		expect(claudeReadHint(project())).toBe(false);
	});
});

describe('file access', () => {
	const chatOnlyClaude = { ...claudePlan, access: 'chat' as const };

	it('is ready while a plan can open folders on some computer', () => {
		expect(fileAccessNeed(project())).toBe('ready');
		// A Chat-only plan beside it is still offered.
		const mixed = project({
			plans: [claudePlan, { ...chatGptPlan, enable: true, machine: claudePlan.machine }]
		});
		expect(fileAccessNeed(mixed)).toBe('ready');
		expect(plansToAllow(mixed).map((plan) => plan.id)).toEqual(['codex']);
	});

	it('asks to allow a Chat-only plan that is on and has a computer', () => {
		const chatOnly = project({ plans: [chatOnlyClaude, chatGptPlan], computers: [] });
		expect(fileAccessNeed(chatOnly)).toBe('allow');
		expect(plansToAllow(chatOnly)).toEqual([chatOnlyClaude]);
	});

	it('never offers a plan that is off or has no computer it can use', () => {
		const off = { ...chatOnlyClaude, enable: false };
		const homeless = { ...chatOnlyClaude, machine: null };
		expect(plansToAllow(project({ plans: [off, homeless] }))).toEqual([]);
	});

	it('needs a computer when the plans that are on have none they can use', () => {
		const homeless = project({
			plans: [{ ...claudePlan, machine: null }, chatGptPlan],
			computers: []
		});
		expect(fileAccessNeed(homeless)).toBe('computer');
		expect(plansWithoutComputer(homeless).map((plan) => plan.id)).toEqual(['claude']);
	});

	it('names a plan with files but no computer beside one that can be allowed', () => {
		const mixed = project({
			plans: [
				{ ...claudePlan, machine: null },
				{ ...chatGptPlan, enable: true, machine: claudePlan.machine }
			],
			computers: []
		});
		expect(fileAccessNeed(mixed)).toBe('allow');
		expect(plansWithoutComputer(mixed).map((plan) => plan.id)).toEqual(['claude']);
		// A Chat-only plan without a computer has nothing to open folders with anyway.
		const chatOnlyHomeless = { ...chatGptPlan, enable: true, machine: null };
		expect(plansWithoutComputer(project({ plans: [chatOnlyHomeless] }))).toEqual([]);
	});

	it('needs a plan turned on when none is', () => {
		const none = project({
			has_plans: false,
			plans: [{ ...claudePlan, enable: false }, chatGptPlan],
			computers: []
		});
		expect(fileAccessNeed(none)).toBe('turn_on');
	});
});

describe('paths inside shared folders', () => {
	it('joins with the computer’s separator', () => {
		expect(joinSharedPath('C:\\code\\', 'site/src', 'win32')).toBe('C:\\code\\site\\src');
		expect(joinSharedPath('/home/a/code', 'site', 'linux')).toBe('/home/a/code/site');
		expect(joinSharedPath('C:\\code', '', 'win32')).toBe('C:\\code');
	});

	it('finds the shared folder containing a path, on a separator boundary', () => {
		const grants = [
			{ id: 'code', path: 'C:\\Code' },
			{ id: 'site', path: 'C:\\Code\\Site' },
			{ id: 'codex', path: 'C:\\Codex' }
		];
		expect(grantContaining(grants, 'c:\\code\\site\\Src', 'win32')).toEqual({
			grant: grants[1],
			relative: 'Src'
		});
		expect(grantContaining(grants, 'C:\\Code', 'win32')).toEqual({
			grant: grants[0],
			relative: ''
		});
		expect(grantContaining([{ id: 'code', path: 'C:\\Code' }], 'C:\\Codex\\a', 'win32')).toBeNull();
		expect(grantContaining([{ id: 'a', path: '/home/A' }], '/home/a/x', 'linux')).toBeNull();
	});

	it('goes up but never above the shared folder', () => {
		expect(parentWithinRoot('site/src')).toBe('site');
		expect(parentWithinRoot('site')).toBe('');
		expect(parentWithinRoot('')).toBe('');
	});

	it('shows the last part of a path', () => {
		expect(lastSegment('C:\\code\\site\\')).toBe('site');
		expect(lastSegment('/srv/app')).toBe('app');
	});
});

describe('drafts', () => {
	it('start from the folder’s own choice, or follow the parent', () => {
		const own = project({ own: choice('folder', { 'pc-1': { path: 'C:\\a', problem: null } }) });
		expect(draftFromProject(own)).toEqual({
			mode: 'folder',
			paths: { 'pc-1': 'C:\\a' },
			machineId: '',
			access: '',
			offered: {}
		});
		expect(draftFromProject(project({ inherited: choice('chat') }))).toEqual(emptyDraft('inherit'));
		expect(draftFromProject(project())).toEqual(emptyDraft('auto'));
	});

	it('carry where new chats start, their access and the folders offered', () => {
		const own = {
			...choice('auto'),
			machine_id: 'pc-1',
			access: 'full' as const,
			offered: { 'pc-1': [{ path: 'C:\\docs', problem: null }] }
		};
		const draft = draftFromProject(project({ own }));
		expect(draft).toEqual({
			mode: 'auto',
			paths: {},
			machineId: 'pc-1',
			access: 'full',
			offered: { 'pc-1': ['C:\\docs'] }
		});
	});

	it('compare every field', () => {
		const base = { ...emptyDraft('auto'), offered: { 'pc-1': ['C:\\a'] } };
		expect(sameDraft(base, { ...base, offered: { 'pc-1': ['C:\\a'] } })).toBe(true);
		expect(sameDraft(base, { ...base, machineId: 'pc-1' })).toBe(false);
		expect(sameDraft(base, { ...base, access: 'read' })).toBe(false);
		expect(sameDraft(base, { ...base, offered: { 'pc-1': ['C:\\b'] } })).toBe(false);
		const folder = { ...emptyDraft('folder'), paths: { a: 'x' } };
		expect(sameDraft(folder, { ...folder, paths: { a: 'y' } })).toBe(false);
	});
});

describe('the choice to save', () => {
	const draft = {
		mode: 'folder' as const,
		paths: { 'pc-1': 'C:\\site' },
		machineId: 'pc-1',
		access: 'full' as const,
		offered: { 'pc-1': ['C:\\docs'], 'pc-2': [] }
	};

	it('always sends every field, so nothing saved is lost', () => {
		expect(choiceFromDraft(draft, 'folder', [workPc])).toEqual({
			mode: 'folder',
			paths: { 'pc-1': 'C:\\site' },
			machine_id: 'pc-1',
			access: 'full',
			offered: { 'pc-1': ['C:\\docs'] }
		});
		expect(choiceFromDraft(draft, 'auto', [workPc]).paths).toEqual({});
	});

	it('keeps nothing else for detached chats', () => {
		expect(choiceFromDraft(draft, 'chat', [workPc])).toEqual({
			mode: 'chat',
			paths: {},
			machine_id: '',
			access: '',
			offered: {}
		});
	});

	it('forgets a computer that was removed, but keeps this server', () => {
		expect(choiceFromDraft({ ...draft, machineId: 'gone' }, 'auto', [workPc]).machine_id).toBe('');
		expect(choiceFromDraft({ ...draft, machineId: 'local' }, 'auto', [workPc]).machine_id).toBe(
			'local'
		);
	});
});

describe('suggested folders', () => {
	it('name the CLIs that worked there', () => {
		expect(suggestionTools(['claude'])).toBe('claude');
		expect(suggestionTools(['codex'])).toBe('codex');
		expect(suggestionTools(['codex', 'claude'])).toBe('both');
	});

	it('say how long ago, in days then months', () => {
		const day = 86400;
		const now = 100 * day + 500;
		expect(lastUsedAgo(100 * day, now)).toEqual({ unit: 'today', count: 0 });
		expect(lastUsedAgo(99 * day, now)).toEqual({ unit: 'yesterday', count: 1 });
		expect(lastUsedAgo(90 * day, now)).toEqual({ unit: 'days', count: 10 });
		expect(lastUsedAgo(10 * day, now)).toEqual({ unit: 'months', count: 3 });
		expect(lastUsedAgo(200 * day, now)).toEqual({ unit: 'today', count: 0 });
	});
});
