// Finds the custom agents in a chat folder's project folder. On a computer
// connected with the Buddy app they are read through the relay, the way the
// Project tab reads files, and the computer still decides which folders are
// shared. On This server, Buddy reads them itself.

import { CompanionError, type CompanionGrant, type WorkspaceClient } from '$lib/apis/companion';
import type { AgentFileText } from '$lib/apis/subscriptions';
import {
	isReadableGrant,
	preferredTarget,
	targetProblem,
	type ProjectTarget,
	type TargetProblem
} from '$lib/components/chat/ProjectPanel/project-session';
import { grantContaining } from '$lib/components/projectFolders/project-folders';

import { AGENT_FOLDERS, isAgentFile, parseAgentFile, sortAgents } from './agent-files';
import type { ChatAgent } from './chat-agent';

// Agent files after this many are left out, so a crowded folder stays quick.
export const MAX_AGENT_FILES = 50;

export type AgentReader = Pick<WorkspaceClient, 'getGrants' | 'getDirectory' | 'getFile'>;

export type AgentListing =
	| { status: 'ready'; agents: ChatAgent[]; leftOut: number }
	| { status: 'not_shared' };

/** Why agents can't be read from this computer now, or null when they can. */
export function agentReadProblem(target: ProjectTarget): TargetProblem {
	if (target.chooseAgain) return 'choose_again';
	// Buddy reads This server itself, so it needs no relay.
	if (target.computer.kind === 'server') return null;
	return targetProblem(target);
}

/**
 * The computer to read agents from: one that can be read now, preferring one
 * that runs a plan. Otherwise one that could be, so the menu can say what to
 * do; null when none ever can.
 */
export function agentTarget(targets: ProjectTarget[]): ProjectTarget | null {
	const readable = targets.filter((target) => agentReadProblem(target) === null);
	if (readable.length > 0) return preferredTarget(readable);
	const fixable = targets.filter((target) => agentReadProblem(target) !== 'not_browsable');
	return preferredTarget(fixable);
}

/** The agents these files describe, without the ones hidden from the picker, sorted. */
export function agentsFromFiles(files: AgentFileText[]): ChatAgent[] {
	const agents: ChatAgent[] = [];
	for (const file of files) {
		const agent = parseAgentFile(file.content, file.path);
		if (agent) agents.push(agent);
	}
	return sortAgents(agents);
}

type AgentFile = {
	// Relative to the shared folder, for the computer.
	sharedPath: string;
	// Relative to the project folder, for people and the model.
	projectPath: string;
};

/** A folder or file that is missing, refused, too large or not text, so it is passed over. */
function isPassedOver(cause: unknown): boolean {
	if (!(cause instanceof CompanionError)) return false;
	return [403, 404, 413, 415].includes(cause.status);
}

function joinPath(parent: string, child: string): string {
	if (parent === '') return child;
	return `${parent}/${child}`;
}

async function agentFilesIn(
	reader: AgentReader,
	grant: CompanionGrant,
	projectRoot: string,
	folder: string
): Promise<AgentFile[]> {
	let listing: Awaited<ReturnType<AgentReader['getDirectory']>>;
	try {
		listing = await reader.getDirectory(grant.id, joinPath(projectRoot, folder));
	} catch (cause) {
		if (isPassedOver(cause)) return [];
		throw cause;
	}
	const files: AgentFile[] = [];
	for (const entry of listing.entries) {
		if (entry.type !== 'file' || entry.name.includes('/')) continue;
		if (!isAgentFile(folder, entry.name)) continue;
		const projectPath = `${folder}/${entry.name}`;
		files.push({ sharedPath: joinPath(projectRoot, projectPath), projectPath });
	}
	return files;
}

async function readAgentFile(
	reader: AgentReader,
	grant: CompanionGrant,
	file: AgentFile
): Promise<AgentFileText | null> {
	try {
		const read = await reader.getFile(grant.id, file.sharedPath);
		return { path: file.projectPath, content: read.content };
	} catch (cause) {
		if (isPassedOver(cause)) return null;
		throw cause;
	}
}

/** The agents in the target's project folder, or 'not_shared' when the computer doesn't share it. */
export async function listProjectAgents(
	reader: AgentReader,
	target: ProjectTarget
): Promise<AgentListing> {
	const grants = await reader.getGrants();
	const match = grantContaining(
		grants.filter(isReadableGrant),
		target.path,
		target.computer.platform
	);
	if (!match) return { status: 'not_shared' };

	const perFolder = await Promise.all(
		AGENT_FOLDERS.map((folder) => agentFilesIn(reader, match.grant, match.relative, folder))
	);
	const files = perFolder.flat();
	const kept = files.slice(0, MAX_AGENT_FILES);

	const read = await Promise.all(kept.map((file) => readAgentFile(reader, match.grant, file)));
	const texts: AgentFileText[] = [];
	for (const text of read) {
		if (text) texts.push(text);
	}
	return { status: 'ready', agents: agentsFromFiles(texts), leftOut: files.length - kept.length };
}
