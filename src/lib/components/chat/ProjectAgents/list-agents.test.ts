import { describe, expect, it } from 'vitest';

import {
	CompanionError,
	type CompanionDirectoryEntry,
	type CompanionGrant
} from '$lib/apis/companion';
import type { ProjectFolderComputer } from '$lib/apis/subscriptions';
import type { ProjectTarget } from '$lib/components/chat/ProjectPanel/project-session';

import {
	agentReadProblem,
	agentsFromFiles,
	agentTarget,
	listProjectAgents,
	MAX_AGENT_FILES,
	type AgentReader
} from './list-agents';

const workPc: ProjectFolderComputer = {
	machine_id: 'pc-1',
	name: 'Work PC',
	kind: 'app',
	online: true,
	platform: 'win32',
	can_browse: true,
	providers: [],
	recent_paths: []
};

const target: ProjectTarget = { computer: workPc, path: 'C:\\code\\site', chooseAgain: false };

const codeGrant: CompanionGrant = {
	id: 'grant-code',
	name: 'code',
	path: 'C:\\code',
	capabilities: { read: true, write: false, execute: false },
	available: true,
	unavailableReason: null
};

function file(name: string, folder: string): CompanionDirectoryEntry {
	return { name, path: `${folder}/${name}`, type: 'file' };
}

/** A computer sharing C:\code, with the given folders and files below it. */
function fakeComputer(
	folders: Record<string, CompanionDirectoryEntry[]>,
	files: Record<string, string | CompanionError>,
	grants: CompanionGrant[] = [codeGrant]
) {
	const reads: string[] = [];
	const reader: AgentReader = {
		async getGrants() {
			return grants;
		},
		async getDirectory(grantId: string, path = '') {
			expect(grantId).toBe('grant-code');
			const entries = folders[path];
			if (!entries)
				throw new CompanionError('The requested file or directory does not exist.', 404);
			return { path, entries };
		},
		async getFile(grantId: string, path: string) {
			expect(grantId).toBe('grant-code');
			reads.push(path);
			const content = files[path];
			if (content instanceof CompanionError) throw content;
			if (content === undefined) throw new CompanionError('Missing', 404);
			return { path, content, version: 'v1' };
		}
	};
	return { reader, reads };
}

describe('listProjectAgents', () => {
	it('lists agents from .github/agents, .claude/agents and old chat modes', async () => {
		const { reader } = fakeComputer(
			{
				'site/.github/agents': [
					file('reviewer.agent.md', 'site/.github/agents'),
					file('notes.txt', 'site/.github/agents'),
					{ name: 'drafts', path: 'site/.github/agents/drafts', type: 'directory' }
				],
				'site/.claude/agents': [file('tester.md', 'site/.claude/agents')],
				'site/.github/chatmodes': [
					file('plan.chatmode.md', 'site/.github/chatmodes'),
					file('README.md', 'site/.github/chatmodes')
				]
			},
			{
				'site/.github/agents/reviewer.agent.md': '---\ndescription: Reviews\n---\nReview it.',
				'site/.claude/agents/tester.md': '---\nname: Tester\n---\nTest it.',
				'site/.github/chatmodes/plan.chatmode.md': 'Plan it.'
			}
		);
		const listing = await listProjectAgents(reader, target);
		expect(listing).toEqual({
			status: 'ready',
			leftOut: 0,
			agents: [
				{
					name: 'plan',
					description: '',
					path: '.github/chatmodes/plan.chatmode.md',
					instructions: 'Plan it.'
				},
				{
					name: 'reviewer',
					description: 'Reviews',
					path: '.github/agents/reviewer.agent.md',
					instructions: 'Review it.'
				},
				{
					name: 'Tester',
					description: '',
					path: '.claude/agents/tester.md',
					instructions: 'Test it.'
				}
			]
		});
	});

	it('finds agents when the project folder is the shared folder itself', async () => {
		const siteGrant = { ...codeGrant, path: 'C:\\code\\site' };
		const { reader } = fakeComputer(
			{ '.github/agents': [file('a.agent.md', '.github/agents')] },
			{ '.github/agents/a.agent.md': 'A' },
			[siteGrant]
		);
		const listing = await listProjectAgents(reader, target);
		expect(listing.status === 'ready' && listing.agents.map((agent) => agent.path)).toEqual([
			'.github/agents/a.agent.md'
		]);
	});

	it('says so when the computer does not share the project folder', async () => {
		const elsewhere = { ...codeGrant, path: 'D:\\other' };
		const unreadable = {
			...codeGrant,
			capabilities: { read: false, write: false, execute: false }
		};
		expect(await listProjectAgents(fakeComputer({}, {}, [elsewhere]).reader, target)).toEqual({
			status: 'not_shared'
		});
		expect(await listProjectAgents(fakeComputer({}, {}, [unreadable]).reader, target)).toEqual({
			status: 'not_shared'
		});
	});

	it('passes over files that are refused, too large or not text', async () => {
		const folder = 'site/.github/agents';
		const { reader } = fakeComputer(
			{
				[folder]: [
					file('big.agent.md', folder),
					file('binary.agent.md', folder),
					file('ok.agent.md', folder)
				]
			},
			{
				[`${folder}/big.agent.md`]: new CompanionError('Files are limited to 1 MiB.', 413),
				[`${folder}/binary.agent.md`]: new CompanionError('Only UTF-8 text files.', 415),
				[`${folder}/ok.agent.md`]: 'Fine.'
			}
		);
		const listing = await listProjectAgents(reader, target);
		expect(listing.status === 'ready' && listing.agents.map((agent) => agent.name)).toEqual(['ok']);
	});

	it('leaves out agents hidden from the picker', async () => {
		const folder = 'site/.github/agents';
		const { reader } = fakeComputer(
			{ [folder]: [file('helper.agent.md', folder)] },
			{ [`${folder}/helper.agent.md`]: '---\nuser-invocable: false\n---\nHelp.' }
		);
		expect(await listProjectAgents(reader, target)).toEqual({
			status: 'ready',
			agents: [],
			leftOut: 0
		});
	});

	it('reads at most MAX_AGENT_FILES files and counts the rest', async () => {
		const folder = 'site/.github/agents';
		const entries: CompanionDirectoryEntry[] = [];
		const files: Record<string, string> = {};
		for (let index = 0; index < MAX_AGENT_FILES + 3; index++) {
			const name = `agent-${String(index).padStart(3, '0')}.agent.md`;
			entries.push(file(name, folder));
			files[`${folder}/${name}`] = 'Do it.';
		}
		const { reader, reads } = fakeComputer({ [folder]: entries }, files);
		const listing = await listProjectAgents(reader, target);
		expect(reads).toHaveLength(MAX_AGENT_FILES);
		expect(listing.status === 'ready' && listing.leftOut).toBe(3);
	});

	it('fails when the computer cannot be reached, so the list can be tried again', async () => {
		const folder = 'site/.github/agents';
		const { reader } = fakeComputer(
			{ [folder]: [file('a.agent.md', folder)] },
			{ [`${folder}/a.agent.md`]: new CompanionError('Work PC is offline.', 503) }
		);
		await expect(listProjectAgents(reader, target)).rejects.toThrow('Work PC is offline.');
	});
});

