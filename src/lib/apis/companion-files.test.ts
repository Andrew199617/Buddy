import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import { CompanionError } from './companion';
import { RelayCompanionClient } from './companion-relay';

const fetchMock = vi.fn();

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

function respond(status: number, body: unknown) {
	fetchMock.mockResolvedValue({ ok: status < 400, status, json: async () => body });
}

function sentBody() {
	const [, options] = fetchMock.mock.calls[0];
	return JSON.parse(options.body);
}

function client() {
	return new RelayCompanionClient('pc-1', 'buddy-token');
}

describe('Runner project file saves', () => {
	it('saves over the version that was opened', async () => {
		respond(200, { path: 'notes.md', content: 'new', version: 'version-2' });
		const saved = await client().putFile('grant', 'notes.md', 'new', 'version-1');
		expect(sentBody()).toEqual({
			grantId: 'grant',
			path: 'notes.md',
			content: 'new',
			baseVersion: 'version-1',
			overwrite: false
		});
		expect(saved.version).toBe('version-2');
	});

	it('overwrites only when asked', async () => {
		respond(200, { path: 'notes.md', content: 'new', version: 'version-3' });
		await client().putFile('grant', 'notes.md', 'new', 'version-1', true);
		expect(sentBody().overwrite).toBe(true);
	});

	it('reports a changed host copy as a 409 conflict', async () => {
		respond(409, { error: 'This file changed on the host after it was opened.' });
		const saving = client().putFile('grant', 'notes.md', 'new', 'version-1');
		await expect(saving).rejects.toBeInstanceOf(CompanionError);
		await expect(saving).rejects.toMatchObject({
			status: 409,
			message: 'This file changed on the host after it was opened.'
		});
	});
});
