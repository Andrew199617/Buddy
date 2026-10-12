import { writable } from 'svelte/store';

// The chat folder whose project folder was saved last, so every open view of it
// (the folder page chip, a chat's Project tab) reloads, wherever it was saved.
export const projectFolderChanges = writable<{ folderId: string; count: number }>({
	folderId: '',
	count: 0
});

export function announceProjectFolderChange(folderId: string) {
	projectFolderChanges.update((last) => ({ folderId, count: last.count + 1 }));
}
