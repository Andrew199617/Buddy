import { describe, expect, it } from 'vitest';

import type { FolderProject, ProjectFolderComputer } from '$lib/apis/subscriptions';
import {
	chipState,
	choiceFromDraft,
	draftFromProject,
	draftProblem,
	draftWithOffered,
	draftWithoutOffered,
	draftWithRunOn,
	effectiveChoice,
	emptyDraft,
	grantContaining,
	joinSharedPath,
	lastSegment,
	lastUsedAgo,
	needsAttention,
	parentWithinRoot,
	planAccesses,
	projectSummary,
	runOnValue,
	sameDraft,
	sharedPlanAccess,
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

function project(overrides: Partial<FolderProject> = {}): FolderProject {
	return {
		folder_id: 'website',
		folder_name: 'Website',
		own: null,
		inherited: null,
		shared_instructions: false,
		has_plans: true,
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
		expect(chipState(project()).label).toBe('Project folder not set');
		expect(chipState(project({ own: choice('auto') })).label).toBe('AI chooses where to work');
		const inherited = chipState(project({ inherited: choice('chat', {}, 'Work') }));
		expect(inherited).toEqual({
			label: 'Detached',
			tone: 'normal',
			fromFolderName: 'Work'
		});
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
		// No own choice and no parent: new chats start detached until one is made.
		expect(draftFromProject(project())).toEqual(emptyDraft('inherit'));
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
		// Left over from an earlier pick, but not saved for these modes.
		for (const mode of ['inherit', 'unchosen', 'chat'] as const) {
			expect(sameDraft(emptyDraft(mode), { ...emptyDraft(mode), machineId: 'pc-1' })).toBe(true);
		}
	});
});

describe('where new chats run', () => {
	it('reads from the draft', () => {
		expect(runOnValue(emptyDraft('unchosen'))).toBeNull();
		expect(runOnValue(emptyDraft('inherit'))).toBe('inherit');
		expect(runOnValue(emptyDraft('chat'))).toBe('detached');
		expect(runOnValue(emptyDraft('auto'))).toBe('automatic');
		expect(runOnValue({ ...emptyDraft('folder'), machineId: 'pc-1' })).toBe('pc-1');
	});

	it('keeps a folder choice when the computer changes, and lets the AI choose otherwise', () => {
		const folder = { ...emptyDraft('folder'), paths: { 'pc-1': 'C:\\site' } };
		expect(draftWithRunOn(folder, 'pc-1')).toEqual({ ...folder, machineId: 'pc-1' });
		expect(draftWithRunOn(emptyDraft('chat'), 'automatic')).toEqual(emptyDraft('auto'));
		expect(draftWithRunOn(emptyDraft('inherit'), 'pc-1').mode).toBe('auto');
		expect(draftWithRunOn(folder, 'detached').mode).toBe('chat');
		expect(draftWithRunOn(folder, 'inherit').mode).toBe('inherit');
	});

	it('comes back to a specific folder after passing through detached or the parent', () => {
		const folder = { ...emptyDraft('folder'), paths: { 'pc-1': 'C:\site' }, machineId: 'pc-1' };
		for (const via of ['detached', 'inherit']) {
			const back = draftWithRunOn(draftWithRunOn(folder, via), 'pc-1');
			expect(back).toEqual(folder);
		}
		const automatic = draftWithRunOn(draftWithRunOn(folder, 'detached'), 'automatic');
		expect(automatic.mode).toBe('folder');
		// A computer with no folder picked yet lets the AI choose.
		expect(draftWithRunOn(draftWithRunOn(folder, 'detached'), 'pc-2').mode).toBe('auto');
	});

	it('needs a folder on the chosen computer before saving a specific folder', () => {
		const folder = { ...emptyDraft('folder'), paths: { 'pc-2': 'D:\\x' }, machineId: 'pc-1' };
		expect(draftProblem(folder)).toEqual({ kind: 'needs_folder', machineId: 'pc-1' });
		expect(draftProblem({ ...emptyDraft('folder') })).toEqual({
			kind: 'needs_folder',
			machineId: ''
		});
		expect(draftProblem({ ...folder, machineId: 'pc-2' })).toBeNull();
		expect(draftProblem(emptyDraft('auto'))).toBeNull();
	});
});

