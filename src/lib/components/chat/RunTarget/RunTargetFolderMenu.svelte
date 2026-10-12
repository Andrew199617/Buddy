<script lang="ts">
	// Which folder a new chat works in: the chat folder's, a recent one, one Claude Code or Codex
	// worked in, a typed path, a browsed one, or Let the AI choose. Every pick is checked on its
	// computer, except while that computer is offline.
	import { getContext, onMount } from 'svelte';

	import {
		checkMachineFolder,
		getSuggestedFolders,
		type FolderSuggestion,
		type RunTargetComputer,
		type RunTargetFolder,
		type RunTargetOptions
	} from '$lib/apis/subscriptions';
	import { getDesktop } from '$lib/desktop/bridge';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import FolderBrowser from '$lib/components/projectFolders/FolderBrowser.svelte';
	import {
		computerFolderSource,
		sharedFolderSource
	} from '$lib/components/projectFolders/folder-sources';
	import {
		lastSegment,
		lastUsedAgo,
		pathPlaceholder,
		suggestionTools,
		type LastUsed
	} from '$lib/components/projectFolders/project-folders';
	import { buildFolderSections, looksLikePath } from './folder-menu';
	import { computerPlace, type RunTargetDraft } from './run-target';

	const i18n: any = getContext('i18n');
	const desktop = getDesktop();

	export let options: RunTargetOptions;
	export let draft: RunTargetDraft;
	export let computer: RunTargetComputer;
	export let thisPcMachineId: string | null = null;
	// Suggestions loaded before the menu opened, so they show at once.
	export let prefetched: FolderSuggestion[] | null = null;
	export let autofocus = true;
	export let onPick: (folder: RunTargetFolder, machineId: string) => void;

	let query = '';
	let suggestions: FolderSuggestion[] = [];
	let suggestionsLoaded = false;
	let checking = false;
	let problem = '';
	let browsing = false;
	let searchInput: HTMLInputElement;

	$: offline = computer.kind === 'app' && computer.online === false;
	$: isThisPc = computerPlace(computer, thisPcMachineId) === 'local';
	$: canUseNativeDialog = isThisPc && Boolean(desktop?.chooseProjectFolder);
	$: browseSource = folderSource(computer);
	$: sections = buildFolderSections({
		options,
		suggested: suggestions,
		machineId: computer.machine_id,
		query,
		platform: computer.platform
	});
	$: typedPath = looksLikePath(query, computer.platform) ? query.trim() : '';
	// The bar's suggestions, also when they arrive after the menu opened.
	$: if (prefetched) takeSuggestions(prefetched);

	function takeSuggestions(found: FolderSuggestion[]) {
		suggestions = found;
		suggestionsLoaded = true;
	}

	function folderSource(target: RunTargetComputer) {
		if (target.folders === 'on') return computerFolderSource(target.machine_id, target.platform);
		if (target.can_browse) return sharedFolderSource(target.machine_id, target.platform);
		return null;
	}

	function computerName(target: { machine_id: string; name: string; kind: string }): string {
		if (target.kind === 'server') return $i18n.t('Buddy server');
		return target.name;
	}

	function nameOf(machineId: string): string {
		const target = options.computers?.find((candidate) => candidate.machine_id === machineId);
		if (!target) return machineId;
		return computerName(target);
	}

	onMount(async () => {
		if (autofocus) searchInput?.focus();
		if (suggestionsLoaded || computer.folders !== 'on') {
			suggestionsLoaded = true;
			return;
		}
		try {
			const found = await getSuggestedFolders(localStorage.token, computer.machine_id);
			if (found.state === 'ok') takeSuggestions(found.folders);
		} catch {
			// Recent folders, typing and browsing still work.
		} finally {
			suggestionsLoaded = true;
		}
	});

	/** Picks a folder on this computer once it has checked the folder is there. */
	async function pickPath(path: string) {
		problem = '';
		if (offline) {
			// Checked on the computer when the chat runs there.
			onPick({ mode: 'path', path }, computer.machine_id);
			return;
		}
		checking = true;
		try {
			const found = await checkMachineFolder(localStorage.token, computer.machine_id, path);
			onPick({ mode: 'path', path: found.path }, computer.machine_id);
		} catch (error) {
			problem = `${error}`;
		} finally {
			checking = false;
		}
	}

	function pickElsewhere(machineId: string, path: string) {
		// Recent folders on other computers were checked when they were chosen.
		onPick({ mode: 'path', path }, machineId);
	}

	async function chooseInAppDialog() {
		if (!desktop?.chooseProjectFolder) return;
		let startAt: string | undefined;
		if (draft.folder?.mode === 'path') startAt = draft.folder.path;
		const chosen = await desktop.chooseProjectFolder({ defaultPath: startAt });
		if (chosen) await pickPath(chosen);
	}

	function pickOnEnter(event: KeyboardEvent) {
		if (event.key !== 'Enter' || event.isComposing) return;
		event.preventDefault();
		if (typedPath && !checking) void pickPath(typedPath);
	}

	function whenLabel(ago: LastUsed): string {
		if (ago.unit === 'today') return $i18n.t('today');
		if (ago.unit === 'yesterday') return $i18n.t('yesterday');
		if (ago.unit === 'days') return $i18n.t('{{count}} days ago', { count: ago.count });
		return $i18n.t('{{count}} months ago', { count: ago.count });
	}

	function suggestionDetail(suggestion: FolderSuggestion): string {
		const tools = suggestionTools(suggestion.sources);
		let toolsLabel = 'Claude Code';
		if (tools === 'codex') toolsLabel = 'Codex';
		if (tools === 'both') toolsLabel = $i18n.t('Both');
		return `${toolsLabel} · ${whenLabel(lastUsedAgo(suggestion.last_used, Date.now() / 1000))}`;
	}

	$: noFolders =
		sections.inChatFolder.length === 0 &&
		sections.recentHere.length === 0 &&
		sections.recentElsewhere.length === 0 &&
		sections.fromAgents.length === 0;
