// Finds the custom agents in a chat folder's project folder, reading them from
// the computer the way the Project tab does. The computer still decides which
// folders are shared.

import { CompanionError, type CompanionGrant, type WorkspaceClient } from '$lib/apis/companion';
import {
	isReadableGrant,
	type ProjectTarget
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

async function readAgent(
	reader: AgentReader,
	grant: CompanionGrant,
	file: AgentFile
): Promise<ChatAgent | null> {
	try {
		const read = await reader.getFile(grant.id, file.sharedPath);
		return parseAgentFile(read.content, file.projectPath);
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

	const read = await Promise.all(kept.map((file) => readAgent(reader, match.grant, file)));
	const agents: ChatAgent[] = [];
	for (const agent of read) {
		if (agent) agents.push(agent);
	}
	return { status: 'ready', agents: sortAgents(agents), leftOut: files.length - kept.length };
}
