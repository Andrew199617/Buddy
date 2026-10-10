import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));

import {
	cancelSubscriptionLogin,
	getSubscription,
	getSubscriptionLogin,
	logoutSubscription,
	startSubscriptionLogin,
	submitSubscriptionLoginCode,
	updateSubscriptionConfig
} from './index';

const fetchMock = vi.fn();
const machineId = 'runner / A';
const revision = 'revision + 1';

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	fetchMock.mockReset();
	fetchMock.mockResolvedValue({ ok: true, json: async () => ({ accepted: true }) });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

function request() {
	const [url, options] = fetchMock.mock.calls[0];
	return { url: new URL(url, 'http://fixture.invalid'), options };
}

function expectGuardedQuery(method: string, path: string) {
	const { url, options } = request();
	expect(options.method).toBe(method);
	expect(url.pathname).toBe(path);
	expect(url.searchParams.get('expected_machine_id')).toBe(machineId);
	expect(url.searchParams.get('expected_machine_revision')).toBe(revision);
	expect(options.headers.Authorization).toBe('Bearer fixture-token');
}

describe('subscription selected-computer proof', () => {
	it('keeps the old selection proof separate from the new config target', async () => {
		await updateSubscriptionConfig(
			'fixture-token',
			'claude',
			{ machine_id: 'runner-b', enable: true },
			machineId,
			revision
		);
		const { options } = request();
		expect(JSON.parse(options.body)).toEqual({
			machine_id: 'runner-b',
			enable: true,
			expected_machine_id: machineId,
			expected_machine_revision: revision
		});
	});

	it('sends the captured machine proof when starting sign-in', async () => {
		await startSubscriptionLogin('fixture-token', 'claude', 'device', machineId, revision);
		expect(JSON.parse(request().options.body)).toEqual({
			method: 'device',
			expected_machine_id: machineId,
			expected_machine_revision: revision
		});
	});

	it('sends the captured machine proof when submitting a sign-in code', async () => {
		await submitSubscriptionLoginCode(
			'fixture-token',
			'claude',
			'fixture-code',
			machineId,
			revision
		);
		expect(JSON.parse(request().options.body)).toEqual({
			code: 'fixture-code',
			expected_machine_id: machineId,
			expected_machine_revision: revision
		});
	});

	it('encodes machine proof on sign-in cancellation', async () => {
		await cancelSubscriptionLogin('fixture-token', 'claude', machineId, revision);
		expectGuardedQuery('DELETE', '/api/v1/subscriptions/claude/login');
	});

	it('encodes machine proof on logout', async () => {
		await logoutSubscription('fixture-token', 'claude', machineId, revision);
		expectGuardedQuery('POST', '/api/v1/subscriptions/claude/logout');
	});

	it('retains refresh alongside the captured status proof', async () => {
		await getSubscription('fixture-token', 'claude', true, machineId, revision);
		expectGuardedQuery('GET', '/api/v1/subscriptions/claude');
		expect(request().url.searchParams.get('refresh')).toBe('true');
	});

	it('pins login polling to the same captured computer', async () => {
		await getSubscriptionLogin('fixture-token', 'claude', machineId, revision);
		expectGuardedQuery('GET', '/api/v1/subscriptions/claude/login');
	});

	it('preserves local legacy calls without inventing a remote revision', async () => {
		await updateSubscriptionConfig('fixture-token', 'claude', { enable: false });
		expect(JSON.parse(request().options.body)).toEqual({ enable: false });
	});

	it('surfaces stale proof rejection without retrying on a different computer', async () => {
		fetchMock.mockResolvedValue({
			ok: false,
			status: 409,
			json: async () => ({ detail: 'Selected machine changed' })
		});
		await expect(logoutSubscription('fixture-token', 'claude', machineId, revision)).rejects.toBe(
			'Selected machine changed'
		);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});
});
