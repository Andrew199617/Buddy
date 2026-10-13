import { WEBUI_API_BASE_URL } from '$lib/constants';

import { CompanionError, WorkspaceClient } from './companion';

/**
 * Names this page to the computer, so its workspaces and terminals stay its own.
 * getRandomValues also works on a plain-HTTP Buddy address, unlike randomUUID.
 */
export function newRelayClientId(): string {
	const bytes = crypto.getRandomValues(new Uint8Array(16));
	return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * Opens a computer connected through the Buddy app, with Buddy relaying each
 * request over that computer's own connection. Buddy checks that the computer
 * is yours, so no pairing code is needed; the computer still checks its own
 * shared folders and its edit and command approvals.
 */
export class RelayCompanionClient extends WorkspaceClient {
	readonly machineId: string;
	private readonly buddyToken: string;
	private readonly clientId = newRelayClientId();

	constructor(machineId: string, buddyToken: string) {
		super();
		this.machineId = machineId;
		this.buddyToken = buddyToken;
	}

	private relayUrl(path: string): string {
		return `${WEBUI_API_BASE_URL}/runners/machines/${encodeURIComponent(this.machineId)}/workspace${path}`;
	}

	private relayHeaders(withBody: boolean): Record<string, string> {
		const headers: Record<string, string> = {
			Accept: 'application/json',
			Authorization: `Bearer ${this.buddyToken}`,
			'X-Buddy-Relay-Client': this.clientId
		};
		if (withBody) headers['Content-Type'] = 'application/json';
		return headers;
	}

	protected send(path: string, method: string, body: unknown, signal: AbortSignal) {
		return fetch(this.relayUrl(path), {
			method,
			headers: this.relayHeaders(body !== undefined),
			body: body === undefined ? undefined : JSON.stringify(body),
			redirect: 'error',
			referrerPolicy: 'no-referrer',
			signal
		});
	}

	/**
	 * Stops this page's commands on the computer. When Buddy has signed this page
	 * out or the computer is offline, the session ends on its own: the computer
	 * stops relayed sessions when its connection ends and expires idle ones.
	 */
	protected async endSession(): Promise<void> {
		const controller = new AbortController();
		const timeout = setTimeout(() => controller.abort(), 5000);
		try {
			const response = await fetch(this.relayUrl('/v1/session'), {
				method: 'DELETE',
				headers: this.relayHeaders(false),
				redirect: 'error',
				referrerPolicy: 'no-referrer',
				keepalive: true,
				signal: controller.signal
			});
			const sessionAlreadyEnded = response.status === 401 || response.status === 503;
			if (!response.ok && !sessionAlreadyEnded) {
				throw new CompanionError(
					'Could not end this page’s session on the computer.',
					response.status
				);
			}
		} finally {
			clearTimeout(timeout);
		}
	}
}
