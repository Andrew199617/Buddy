<script lang="ts">
	import { getContext } from 'svelte';

	import type {
		SubscriptionComputer,
		SubscriptionMachine,
		SubscriptionProvider
	} from '$lib/apis/subscriptions';

	const i18n: any = getContext('i18n');

	export let machine: SubscriptionMachine;
	// Whether this row is the computer running the Buddy app this page is open in.
	export let isThisComputer = false;
	// Buddy server can't be renamed or removed.
	export let removable = true;
	export let onRename: (name: string) => Promise<void> = async () => {};
	export let onRemove: () => Promise<void> = async () => {};
	// Each plan that is on, with how it stands on this computer.
	export let plans: { provider: SubscriptionProvider; computer: SubscriptionComputer | null }[] =
		[];
	export let onSignIn: (provider: SubscriptionProvider) => void = () => {};
	export let onSignOut: (provider: SubscriptionProvider) => Promise<void> = async () => {};

	// Buddy server is always there; a computer added by address can't say whether it is.
	$: available = machine.id === 'local' || machine.online === true;
	let signingOut = '';

	type Plan = (typeof plans)[number];

	function toolName(provider: SubscriptionProvider): string {
		if (provider.id === 'codex') return 'Codex';
		return 'Claude Code';
	}

	/** A token or API key Buddy keeps works on every computer; only the computer's own sign-in is per computer. */
	function usesComputerSignIn(plan: Plan): boolean {
		const mode = plan.provider.credential?.mode ?? 'machine';
		return mode === 'machine';
	}

	function planStateText(plan: Plan): string {
		const mode = plan.provider.credential?.mode ?? 'machine';
		if (mode === 'plan_token') return $i18n.t('Uses your saved Claude token');
		if (mode === 'api_key') return $i18n.t('Uses your API key');
		const state = plan.computer?.state ?? 'not_checked';
		if (plan.computer && !plan.computer.online) return $i18n.t('Offline');
		if (state === 'ready') return $i18n.t('Signed in');
		if (state === 'api_billing') return $i18n.t('Signed in with API billing');
		if (state === 'not_installed') {
			return $i18n.t("{{tool}} isn't installed", { tool: toolName(plan.provider) });
		}
		if (state === 'signed_out') return $i18n.t('Not signed in');
		return $i18n.t('Not checked yet');
	}

	function canSignOut(plan: Plan): boolean {
		const state = plan.computer?.state;
		return state === 'ready' || state === 'api_billing';
	}

	function canSignIn(plan: Plan): boolean {
		if (!plan.computer?.online) return false;
		const state = plan.computer.state;
		return state === 'signed_out' || state === 'not_checked';
	}

	async function signOut(provider: SubscriptionProvider) {
		signingOut = provider.id;
		try {
			await onSignOut(provider);
		} finally {
			signingOut = '';
		}
	}

	let editing = false;
	let nameDraft = '';
	let busy = false;
	let confirmingRemoval = false;

	const PLATFORM_NAMES: Record<string, string> = {
		win32: 'Windows',
		darwin: 'macOS',
		linux: 'Linux'
	};

	$: platform = PLATFORM_NAMES[machine.host?.platform ?? ''] ?? machine.host?.platform ?? '';

	const lastSeenText = (seconds: number | undefined) => {
		if (!seconds) {
			return $i18n.t('Not connected yet');
		}
		const date = new Date(seconds * 1000);
		return $i18n.t('Last seen {{date}}', {
			date: date.toLocaleString(undefined, {
				month: 'short',
				day: 'numeric',
				hour: 'numeric',
				minute: '2-digit'
			})
		});
	};

	const startEditing = () => {
		nameDraft = machine.name;
		editing = true;
	};

	const saveName = async () => {
		const name = nameDraft.trim();
		if (!name || name === machine.name) {
			editing = false;
			return;
		}
		busy = true;
		try {
			await onRename(name);
			editing = false;
		} finally {
			busy = false;
		}
	};

	const remove = async () => {
		busy = true;
		try {
			await onRemove();
		} finally {
			busy = false;
			confirmingRemoval = false;
		}
	};
</script>

