import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import { getSuperUsers, setSuperUser } from './index';

const fetchMock = vi.fn();

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	fetchMock.mockResolvedValue({
		ok: true,
		json: async () => ({ owner_id: 'owner', user_ids: ['admin-2'] })
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('super users api', () => {
	it('reads the owner and the named super users', async () => {
		expect(await getSuperUsers('fixture-token')).toEqual({
			owner_id: 'owner',
			user_ids: ['admin-2']
		});
		const [url, options] = fetchMock.mock.calls[0];
		expect(url).toBe('/api/v1/users/super-users');
		expect(options.headers.Authorization).toBe('Bearer fixture-token');
	});

	it('names a super user with POST and an encoded id', async () => {
		await setSuperUser('fixture-token', 'admin/2', true);
		const [url, options] = fetchMock.mock.calls[0];
		expect(url).toBe('/api/v1/users/admin%2F2/super-user');
		expect(options.method).toBe('POST');
		expect(JSON.parse(options.body)).toEqual({ enabled: true });
	});

	it('rejects with the server detail', async () => {
		fetchMock.mockResolvedValue({
			ok: false,
			status: 403,
			json: async () => ({ detail: 'Only the server owner can choose super users.' })
		});
		await expect(setSuperUser('fixture-token', 'admin-2', true)).rejects.toBe(
			'Only the server owner can choose super users.'
		);
	});

	it('rejects when the answer is not JSON, such as a proxy error page', async () => {
		fetchMock.mockResolvedValue({
			ok: false,
			status: 502,
			json: async () => {
				throw new SyntaxError('Unexpected token <');
			}
		});
		await expect(setSuperUser('fixture-token', 'admin-2', false)).rejects.toBe(
			'Request failed (502)'
		);
	});

	it('rejects when Buddy cannot be reached', async () => {
		fetchMock.mockRejectedValue(new TypeError('fetch failed'));
		await expect(setSuperUser('fixture-token', 'admin-2', true)).rejects.toThrow('fetch failed');
	});
});
