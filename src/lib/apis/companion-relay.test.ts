import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import { CompanionError } from './companion';
import { RelayCompanionClient, newRelayClientId } from './companion-relay';

const fetchMock = vi.fn();
const relayBase = '/api/v1/runners/machines/machine%20%2F%20A/workspace';

function reply(body: unknown, status = 200) {
	return { ok: status < 400, status, json: async () => body };
}

function call(index: number) {
	const [url, options] = fetchMock.mock.calls[index];
	return { url: new URL(url, 'http://buddy.invalid'), options };
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	fetchMock.mockImplementation(async (url: string) => {
		const path = new URL(url, 'http://buddy.invalid').pathname;
		if (path.endsWith('/v1/grants')) return reply({ grants: [] });
		if (path.endsWith('/v1/directories')) return reply({ path: 'src', entries: [] });
		return reply({ revoked: true });
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('projects relayed through Buddy', () => {
	it('names a page with an ID the computer accepts', () => {
		const id = newRelayClientId();
		expect(id).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
		expect(newRelayClientId()).not.toBe(id);
	});

	it('reaches the computer through Buddy with the Buddy sign-in and no pairing', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		await client.getGrants();
		await client.getDirectory('grant-1', 'src');
		const { url, options } = call(1);
		expect(url.pathname).toBe(`${relayBase}/v1/directories`);
		expect(url.searchParams.get('grantId')).toBe('grant-1');
		expect(url.searchParams.get('path')).toBe('src');
		expect(options.headers.Authorization).toBe('Bearer buddy-token');
	});

	it('keeps one page ID for every request of a connection', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		await client.getGrants();
		await client.getDirectory('grant-1', 'src');
		const ids = fetchMock.mock.calls.map(([, options]) => options.headers['X-Buddy-Relay-Client']);
		expect(new Set(ids).size).toBe(1);
		expect(ids[0]).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
	});

	it('sends request bodies as JSON', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		fetchMock.mockResolvedValueOnce(reply({ workspace: { id: 'w1' } }));
		await client.createWorkspace('grant-1', 'src');
		const { url, options } = call(0);
		expect(options.method).toBe('POST');
		expect(url.pathname).toBe(`${relayBase}/v1/workspaces`);
		expect(options.headers['Content-Type']).toBe('application/json');
		expect(JSON.parse(options.body)).toEqual({ grantId: 'grant-1', path: 'src' });
	});

	it('reports Buddy’s own refusal, such as an offline computer', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		fetchMock.mockResolvedValueOnce(reply({ detail: 'Studio PC is offline.' }, 503));
		const failure = await client.getGrants().catch((cause) => cause);
		expect(failure).toBeInstanceOf(CompanionError);
		expect(failure.message).toBe('Studio PC is offline.');
		expect(failure.status).toBe(503);
	});

	it('ends the page’s session with a keepalive request when disconnecting', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		await client.getGrants();
		await client.disconnect();
		const { url, options } = call(fetchMock.mock.calls.length - 1);
		expect(url.pathname).toBe(`${relayBase}/v1/session`);
		expect(options.method).toBe('DELETE');
		expect(options.keepalive).toBe(true);
		expect(options.headers['X-Buddy-Relay-Client']).toBe(
			call(0).options.headers['X-Buddy-Relay-Client']
		);
	});

	it('treats an offline computer as an ended session', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		fetchMock.mockResolvedValueOnce(reply({ detail: 'Studio PC is offline.' }, 503));
		await expect(client.disconnect()).resolves.toBeUndefined();
	});

	it('refuses requests after disconnecting', async () => {
		const client = new RelayCompanionClient('machine / A', 'buddy-token');
		await client.disconnect();
		await expect(client.getGrants()).rejects.toThrow('closed');
	});
});
