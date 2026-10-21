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
	storable = true;
	// Secure storage that says it works, then fails to save.
	failWrite = false;
	read() {
		return this.value;
	}
	canStore() {
		return this.storable;
	}
	write(credential: string) {
		if (this.failWrite) throw new Error('The connection file could not be written.');
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
	browsing: boolean[] = [];
	setFolderBrowsing(allowed: boolean) {
		this.browsing.push(allowed);
	}
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

type Answers = {
	connect?: boolean;
	browseFolders?: boolean;
	folder?: string | null;
	projectFolder?: string | null;
	permission?: boolean;
	unenrollFails?: boolean;
};

function setUp(answers: Answers = {}, initial: Partial<DesktopSettings> = {}) {
	const settings = new MemorySettings(initial);
	const credentials = new MemoryCredentials();
	const supervisor = new FakeSupervisor();
	const asked: string[] = [];
	const openedAt: (string | undefined)[] = [];
	const enrollments: { token: string; host: HostDetails }[] = [];
	const unenrollments: { serverUrl: string; machineId: string; credential: string }[] = [];
	let loginItem = false;
	const dialogs: Dialogs = {
		confirmConnect: async () => {
			asked.push('connect');
			return { connect: answers.connect ?? true, allowFolderBrowsing: answers.browseFolders ?? false };
		},
		chooseFolder: async () => {
			asked.push('folder');
			return answers.folder ?? null;
		},
		chooseProjectFolder: async (defaultPath?: string) => {
			openedAt.push(defaultPath);
			asked.push('project folder');
			return answers.projectFolder ?? null;
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
		unenroll: async (serverUrl, machineId, credential) => {
			unenrollments.push({ serverUrl, machineId, credential });
			if (answers.unenrollFails) throw new Error('Could not reach Buddy to remove this computer.');
		},
		platform: 'win32',
		appVersion: '0.1.0',
		hostName: 'STUDIO-PC',
		accountName: 'sam'
	});
	return { service, settings, credentials, supervisor, asked, enrollments, unenrollments, openedAt };
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

test('the connect question can allow folder browsing in the same step', async () => {
	const ticked = setUp({ browseFolders: true });
	await ticked.service.connectThisPC('bde1_token', 'PC');
	assert.equal(ticked.settings.saved.allowFolderBrowsing, true);
	assert.equal(ticked.supervisor.started[0].allowFolderBrowsing, true);

	const unticked = setUp({ browseFolders: false });
	await unticked.service.connectThisPC('bde1_token', 'PC');
	assert.equal(unticked.settings.saved.allowFolderBrowsing, false);
	assert.equal(unticked.supervisor.started[0].allowFolderBrowsing, false);
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

test('folder browsing asks first and changes without restarting the Runner', async () => {
	const declined = setUp({ permission: false });
	await declined.service.connectThisPC('bde1_token', 'PC');
	assert.equal((await declined.service.setPermission('allowFolderBrowsing', true)).allowFolderBrowsing, false);
	assert.deepEqual(declined.supervisor.browsing, []);

	const { service, supervisor, settings, asked } = setUp({ permission: true });
	await service.connectThisPC('bde1_token', 'PC');
	const status = await service.setPermission('allowFolderBrowsing', true);
	assert.equal(status.allowFolderBrowsing, true);
	assert.equal(settings.saved.allowFolderBrowsing, true);
	assert.deepEqual(asked, ['connect', 'allowFolderBrowsing']);
	assert.deepEqual(supervisor.browsing, [true]);
	assert.equal(supervisor.started.length, 1);
	assert.equal(supervisor.stops, 0);
	await service.setPermission('allowFolderBrowsing', false);
	assert.deepEqual(supervisor.browsing, [true, false]);
	assert.deepEqual(asked, ['connect', 'allowFolderBrowsing']);
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

test('changing the Buddy address removes this computer from the old server first', async () => {
	const { service, credentials, settings, unenrollments } = setUp();
	await service.connectThisPC('bde1_token', 'PC');
	await service.changeServer('https://other.example.com');
	assert.deepEqual(unenrollments, [
		{ serverUrl: 'https://buddy.example.com', machineId: 'machine-1', credential: 'bdr1_secret' }
	]);
	assert.equal(credentials.value, null);
	assert.equal(settings.saved.serverUrl, 'https://other.example.com');
	assert.equal(service.getStatus().message, undefined);
});

test('an unreachable old server still lets the address change, and says what is left there', async () => {
	const { service, credentials, settings } = setUp({ unenrollFails: true });
	await service.connectThisPC('bde1_token', 'PC');
	await service.changeServer('https://other.example.com');
	assert.equal(credentials.value, null);
	assert.equal(settings.saved.serverUrl, 'https://other.example.com');
	assert.match(service.getStatus().message ?? '', /buddy\.example\.com did not remove this computer.*Settings → Machines/);
});

test('an unreadable credential on an address change says the computer was left behind', async () => {
	const { service, credentials, settings, unenrollments } = setUp();
	await service.connectThisPC('bde1_token', 'PC');
	credentials.value = null;
	await service.changeServer('https://other.example.com');
	assert.equal(unenrollments.length, 0);
	assert.equal(settings.saved.serverUrl, 'https://other.example.com');
	assert.match(service.getStatus().message ?? '', /buddy\.example\.com did not remove this computer/);
});

test('a computer that cannot store the connection is never enrolled', async () => {
	const { service, credentials, enrollments } = setUp();
	credentials.storable = false;
	await assert.rejects(service.connectThisPC('bde1_token', 'PC'), /cannot store the connection securely/);
	assert.equal(enrollments.length, 0);
});

test('a credential that fails to save is taken back off Buddy', async () => {
	const { service, credentials, settings, enrollments, unenrollments } = setUp();
	credentials.failWrite = true;
	await assert.rejects(service.connectThisPC('bde1_token', 'PC'), /could not be written/);
	assert.equal(enrollments.length, 1);
	assert.deepEqual(unenrollments, [
		{ serverUrl: 'https://buddy.example.com', machineId: 'machine-1', credential: 'bdr1_secret' }
	]);
	assert.equal(settings.saved.machine, null);
});

test('a credential that fails to save and cannot be taken back off says to remove it', async () => {
	const { service, credentials, settings, unenrollments } = setUp({ unenrollFails: true });
	credentials.failWrite = true;
	await assert.rejects(
		service.connectThisPC('bde1_token', 'PC'),
		/Buddy did not remove it again\. Remove it in Settings → Machines\./
	);
	assert.equal(unenrollments.length, 1);
	assert.equal(credentials.value, null);
	assert.equal(settings.saved.machine, null);
	assert.equal(service.getStatus().state, 'not_connected');
});

test('starting at sign-in is reported back', () => {
	const { service } = setUp();
	assert.equal(service.setStartAtLogin(true).startAtLogin, true);
});

test('choosing a project folder returns its path and changes nothing on this computer', async () => {
	const folder = 'C:\\Projects\\site';
	const { service, settings, supervisor, asked, openedAt } = setUp({ projectFolder: folder });
	await service.connectThisPC('bde1_token', 'PC');
	const before = structuredClone(settings.saved);
	const starts = supervisor.started.length;
	assert.equal(await service.chooseProjectFolder(), folder);
	await service.chooseProjectFolder('C:\\Projects');
	assert.deepEqual(openedAt, [undefined, 'C:\\Projects']);
	assert.deepEqual(settings.saved, before);
	assert.equal(supervisor.started.length, starts);
	assert.equal(supervisor.stops, 0);
	assert.deepEqual(asked, ['connect', 'project folder', 'project folder']);
});

test('a project folder can be chosen only on a connected computer', async () => {
	const { service, asked } = setUp({ projectFolder: 'C:\\Projects\\site' });
	await assert.rejects(service.chooseProjectFolder(), /Connect this computer/);
	assert.deepEqual(asked, []);
});
