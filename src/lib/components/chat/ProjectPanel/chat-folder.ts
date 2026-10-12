// Which chat folder the open chat is in, for its Project tab.

type LoadedChat = { id?: string; folder_id?: string | null } | null;
export type ChatFolderMove = { chatId: string; folderId: string | null };

/**
 * The loaded chat's folder while it is the open chat; otherwise, for a new chat,
 * the folder it starts in. The loaded chat is kept after starting a new one.
 */
export function openChatFolderId(
	loaded: LoadedChat,
	openId: string,
	selectedFolder: { id?: string } | null
): string | null {
	if (loaded?.id && loaded.id === openId) return loaded.folder_id ?? null;
	return selectedFolder?.id ?? null;
}

/** The loaded chat with its new folder, or null when the move was another chat's. */
export function afterFolderMove<T extends LoadedChat>(
	loaded: T,
	move: ChatFolderMove | null
): (T & { folder_id: string | null }) | null {
	if (!move || !loaded || loaded.id !== move.chatId) return null;
	return { ...loaded, folder_id: move.folderId };
}
