import { derived, get, readonly, writable } from 'svelte/store';
import { getChatList, getChatUnreadSummary, getPinnedChatList } from '$lib/apis/chats';
import { chatId } from './chatSelection';
import { hasUnreadChatsOutsideSelection, type ChatUnreadSummary } from './chatUnread';

type ChatListItem = {
	id: string;
	[key: string]: unknown;
};

const chatsStore = writable<ChatListItem[] | null>(null);
const pinnedChatsStore = writable<ChatListItem[]>([]);
const unreadSummaryStore = writable<ChatUnreadSummary>({ count: 0, only_chat_id: null });

export const chats = readonly(chatsStore);
export const pinnedChats = readonly(pinnedChatsStore);
export const unreadSummary = readonly(unreadSummaryStore);
export const hasUnreadChats = derived([unreadSummaryStore, chatId], ([$summary, $chatId]) =>
	hasUnreadChatsOutsideSelection($summary, $chatId)
);

let unreadRequestGeneration = 0;
let unreadMutationVersion = 0;
let unreadRefreshToken: string | null = null;
let unreadRefreshPromise: Promise<boolean> | null = null;
let unreadRefreshStarted = false;
let unreadRefreshRequested = false;
let unreadRefreshScheduled = false;

const scheduleUnreadRefresh = () => {
	if (unreadRefreshToken === null || unreadRefreshPromise || unreadRefreshScheduled) {
		return;
	}

	unreadRefreshScheduled = true;
	const generation = unreadRequestGeneration;
	queueMicrotask(() => {
		if (generation !== unreadRequestGeneration || unreadRefreshToken === null) {
			return;
		}
		unreadRefreshScheduled = false;
		void refreshUnreadChats(unreadRefreshToken);
	});
};

const invalidateUnreadSummary = () => {
	unreadMutationVersion += 1;
	unreadRefreshRequested = true;
	scheduleUnreadRefresh();
};

const loadUnreadSummary = async (generation: number, token: string): Promise<boolean> => {
	let accepted = false;
	do {
		unreadRefreshRequested = false;
		const mutationVersion = unreadMutationVersion;
		try {
			const nextSummary = await getChatUnreadSummary(token);
			if (generation !== unreadRequestGeneration) {
				return false;
			}
			if (mutationVersion === unreadMutationVersion) {
				unreadSummaryStore.set(nextSummary);
				accepted = true;
			} else {
				// Events changed server state while this snapshot was being fetched.
				unreadRefreshRequested = true;
			}
		} catch {
			if (generation !== unreadRequestGeneration) {
				return false;
			}
			// Preserve the last accepted summary when the background request fails.
		}
	} while (unreadRefreshRequested);

	return accepted;
};

export const refreshUnreadChats = (token: string = ''): Promise<boolean> => {
	if (token !== unreadRefreshToken) {
		unreadRequestGeneration += 1;
		unreadRefreshToken = token;
		unreadRefreshPromise = null;
		unreadRefreshStarted = false;
		unreadRefreshRequested = false;
		unreadRefreshScheduled = false;
		// A different account must not retain the previous account's accepted summary.
		unreadSummaryStore.set({ count: 0, only_chat_id: null });
	}
	if (unreadRefreshPromise) {
		if (unreadRefreshStarted) {
			// A later refresh can reflect a delete/import without a read or active event.
			unreadMutationVersion += 1;
			unreadRefreshRequested = true;
		}
		return unreadRefreshPromise;
	}

	const generation = ++unreadRequestGeneration;
	unreadRefreshScheduled = false;
	const startUnreadRefresh = () => {
		if (generation !== unreadRequestGeneration) {
			return false;
		}
		unreadRefreshStarted = true;
		return loadUnreadSummary(generation, token);
	};
	// Requests made in the same turn share one fetch; later requests get fresh state.
	const request = Promise.resolve().then(startUnreadRefresh);
	unreadRefreshPromise = request;
	void request.finally(() => {
		if (generation === unreadRequestGeneration) {
			unreadRefreshPromise = null;
			unreadRefreshStarted = false;
			if (unreadRefreshRequested) {
				scheduleUnreadRefresh();
			}
		}
	});
	return request;
};

let currentPage = 1;
let paginationReady = false;
let requestGeneration = 0;
let allLoaded = false;
let loadingNextPage = false;

type RefreshChatListOptions = {
	refreshPinned?: boolean;
	clearPinned?: boolean;
};

type ChatListResult = {
	accepted: boolean;
	allLoaded: boolean;
};

