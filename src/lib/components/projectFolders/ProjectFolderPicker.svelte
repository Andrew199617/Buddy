<script lang="ts">
	// Choosing the project folder on one computer: the app's own folder dialog,
	// folders Claude Code and Codex worked in, any folder on the computer (or
	// only its shared folders with an older app), a typed path, or a recent one.
	// Every pick is checked on that computer before it is used.
	import { getContext, onMount, tick } from 'svelte';

	import {
		checkMachineFolder,
		getSuggestedFolders,
		type FolderSuggestion,
		type ProjectFolderComputer
	} from '$lib/apis/subscriptions';
	import { getDesktop } from '$lib/desktop/bridge';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import FolderBrowser from './FolderBrowser.svelte';
	import { computerFolderSource, sharedFolderSource } from './folder-sources';
	import {
		lastSegment,
		lastUsedAgo,
		pathPlaceholder,
		suggestionTools,
		type LastUsed
	} from './project-folders';

	const i18n: any = getContext('i18n');

	export let computer: ProjectFolderComputer;
	export let initialPath = '';
	export let onBack: () => void;
	export let onPick: (path: string) => void;

	const desktop = getDesktop();

	let typedPath = initialPath;
	let checking = false;
	let result = '';
	let resultIsError = false;
	let canUseNativeDialog = false;
	let pathInput: HTMLInputElement;
	let backButton: HTMLButtonElement;
	// Whether this computer's folders can be browsed; its answers can change it.
	let browsing = computer.folders;
	let suggestions: FolderSuggestion[] = [];
	let suggestionsLoaded = false;
	let showBrowser = false;
	let isThisPc = false;

	$: offline = computer.kind === 'app' && computer.online === false;

	async function loadSuggestions() {
		suggestionsLoaded = false;
		try {
			const found = await getSuggestedFolders(localStorage.token, computer.machine_id);
			if (found.state !== 'ok') {
				browsing = found.state;
				return;
			}
			suggestions = found.folders;
		} catch {
			suggestions = [];
		} finally {
			suggestionsLoaded = true;
		}
	}

	async function findThisPc() {
		if (!desktop) return;
		try {
			const status = await desktop.getStatus();
			isThisPc = status.machineId === computer.machine_id;
		} catch {
			isThisPc = false;
		}
		canUseNativeDialog = isThisPc && Boolean(desktop.chooseProjectFolder);
	}

	onMount(() => {
		// The picker replaced the button that opened it, so focus moves here.
		backButton?.focus();
		void findThisPc();
		if (browsing === 'on') void loadSuggestions();
	});

	/** In the Buddy app on this computer: ask the app, which asks the person in Windows. */
	async function allowBrowsingHere() {
		if (!desktop) return;
		const status = await desktop.setPermission('allowFolderBrowsing', true);
		if (status.allowFolderBrowsing !== true) return;
		browsing = 'on';
		await loadSuggestions();
	}

	function toolsLabel(suggestion: FolderSuggestion): string {
		const tools = suggestionTools(suggestion.sources);
		if (tools === 'both') return $i18n.t('Claude Code and Codex');
		if (tools === 'codex') return 'Codex';
		return 'Claude Code';
	}

	function whenLabel(ago: LastUsed): string {
		if (ago.unit === 'today') return $i18n.t('today');
		if (ago.unit === 'yesterday') return $i18n.t('yesterday');
		if (ago.unit === 'days') return $i18n.t('{{count}} days ago', { count: ago.count });
		return $i18n.t('{{count}} months ago', { count: ago.count });
	}

	function suggestionDetail(suggestion: FolderSuggestion): string {
		const ago = lastUsedAgo(suggestion.last_used, Date.now() / 1000);
		return `${toolsLabel(suggestion)} · ${whenLabel(ago)}`;
	}

	// Checks a candidate on the computer; only a folder that exists there is used.
	async function runCheck(path: string) {
		checking = true;
		result = '';
		try {
			const found = await checkMachineFolder(localStorage.token, computer.machine_id, path);
			typedPath = found.path;
			result = $i18n.t('Found on {{name}}', { name: computer.name });
			resultIsError = false;
			onPick(found.path);
		} catch (error) {
			result = `${error}`;
			resultIsError = true;
		} finally {
			checking = false;
		}
	}

	async function chooseInAppDialog() {
		if (!desktop?.chooseProjectFolder) return;
		const startAt = typedPath.trim() || initialPath || undefined;
		const chosen = await desktop.chooseProjectFolder({ defaultPath: startAt });
		if (chosen) await runCheck(chosen);
	}

	// Not a form: the Edit Folder dialog around this picker is one, and Enter must not submit it.
	function checkOnEnter(event: KeyboardEvent) {
		if (event.key !== 'Enter' || event.isComposing) return;
		event.preventDefault();
		if (checking || !typedPath.trim()) return;
		void runCheck(typedPath);
	}

	async function keepInputVisible() {
		// The on-screen keyboard can cover the field on a phone.
		await tick();
		pathInput?.scrollIntoView({ block: 'center' });
	}
