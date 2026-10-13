import { readFileSync } from 'node:fs';
import { get } from 'svelte/store';
import ts from 'typescript';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getChatList, getChatUnreadSummary, getPinnedChatList } = vi.hoisted(() => ({
	getChatList: vi.fn(),
	getChatUnreadSummary: vi.fn(),
	getPinnedChatList: vi.fn()
}));
vi.mock('$lib/apis/chats', () => ({ getChatList, getChatUnreadSummary, getPinnedChatList }));

import { chatId } from './chatSelection';
import {
	chats,
	hasUnreadChats,
	refreshChatList,
	resetChatListState,
	setChatActive,
	unreadSummary
} from './chatList';

const layoutSource = readFileSync(new URL('../../routes/+layout.svelte', import.meta.url), 'utf8');

// Runs the root layout's own clearExpiredSession, so the test covers the
// production wiring. Browser and sign-out side effects are replaced by fakes.
function loadClearExpiredSession() {
	const script = layoutSource.match(/<script[^>]*>([\s\S]*?)<\/script>/)?.[1] ?? '';
	const layout = ts.createSourceFile('layout.js', script, ts.ScriptTarget.ESNext, true);
	const declaration = layout.statements.find(
		(statement) =>
			ts.isVariableStatement(statement) &&
			statement.declarationList.declarations.some(
				(variable) => variable.name.getText(layout) === 'clearExpiredSession'
			)
	);
	if (!declaration) {
		throw new Error('The root layout no longer declares clearExpiredSession');
	}

	const layoutScope = {
		isAuthRedirectInProgress: false,
		tokenTimer: null,
		user: { set: vi.fn() },
		localStorage: { removeItem: vi.fn() },
		document: { cookie: '' },
		userSignOut: vi.fn(() => Promise.resolve()),
		toast: { error: vi.fn() },
		$i18n: { t: (text: string) => text },
		resetChatListState
	};
	const createClearExpiredSession = new Function(
		...Object.keys(layoutScope),
		`${declaration.getText(layout)}\nreturn clearExpiredSession;`
	);
	return createClearExpiredSession(...Object.values(layoutScope)) as () => void;
}

beforeEach(() => {
	resetChatListState();
	chatId.set('');
	vi.clearAllMocks();
	getChatList.mockResolvedValue([]);
	getPinnedChatList.mockResolvedValue([]);
});

describe('expired session', () => {
	it('drops the signed-out account unread state before another account signs in', async () => {
		getChatList.mockResolvedValue([{ id: 'account-a-chat', updated_at: 100, last_read_at: 10 }]);
		getChatUnreadSummary.mockResolvedValue({ count: 2, only_chat_id: null });
		await refreshChatList('account-a-token');
		await vi.waitFor(() => expect(get(hasUnreadChats)).toBe(true));

		const clearExpiredSession = loadClearExpiredSession();
		clearExpiredSession();

		// /auth signs the next account in without a reload, so nothing of
		// account A may stay visible while account B's sidebar loads.
		expect(get(hasUnreadChats)).toBe(false);
		expect(get(unreadSummary)).toEqual({ count: 0, only_chat_id: null });
		expect(get(chats)).toBeNull();

		// An event before account B's first refresh must not reuse A's token.
		setChatActive('account-b-chat', false);
		await Promise.resolve();
		await Promise.resolve();
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(1);
		expect(getChatUnreadSummary).toHaveBeenLastCalledWith('account-a-token');
	});
});
