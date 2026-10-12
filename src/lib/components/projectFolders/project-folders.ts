// What a chat folder's project folder choice means, for the folder dialog, the
// folder page chip and the chat's project panel. No requests here.

import type {
	FolderProject,
	ProjectFolderChoice,
	ProjectFolderComputer,
	ProjectFolderMode
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
};

export function draftFromProject(project: FolderProject): ProjectDraft {
	if (!project.own) {
		if (waitsForOwnChoice(project)) return { mode: 'unchosen', paths: {} };
		if (project.inherited) return { mode: 'inherit', paths: {} };
		return { mode: 'auto', paths: {} };
	}
	const paths: Record<string, string> = {};
	for (const [machineId, saved] of Object.entries(project.own.paths)) paths[machineId] = saved.path;
	return { mode: project.own.mode, paths };
}

export function sameDraft(first: ProjectDraft, second: ProjectDraft): boolean {
	if (first.mode !== second.mode) return false;
	const firstKeys = Object.keys(first.paths).sort();
	const secondKeys = Object.keys(second.paths).sort();
	if (firstKeys.join('\n') !== secondKeys.join('\n')) return false;
	return firstKeys.every((key) => first.paths[key] === second.paths[key]);
}
