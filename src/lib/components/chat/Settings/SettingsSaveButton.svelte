<script lang="ts">
	import { createEventDispatcher, getContext, onDestroy } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n: any = getContext('i18n');
	const dispatch = createEventDispatcher();
	const captureNavigation = getContext<() => { navigationRevision: string | null }>(
		'settings-save-navigation'
	);

	export let type: 'button' | 'submit' = 'button';
	export let saveAction: () => unknown | Promise<unknown>;

	let saving = false;
	let mounted = true;

	onDestroy(() => {
		mounted = false;
	});

	export const save = async () => {
		if (saving) return;
		saving = true;
		const startedAt = Date.now();
		const navigation = captureNavigation?.();
		let succeeded = false;

		try {
			const result = await saveAction();
			succeeded = result !== false;
		} catch {
			// The settings handler reports the error and preserves the form for retry.
		}

		const feedbackDelay = 350 - (Date.now() - startedAt);
		if (feedbackDelay > 0) {
			await new Promise<void>((resolve) => setTimeout(resolve, feedbackDelay));
		}
		saving = false;
		if (succeeded && mounted) dispatch('save', navigation);
	};

	const handleClick = () => {
		if (type === 'button') save();
	};
</script>

<button
	{type}
	class="inline-flex min-w-24 items-center justify-center gap-2 rounded-full bg-black px-3.5 py-1.5 text-sm font-normal text-white transition duration-150 hover:bg-gray-900 active:scale-[0.96] disabled:cursor-wait disabled:opacity-70 dark:bg-white dark:text-black dark:hover:bg-gray-100"
	disabled={saving}
	aria-busy={saving}
	data-settings-save-button
	on:click={handleClick}
>
	{#if saving}
		<Spinner className="size-4" />
	{/if}
	<span aria-live="polite">{saving ? $i18n.t('Saving...') : $i18n.t('Save')}</span>
</button>
