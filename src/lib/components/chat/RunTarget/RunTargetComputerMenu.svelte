<script lang="ts">
	// Where a new chat runs: this PC, another of the person's computers, Buddy server, or Detached.
	import { getContext } from 'svelte';

	import type {
		RunTargetComputer,
		RunTargetOptions,
		RunTargetPlanState,
		SubscriptionProviderId
	} from '$lib/apis/subscriptions';
	import { getDesktop } from '$lib/desktop/bridge';
	import { showSettings } from '$lib/stores';
	import { computerPlace, providerNeedingAttention, type RunTargetDraft } from './run-target';

	const i18n = getContext('i18n');

	export let options: RunTargetOptions;
	export let draft: RunTargetDraft;
	export let providers: SubscriptionProviderId[] = [];
	export let thisPcMachineId: string | null = null;
	// The state each computer is shown with, including fresh checks the bar made.
	export let stateFor: (computer: RunTargetComputer) => RunTargetPlanState;
	export let onPick: (machineId: string | null) => void;
	export let onClose: () => void = () => {};

	const inDesktopApp = getDesktop() !== null;

	$: computers = options.computers ?? [];
	$: local = computers.filter((computer) => computerPlace(computer, thisPcMachineId) === 'local');
	$: remote = computers.filter((computer) => computerPlace(computer, thisPcMachineId) !== 'local');

	function computerName(computer: RunTargetComputer): string {
		if (computer.kind === 'server') return $i18n.t('Buddy server');
		return computer.name;
	}

	function planName(provider: SubscriptionProviderId | null): string {
		if (provider === 'codex') return 'ChatGPT';
		return 'Claude';
	}

	function toolName(provider: SubscriptionProviderId | null): string {
		if (provider === 'codex') return 'Codex';
		return 'Claude Code';
	}

	function statusText(computer: RunTargetComputer): string {
		const state = stateFor(computer);
		const provider = providerNeedingAttention(computer, providers);
		if (state === 'ready') return $i18n.t('Ready');
		if (state === 'checking') return $i18n.t('Checking…');
		if (state === 'offline') return $i18n.t('Offline · open Buddy on it');
		if (state === 'sign_in') {
			return $i18n.t('Sign in to {{plan}} here', { plan: planName(provider) });
		}
		if (state === 'not_installed') {
			return $i18n.t("{{tool}} isn't installed", { tool: toolName(provider) });
		}
		if (state === 'api_billing') return $i18n.t('Uses API billing');
		if (state === 'not_allowed') return $i18n.t('Not available');
		return $i18n.t('Not checked yet');
	}

	function connect() {
		onClose();
		showSettings.set('machines');
	}

	$: connectLabel =
		inDesktopApp && thisPcMachineId === null
			? $i18n.t('Connect this PC')
			: $i18n.t('Connect a computer…');
</script>

{#snippet computerRow(computer: RunTargetComputer, label: string)}
	{@const ready = stateFor(computer) === 'ready'}
	<button
		type="button"
		role="menuitemradio"
		aria-checked={draft.kind === 'computer' && draft.machineId === computer.machine_id}
		class="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-start hover:bg-gray-50 dark:hover:bg-gray-800/60 md:min-h-9 {draft.machineId ===
		computer.machine_id
			? 'bg-gray-50 dark:bg-gray-800/60'
			: ''}"
		on:click={() => onPick(computer.machine_id)}
	>
		<span
			class="size-1.5 shrink-0 rounded-full {ready ? 'bg-emerald-500' : 'bg-amber-500'}"
			aria-hidden="true"
		></span>
		<span class="flex min-w-0 flex-col">
			<span class="truncate text-[0.8125rem]"><bdi>{label}</bdi></span>
			<span class="truncate text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{statusText(computer)}
				{#if computer.kind === 'address'}
					· {$i18n.t('Added by address · type paths')}
				{/if}
			</span>
		</span>
	</button>
{/snippet}

<div class="flex flex-col gap-0.5 p-1" role="group" aria-label={$i18n.t('Run on')}>
	<div class="px-2 pt-1 text-[0.6875rem] font-medium text-gray-500 dark:text-gray-400">
		{$i18n.t('Run on')}
	</div>
	{#if local.length > 0}
		<div class="px-2 pt-1 text-[0.625rem] uppercase tracking-wider text-gray-400">
			{$i18n.t('Local')}
		</div>
		{#each local as computer (computer.machine_id)}
			{@render computerRow(computer, $i18n.t('This PC · {{name}}', { name: computer.name }))}
		{/each}
	{/if}
	{#if remote.length > 0}
		<div class="px-2 pt-1 text-[0.625rem] uppercase tracking-wider text-gray-400">
			{$i18n.t('Remote')}
		</div>
		{#each remote as computer (computer.machine_id)}
			{@render computerRow(computer, computerName(computer))}
		{/each}
	{/if}
	<div class="px-2 pt-1 text-[0.625rem] uppercase tracking-wider text-gray-400">
		{$i18n.t('No computer')}
	</div>
	<button
		type="button"
		role="menuitemradio"
		aria-checked={draft.kind === 'detached'}
		class="flex min-h-11 w-full flex-col items-start justify-center rounded-lg px-2 text-start hover:bg-gray-50 dark:hover:bg-gray-800/60 md:min-h-9 {draft.kind ===
		'detached'
			? 'bg-gray-50 dark:bg-gray-800/60'
			: ''}"
		on:click={() => onPick(null)}
	>
		<span class="text-[0.8125rem]">{$i18n.t('Detached')}</span>
		<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
			{$i18n.t('Chat only. No files or commands.')}
		</span>
	</button>
	<hr class="mx-1 my-0.5 border-gray-100 dark:border-gray-800" />
	<button
		type="button"
		class="min-h-9 rounded-lg px-2 text-start text-[0.8125rem] text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800/60"
		on:click={connect}
	>
		{connectLabel}
	</button>
</div>
