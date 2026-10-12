// What a chat folder's project folder choice means, for the folder dialog, the
// folder page chip and the chat's project panel, and where keyboard focus goes
// in the folder settings. No requests here.

import type {
	FolderPlan,
	FolderProject,
	FolderProjectChoice,
	ProjectFolderChoice,
	ProjectFolderComputer,
	ProjectFolderMode,
	SubscriptionAccess
} from '$lib/apis/subscriptions';

export type EffectiveChoice = {
	mode: ProjectFolderMode;
	paths: ProjectFolderChoice['paths'];
	// The parent folder the choice comes from, or '' when it is the folder's own or the default.
	fromFolderName: string;
};

/** Chats stay chat-only until the person chooses, because others can change the folder's instructions. */
export function waitsForOwnChoice(project: FolderProject): boolean {
	return project.shared_instructions && project.own === null;
}

/** The folder's own choice, else its parent's, else "Let the AI choose". */
export function effectiveChoice(project: FolderProject): EffectiveChoice {
	if (project.own) {
		return { mode: project.own.mode, paths: project.own.paths, fromFolderName: '' };
	}
	if (waitsForOwnChoice(project)) {
		return { mode: 'chat', paths: {}, fromFolderName: '' };
	}
	if (project.inherited) {
		return {
			mode: project.inherited.mode,
			paths: project.inherited.paths,
			fromFolderName: project.inherited.folder_name
		};
	}
	return { mode: 'auto', paths: {}, fromFolderName: '' };
}

export function lastSegment(path: string): string {
	const parts = path.split(/[\\/]/).filter(Boolean);
	if (parts.length === 0) return path;
	return parts[parts.length - 1];
}

/** Computers that run a plan which may use files. */
function workingComputers(project: FolderProject): ProjectFolderComputer[] {
	return project.computers.filter((computer) => computer.providers.length > 0);
}

/**
 * Something needs choosing again: a computer was set up again, the computers
 * with folders were all removed, or one running a plan has no folder.
 */
export function needsAttention(project: FolderProject): boolean {
	const choice = effectiveChoice(project);
	if (choice.mode !== 'folder') return false;
	if (Object.keys(choice.paths).length === 0) return true;
	if (Object.values(choice.paths).some((saved) => saved.problem !== null)) return true;
	return workingComputers(project).some((computer) => !choice.paths[computer.machine_id]);
}

export type ChipState = { label: string; tone: 'normal' | 'attention'; fromFolderName: string };

/** The short label shown for the folder; the caller translates the fixed phrases. */
export function chipState(project: FolderProject): ChipState {
	const choice = effectiveChoice(project);
	const fromFolderName = choice.fromFolderName;
	if (needsAttention(project)) {
		return { label: 'Project folder needs attention', tone: 'attention', fromFolderName };
	}
	if (choice.mode === 'chat') {
		return { label: 'Just chat, no files', tone: 'normal', fromFolderName };
	}
	if (choice.mode === 'auto') {
		return { label: 'AI chooses where to work', tone: 'normal', fromFolderName };
	}
	const entries = Object.entries(choice.paths);
	const [machineId, saved] = entries[0];
	const computer = project.computers.find((candidate) => candidate.machine_id === machineId);
	let label = lastSegment(saved.path);
	if (computer) label = `${computer.name} · ${label}`;
	if (entries.length > 1) label = `${label} +${entries.length - 1}`;
	return { label, tone: 'normal', fromFolderName };
}

/** With Read files, Claude reads only inside the folder it starts in. */
export function claudeReadHint(project: FolderProject): boolean {
	if (effectiveChoice(project).mode !== 'auto') return false;
	return project.computers.some((computer) =>
		computer.providers.some((provider) => provider.id === 'claude' && provider.access === 'read')
	);
}

export function anyFullAccess(project: FolderProject): boolean {
	return project.computers.some((computer) =>
		computer.providers.some((provider) => provider.access === 'full')
	);
}

// What a Chat-only plan can be allowed from the folder settings.
export type FileAccess = 'read' | 'full';

/** Plans that are on but Chat only, on a computer they can use: the folder's settings can let them use files. */
export function plansToAllow(project: FolderProject): FolderPlan[] {
	return project.plans.filter(
		(plan) => plan.enable && plan.access === 'chat' && plan.machine !== null
	);
}

