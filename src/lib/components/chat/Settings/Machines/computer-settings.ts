// What the Advanced form for one computer starts with. No requests here.

import type { ComputerSettings, SubscriptionProviderId } from '$lib/apis/subscriptions';

const PROVIDER_IDS: SubscriptionProviderId[] = ['claude', 'codex'];

export type ComputerSettingsDraft = {
	startFolder: string;
	cliPaths: Record<SubscriptionProviderId, string>;
};

/** The start folder every plan saved for this computer, or '' when they differ. */
function sharedStartFolder(loaded: ComputerSettings): string {
	const folders = new Set<string>();
	for (const providerId of PROVIDER_IDS) {
		const fromPlan = loaded.from_plans[providerId];
		if (fromPlan) folders.add(fromPlan.start_folder);
	}
	if (folders.size !== 1) return '';
	return [...folders][0];
}

/**
 * The computer's own settings once saved. Before that, what its plans saved for it, so saving
 * keeps a CLI path a plan already uses there instead of dropping it.
 */
export function draftFromSettings(loaded: ComputerSettings): ComputerSettingsDraft {
	if (loaded.saved) {
		return {
			startFolder: loaded.start_folder,
			cliPaths: { claude: loaded.cli_paths.claude ?? '', codex: loaded.cli_paths.codex ?? '' }
		};
	}
	return {
		startFolder: sharedStartFolder(loaded),
		cliPaths: {
			claude: loaded.from_plans.claude?.cli_path ?? '',
			codex: loaded.from_plans.codex?.cli_path ?? ''
		}
	};
}
