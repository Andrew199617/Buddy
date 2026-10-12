import { beforeEach, describe, expect, it, vi } from 'vitest';

const apis = vi.hoisted(() => ({
	getChatById: vi.fn(),
	createNewChat: vi.fn(),
	updateUserSettings: vi.fn()
}));

vi.mock('$lib/apis/chats', () => ({
	getChatById: apis.getChatById,
	createNewChat: apis.createNewChat
}));
vi.mock('$lib/apis/users', () => ({ updateUserSettings: apis.updateUserSettings }));

import { ensureMainChat, MAIN_CHAT_COMPACTION_TOKENS } from './main-chat';

beforeEach(() => {
	apis.getChatById.mockReset();
	apis.createNewChat.mockReset();
	apis.updateUserSettings.mockReset();
	apis.updateUserSettings.mockResolvedValue({});
});

function inputs(mainChatId: string | null) {
	return {
		token: 'fixture-token',
		mainChatId,
		modelIds: ['claude-code.opus'],
		title: 'Main chat',
		onSaved: vi.fn()
	};
}

describe('the main chat', () => {
	it('opens the saved chat while it exists', async () => {
		apis.getChatById.mockResolvedValue({ id: 'main-1' });
		const chosen = inputs('main-1');
		expect(await ensureMainChat(chosen)).toBe('main-1');
		expect(apis.createNewChat).not.toHaveBeenCalled();
		expect(chosen.onSaved).not.toHaveBeenCalled();
	});

	it('starts a Detached chat with a rolling context window when there is none, and saves it', async () => {
		apis.getChatById.mockRejectedValue('Not found');
		apis.createNewChat.mockResolvedValue({ id: 'main-2' });
		const chosen = inputs('deleted-main');
		expect(await ensureMainChat(chosen)).toBe('main-2');
		const [, chat, folderId, variables, runTarget] = apis.createNewChat.mock.calls[0];
		expect(chat.params).toEqual({ compact_token_threshold: MAIN_CHAT_COMPACTION_TOKENS });
		expect(chat.models).toEqual(['claude-code.opus']);
		expect([folderId, variables]).toEqual([null, null]);
		expect(runTarget).toEqual({ kind: 'detached', access: 'chat' });
		expect(apis.updateUserSettings).toHaveBeenCalledWith('fixture-token', {
			ui: { mainChatId: 'main-2' }
		});
		expect(chosen.onSaved).toHaveBeenCalledWith('main-2');
	});

	it('says so when the chat could not be started', async () => {
		apis.createNewChat.mockResolvedValue(null);
		await expect(ensureMainChat(inputs(null))).rejects.toThrow('could not start your main chat');
		expect(apis.updateUserSettings).not.toHaveBeenCalled();
	});
});