export const refreshChatList = async (
	token: string = '',
	options: RefreshChatListOptions = {}
): Promise<ChatListResult> => {
	const generation = ++requestGeneration;
	paginationReady = false;
	loadingNextPage = false;

	void refreshUnreadChats(token);
	const [nextChats, nextPinnedChats] = await Promise.all([
		getChatList(token, 1) as Promise<ChatListItem[]>,
		options.refreshPinned && !options.clearPinned
			? (getPinnedChatList(token) as Promise<ChatListItem[]>)
			: Promise.resolve(undefined as ChatListItem[] | undefined)
	]);

	if (generation !== requestGeneration) {
		return { accepted: false, allLoaded };
	}

	chatsStore.set(nextChats);
	currentPage = 1;
	allLoaded = nextChats.length === 0;

	if (options.clearPinned) {
		pinnedChatsStore.set([]);
	} else if (options.refreshPinned) {
		pinnedChatsStore.set(nextPinnedChats ?? []);
	}

	paginationReady = true;
	return { accepted: true, allLoaded };
};

// The sidebar owns folder state. This bridge lets other components refresh it.
type FolderRefreshHandler = (folderId?: string | null, chat?: ChatListItem | null) => unknown;
const folderRefreshHandlers = new Set<FolderRefreshHandler>();

export const registerFolderRefreshHandler = (handler: FolderRefreshHandler) => {
	folderRefreshHandlers.add(handler);
	return () => {
		folderRefreshHandlers.delete(handler);
	};
};

export const refreshFolderChatLists = async (
	folderId?: string | null,
	chat?: ChatListItem | null
) => {
	await Promise.all([...folderRefreshHandlers].map((handler) => handler(folderId, chat)));
};

export const refreshSidebar = async (token: string = '') => {
	await Promise.all([
		refreshChatList(token, { refreshPinned: true }),
		refreshFolderChatLists(null),
		refreshFolderChatLists()
	]);
};

export const loadNextChatListPage = async (token: string = ''): Promise<ChatListResult> => {
	if (!paginationReady || allLoaded || loadingNextPage) {
		return { accepted: false, allLoaded };
	}

	const generation = requestGeneration;
	const nextPage = currentPage + 1;
	loadingNextPage = true;

	try {
		const nextChats = (await getChatList(token, nextPage)) as ChatListItem[];

		if (generation !== requestGeneration) {
			return { accepted: false, allLoaded };
		}

		allLoaded = nextChats.length === 0;
		currentPage = nextPage;

		const existingIds = new Set((get(chatsStore) ?? []).map((chat) => chat.id));
		const uniqueChats = nextChats.filter((chat) => !existingIds.has(chat.id));
		chatsStore.set([...(get(chatsStore) ?? []), ...uniqueChats]);

		return { accepted: true, allLoaded };
	} finally {
		loadingNextPage = false;
	}
};

export const setChatActive = (chatId: string, active: boolean): boolean => {
	let found = false;
	const updateChat = (chat: ChatListItem) => {
		if (chat.id !== chatId) {
			return chat;
		}
		found = true;
		return { ...chat, active };
	};

	chatsStore.update((items) => (items ? items.map(updateChat) : items));
	pinnedChatsStore.update((items) => items.map(updateChat));
	const foundInChatList = found;
	invalidateUnreadSummary();
	return foundInChatList;
};

export const setChatReadAt = (chatId: string, lastReadAt: number): boolean => {
	let found = false;
	const updateChat = (chat: ChatListItem) => {
		if (chat.id !== chatId) {
			return chat;
		}
		found = true;
		return { ...chat, last_read_at: lastReadAt };
	};

	chatsStore.update((items) => (items ? items.map(updateChat) : items));
	pinnedChatsStore.update((items) => items.map(updateChat));
	const foundInChatList = found;
	invalidateUnreadSummary();
	return foundInChatList;
};

export const setAllChatsRead = () => {
	const updateChat = (chat: ChatListItem) => ({ ...chat, last_read_at: chat.updated_at });

	chatsStore.update((items) => (items ? items.map(updateChat) : items));
	pinnedChatsStore.update((items) => items.map(updateChat));
	unreadSummaryStore.set({ count: 0, only_chat_id: null });
	invalidateUnreadSummary();
};

export const resetChatListState = () => {
	requestGeneration += 1;
	currentPage = 1;
	paginationReady = false;
	allLoaded = false;
	loadingNextPage = false;
	chatsStore.set(null);
	pinnedChatsStore.set([]);
	unreadRequestGeneration += 1;
	unreadMutationVersion = 0;
	unreadSummaryStore.set({ count: 0, only_chat_id: null });
	unreadRefreshToken = null;
	unreadRefreshPromise = null;
	unreadRefreshStarted = false;
	unreadRefreshRequested = false;
	unreadRefreshScheduled = false;
};
