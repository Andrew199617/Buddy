// What the Buddy desktop app offers this page, through window.buddyDesktop.
//
// The app exposes it only to the Buddy address it was set up with. The page can
// ask; the app decides: connecting this PC, sharing a folder and allowing edits
// or commands each show the app's own confirmation, so a page can never widen
// what this computer allows. The machine credential stays inside the app.

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
	// The name this computer has in Buddy, once connected.
	machineName?: string;
	machineId?: string;
	message?: string;
	// Folders this computer shares with Buddy for projects, chosen in the app.
	folders: string[];
	allowEdits: boolean;
	allowCommands: boolean;
	startAtLogin: boolean;
	platform: string;
	appVersion: string;
};

export type DesktopPermission = 'allowEdits' | 'allowCommands';

export type BuddyDesktop = {
	getStatus(): Promise<DesktopStatus>;
	// The page asks Buddy for a single-use token; the app trades it for the credential itself.
	connectThisPC(options: { enrollmentToken: string; name: string }): Promise<DesktopStatus>;
	disconnectThisPC(): Promise<DesktopStatus>;
	chooseSharedFolder(): Promise<DesktopStatus>;
	removeSharedFolder(path: string): Promise<DesktopStatus>;
	setPermission(permission: DesktopPermission, allowed: boolean): Promise<DesktopStatus>;
	setStartAtLogin(enabled: boolean): Promise<DesktopStatus>;
	// Opens a terminal running `claude setup-token`; the user pastes the token into Buddy.
	openClaudeTokenSetup(): Promise<void>;
	// Asks for a project folder in the system folder dialog. Shares nothing and changes no setting.
	// Older apps lack it, so callers check that it exists.
	chooseProjectFolder?(): Promise<string | null>;
	onStatusChanged(listener: (status: DesktopStatus) => void): () => void;
};

declare global {
	interface Window {
		buddyDesktop?: BuddyDesktop;
	}
}

// The desktop app's bridge, or null in an ordinary browser.
export const getDesktop = (): BuddyDesktop | null => {
	if (typeof window === 'undefined') {
		return null;
	}
	return window.buddyDesktop ?? null;
};
