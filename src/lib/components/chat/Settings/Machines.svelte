<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteSubscriptionMachine,
		getSubscriptions,
		renameSubscriptionMachine,
		type SubscriptionMachine,
		type SubscriptionProvider,
		type SubscriptionPublicPolicy
	} from '$lib/apis/subscriptions';
	import { createRunnerEnrollment } from '$lib/apis/runners';
	import { getDesktop, type DesktopStatus } from '$lib/desktop/bridge';
	import { refreshModelList } from '$lib/utils/model-list';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import SubscriptionConnection from '$lib/components/admin/Settings/Connections/SubscriptionConnection.svelte';
	import MachineRow from './Machines/MachineRow.svelte';
	import ProviderSignIn from './Machines/ProviderSignIn.svelte';
	import ThisComputer from './Machines/ThisComputer.svelte';

	const i18n: any = getContext('i18n');

	const desktop = getDesktop();

	let loaded = false;
	let allowed = true;
	let providers: SubscriptionProvider[] = [];
	let machines: SubscriptionMachine[] = [];
	let policy: SubscriptionPublicPolicy = {
		plan_sign_in: true,
		api_keys: true,
		desktop_download_url: ''
	};
	let desktopStatus: DesktopStatus | null = null;
	let stopWatchingDesktop: (() => void) | null = null;
	let disposed = false;

	// Computers connected through the Buddy app; "This server" only appears in the provider settings.
	$: computers = machines.filter((machine) => machine.kind === 'runner');

	const load = async () => {
		try {
			const result = await getSubscriptions(localStorage.token);
			if (disposed) return;
			allowed = result.allowed;
			providers = result.providers;
			machines = result.machines;
			policy = result.policy;
		} catch (error) {
			if (!disposed) toast.error(`${error}`);
		} finally {
			loaded = true;
		}
	};

	// Turning a plan on, signing in or changing its computer changes the model picker too.
	const reloadAfterChange = async () => {
		await load();
		try {
			await refreshModelList();
		} catch (error) {
			if (!disposed) toast.error(`${error}`);
		}
	};

	const watchDesktop = async () => {
		if (!desktop) return;
		try {
			desktopStatus = await desktop.getStatus();
		} catch (error) {
			toast.error(`${error}`);
		}
		stopWatchingDesktop = desktop.onStatusChanged((status) => {
			const stateChanged = status.state !== desktopStatus?.state;
			desktopStatus = status;
			// Online and offline show in the computer list too.
			if (stateChanged) load();
		});
	};

	onMount(() => {
		load();
		watchDesktop();
	});

	onDestroy(() => {
		disposed = true;
		stopWatchingDesktop?.();
	});

	const connectThisComputer = async () => {
		if (!desktop || !desktopStatus) return;
		// Buddy issues a single-use token; the app trades it for this computer's credential itself.
		const enrollment = await createRunnerEnrollment(localStorage.token);
		desktopStatus = await desktop.connectThisPC({
			enrollmentToken: enrollment.token,
			name: desktopStatus.machineName ?? ''
		});
		await load();
	};

	const disconnectThisComputer = async () => {
		if (!desktop) return;
		const machineId = desktopStatus?.machineId;
		desktopStatus = await desktop.disconnectThisPC();
		if (machineId) {
			await deleteSubscriptionMachine(localStorage.token, machineId);
		}
		await load();
	};

	const renameMachine = async (machine: SubscriptionMachine, name: string) => {
		try {
			const result = await renameSubscriptionMachine(localStorage.token, machine.id, name);
			machines = result.machines;
		} catch (error) {
			toast.error(`${error}`);
			throw error;
		}
	};

	const removeMachine = async (machine: SubscriptionMachine) => {
		try {
			const result = await deleteSubscriptionMachine(localStorage.token, machine.id);
			machines = result.machines;
			toast.success($i18n.t('Removed {{name}}', { name: machine.name }));
			await load();
		} catch (error) {
			toast.error(`${error}`);
		}
	};
</script>

<div id="tab-machines" class="flex h-full flex-col text-sm">
	<h2 class="mb-1 text-sm font-medium text-gray-900 dark:text-white">
		{$i18n.t('settings.personal.machines.title')}
	</h2>
	<p class="mb-4 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
		{$i18n.t('settings.personal.machines.description')}
	</p>

	<div
		class="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto pr-1.5 pb-[max(env(safe-area-inset-bottom),0.75rem)] scrollbar-hover"
	>
		{#if !loaded}
			<div class="flex justify-center py-8"><Spinner className="size-6" /></div>
		{:else if !allowed}
			<p class="text-sm text-gray-600 dark:text-gray-300">
				{$i18n.t('Your account cannot use AI plans on computers here. Ask an administrator.')}
			</p>
		{:else}
			{#if desktop && desktopStatus}
				<ThisComputer
					{desktop}
					status={desktopStatus}
					onConnect={connectThisComputer}
					onDisconnect={disconnectThisComputer}
				/>
			{:else}
				<section
					aria-labelledby="connect-computer-heading"
					class="rounded-2xl border border-gray-100 p-3 dark:border-gray-850"
				>
					<h3 id="connect-computer-heading" class="text-xs text-gray-600 dark:text-gray-400">
						{$i18n.t('settings.personal.machines.connect.title')}
					</h3>
					<p class="mt-1 text-[0.6875rem] leading-relaxed text-gray-500 dark:text-gray-400">
						{$i18n.t('settings.personal.machines.connect.description')}
					</p>
					{#if policy.desktop_download_url}
						<div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
							<a
								class="inline-flex min-h-11 items-center rounded-xl bg-gray-900 px-4 text-sm font-medium text-white dark:bg-white dark:text-gray-900 md:min-h-9"
								href={policy.desktop_download_url}
								target="_blank"
								rel="noopener noreferrer"
							>
								{$i18n.t('Get Buddy for Windows')}
							</a>
							<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
								{$i18n.t('Mac: coming soon')}
							</span>
						</div>
					{/if}
				</section>
			{/if}

			<section aria-labelledby="computers-heading">
				<h3 id="computers-heading" class="text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('settings.personal.machines.computers.title')}
				</h3>
				{#if computers.length === 0}
					<p class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{$i18n.t('No computers connected yet.')}
					</p>
				{:else}
					<ul class="mt-1 flex flex-col">
						{#each computers as machine (machine.id)}
							<MachineRow
								{machine}
								isThisComputer={machine.id === desktopStatus?.machineId}
								onRename={(name) => renameMachine(machine, name)}
								onRemove={() => removeMachine(machine)}
							/>
						{/each}
					</ul>
				{/if}
			</section>

			<section aria-labelledby="plans-heading">
				<h3 id="plans-heading" class="mb-2 text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('settings.personal.machines.plans.title')}
				</h3>
				<div class="flex flex-col gap-5">
					{#each providers as provider (provider.id)}
						<div class="flex flex-col">
							<SubscriptionConnection
								{provider}
								{machines}
								onModelsChanged={reloadAfterChange}
								onSettingsChanged={reloadAfterChange}
							/>
							<ProviderSignIn {provider} {policy} onChanged={reloadAfterChange} />
						</div>
					{/each}
				</div>
			</section>
		{/if}
	</div>
</div>
