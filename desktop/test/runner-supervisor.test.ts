import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import type { ChildProcess, SpawnOptions } from 'node:child_process';

import {
	RunnerSupervisor,
	runnerArguments,
	type RunnerSettings,
	type RunnerStatus
} from '../src/runner-supervisor';

const settings: RunnerSettings = {
	serverUrl: 'https://buddy.example.com',
	machineId: 'machine-1',
	credential: 'bdr1_secret',
	machineName: 'Studio PC',
	folders: ['C:\\Projects\\site'],
	allowEdits: true,
	allowCommands: false,
	allowFolderBrowsing: false
};
const launch = { command: 'buddy-runner.exe', args: [] };

class FakeRunner extends EventEmitter {
	stdin = new PassThrough();
	stdout = new PassThrough();
	stderr = new PassThrough();
	written = '';
	killed = false;

	constructor() {
		super();
		this.stdin.on('data', (chunk) => {
			this.written += String(chunk);
		});
	}

	report(state: string, message = '') {
		this.stdout.write(`${JSON.stringify({ event: 'status', state, message })}\n`);
	}

	exit(code: number | null) {
		this.emit('exit', code);
	}

	kill() {
		this.killed = true;
		return true;
	}
}

function supervised() {
	const spawned: { runner: FakeRunner; command: string; args: string[]; options: SpawnOptions }[] = [];
	const supervisor = new RunnerSupervisor({
		launch,
		stateDir: 'C:\\State',
		spawnRunner: (command, args, options) => {
			const runner = new FakeRunner();
			spawned.push({ runner, command, args, options });
			return runner as unknown as ChildProcess;
		}
	});
	const statuses: RunnerStatus[] = [];
	supervisor.on('status', (status: RunnerStatus) => statuses.push(status));
	return { supervisor, spawned, statuses };
}

function nextTick() {
	return new Promise((resolve) => setImmediate(resolve));
}

test('the app passes only local choices as arguments', () => {
	assert.deepEqual(runnerArguments(launch, settings, 'C:\\State'), [
		'run',
		'--connection-stdin',
		'--state-dir',
		'C:\\State',
		'--name',
		'Studio PC',
		'--root',
		'C:\\Projects\\site',
		'--write'
	]);
	const python = { command: 'python.exe', args: ['-m', 'buddy_runner'] };
	const allowed = { ...settings, folders: [], allowEdits: false, allowCommands: true };
	assert.deepEqual(runnerArguments(python, allowed, 'S').slice(0, 3), ['-m', 'buddy_runner', 'run']);
	assert.equal(runnerArguments(python, allowed, 'S').includes('--allow-host-execution'), true);
	const browsing = { ...settings, allowFolderBrowsing: true };
	assert.equal(runnerArguments(launch, browsing, 'S').includes('--browse-folders'), true);
	assert.equal(runnerArguments(launch, settings, 'S').includes('--browse-folders'), false);
});

test('folder browsing changes in the running Runner without a restart', () => {
	const { supervisor, spawned } = supervised();
	supervisor.start(settings);
	const [{ runner }] = spawned;
	runner.written = '';
	supervisor.setFolderBrowsing(true);
	assert.deepEqual(JSON.parse(runner.written), { command: 'folder_browsing', allowed: true });
	assert.equal(spawned.length, 1);
});

test('the credential goes over stdin, never in the arguments', () => {
	const { supervisor, spawned } = supervised();
	supervisor.start(settings);
	const [{ runner, args, options }] = spawned;
	assert.equal(args.join(' ').includes('bdr1_secret'), false);
	assert.equal(options.windowsHide, true);
	assert.deepEqual(JSON.parse(runner.written), {
		server_url: 'https://buddy.example.com',
		machine_id: 'machine-1',
		credential: 'bdr1_secret'
	});
});

test("the Runner's status lines become the app's status", async () => {
	const { supervisor, spawned, statuses } = supervised();
	supervisor.start(settings);
	const [{ runner }] = spawned;
	runner.stdout.write('not json\n');
	runner.stdout.write(`${JSON.stringify({ event: 'status', state: 'surprise' })}\n`);
	runner.report('connected', 'Studio PC');
	await nextTick();
	assert.deepEqual(
		statuses.map((status) => status.state),
		['connecting', 'connected']
	);
	assert.deepEqual(supervisor.status, { state: 'connected', message: 'Studio PC' });
});

test('a computer Buddy removed is not restarted', async () => {
	const { supervisor, spawned } = supervised();
	supervisor.start(settings);
	const [{ runner }] = spawned;
	runner.report('removed', 'This computer was removed from Buddy.');
	await nextTick();
	runner.exit(3);
	assert.deepEqual(supervisor.status, { state: 'removed', message: 'This computer was removed from Buddy.' });
	assert.equal(supervisor.active, false);
	assert.equal(spawned.length, 1);
});

test('the refusal exit codes each end the connection for good', () => {
	const expected: [number, string][] = [
		[4, 'refused'],
		[5, 'replaced'],
		[6, 'needs_update'],
		[2, 'error']
	];
	for (const [code, state] of expected) {
		const { supervisor, spawned } = supervised();
		supervisor.start(settings);
		spawned[0].runner.exit(code);
		assert.equal(supervisor.status.state, state);
		assert.equal(supervisor.active, false);
	}
});

test('an unexpected exit restarts the Runner after a short wait', (context) => {
	context.mock.timers.enable({ apis: ['setTimeout'] });
	const { supervisor, spawned } = supervised();
	supervisor.start(settings);
	spawned[0].runner.exit(1);
	assert.equal(supervisor.status.state, 'offline');
	assert.equal(spawned.length, 1);
	context.mock.timers.tick(1000);
	assert.equal(spawned.length, 2);
	spawned[1].runner.exit(1);
	context.mock.timers.tick(1000);
	assert.equal(spawned.length, 2, 'the second restart waits longer');
	context.mock.timers.tick(1000);
	assert.equal(spawned.length, 3);
});

test('stopping asks the Runner to stop and waits for it', async () => {
	const { supervisor, spawned } = supervised();
	supervisor.start(settings);
	const [{ runner }] = spawned;
	const stopped = supervisor.stop();
	await nextTick();
	assert.match(runner.written, /\{"command":"stop"\}\n$/);
	runner.exit(0);
	await stopped;
	assert.equal(supervisor.status.state, 'stopped');
	assert.equal(runner.killed, false);
	assert.equal(spawned.length, 1);
});

test('stopping during a restart wait cancels the restart', (context) => {
	context.mock.timers.enable({ apis: ['setTimeout'] });
	const { supervisor, spawned } = supervised();
	supervisor.start(settings);
	spawned[0].runner.exit(1);
	void supervisor.stop();
	context.mock.timers.tick(60000);
	assert.equal(spawned.length, 1);
	assert.equal(supervisor.status.state, 'stopped');
});
