<script lang="ts" context="module">
	import { createLazyComponent } from '$lib/utils/lazy-component';

	function importTerminalOutputFile() {
		return import('./TerminalOutputFile.svelte');
	}
	const terminalFileFeature = createLazyComponent(importTerminalOutputFile);
</script>

<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import LazyFeatureStatus from '$lib/components/common/LazyFeatureStatus.svelte';

	export let item: any;
	export let chatId = '';
	const i18n: Writable<I18n> = getContext('i18n');

	function getFileName(file: any): string {
		if (file?.name) return String(file.name);
		const path = String(file?.full_path || file?.path || '');
		const segments = path.split('/').filter(Boolean);
		return segments.at(-1) || 'file';
	}
	$: fileName = getFileName(item);

	onMount(() => {
		void terminalFileFeature.load();
	});
</script>

{#if $terminalFileFeature.component}
	<svelte:component this={$terminalFileFeature.component} {item} {chatId} />
{:else}
	<div class="my-2 overflow-hidden rounded-xl border border-gray-200 dark:border-white/8">
		<div
			class="truncate border-b border-gray-100 px-2.5 py-2 text-xs font-medium dark:border-white/8"
		>
			{fileName}
		</div>
		<LazyFeatureStatus
			feature={$i18n.t('File preview')}
			compact={true}
			error={$terminalFileFeature.error}
			onRetry={terminalFileFeature.load}
		/>
	</div>
{/if}
