// Script for pages/offline.html, shown when the Buddy address does not load.
// A plain script, not a module, so it runs as is.

function showOfflineDetails(): void {
	const query = new URLSearchParams(window.location.search);
	const server = query.get('server') ?? '';
	const retry = document.getElementById('retry') as HTMLAnchorElement;
	const address = document.getElementById('server-address') as HTMLElement;
	let serverUrl: URL | null = null;
	try {
		serverUrl = new URL(server);
	} catch {
		serverUrl = null;
	}
	if (serverUrl && (serverUrl.protocol === 'https:' || serverUrl.protocol === 'http:')) {
		retry.href = serverUrl.origin;
		address.textContent = serverUrl.host;
	} else {
		retry.hidden = true;
	}
}

showOfflineDetails();
