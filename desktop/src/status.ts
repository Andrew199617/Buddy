// What the app tells the Buddy page about this computer. Keep in step with
// src/lib/desktop/bridge.ts in the web app, which reads it.

export type DesktopConnectionState =
	| 'not_connected'
	| 'connecting'
	| 'connected'
	| 'offline'
	| 'stopped'
	| 'removed'
	| 'refused'
	| 'replaced'
	| 'needs_update'
	| 'error';

export type DesktopStatus = {
	state: DesktopConnectionState;
	machineName?: string;
	machineId?: string;
	message?: string;
	folders: string[];
	allowEdits: boolean;
	allowCommands: boolean;
	allowFolderBrowsing: boolean;
	startAtLogin: boolean;
	platform: string;
	appVersion: string;
};

export type DesktopPermission = 'allowEdits' | 'allowCommands' | 'allowFolderBrowsing';

export function isDesktopPermission(value: unknown): value is DesktopPermission {
	return value === 'allowEdits' || value === 'allowCommands' || value === 'allowFolderBrowsing';
}
