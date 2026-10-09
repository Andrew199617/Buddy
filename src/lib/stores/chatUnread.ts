export type ChatUnreadState = {
	id: string;
	active?: unknown;
	updated_at?: unknown;
	last_read_at?: unknown;
};

// Match the existing chat-row unread state, without treating an open sidebar as a read.
export const isChatUnread = (chat: ChatUnreadState): boolean =>
	!chat.active &&
	(chat.last_read_at == null ||
		(typeof chat.updated_at === 'number' &&
			typeof chat.last_read_at === 'number' &&
			chat.updated_at > chat.last_read_at));

export const getUnreadChatIds = (chats: ChatUnreadState[]): string[] => [
	...new Set(chats.filter(isChatUnread).map((chat) => chat.id))
];
