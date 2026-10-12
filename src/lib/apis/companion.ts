export type CompanionGrant = {
	id: string;
	name: string;
	path: string;
	capabilities: { read: boolean; write: boolean; execute: boolean };
	/** False when the granted directory is missing or was replaced on the host. */
	available: boolean;
	unavailableReason: string | null;
};

export type CompanionDirectoryEntry = {
	name: string;
	path: string;
	type: 'directory' | 'file';
};

export type CompanionFile = {
	path: string;
	content: string;
	/** Identifies the contents that were read; a save made from an older copy is refused. */
	version: string;
};

export type LineEnding = '\n' | '\r\n' | '\r';

/** The line ending most lines of this text use. Text without line breaks counts as LF. */
export function detectLineEnding(text: string): LineEnding {
	const crlf = text.split('\r\n').length - 1;
	const lf = text.split('\n').length - 1 - crlf;
	const cr = text.split('\r').length - 1 - crlf;
	if (crlf > 0 && crlf >= lf && crlf >= cr) return '\r\n';
	if (cr > lf) return '\r';
	return '\n';
}

/** A textarea reports every line break as LF, so the editor works with LF text. */
export function toEditorText(text: string): string {
	return text.replace(/\r\n?/g, '\n');
}

/** Puts the file's own line ending back before saving editor text. */
export function toFileText(editorText: string, lineEnding: LineEnding): string {
	if (lineEnding === '\n') return editorText;
	return editorText.replace(/\n/g, lineEnding);
}

export type CompanionWorkspace = {
	id: string;
	name: string;
	hostId: string;
	grantId: string;
	path: string;
};

export type CompanionTerminal = {
	id: string;
	workspaceId: string;
	status: 'ready' | 'running' | 'exited' | 'closed';
	output: string;
	exitCode: number | null;
};

export class CompanionError extends Error {
	constructor(
		message: string,
		readonly status: number
	) {
		super(message);
		this.name = 'CompanionError';
	}
}

function describeFailure(data: unknown, status: number): string {
	const reply = data as { error?: unknown; detail?: unknown } | null;
	if (typeof reply?.error === 'string') return reply.error;
	// Buddy's relay reports its own failures, such as an offline computer, this way.
	if (typeof reply?.detail === 'string') return reply.detail;
	return `Runner project request failed (${status}).`;
}

/**
 * The Runner's projects protocol. Subclasses decide how a request reaches the
 * computer; the app relays it through Buddy (RelayCompanionClient).
 */
export abstract class WorkspaceClient {
	protected closed = false;
	private requests = new Set<AbortController>();
	private disconnectPromise: Promise<void> | null = null;

	/** Sends one request; the caller owns the timeout and error handling. */
	protected abstract send(
		path: string,
		method: string,
		body: unknown,
		signal: AbortSignal
	): Promise<Response>;

	/** Ends this page's session on the computer, stopping its commands. */
	protected abstract endSession(): Promise<void>;

	protected async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
		if (this.closed)
			throw new Error('This computer connection is closed. Open the computer again to reconnect.');
		const controller = new AbortController();
		this.requests.add(controller);
		const timeout = setTimeout(() => controller.abort(), 15000);
		try {
			const response = await this.send(path, method, body, controller.signal);
			if (!response.ok) {
				const data = await response.json().catch(() => null);
				throw new CompanionError(describeFailure(data, response.status), response.status);
			}
			if (response.status === 204) return undefined as T;
			return (await response.json()) as T;
		} finally {
			clearTimeout(timeout);
			this.requests.delete(controller);
		}
	}

	async getGrants(): Promise<CompanionGrant[]> {
		const response = await this.request<{ grants: CompanionGrant[] }>('/v1/grants');
		return response.grants;
	}

	getDirectory(grantId: string, path = '') {
		const query = new URLSearchParams({ grantId, path });
		return this.request<{ path: string; entries: CompanionDirectoryEntry[] }>(
			`/v1/directories?${query}`
		);
	}

	getFile(grantId: string, path: string) {
		const query = new URLSearchParams({ grantId, path });
		return this.request<CompanionFile>(`/v1/files?${query}`);
	}

	/**
	 * Saves over the version that was opened. If the host copy changed since
	 * then, the Runner refuses with 409 unless overwrite is chosen.
	 */
	putFile(grantId: string, path: string, content: string, baseVersion: string, overwrite = false) {
		return this.request<CompanionFile>('/v1/files', 'PUT', {
			grantId,
			path,
			content,
			baseVersion,
			overwrite
		});
	}

	async createWorkspace(grantId: string, path = ''): Promise<CompanionWorkspace> {
		const response = await this.request<{ workspace: CompanionWorkspace }>(
			'/v1/workspaces',
			'POST',
			{ grantId, path }
		);
		return response.workspace;
	}

	async getWorkspaces(): Promise<CompanionWorkspace[]> {
		const response = await this.request<{ workspaces: CompanionWorkspace[] }>('/v1/workspaces');
		return response.workspaces;
	}

	async createTerminal(workspaceId: string): Promise<CompanionTerminal> {
		const response = await this.request<{ terminal: CompanionTerminal }>('/v1/terminals', 'POST', {
			workspaceId
		});
		return response.terminal;
	}

	async runCommand(
		terminalId: string,
		executable: string,
		args: string[]
	): Promise<CompanionTerminal> {
		const response = await this.request<{ terminal: CompanionTerminal }>(
			`/v1/terminals/${encodeURIComponent(terminalId)}/commands`,
			'POST',
			{ executable, args }
		);
		return response.terminal;
	}

	async getTerminal(terminalId: string): Promise<CompanionTerminal> {
		const response = await this.request<{ terminal: CompanionTerminal }>(
			`/v1/terminals/${encodeURIComponent(terminalId)}`
		);
		return response.terminal;
	}

	/**
	 * Resolves once the Runner confirms that this command session stopped. The Runner
	 * removes a session from a live pairing only after a confirmed stop, so "not found"
	 * means an earlier stop went through and its reply was lost. A retry treats it as stopped.
	 */
	async deleteTerminal(terminalId: string): Promise<void> {
		let response: { terminal: CompanionTerminal };
		try {
			response = await this.request<{ terminal: CompanionTerminal }>(
				`/v1/terminals/${encodeURIComponent(terminalId)}`,
				'DELETE'
			);
		} catch (cause) {
			if (cause instanceof CompanionError && cause.status === 404) return;
			throw cause;
		}
		if (response?.terminal?.id !== terminalId || response.terminal.status !== 'closed') {
			throw new CompanionError(
				'The Runner did not confirm that this command session stopped.',
				502
			);
		}
	}

	disconnect(): Promise<void> {
		if (this.disconnectPromise) return this.disconnectPromise;
		this.closed = true;
		for (const request of this.requests) request.abort();
		this.requests.clear();
		this.disconnectPromise = this.endSession();
		return this.disconnectPromise;
	}
}
