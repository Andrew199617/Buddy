import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getChatList, getPinnedChatList } = vi.hoisted(() => ({
	getChatList: vi.fn(),
	getPinnedChatList: vi.fn()
}));
vi.mock('$lib/apis/chats', () => ({ getChatList, getPinnedChatList }));

import {
	chats,
	hasUnreadChats,
	refreshChatList,
	refreshUnreadChats,
	resetChatListState,
	setAllChatsRead,
	setChatActive,
	setChatReadAt,
	unreadChatIds
} from './chatList';

const readChat = { id: 'read', updated_at: 100, last_read_at: 100 };
const unreadChat = { id: 'unread', updated_at: 100, last_read_at: 10 };

beforeEach(() => {
	resetChatListState();
	vi.clearAllMocks();
	getChatList.mockResolvedValue([]);
	getPinnedChatList.mockResolvedValue([]);
});

describe('aggregate chat unread state', () => {
	it('includes pinned, folder and older chats beyond the loaded first page', async () => {
		const oldChat = { ...unreadChat, id: 'old-page' };
		const pinnedChat = { ...unreadChat, id: 'pinned' };
		const folderChat = { ...unreadChat, id: 'folder' };
		getChatList.mockImplementation((_token, page) =>
			Promise.resolve(page === 1 ? [readChat] : [readChat, oldChat, pinnedChat, folderChat])
		);
		getPinnedChatList.mockResolvedValue([pinnedChat]);

		await refreshChatList('token', { refreshPinned: true });

		expect(getChatList).toHaveBeenCalledWith('token', null, true, true);
		expect(get(chats)).toEqual([readChat]);
		expect(get(unreadChatIds)).toEqual(['old-page', 'pinned', 'folder']);
		expect(get(hasUnreadChats)).toBe(true);
	});

	it('reacts to reading one of several chats and clears only after the last is read', async () => {
		getChatList.mockResolvedValue([unreadChat, { ...unreadChat, id: 'second' }]);
		await refreshUnreadChats('token');
		const states: boolean[] = [];
		const unsubscribe = hasUnreadChats.subscribe((state) => states.push(state));

		setChatReadAt('unread', 100);
		expect(get(unreadChatIds)).toEqual(['second']);
		expect(get(hasUnreadChats)).toBe(true);
		setChatReadAt('second', 100);
		expect(get(hasUnreadChats)).toBe(false);
		setChatReadAt('second', 0);
		expect(get(hasUnreadChats)).toBe(true);
		unsubscribe();

		expect(states).toEqual([true, false, true]);
		expect(getChatList).toHaveBeenCalledTimes(1);
	});

	it('shows a newly completed unread chat and clears bulk reads without refreshing', async () => {
		getChatList.mockResolvedValue([{ ...unreadChat, active: true }]);
		await refreshUnreadChats('token');
		expect(get(hasUnreadChats)).toBe(false);

		// Keep the existing visible-row lookup result for Sidebar event handling.
		expect(setChatActive('unread', false)).toBe(false);
		expect(get(hasUnreadChats)).toBe(true);
		setAllChatsRead();
		expect(get(hasUnreadChats)).toBe(false);
		expect(getChatList).toHaveBeenCalledTimes(1);
	});

	it('keeps read and active events that arrive while a snapshot is loading', async () => {
		getChatList.mockResolvedValue([unreadChat, { ...unreadChat, id: 'second' }]);
		await refreshUnreadChats('token');
		let resolveSnapshot!: (items: (typeof unreadChat)[]) => void;
		getChatList.mockReturnValueOnce(
			new Promise((resolve) => {
				resolveSnapshot = resolve;
			})
		);
		const refresh = refreshUnreadChats('token');
		setChatReadAt('unread', 100);
		setChatActive('second', true);
		resolveSnapshot([unreadChat, { ...unreadChat, id: 'second' }]);
		await refresh;

		expect(get(hasUnreadChats)).toBe(false);
	});

	it('preserves a bulk read followed by marking one chat unread during a snapshot', async () => {
		getChatList.mockResolvedValue([unreadChat, { ...unreadChat, id: 'second' }]);
		await refreshUnreadChats('token');
		let resolveSnapshot!: (items: (typeof unreadChat)[]) => void;
		getChatList.mockReturnValueOnce(
			new Promise((resolve) => {
				resolveSnapshot = resolve;
			})
		);
		const refresh = refreshUnreadChats('token');
		setAllChatsRead();
		setChatReadAt('second', 0);
		resolveSnapshot([unreadChat, { ...unreadChat, id: 'second' }]);
		await refresh;

		expect(get(unreadChatIds)).toEqual(['second']);
	});

	it('retains known unread state when an aggregate request fails while sidebar rows refresh', async () => {
		getChatList.mockResolvedValue([unreadChat]);
		await refreshUnreadChats('token');
		getChatList.mockImplementation((_token, page) => {
			if (page === null) return Promise.reject(new Error('Network unavailable'));
			return Promise.resolve([readChat]);
		});

		const result = await refreshChatList('token');

		expect(result.accepted).toBe(true);
		expect(get(chats)).toEqual([readChat]);
		expect(get(hasUnreadChats)).toBe(true);
	});

	it('ignores a snapshot that resolves after logout/reset', async () => {
		let resolveSnapshot!: (items: (typeof unreadChat)[]) => void;
		getChatList.mockReturnValueOnce(
			new Promise((resolve) => {
				resolveSnapshot = resolve;
			})
		);
		const refresh = refreshUnreadChats('token');
		resetChatListState();
		resolveSnapshot([unreadChat]);

		expect(await refresh).toBe(false);
		expect(get(hasUnreadChats)).toBe(false);
	});
});
