import fs from 'node:fs';
import { EventEmitter } from 'node:events';
import readline from 'node:readline';
import type { ChildProcess, SpawnOptions } from 'node:child_process';

// Starts the bundled Buddy Runner, keeps it running and reports its status.
// The Runner itself reconnects to Buddy after network trouble; this restarts
// it only if the process itself stops unexpectedly.

export type RunnerState =
	| 'connecting'
	| 'connected'
	| 'offline'
	| 'stopped'
	| 'removed'
	| 'refused'
	| 'replaced'
	| 'needs_update'
	| 'error';

export type RunnerStatus = { state: RunnerState; message: string };

/** How to start the Runner: the bundled buddy-runner.exe, or Python in development. */
export type RunnerLaunch = { command: string; args: string[]; env?: Record<string, string> };

export type RunnerSettings = {
	serverUrl: string;
	machineId: string;
	credential: string;
	machineName: string;
	folders: string[];
	allowEdits: boolean;
	allowCommands: boolean;
	allowFolderBrowsing: boolean;
};

export type SpawnRunner = (command: string, args: string[], options: SpawnOptions) => ChildProcess;

export type RunnerSupervisorOptions = {
	launch: RunnerLaunch;
	stateDir: string;
	spawnRunner: SpawnRunner;
	// The Runner's own log; it never contains the credential.
	logFile?: string;
};

// Exit codes from buddy_runner/connect.py for a connection Buddy ended for good.
const PERMANENT_EXIT_STATES: Record<number, RunnerState> = {
	3: 'removed',
	4: 'refused',
	5: 'replaced',
	6: 'needs_update'
};
const EXIT_USAGE = 2;
const REPORTED_STATES = new Set<string>([
	'connecting',
	'connected',
	'offline',
	'stopped',
	'removed',
	'refused',
	'replaced',
	'needs_update'
]);
// Stopping waits for running Claude Code and Codex processes to stop first.
const STOP_GRACE_MS = 30000;
const FIRST_RESTART_DELAY_MS = 1000;
const MAX_RESTART_DELAY_MS = 60000;
const MAX_LOG_BYTES = 5 * 1024 * 1024;

export function runnerArguments(launch: RunnerLaunch, settings: RunnerSettings, stateDir: string): string[] {
	const args = [...launch.args, 'run', '--connection-stdin', '--state-dir', stateDir];
	if (settings.machineName) args.push('--name', settings.machineName);
	for (const folder of settings.folders) args.push('--root', folder);
	if (settings.allowEdits) args.push('--write');
	if (settings.allowCommands) args.push('--allow-host-execution');
	if (settings.allowFolderBrowsing) args.push('--browse-folders');
	return args;
}

function connectionLine(settings: RunnerSettings): string {
	const connection = {
		server_url: settings.serverUrl,
		machine_id: settings.machineId,
		credential: settings.credential
	};
	return `${JSON.stringify(connection)}\n`;
}

function ignoreError(): void {}

export class RunnerSupervisor extends EventEmitter {
	status: RunnerStatus = { state: 'stopped', message: '' };
	private child: ChildProcess | null = null;
	// The settings to run with; null once stopped or ended for good, so nothing restarts.
	private settings: RunnerSettings | null = null;
	private restartTimer: ReturnType<typeof setTimeout> | null = null;
	private failures = 0;

	constructor(private readonly options: RunnerSupervisorOptions) {
		super();
	}

	/** True while the Runner runs or is about to restart. */
	get active(): boolean {
		return this.settings !== null;
	}

	start(settings: RunnerSettings): void {
		if (this.child) throw new Error('The Buddy Runner is already running.');
		this.clearRestart();
		this.settings = settings;
		this.failures = 0;
		this.spawnChild();
	}

	/**
	 * Turns folder browsing on or off in the running Runner, without restarting
	 * it, so no Claude or Codex turn stops. A restart later keeps the choice.
	 */
	setFolderBrowsing(allowed: boolean): void {
		if (this.settings) this.settings = { ...this.settings, allowFolderBrowsing: allowed };
		this.child?.stdin?.write(`${JSON.stringify({ command: 'folder_browsing', allowed })}\n`);
	}

	/** Asks the Runner to stop its commands and say goodbye to Buddy, then waits for it. */
	async stop(): Promise<void> {
		this.settings = null;
		this.clearRestart();
		const child = this.child;
		if (child) await this.stopChild(child);
		this.setStatus('stopped', '');
	}

