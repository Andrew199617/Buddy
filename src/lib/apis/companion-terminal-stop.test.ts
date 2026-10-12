import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import { CompanionError } from './companion';
import { RelayCompanionClient } from './companion-relay';

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

function client() {
	return new RelayCompanionClient('pc-1', 'buddy-token');
}

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	stopReply = { status: 200, body: terminalReply() };
	fetchMock.mockImplementation(async (url: string) => {
		const path = new URL(url, 'http://buddy.invalid').pathname;
		if (path.endsWith(`/v1/terminals/${terminalId}`))
			return reply(stopReply.status, stopReply.body);
		return reply(200, { revoked: true });
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('Stopping a Runner command session', () => {
	it('resolves when the Runner confirms the session closed', async () => {
		await expect(client().deleteTerminal(terminalId)).resolves.toBeUndefined();
		const [url, options] = fetchMock.mock.calls.at(-1);
		expect(new URL(url, 'http://buddy.invalid').pathname).toBe(
			`/api/v1/runners/machines/pc-1/workspace/v1/terminals/${terminalId}`
		);
		expect(options.method).toBe('DELETE');
	});

	it('treats a session the Runner already removed as stopped, so a retry after a lost reply succeeds', async () => {
		stopReply = { status: 404, body: { error: 'Terminal not found in this session.' } };
		await expect(client().deleteTerminal(terminalId)).resolves.toBeUndefined();
	});

	it('still reports a stop the Runner could not confirm', async () => {
		stopReply = { status: 503, body: { error: 'Command cleanup is unconfirmed.' } };
		const failure = client().deleteTerminal(terminalId);
		await expect(failure).rejects.toBeInstanceOf(CompanionError);
		await expect(failure).rejects.toMatchObject({ status: 503 });
	});

	it('rejects a reply that does not show the session closed', async () => {
		stopReply = { status: 200, body: terminalReply('running') };
		await expect(client().deleteTerminal(terminalId)).rejects.toMatchObject({ status: 502 });
	});
});
