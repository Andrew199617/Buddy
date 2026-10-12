<script lang="ts">
	// The chat side panel's Project tab: the chat folder's project folder, its
	// files and its commands. What it can open follows the folder's choice.
	import { getContext } from 'svelte';

	import type { FolderProject } from '$lib/apis/subscriptions';
	import ProjectFolderModal from '$lib/components/projectFolders/ProjectFolderModal.svelte';
	import {
		effectiveChoice,
		waitsForOwnChoice
	} from '$lib/components/projectFolders/project-folders';
	import ProjectComputer from './ProjectComputer.svelte';
	import {
		preferredTarget,
		projectTargets,
		targetProblem,
		type ProjectTarget
	} from './project-session';

	const i18n: any = getContext('i18n');

	export let project: FolderProject;
	export let onChanged: () => void = () => {};

	let showModal = false;
	let selectedMachineId = '';

	$: mode = effectiveChoice(project).mode;
	$: targets = projectTargets(project);
	$: target = chooseTarget(targets, selectedMachineId);
	$: problem = target ? targetProblem(target) : null;

	function chooseTarget(all: ProjectTarget[], machineId: string): ProjectTarget | null {
		const chosen = all.find((candidate) => candidate.computer.machine_id === machineId);
		return chosen ?? preferredTarget(all);
	}

	function selectComputer(event: Event) {
		selectedMachineId = (event.currentTarget as HTMLSelectElement).value;
	}
</script>

<ProjectFolderModal
	bind:show={showModal}
	folderId={project.folder_id}
	folderName={project.folder_name}
	onSaved={onChanged}
/>

<div class="flex h-full min-h-0 flex-col">
	<div class="flex shrink-0 items-center gap-2 px-3 pb-2">
		<div class="min-w-0 flex-1">
			<div class="truncate text-sm">{project.folder_name}</div>
			{#if target}
				<div
					class="truncate font-mono text-[0.6875rem] text-gray-500"
					dir="ltr"
					title={target.path}
				>
					{target.path}
				</div>
			{/if}
		</div>
		<button
			type="button"
			class="min-h-11 shrink-0 rounded-lg px-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
			on:click={() => (showModal = true)}
		>
			{$i18n.t('Change')}
		</button>
	</div>

	{#if targets.length > 1 && target}
		<div class="shrink-0 px-3 pb-2">
			<label class="sr-only" for="project-panel-computer">{$i18n.t('Computer')}</label>
			<select
				id="project-panel-computer"
				class="min-h-11 w-full rounded-lg bg-gray-50 px-2 text-base dark:bg-gray-850 md:min-h-8 md:text-sm"
				value={target.computer.machine_id}
				on:change={selectComputer}
			>
				{#each targets as option (option.computer.machine_id)}
					<option value={option.computer.machine_id}>{option.computer.name}</option>
				{/each}
			</select>
		</div>
	{/if}

	<div class="min-h-0 flex-1">
		{#if waitsForOwnChoice(project)}
			<div class="flex flex-col gap-2 px-3 text-xs text-gray-600 dark:text-gray-300">
				<p>
					{$i18n.t(
						"Someone else can change this folder's instructions, so its chats don't use files on your computers until you choose a project folder for it."
					)}
				</p>
				<button
					type="button"
					class="min-h-11 self-start rounded-xl bg-gray-900 px-4 text-sm font-medium text-white dark:bg-white dark:text-gray-900 md:min-h-9"
					on:click={() => (showModal = true)}
				>
					{$i18n.t('Choose a project folder')}
				</button>
			</div>
		{:else if mode === 'chat'}
			<p class="px-3 text-xs text-gray-600 dark:text-gray-300">
				{$i18n.t(
					'This folder is set to No files, just chat, so Claude and ChatGPT never touch your computers here.'
				)}
			</p>
		{:else if mode === 'auto'}
			<div class="flex flex-col gap-2 px-3 text-xs text-gray-600 dark:text-gray-300">
				<p>
					{$i18n.t(
						'Claude and ChatGPT choose where to work for chats in this folder. Choose a specific project folder to browse, edit and run commands here.'
					)}
				</p>
				<button
					type="button"
					class="min-h-11 self-start rounded-xl bg-gray-900 px-4 text-sm font-medium text-white dark:bg-white dark:text-gray-900 md:min-h-9"
					on:click={() => (showModal = true)}
				>
					{$i18n.t('Choose a project folder')}
				</button>
			</div>
		{:else if !target}
			<p class="px-3 text-xs text-gray-600 dark:text-gray-300">
				{$i18n.t('No project folder is set for your computers yet.')}
			</p>
		{:else if problem === 'choose_again'}
			<p class="px-3 text-xs text-amber-700 dark:text-amber-400">
				{$i18n.t('{{name}} was set up again. Choose the folder again.', {
					name: target.computer.name
				})}
			</p>
		{:else if problem === 'needs_app'}
			<p class="px-3 text-xs text-gray-600 dark:text-gray-300">
				{$i18n.t(
					'Browsing and commands here need the Buddy app on {{name}}. Claude and ChatGPT still work in this folder.',
					{ name: target.computer.name }
				)}
			</p>
		{:else if problem === 'not_browsable'}
			<p class="px-3 text-xs text-gray-600 dark:text-gray-300">
				{$i18n.t(
					'Browsing and commands here work only on computers connected with the Buddy app, so not on {{name}}. Claude and ChatGPT still work in this folder.',
					{ name: target.computer.name }
				)}
			</p>
		{:else if problem === 'offline'}
			<div class="flex flex-col gap-2 px-3 text-xs text-gray-600 dark:text-gray-300">
				<p>
					{$i18n.t('{{name}} is offline. Open the Buddy app on it, then check again.', {
						name: target.computer.name
					})}
				</p>
				<button
					type="button"
					class="min-h-11 self-start rounded-lg px-3 font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
					on:click={onChanged}
				>
					{$i18n.t('Check again')}
				</button>
			</div>
		{:else}
			{#key `${target.computer.machine_id}:${target.path}`}
				<ProjectComputer {target} />
			{/key}
		{/if}
	</div>
</div>
