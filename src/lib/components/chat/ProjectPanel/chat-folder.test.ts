import { describe, expect, it } from 'vitest';

import { afterFolderMove, openChatFolderId } from './chat-folder';

describe('the open chat’s folder', () => {
	it('is the loaded chat’s folder while that chat is open', () => {
		expect(openChatFolderId({ id: 'c1', folder_id: 'a' }, 'c1', { id: 'b' })).toBe('a');
		expect(openChatFolderId({ id: 'c1', folder_id: null }, 'c1', { id: 'b' })).toBeNull();
	});

	it('is the selected folder for a new chat, even with an old chat still loaded', () => {
		expect(openChatFolderId({ id: 'c1', folder_id: 'a' }, '', { id: 'b' })).toBe('b');
		expect(openChatFolderId(null, '', null)).toBeNull();
	});

	it('follows a move of the loaded chat only', () => {
		const loaded = { id: 'c1', folder_id: 'a', title: 'Plan' };
		expect(afterFolderMove(loaded, { chatId: 'c1', folderId: 'b' })).toEqual({
			id: 'c1',
			folder_id: 'b',
			title: 'Plan'
		});
		expect(afterFolderMove(loaded, { chatId: 'c2', folderId: 'b' })).toBeNull();
		expect(afterFolderMove(loaded, null)).toBeNull();
	});
});
