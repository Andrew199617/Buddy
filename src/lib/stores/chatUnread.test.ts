import { describe, expect, it } from 'vitest';
import { hasUnreadChatsOutsideSelection } from './chatUnread';

describe('chat unread summary', () => {
	it('shows no indicator for an empty aggregate', () => {
		expect(hasUnreadChatsOutsideSelection({ count: 0, only_chat_id: null }, '')).toBe(false);
	});

	it('excludes only the selected chat without changing the summary', () => {
		const summary = { count: 1, only_chat_id: 'unread' };
		expect(hasUnreadChatsOutsideSelection(summary, 'unread')).toBe(false);
		expect(hasUnreadChatsOutsideSelection(summary, 'other')).toBe(true);
		expect(hasUnreadChatsOutsideSelection(summary, '')).toBe(true);
		expect(summary).toEqual({ count: 1, only_chat_id: 'unread' });
	});

	it('shows an indicator when another unread chat remains outside the selection', () => {
		expect(hasUnreadChatsOutsideSelection({ count: 2, only_chat_id: null }, 'unread')).toBe(true);
		expect(hasUnreadChatsOutsideSelection({ count: 10000, only_chat_id: null }, 'unread')).toBe(
			true
		);
	});
});
