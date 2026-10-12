<script lang="ts">
	// Browses one computer's folders to pick a project folder: its whole disk
	// through Buddy, or only the folders shared in the Buddy app, depending on
	// the source. Lists folders only and changes nothing.
	import { getContext, onDestroy, onMount } from 'svelte';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import type { BrowsePlace, BrowseView, FolderSource } from './folder-sources';

	const i18n: any = getContext('i18n');

	export let source: FolderSource;
	export let machineName: string;
	export let onPick: (path: string) => void;

	let view: BrowseView | null = null;
	let busy = false;
	let slow = false;
	let error = '';
	let showHidden = false;
	let narrowing = '';
	let disposed = false;
	let request = 0;

	$: currentName = view?.crumbs[view.crumbs.length - 1]?.name ?? '';

	function describeError(cause: unknown): string {
		if (cause instanceof Error) return cause.message;
		return String(cause);
	}

	// One place for the busy state, the slow-drive note and errors of every listing.
	async function load(task: () => Promise<BrowseView>) {
		const current = ++request;
		busy = true;
		slow = false;
		error = '';
		const slowTimer = setTimeout(() => {
			if (!disposed && current === request) slow = true;
		}, 3000);
		try {
			const next = await task();
			if (disposed || current !== request) return;
			view = next;
		} catch (cause) {
			if (!disposed && current === request) error = describeError(cause);
		} finally {
			clearTimeout(slowTimer);
			if (!disposed && current === request) {
				busy = false;
				slow = false;
			}
		}
	}

	function showPlaces() {
		narrowing = '';
		return load(() => source.start());
	}

	function openFolder(path: string) {
		narrowing = '';
		return load(() => source.open(path, { hidden: showHidden, prefix: '' }));
	}

	function narrow() {
		if (!view?.path) return;
		const path = view.path;
		return load(() => source.open(path, { hidden: showHidden, prefix: narrowing.trim() }));
	}

	function goUp() {
		if (!view) return;
		if (view.parent === null) {
			void showPlaces();
			return;
		}
		void openFolder(view.parent);
	}

	function toggleHidden() {
		showHidden = !showHidden;
		if (view?.path) void openFolder(view.path);
	}

	function placeDetail(place: BrowsePlace): string {
		if (place.kind === 'remote_drive') return $i18n.t('Network drive');
		if (place.kind === 'removable') return $i18n.t('Removable drive');
		if (place.kind === 'shared') return place.path;
		return '';
	}

	onMount(showPlaces);

	onDestroy(() => {
		disposed = true;
		source.close();
	});
</script>

<div class="flex flex-col gap-2">
	{#if error}
		<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
	{/if}

	{#if !view && busy}
		<div class="flex items-center gap-2 text-xs text-gray-500" role="status">
			<Spinner className="size-3.5" />
			{$i18n.t('Looking at the folders on {{name}}…', { name: machineName })}
		</div>
	{:else if view && view.path === ''}
		{#if view.places.length === 0}
			<p class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('No folders to show on {{name}}. Type a path below.', { name: machineName })}
			</p>
		{:else}
			<ul class="flex flex-col">
				{#each view.places as place (place.path)}
					<li>
						<button
							type="button"
							class="flex min-h-11 w-full flex-col justify-center rounded-lg px-2 text-start hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-9"
							disabled={busy}
							on:click={() => openFolder(place.path)}
						>
							<span class="text-sm"><bdi>{place.name}</bdi></span>
							{#if placeDetail(place)}
								<span class="break-all font-mono text-[0.6875rem] text-gray-500" dir="ltr">
									{placeDetail(place)}
								</span>
							{/if}
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	{:else if view}
		<div class="flex items-center gap-1">
			<button
				type="button"
				class="min-h-11 shrink-0 rounded-lg px-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 dark:text-gray-300 dark:hover:bg-gray-850 md:min-h-8"
				aria-label={$i18n.t('Up one folder')}
				disabled={busy}
				on:click={goUp}
			>
				↑
			</button>
			<nav
				class="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-xs text-gray-500 scrollbar-hidden"
				aria-label={$i18n.t('Folder')}
				dir="ltr"
			>
				{#each view.crumbs as crumb, index (crumb.path)}
					{#if index > 0}<span aria-hidden="true"> / </span>{/if}
					<button
						type="button"
						class="min-h-11 rounded px-1 md:min-h-8"
						disabled={busy}
						on:click={() => openFolder(crumb.path)}
					>
						<bdi>{crumb.name}</bdi>
					</button>
				{/each}
			</nav>
			{#if busy}<Spinner className="size-3.5 shrink-0" />{/if}
		</div>

		{#if source.showsHidden}
			<label
				class="flex min-h-11 items-center gap-2 text-xs text-gray-600 dark:text-gray-400 md:min-h-0"
			>
				<input type="checkbox" checked={showHidden} on:change={toggleHidden} disabled={busy} />
				{$i18n.t('Show hidden folders')}
			</label>
		{/if}

		{#if slow}
			<p class="text-xs text-amber-700 dark:text-amber-400" role="status">
				{$i18n.t(
					'{{name}} is taking a while to list this folder. It may be a sleeping or network drive.',
					{
						name: machineName
					}
				)}
			</p>
		{/if}

		{#if view.truncated || narrowing}
			<form class="flex gap-2" on:submit|preventDefault={narrow}>
				<input
					class="min-h-11 min-w-0 flex-1 rounded-xl bg-gray-50 px-3 text-base outline-hidden dark:bg-gray-850 md:min-h-9 md:text-sm"
					bind:value={narrowing}
					placeholder={$i18n.t('Folders starting with…')}
					aria-label={$i18n.t('Folders starting with')}
					autocapitalize="off"
					autocorrect="off"
					spellcheck="false"
					autocomplete="off"
					enterkeyhint="search"
				/>
				<button
					type="submit"
					class="min-h-11 shrink-0 rounded-xl px-3 text-sm hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-9"
					disabled={busy}
				>
					{$i18n.t('Narrow')}
				</button>
			</form>
			{#if view.truncated}
				<p class="text-[0.6875rem] text-gray-500">
					{$i18n.t('Showing the first folders only. Type the start of a name to narrow.')}
				</p>
			{/if}
		{/if}

		<ul class="flex max-h-[50vh] flex-col overflow-y-auto overscroll-contain" aria-busy={busy}>
			{#each view.entries as folder (folder.path)}
				<li>
					<button
						type="button"
						class="flex min-h-11 w-full items-center gap-1 rounded-lg px-2 text-start text-sm hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-8 {folder.hidden
							? 'text-gray-500'
							: ''}"
						disabled={busy}
						on:click={() => openFolder(folder.path)}
					>
						<bdi class="min-w-0 truncate">{folder.name}</bdi>
						{#if folder.link}
							<span class="shrink-0 text-[0.6875rem] text-gray-500">{$i18n.t('link')}</span>
						{/if}
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
				{#if view.selectable}
					{view.path}
				{:else}
					{$i18n.t('Choose a folder, not a whole drive.')}
				{/if}
			</span>
			<button
				type="button"
				class="min-h-11 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-9"
				disabled={busy || !view.selectable}
				on:click={() => view && onPick(view.path)}
			>
				{$i18n.t('Use “{{name}}”', { name: currentName })}
			</button>
		</div>
	{/if}
</div>
