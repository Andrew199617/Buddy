// Rules for the chat side panel's Project tab: which project folders it can
// open, how far its file browser may go, and how it reads command results.
// No requests here.

import { CompanionError, type CompanionGrant, type CompanionTerminal } from '$lib/apis/companion';
import type {
	ChatRunTargetDetails,
	FolderProject,
	ProjectFolderComputer,
	RunTargetComputer
} from '$lib/apis/subscriptions';
import { effectiveChoice } from '$lib/components/projectFolders/project-folders';

export type ProjectTarget = {
	computer: ProjectFolderComputer;
	path: string;
	// The computer was set up again after the folder was chosen; chats refuse to start there.
	chooseAgain: boolean;
};

/**
 * The chat folder's exact project folders, one per computer that has one. The folder's
 * chosen computer comes first, since new chats start there.
 */
export function projectTargets(project: FolderProject): ProjectTarget[] {
	const choice = effectiveChoice(project);
	if (choice.mode !== 'folder') return [];
	const targets: ProjectTarget[] = [];
	for (const computer of project.computers) {
		const saved = choice.paths[computer.machine_id];
		if (!saved) continue;
		targets.push({ computer, path: saved.path, chooseAgain: saved.problem !== null });
	}
	const chosen = targets.filter((target) => target.computer.machine_id === choice.machineId);
	const others = targets.filter((target) => target.computer.machine_id !== choice.machineId);
	return [...chosen, ...others];
}

/**
 * The exact folder a started chat works in, as its Project tab opens it. Null when the chat
 * runs Detached or lets the AI choose; the tab then shows the chat folder's project folder.
 */
export function chatProjectTarget(
	details: ChatRunTargetDetails | null,
	computers: RunTargetComputer[]
): ProjectTarget | null {
	if (!details || details.kind !== 'computer' || details.folder_mode !== 'path' || !details.path) {
		return null;
	}
	const computer = computers.find((candidate) => candidate.machine_id === details.machine_id);
	if (!computer) return null;
	return {
		computer: { ...computer, providers: [] },
		path: details.path,
		chooseAgain: details.problem === 'reset'
	};
}

/** Puts a computer that runs a plan first, since that is where chats work. */
export function preferredTarget(targets: ProjectTarget[]): ProjectTarget | null {
	const working = targets.find((target) => target.computer.providers.length > 0);
	return working ?? targets[0] ?? null;
}

// 'needs_app': a Runner added by address, which could get the Buddy app.
// 'not_browsable': This server, which never can.
export type TargetProblem = 'choose_again' | 'needs_app' | 'not_browsable' | 'offline' | null;

/** Only computers connected through the Buddy app can be opened from the chat. */
export function targetProblem(target: ProjectTarget): TargetProblem {
	if (target.chooseAgain) return 'choose_again';
	if (target.computer.kind === 'address') return 'needs_app';
	if (target.computer.kind !== 'app') return 'not_browsable';
	if (!target.computer.online) return 'offline';
	return null;
}

/** A shared folder the computer still has and lets this page read. */
export function isReadableGrant(grant: CompanionGrant): boolean {
	return grant.available !== false && grant.capabilities.read;
}

/** The folder above `current`, or null at the project folder itself. Paths are '/'-separated. */
export function parentWithinProject(current: string, root: string): string | null {
	if (current === root) return null;
	const parts = current.split('/').filter(Boolean);
	parts.pop();
	const parent = parts.join('/');
	if (root && parent.length < root.length) return root;
	return parent;
}

export type Crumb = { name: string; path: string };

/** The folders from the project folder down to `current`, for the breadcrumb. */
export function crumbsBelow(current: string, root: string): Crumb[] {
	let below = current;
	if (root) below = current.slice(root.length);
	const crumbs: Crumb[] = [];
	let path = root;
	for (const name of below.split('/').filter(Boolean)) {
		if (path) path = `${path}/${name}`;
		else path = name;
		crumbs.push({ name, path });
	}
	return crumbs;
}

/** True when `path` is the project folder or inside it. */
export function isWithinProject(path: string, root: string): boolean {
	if (!root) return true;
	return path === root || path.startsWith(`${root}/`);
}

/** Worth retrying: Buddy or the computer was briefly unreachable, busy or slow. */
export function isTransientFailure(cause: unknown): boolean {
	if (!(cause instanceof CompanionError)) return true;
	return cause.status >= 500 || cause.status === 408 || cause.status === 429;
}

/** A request whose reply was lost may still have run on the computer. */
export function mayHaveRun(cause: unknown): boolean {
	if (!(cause instanceof CompanionError)) return true;
	return cause.status >= 500;
}

const TERMINAL_STATES = ['ready', 'running', 'exited', 'closed'];

/** Throws unless the computer's reply describes this command session. */
export function checkTerminalReply(
	reply: CompanionTerminal,
	terminalId: string,
	workspaceId: string
): CompanionTerminal {
	const valid =
		reply?.id === terminalId &&
		reply.workspaceId === workspaceId &&
		TERMINAL_STATES.includes(reply.status) &&
		typeof reply.output === 'string' &&
		(reply.exitCode === null || Number.isInteger(reply.exitCode));
	if (!valid) {
		throw new CompanionError('The computer did not confirm this command session status.', 502);
	}
	return reply;
}

/** One argument per line; blank lines are left out. */
export function parseArguments(text: string): string[] {
	const args: string[] = [];
	for (const line of text.split(/\r?\n/)) {
		if (line.trim() === '') continue;
		args.push(line);
	}
	return args;
}

// The output view keeps the latest 256 KiB, like the computer does.
export const OUTPUT_LIMIT = 262144;

export function visibleOutput(output: string): string {
	if (output.length <= OUTPUT_LIMIT) return output;
	return output.slice(-OUTPUT_LIMIT);
}

/** Delay before the next status check, backing off after failures up to 10 seconds. */
export function pollDelay(failures: number): number {
	return Math.min(10000, 750 * 2 ** failures);
}
