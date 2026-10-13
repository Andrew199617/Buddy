// The Buddy address the app opens and connects this computer to.

export function isLoopbackHost(hostname: string): boolean {
	if (hostname === 'localhost' || hostname === '[::1]') return true;
	const octets = hostname.split('.');
	if (octets.length !== 4 || octets[0] !== '127') return false;
	return octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255);
}

/**
 * The address as an origin, such as https://buddy.example.com. HTTPS is
 * required, because this computer's credential travels to that address;
 * plain HTTP is allowed only for Buddy running on this computer.
 */
export function normalizeServerAddress(input: string): string {
	const text = input.trim();
	if (!text) throw new Error('Enter your Buddy address, such as https://buddy.example.com.');
	let withScheme = text;
	if (!/^[a-z][a-z\d+.-]*:\/\//i.test(text)) withScheme = `https://${text}`;
	let url: URL;
	try {
		url = new URL(withScheme);
	} catch {
		throw new Error('Enter your Buddy address, such as https://buddy.example.com.');
	}
	if (url.protocol !== 'https:' && url.protocol !== 'http:') {
		throw new Error('Enter an https:// address.');
	}
	if (url.username || url.password) {
		throw new Error('The address cannot include a user name or password.');
	}
	if (url.pathname !== '/' || url.search || url.hash) {
		throw new Error('Enter only the address, without a path.');
	}
	if (url.protocol === 'http:' && !isLoopbackHost(url.hostname)) {
		throw new Error('Use an https:// address. Plain http:// is allowed only for Buddy on this computer.');
	}
	return url.origin;
}

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

/** Checks that a Buddy server answers at this address; returns its name. */
export async function checkBuddyServer(origin: string, fetchImpl: FetchLike): Promise<string> {
	let response: Response;
	try {
		response = await fetchImpl(`${origin}/api/config`, {
			headers: { Accept: 'application/json' },
			redirect: 'error',
			signal: AbortSignal.timeout(10000)
		});
	} catch {
		throw new Error('Could not reach that address. Check it and your connection.');
	}
	const config = (await response.json().catch(() => null)) as { name?: unknown } | null;
	if (!response.ok || typeof config?.name !== 'string') {
		throw new Error('No Buddy server answered at that address.');
	}
	return config.name;
}