</script>

<div class="flex flex-col gap-3">
	<div class="sticky top-0 z-10 flex items-center gap-2 bg-white py-1 dark:bg-gray-900">
		<button
			type="button"
			class="min-h-11 rounded-lg px-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-8"
			bind:this={backButton}
			on:click={onBack}
		>
			← {computer.name}
		</button>
		<span class="text-xs text-gray-500">{$i18n.t('Choose a folder')}</span>
	</div>

	{#if canUseNativeDialog}
		<button
			type="button"
			class="min-h-11 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-9"
			disabled={checking}
			on:click={chooseInAppDialog}
		>
			{$i18n.t('Choose folder on this computer…')}
		</button>
	{/if}

	{#if offline}
		<p class="text-xs text-amber-700 dark:text-amber-400">
			{$i18n.t('{{name}} is offline. Open the Buddy app on it to choose a folder.', {
				name: computer.name
			})}
		</p>
	{:else if browsing === 'on'}
		<section aria-label={$i18n.t('From Claude Code and Codex')}>
			<h4 class="mb-1 text-xs text-gray-600 dark:text-gray-400">
				{$i18n.t('From Claude Code and Codex')}
			</h4>
			{#if !suggestionsLoaded}
				<div class="flex items-center gap-2 px-2 py-1 text-xs text-gray-500" role="status">
					<Spinner className="size-3.5" />
					{$i18n.t('Looking for folders Claude Code and Codex worked in…')}
				</div>
			{:else if suggestions.length === 0}
				<p class="px-2 text-xs text-gray-500">
					{$i18n.t('No Claude Code or Codex history on {{name}}.', { name: computer.name })}
				</p>
			{:else}
				<ul class="flex max-h-[40vh] flex-col overflow-y-auto overscroll-contain">
					{#each suggestions as suggestion (suggestion.path)}
						<li>
							<button
								type="button"
								class="flex min-h-11 w-full flex-col justify-center rounded-lg px-2 text-start hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-9"
								disabled={checking}
								on:click={() => runCheck(suggestion.path)}
							>
								<span class="text-sm"><bdi>{lastSegment(suggestion.path)}</bdi></span>
								<span class="break-all font-mono text-[0.6875rem] text-gray-500" dir="ltr">
									{suggestion.path}
								</span>
								<span class="text-[0.6875rem] text-gray-500">{suggestionDetail(suggestion)}</span>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
		{#if showBrowser}
			<section aria-label={$i18n.t('Browse {{name}}', { name: computer.name })}>
				<FolderBrowser
					source={computerFolderSource(computer.machine_id, computer.platform)}
					machineName={computer.name}
					onPick={runCheck}
				/>
			</section>
		{:else}
			<button
				type="button"
				class="min-h-11 self-start rounded-lg px-3 text-sm font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
				on:click={() => (showBrowser = true)}
			>
				{$i18n.t('Browse {{name}}…', { name: computer.name })}
			</button>
		{/if}
	{:else if browsing === 'off' && isThisPc && desktop}
		<button
			type="button"
			class="min-h-11 self-start rounded-lg px-3 text-sm font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
			on:click={allowBrowsingHere}
		>
			{$i18n.t('Show folders from this PC…')}
		</button>
	{:else if browsing === 'off'}
		<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t(
				'Turn on Folder suggestions in the Buddy app on {{name}}. You can still type a path.',
				{
					name: computer.name
				}
			)}
		</p>
	{:else if computer.can_browse}
		<section aria-label={$i18n.t('Shared folders')}>
			<h4 class="mb-1 text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Shared folders')}</h4>
			<p class="mb-1 text-[0.6875rem] text-gray-500">
				{$i18n.t('Update the Buddy app on {{name}} to browse all its folders.', {
					name: computer.name
				})}
			</p>
			<FolderBrowser
				source={sharedFolderSource(computer.machine_id, computer.platform)}
				machineName={computer.name}
				onPick={runCheck}
			/>
		</section>
	{:else if computer.kind === 'address'}
		<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t("Browsing needs the Buddy app on that computer. Type the folder's full path.")}
		</p>
	{:else if computer.kind === 'server'}
		<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t(
				"This is a folder inside the Buddy server. With Full access, Claude and ChatGPT can change Buddy's own files there."
			)}
		</p>
	{/if}

	<div class="flex flex-col gap-1">
		<label
			class="text-xs text-gray-600 dark:text-gray-400"
			for="project-folder-path-{computer.machine_id}"
		>
			{$i18n.t('Folder path')}
		</label>
		<div class="flex gap-2">
			<input
				id="project-folder-path-{computer.machine_id}"
				bind:this={pathInput}
				bind:value={typedPath}
				class="min-h-11 min-w-0 flex-1 rounded-xl bg-gray-50 px-3 font-mono text-base outline-hidden dark:bg-gray-850 md:min-h-9 md:text-sm"
				dir="ltr"
				autocapitalize="off"
				autocorrect="off"
				spellcheck="false"
				autocomplete="off"
				enterkeyhint="done"
				placeholder={pathPlaceholder(computer.platform)}
				disabled={checking}
				on:focus={keepInputVisible}
				on:keydown={checkOnEnter}
			/>
			<button
				type="button"
				class="min-h-11 shrink-0 rounded-xl px-3 text-sm font-medium hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-850 md:min-h-9"
				disabled={checking || !typedPath.trim()}
				on:click={() => runCheck(typedPath)}
			>
				{#if checking}<Spinner className="size-4" />{:else}{$i18n.t('Use')}{/if}
			</button>
		</div>
		{#if result}
			<p
				class="text-xs {resultIsError
					? 'text-red-600 dark:text-red-400'
					: 'text-green-700 dark:text-green-400'}"
				role={resultIsError ? 'alert' : 'status'}
			>
				{result}
			</p>
		{/if}
	</div>

	{#if computer.recent_paths.length > 0}
		<section aria-label={$i18n.t('Recent')}>
			<h4 class="mb-1 text-xs text-gray-600 dark:text-gray-400">
				{$i18n.t('Recent on {{name}}', { name: computer.name })}
			</h4>
			<ul class="flex flex-col">
				{#each computer.recent_paths as recent (recent)}
					<li>
						<button
							type="button"
							class="flex min-h-11 w-full items-center rounded-lg px-2 text-start font-mono text-xs break-all hover:bg-gray-50 dark:hover:bg-gray-850 md:min-h-8"
							dir="ltr"
							disabled={checking}
							on:click={() => runCheck(recent)}
						>
							{recent}
						</button>
					</li>
				{/each}
			</ul>
		</section>
	{/if}
</div>
