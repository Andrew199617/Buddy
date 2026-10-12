<script lang="ts">
	// The project folder's files in the chat side panel: browse inside the
	// project folder, read text files and, when the computer allows it, edit them.
	import { getContext, onDestroy } from 'svelte';

	import {
		CompanionError,
		detectLineEnding,
		toEditorText,
		toFileText,
		type CompanionDirectoryEntry,
		type CompanionGrant,
		type LineEnding,
		type WorkspaceClient
	} from '$lib/apis/companion';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import FileTypeIcon from '$lib/components/chat/FileNav/FileTypeIcon.svelte';
	import { crumbsBelow, isWithinProject, parentWithinProject } from './project-session';

	const i18n: any = getContext('i18n');

	export let client: WorkspaceClient;
	export let grant: CompanionGrant;
	// The project folder, '/'-separated and relative to the shared folder.
	export let root: string;
	export let projectName: string;
	export let computerName: string;
	// Turns a failed request into a message; ends the connection when the sign-in ended.
	export let onFailure: (cause: unknown) => string;

	let directory = root;
	let entries: CompanionDirectoryEntry[] = [];
	let listing = false;
	let listError = '';
	let browseGeneration = 0;

	let filePath: string | null = null;
	let fileName = '';
	let fileText = '';
	let savedText = '';
	let fileVersion = '';
	let lineEnding: LineEnding = '\n';
	let editing = false;
	let conflict = false;
	let fileBusy = false;
	let fileError = '';
	let fileNotice = '';
	let fileGeneration = 0;
	let disposed = false;

	$: canEdit = grant.capabilities.write;
	$: dirty = filePath !== null && fileText !== savedText;
	$: crumbs = crumbsBelow(directory, root);
	$: parent = parentWithinProject(directory, root);

	async function browse(path: string) {
		if (!isWithinProject(path, root)) return;
		const generation = ++browseGeneration;
		listing = true;
		listError = '';
		try {
			const found = await client.getDirectory(grant.id, path);
			if (disposed || generation !== browseGeneration) return;
			directory = found.path;
			entries = found.entries;
		} catch (cause) {
			if (disposed || generation !== browseGeneration) return;
			listError = onFailure(cause);
		} finally {
			if (!disposed && generation === browseGeneration) listing = false;
		}
	}

	function forgetFile() {
		fileGeneration += 1;
		filePath = null;
		fileName = '';
		fileText = '';
		savedText = '';
		fileVersion = '';
		lineEnding = '\n';
		editing = false;
		conflict = false;
		fileBusy = false;
		fileError = '';
		fileNotice = '';
	}

	async function openFile(entry: CompanionDirectoryEntry) {
		forgetFile();
		const generation = fileGeneration;
		filePath = entry.path;
		fileName = entry.name;
		fileBusy = true;
		try {
			const file = await client.getFile(grant.id, entry.path);
			if (disposed || generation !== fileGeneration) return;
			// The editor holds LF text; saving puts the file's own line ending back.
			lineEnding = detectLineEnding(file.content);
			fileText = toEditorText(file.content);
			savedText = fileText;
			fileVersion = file.version;
		} catch (cause) {
			if (disposed || generation !== fileGeneration) return;
			fileError = onFailure(cause);
		} finally {
			if (!disposed && generation === fileGeneration) fileBusy = false;
		}
	}

	function openEntry(entry: CompanionDirectoryEntry) {
		if (entry.type === 'directory') {
			void browse(entry.path);
			return;
		}
		void openFile(entry);
	}

	function closeFile() {
		if (dirty && !confirm($i18n.t('Discard your changes to {{name}}?', { name: fileName }))) {
			return;
		}
		forgetFile();
	}

	/** Saves over the version that was opened; overwrite is only offered after a conflict. */
	async function writeFile(overwrite: boolean) {
		const path = filePath;
		const text = fileText;
		if (path === null || !canEdit || !editing) return;
		const generation = fileGeneration;
		fileBusy = true;
		fileError = '';
		fileNotice = '';
		try {
			const saved = await client.putFile(
				grant.id,
				path,
				toFileText(text, lineEnding),
				fileVersion,
				overwrite
			);
			if (disposed || generation !== fileGeneration) return;
			savedText = text;
			fileVersion = saved.version;
			conflict = false;
			fileNotice = $i18n.t('Saved on {{name}}', { name: computerName });
		} catch (cause) {
			if (disposed || generation !== fileGeneration) return;
			if (cause instanceof CompanionError && cause.status === 409) {
				// Keep the edits and let the person choose; the computer's copy is unchanged.
				conflict = true;
				return;
			}
			fileError = onFailure(cause);
		} finally {
			if (!disposed && generation === fileGeneration) fileBusy = false;
		}
	}

	function reloadFile() {
		if (filePath === null) return;
		void openFile({ name: fileName, path: filePath, type: 'file' });
	}

	void browse(root);

	onDestroy(() => {
		disposed = true;
	});
</script>

