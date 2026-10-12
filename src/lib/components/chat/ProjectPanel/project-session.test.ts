import { describe, expect, it } from 'vitest';

import { CompanionError, type CompanionTerminal } from '$lib/apis/companion';
import type { FolderProject, ProjectFolderComputer } from '$lib/apis/subscriptions';
import {
	checkTerminalReply,
	crumbsBelow,
	isTransientFailure,
	isWithinProject,
	mayHaveRun,
	parentWithinProject,
	parseArguments,
	pollDelay,
	preferredTarget,
	projectTargets,
	targetProblem,
	visibleOutput,
	OUTPUT_LIMIT
} from './project-session';

function computer(overrides: Partial<ProjectFolderComputer> = {}): ProjectFolderComputer {
	return {
		machine_id: 'pc-1',
		name: 'Work PC',
		kind: 'app',
		online: true,
		platform: 'win32',
		can_browse: true,
		folders: 'on',
		providers: [{ id: 'claude', name: 'Claude', access: 'full' }],
		recent_paths: [],
		...overrides
	};
}

const laptop = computer({ machine_id: 'laptop', name: 'Laptop', providers: [] });

function project(overrides: Partial<FolderProject> = {}): FolderProject {
	return {
		folder_id: 'website',
		folder_name: 'Website',
		own: null,
		inherited: null,
		shared_instructions: false,
		has_plans: true,
		computers: [laptop, computer()],
		...overrides
	};
}

type SavedPaths = Record<string, { path: string; problem: 'computer_changed' | null }>;

function folderChoice(paths: SavedPaths) {
	return {
		folder_id: 'website',
		folder_name: 'Website',
		mode: 'folder' as const,
		paths,
		machine_id: '',
		access: '' as const,
		offered: {}
	};
}

describe('the project folders a chat can open', () => {
	it('lists each computer with an exact folder and prefers one that runs a plan', () => {
		const chosen = project({
			own: folderChoice({
				laptop: { path: '/home/sam/site', problem: null },
				'pc-1': { path: 'C:\\code\\site', problem: null }
			})
		});
		const targets = projectTargets(chosen);
		expect(targets.map((target) => target.path)).toEqual(['/home/sam/site', 'C:\\code\\site']);
		expect(preferredTarget(targets)?.computer.name).toBe('Work PC');
	});

	it('uses a parent folder’s choice and has none when the AI chooses', () => {
		const inherited = project({
			inherited: folderChoice({ 'pc-1': { path: 'C:\\x', problem: null } })
		});
		expect(projectTargets(inherited)).toHaveLength(1);
		expect(projectTargets(project())).toEqual([]);
		expect(preferredTarget([])).toBeNull();
	});

	it('opens only folders on online Buddy app computers that were not set up again', () => {
		const target = { computer: computer(), path: 'C:\\x', chooseAgain: false };
		expect(targetProblem(target)).toBeNull();
		expect(targetProblem({ ...target, chooseAgain: true })).toBe('choose_again');
		expect(targetProblem({ ...target, computer: computer({ kind: 'address' }) })).toBe('needs_app');
		expect(targetProblem({ ...target, computer: computer({ kind: 'server' }) })).toBe(
			'not_browsable'
		);
		expect(targetProblem({ ...target, computer: computer({ online: false }) })).toBe('offline');
	});

	it('marks a folder chosen before the computer was set up again', () => {
		const chosen = project({
			own: folderChoice({ 'pc-1': { path: 'C:\\x', problem: 'computer_changed' } })
		});
		expect(projectTargets(chosen)[0].chooseAgain).toBe(true);
	});
});

describe('browsing inside the project folder', () => {
	it('goes up only as far as the project folder', () => {
		expect(parentWithinProject('site/src/lib', 'site')).toBe('site/src');
		expect(parentWithinProject('site/src', 'site')).toBe('site');
		expect(parentWithinProject('site', 'site')).toBeNull();
		expect(parentWithinProject('src', '')).toBe('');
		expect(parentWithinProject('', '')).toBeNull();
	});

	it('builds the breadcrumb below the project folder', () => {
		expect(crumbsBelow('site/src/lib', 'site')).toEqual([
			{ name: 'src', path: 'site/src' },
			{ name: 'lib', path: 'site/src/lib' }
		]);
		expect(crumbsBelow('site', 'site')).toEqual([]);
		expect(crumbsBelow('a/b', '')).toEqual([
			{ name: 'a', path: 'a' },
			{ name: 'b', path: 'a/b' }
		]);
	});

	it('knows what is inside the project folder', () => {
		expect(isWithinProject('site/src', 'site')).toBe(true);
		expect(isWithinProject('site', 'site')).toBe(true);
		expect(isWithinProject('site-old/src', 'site')).toBe(false);
		expect(isWithinProject('anything', '')).toBe(true);
	});
});

describe('command results', () => {
	const reply: CompanionTerminal = {
		id: 't-1',
		workspaceId: 'w-1',
		status: 'running',
		output: 'hi',
		exitCode: null
	};

	it('accepts only a reply about this session', () => {
		expect(checkTerminalReply(reply, 't-1', 'w-1')).toBe(reply);
		expect(() => checkTerminalReply(reply, 't-2', 'w-1')).toThrow(CompanionError);
		const oddStatus = { ...reply, status: 'odd' as CompanionTerminal['status'] };
		expect(() => checkTerminalReply(oddStatus, 't-1', 'w-1')).toThrow();
		expect(() => checkTerminalReply({ ...reply, exitCode: 1.5 }, 't-1', 'w-1')).toThrow();
	});

	it('retries only failures that may pass, and treats lost replies as maybe run', () => {
		expect(isTransientFailure(new TypeError('offline'))).toBe(true);
		expect(isTransientFailure(new CompanionError('busy', 429))).toBe(true);
		expect(isTransientFailure(new CompanionError('gone', 404))).toBe(false);
		expect(mayHaveRun(new CompanionError('bad gateway', 502))).toBe(true);
		expect(mayHaveRun(new CompanionError('refused', 403))).toBe(false);
	});

	it('reads one argument per line and keeps spaces inside them', () => {
		expect(parseArguments('--version\n\n  two words \r\n')).toEqual(['--version', '  two words ']);
		expect(parseArguments('')).toEqual([]);
	});

	it('keeps the latest output and backs off status checks', () => {
		const long = 'a'.repeat(OUTPUT_LIMIT) + 'tail';
		expect(visibleOutput(long).endsWith('tail')).toBe(true);
		expect(visibleOutput(long)).toHaveLength(OUTPUT_LIMIT);
		expect(pollDelay(0)).toBe(750);
		expect(pollDelay(5)).toBe(10000);
	});
});
