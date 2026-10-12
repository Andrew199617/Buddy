<script lang="ts">
	// Browses the folders shared in the Buddy app on one computer, through Buddy,
	// to pick a project folder. Lists folders only and changes nothing.
	import { getContext, onDestroy, onMount } from 'svelte';

	import type { CompanionDirectoryEntry, CompanionGrant } from '$lib/apis/companion';
	import { RelayCompanionClient } from '$lib/apis/companion-relay';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { joinSharedPath, parentWithinRoot } from './project-folders';

	const i18n: any = getContext('i18n');

	export let machineId: string;
	export let machineName: string;
	export let platform: string | null;
	export let onPick: (path: string) => void;

	const client = new RelayCompanionClient(machineId, localStorage.token);

	let grants: CompanionGrant[] = [];
	let grant: CompanionGrant | null = null;
	let relative = '';
	let folders: CompanionDirectoryEntry[] = [];
	let busy = false;
	let loaded = false;
	let error = '';
	let disposed = false;

	$: currentPath = grant ? joinSharedPath(grant.path, relative, platform) : '';
	$: crumbs = relative.split('/').filter(Boolean);

	function describeError(cause: unknown): string {
		if (cause instanceof Error) return cause.message;
		return String(cause);
	}

	// One place for the busy state and errors of every listing.
	async function load(task: () => Promise<void>) {
		busy = true;
		error = '';
		try {
			await task();
		} catch (cause) {
			if (!disposed) error = describeError(cause);
		} finally {
			if (!disposed) busy = false;
		}
	}

	function loadSharedFolders() {
		return load(async () => {
			const all = await client.getGrants();
			if (disposed) return;
			grants = all.filter(
				(candidate) => candidate.available !== false && candidate.capabilities.read
			);
			loaded = true;
		});
	}

	function openFolder(nextGrant: CompanionGrant, nextRelative: string) {
		return load(async () => {
			const listing = await client.getDirectory(nextGrant.id, nextRelative);
			if (disposed) return;
			grant = nextGrant;
			relative = listing.path;
			folders = listing.entries.filter((entry) => entry.type === 'directory');
		});
	}

	function goUp() {
		if (!grant) return;
		if (!relative) {
			grant = null;
			folders = [];
			return;
		}
		openFolder(grant, parentWithinRoot(relative));
	}

	onMount(loadSharedFolders);

	onDestroy(() => {
		disposed = true;
		void client.disconnect().catch(() => {});
	});
</script>

<div class="flex flex-col gap-2">
	{#if error}
		<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
	{/if}

	{#if !loaded && busy}
		<div class="flex items-center gap-2 text-xs text-gray-500">
			<Spinner className="size-3.5" />
			{$i18n.t('Looking at the shared folders on {{name}}…', { name: machineName })}
		</div>
	{:else if loaded && grants.length === 0}
		<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t(
				'No shared folders on {{name}}. Share one in the Buddy app on that computer, or type a path below.',
				{ name: machineName }
			)}
		</p>
	{:else if loaded && !grant}
		<ul class="flex flex-col">
			{#each grants as shared (shared.id)}
				<li>
					<button
						type="button"
						class="flex min-h-11 w-full flex-col justify-center rounded-lg px-2 text-start hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-9"
						disabled={busy}
						on:click={() => openFolder(shared, '')}
					>
						<span class="text-sm">{shared.name}</span>
						<span class="break-all font-mono text-[0.6875rem] text-gray-500" dir="ltr">
							{shared.path}
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{:else if grant}
		<div
			class="overflow-x-auto whitespace-nowrap text-xs text-gray-500"
			aria-label={$i18n.t('Folder')}
		>
			<span class="font-medium text-gray-700 dark:text-gray-300">{grant.name}</span>
			{#each crumbs as crumb}
				<span aria-hidden="true"> / </span><span>{crumb}</span>
			{/each}
		</div>
		<ul class="flex max-h-[50vh] flex-col overflow-y-auto overscroll-contain" aria-busy={busy}>
			<li>
				<button
					type="button"
					class="flex min-h-11 w-full items-center rounded-lg px-2 text-start text-sm text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-850 md:min-h-9"
					disabled={busy}
					on:click={goUp}
				>
					← {$i18n.t('Up')}
				</button>
			</li>
			{#each folders as folder (folder.path)}
				<li>
					<button
						type="button"
						class="flex min-h-11 w-full items-center rounded-lg px-2 text-start text-sm hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-9"
						disabled={busy}
						on:click={() => grant && openFolder(grant, folder.path)}
					>
						{folder.name}
					</button>
				</li>
			{:else}
				<li class="px-2 py-2 text-xs text-gray-500">{$i18n.t('No folders inside.')}</li>
			{/each}
		</ul>
		<div
			class="sticky bottom-0 flex flex-wrap items-center gap-2 bg-white pt-2 pb-[max(env(safe-area-inset-bottom),0.25rem)] dark:bg-gray-900"
		>
			<span class="min-w-0 flex-1 break-all font-mono text-[0.6875rem] text-gray-500" dir="ltr">
				{currentPath}
			</span>
			<button
				type="button"
				class="min-h-11 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-9"
				disabled={busy}
				on:click={() => onPick(currentPath)}
			>
				{$i18n.t('Use this folder')}
			</button>
		</div>
	{/if}
</div>
