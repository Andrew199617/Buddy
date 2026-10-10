import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getChatList, getChatUnreadSummary, getPinnedChatList } = vi.hoisted(() => ({
	getChatList: vi.fn(),
	getChatUnreadSummary: vi.fn(),
	getPinnedChatList: vi.fn()
}));
vi.mock('$lib/apis/chats', () => ({ getChatList, getChatUnreadSummary, getPinnedChatList }));

import { chatId } from './chatSelection';
import type { ChatUnreadSummary } from './chatUnread';
import {
	chats,
	hasUnreadChats,
	refreshChatList,
	refreshUnreadChats,
	resetChatListState,
	setAllChatsRead,
	setChatActive,
	setChatReadAt,
	unreadSummary
} from './chatList';

const emptySummary: ChatUnreadSummary = { count: 0, only_chat_id: null };
const soleUnread: ChatUnreadSummary = { count: 1, only_chat_id: 'unread' };
const readChat = { id: 'read', updated_at: 100, last_read_at: 100 };

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

async function flushUnreadRefresh() {
	await Promise.resolve();
	await refreshUnreadChats('token');
}

beforeEach(() => {
	resetChatListState();
	chatId.set('');
	vi.clearAllMocks();
	getChatList.mockResolvedValue([]);
	getChatUnreadSummary.mockResolvedValue(emptySummary);
	getPinnedChatList.mockResolvedValue([]);
});

