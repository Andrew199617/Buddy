// Gives the Buddy page window.buddyDesktop, and only the Buddy page: the app
// passes its address as --buddy-origin, and every request is checked again in
// the main process. Sandboxed, so this file cannot import the app's modules;
// the channel names repeat those in bridge-channels.ts.

import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';

const ORIGIN_ARGUMENT = '--buddy-origin=';

function buddyOrigin(): string {
	const argument = process.argv.find((value) => value.startsWith(ORIGIN_ARGUMENT));
	if (!argument) return '';
	return argument.slice(ORIGIN_ARGUMENT.length);
}

// Electron prefixes errors from the main process; the page shows just the reason.
function cleanError(error: unknown): Error {
	const message = String((error as Error)?.message ?? error);
	return new Error(message.replace(/^Error invoking remote method '[^']+': (Error: )?/, ''));
}

async function invoke<T>(channel: string, ...args: unknown[]): Promise<T> {
	try {
		return await ipcRenderer.invoke(channel, ...args);
	} catch (error) {
		throw cleanError(error);
	}
}

type StatusListener = (status: unknown) => void;

function onStatusChanged(listener: StatusListener): () => void {
	const relay = (_event: IpcRendererEvent, status: unknown) => listener(status);
	ipcRenderer.on('buddy-desktop:status', relay);
	return () => {
		ipcRenderer.removeListener('buddy-desktop:status', relay);
	};
}

const buddyDesktop = {
	getStatus: () => invoke('buddy-desktop:get-status'),
	connectThisPC: (options: { enrollmentToken?: unknown; name?: unknown }) =>
		invoke('buddy-desktop:connect', {
			enrollmentToken: String(options?.enrollmentToken ?? ''),
			name: String(options?.name ?? '')
		}),
	disconnectThisPC: () => invoke('buddy-desktop:disconnect'),
	chooseSharedFolder: () => invoke('buddy-desktop:choose-folder'),
	chooseProjectFolder: (options?: { defaultPath?: unknown }) =>
		invoke('buddy-desktop:choose-project-folder', { defaultPath: options?.defaultPath }),
	removeSharedFolder: (folder: unknown) => invoke('buddy-desktop:remove-folder', String(folder)),
	setPermission: (permission: unknown, allowed: unknown) =>
		invoke('buddy-desktop:set-permission', String(permission), allowed === true),
	setStartAtLogin: (enabled: unknown) => invoke('buddy-desktop:set-start-at-login', enabled === true),
	openClaudeTokenSetup: () => invoke('buddy-desktop:open-claude-token-setup'),
	onStatusChanged
};

const origin = buddyOrigin();
if (origin && window.location.origin === origin) {
	contextBridge.exposeInMainWorld('buddyDesktop', buddyDesktop);
}
