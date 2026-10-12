import type { IpcMain, IpcMainInvokeEvent } from 'electron';

import { BRIDGE_CHANNELS, SETUP_CHANNELS } from './bridge-channels';
import type { DesktopService } from './desktop-service';
import { isDesktopPermission } from './status';

// The main-process side of window.buddyDesktop and the setup page. Every
// request is checked against the page that sent it, whatever the preload did.

type SenderCheck = (event: IpcMainInvokeEvent) => boolean;
type Handler = (...args: unknown[]) => unknown;

function handleFrom(ipc: IpcMain, channel: string, isAllowed: SenderCheck, handler: Handler): void {
	ipc.handle(channel, (event, ...args) => {
		if (!isAllowed(event)) throw new Error('Only your Buddy address can use the Buddy app.');
		return handler(...args);
	});
}

function readText(value: unknown, what: string): string {
	if (typeof value !== 'string') throw new Error(`Buddy sent an invalid ${what}.`);
	return value;
}

function readConnectRequest(value: unknown): { token: string; name: string } {
	const request = value as { enrollmentToken?: unknown; name?: unknown } | null;
	return {
		token: readText(request?.enrollmentToken, 'connection request'),
		name: readText(request?.name ?? '', 'computer name')
	};
}

export type BridgeActions = {
	openClaudeTokenSetup: () => void;
};

export function registerBuddyBridge(
	ipc: IpcMain,
	service: DesktopService,
	isBuddyPage: SenderCheck,
	actions: BridgeActions
): void {
	handleFrom(ipc, BRIDGE_CHANNELS.getStatus, isBuddyPage, () => service.getStatus());

	handleFrom(ipc, BRIDGE_CHANNELS.connect, isBuddyPage, (request) => {
		const { token, name } = readConnectRequest(request);
		return service.connectThisPC(token, name);
	});

	handleFrom(ipc, BRIDGE_CHANNELS.disconnect, isBuddyPage, () => service.disconnectThisPC());

	handleFrom(ipc, BRIDGE_CHANNELS.chooseFolder, isBuddyPage, () => service.chooseSharedFolder());

	handleFrom(ipc, BRIDGE_CHANNELS.removeFolder, isBuddyPage, (folder) =>
		service.removeSharedFolder(readText(folder, 'folder'))
	);

	handleFrom(ipc, BRIDGE_CHANNELS.setPermission, isBuddyPage, (permission, allowed) => {
		if (!isDesktopPermission(permission) || typeof allowed !== 'boolean') {
			throw new Error('Buddy sent an invalid permission change.');
		}
		return service.setPermission(permission, allowed);
	});

	handleFrom(ipc, BRIDGE_CHANNELS.setStartAtLogin, isBuddyPage, (enabled) =>
		service.setStartAtLogin(enabled === true)
	);

	handleFrom(ipc, BRIDGE_CHANNELS.openClaudeTokenSetup, isBuddyPage, () => actions.openClaudeTokenSetup());
}

export type SetupActions = {
	currentAddress: () => string | null;
	saveAddress: (address: string) => Promise<{ error?: string }>;
};

export function registerSetupBridge(ipc: IpcMain, isSetupPage: SenderCheck, actions: SetupActions): void {
	handleFrom(ipc, SETUP_CHANNELS.getAddress, isSetupPage, () => actions.currentAddress());

	handleFrom(ipc, SETUP_CHANNELS.saveAddress, isSetupPage, (address) =>
		actions.saveAddress(readText(address, 'address'))
	);
}
