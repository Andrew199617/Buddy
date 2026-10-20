import { EventEmitter } from 'node:events';

import type { CredentialStore } from './credential-store';
import type { EnrolledComputer, HostDetails } from './enrollment';
import type { RunnerSettings, RunnerStatus, RunnerSupervisor } from './runner-supervisor';
import type { DesktopSettings, SettingsStore } from './settings-store';
import type { DesktopConnectionState, DesktopPermission, DesktopStatus } from './status';

// What window.buddyDesktop does. The page may ask for anything here; the
// decisions that widen access (connecting, sharing a folder, allowing edits or
// commands) are made by the person in the app's own dialogs.

// The answer to the connect question, with its folder browsing checkbox.
export type ConnectAnswer = { connect: boolean; allowFolderBrowsing: boolean };

export type Dialogs = {
	confirmConnect(serverUrl: string, accountName: string): Promise<ConnectAnswer>;
	chooseFolder(): Promise<string | null>;
	chooseProjectFolder(defaultPath?: string): Promise<string | null>;
	confirmPermission(permission: DesktopPermission): Promise<boolean>;
};

export type LoginItem = {
	isEnabled(): boolean;
	setEnabled(enabled: boolean): void;
};

export type DesktopServiceOptions = {
	settings: SettingsStore;
	credentials: CredentialStore;
	supervisor: RunnerSupervisor;
	dialogs: Dialogs;
	loginItem: LoginItem;
	enroll: (serverUrl: string, token: string, host: HostDetails) => Promise<EnrolledComputer>;
	unenroll: (serverUrl: string, machineId: string, credential: string) => Promise<void>;
	platform: string;
	appVersion: string;
	hostName: string;
	// The OS account Claude Code and Codex run as.
	accountName: string;
};

type ConnectionState = { state: DesktopConnectionState; message: string };

const MAX_SHARED_FOLDERS = 20;
const MAX_NAME_LENGTH = 80;
// Buddy's enrollment tokens are short URL-safe strings.
const ENROLLMENT_TOKEN = /^[A-Za-z0-9_-]{1,200}$/;

export class DesktopService extends EventEmitter {
	private settings: DesktopSettings;
	// How the last connection ended, shown until this computer connects again.
	private ending: ConnectionState | null = null;
	private connecting = false;

	constructor(private readonly options: DesktopServiceOptions) {
		super();
		this.settings = options.settings.load();
		options.supervisor.on('status', (status: RunnerStatus) => this.handleRunnerStatus(status));
	}

	get serverUrl(): string | null {
		return this.settings.serverUrl;
	}

	get isConnected(): boolean {
		return this.settings.machine !== null;
	}

	/** Reconnects a computer connected in an earlier session. */
	start(): void {
		if (!this.settings.machine) return;
		const runnerSettings = this.runnerSettings();
		if (!runnerSettings) {
			this.forgetMachine();
			this.ending = {
				state: 'error',
				message: "This computer's saved connection could not be read. Connect it again."
			};
			this.emitStatus();
			return;
		}
		this.options.supervisor.start(runnerSettings);
	}

	getStatus(): DesktopStatus {
		const connection = this.connectionState();
		const status: DesktopStatus = {
			state: connection.state,
			machineName: this.settings.machine?.name || this.options.hostName,
			folders: [...this.settings.folders],
			allowEdits: this.settings.allowEdits,
			allowCommands: this.settings.allowCommands,
			allowFolderBrowsing: this.settings.allowFolderBrowsing,
			startAtLogin: this.options.loginItem.isEnabled(),
			platform: this.options.platform,
			appVersion: this.options.appVersion
		};
		if (this.settings.machine) status.machineId = this.settings.machine.id;
		if (connection.message) status.message = connection.message;
		return status;
	}

	async connectThisPC(token: string, name: string): Promise<DesktopStatus> {
		const serverUrl = this.settings.serverUrl;
		if (!serverUrl) throw new Error('Set your Buddy address first.');
		if (this.settings.machine) return this.getStatus();
		if (this.connecting) throw new Error('This computer is already connecting.');
		if (!ENROLLMENT_TOKEN.test(token)) throw new Error('Buddy sent an invalid connection request.');
		const answer = await this.options.dialogs.confirmConnect(serverUrl, this.options.accountName);
		if (!answer.connect) return this.getStatus();
		// Checked first: once Buddy enrolls this computer, a lost credential leaves it unusable there.
		if (!this.options.credentials.canStore()) {
			throw new Error('This computer cannot store the connection securely, so it was not connected.');
		}

		this.connecting = true;
		this.emitStatus();
		try {
			const host: HostDetails = {
				name: cleanName(name) || this.options.hostName,
				platform: this.options.platform,
				version: this.options.appVersion
			};
			const enrolled = await this.options.enroll(serverUrl, token, host);
			try {
				this.options.credentials.write(enrolled.credential);
			} catch (error) {
				// Buddy already added this computer; take it back off so it doesn't count against the limit.
				await this.removeFromServer(serverUrl, enrolled.machineId, enrolled.credential);
				throw error;
			}
			this.settings.machine = { id: enrolled.machineId, name: enrolled.name };
			this.settings.allowFolderBrowsing = answer.allowFolderBrowsing;
			this.saveSettings();
			this.ending = null;
		} finally {
			this.connecting = false;
			this.emitStatus();
		}
		this.start();
		return this.getStatus();
	}

