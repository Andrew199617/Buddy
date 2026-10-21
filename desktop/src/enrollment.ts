// Trades the page's single-use token for this computer's machine credential.
// The app's main process makes this request itself, so the credential never
// reaches the web page.

export const ENROLL_PATH = '/api/v1/runners/enroll';
export const UNENROLL_PATH = '/api/v1/runners/unenroll';

export type HostDetails = { name: string; platform: string; version: string };

export type EnrolledComputer = { machineId: string; credential: string; name: string };

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

function refusalMessage(body: unknown, status: number, refused = 'Buddy did not connect this computer'): string {
	const detail = (body as { detail?: unknown } | null)?.detail;
	if (typeof detail === 'string' && detail) return detail;
	return `${refused} (HTTP ${status}).`;
}

export async function enrollComputer(
	serverUrl: string,
	token: string,
	host: HostDetails,
	fetchImpl: FetchLike
): Promise<EnrolledComputer> {
	let response: Response;
	try {
		response = await fetchImpl(`${serverUrl}${ENROLL_PATH}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
			body: JSON.stringify({ token, name: host.name, host }),
			redirect: 'error',
			signal: AbortSignal.timeout(30000)
		});
	} catch {
		throw new Error('Could not reach Buddy to connect this computer. Try again.');
	}
	const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
	if (!response.ok) throw new Error(refusalMessage(body, response.status));
	const machineId = body?.machine_id;
	const credential = body?.credential;
	if (typeof machineId !== 'string' || !machineId || typeof credential !== 'string' || !credential) {
		throw new Error('Buddy sent an incomplete reply. Try connecting again.');
	}
	let name = host.name;
	if (typeof body?.name === 'string' && body.name) name = body.name;
	return { machineId, credential, name };
}

/**
 * Removes this computer from a Buddy server with its own credential. The server
 * answers {"removed": true or false}; false means it was already gone, which
 * counts as done. Anything else throws, including the 404 or 405 of a server
 * too old to have this route, which still has the computer.
 */
export async function unenrollComputer(
	serverUrl: string,
	machineId: string,
	credential: string,
	fetchImpl: FetchLike
): Promise<void> {
	let response: Response;
	try {
		response = await fetchImpl(`${serverUrl}${UNENROLL_PATH}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
			body: JSON.stringify({ machine_id: machineId, credential }),
			redirect: 'error',
			signal: AbortSignal.timeout(30000)
		});
	} catch {
		throw new Error('Could not reach Buddy to remove this computer.');
	}
	const body = await response.json().catch(() => null);
	if (response.ok && typeof body?.removed === 'boolean') return;
	throw new Error(refusalMessage(body, response.status, 'Buddy did not remove this computer'));
}
