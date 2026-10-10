import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import { getBackupOverview, getBackupRunLog, runBackupNow } from './index';

const fetchMock = vi.fn();

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('backup api', () => {
	it('reads the overview with the admin token', async () => {
		await getBackupOverview('fixture-token');
		const [url, options] = fetchMock.mock.calls[0];
		expect(url).toBe('/api/v1/backups/');
		expect(options.method).toBe('GET');
		expect(options.headers.Authorization).toBe('Bearer fixture-token');
	});

	it('queues a run with POST', async () => {
		await runBackupNow('fixture-token');
		const [url, options] = fetchMock.mock.calls[0];
		expect(url).toBe('/api/v1/backups/run');
		expect(options.method).toBe('POST');
	});

	it('encodes the run id of a log request', async () => {
		await getBackupRunLog('fixture-token', 'run/1');
		expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/backups/runs/run%2F1/log');
	});

	it('rejects with the server detail, such as a run already in progress', async () => {
		fetchMock.mockResolvedValue({
			ok: false,
			status: 409,
			json: async () => ({ detail: 'A backup is already queued or running.' })
		});
		await expect(runBackupNow('fixture-token')).rejects.toBe('A backup is already queued or running.');
	});
});
