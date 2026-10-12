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

	it('keeps query guards on browsers without URLSearchParams.size', async () => {
		const size = Object.getOwnPropertyDescriptor(URLSearchParams.prototype, 'size');
		// Safari 16 and Chrome 112 have no size property.
		delete (URLSearchParams.prototype as { size?: number }).size;
		try {
			await logoutSubscription('fixture-token', 'claude', machineId, revision);
		} finally {
			if (size) Object.defineProperty(URLSearchParams.prototype, 'size', size);
		}
		expectGuardedQuery('POST', '/api/v1/subscriptions/claude/logout');
	});

	it('sends no query string when there is nothing to guard', async () => {
		await getSubscriptionLogin('fixture-token', 'claude');
		const [url] = fetchMock.mock.calls[0];
		expect(url).toBe('/api/v1/subscriptions/claude/login');
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

describe('per-user machines and sign-ins', () => {
	it('renames a machine with its own encoded ID', async () => {
		const { renameSubscriptionMachine } = await import('./index');
		await renameSubscriptionMachine('fixture-token', 'machine / A', 'Studio PC');
		const { url, options } = request();
		expect(options.method).toBe('PATCH');
		expect(url.pathname).toBe('/api/v1/subscriptions/machines/machine%20%2F%20A');
		expect(JSON.parse(options.body)).toEqual({ name: 'Studio PC' });
	});

	it('sends an API key only in the request body', async () => {
		const { saveSubscriptionApiKey } = await import('./index');
		await saveSubscriptionApiKey('fixture-token', 'codex', 'sk-fixture');
		const { url, options } = request();
		expect(options.method).toBe('PUT');
		expect(url.pathname).toBe('/api/v1/subscriptions/codex/credentials/api_key');
		expect(url.search).toBe('');
		expect(JSON.parse(options.body)).toEqual({ key: 'sk-fixture' });
	});

	it('tells a project folder that is not available apart from a failed lookup', async () => {
		const { getFolderProject } = await import('./index');
		for (const status of [403, 404]) {
			fetchMock.mockResolvedValueOnce({ ok: false, status, json: async () => ({ detail: 'No' }) });
			await expect(getFolderProject('fixture-token', 'website')).resolves.toBeNull();
		}
		fetchMock.mockResolvedValueOnce({
			ok: false,
			status: 500,
			json: async () => ({ detail: 'Buddy restarted' })
		});
		await expect(getFolderProject('fixture-token', 'website')).rejects.toBe('Buddy restarted');
	});

	it('stores the Claude token under Claude only', async () => {
		const { saveClaudePlanToken } = await import('./index');
		await saveClaudePlanToken('fixture-token', 'sk-ant-oat01-fixture');
		const { url, options } = request();
		expect(options.method).toBe('PUT');
		expect(url.pathname).toBe('/api/v1/subscriptions/claude/credentials/plan_token');
		expect(JSON.parse(options.body)).toEqual({ token: 'sk-ant-oat01-fixture' });
	});

	it('forgets a stored sign-in and switches back to the computer', async () => {
		const { deleteSubscriptionCredential, useMachineSignIn } = await import('./index');
		await deleteSubscriptionCredential('fixture-token', 'claude', 'plan_token');
		expect(request().options.method).toBe('DELETE');
		expect(request().url.pathname).toBe('/api/v1/subscriptions/claude/credentials/plan_token');
		fetchMock.mockClear();
		await useMachineSignIn('fixture-token', 'codex');
		expect(request().options.method).toBe('POST');
		expect(request().url.pathname).toBe('/api/v1/subscriptions/codex/credentials/machine');
	});

	it('reads, saves and clears a chat folder’s project folder by its encoded ID', async () => {
		const { getFolderProject, saveFolderProject, clearFolderProject } = await import('./index');
		await getFolderProject('fixture-token', 'folder / A');
		expect(request().url.pathname).toBe('/api/v1/subscriptions/folders/folder%20%2F%20A/project');
		fetchMock.mockClear();
		await saveFolderProject('fixture-token', 'folder / A', {
			mode: 'folder',
			paths: { 'pc-1': 'C:\\code\\site' }
		});
		expect(request().options.method).toBe('PUT');
		expect(JSON.parse(request().options.body)).toEqual({
			mode: 'folder',
			paths: { 'pc-1': 'C:\\code\\site' }
		});
		fetchMock.mockClear();
		await clearFolderProject('fixture-token', 'folder / A');
		expect(request().options.method).toBe('DELETE');
	});

	it('checks a folder on a computer with the path only in the body', async () => {
		const { checkMachineFolder } = await import('./index');
		await checkMachineFolder('fixture-token', 'pc / 1', 'C:\\code\\site');
		const { url, options } = request();
		expect(options.method).toBe('POST');
		expect(url.pathname).toBe('/api/v1/subscriptions/machines/pc%20%2F%201/folders/check');
		expect(url.search).toBe('');
		expect(JSON.parse(options.body)).toEqual({ path: 'C:\\code\\site' });
	});

	it('lists a computer’s folders with the path in the query, never the URL path', async () => {
		const { listMachineFolders, getSuggestedFolders } = await import('./index');
		await listMachineFolders('fixture-token', 'pc / 1', {
			path: 'C:\\code',
			hidden: true,
			prefix: 'si'
		});
		const { url, options } = request();
		expect(options.method).toBe('GET');
		expect(url.pathname).toBe('/api/v1/subscriptions/machines/pc%20%2F%201/folders');
		expect(url.searchParams.get('path')).toBe('C:\\code');
		expect(url.searchParams.get('hidden')).toBe('true');
		expect(url.searchParams.get('prefix')).toBe('si');
		fetchMock.mockClear();
		await listMachineFolders('fixture-token', 'pc-1');
		expect(request().url.searchParams.get('path')).toBe('');
		expect(request().url.searchParams.get('hidden')).toBe('false');
		fetchMock.mockClear();
		await getSuggestedFolders('fixture-token', 'pc / 1');
		expect(request().url.pathname).toBe(
			'/api/v1/subscriptions/machines/pc%20%2F%201/folders/suggested'
		);
	});

	it('asks for the bar’s options with the folder and this computer in the query', async () => {
		const { getRunTargetOptions } = await import('./index');
		await getRunTargetOptions('fixture-token', { folderId: 'folder / 1', thisMachineId: 'pc 1' });
		const { url, options } = request();
		expect(options.method).toBe('GET');
		expect(url.pathname).toBe('/api/v1/subscriptions/run-target/options');
		expect(url.searchParams.get('folder_id')).toBe('folder / 1');
		expect(url.searchParams.get('this_machine_id')).toBe('pc 1');
		fetchMock.mockClear();
		await getRunTargetOptions('fixture-token');
		expect(request().url.search).toBe('');
	});

	it('checks one plan on one computer by query', async () => {
		const { getRunTargetReadiness } = await import('./index');
		await getRunTargetReadiness('fixture-token', 'codex', 'pc / 1');
		const { url } = request();
		expect(url.pathname).toBe('/api/v1/subscriptions/run-target/readiness');
		expect(url.searchParams.get('provider')).toBe('codex');
		expect(url.searchParams.get('machine_id')).toBe('pc / 1');
	});

	it('tells a chat that isn’t the person’s apart from a failed lookup', async () => {
		const { getChatRunTarget } = await import('./index');
		fetchMock.mockResolvedValueOnce({
			ok: false,
			status: 404,
			json: async () => ({ detail: 'No' })
		});
		await expect(getChatRunTarget('fixture-token', 'chat / 1')).resolves.toBeNull();
		expect(request().url.pathname).toBe('/api/v1/subscriptions/chats/chat%20%2F%201/run-target');
		fetchMock.mockResolvedValueOnce({
			ok: false,
			status: 500,
			json: async () => ({ detail: 'Down' })
		});
		await expect(getChatRunTarget('fixture-token', 'chat-1')).rejects.toBe('Down');
	});

	it('signs in and out on a chosen computer, separate from the plan’s own', async () => {
		await startSubscriptionLogin(
			'fixture-token',
			'claude',
			'device',
			undefined,
			revision,
			machineId
		);
		expect(JSON.parse(request().options.body)).toEqual({
			method: 'device',
			expected_machine_revision: revision,
			machine_id: machineId
		});
		fetchMock.mockClear();
		await getSubscriptionLogin('fixture-token', 'claude', undefined, revision, machineId);
		let url = request().url;
		expect(url.searchParams.get('machine_id')).toBe(machineId);
		expect(url.searchParams.get('expected_machine_id')).toBeNull();
		fetchMock.mockClear();
		await logoutSubscription('fixture-token', 'claude', undefined, revision, machineId);
		url = request().url;
		expect(url.pathname).toBe('/api/v1/subscriptions/claude/logout');
		expect(url.searchParams.get('machine_id')).toBe(machineId);
	});

	it('reads and saves a computer’s own settings by its encoded ID', async () => {
		const { getComputerSettings, saveComputerSettings } = await import('./index');
		await getComputerSettings('fixture-token', 'pc / 1');
		expect(request().url.pathname).toBe('/api/v1/subscriptions/machines/pc%20%2F%201/settings');
		fetchMock.mockClear();
		const settings = {
			expected_revision: 'rev-1',
			start_folder: 'C:\\code',
			cli_paths: { claude: '' }
		};
		await saveComputerSettings('fixture-token', 'pc / 1', settings);
		const { options } = request();
		expect(options.method).toBe('PUT');
		expect(JSON.parse(options.body)).toEqual(settings);
	});

	it('lets an administrator remove anyone’s machine by its encoded ID', async () => {
		const { deleteAnySubscriptionMachine } = await import('./index');
		await deleteAnySubscriptionMachine('fixture-token', 'machine / A');
		const { url, options } = request();
		expect(options.method).toBe('DELETE');
		expect(url.pathname).toBe('/api/v1/subscriptions/admin/machines/machine%20%2F%20A');
	});
});
