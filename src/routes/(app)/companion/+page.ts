import { redirect } from '@sveltejs/kit';

// The Projects page moved into each chat's Project tab, so old links go home.
export function load() {
	redirect(307, '/');
}