/** Plans that are on and may use files, but have no computer they can use. */
export function plansWithoutComputer(project: FolderProject): FolderPlan[] {
	return project.plans.filter(
		(plan) => plan.enable && plan.access !== 'chat' && plan.machine === null
	);
}

// 'ready': some plan can open folders. Otherwise what is missing: a Chat-only
// plan to 'allow', a 'computer' for the plans that are on, or a plan to 'turn_on'.
export type FileAccessNeed = 'ready' | 'allow' | 'computer' | 'turn_on';

export function fileAccessNeed(project: FolderProject): FileAccessNeed {
	if (workingComputers(project).length > 0) return 'ready';
	if (plansToAllow(project).length > 0) return 'allow';
	if (project.has_plans) return 'computer';
	return 'turn_on';
}

/** Whether keyboard focus fell to the page, because what had it was removed or disabled. */
export function focusWasLost(): boolean {
	const active = document.activeElement;
	return !active || active === document.body || !active.isConnected;
}

export function pathPlaceholder(platform: string | null): string {
	if (platform === 'win32') return 'C:\\Users\\you\\code\\project';
	return '/home/you/code/project';
}

function separatorFor(platform: string | null): string {
	if (platform === 'win32') return '\\';
	return '/';
}

/** A folder inside a shared folder, from the shared folder's path and a '/'-separated relative path. */
export function joinSharedPath(
	grantPath: string,
	relative: string,
	platform: string | null
): string {
	if (!relative) return grantPath;
	const separator = separatorFor(platform);
	const root = grantPath.replace(/[\\/]+$/, '');
	return `${root}${separator}${relative.split('/').filter(Boolean).join(separator)}`;
}

/** The '/'-separated relative path one level up, stopping at the shared folder itself. */
export function parentWithinRoot(relative: string): string {
	const parts = relative.split('/').filter(Boolean);
	parts.pop();
	return parts.join('/');
}

export type SharedFolder = { id: string; path: string };

