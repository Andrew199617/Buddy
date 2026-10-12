import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));
vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import { createNewChat, getChatUnreadSummary } from './index';

const fetchMock = vi.fn();

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('creating a chat', () => {
	it('sends where the chat runs only when the bar chose', async () => {
		fetchMock.mockResolvedValue({ ok: true, json: async () => ({ id: 'chat-1' }) });
		const runTarget = { kind: 'detached', access: 'chat' };
		await createNewChat('token', { title: 'New Chat' }, null, null, runTarget);
		await createNewChat('token', { title: 'New Chat' }, 'folder-1');
		const [withTarget, without] = fetchMock.mock.calls.map(([, options]) =>
			JSON.parse(options.body)
		);
		expect(withTarget.run_target).toEqual(runTarget);
		expect('run_target' in without).toBe(false);
		expect(without.folder_id).toBe('folder-1');
	});
});

describe('chat unread summary API', () => {
	it.each([
		{ count: 0, only_chat_id: null },
		{ count: 1, only_chat_id: 'unread' },
		{ count: 10000, only_chat_id: null }
	])('accepts the bounded summary contract: %j', async (summary) => {
		fetchMock.mockResolvedValue({ ok: true, json: async () => summary });
		expect(await getChatUnreadSummary('token')).toEqual(summary);
		expect(fetchMock).toHaveBeenCalledWith('/api/v1/chats/unread', {
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: 'Bearer token'
			}
		});
	});

	it.each([
		null,
		{ count: -1, only_chat_id: null },
		{ count: 1.5, only_chat_id: null },
		{ count: Infinity, only_chat_id: null },
		{ count: Number.MAX_SAFE_INTEGER + 1, only_chat_id: null },
		{ count: '1', only_chat_id: 'unread' },
		{ count: 1, only_chat_id: null },
		{ count: 1, only_chat_id: '' },
		{ count: 0, only_chat_id: 'unexpected' },
		{ count: 2, only_chat_id: 'unexpected' }
	])('rejects malformed summary data: %j', async (summary) => {
		fetchMock.mockResolvedValue({ ok: true, json: async () => summary });
		await expect(getChatUnreadSummary('token')).rejects.toThrow('Invalid chat unread summary');
	});

	it('surfaces lookup failures so the store retains the last accepted summary', async () => {
		fetchMock.mockResolvedValue({ ok: false, json: async () => ({ detail: 'Unavailable' }) });
		await expect(getChatUnreadSummary('token')).rejects.toBe('Unavailable');
	});
});
