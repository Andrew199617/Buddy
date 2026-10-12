// Trades the page's single-use token for this computer's machine credential.
// The app's main process makes this request itself, so the credential never
// reaches the web page.

export const ENROLL_PATH = '/api/v1/runners/enroll';

export type HostDetails = { name: string; platform: string; version: string };

export type EnrolledComputer = { machineId: string; credential: string; name: string };

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

function refusalMessage(body: unknown, status: number): string {
	const detail = (body as { detail?: unknown } | null)?.detail;
	if (typeof detail === 'string' && detail) return detail;
	return `Buddy did not connect this computer (HTTP ${status}).`;
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