describe('the plans’ default access', () => {
	it('is shared when every plan that is on starts the same, and listed otherwise', () => {
		const both = {
			...workPc,
			providers: [
				{ id: 'claude' as const, name: 'Claude', access: 'full' as const },
				{ id: 'codex' as const, name: 'ChatGPT', access: 'read' as const }
			]
		};
		const differing = planAccesses(project({ computers: [both] }));
		expect(differing).toEqual([
			{ name: 'Claude', access: 'full' },
			{ name: 'ChatGPT', access: 'read' }
		]);
		expect(sharedPlanAccess(differing)).toBeNull();
		expect(sharedPlanAccess(planAccesses(project()))).toBe('full');
		expect(sharedPlanAccess(planAccesses(project({ computers: [] })))).toBeNull();
	});
});

describe('the row summary', () => {
	it('names the folder, the computer and the access', () => {
		const draft = {
			...emptyDraft('folder'),
			paths: { 'pc-1': 'C:\\code\\site' },
			machineId: 'pc-1',
			access: 'full' as const
		};
		expect(projectSummary(draft, project())).toEqual({
			kind: 'set',
			folder: 'site',
			missingFolder: false,
			computer: workPc,
			access: 'full'
		});
		expect(projectSummary(emptyDraft('auto'), project())).toEqual({
			kind: 'set',
			folder: null,
			missingFolder: false,
			computer: null,
			access: ''
		});
	});

	it('never names another computer’s folder for the chosen one', () => {
		const elsewhere = {
			...emptyDraft('folder'),
			paths: { 'pc-2': 'D:\\docs' },
			machineId: 'pc-1'
		};
		const summary = projectSummary(elsewhere, project());
		expect(summary).toMatchObject({ kind: 'set', folder: null, missingFolder: true });
	});

	it('says when the folder follows its parent, has no choice, or is detached', () => {
		const inherited = project({ inherited: choice('chat', {}, 'Work') });
		expect(projectSummary(emptyDraft('inherit'), inherited)).toEqual({
			kind: 'inherit',
			folderName: 'Work'
		});
		expect(projectSummary(emptyDraft('inherit'), project())).toEqual({ kind: 'unset' });
		expect(projectSummary(emptyDraft('chat'), project())).toEqual({ kind: 'detached' });
		expect(projectSummary(emptyDraft('unchosen'), project())).toEqual({ kind: 'unchosen' });
	});
});

describe('folders to offer', () => {
	it('adds up to five per computer without repeats, and removes them', () => {
		let draft = emptyDraft('auto');
		for (const path of ['a', 'b', 'c', 'd', 'e', 'f', 'a']) {
			draft = draftWithOffered(draft, 'pc-1', 'C:\\' + path);
		}
		expect(draft.offered['pc-1']).toEqual(['C:\\a', 'C:\\b', 'C:\\c', 'C:\\d', 'C:\\e']);
		draft = draftWithoutOffered(draft, 'pc-1', 'C:\\a');
		expect(draft.offered['pc-1']).toHaveLength(4);
		for (const path of ['b', 'c', 'd', 'e']) {
			draft = draftWithoutOffered(draft, 'pc-1', 'C:\\' + path);
		}
		expect(draft.offered).toEqual({});
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
		const removedSince = {
			...draft,
			paths: { 'pc-1': 'C:\\site', gone: 'D:\\x', local: '/srv/site' },
			offered: { gone: ['D:\\y'], local: ['/srv/docs'] }
		};
		const saved = choiceFromDraft(removedSince, 'folder', [workPc]);
		expect(saved.paths).toEqual({ 'pc-1': 'C:\\site', local: '/srv/site' });
		expect(saved.offered).toEqual({ local: ['/srv/docs'] });
	});

	it('starts a draft without a computer that was removed since', () => {
		const own = { ...choice('auto'), machine_id: 'gone' };
		expect(draftFromProject(project({ own })).machineId).toBe('');
		const server = { ...choice('auto'), machine_id: 'local' };
		expect(draftFromProject(project({ own: server })).machineId).toBe('local');
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
