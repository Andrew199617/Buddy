<script lang="ts">
	// Shows a chat folder's project folder under its title, and changes it.
	// Hidden for people without plans or who may not use computers.
	import { getContext, onMount } from 'svelte';

	import { getFolderProject, type FolderProject } from '$lib/apis/subscriptions';
	import ProjectFolderModal from './ProjectFolderModal.svelte';
	import { projectFolderChanges } from './project-folder-changes';
	import { chipState } from './project-folders';

	const i18n: any = getContext('i18n');

	export let folderId: string;
	export let folderName: string;

	let project: FolderProject | null = null;
	let showModal = false;

	$: chip = project ? chipState(project) : null;

	async function load() {
		try {
			const loaded = await getFolderProject(localStorage.token, folderId);
			project = null;
			if (loaded?.has_plans) project = loaded;
		} catch {
			project = null;
		}
	}

	onMount(load);

	// Saved here, in the folder's settings or from a chat's Project tab.
	$: if ($projectFolderChanges.folderId === folderId && $projectFolderChanges.count > 0) {
		void load();
	}

	// The fixed labels are translated; a computer and folder name are shown as they are.
	function chipLabel(label: string): string {
		if (label === 'Project folder needs attention')
			return $i18n.t('Project folder needs attention');
		if (label === 'Just chat, no files') return $i18n.t('Just chat, no files');
		if (label === 'AI chooses where to work') return $i18n.t('AI chooses where to work');
		return label;
	}
</script>

{#if project && chip}
	<ProjectFolderModal bind:show={showModal} {folderId} {folderName} />
	<button
		type="button"
		class="inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-full border px-3 text-xs md:min-h-7 {chip.tone ===
		'attention'
			? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300'
			: 'border-gray-200 bg-white text-gray-600 dark:border-gray-800 dark:bg-gray-850 dark:text-gray-300'}"
		title={chip.fromFolderName
			? $i18n.t('From “{{name}}”', { name: chip.fromFolderName })
			: undefined}
		aria-label={$i18n.t('Project folder: {{label}}', { label: chipLabel(chip.label) })}
		on:click={() => (showModal = true)}
	>
		<span class="truncate">{chipLabel(chip.label)}</span>
	</button>
{/if}
