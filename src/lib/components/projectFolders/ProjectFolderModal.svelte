<script lang="ts">
	// The Project folder section on its own, opened from the folder page chip.
	// Works for people who can only read a shared folder: the choice is theirs.
	import { getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ProjectFolderSection from './ProjectFolderSection.svelte';

	const i18n: any = getContext('i18n');

	export let show = false;
	export let folderId: string;
	export let folderName: string;

	let section: ProjectFolderSection;
	let saving = false;
	let error = '';
	let loadError = '';

	// A new opening starts without the last one's errors.
	$: if (!show) {
		error = '';
		loadError = '';
	}

	async function save() {
		saving = true;
		const saved = await section.save();
		saving = false;
		if (!saved) return;
		show = false;
	}
</script>

<Modal size="sm" bind:show>
	<div class="flex flex-col gap-3 px-4 pt-3 pb-4 dark:text-gray-200">
		<div class="flex items-center justify-between gap-2">
			<h2 class="min-w-0 truncate text-sm">
				{$i18n.t('Project folder for “{{name}}”', { name: folderName })}
			</h2>
			<button
				type="button"
				aria-label={$i18n.t('Close')}
				class="flex size-11 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800 md:size-7"
				on:click={() => (show = false)}
			>
				<XMark className="size-4" />
			</button>
		</div>

		{#if show}
			<ProjectFolderSection bind:this={section} {folderId} bind:error bind:loadError />
		{/if}

		<div
			class="sticky bottom-0 flex justify-end gap-2 bg-white pt-2 pb-[max(env(safe-area-inset-bottom),0rem)] dark:bg-gray-900"
		>
			<button
				type="button"
				class="min-h-11 rounded-full px-4 text-sm hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
				on:click={() => (show = false)}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				type="button"
				class="flex min-h-11 items-center gap-2 rounded-full bg-black px-4 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black md:min-h-8"
				disabled={saving || loadError !== ''}
				on:click={save}
			>
				{$i18n.t('Save')}
				{#if saving}<Spinner className="size-3.5" />{/if}
			</button>
		</div>
	</div>
</Modal>