<li class="flex flex-col gap-1 rounded-xl px-1 py-2">
	<div class="flex items-center gap-2">
		<span
			class="size-2 shrink-0 rounded-full {available
				? 'bg-green-500'
				: 'bg-gray-300 dark:bg-gray-700'}"
			aria-hidden="true"
		></span>
		<div class="min-w-0 flex-1">
			{#if editing}
				<form class="flex gap-1.5" on:submit|preventDefault={saveName}>
					<label class="sr-only" for="machine-name-{machine.id}">{$i18n.t('Computer name')}</label>
					<input
						id="machine-name-{machine.id}"
						class="min-h-11 w-full rounded-lg bg-gray-50 px-2 text-base outline-hidden dark:bg-gray-850 md:min-h-8 md:text-sm"
						maxlength="80"
						bind:value={nameDraft}
						disabled={busy}
					/>
					<button
						type="submit"
						class="min-h-11 shrink-0 rounded-lg px-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
						disabled={busy}
					>
						{$i18n.t('Save')}
					</button>
				</form>
			{:else}
				<div class="truncate font-medium">
					{machine.name}
					{#if isThisComputer}
						<span class="text-xs font-normal text-gray-500 dark:text-gray-400">
							· {$i18n.t('This computer')}
						</span>
					{/if}
				</div>
				<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{#if machine.id === 'local'}
						{$i18n.t('Runs on Buddy itself')}
					{:else if machine.kind !== 'runner'}
						{$i18n.t('Added by address')}
					{:else if machine.online}
						{$i18n.t('Online')}
					{:else}
						{lastSeenText(machine.last_seen_at)}
					{/if}
					{#if platform}
						· {platform}
					{/if}
				</div>
			{/if}
		</div>
		{#if !editing && removable}
			<div class="flex shrink-0 items-center gap-1">
				<button
					type="button"
					class="min-h-11 rounded-lg px-2 text-xs text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850 md:min-h-8"
					on:click={startEditing}
				>
					{$i18n.t('Rename')}
				</button>
				<button
					type="button"
					class="min-h-11 rounded-lg px-2 text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40 md:min-h-8"
					on:click={() => (confirmingRemoval = true)}
				>
					{$i18n.t('Remove')}
				</button>
			</div>
		{/if}
	</div>
	{#if plans.length > 0}
		<ul
			class="ml-4 flex flex-col"
			aria-label={$i18n.t('Plans on {{name}}', { name: machine.name })}
		>
			{#each plans as plan (plan.provider.id)}
				<li class="flex min-h-11 items-center gap-2 text-xs md:min-h-8">
					<span class="w-[4.5rem] shrink-0 font-medium">{plan.provider.name}</span>
					<span
						class="min-w-0 flex-1 truncate text-gray-500 dark:text-gray-400"
						title={plan.computer?.message ?? undefined}
					>
						{planStateText(plan)}
					</span>
					{#if usesComputerSignIn(plan) && canSignOut(plan)}
						<button
							type="button"
							class="min-h-11 shrink-0 rounded-lg px-2 text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850 md:min-h-8"
							disabled={signingOut === plan.provider.id}
							on:click={() => signOut(plan.provider)}
						>
							{$i18n.t('Sign out')}
						</button>
					{:else if usesComputerSignIn(plan) && canSignIn(plan)}
						<button
							type="button"
							class="min-h-11 shrink-0 rounded-lg px-2 font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
							on:click={() => onSignIn(plan.provider)}
						>
							{$i18n.t('Sign in here')}
						</button>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
	{#if confirmingRemoval}
		<div
			class="ml-4 flex flex-wrap items-center gap-2 rounded-lg bg-red-50 p-2 text-xs dark:bg-red-950/30"
			role="alert"
		>
			<span class="min-w-0 flex-1">
				{$i18n.t(
					'Remove {{name}}? Buddy stops running anything there, and the app on it disconnects.',
					{
						name: machine.name
					}
				)}
			</span>
			<button
				type="button"
				class="min-h-11 rounded-lg px-3 font-medium text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-900/40 md:min-h-8"
				disabled={busy}
				on:click={remove}
			>
				{$i18n.t('Remove')}
			</button>
			<button
				type="button"
				class="min-h-11 rounded-lg px-3 hover:bg-white/60 dark:hover:bg-black/20 md:min-h-8"
				disabled={busy}
				on:click={() => (confirmingRemoval = false)}
			>
				{$i18n.t('Cancel')}
			</button>
		</div>
	{/if}
</li>
