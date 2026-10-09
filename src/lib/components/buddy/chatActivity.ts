import { getOutputText, type OutputItem } from '../chat/Messages/structuredOutput';

export type BuddyActivity = 'idle' | 'thinking' | 'responding';

type BuddyMessage = {
	role?: string;
	parentId?: string | null;
	childrenIds?: string[];
	done?: boolean;
	content?: string;
	output?: OutputItem[];
	merged?: { status?: boolean; content?: string };
};

type BuddyHistory = {
	currentId: string | null;
	messages: Record<string, BuddyMessage>;
};

function visibleLegacyAnswer(content = ''): string {
	const withoutDetails = content.replace(/<details\b[^>]*>[\s\S]*?<\/details>/gi, '');
	const unfinishedDetails = withoutDetails.search(/<details\b/i);
	if (unfinishedDetails !== -1) {
		return withoutDetails.slice(0, unfinishedDetails).trim();
	}
	return withoutDetails.trim();
}

function hasVisibleAnswer(message: BuddyMessage): boolean {
	if (message.output?.length) {
		return Boolean(getOutputText(message.output).trim());
	}
	return Boolean(visibleLegacyAnswer(message.content));
}

export function getBuddyActivity(
	history: BuddyHistory,
	generating: boolean,
	taskIds: string[] | null
): BuddyActivity {
	if (!generating && !taskIds?.length) {
		return 'idle';
	}
	const current = history.currentId ? history.messages[history.currentId] : undefined;
	if (generating && current?.merged?.status) {
		if (visibleLegacyAnswer(current.merged.content)) {
			return 'responding';
		}
		return 'thinking';
	}

	let parent = current;
	if (current?.role === 'assistant' && current.parentId) {
		parent = history.messages[current.parentId];
	}
	const activeResponses: BuddyMessage[] = [];
	if (parent?.childrenIds?.length) {
		for (const id of parent.childrenIds) {
			const candidate = history.messages[id];
			if (candidate?.role === 'assistant' && candidate.done !== true) {
				activeResponses.push(candidate);
			}
		}
	} else if (current?.role === 'assistant' && current.done !== true) {
		activeResponses.push(current);
	}
	if (activeResponses.some(hasVisibleAnswer)) {
		return 'responding';
	}
	if (activeResponses.length || generating || !current || current.role === 'user') {
		return 'thinking';
	}
	return 'idle';
}
