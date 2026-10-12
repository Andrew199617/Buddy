<script lang="ts">
	// One computer's own start folder, where chats that let the AI choose begin, and the CLI each
	// plan runs there. Paths are checked on that computer when they are saved.
	import { getContext, onMount } from 'svelte';

	import {
		getComputerSettings,
		saveComputerSettings,
		type ComputerSettings,
		type SubscriptionProviderId
	} from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: any = getContext('i18n');

	export let machineId: string;
	export let machineName: string;

	const PLAN_NAMES: Record<SubscriptionProviderId, string> = {
		claude: 'Claude Code',
		codex: 'Codex'
	};
	const PROVIDER_IDS: SubscriptionProviderId[] = ['claude', 'codex'];

	let settings: ComputerSettings | null = null;
	let startFolder = '';
	let cliPaths: Record<SubscriptionProviderId, string> = { claude: '', codex: '' };
	let loading = true;
	let saving = false;
	let problem = '';
	let saved = false;

	const inputClass =
		'min-h-11 w-full rounded-lg bg-gray-50 px-2 font-mono text-base outline-hidden dark:bg-gray-850 md:min-h-8 md:text-xs';

	function show(loaded: ComputerSettings) {
		settings = loaded;
		startFolder = loaded.start_folder;
		cliPaths = { claude: loaded.cli_paths.claude ?? '', codex: loaded.cli_paths.codex ?? '' };
	}

	onMount(async () => {
		try {
			show(await getComputerSettings(localStorage.token, machineId));
		} catch (error) {
			problem = `${error}`;
		} finally {
			loading = false;
		}
	});

	async function save() {
		if (!settings) return;
		saving = true;
		problem = '';
		saved = false;
		try {
			show(
				await saveComputerSettings(localStorage.token, machineId, {
					expected_revision: settings.revision,
					start_folder: startFolder,
					cli_paths: cliPaths
				})
			);
			saved = true;
		} catch (error) {
			problem = `${error}`;
		} finally {
			saving = false;
		}
	}

	$: fromPlans = settings?.from_plans ?? {};
</script>

<div class="ml-4 flex flex-col gap-2 rounded-lg border border-gray-100 p-2 dark:border-gray-850">
	{#if loading}
		<div class="flex items-center gap-2 text-xs text-gray-500" role="status">
			<Spinner className="size-3.5" />
			{$i18n.t('Loading…')}
		</div>
	{:else if settings}
		{#if !settings.saved && Object.keys(fromPlans).length > 0}
			<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('Chats on {{name}} still use what each plan saved for it, until you save here:', {
					name: machineName
				})}
			</p>
			<ul class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{#each PROVIDER_IDS as providerId}
					{#if fromPlans[providerId]}
						<li class="break-all">
							{PLAN_NAMES[providerId]}:
							<span class="font-mono" dir="ltr">{fromPlans[providerId]?.start_folder || '—'}</span>
						</li>
					{/if}
				{/each}
			</ul>
		{/if}
		<label class="flex flex-col gap-1 text-xs">
			<span>{$i18n.t('Start folder')}</span>
			<input
				class={inputClass}
				bind:value={startFolder}
				placeholder={$i18n.t('The computer’s default workspace')}
				dir="ltr"
			/>
			<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('Where chats that let the AI choose start on {{name}}.', { name: machineName })}
			</span>
		</label>
		{#each PROVIDER_IDS as providerId}
			<label class="flex flex-col gap-1 text-xs">
				<span>{$i18n.t('{{tool}} path', { tool: PLAN_NAMES[providerId] })}</span>
				<input
					class={inputClass}
					bind:value={cliPaths[providerId]}
					placeholder={$i18n.t('Found automatically')}
					dir="ltr"
				/>
			</label>
		{/each}
		{#if problem}
			<p class="text-xs text-red-600 dark:text-red-400" role="alert">{problem}</p>
		{/if}
		<div class="flex items-center gap-2">
			<button
				type="button"
				class="min-h-11 rounded-lg bg-gray-900 px-3 text-xs font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-8"
				disabled={saving}
				on:click={save}
			>
				{$i18n.t('Save')}
			</button>
			{#if saving}
				<Spinner className="size-3.5" />
			{:else if saved}
				<span class="text-xs text-gray-500" role="status">{$i18n.t('Saved')}</span>
			{/if}
		</div>
	{:else if problem}
		<p class="text-xs text-red-600 dark:text-red-400" role="alert">{problem}</p>
	{/if}
</div>
