<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getModels as fetchModels } from '$lib/apis';
	import {
		getSubscriptions,
		type SubscriptionMachine,
		type SubscriptionProvider
	} from '$lib/apis/subscriptions';
	import { config, models, settings } from '$lib/stores';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import RunnerMachinesOverview from './Connections/RunnerMachinesOverview.svelte';
	import SubscriptionMachineModal from './Connections/SubscriptionMachineModal.svelte';
	import SubscriptionPolicy from './Connections/SubscriptionPolicy.svelte';

	const i18n: any = getContext('i18n');

	// The older setup: Runners started by hand and added by address. Loading them
	// checks Claude Code and Codex, so the rest of the tab shows first.
	let providers: SubscriptionProvider[] = [];
	let machines: SubscriptionMachine[] = [];
	let machinesLoaded = false;
	let machinesError = '';
	let showMachineModal = false;
	let editedMachine: SubscriptionMachine | null = null;
	let disposed = false;
	let loadGeneration = 0;

	// Computers connected through the Buddy app are listed under everyone's computers.
	$: addressMachines = machines.filter((machine) => machine.kind !== 'runner');

	const loadMachines = async () => {
		const generation = ++loadGeneration;
		try {
			const result = await getSubscriptions(localStorage.token);
			if (disposed || generation !== loadGeneration) return;
			providers = result.providers;
			machines = result.machines;
			machinesError = '';
		} catch (error) {
			if (disposed || generation !== loadGeneration) return;
			machinesError = `${error}`;
		} finally {
			if (!disposed && generation === loadGeneration) machinesLoaded = true;
		}
	};

	// Removing or changing a machine turns off the providers on it, so the model list changes.
	const refreshModels = async () => {
		try {
			const directConnections = $config?.features?.enable_direct_connections
				? ($settings?.directConnections ?? null)
				: null;
			const nextModels = await fetchModels(localStorage.token, directConnections, false, true);
			if (!disposed) models.set(nextModels);
		} catch (error) {
			if (!disposed) toast.error(`${error}`);
		}
	};

	const openMachineModal = (machine: SubscriptionMachine | null) => {
		editedMachine = machine;
		showMachineModal = true;
	};

	const handleMachinesChanged = async (changed: SubscriptionMachine[]) => {
		if (disposed) return;
		machines = changed;
		await loadMachines();
		await refreshModels();
	};

	onMount(loadMachines);

	onDestroy(() => {
		disposed = true;
	});
</script>

<SubscriptionMachineModal
	bind:show={showMachineModal}
	machine={editedMachine}
	{providers}
	onChanged={handleMachinesChanged}
/>

<div class="flex h-full flex-col text-sm">
	<h2 class="mb-1 text-sm font-medium text-gray-900 dark:text-white">
		{$i18n.t('settings.admin.plans.title')}
	</h2>
	<p class="mb-4 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
		{$i18n.t('settings.admin.plans.description')}
	</p>

	<div
		class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5 pb-[max(env(safe-area-inset-bottom),0.75rem)]"
	>
		<AdminSettingSection first title={$i18n.t('Policy')}>
			<SubscriptionPolicy />
		</AdminSettingSection>

		<AdminSettingSection title={$i18n.t("Everyone's computers")}>
			<RunnerMachinesOverview />
		</AdminSettingSection>

		<AdminSettingSection title={$i18n.t('Your Runner addresses (older setup)')}>
			<div class="flex items-start justify-between gap-3">
				<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t(
						'Runners you started by hand and added by address. They keep working until you move to the Buddy app.'
					)}
				</p>
				<Tooltip content={$i18n.t('Add machine')}>
					<button
						class="flex size-8 shrink-0 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-black/5 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-gray-300 md:size-6"
						type="button"
						aria-label={$i18n.t('Add machine')}
						on:click={() => openMachineModal(null)}
					>
						<Plus />
					</button>
				</Tooltip>
			</div>
			{#if machinesError}
				<p role="alert" class="text-xs text-red-600 dark:text-red-400">{machinesError}</p>
			{:else if !machinesLoaded}
				<div class="flex items-center gap-2 text-xs text-gray-400">
					<Spinner className="size-3.5" />
					{$i18n.t('Checking Claude Code and Codex…')}
				</div>
			{:else}
				<div class="flex flex-col gap-1">
					{#each addressMachines as machine (machine.id)}
						<div class="flex items-center justify-between gap-2">
							<div class="min-w-0">
								<span class="text-sm">{machine.name}</span>
								<span class="ml-1.5 break-all text-xs text-gray-400 dark:text-gray-600">
									{machine.url ?? $i18n.t('Runs the CLIs inside the Buddy server')}
								</span>
							</div>
							{#if machine.url}
								<Tooltip content={$i18n.t('Configure')}>
									<button
										class="rounded-lg p-1 transition hover:bg-gray-100 dark:hover:bg-gray-850"
										type="button"
										aria-label={$i18n.t('Configure {{name}}', { name: machine.name })}
										on:click={() => openMachineModal(machine)}
									>
										<Cog6 />
									</button>
								</Tooltip>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</AdminSettingSection>
	</div>
</div>
