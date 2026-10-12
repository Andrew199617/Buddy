// The person's main chat: one always-on chat with a rolling context window, so it can run for as
// long as they like. It runs Detached, and compacts its older messages once it grows past a
// threshold. Its id is kept in the person's settings, so every device opens the same chat.

import { chatExists, createNewChat } from '$lib/apis/chats';
import { updateUserSettings } from '$lib/apis/users';

// Older messages are summarised once the chat's context grows past this many tokens.
export const MAIN_CHAT_COMPACTION_TOKENS = 60000;

type MainChatDependencies = {
	token: string;
	mainChatId: string | null | undefined;
	modelIds: string[];
	title: string;
	onSaved: (mainChatId: string) => void;
};

/**
 * The main chat's id: the saved one while it still exists, otherwise a new one, saved for next
 * time. A failed check throws instead, so a brief outage never replaces the saved main chat.
 */
export async function ensureMainChat(dependencies: MainChatDependencies): Promise<string> {
	const { token, mainChatId, modelIds, title, onSaved } = dependencies;
	if (mainChatId && (await chatExists(token, mainChatId))) return mainChatId;
	const created = await createNewChat(
		token,
		{
			title,
			models: modelIds,
			params: { compact_token_threshold: MAIN_CHAT_COMPACTION_TOKENS },
			history: { messages: {}, currentId: null },
			messages: [],
			tags: [],
			timestamp: Date.now()
		},
		null,
		null,
		{ kind: 'detached', access: 'chat' }
	);
	if (!created?.id) throw new Error('Buddy could not start your main chat.');
	await updateUserSettings(token, { ui: { mainChatId: created.id } });
	onSaved(created.id);
	return created.id;
}