describe('aggregate chat unread state', () => {
	it('uses a bounded summary for older, pinned and folder chats without loading whole history', async () => {
		getChatList.mockResolvedValue([readChat]);
		getChatUnreadSummary.mockResolvedValue({ count: 3, only_chat_id: null });
		await refreshChatList('token', { refreshPinned: true });

		expect(getChatList).toHaveBeenCalledTimes(1);
		expect(getChatList).toHaveBeenCalledWith('token', 1);
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(1);
		expect(getChatUnreadSummary).toHaveBeenCalledWith('token');
		expect(get(chats)).toEqual([readChat]);
		expect(get(unreadSummary)).toEqual({ count: 3, only_chat_id: null });
		expect(get(hasUnreadChats)).toBe(true);
	});

	it('does not delay sidebar rows while the unread summary is pending', async () => {
		const snapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValue(snapshot.promise);
		getChatList.mockResolvedValue([readChat]);

		const result = await refreshChatList('token');

		expect(result.accepted).toBe(true);
		expect(get(chats)).toEqual([readChat]);
		expect(get(unreadSummary)).toEqual(emptySummary);
		snapshot.resolve(soleUnread);
		await refreshUnreadChats('token');
		expect(get(hasUnreadChats)).toBe(true);
	});

	it('excludes an open completed chat and reacts to selection without marking it read', async () => {
		const current = { id: 'unread', updated_at: 100, last_read_at: 10, active: true };
		getChatList.mockResolvedValue([current]);
		chatId.set('unread');
		await refreshChatList('token');
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		setChatActive('unread', false);
		await flushUnreadRefresh();

		expect(get(hasUnreadChats)).toBe(false);
		const requests = getChatUnreadSummary.mock.calls.length;
		chatId.set('another-chat');
		expect(get(hasUnreadChats)).toBe(true);
		chatId.set('unread');
		expect(get(hasUnreadChats)).toBe(false);
		chatId.set('');
		expect(get(hasUnreadChats)).toBe(true);
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(requests);
		expect(get(chats)?.[0].last_read_at).toBe(10);
		expect(get(unreadSummary)).toEqual(soleUnread);
	});

	it('shows another unread chat while the current chat is selected', async () => {
		chatId.set('unread');
		getChatUnreadSummary.mockResolvedValue({ count: 2, only_chat_id: null });
		await refreshUnreadChats('token');
		expect(get(hasUnreadChats)).toBe(true);

		getChatUnreadSummary.mockResolvedValue(soleUnread);
		setChatReadAt('other-chat', 100);
		await flushUnreadRefresh();
		expect(get(hasUnreadChats)).toBe(false);
	});

	it('refreshes after read and active events outside the loaded page', async () => {
		getChatUnreadSummary.mockResolvedValue({ count: 2, only_chat_id: null });
		await refreshUnreadChats('token');
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		expect(setChatReadAt('old-page-chat', 100)).toBe(false);
		await flushUnreadRefresh();
		expect(get(hasUnreadChats)).toBe(true);
		getChatUnreadSummary.mockResolvedValue(emptySummary);
		expect(setChatActive('unread', true)).toBe(false);
		await flushUnreadRefresh();
		expect(get(hasUnreadChats)).toBe(false);
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		setChatActive('unread', false);
		await flushUnreadRefresh();
		expect(get(hasUnreadChats)).toBe(true);
		expect(getChatList).not.toHaveBeenCalled();
	});

	it('coalesces a synchronous burst of read and active events', async () => {
		await refreshUnreadChats('token');
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		setChatReadAt('old-page', 0);
		setChatActive('pinned', true);
		setChatActive('folder', false);
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(1);
		await flushUnreadRefresh();
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(2);
		expect(get(hasUnreadChats)).toBe(true);
	});

	it('coalesces concurrent refreshes and replaces a snapshot invalidated by events', async () => {
		await refreshUnreadChats('token');
		const snapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(snapshot.promise).mockResolvedValue(soleUnread);
		const states: boolean[] = [];
		const unsubscribe = hasUnreadChats.subscribe((state) => states.push(state));
		const refresh = refreshUnreadChats('token');
		expect(refreshUnreadChats('token')).toBe(refresh);
		await Promise.resolve();
		setChatReadAt('old-page', 100);
		setChatActive('unread', false);
		snapshot.resolve({ count: 8, only_chat_id: null });
		await refresh;
		unsubscribe();

		expect(getChatUnreadSummary).toHaveBeenCalledTimes(3);
		expect(get(unreadSummary)).toEqual(soleUnread);
		expect(states).toEqual([false, true]);
	});

	it('does not resurrect unread after bulk read when an old snapshot resolves and retry fails', async () => {
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		await refreshUnreadChats('token');
		const snapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary
			.mockReturnValueOnce(snapshot.promise)
			.mockRejectedValueOnce(new Error('Network unavailable'));
		const refresh = refreshUnreadChats('token');
		await Promise.resolve();
		setAllChatsRead();
		expect(get(hasUnreadChats)).toBe(false);
		snapshot.resolve(soleUnread);
		expect(await refresh).toBe(false);
		expect(get(unreadSummary)).toEqual(emptySummary);
	});

	it('requests fresh unread state when a delete refresh arrives during an older snapshot', async () => {
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		await refreshUnreadChats('token');
		const snapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(snapshot.promise).mockResolvedValue(emptySummary);
		const refresh = refreshUnreadChats('token');
		await Promise.resolve();
		// The chat-delete path refreshes rows without a read or active mutation event.
		await refreshChatList('token', { refreshPinned: true });
		snapshot.resolve(soleUnread);
		await refresh;

		expect(get(unreadSummary)).toEqual(emptySummary);
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(3);
	});

	it('refreshes a bulk read followed by marking one chat unread during a snapshot', async () => {
		getChatUnreadSummary.mockResolvedValue({ count: 2, only_chat_id: null });
		await refreshUnreadChats('token');
		const snapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(snapshot.promise).mockResolvedValue(soleUnread);
		const refresh = refreshUnreadChats('token');
		await Promise.resolve();
		setAllChatsRead();
		setChatReadAt('unread', 0);
		snapshot.resolve({ count: 2, only_chat_id: null });
		await refresh;
		expect(get(unreadSummary)).toEqual(soleUnread);
	});

	it('retains an accepted summary on failure while sidebar rows refresh', async () => {
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		await refreshUnreadChats('token');
		getChatUnreadSummary.mockRejectedValue(new Error('Network unavailable'));
		getChatList.mockResolvedValue([readChat]);
		const result = await refreshChatList('token');
		expect(result.accepted).toBe(true);
		expect(get(chats)).toEqual([readChat]);
		expect(get(unreadSummary)).toEqual(soleUnread);
	});

	it('ignores snapshots and queued refreshes after logout/reset', async () => {
		const snapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(snapshot.promise);
		const refresh = refreshUnreadChats('token');
		await Promise.resolve();
		setChatReadAt('unread', 0);
		resetChatListState();
		snapshot.resolve(soleUnread);
		expect(await refresh).toBe(false);
		setChatActive('unread', false);
		await Promise.resolve();
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(1);
		expect(get(hasUnreadChats)).toBe(false);

		getChatUnreadSummary.mockResolvedValue(soleUnread);
		await refreshUnreadChats('token');
		setChatReadAt('unread', 100);
		resetChatListState();
		await Promise.resolve();
		expect(getChatUnreadSummary).toHaveBeenCalledTimes(2);
	});

	it('invalidates an old token request and uses the new token for event refreshes', async () => {
		const oldSnapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(oldSnapshot.promise).mockResolvedValue(soleUnread);
		const oldRefresh = refreshUnreadChats('old-token');
		await Promise.resolve();
		await refreshUnreadChats('new-token');
		oldSnapshot.resolve(emptySummary);
		expect(await oldRefresh).toBe(false);
		expect(get(unreadSummary)).toEqual(soleUnread);
		setChatActive('unread', false);
		await Promise.resolve();
		await refreshUnreadChats('new-token');
		expect(getChatUnreadSummary).toHaveBeenLastCalledWith('new-token');
	});

	it('does not request a summary before a token has been provided', async () => {
		setChatReadAt('unread', 0);
		setChatActive('unread', false);
		await Promise.resolve();
		expect(getChatUnreadSummary).not.toHaveBeenCalled();
	});

	it('clears the previous account summary even if the new account request fails', async () => {
		getChatUnreadSummary.mockResolvedValue(soleUnread);
		await refreshUnreadChats('old-token');
		expect(get(hasUnreadChats)).toBe(true);
		const oldSnapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(oldSnapshot.promise);
		const oldRefresh = refreshUnreadChats('old-token');
		await Promise.resolve();

		const newSnapshot = deferred<ChatUnreadSummary>();
		getChatUnreadSummary.mockReturnValueOnce(newSnapshot.promise);
		const newRefresh = refreshUnreadChats('new-token');
		expect(get(unreadSummary)).toEqual(emptySummary);
		expect(get(hasUnreadChats)).toBe(false);
		await Promise.resolve();
		newSnapshot.reject(new Error('New account lookup unavailable'));
		expect(await newRefresh).toBe(false);
		oldSnapshot.resolve(soleUnread);
		expect(await oldRefresh).toBe(false);

		expect(get(unreadSummary)).toEqual(emptySummary);
		expect(get(hasUnreadChats)).toBe(false);
	});
});
