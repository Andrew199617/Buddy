import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import type { IpcMain, IpcMainInvokeEvent } from 'electron';

import { BRIDGE_CHANNELS, SETUP_CHANNELS } from '../src/bridge-channels';
import type { DesktopService } from '../src/desktop-service';
import { registerBuddyBridge, registerSetupBridge } from '../src/ipc';

type Handler = (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown;

class FakeIpc {
	handlers = new Map<string, Handler>();
	handle(channel: string, handler: Handler) {
		this.handlers.set(channel, handler);
	}
	call(channel: string, fromBuddy: boolean, ...args: unknown[]) {
		const event = { fromBuddy } as unknown as IpcMainInvokeEvent;
		return this.handlers.get(channel)!(event, ...args);
	}
}

function bridge() {
	const ipc = new FakeIpc();
	const calls: unknown[][] = [];
	const service = new Proxy(
		{},
		{
			get:
				(_target, name) =>
				(...args: unknown[]) => {
					calls.push([name, ...args]);
					return { state: 'not_connected' };
				}
		}
	) as DesktopService;
	const isBuddyPage = (event: IpcMainInvokeEvent) => (event as unknown as { fromBuddy: boolean }).fromBuddy;
	registerBuddyBridge(ipc as unknown as IpcMain, service, isBuddyPage, { openClaudeTokenSetup: () => {} });
	return { ipc, calls };
}

test('every bridge channel is registered', () => {
	const { ipc } = bridge();
	const requestChannels = Object.values(BRIDGE_CHANNELS).filter((channel) => channel !== BRIDGE_CHANNELS.status);
	assert.deepEqual([...ipc.handlers.keys()].sort(), [...requestChannels].sort());
});

test('pages other than Buddy are refused before anything runs', () => {
	const { ipc, calls } = bridge();
	assert.throws(() => ipc.call(BRIDGE_CHANNELS.disconnect, false), /Only your Buddy address/);
	assert.deepEqual(calls, []);
});

test('requests are checked before they reach the service', () => {
	const { ipc, calls } = bridge();
	assert.throws(() => ipc.call(BRIDGE_CHANNELS.setPermission, true, 'allowEverything', true), /invalid permission/);
	assert.throws(() => ipc.call(BRIDGE_CHANNELS.setPermission, true, 'allowCommands', 'yes'), /invalid permission/);
	assert.throws(() => ipc.call(BRIDGE_CHANNELS.connect, true, { enrollmentToken: 7 }), /invalid connection request/);
	assert.throws(() => ipc.call(BRIDGE_CHANNELS.removeFolder, true, ['C:\\']), /invalid folder/);
	assert.deepEqual(calls, []);
	ipc.call(BRIDGE_CHANNELS.connect, true, { enrollmentToken: 'bde1_token', name: 'Studio PC' });
	ipc.call(BRIDGE_CHANNELS.setPermission, true, 'allowEdits', true);
	ipc.call(BRIDGE_CHANNELS.setPermission, true, 'allowFolderBrowsing', true);
	ipc.call(BRIDGE_CHANNELS.chooseProjectFolder, true, { defaultPath: 'C:\\Projects' });
	ipc.call(BRIDGE_CHANNELS.chooseProjectFolder, true, { defaultPath: 'relative' });
	ipc.call(BRIDGE_CHANNELS.chooseProjectFolder, true, { defaultPath: 'C:\\' + 'a'.repeat(1100) });
	assert.deepEqual(calls, [
		['connectThisPC', 'bde1_token', 'Studio PC'],
		['setPermission', 'allowEdits', true],
		['setPermission', 'allowFolderBrowsing', true],
		['chooseProjectFolder', 'C:\\Projects'],
		['chooseProjectFolder', undefined],
		['chooseProjectFolder', undefined]
	]);
});

test('the setup page bridge answers only the setup page', async () => {
	const ipc = new FakeIpc();
	const saved: string[] = [];
	registerSetupBridge(ipc as unknown as IpcMain, (event) => (event as unknown as { fromBuddy: boolean }).fromBuddy, {
		currentAddress: () => 'https://buddy.example.com',
		saveAddress: async (address) => {
			saved.push(address);
			return {};
		}
	});
	assert.throws(() => ipc.call(SETUP_CHANNELS.saveAddress, false, 'https://evil.test'));
	await ipc.call(SETUP_CHANNELS.saveAddress, true, 'https://buddy.example.com');
	assert.deepEqual(saved, ['https://buddy.example.com']);
});

test('the sandboxed preloads use the same channel names', () => {
	const source = path.join(__dirname, '..', '..', 'src');
	const preload = fs.readFileSync(path.join(source, 'preload.ts'), 'utf8');
	for (const channel of Object.values(BRIDGE_CHANNELS)) {
		assert.ok(preload.includes(`'${channel}'`), `preload.ts uses ${channel}`);
	}
	const setupPreload = fs.readFileSync(path.join(source, 'setup-preload.ts'), 'utf8');
	for (const channel of Object.values(SETUP_CHANNELS)) {
		assert.ok(setupPreload.includes(`'${channel}'`), `setup-preload.ts uses ${channel}`);
	}
});
