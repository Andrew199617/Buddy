import { get } from 'svelte/store';

import { getModels } from '$lib/apis';
import { config, models, settings } from '$lib/stores';

/**
 * Reloads the model picker's list from the server, for example after a Claude
 * or ChatGPT plan is turned on or a computer it runs on changes.
 */
export async function refreshModelList(): Promise<void> {
	let directConnections = null;
	if (get(config)?.features?.enable_direct_connections) {
		directConnections = get(settings)?.directConnections ?? null;
	}
	models.set(await getModels(localStorage.token, directConnections, false, true));
}
