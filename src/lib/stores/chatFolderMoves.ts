import { writable } from 'svelte/store';

// The last chat moved into another chat folder, or out of one, from wherever it
// was moved (sidebar, search, chat menu), so the open chat can follow it.
export const chatFolderMoves = writable<{ chatId: string; folderId: string | null } | null>(null);
