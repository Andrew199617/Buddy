import { describe, expect, it } from 'vitest';

import type { FolderProject, ProjectFolderComputer } from '$lib/apis/subscriptions';
import {
	chipState,
	claudeReadHint,
	draftFromProject,
	effectiveChoice,
	grantContaining,
	joinSharedPath,
	lastSegment,
	needsAttention,
	parentWithinRoot,
	sameDraft,
	waitsForOwnChoice
} from './project-folders';

const workPc: ProjectFolderComputer = {
	machine_id: 'pc-1',
	name: 'Work PC',
	kind: 'app',
	online: true,
	platform: 'win32',
	can_browse: true,
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
	return { folder_id: 'f', folder_name: folderName, mode, paths };
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
		expect(sameDraft(draftFromProject(shared), { mode: 'chat', paths: {} })).toBe(false);
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
		expect(draftFromProject(own)).toEqual({ mode: 'folder', paths: { 'pc-1': 'C:\\a' } });
		expect(draftFromProject(project({ inherited: choice('chat') }))).toEqual({
			mode: 'inherit',
			paths: {}
		});
		expect(draftFromProject(project())).toEqual({ mode: 'auto', paths: {} });
	});

	it('compare by mode and paths', () => {
		expect(sameDraft({ mode: 'auto', paths: {} }, { mode: 'auto', paths: {} })).toBe(true);
		expect(
			sameDraft({ mode: 'folder', paths: { a: 'x' } }, { mode: 'folder', paths: { a: 'y' } })
		).toBe(false);
	});
});
