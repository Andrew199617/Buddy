// Which pages the app shows itself, and what they may ask the browser for.

export function isSameOrigin(url: string, origin: string): boolean {
	try {
		return new URL(url).origin === origin;
	} catch {
		return false;
	}
}

/** Links the app hands to the system browser; anything else is ignored. */
export function isWebLink(url: string): boolean {
	try {
		const protocol = new URL(url).protocol;
		return protocol === 'https:' || protocol === 'http:';
	} catch {
		return false;
	}
}

// Buddy uses the microphone for voice input, notifications for replies,
// and the clipboard for copy buttons. Everything else is refused.
const BUDDY_PERMISSIONS = new Set([
	'media',
	'notifications',
	'clipboard-sanitized-write',
	'fullscreen'
]);

export function allowsPermission(permission: string, requestingUrl: string, origin: string): boolean {
	return BUDDY_PERMISSIONS.has(permission) && isSameOrigin(requestingUrl, origin);
}
