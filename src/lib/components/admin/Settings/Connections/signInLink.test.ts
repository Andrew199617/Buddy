import { describe, expect, it } from 'vitest';

import { getHttpsSignInUrl } from './signInLink';

describe('getHttpsSignInUrl', () => {
	it('keeps an HTTPS sign-in address', () => {
		expect(getHttpsSignInUrl('https://auth.openai.com/codex/device?code=1')).toBe(
			'https://auth.openai.com/codex/device?code=1'
		);
	});

	it.each([
		['script', 'javascript:alert(document.cookie)'],
		['script with spaces and case', '  JavaScript:alert(1)'],
		['data', 'data:text/html,<script>alert(1)</script>'],
		['protocol handler', 'ms-officecmd:open'],
		['plain HTTP', 'http://auth.openai.com/oauth/authorize'],
		['relative', '/oauth/authorize'],
		['not a URL', 'open the sign-in page']
	])('refuses a %s address', (_kind, url) => {
		expect(getHttpsSignInUrl(url)).toBeNull();
	});

	it('has nothing to open without an address', () => {
		expect(getHttpsSignInUrl(undefined)).toBeNull();
		expect(getHttpsSignInUrl(null)).toBeNull();
		expect(getHttpsSignInUrl('')).toBeNull();
	});
});