	/** Stops the Runner and forgets the credential. The page removes the computer from Buddy. */
	async disconnectThisPC(): Promise<DesktopStatus> {
		await this.options.supervisor.stop();
		this.forgetMachine();
		this.ending = null;
		this.emitStatus();
		return this.getStatus();
	}

	async chooseSharedFolder(): Promise<DesktopStatus> {
		const folder = await this.options.dialogs.chooseFolder();
		if (!folder || this.settings.folders.includes(folder)) return this.getStatus();
		if (this.settings.folders.length >= MAX_SHARED_FOLDERS) {
			throw new Error(`You can share up to ${MAX_SHARED_FOLDERS} folders.`);
		}
		this.settings.folders = [...this.settings.folders, folder];
		await this.applyLocalChange();
		return this.getStatus();
	}

	/**
	 * Picks a chat folder's project folder on this computer and returns its path.
	 * It shares nothing and changes no permission: Buddy checks the folder on this
	 * computer through the Runner before it is used.
	 */
	async chooseProjectFolder(defaultPath?: string): Promise<string | null> {
		if (!this.settings.machine) throw new Error('Connect this computer to Buddy first.');
		return this.options.dialogs.chooseProjectFolder(defaultPath);
	}

	async removeSharedFolder(folder: string): Promise<DesktopStatus> {
		if (!this.settings.folders.includes(folder)) return this.getStatus();
		this.settings.folders = this.settings.folders.filter((shared) => shared !== folder);
		await this.applyLocalChange();
		return this.getStatus();
	}

	async setPermission(permission: DesktopPermission, allowed: boolean): Promise<DesktopStatus> {
		if (this.settings[permission] === allowed) return this.getStatus();
		if (allowed) {
			const confirmed = await this.options.dialogs.confirmPermission(permission);
			if (!confirmed) return this.getStatus();
		}
		this.settings[permission] = allowed;
		if (permission === 'allowFolderBrowsing') {
			// Changes in the running Runner, so no Claude or Codex turn stops.
			this.saveSettings();
			this.options.supervisor.setFolderBrowsing(allowed);
			this.emitStatus();
			return this.getStatus();
		}
		await this.applyLocalChange();
		return this.getStatus();
	}

	setStartAtLogin(enabled: boolean): DesktopStatus {
		this.options.loginItem.setEnabled(enabled);
		this.emitStatus();
		return this.getStatus();
	}

	/**
	 * A computer's connection belongs to one Buddy server, so changing it removes
	 * this computer from the old server first, while the credential still exists.
	 */
	async changeServer(serverUrl: string): Promise<void> {
		const oldServer = this.settings.serverUrl;
		if (serverUrl === oldServer) return;
		const machine = this.settings.machine;
		let leftBehind = false;
		if (machine) {
			const credential = this.options.credentials.read();
			if (oldServer && credential) {
				leftBehind = !(await this.removeFromServer(oldServer, machine.id, credential));
			}
			await this.disconnectThisPC();
		}
		this.settings.serverUrl = serverUrl;
		this.saveSettings();
		if (leftBehind) {
			this.ending = {
				state: 'not_connected',
				message: `${oldServer} could not be reached to remove this computer. Remove it in Settings → Machines there.`
			};
			this.emitStatus();
		}
	}

	/** Removes this computer from a server; false when that did not happen. */
	private async removeFromServer(serverUrl: string, machineId: string, credential: string): Promise<boolean> {
		try {
			await this.options.unenroll(serverUrl, machineId, credential);
			return true;
		} catch {
			return false;
		}
	}

	async shutdown(): Promise<void> {
		await this.options.supervisor.stop();
	}

	private connectionState(): ConnectionState {
		if (this.connecting) return { state: 'connecting', message: '' };
		if (!this.settings.machine) return this.ending ?? { state: 'not_connected', message: '' };
		return this.options.supervisor.status;
	}

	private runnerSettings(): RunnerSettings | null {
		const machine = this.settings.machine;
		const serverUrl = this.settings.serverUrl;
		if (!machine || !serverUrl) return null;
		const credential = this.options.credentials.read();
		if (!credential) return null;
		return {
			serverUrl,
			machineId: machine.id,
			credential,
			machineName: machine.name,
			folders: [...this.settings.folders],
			allowEdits: this.settings.allowEdits,
			allowCommands: this.settings.allowCommands,
			allowFolderBrowsing: this.settings.allowFolderBrowsing
		};
	}

	/** Saves a folder or permission change and restarts a running Runner with it. */
	private async applyLocalChange(): Promise<void> {
		this.saveSettings();
		if (this.options.supervisor.active) {
			await this.options.supervisor.stop();
			this.start();
		}
		this.emitStatus();
	}

	private handleRunnerStatus(status: RunnerStatus): void {
		if (status.state === 'removed' && this.settings.machine) {
			// Buddy removed this computer, so its credential no longer works.
			this.forgetMachine();
			this.ending = { state: 'removed', message: status.message };
		}
		this.emitStatus();
	}

	private forgetMachine(): void {
		this.options.credentials.delete();
		this.settings.machine = null;
		this.saveSettings();
	}

	private saveSettings(): void {
		this.options.settings.save(this.settings);
	}

	private emitStatus(): void {
		this.emit('status', this.getStatus());
	}
}

function cleanName(name: string): string {
	return name.trim().slice(0, MAX_NAME_LENGTH);
}
