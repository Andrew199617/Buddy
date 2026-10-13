import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CompanionClient, CompanionError } from './companion';

const host = { id: 'host-1', name: 'Runner PC', platform: 'win32', version: '0.1.0' };
const capabilities = {
	protocol: 'buddy-workspaces',
	version: 1,
	host,
	capabilities: { directories: true, files: true, workspaces: true, terminals: true, pty: false },
	permissions: { requiresPairing: true, grants: 'host-approved', commands: 'opt-in-os-account' }
};
const terminalId = '0b7d2f4e-1c3a-4f5b-9d8e-6a7b8c9d0e1f';
const fetchMock = vi.fn();
let stopReply: { status: number; body: unknown };

function reply(status: number, body: unknown) {
	return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function terminalReply(status = 'closed') {
	return {
		terminal: { id: terminalId, workspaceId: 'workspace-1', status, output: '', exitCode: null }
	};
}

async function pairedClient() {
	const client = new CompanionClient();
	await client.getHost();
	await client.pair('7K3M-Q9TD-2XHA-PW4N-B8RE');
	return client;
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	stopReply = { status: 200, body: terminalReply() };
	fetchMock.mockImplementation(async (url: string) => {
		const path = new URL(url).pathname;
		if (path === '/v1/capabilities') return reply(200, capabilities);
		if (path === '/v1/host') return reply(200, { host });
		if (path === '/v1/pair') {
			return reply(200, { token: 'session-token', expiresAt: Date.now() + 60000, host });
		}
		if (path === `/v1/terminals/${terminalId}`) return reply(stopReply.status, stopReply.body);
		return reply(200, { revoked: true });
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('Stopping a Runner command session', () => {
	it('resolves when the Runner confirms the session closed', async () => {
		const client = await pairedClient();
		await expect(client.deleteTerminal(terminalId)).resolves.toBeUndefined();
		const [url, options] = fetchMock.mock.calls.at(-1);
		expect(new URL(url).pathname).toBe(`/v1/terminals/${terminalId}`);
		expect(options.method).toBe('DELETE');
	});

	it('treats a session the Runner already removed as stopped, so a retry after a lost reply succeeds', async () => {
		const client = await pairedClient();
		stopReply = { status: 404, body: { error: 'Terminal not found in this session.' } };
		await expect(client.deleteTerminal(terminalId)).resolves.toBeUndefined();
	});

	it('still reports a stop the Runner could not confirm', async () => {
		const client = await pairedClient();
		stopReply = { status: 503, body: { error: 'Command cleanup is unconfirmed.' } };
		const failure = client.deleteTerminal(terminalId);
		await expect(failure).rejects.toBeInstanceOf(CompanionError);
		await expect(failure).rejects.toMatchObject({ status: 503 });
	});

	it('rejects a reply that does not show the session closed', async () => {
		const client = await pairedClient();
		stopReply = { status: 200, body: terminalReply('running') };
		await expect(client.deleteTerminal(terminalId)).rejects.toMatchObject({ status: 502 });
	});
});
