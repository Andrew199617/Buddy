// The sections of the bar's folder menu, filtered by what the person typed.
// Pure helpers; the menu checks every pick on its computer.

import type { FolderSuggestion, RunTargetOptions } from '$lib/apis/subscriptions';

export const MAX_RECENT_ELSEWHERE = 3;

export type ChatFolderEntry = { path: string; isDefault: boolean };
export type ElsewhereEntry = { machineId: string; path: string };

export type FolderSections = {
	inChatFolder: ChatFolderEntry[];
	recentHere: string[];
	recentElsewhere: ElsewhereEntry[];
	fromAgents: FolderSuggestion[];
};

type SectionInputs = {
	options: RunTargetOptions;
	suggested: FolderSuggestion[];
	machineId: string;
	query: string;
	platform: string | null;
};

/** Windows paths compare without case; others exactly. */
function pathKey(path: string, platform: string | null): string {
	if (platform === 'win32') return path.toLowerCase();
	return path;
}

function matches(path: string, query: string): boolean {
	if (!query) return true;
	return path.toLowerCase().includes(query.toLowerCase());
}

export function buildFolderSections(inputs: SectionInputs): FolderSections {
	const { options, suggested, machineId, platform } = inputs;
	const query = inputs.query.trim();
	const seen = new Set<string>();

	function take(path: string): boolean {
		const key = pathKey(path, platform);
		if (seen.has(key) || !matches(path, query)) return false;
		seen.add(key);
		return true;
	}

	const inChatFolder: ChatFolderEntry[] = [];
	const chatFolder = options.chat_folder;
	const folderDefault = chatFolder?.paths?.[machineId];
	if (folderDefault && take(folderDefault)) {
		inChatFolder.push({ path: folderDefault, isDefault: true });
	}
	for (const path of chatFolder?.offered?.[machineId] ?? []) {
		if (take(path)) inChatFolder.push({ path, isDefault: false });
	}

	const recentHere: string[] = [];
	for (const entry of options.recent ?? []) {
		if (entry.machine_id === machineId && take(entry.path)) recentHere.push(entry.path);
	}
	const computer = options.computers?.find((candidate) => candidate.machine_id === machineId);
	for (const path of computer?.recent_paths ?? []) {
		if (take(path)) recentHere.push(path);
	}

	const recentElsewhere: ElsewhereEntry[] = [];
	for (const entry of options.recent ?? []) {
		if (recentElsewhere.length >= MAX_RECENT_ELSEWHERE) break;
		if (entry.machine_id === machineId || !matches(entry.path, query)) continue;
		const duplicate = recentElsewhere.some(
			(other) => other.machineId === entry.machine_id && other.path === entry.path
		);
		if (!duplicate) recentElsewhere.push({ machineId: entry.machine_id, path: entry.path });
	}

	const fromAgents: FolderSuggestion[] = [];
	for (const suggestion of suggested) {
		if (take(suggestion.path)) fromAgents.push(suggestion);
	}
	return { inChatFolder, recentHere, recentElsewhere, fromAgents };
}

/** Text that is a full path on that computer, so the menu offers "Use" for it. */
export function looksLikePath(text: string, platform: string | null): boolean {
	const trimmed = text.trim();
	if (!trimmed) return false;
	const windows = /^[A-Za-z]:[\\/]/.test(trimmed);
	const posix = trimmed.startsWith('/') || trimmed.startsWith('~');
	if (platform === 'win32') return windows;
	if (platform === null) return windows || posix;
	return posix;
}
