import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CompanionClient } from './companion';

const host = { id: 'host-1', name: 'Runner PC', platform: 'win32', version: '0.1.0' };
const capabilities = {
	protocol: 'buddy-workspaces',
	version: 1,
	host,
	capabilities: { directories: true, files: true, workspaces: true, terminals: true, pty: false },
	permissions: { requiresPairing: true, grants: 'host-approved', commands: 'opt-in-os-account' }
};
const fetchMock = vi.fn();

function reply(body: unknown) {
	return { ok: true, status: 200, json: async () => body };
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	fetchMock.mockImplementation(async (url: string) => {
		const path = new URL(url).pathname;
		if (path === '/v1/capabilities') return reply(capabilities);
		if (path === '/v1/host') return reply({ host });
		if (path === '/v1/pair') {
			return reply({ token: 'session-token', expiresAt: Date.now() + 60000, host });
		}
		return reply({ revoked: true });
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('Runner project session revocation', () => {
	it('revokes with a keepalive request, so it survives the page closing', async () => {
		const client = new CompanionClient();
		await client.getHost();
		await client.pair('7K3M-Q9TD-2XHA-PW4N-B8RE');
		await client.disconnect();
		const [url, options] = fetchMock.mock.calls.at(-1);
		expect(new URL(url).pathname).toBe('/v1/session');
		expect(options.method).toBe('DELETE');
		expect(options.keepalive).toBe(true);
		expect(options.headers.Authorization).toBe('Bearer session-token');
	});
});
