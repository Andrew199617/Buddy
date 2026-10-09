import { describe, expect, it } from 'vitest';
import { getUnreadChatIds, isChatUnread } from './chatUnread';

describe('chat unread state', () => {
	it('matches the existing timestamp and completion state', () => {
		expect(isChatUnread({ id: 'unread', updated_at: 20, last_read_at: 10 })).toBe(true);
		expect(isChatUnread({ id: 'never-read', updated_at: 20, last_read_at: null })).toBe(true);
		expect(isChatUnread({ id: 'read', updated_at: 20, last_read_at: 20 })).toBe(false);
		expect(isChatUnread({ id: 'later-read', updated_at: 20, last_read_at: 30 })).toBe(false);
		expect(isChatUnread({ id: 'generating', updated_at: 20, last_read_at: 10, active: true })).toBe(
			false
		);
	});

	it('returns unique unread IDs without clearing them because their sidebar is opened', () => {
		const unread = { id: 'unread', updated_at: 20, last_read_at: 10 };
		expect(getUnreadChatIds([])).toEqual([]);
		expect(getUnreadChatIds([unread, unread])).toEqual(['unread']);
		expect(getUnreadChatIds([unread, unread])).toEqual(['unread']);
	});
});
