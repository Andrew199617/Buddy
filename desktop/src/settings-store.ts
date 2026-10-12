import fs from 'node:fs';
import path from 'node:path';

// The app's own settings. Shared folders and permissions live only here, on
// this computer, so a Buddy server can never widen them.
export type DesktopSettings = {
	serverUrl: string | null;
	machine: { id: string; name: string } | null;
	folders: string[];
	allowEdits: boolean;
	allowCommands: boolean;
};

export function defaultSettings(): DesktopSettings {
	return { serverUrl: null, machine: null, folders: [], allowEdits: false, allowCommands: false };
}

function readMachine(value: unknown): DesktopSettings['machine'] {
	const machine = value as { id?: unknown; name?: unknown } | null;
	if (typeof machine?.id !== 'string' || !machine.id) return null;
	let name = '';
	if (typeof machine.name === 'string') name = machine.name;
	return { id: machine.id, name };
}

function readFolders(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	return value.filter((folder): folder is string => typeof folder === 'string' && path.isAbsolute(folder));
}

/** Anything unreadable falls back to the safe default: nothing shared, nothing allowed. */
export function parseSettings(text: string): DesktopSettings {
	let saved: Record<string, unknown>;
	try {
		saved = JSON.parse(text);
	} catch {
		return defaultSettings();
	}
	if (!saved || typeof saved !== 'object') return defaultSettings();
	let serverUrl: string | null = null;
	if (typeof saved.serverUrl === 'string' && saved.serverUrl) serverUrl = saved.serverUrl;
	return {
		serverUrl,
		machine: readMachine(saved.machine),
		folders: readFolders(saved.folders),
		allowEdits: saved.allowEdits === true,
		allowCommands: saved.allowCommands === true
	};
}

export class SettingsStore {
	constructor(private readonly file: string) {}

	load(): DesktopSettings {
		let text: string;
		try {
			text = fs.readFileSync(this.file, 'utf8');
		} catch {
			return defaultSettings();
		}
		return parseSettings(text);
	}

	save(settings: DesktopSettings): void {
		fs.mkdirSync(path.dirname(this.file), { recursive: true });
		const temporary = `${this.file}.tmp`;
		fs.writeFileSync(temporary, JSON.stringify(settings, null, '\t'));
		fs.renameSync(temporary, this.file);
	}
}
