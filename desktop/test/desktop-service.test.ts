import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import type { CredentialStore } from '../src/credential-store';
import { DesktopService, type Dialogs } from '../src/desktop-service';
import type { HostDetails } from '../src/enrollment';
import type { RunnerSettings, RunnerStatus, RunnerSupervisor } from '../src/runner-supervisor';
import { defaultSettings, type DesktopSettings, type SettingsStore } from '../src/settings-store';
import type { DesktopPermission } from '../src/status';

class MemorySettings {
	saved: DesktopSettings;
	constructor(initial: Partial<DesktopSettings> = {}) {
		this.saved = { ...defaultSettings(), serverUrl: 'https://buddy.example.com', ...initial };
	}
	load() {
		return structuredClone(this.saved);
	}
	save(settings: DesktopSettings) {
		this.saved = structuredClone(settings);
	}
}

class MemoryCredentials {
	value: string | null = null;
	read() {
		return this.value;
	}
	write(credential: string) {
		this.value = credential;
	}
	delete() {
		this.value = null;
	}
}

class FakeSupervisor extends EventEmitter {
	status: RunnerStatus = { state: 'stopped', message: '' };
	started: RunnerSettings[] = [];
	stops = 0;
	active = false;
	start(settings: RunnerSettings) {
		this.started.push(settings);
		this.active = true;
		this.report('connecting');
	}
	async stop() {
		this.stops += 1;
		this.active = false;
		this.report('stopped');
	}
	report(state: RunnerStatus['state'], message = '') {
		this.status = { state, message };
		this.emit('status', this.status);
	}
}

type Answers = { connect?: boolean; folder?: string | null; permission?: boolean };

function setUp(answers: Answers = {}, initial: Partial<DesktopSettings> = {}) {
	const settings = new MemorySettings(initial);
	const credentials = new MemoryCredentials();
	const supervisor = new FakeSupervisor();
	const asked: string[] = [];
	const enrollments: { token: string; host: HostDetails }[] = [];
	let loginItem = false;
	const dialogs: Dialogs = {
		confirmConnect: async () => {
			asked.push('connect');
			return answers.connect ?? true;
		},
		chooseFolder: async () => {
			asked.push('folder');
			return answers.folder ?? null;
		},
		confirmPermission: async (permission: DesktopPermission) => {
			asked.push(permission);
			return answers.permission ?? true;
		}
	};
	const service = new DesktopService({
		settings: settings as unknown as SettingsStore,
		credentials: credentials as unknown as CredentialStore,
		supervisor: supervisor as unknown as RunnerSupervisor,
		dialogs,
		loginItem: { isEnabled: () => loginItem, setEnabled: (enabled) => (loginItem = enabled) },
		enroll: async (_serverUrl, token, host) => {
			enrollments.push({ token, host });
			return { machineId: 'machine-1', credential: 'bdr1_secret', name: host.name };
		},
		platform: 'win32',
		appVersion: '0.1.0',
		hostName: 'STUDIO-PC',
		accountName: 'sam'
	});
	return { service, settings, credentials, supervisor, asked, enrollments };
}

test('connecting asks first, and a no changes nothing', async () => {
	const { service, enrollments, asked } = setUp({ connect: false });
	const status = await service.connectThisPC('bde1_token', 'Studio PC');
	assert.deepEqual(asked, ['connect']);
	assert.equal(enrollments.length, 0);
	assert.equal(status.state, 'not_connected');
});

test('connecting stores the credential and starts the Runner with local choices', async () => {
	const folder = 'C:\\Projects\\site';
	const { service, settings, credentials, supervisor, enrollments } = setUp({}, { folders: [folder] });
	const status = await service.connectThisPC('bde1_token', '  Studio PC  ');
	assert.deepEqual(enrollments, [
		{ token: 'bde1_token', host: { name: 'Studio PC', platform: 'win32', version: '0.1.0' } }
	]);
	assert.equal(credentials.value, 'bdr1_secret');
	assert.deepEqual(settings.saved.machine, { id: 'machine-1', name: 'Studio PC' });
	assert.equal(supervisor.started[0].credential, 'bdr1_secret');
	assert.deepEqual(supervisor.started[0].folders, [folder]);
	assert.equal(status.state, 'connecting');
	assert.equal(status.machineId, 'machine-1');
	// The page's status never carries the credential.
	assert.equal(JSON.stringify(status).includes('bdr1_secret'), false);
});

