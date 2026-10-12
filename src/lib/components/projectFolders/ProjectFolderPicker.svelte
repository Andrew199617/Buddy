<script lang="ts">
	// Choosing the project folder on one computer: the app's own folder dialog,
	// the computer's shared folders, a typed path, or a recent one. Every pick
	// is checked on that computer before it is used.
	import { getContext, onMount, tick } from 'svelte';

	import { checkMachineFolder, type ProjectFolderComputer } from '$lib/apis/subscriptions';
	import { getDesktop } from '$lib/desktop/bridge';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SharedFolderBrowser from './SharedFolderBrowser.svelte';
	import { pathPlaceholder } from './project-folders';

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

	$: offline = computer.kind === 'app' && computer.online === false;

	onMount(async () => {
		if (!desktop?.chooseProjectFolder) return;
		try {
			const status = await desktop.getStatus();
			canUseNativeDialog = status.machineId === computer.machine_id;
		} catch {
			canUseNativeDialog = false;
		}
	});

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
		const chosen = await desktop.chooseProjectFolder();
		if (chosen) await runCheck(chosen);
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
	{:else if computer.can_browse}
		<section aria-label={$i18n.t('Shared folders')}>
			<h4 class="mb-1 text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Shared folders')}</h4>
			<SharedFolderBrowser
				machineId={computer.machine_id}
				machineName={computer.name}
				platform={computer.platform}
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

	<form class="flex flex-col gap-1" on:submit|preventDefault={() => runCheck(typedPath)}>
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
			/>
			<button
				type="submit"
				class="min-h-11 shrink-0 rounded-xl px-3 text-sm font-medium hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-850 md:min-h-9"
				disabled={checking || !typedPath.trim()}
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
	</form>

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
