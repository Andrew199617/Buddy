<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getSubscription,
		logoutSubscription,
		updateSubscriptionConfig,
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
	// Called after anything that can add or remove models.
	export let onModelsChanged: () => Promise<void> = async () => {};

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

	$: providerStatus = provider.status ?? {};
	$: accessLabel = getAccessLabel(provider.settings.access);
	$: cliName = provider.id === 'claude' ? 'Claude Code' : 'Codex';

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

	const refresh = async (fresh = true) => {
		provider = await getSubscription(localStorage.token, provider.id, fresh);
	};

	const runAction = async (action: () => Promise<void>) => {
		busy = true;
		try {
			await action();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const saveSettings = async (settings: Partial<SubscriptionSettings>) => {
		try {
			provider = await updateSubscriptionConfig(localStorage.token, provider.id, settings);
		} catch (error) {
			toast.error(`${error}`);
			return false;
		}
		toast.success($i18n.t('{{name}} subscription settings saved', { name: provider.name }));
		await onModelsChanged();
		return true;
	};

	const toggleEnabled = async () => {
		const enable = provider.settings.enable;
		const saved = await saveSettings({ enable });
		if (!saved) {
			provider.settings.enable = !enable;
			return;
		}
		if (enable && provider.status.installed && !provider.status.signed_in) {
			showLoginModal = true;
		}
	};

	const signOut = () =>
		runAction(async () => {
			provider = await logoutSubscription(localStorage.token, provider.id);
			toast.success($i18n.t('Signed out of {{name}}', { name: provider.name }));
			await onModelsChanged();
		});

	const handleSignedIn = () =>
		runAction(async () => {
			await refresh(true);
			await onModelsChanged();
		});
</script>

<SubscriptionLoginModal bind:show={showLoginModal} {provider} onSignedIn={handleSignedIn} />
<SubscriptionSettingsModal bind:show={showSettingsModal} {provider} onSave={saveSettings} />

<div class="flex w-full flex-col gap-1">
	<div class="flex w-full items-center gap-2">
		<div class="min-w-0 flex-1 {provider.settings.enable ? '' : 'opacity-60'}">
			<div class="flex flex-wrap items-baseline gap-x-1.5">
				<span class="whitespace-nowrap font-medium">{provider.name}</span>
				<span class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t('via {{cli}}', { cli: cliName })} · {accessLabel}
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

			<Tooltip content={provider.settings.enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}>
				<Switch
					bind:state={provider.settings.enable}
					ariaLabel={$i18n.t('Use {{name}} subscription models', { name: provider.name })}
					on:change={toggleEnabled}
				/>
			</Tooltip>
		</div>
	</div>

	{#if providerStatus.message}
		<p class="text-[0.6875rem] text-amber-700 dark:text-amber-400">{providerStatus.message}</p>
	{/if}

	{#if provider.settings.enable && providerStatus.signed_in}
		{#each providerStatus.usage?.windows ?? [] as window}
			<div class="flex flex-col gap-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				<div class="flex flex-wrap justify-between gap-x-2">
					<span>{$i18n.t('{{label}} limit', { label: window.label })}</span>
					<span>
						{$i18n.t('{{percent}}% used', { percent: window.used_percent ?? 0 })}
						{#if window.resets_at}
							· {getResetText(window)}
						{/if}
					</span>
				</div>
				<div class="h-1 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-850">
					<div
						class="h-full rounded-full {(window.used_percent ?? 0) >= 90
							? 'bg-amber-500'
							: 'bg-gray-400 dark:bg-gray-500'}"
						style="width: {Math.min(100, Math.max(0, window.used_percent ?? 0))}%"
					></div>
				</div>
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