/** The shared folder that contains a path, and the path relative to it, if any. */
export function grantContaining<T extends SharedFolder>(
	grants: T[],
	path: string,
	platform: string | null
): { grant: T; relative: string } | null {
	const windows = platform === 'win32';
	const normalize = (value: string) => {
		let normalized = value.replace(/[\\/]+$/, '');
		if (windows) normalized = normalized.replace(/\//g, '\\').toLowerCase();
		return normalized;
	};
	const separator = separatorFor(platform);
	const target = normalize(path);
	let best: { grant: T; relative: string } | null = null;
	for (const grant of grants) {
		const root = normalize(grant.path);
		let relative: string | null = null;
		if (target === root) relative = '';
		else if (target.startsWith(root + separator)) relative = target.slice(root.length + 1);
		if (relative === null) continue;
		// The deepest shared folder wins; keep the original case of the relative part.
		if (best === null || grant.path.length > best.grant.path.length) {
			const original = path.replace(/[\\/]+$/, '');
			const originalRelative = original.slice(original.length - relative.length);
			best = { grant, relative: originalRelative.split(/[\\/]/).filter(Boolean).join('/') };
		}
	}
	return best;
}

export type ProjectDraft = {
	// 'inherit': no own choice, follow the parent folder.
	// 'unchosen': no own choice yet in a folder whose instructions others can change.
	mode: ProjectFolderMode | 'inherit' | 'unchosen';
	paths: Record<string, string>;
	// The computer new chats start on; '' lets Buddy pick one.
	machineId: string;
	// '' follows each plan's default access.
	access: SubscriptionAccess | '';
	// More folders new chats can pick from, per computer.
	offered: Record<string, string[]>;
};

/** A draft with no own choice beyond its mode. */
export function emptyDraft(mode: ProjectDraft['mode']): ProjectDraft {
	return { mode, paths: {}, machineId: '', access: '', offered: {} };
}

export function draftFromProject(project: FolderProject): ProjectDraft {
	if (!project.own) {
		if (waitsForOwnChoice(project)) return emptyDraft('unchosen');
		// No own choice: new chats follow the parent folder, or start detached without one.
		return emptyDraft('inherit');
	}
	const own = project.own;
	const paths: Record<string, string> = {};
	for (const [machineId, saved] of Object.entries(own.paths)) paths[machineId] = saved.path;
	const offered: Record<string, string[]> = {};
	for (const [machineId, entries] of Object.entries(own.offered ?? {})) {
		offered[machineId] = entries.map((entry) => entry.path);
	}
	return {
		mode: own.mode,
		paths,
		machineId: own.machine_id ?? '',
		access: own.access ?? '',
		offered
	};
}

export function copyDraft(draft: ProjectDraft): ProjectDraft {
	const offered: Record<string, string[]> = {};
	for (const [machineId, paths] of Object.entries(draft.offered)) offered[machineId] = [...paths];
	return { ...draft, paths: { ...draft.paths }, offered };
}

function samePaths(first: Record<string, string>, second: Record<string, string>): boolean {
	const firstKeys = Object.keys(first).sort();
	const secondKeys = Object.keys(second).sort();
	if (firstKeys.join('\n') !== secondKeys.join('\n')) return false;
	return firstKeys.every((key) => first[key] === second[key]);
}

function sameOffered(first: Record<string, string[]>, second: Record<string, string[]>): boolean {
	const firstKeys = Object.keys(first).sort();
	const secondKeys = Object.keys(second).sort();
	if (firstKeys.join('\n') !== secondKeys.join('\n')) return false;
	return firstKeys.every((key) => first[key].join('\n') === second[key].join('\n'));
}

export function sameDraft(first: ProjectDraft, second: ProjectDraft): boolean {
	if (first.mode !== second.mode) return false;
	if (first.machineId !== second.machineId || first.access !== second.access) return false;
	return samePaths(first.paths, second.paths) && sameOffered(first.offered, second.offered);
}

/**
 * The whole choice to save. The server replaces the folder's choice, so every
 * field goes, even unchanged ones. A computer that is no longer listed (removed
 * since) is dropped from where new chats start, since saving would be refused.
 */
export function choiceFromDraft(
	draft: ProjectDraft,
	mode: ProjectFolderMode,
	computers: ProjectFolderComputer[]
): FolderProjectChoice {
	// Detached: new chats start with no computer, folder or access to keep.
	if (mode === 'chat') return { mode, paths: {}, machine_id: '', access: '', offered: {} };
	let machineId = draft.machineId;
	const listed = computers.some((computer) => computer.machine_id === machineId);
	// This server stays saved while the policy turns it off for a while.
	if (!listed && machineId !== 'local') machineId = '';
	let paths: Record<string, string> = {};
	if (mode === 'folder') paths = { ...draft.paths };
	const offered: Record<string, string[]> = {};
	for (const [computerId, entries] of Object.entries(draft.offered)) {
		if (entries.length > 0) offered[computerId] = [...entries];
	}
	return { mode, paths, machine_id: machineId, access: draft.access, offered };
}

const ACCESS_ORDER: SubscriptionAccess[] = ['chat', 'read', 'full'];

/**
 * The access new chats start with when the folder follows the plans: the lowest
 * of the plans that are on. Null when no plan is on.
 */
export function planDefaultAccess(project: FolderProject): SubscriptionAccess | null {
	// Every computer lists the same plans, the ones that are on.
	const providers = project.computers[0]?.providers ?? [];
	let lowest: SubscriptionAccess | null = null;
	for (const provider of providers) {
		if (lowest === null || ACCESS_ORDER.indexOf(provider.access) < ACCESS_ORDER.indexOf(lowest)) {
			lowest = provider.access;
		}
	}
	return lowest;
}

// Where new chats in the folder start: following the parent folder, a computer
// Buddy picks, one computer by its machine ID, or detached. Null until chosen.
export const RUN_ON_INHERIT = 'inherit';
export const RUN_ON_AUTOMATIC = 'automatic';
export const RUN_ON_DETACHED = 'detached';

export function runOnValue(draft: ProjectDraft): string | null {
	if (draft.mode === 'unchosen') return null;
	if (draft.mode === 'inherit') return RUN_ON_INHERIT;
	if (draft.mode === 'chat') return RUN_ON_DETACHED;
	if (draft.machineId === '') return RUN_ON_AUTOMATIC;
	return draft.machineId;
}

/** The draft after choosing where new chats run; a folder choice already made is kept. */
export function draftWithRunOn(draft: ProjectDraft, value: string): ProjectDraft {
	if (value === RUN_ON_INHERIT) return { ...draft, mode: 'inherit' };
	if (value === RUN_ON_DETACHED) return { ...draft, mode: 'chat' };
	let machineId = value;
	if (value === RUN_ON_AUTOMATIC) machineId = '';
	let mode = draft.mode;
	// Coming from no choice or detached, the AI chooses the folder until one is picked.
	if (mode !== 'auto' && mode !== 'folder') mode = 'auto';
	return { ...draft, mode, machineId };
}

export type ProjectSummary =
	| { kind: 'inherit'; folderName: string }
	| { kind: 'unset' }
	| { kind: 'unchosen' }
	| { kind: 'detached' }
	| {
			kind: 'set';
			// The exact folder's last part, or null when the AI chooses.
			folder: string | null;
			// Null when Buddy picks the computer.
			computer: ProjectFolderComputer | null;
			access: SubscriptionAccess | '';
	  };

/** What the collapsed Project folder row says about a draft. */
export function projectSummary(draft: ProjectDraft, project: FolderProject): ProjectSummary {
	if (draft.mode === 'unchosen') return { kind: 'unchosen' };
	if (draft.mode === 'chat') return { kind: 'detached' };
	if (draft.mode === 'inherit') {
		if (project.inherited) return { kind: 'inherit', folderName: project.inherited.folder_name };
		return { kind: 'unset' };
	}
	let computer: ProjectFolderComputer | null = null;
	if (draft.machineId) {
		computer = project.computers.find((candidate) => candidate.machine_id === draft.machineId) ?? null;
	}
	let folder: string | null = null;
	if (draft.mode === 'folder') {
		let path = '';
		if (draft.machineId) path = draft.paths[draft.machineId] ?? '';
		if (!path) path = Object.values(draft.paths)[0] ?? '';
		if (path) folder = lastSegment(path);
	}
	return { kind: 'set', folder, computer, access: draft.access };
}

// Why a draft can't be saved yet: a specific folder is wanted but not chosen.
export type DraftProblem = { kind: 'needs_folder'; machineId: string } | null;

export function draftProblem(draft: ProjectDraft): DraftProblem {
	if (draft.mode !== 'folder') return null;
	if (draft.machineId && !draft.paths[draft.machineId]) {
		return { kind: 'needs_folder', machineId: draft.machineId };
	}
	if (Object.values(draft.paths).every((path) => !path)) {
		return { kind: 'needs_folder', machineId: '' };
	}
	return null;
}

export const MAX_OFFERED_PER_COMPUTER = 5;

/** The draft with one more folder offered on a computer; at most five, no repeats. */
export function draftWithOffered(draft: ProjectDraft, machineId: string, path: string): ProjectDraft {
	const current = draft.offered[machineId] ?? [];
	if (current.includes(path) || current.length >= MAX_OFFERED_PER_COMPUTER) return draft;
	return { ...draft, offered: { ...draft.offered, [machineId]: [...current, path] } };
}

export function draftWithoutOffered(
	draft: ProjectDraft,
	machineId: string,
	path: string
): ProjectDraft {
	const remaining = (draft.offered[machineId] ?? []).filter((entry) => entry !== path);
	const offered = { ...draft.offered };
	if (remaining.length > 0) offered[machineId] = remaining;
	else delete offered[machineId];
	return { ...draft, offered };
}

export type SuggestionTools = 'claude' | 'codex' | 'both';

/** Which CLIs worked in a suggested folder. */
export function suggestionTools(sources: string[]): SuggestionTools {
	const claude = sources.includes('claude');
	const codex = sources.includes('codex');
	if (claude && codex) return 'both';
	if (codex) return 'codex';
	return 'claude';
}

export type LastUsed = { unit: 'today' | 'yesterday' | 'days' | 'months'; count: number };

/** How long ago a folder was used, from day-rounded seconds. */
export function lastUsedAgo(lastUsed: number, nowSeconds: number): LastUsed {
	const days = Math.max(0, Math.floor((nowSeconds - lastUsed) / 86400));
	if (days === 0) return { unit: 'today', count: 0 };
	if (days === 1) return { unit: 'yesterday', count: 1 };
	if (days < 60) return { unit: 'days', count: days };
	return { unit: 'months', count: Math.floor(days / 30) };
}
