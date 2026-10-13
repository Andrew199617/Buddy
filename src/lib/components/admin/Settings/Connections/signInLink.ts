// Sign-in links come from the CLI on the selected machine, so a Runner could
// send any scheme. Only an HTTPS address is safe to open as a link.
export const getHttpsSignInUrl = (url: string | null | undefined): string | null => {
	if (!url) {
		return null;
	}
	let parsed: URL;
	try {
		parsed = new URL(url);
	} catch {
		return null;
	}
	if (parsed.protocol !== 'https:') {
		return null;
	}
	return parsed.href;
};
