<script lang="ts">
	// A bottom sheet for the bar on phones. It sits above the safe area, follows the visible height
	// when the keyboard is open, and drops the keyboard when it opens.
	import { getContext, onDestroy, tick } from 'svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let title: string;
	export let onClose: () => void = () => {};

	let panel: HTMLDivElement;

	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	}

	function close() {
		show = false;
		onClose();
	}

	function closeOnEscape(event: KeyboardEvent) {
		if (show && event.key === 'Escape') close();
	}

	async function opened() {
		// The keyboard would cover the sheet; the composer gets focus back after a pick.
		const active = document.activeElement;
		if (active instanceof HTMLElement && active.closest('.buddy-composer')) active.blur();
		await tick();
		panel?.focus();
	}

	$: if (show) void opened();

	onDestroy(() => {
		show = false;
	});
</script>

<svelte:window on:keydown={closeOnEscape} />

{#if show}
	<div
		use:portal
		class="buddy-run-target-sheet fixed inset-0 z-[9999]"
		data-composer-menu="run-target"
	>
		<button
			type="button"
			class="absolute inset-0 h-full w-full cursor-default bg-black/30"
			aria-label={$i18n.t('Close')}
			tabindex="-1"
			on:click={close}
		></button>
		<div
			bind:this={panel}
			role="dialog"
			aria-modal="true"
			aria-label={title}
			tabindex="-1"
			class="buddy-run-target-sheet-panel absolute inset-x-0 bottom-0 flex flex-col rounded-t-3xl bg-white text-gray-800 shadow-xl outline-hidden dark:bg-gray-850 dark:text-gray-100"
		>
			<div class="flex h-12 shrink-0 items-center justify-between px-4">
				<span class="text-sm font-medium">{title}</span>
				<button
					type="button"
					class="min-h-11 rounded-full px-3 text-sm font-medium text-emerald-700 dark:text-emerald-400"
					on:click={close}
				>
					{$i18n.t('Done')}
				</button>
			</div>
			<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2">
				<slot />
			</div>
		</div>
	</div>
{/if}

<style>
	.buddy-run-target-sheet-panel {
		max-height: calc(var(--buddy-visible-height, 100dvh) * 0.85);
		padding-bottom: max(16px, var(--buddy-safe-bottom, env(safe-area-inset-bottom, 0px)));
		padding-left: env(safe-area-inset-left, 0px);
		padding-right: env(safe-area-inset-right, 0px);
	}
</style>
