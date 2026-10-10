import { writable } from 'svelte/store';
import type { SessionUser } from '$lib/stores';

export type DockFeatures = {
	enable_notes?: boolean;
	enable_automations?: boolean;
	enable_plugins?: boolean;
};

export type DockNavigation = {
	notesVisible: boolean;
	knowledgeVisible: boolean;
	automationsVisible: boolean;
	workspaceHref: string | null;
};

export type DockState = {
	mounted: boolean;
	keyboardOpen: boolean;
};

export type NavigationFallbacks = {
	notes: boolean;
	workspace: boolean;
	automations: boolean;
};

// The dock owns its keyboard visibility and mount lifecycle. The sidebar uses
// that same state so hidden navigation never removes a sidebar fallback.
export const buddyDockState = writable<DockState>({ mounted: false, keyboardOpen: false });

export function getWorkspaceDockDestination(
	currentUser: SessionUser | undefined,
	features: DockFeatures | undefined
): string | null {
	if (currentUser?.role === 'admin' || currentUser?.permissions?.workspace?.models) {
		return '/workspace/models';
	}
	if (currentUser?.permissions?.workspace?.prompts) {
		return '/workspace/prompts';
	}
	if (features?.enable_plugins && currentUser?.permissions?.workspace?.tools) {
		return '/workspace/tools';
	}
	if (currentUser?.permissions?.workspace?.skills) {
		return '/workspace/skills';
	}
	return null;
}

export function getWorkspaceLandingDestination(
	currentUser: SessionUser | undefined,
	features: DockFeatures | undefined
): string | null {
	if (currentUser?.role === 'admin' || currentUser?.permissions?.workspace?.models) {
		return '/workspace/models';
	}
	if (currentUser?.permissions?.workspace?.knowledge) {
		return '/workspace/knowledge';
	}
	return getWorkspaceDockDestination(currentUser, features);
}

export function getDockNavigation(
	currentUser: SessionUser | undefined,
	features: DockFeatures | undefined
): DockNavigation {
	const admin = currentUser?.role === 'admin';
	return {
		notesVisible:
			(features?.enable_notes ?? false) &&
			(admin || (currentUser?.permissions?.features?.notes ?? true)),
		knowledgeVisible: admin || Boolean(currentUser?.permissions?.workspace?.knowledge),
		automationsVisible:
			(features?.enable_automations ?? false) &&
			(admin || Boolean(currentUser?.permissions?.features?.automations)),
		workspaceHref: getWorkspaceDockDestination(currentUser, features)
	};
}

export function isDockAvailable(state: DockState, mobile: boolean, sidebarOpen: boolean): boolean {
	return state.mounted && !state.keyboardOpen && !(mobile && sidebarOpen);
}

export function hasDockDestination(
	navigation: DockNavigation,
	destination: string | null
): boolean {
	if (!destination) {
		return false;
	}
	if (destination === '/notes') {
		return navigation.notesVisible;
	}
	if (destination === '/workspace/knowledge') {
		return navigation.knowledgeVisible;
	}
	if (destination === '/automations') {
		return navigation.automationsVisible;
	}
	return navigation.workspaceHref === destination;
}

export function getNavigationFallbacks(
	currentUser: SessionUser | undefined,
	features: DockFeatures | undefined,
	state: DockState,
	mobile: boolean,
	sidebarOpen: boolean
): NavigationFallbacks {
	const navigation = getDockNavigation(currentUser, features);
	const dockAvailable = isDockAvailable(state, mobile, sidebarOpen);
	const workspaceDestination = getWorkspaceLandingDestination(currentUser, features);
	const workspace = currentUser?.permissions?.workspace;
	const workspaceAllowed = Boolean(
		currentUser?.role === 'admin' ||
		workspace?.models ||
		workspace?.knowledge ||
		workspace?.prompts ||
		workspace?.tools ||
		workspace?.skills
	);

	return {
		notes: navigation.notesVisible && !(dockAvailable && hasDockDestination(navigation, '/notes')),
		workspace:
			workspaceAllowed && !(dockAvailable && hasDockDestination(navigation, workspaceDestination)),
		automations:
			navigation.automationsVisible &&
			!(dockAvailable && hasDockDestination(navigation, '/automations'))
	};
}