<div class="flex h-full min-h-0 flex-col">
	{#if filePath !== null}
		<div class="flex shrink-0 items-center gap-1 px-2 pb-1">
			<button
				type="button"
				class="min-h-11 shrink-0 rounded-lg px-2 text-sm text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-850 md:min-h-8"
				on:click={closeFile}
			>
				← {$i18n.t('Files')}
			</button>
			<span class="min-w-0 flex-1 truncate text-sm" title={filePath}>{fileName}</span>
			{#if canEdit && !editing}
				<button
					type="button"
					class="min-h-11 shrink-0 rounded-lg px-3 text-sm font-medium hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-850 md:min-h-8"
					disabled={fileBusy || fileError !== ''}
					on:click={() => (editing = true)}
				>
					{$i18n.t('Edit')}
				</button>
			{:else if editing && !conflict}
				<button
					type="button"
					class="flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg bg-gray-900 px-3 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-8"
					disabled={!dirty || fileBusy}
					on:click={() => writeFile(false)}
				>
					{$i18n.t('Save')}
					{#if fileBusy}<Spinner className="size-3.5" />{/if}
				</button>
			{/if}
		</div>

		{#if conflict}
			<div
				role="alert"
				class="mx-2 mb-2 flex flex-col gap-2 rounded-xl bg-amber-50 p-2.5 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
			>
				<p>
					{$i18n.t(
						'{{name}} changed on {{computer}} after you opened it. Reload discards your edits; Overwrite replaces the copy there with yours.',
						{ name: fileName, computer: computerName }
					)}
				</p>
				<div class="flex flex-wrap gap-2">
					<button
						type="button"
						class="min-h-11 rounded-lg px-3 font-medium hover:bg-amber-100 dark:hover:bg-amber-900/40 md:min-h-8"
						disabled={fileBusy}
						on:click={reloadFile}
					>
						{$i18n.t('Reload')}
					</button>
					<button
						type="button"
						class="min-h-11 rounded-lg bg-amber-900 px-3 font-medium text-white disabled:opacity-50 dark:bg-amber-200 dark:text-amber-950 md:min-h-8"
						disabled={fileBusy}
						on:click={() => writeFile(true)}
					>
						{$i18n.t('Overwrite')}
					</button>
				</div>
			</div>
		{/if}
		{#if fileError}
			<p role="alert" class="px-3 pb-2 text-xs text-red-600 dark:text-red-400">{fileError}</p>
		{/if}
		{#if fileNotice && !dirty}
			<p role="status" class="px-3 pb-2 text-xs text-green-700 dark:text-green-400">{fileNotice}</p>
		{/if}

		{#if fileBusy && fileText === '' && !fileError}
			<div class="flex items-center gap-2 px-3 py-2 text-xs text-gray-500">
				<Spinner className="size-3.5" />
				{$i18n.t('Opening {{name}}…', { name: fileName })}
			</div>
		{:else if !fileError}
			<textarea
				class="mx-2 mb-2 min-h-0 flex-1 resize-none rounded-xl bg-gray-50 p-2.5 font-mono text-base outline-hidden dark:bg-gray-850 md:text-xs"
				aria-label={$i18n.t('Contents of {{name}}', { name: fileName })}
				bind:value={fileText}
				readonly={!editing}
				spellcheck="false"
				autocapitalize="off"
				autocomplete="off"
				dir="ltr"
			></textarea>
			{#if editing}
				<p
					class="shrink-0 px-3 pb-[max(env(safe-area-inset-bottom),0.5rem)] text-[0.6875rem] text-gray-500"
				>
					{$i18n.t(
						'Saving changes the file on {{computer}} and keeps its line endings. Tools there may run what project files say, such as build scripts.',
						{ computer: computerName }
					)}
				</p>
			{/if}
		{/if}
	{:else}
		<div class="flex shrink-0 items-center gap-1 px-2 pb-1">
			<button
				type="button"
				class="min-h-11 shrink-0 rounded-lg px-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 dark:text-gray-300 dark:hover:bg-gray-850 md:min-h-8"
				aria-label={$i18n.t('Up one folder')}
				disabled={parent === null || listing}
				on:click={() => parent !== null && browse(parent)}
			>
				↑
			</button>
			<nav
				class="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-xs text-gray-500 scrollbar-hidden"
				aria-label={$i18n.t('Folder')}
			>
				<button
					type="button"
					class="min-h-11 rounded px-1 font-medium text-gray-700 dark:text-gray-300 md:min-h-8"
					on:click={() => browse(root)}
				>
					{projectName}
				</button>
				{#each crumbs as crumb (crumb.path)}
					<span aria-hidden="true">/</span>
					<button
						type="button"
						class="min-h-11 rounded px-1 md:min-h-8"
						on:click={() => browse(crumb.path)}
					>
						{crumb.name}
					</button>
				{/each}
			</nav>
			<button
				type="button"
				class="min-h-11 shrink-0 rounded-lg px-2 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40 dark:text-gray-300 dark:hover:bg-gray-850 md:min-h-8"
				disabled={listing}
				on:click={() => browse(directory)}
			>
				{$i18n.t('Refresh')}
			</button>
		</div>

		{#if listError}
			<p role="alert" class="px-3 pb-2 text-xs text-red-600 dark:text-red-400">{listError}</p>
		{/if}

		<ul
			class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-1 pb-[max(env(safe-area-inset-bottom),0.5rem)]"
			aria-busy={listing}
		>
			{#each entries as entry (entry.path)}
				<li>
					<button
						type="button"
						class="flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-start text-sm hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-8"
						on:click={() => openEntry(entry)}
					>
						<FileTypeIcon name={entry.name} type={entry.type} />
						<span class="min-w-0 truncate">{entry.name}</span>
					</button>
				</li>
			{:else}
				{#if listing}
					<li class="flex items-center gap-2 px-2 py-2 text-xs text-gray-500">
						<Spinner className="size-3.5" />
						{$i18n.t('Reading the folder…')}
					</li>
				{:else if !listError}
					<li class="px-2 py-2 text-xs text-gray-500">{$i18n.t('This folder is empty.')}</li>
				{/if}
			{/each}
		</ul>
	{/if}
</div>