describe('agentTarget', () => {
	const server: ProjectFolderComputer = {
		...workPc,
		machine_id: 'local',
		name: 'This server',
		kind: 'server',
		online: true,
		providers: [{ id: 'claude', name: 'Claude', access: 'full' }]
	};
	const onServer: ProjectTarget = {
		computer: server,
		path: 'C:\\baseball\\src',
		chooseAgain: false
	};
	const offlinePc: ProjectTarget = { ...target, computer: { ...workPc, online: false } };
	const addressPc: ProjectTarget = { ...target, computer: { ...workPc, kind: 'address' } };
	const cloud: ProjectTarget = { ...target, computer: { ...workPc, kind: 'cloud' } };

	it('reads This server itself, unless it was set up again', () => {
		expect(agentReadProblem(onServer)).toBeNull();
		expect(agentReadProblem({ ...onServer, chooseAgain: true })).toBe('choose_again');
		expect(agentReadProblem(target)).toBeNull();
		expect(agentReadProblem(offlinePc)).toBe('offline');
		expect(agentReadProblem(cloud)).toBe('not_browsable');
	});

	it('prefers a computer it can read now, then one that runs a plan', () => {
		expect(agentTarget([offlinePc, onServer])).toBe(onServer);
		expect(agentTarget([target, onServer])).toBe(onServer);
		expect(agentTarget([cloud, target])).toBe(target);
	});

	it('falls back to one that could be read, so the menu can say why', () => {
		expect(agentTarget([cloud, offlinePc])).toBe(offlinePc);
		expect(agentTarget([addressPc])).toBe(addressPc);
	});

	it('has none when no computer can ever be read', () => {
		expect(agentTarget([cloud])).toBeNull();
		expect(agentTarget([])).toBeNull();
	});
});

describe('agentsFromFiles', () => {
	it('parses the files, leaves out hidden agents and sorts the rest', () => {
		const agents = agentsFromFiles([
			{ path: '.github/agents/tester.agent.md', content: 'Test.' },
			{ path: '.github/agents/helper.agent.md', content: '---\nuser-invocable: false\n---\nHelp.' },
			{ path: '.claude/agents/planner.md', content: '---\nname: Planner\n---\nPlan.' }
		]);
		expect(agents.map((agent) => [agent.name, agent.path])).toEqual([
			['Planner', '.claude/agents/planner.md'],
			['tester', '.github/agents/tester.agent.md']
		]);
	});
});
