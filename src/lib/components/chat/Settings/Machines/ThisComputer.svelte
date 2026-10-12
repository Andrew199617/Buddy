<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import type { BuddyDesktop, DesktopPermission, DesktopStatus } from '$lib/desktop/bridge';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n: any = getContext('i18n');

	export let desktop: BuddyDesktop;
	export let status: DesktopStatus;
	export let onConnect: () => Promise<void>;
	export let onDisconnect: () => Promise<void>;

	let busy = false;
	let allowEdits = status.allowEdits;
	let allowCommands = status.allowCommands;
	let startAtLogin = status.startAtLogin;
	$: allowEdits = status.allowEdits;
	$: allowCommands = status.allowCommands;
	$: startAtLogin = status.startAtLogin;

	$: connected = ['connected', 'connecting', 'offline'].includes(status.state);

	const statusText = (current: DesktopStatus) => {
		if (current.state === 'connected') {
			return $i18n.t('Connected as {{name}}', { name: current.machineName ?? '' });
		}
		if (current.state === 'connecting') {
			return $i18n.t('Connecting…');
		}
		if (current.state === 'offline') {
			return $i18n.t('Offline. The Buddy app reconnects on its own.');
		}
		if (current.state === 'removed') {
			return $i18n.t('This computer was removed from Buddy. Connect it again to use it.');
		}
		if (current.state === 'refused') {
			return $i18n.t('Your account cannot connect computers here. Ask an administrator.');
		}
		if (current.state === 'replaced') {
			return $i18n.t('This computer connected again from another Buddy app session.');
		}
		if (current.state === 'needs_update') {
			return $i18n.t('Update the Buddy app to connect this computer.');
		}
		if (current.state === 'error') {
			return current.message || $i18n.t('Something went wrong with the connection.');
		}
		return $i18n.t('Not connected. Connect it to run Claude and ChatGPT here and to open its projects from any device.');
	};

	// Every bridge call returns the app's new status; a refusal in the app's own dialog is not an error.
	const runBridge = async (action: () => Promise<DesktopStatus | void>) => {
		if (busy) return;
		busy = true;
		try {
			const next = await action();
			if (next) {
				status = next;
			}
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const setPermission = (permission: DesktopPermission, allowed: boolean) =>
		runBridge(() => desktop.setPermission(permission, allowed));
</script>

<section aria-labelledby="this-computer-heading" class="rounded-2xl border border-gray-100 p-3 dark:border-gray-850">
	<h3 id="this-computer-heading" class="text-xs text-gray-600 dark:text-gray-400">
		{$i18n.t('This computer')}
	</h3>
	<p class="mt-1 text-sm" aria-live="polite">{statusText(status)}</p>

	<div class="mt-2 flex flex-wrap gap-2">
		{#if connected}
			<button
				type="button"
				class="min-h-11 rounded-xl px-4 text-sm font-medium text-red-600 transition hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40 md:min-h-9"
				disabled={busy}
				on:click={() => runBridge(onDisconnect)}
			>
				{$i18n.t('Disconnect this computer')}
			</button>
		{:else}
			<button
				type="button"
				class="min-h-11 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white transition disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-9"
				disabled={busy || status.state === 'refused'}
				on:click={() => runBridge(onConnect)}
			>
				{$i18n.t('Connect this computer')}
			</button>
		{/if}
	</div>

	<div class="mt-4">
		<h4 class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Shared folders')}</h4>
		<p class="mt-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
			{$i18n.t('Projects and file access can use only the folders you share here. Buddy cannot add any.')}
		</p>
		<ul class="mt-1 flex flex-col gap-1">
			{#each status.folders as folder (folder)}
				<li class="flex items-center gap-2">
					<span class="min-w-0 flex-1 truncate font-mono text-xs" title={folder}>{folder}</span>
					<button
						type="button"
						class="min-h-11 shrink-0 rounded-lg px-2 text-xs text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850 md:min-h-8"
						disabled={busy}
						on:click={() => runBridge(() => desktop.removeSharedFolder(folder))}
					>
						{$i18n.t('Stop sharing')}
					</button>
				</li>
			{/each}
		</ul>
		<button
			type="button"
			class="mt-1 min-h-11 rounded-lg px-2 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
			disabled={busy}
			on:click={() => runBridge(() => desktop.chooseSharedFolder())}
		>
			{$i18n.t('Share a folder…')}
		</button>
	</div>

	<div class="mt-4 flex flex-col gap-3">
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm">{$i18n.t('Allow editing files')}</div>
				<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Lets the Projects page save changes to files in shared folders.')}
				</div>
			</div>
			<Switch
				bind:state={allowEdits}
				ariaLabel={$i18n.t('Allow editing files')}
				on:change={() => setPermission('allowEdits', allowEdits)}
			/>
		</div>
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm">{$i18n.t('Allow running commands')}</div>
				<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Commands run as your account on this computer; their folder is not a sandbox.')}
				</div>
			</div>
			<Switch
				bind:state={allowCommands}
				ariaLabel={$i18n.t('Allow running commands')}
				on:change={() => setPermission('allowCommands', allowCommands)}
			/>
		</div>
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm">{$i18n.t('Start Buddy when you sign in')}</div>
				<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Keeps this computer connected, so you can use it from your phone.')}
				</div>
			</div>
			<Switch
				bind:state={startAtLogin}
				ariaLabel={$i18n.t('Start Buddy when you sign in')}
				on:change={() => runBridge(() => desktop.setStartAtLogin(startAtLogin))}
			/>
		</div>
	</div>
</section>