test('a malformed token is refused before asking', async () => {
	const { service, asked } = setUp();
	await assert.rejects(service.connectThisPC('bde1 token?x=1', 'PC'), /invalid connection request/);
	assert.deepEqual(asked, []);
});

test('without a name the computer uses its own', async () => {
	const { service, enrollments } = setUp();
	await service.connectThisPC('bde1_token', '');
	assert.equal(enrollments[0].host.name, 'STUDIO-PC');
});

test('allowing edits or commands asks first; turning them off does not', async () => {
	const declined = setUp({ permission: false });
	const status = await declined.service.setPermission('allowCommands', true);
	assert.equal(status.allowCommands, false);
	assert.deepEqual(declined.asked, ['allowCommands']);

	const accepted = setUp({ permission: true }, { allowEdits: true });
	assert.equal((await accepted.service.setPermission('allowCommands', true)).allowCommands, true);
	assert.equal((await accepted.service.setPermission('allowEdits', false)).allowEdits, false);
	assert.deepEqual(accepted.asked, ['allowCommands']);
	assert.equal(accepted.settings.saved.allowCommands, true);
});

test('a running Runner restarts with a newly shared folder', async () => {
	const { service, supervisor } = setUp({ folder: 'D:\\Work' });
	await service.connectThisPC('bde1_token', 'PC');
	const status = await service.chooseSharedFolder();
	assert.deepEqual(status.folders, ['D:\\Work']);
	assert.equal(supervisor.stops, 1);
	assert.deepEqual(supervisor.started.at(-1)?.folders, ['D:\\Work']);
	await service.chooseSharedFolder();
	assert.equal(supervisor.stops, 1, 'sharing the same folder again changes nothing');
	await service.removeSharedFolder('D:\\Work');
	assert.deepEqual(supervisor.started.at(-1)?.folders, []);
});

test('changes while disconnected are saved without starting the Runner', async () => {
	const { service, supervisor, settings } = setUp({ folder: 'D:\\Work' });
	await service.chooseSharedFolder();
	assert.deepEqual(settings.saved.folders, ['D:\\Work']);
	assert.equal(supervisor.started.length, 0);
});

test('a computer Buddy removed forgets its credential', async () => {
	const { service, supervisor, credentials, settings } = setUp();
	await service.connectThisPC('bde1_token', 'PC');
	supervisor.report('removed', 'This computer was removed from Buddy.');
	assert.equal(credentials.value, null);
	assert.equal(settings.saved.machine, null);
	assert.equal(service.getStatus().state, 'removed');
});

test('disconnecting stops the Runner and forgets the credential', async () => {
	const { service, supervisor, credentials } = setUp();
	await service.connectThisPC('bde1_token', 'PC');
	const status = await service.disconnectThisPC();
	assert.equal(supervisor.stops, 1);
	assert.equal(credentials.value, null);
	assert.equal(status.state, 'not_connected');
	assert.equal(status.machineId, undefined);
});

test('a saved connection without a readable credential asks to connect again', () => {
	const { service, supervisor, settings } = setUp({}, { machine: { id: 'machine-1', name: 'PC' } });
	service.start();
	assert.equal(supervisor.started.length, 0);
	assert.equal(settings.saved.machine, null);
	assert.equal(service.getStatus().state, 'error');
});

test('changing the Buddy address disconnects this computer first', async () => {
	const { service, credentials, settings } = setUp();
	await service.connectThisPC('bde1_token', 'PC');
	await service.changeServer('https://other.example.com');
	assert.equal(credentials.value, null);
	assert.equal(settings.saved.serverUrl, 'https://other.example.com');
});

test('starting at sign-in is reported back', () => {
	const { service } = setUp();
	assert.equal(service.setStartAtLogin(true).startAtLogin, true);
});
