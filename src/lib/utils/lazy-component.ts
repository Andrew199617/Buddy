import { writable } from 'svelte/store';

export type LazyComponentState<T> = {
	component: T | null;
	loading: boolean;
	error: boolean;
};

export function createLazyComponent<T>(importComponent: () => Promise<{ default: T }>) {
	const state = writable<LazyComponentState<T>>({
		component: null,
		loading: false,
		error: false
	});
	let component: T | null = null;
	let pending: Promise<void> | null = null;

	async function loadComponent(): Promise<void> {
		try {
			const imported = await importComponent();
			component = imported.default;
			state.set({ component: component, loading: false, error: false });
		} catch (error) {
			console.error('Failed to load feature component:', error);
			state.set({ component: null, loading: false, error: true });
		} finally {
			pending = null;
		}
	}

	function load(): Promise<void> {
		if (component) {
			return Promise.resolve();
		}
		if (pending) {
			return pending;
		}
		// Assign pending before notifying subscribers or calling the importer.
		pending = Promise.resolve().then(loadComponent);
		state.set({ component: null, loading: true, error: false });
		return pending;
	}

	return { subscribe: state.subscribe, load: load };
}
