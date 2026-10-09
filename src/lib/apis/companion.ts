export const DEFAULT_COMPANION_ENDPOINT = 'http://127.0.0.1:8083';

export type CompanionHost = {
	id: string;
	name: string;
	platform: string;
	version: string;
};

export type CompanionGrant = {
	id: string;
	name: string;
	path: string;
	capabilities: { read: boolean; write: boolean; execute: boolean };
};

export type CompanionDirectoryEntry = {
	name: string;
	path: string;
	type: 'directory' | 'file';
};

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

/** A host address identifies a machine; it never grants filesystem access. */
export function normalizeCompanionEndpoint(value: string): string {
	const input = value.trim();
	const authorityMatch = input.match(/^[a-z][a-z\d+.-]*:\/\/([^/?#]+)(.*)$/i);
	if (!authorityMatch || authorityMatch[1].includes('@')) {
		throw new Error('Enter a host URL without credentials, such as http://127.0.0.1:8083.');
	}
	if (authorityMatch[2] !== '' && authorityMatch[2] !== '/') {
		throw new Error('The host URL cannot include a path, query, or fragment.');
	}

	let url: URL;
	try {
		url = new URL(input);
	} catch {
		throw new Error('Enter a valid companion host URL.');
	}
	const hostname = url.hostname;
	const loopbackAddress =
		/^127(?:\.\d{1,3}){3}$/.test(hostname) &&
		hostname.split('.').every((octet) => Number(octet) <= 255);
	const loopback = hostname === 'localhost' || hostname === '[::1]' || loopbackAddress;
	if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) {
		throw new Error('Use HTTPS for another computer. HTTP is allowed only for loopback hosts.');
	}
	if (url.username || url.password || url.search || url.hash || url.pathname !== '/') {
		throw new Error('The host URL cannot include credentials, a path, query, or fragment.');
	}
	return url.origin;
}

/** Independent of Buddy/model authentication. The short-lived token stays in this instance. */
export class CompanionClient {
	readonly endpoint: string;
	private token = '';
	private closed = false;
	private requests = new Set<AbortController>();

	constructor(endpoint: string = DEFAULT_COMPANION_ENDPOINT) {
		this.endpoint = normalizeCompanionEndpoint(endpoint);
	}

	private async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
		if (this.closed)
			throw new Error('This companion connection is closed. Pair again to reconnect.');
		const controller = new AbortController();
		this.requests.add(controller);
		const timeout = setTimeout(() => controller.abort(), 15000);
		const headers: Record<string, string> = { Accept: 'application/json' };
		if (this.token) headers.Authorization = `Bearer ${this.token}`;
		if (body !== undefined) headers['Content-Type'] = 'application/json';

		try {
			const response = await fetch(`${this.endpoint}${path}`, {
				method,
				headers,
				body: body === undefined ? undefined : JSON.stringify(body),
				credentials: 'omit',
				mode: 'cors',
				redirect: 'error',
				referrerPolicy: 'no-referrer',
				signal: controller.signal
			});
			if (!response.ok) {
				const data = await response.json().catch(() => null);
				const message =
					typeof data?.error === 'string'
						? data.error
						: `Companion request failed (${response.status}).`;
				throw new CompanionError(message, response.status);
			}
			if (response.status === 204) return undefined as T;
			return (await response.json()) as T;
		} finally {
			clearTimeout(timeout);
			this.requests.delete(controller);
		}
	}

	async getHost(): Promise<CompanionHost> {
		const response = await this.request<{ host: CompanionHost }>('/v1/host');
		return response.host;
	}

	async pair(code: string): Promise<{ host: CompanionHost; expiresAt: number }> {
		const response = await this.request<{ token: string; expiresAt: number; host: CompanionHost }>(
			'/v1/pair',
			'POST',
			{ code }
		);
		// If disposed while pairing, revoke any token that arrived after cancellation.
		if (this.closed) {
			await this.revokeToken(response.token);
			throw new Error('The companion connection changed while pairing.');
		}
		if (
			typeof response.token !== 'string' ||
			!response.token ||
			typeof response.host?.id !== 'string' ||
			!response.host.id ||
			!Number.isFinite(response.expiresAt)
		) {
			throw new Error('The companion returned an invalid pairing response.');
		}
		this.token = response.token;
		return { host: response.host, expiresAt: response.expiresAt };
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
		return this.request<{ path: string; content: string }>(`/v1/files?${query}`);
	}

	putFile(grantId: string, path: string, content: string) {
		return this.request<{ path: string }>('/v1/files', 'PUT', { grantId, path, content });
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

	deleteTerminal(terminalId: string) {
		return this.request<void>(`/v1/terminals/${encodeURIComponent(terminalId)}`, 'DELETE');
	}

	private async revokeToken(token: string): Promise<void> {
		const controller = new AbortController();
		const timeout = setTimeout(() => controller.abort(), 5000);
		try {
			const response = await fetch(`${this.endpoint}/v1/session`, {
				method: 'DELETE',
				headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
				credentials: 'omit',
				mode: 'cors',
				redirect: 'error',
				referrerPolicy: 'no-referrer',
				keepalive: true,
				signal: controller.signal
			});
			if (!response.ok && response.status !== 401) {
				throw new CompanionError('Could not revoke the companion session.', response.status);
			}
		} finally {
			clearTimeout(timeout);
		}
	}

	async disconnect(): Promise<void> {
		const token = this.token;
		this.token = '';
		this.closed = true;
		for (const request of this.requests) request.abort();
		this.requests.clear();
		if (token) await this.revokeToken(token);
	}
}
