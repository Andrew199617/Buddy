import { describe, expect, it } from 'vitest';
import type { SessionUser } from '$lib/stores';
import {
	getDockNavigation,
	getNavigationFallbacks,
	getWorkspaceLandingDestination,
	hasDockDestination,
	isDockAvailable
} from './navigation';

function sessionUser(
	role: string,
	workspace: Record<string, boolean> = {},
	notes = true,
	automations = true
) {
	return {
		role,
		permissions: { workspace, features: { notes, automations } }
	} as unknown as SessionUser;
}

describe('shared bottom navigation destinations', () => {
	it('matches both default sidebar destinations for an administrator', () => {
		const user = sessionUser('admin');
		const navigation = getDockNavigation(user, { enable_notes: true });
		expect(hasDockDestination(navigation, '/notes')).toBe(true);
		expect(getWorkspaceLandingDestination(user, {})).toBe('/workspace/models');
		expect(hasDockDestination(navigation, getWorkspaceLandingDestination(user, {}))).toBe(true);
	});

	it('does not replace Notes when its feature or permission hides the icon', () => {
		expect(hasDockDestination(getDockNavigation(sessionUser('user'), {}), '/notes')).toBe(false);
		expect(
			hasDockDestination(
				getDockNavigation(sessionUser('user', {}, false), { enable_notes: true }),
				'/notes'
			)
		).toBe(false);
	});

	it('uses the Knowledge icon for a knowledge-only Workspace landing destination', () => {
		const user = sessionUser('user', { knowledge: true });
		const navigation = getDockNavigation(user, {});
		expect(navigation.workspaceHref).toBeNull();
		expect(getWorkspaceLandingDestination(user, {})).toBe('/workspace/knowledge');
		expect(hasDockDestination(navigation, getWorkspaceLandingDestination(user, {}))).toBe(true);
	});

	it('keeps distinct Knowledge and Workspace icon targets when both permissions exist', () => {
		const user = sessionUser('user', { knowledge: true, prompts: true });
		const navigation = getDockNavigation(user, {});
		expect(navigation.workspaceHref).toBe('/workspace/prompts');
		expect(getWorkspaceLandingDestination(user, {})).toBe('/workspace/knowledge');
		expect(hasDockDestination(navigation, '/workspace/prompts')).toBe(true);
		expect(hasDockDestination(navigation, '/workspace/knowledge')).toBe(true);
	});

	it('preserves Workspace redirect priority and the tools feature gate', () => {
		const user = sessionUser('user', { models: true, knowledge: true, prompts: true, tools: true });
		expect(getWorkspaceLandingDestination(user, { enable_plugins: true })).toBe(
			'/workspace/models'
		);
		expect(
			getWorkspaceLandingDestination(sessionUser('user', { prompts: true, tools: true }), {})
		).toBe('/workspace/prompts');
		expect(getWorkspaceLandingDestination(sessionUser('user', { tools: true }), {})).toBeNull();
		expect(
			getWorkspaceLandingDestination(sessionUser('user', { tools: true }), { enable_plugins: true })
		).toBe('/workspace/tools');
		expect(
			getWorkspaceLandingDestination(sessionUser('user', { tools: true, skills: true }), {})
		).toBe('/workspace/skills');
	});

	it('keeps the fallback until the dock mounts and whenever the keyboard hides it', () => {
		expect(isDockAvailable({ mounted: false, keyboardOpen: false }, false, true)).toBe(false);
		expect(isDockAvailable({ mounted: true, keyboardOpen: true }, false, true)).toBe(false);
		expect(isDockAvailable({ mounted: true, keyboardOpen: false }, false, true)).toBe(true);
	});

	it('keeps the fallback while a mobile drawer blocks bottom navigation', () => {
		expect(isDockAvailable({ mounted: true, keyboardOpen: false }, true, true)).toBe(false);
		expect(isDockAvailable({ mounted: true, keyboardOpen: false }, true, false)).toBe(true);
	});

	it('matches Automations only when its feature and permission allow its dock icon', () => {
		expect(
			hasDockDestination(
				getDockNavigation(sessionUser('user'), { enable_automations: true }),
				'/automations'
			)
		).toBe(true);
		expect(hasDockDestination(getDockNavigation(sessionUser('user'), {}), '/automations')).toBe(
			false
		);
		expect(
			hasDockDestination(
				getDockNavigation(sessionUser('user', {}, true, false), { enable_automations: true }),
				'/automations'
			)
		).toBe(false);
	});

	it('deduplicates the same three permitted destinations in both navigation fallbacks', () => {
		const user = sessionUser('user', { models: true });
		const features = { enable_notes: true, enable_automations: true };
		expect(
			getNavigationFallbacks(user, features, { mounted: true, keyboardOpen: false }, false, true)
		).toEqual({ notes: false, workspace: false, automations: false });
		expect(
			getNavigationFallbacks(user, features, { mounted: true, keyboardOpen: false }, true, true)
		).toEqual({ notes: true, workspace: true, automations: true });
		expect(
			getNavigationFallbacks(user, features, { mounted: true, keyboardOpen: true }, false, true)
		).toEqual({ notes: true, workspace: true, automations: true });
	});

	it('keeps the exact Workspace fallback when no dock destination reaches its landing page', () => {
		const user = sessionUser('user', { tools: true });
		expect(
			getNavigationFallbacks(user, {}, { mounted: true, keyboardOpen: false }, false, true)
		).toEqual({ notes: false, workspace: true, automations: false });
		expect(
			getNavigationFallbacks(
				user,
				{ enable_plugins: true },
				{ mounted: true, keyboardOpen: false },
				false,
				true
			)
		).toEqual({ notes: false, workspace: false, automations: false });
	});

	it('keeps unauthorized destinations absent even when the entire dock is hidden', () => {
		expect(
			getNavigationFallbacks(
				sessionUser('user', {}, false, false),
				{ enable_notes: true, enable_automations: true },
				{ mounted: false, keyboardOpen: true },
				true,
				true
			)
		).toEqual({ notes: false, workspace: false, automations: false });
	});
});