</script>

{#snippet folderRow(path: string, detail: string, onClick: () => void)}
	<button
		type="button"
		role="option"
		aria-selected={draft.folder?.mode === 'path' && draft.folder.path === path}
		disabled={checking}
		class="flex min-h-11 w-full flex-col justify-center rounded-lg px-2 text-start hover:bg-gray-50 disabled:opacity-60 dark:hover:bg-gray-800/60 md:min-h-9"
		title={path}
		on:click={onClick}
	>
		<span class="truncate text-[0.8125rem]"><bdi>{lastSegment(path)}</bdi></span>
		<span class="truncate font-mono text-[0.625rem] text-gray-500 dark:text-gray-400" dir="ltr">
			{path}
		</span>
		{#if detail}
			<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">{detail}</span>
		{/if}
	</button>
{/snippet}

{#if browsing && browseSource}
	<div class="flex flex-col gap-1 p-1">
		<button
			type="button"
			class="min-h-9 self-start rounded-lg px-2 text-[0.8125rem] hover:bg-gray-50 dark:hover:bg-gray-800/60"
			on:click={() => (browsing = false)}
		>
			← {$i18n.t('Back')} · <bdi>{computerName(computer)}</bdi>
		</button>
		<FolderBrowser source={browseSource} machineName={computerName(computer)} onPick={pickPath} />
	</div>
{:else}
	<div class="flex flex-col gap-0.5 p-1" role="listbox" aria-label={$i18n.t('Folder')}>
		<input
			bind:this={searchInput}
			bind:value={query}
			type="text"
			class="mb-1 h-9 w-full rounded-lg border border-gray-100 bg-transparent px-2 text-base outline-hidden placeholder:text-gray-400 dark:border-gray-800 md:text-[0.8125rem]"
			placeholder={$i18n.t('Search or paste a path')}
			title={pathPlaceholder(computer.platform)}
			aria-label={$i18n.t('Search or paste a path')}
			on:keydown={pickOnEnter}
		/>
		{#if problem}
			<p class="px-2 text-xs text-red-600 dark:text-red-400" role="alert">{problem}</p>
		{/if}
		{#if offline}
			<p class="px-2 text-[0.6875rem] text-amber-700 dark:text-amber-400">
				{$i18n.t('Not checked: {{name}} is offline. Buddy checks it when you send.', {
					name: computerName(computer)
				})}
			</p>
		{/if}
		{#if checking}
			<div class="flex items-center gap-2 px-2 py-1 text-xs text-gray-500" role="status">
				<Spinner className="size-3.5" />
				{$i18n.t('Checking on {{name}}…', { name: computerName(computer) })}
			</div>
		{/if}
		{#if typedPath}
			{@render folderRow(typedPath, $i18n.t('Use this path'), () => pickPath(typedPath))}
		{/if}

		{#if sections.inChatFolder.length > 0 && options.chat_folder}
			<div class="px-2 pt-1 text-[0.625rem] uppercase tracking-wider text-gray-400">
				{$i18n.t('In “{{folder}}”', { folder: options.chat_folder.name })}
			</div>
			{#each sections.inChatFolder as entry (entry.path)}
				{@render folderRow(entry.path, entry.isDefault ? $i18n.t('Default') : '', () =>
					pickPath(entry.path)
				)}
			{/each}
		{/if}

		{#if sections.recentHere.length > 0 || sections.recentElsewhere.length > 0}
			<div class="px-2 pt-1 text-[0.625rem] uppercase tracking-wider text-gray-400">
				{$i18n.t('Recent')}
			</div>
			{#each sections.recentHere as path (path)}
				{@render folderRow(path, '', () => pickPath(path))}
			{/each}
			{#each sections.recentElsewhere as entry (`${entry.machineId}:${entry.path}`)}
				{@render folderRow(
					entry.path,
					$i18n.t('On {{name}}', { name: nameOf(entry.machineId) }),
					() => pickElsewhere(entry.machineId, entry.path)
				)}
			{/each}
		{/if}

		{#if computer.folders === 'on'}
			<div class="px-2 pt-1 text-[0.625rem] uppercase tracking-wider text-gray-400">
				{$i18n.t('From Claude Code and Codex')}
			</div>
			{#if !suggestionsLoaded}
				<div class="mx-2 my-1 h-8 animate-pulse rounded-lg bg-gray-100 dark:bg-gray-800"></div>
				<div class="mx-2 my-1 h-8 animate-pulse rounded-lg bg-gray-100 dark:bg-gray-800"></div>
			{:else}
				{#each sections.fromAgents as suggestion (suggestion.path)}
					{@render folderRow(suggestion.path, suggestionDetail(suggestion), () =>
						pickPath(suggestion.path)
					)}
				{/each}
			{/if}
		{/if}

		{#if noFolders && suggestionsLoaded && !typedPath}
			<p class="px-2 py-1 text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('No folders yet. Type a full path, or let the AI choose.')}
			</p>
		{/if}

		<hr class="mx-1 my-0.5 border-gray-100 dark:border-gray-800" />
		{#if canUseNativeDialog}
			<button
				type="button"
				class="min-h-11 rounded-lg px-2 text-start text-[0.8125rem] hover:bg-gray-50 dark:hover:bg-gray-800/60 md:min-h-9"
				disabled={checking}
				on:click={chooseInAppDialog}
			>
				{$i18n.t('Choose folder…')}
			</button>
		{/if}
		{#if browseSource && !offline}
			<button
				type="button"
				class="min-h-11 rounded-lg px-2 text-start text-[0.8125rem] hover:bg-gray-50 dark:hover:bg-gray-800/60 md:min-h-9"
				on:click={() => (browsing = true)}
			>
				{$i18n.t('Browse {{name}}…', { name: computerName(computer) })}
			</button>
		{/if}
		<button
			type="button"
			role="option"
			aria-selected={draft.folder?.mode === 'auto'}
			class="flex min-h-11 flex-col items-start justify-center rounded-lg px-2 text-start hover:bg-gray-50 dark:hover:bg-gray-800/60 md:min-h-9"
			on:click={() => onPick({ mode: 'auto' }, computer.machine_id)}
		>
			<span class="text-[0.8125rem]">{$i18n.t('Let the AI choose')}</span>
			<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('Starts in its start folder on {{name}}', { name: computerName(computer) })}
			</span>
		</button>
	</div>
{/if}
