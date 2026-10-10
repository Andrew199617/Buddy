<script context="module" lang="ts">
	import { writable } from 'svelte/store';
	import type { SubscriptionProviderId } from '$lib/apis/subscriptions';

	const pendingSettings = writable<Record<string, number>>({});
	const settingsChanges = writable<{ providerId: SubscriptionProviderId } | null>(null);
</script>

<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getSubscription,
		logoutSubscription,
		updateSubscriptionConfig,
		type SubscriptionMachine,
		type SubscriptionProvider,
		type SubscriptionSettings,
		type SubscriptionUsageWindow
	} from '$lib/apis/subscriptions';

	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import SubscriptionLoginModal from './SubscriptionLoginModal.svelte';
	import SubscriptionSettingsModal from './SubscriptionSettingsModal.svelte';

	const i18n: any = getContext('i18n');

	export let provider: SubscriptionProvider;
	export let machines: SubscriptionMachine[] = [];
	// Called after anything that can add or remove models.
	export let onModelsChanged: () => Promise<void> = async () => {};
	// Reload the current registry and models after an accepted config write, including across remounts.
	export let onSettingsChanged: () => Promise<void> = onModelsChanged;

	const PLAN_NAMES: Record<string, string> = {
		free: 'Free',
		plus: 'Plus',
		pro: 'Pro',
		prolite: 'Pro Lite',
		max: 'Max',
		team: 'Team',
		business: 'Business',
		enterprise: 'Enterprise',
		edu: 'Edu'
	};

	let showLoginModal = false;
	let showSettingsModal = false;
	let busy = false;
	let checkTimer: ReturnType<typeof setTimeout> | null = null;
	let disposed = false;
	let refreshGeneration = 0;
	let checkInFlight = false;
	let settingsInFlight = false;
	let observedProvider = provider;
	let observedMachines = machines;
	let observedMachineId = provider.settings.machine_id;
	let observedMachineRevision = provider.machine?.revision;
	$: settingsBlocked = settingsInFlight || ($pendingSettings[provider.id] ?? 0) > 0;
	let enabled = provider.settings.enable;
	$: enabled = provider.settings.enable;

	const CHECK_AGAIN_MS = 3000;

	$: providerStatus = provider.status ?? {};
	$: accessLabel = getAccessLabel(provider.settings.access);
	$: cliName = provider.id === 'claude' ? 'Claude Code' : 'Codex';

	// Sign-in and permission drafts belong to the selected computer, not just the provider id.
	$: if (
		provider.settings.machine_id !== observedMachineId ||
		provider.machine?.revision !== observedMachineRevision
	) {
		observedMachineId = provider.settings.machine_id;
		observedMachineRevision = provider.machine?.revision;
		showLoginModal = false;
		showSettingsModal = false;
	}

	const getAccessLabel = (access: string) => {
		if (access === 'read') {
			return $i18n.t('Read files');
		}
		if (access === 'full') {
			return $i18n.t('Full access');
		}
		return $i18n.t('Chat only');
	};

	const getPlanName = (plan: string | null | undefined) => {
		if (!plan) {
			return '';
		}
		return PLAN_NAMES[plan.toLowerCase()] ?? plan;
	};

	const getStatusText = (status: SubscriptionProvider['status']) => {
		if (status.checking) {
			return $i18n.t('Checking {{cli}}…', { cli: cliName });
		}
		if (!status.installed) {
			return $i18n.t('{{cli}} not found', { cli: cliName });
		}
		if (!status.signed_in) {
			return $i18n.t('Not signed in');
		}
		const parts = [$i18n.t('Signed in')];
		if (status.account?.email) {
			parts.push(status.account.email);
		}
		const plan = getPlanName(status.account?.plan ?? status.usage?.plan);
		if (plan) {
			parts.push(plan);
		}
		return parts.join(' · ');
	};

	const getResetText = (window: SubscriptionUsageWindow) => {
		if (!window.resets_at) {
			return '';
		}
		const resetDate = new Date(window.resets_at * 1000);
		return $i18n.t('resets {{date}}', {
			date: resetDate.toLocaleString(undefined, {
				weekday: 'short',
				hour: 'numeric',
				minute: '2-digit'
			})
		});
	};

	const getUsagePercent = (window: SubscriptionUsageWindow): number | null => {
		if (typeof window.used_percent !== 'number' || !Number.isFinite(window.used_percent)) {
			return null;
		}
		return window.used_percent;
	};

	const clearCheckTimer = () => {
		if (checkTimer) {
			clearTimeout(checkTimer);
			checkTimer = null;
		}
	};

	const invalidateChecks = () => {
		clearCheckTimer();
		refreshGeneration += 1;
		checkInFlight = false;
	};

	// A registry reload or settings change replaces the context of an outstanding check.
	$: if (provider !== observedProvider || machines !== observedMachines) {
		observedProvider = provider;
		observedMachines = machines;
		invalidateChecks();
		scheduleCheck();
	}

	const refresh = async (fresh = true): Promise<boolean> => {
		if (disposed || settingsBlocked) {
			return false;
		}
		clearCheckTimer();
		const generation = ++refreshGeneration;
		checkInFlight = true;
		try {
			const nextProvider = await getSubscription(
				localStorage.token,
				provider.id,
				fresh,
				provider.settings.machine_id,
				provider.machine?.revision
			);
			if (disposed || generation !== refreshGeneration) {
				return false;
			}
			observedProvider = nextProvider;
			provider = nextProvider;
			return true;
		} catch (error) {
			if (disposed || generation !== refreshGeneration) {
				return false;
			}
			throw error;
		} finally {
			if (generation === refreshGeneration) {
				checkInFlight = false;
				if (!disposed && provider.status?.checking) {
					scheduleCheck();
				}
			}
		}
	};

	// The first check starts the CLI and can take a while; ask until it answers.
	const scheduleCheck = () => {
		if (disposed || settingsBlocked || checkTimer || checkInFlight || !provider.status?.checking) {
			return;
		}
		const generation = refreshGeneration;
		checkTimer = setTimeout(async () => {
			checkTimer = null;
			if (disposed || generation !== refreshGeneration) {
				return;
			}
			try {
				await refresh(false);
			} catch (error) {
				if (!disposed) {
					toast.error(`${error}`);
				}
			}
		}, CHECK_AGAIN_MS);
	};

	$: if (providerStatus.checking) {
		scheduleCheck();
	} else {
		clearCheckTimer();
	}

	onDestroy(() => {
		disposed = true;
		refreshGeneration += 1;
		clearCheckTimer();
	});

	onMount(() => {
		let initial = true;
		return settingsChanges.subscribe((change) => {
			if (initial) {
				initial = false;
				return;
			}
			if (!disposed && change?.providerId === provider.id) {
				const generation = refreshGeneration;
				Promise.resolve(onSettingsChanged()).catch((error) => {
					if (!disposed && generation === refreshGeneration) toast.error(`${error}`);
				});
			}
		});
	});

	$: if (!settingsBlocked && providerStatus.checking) scheduleCheck();

	const runAction = async (action: () => Promise<void>) => {
		if (disposed || busy) return;
		busy = true;
		try {
			await action();
		} catch (error) {
			if (!disposed) toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const saveSettings = async (
		settings: Partial<SubscriptionSettings>,
		isDialogCurrent: () => boolean = () => true
	) => {
		if (disposed || settingsBlocked) {
			return false;
		}
		invalidateChecks();
		const generation = refreshGeneration;
		const providerId = provider.id;
		const expectedMachineId = provider.settings.machine_id;
		const expectedMachineRevision = provider.machine?.revision;
		settingsInFlight = true;
		pendingSettings.update((pending) => ({
			...pending,
			[providerId]: (pending[providerId] ?? 0) + 1
		}));
		try {
			const nextProvider = await updateSubscriptionConfig(
				localStorage.token,
				providerId,
				settings,
				expectedMachineId,
				expectedMachineRevision
			);
			settingsChanges.set({ providerId });
			if (disposed || generation !== refreshGeneration) {
				return false;
			}
			observedProvider = nextProvider;
			provider = nextProvider;
			if (isDialogCurrent()) {
				toast.success($i18n.t('{{name}} subscription settings saved', { name: provider.name }));
			}
			return !disposed && generation === refreshGeneration;
		} catch (error) {
			if (!disposed && generation === refreshGeneration && isDialogCurrent()) {
				toast.error(`${error}`);
			}
			return false;
		} finally {
			settingsInFlight = false;
			pendingSettings.update((pending) => ({
				...pending,
				[providerId]: (pending[providerId] ?? 1) - 1
			}));
			scheduleCheck();
		}
	};

	const toggleEnabled = async () => {
		if (disposed || settingsBlocked || busy) {
			enabled = provider.settings.enable;
			return;
		}
		const enable = enabled;
		const optimisticProvider = provider;
		const machineId = provider.settings.machine_id;
		const machineRevision = provider.machine?.revision;
		const generation = refreshGeneration + 1;
		const saved = await saveSettings({ enable });
		if (!saved) {
			if (!disposed && provider === optimisticProvider && generation === refreshGeneration) {
				enabled = provider.settings.enable;
			}
			return;
		}
		if (
			!disposed &&
			generation === refreshGeneration &&
			provider.settings.machine_id === machineId &&
			provider.machine?.revision === machineRevision &&
			enable &&
			provider.settings.enable &&
			provider.status.installed &&
			!provider.status.signed_in
		) {
			showLoginModal = true;
		}
	};

	const signOut = () =>
		runAction(async () => {
			invalidateChecks();
			const generation = refreshGeneration;
			const providerId = provider.id;
			const expectedMachineId = provider.settings.machine_id;
			const expectedMachineRevision = provider.machine?.revision;
			try {
				const nextProvider = await logoutSubscription(
					localStorage.token,
					providerId,
					expectedMachineId,
					expectedMachineRevision
				);
				settingsChanges.set({ providerId });
				if (disposed || generation !== refreshGeneration) return;
				observedProvider = nextProvider;
				provider = nextProvider;
				toast.success($i18n.t('Signed out of {{name}}', { name: provider.name }));
			} catch (error) {
				if (!disposed && generation === refreshGeneration) throw error;
			}
		});

	const handleSignedIn = () =>
		runAction(async () => {
			if (await refresh(true)) {
				await onModelsChanged();
			}
		});
</script>

<SubscriptionLoginModal bind:show={showLoginModal} {provider} onSignedIn={handleSignedIn} />
<SubscriptionSettingsModal
	bind:show={showSettingsModal}
	{provider}
	{machines}
	onSave={saveSettings}
/>

<div class="flex w-full flex-col gap-1">
	<div class="flex w-full items-center gap-2">
		<div class="min-w-0 flex-1 {provider.settings.enable ? '' : 'opacity-60'}">
			<div class="flex flex-wrap items-baseline gap-x-1.5">
				<span class="whitespace-nowrap font-medium">{provider.name}</span>
				<span class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t('via {{cli}} on {{machine}}', {
						cli: cliName,
						machine: provider.machine?.name ?? ''
					})} · {accessLabel}
				</span>
			</div>
			<div class="break-words text-xs text-gray-500 dark:text-gray-400">
				{getStatusText(providerStatus)}
			</div>
		</div>

		<div class="flex shrink-0 items-center gap-1">
			{#if providerStatus.installed}
				{#if providerStatus.signed_in}
					<button
						class="rounded-lg px-2 py-1 text-xs text-gray-500 transition hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-850 dark:hover:text-gray-200 disabled:opacity-50"
						type="button"
						disabled={busy}
						on:click={signOut}
					>
						{$i18n.t('Sign out')}
					</button>
				{:else}
					<button
						class="rounded-lg px-2 py-1 text-xs font-medium transition hover:bg-gray-100 dark:hover:bg-gray-850 disabled:opacity-50"
						type="button"
						disabled={busy}
						on:click={() => {
							showLoginModal = true;
						}}
					>
						{$i18n.t('Sign in')}
					</button>
				{/if}
			{/if}

			<Tooltip content={$i18n.t('Configure')}>
				<button
					class="p-1 rounded-lg transition hover:bg-gray-100 dark:hover:bg-gray-850"
					type="button"
					aria-label={$i18n.t('Configure {{name}} subscription', { name: provider.name })}
					on:click={() => {
						showSettingsModal = true;
					}}
				>
					<Cog6 />
				</button>
			</Tooltip>

			<fieldset class="m-0 min-w-0 border-0 p-0" disabled={settingsBlocked || busy}>
				<Tooltip content={enabled ? $i18n.t('Enabled') : $i18n.t('Disabled')}>
					<Switch
						bind:state={enabled}
						ariaLabel={$i18n.t('Use {{name}} subscription models', { name: provider.name })}
						on:change={toggleEnabled}
					/>
				</Tooltip>
			</fieldset>
		</div>
	</div>

	{#if providerStatus.message}
		<p class="text-[0.6875rem] text-amber-700 dark:text-amber-400">{providerStatus.message}</p>
	{/if}

	{#if provider.settings.enable && providerStatus.signed_in}
		{#each providerStatus.usage?.windows ?? [] as window}
			{@const usedPercent = getUsagePercent(window)}
			<div class="flex flex-col gap-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				<div class="flex flex-wrap justify-between gap-x-2">
					<span>{$i18n.t('{{label}} limit', { label: window.label })}</span>
					<span>
						{#if usedPercent === null}
							{$i18n.t('Usage unavailable')}
						{:else}
							{$i18n.t('{{percent}}% used', { percent: usedPercent })}
						{/if}
						{#if window.resets_at}
							· {getResetText(window)}
						{/if}
					</span>
				</div>
				{#if usedPercent !== null}
					<div class="h-1 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-850">
						<div
							class="h-full rounded-full {usedPercent >= 90
								? 'bg-amber-500'
								: 'bg-gray-400 dark:bg-gray-500'}"
							style="width: {Math.min(100, Math.max(0, usedPercent))}%"
						></div>
					</div>
				{/if}
			</div>
		{/each}

		{#if providerStatus.usage?.limit_reached}
			<p class="text-[0.6875rem] text-amber-700 dark:text-amber-400">
				{$i18n.t('Usage limit reached. Replies resume when the limit resets.')}
			</p>
		{/if}

		{#if provider.models.length > 0}
			<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
				{provider.models.map((model) => model.name).join(', ')}
			</div>
		{/if}
	{/if}
</div>