	private stopChild(child: ChildProcess): Promise<void> {
		return new Promise((resolve) => {
			const forceStop = setTimeout(() => child.kill(), STOP_GRACE_MS);
			child.once('exit', () => {
				clearTimeout(forceStop);
				resolve();
			});
			child.stdin?.write(`${JSON.stringify({ command: 'stop' })}\n`);
		});
	}

	private spawnChild(): void {
		const settings = this.settings;
		if (!settings) return;
		const { launch, stateDir } = this.options;
		let child: ChildProcess;
		try {
			child = this.options.spawnRunner(launch.command, runnerArguments(launch, settings, stateDir), {
				env: { ...process.env, ...launch.env },
				stdio: ['pipe', 'pipe', 'pipe'],
				windowsHide: true
			});
		} catch (error) {
			this.settings = null;
			this.setStatus('error', `Could not start the Buddy Runner. ${String(error)}`);
			return;
		}
		this.child = child;
		this.setStatus('connecting', '');
		this.watchChild(child);
		// The credential goes over stdin only, so it is never written to disk.
		child.stdin?.write(connectionLine(settings));
	}

	private watchChild(child: ChildProcess): void {
		let finished = false;
		const finish = (code: number | null) => {
			if (finished) return;
			finished = true;
			this.handleExit(child, code);
		};
		child.stdin?.on('error', ignoreError);
		child.on('error', () => finish(null));
		child.on('exit', (code) => finish(code));
		if (child.stdout) {
			const lines = readline.createInterface({ input: child.stdout });
			lines.on('line', (line) => this.handleLine(child, line));
		}
		this.keepLog(child);
	}

	private keepLog(child: ChildProcess): void {
		const stderr = child.stderr;
		if (!stderr) return;
		const logFile = this.options.logFile;
		if (!logFile) {
			stderr.resume();
			return;
		}
		try {
			if (fs.statSync(logFile).size > MAX_LOG_BYTES) fs.renameSync(logFile, `${logFile}.old`);
		} catch {
			// No log yet.
		}
		const log = fs.createWriteStream(logFile, { flags: 'a' });
		log.on('error', ignoreError);
		log.write(`--- Buddy Runner started ${new Date().toISOString()}\n`);
		stderr.pipe(log);
	}

	private handleLine(child: ChildProcess, line: string): void {
		if (child !== this.child) return;
		let event: { event?: unknown; state?: unknown; message?: unknown };
		try {
			event = JSON.parse(line);
		} catch {
			return;
		}
		if (event?.event !== 'status' || typeof event.state !== 'string' || !REPORTED_STATES.has(event.state)) {
			return;
		}
		if (event.state === 'connected') this.failures = 0;
		let message = '';
		if (typeof event.message === 'string') message = event.message;
		this.setStatus(event.state as RunnerState, message);
	}

	private handleExit(child: ChildProcess, code: number | null): void {
		if (child !== this.child) return;
		this.child = null;
		const permanentState = code === null ? undefined : PERMANENT_EXIT_STATES[code];
		if (permanentState) {
			this.settings = null;
			this.setStatus(permanentState, this.messageFor(permanentState));
			return;
		}
		if (!this.settings) {
			// Asked to stop; stop() reports it.
			return;
		}
		if (code === EXIT_USAGE) {
			this.settings = null;
			this.setStatus('error', 'The Buddy Runner could not start. Its log has the details.');
			return;
		}
		this.scheduleRestart();
	}

	/** The message the Runner sent with this state just before exiting, if any. */
	private messageFor(state: RunnerState): string {
		if (this.status.state === state) return this.status.message;
		return '';
	}

	private scheduleRestart(): void {
		this.failures += 1;
		const delay = Math.min(MAX_RESTART_DELAY_MS, FIRST_RESTART_DELAY_MS * 2 ** (this.failures - 1));
		this.setStatus('offline', 'The Buddy Runner stopped unexpectedly. Restarting it.');
		this.restartTimer = setTimeout(() => {
			this.restartTimer = null;
			if (this.settings && !this.child) this.spawnChild();
		}, delay);
	}

	private clearRestart(): void {
		if (this.restartTimer !== null) clearTimeout(this.restartTimer);
		this.restartTimer = null;
	}

	private setStatus(state: RunnerState, message: string): void {
		this.status = { state, message };
		this.emit('status', this.status);
	}
}
