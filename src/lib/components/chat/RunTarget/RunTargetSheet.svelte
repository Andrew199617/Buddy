<script lang="ts">
	// A bottom sheet for the bar on phones. It sits above the safe area and the on-screen keyboard,
	// drops the keyboard when it opens, keeps focus inside while open, and gives focus back after.
	import { getContext, onDestroy, tick } from 'svelte';
	import * as FocusTrap from 'focus-trap';

	const i18n: any = getContext('i18n');

	export let show = false;
	export let title: string;
	export let onClose: () => void = () => {};

	let panel: HTMLDivElement;
	let focusTrap: FocusTrap.FocusTrap | null = null;
	// What had focus before the sheet opened, given back when it closes.
	let focusBefore: HTMLElement | null = null;
	let isOpen = false;
	// The visible height and how far the keyboard covers the bottom, from the visual viewport.
	let visibleHeight: number | null = null;
	let keyboardCover = 0;

	function followVisibleViewport() {
		const viewport = window.visualViewport;
		if (!viewport) {
			visibleHeight = null;
			keyboardCover = 0;
			return;
		}
		visibleHeight = viewport.height;
		keyboardCover = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
	}

	function watchVisibleViewport(watch: boolean) {
		const viewport = window.visualViewport;
		if (!viewport) return;
		if (watch) {
			viewport.addEventListener('resize', followVisibleViewport);
			viewport.addEventListener('scroll', followVisibleViewport);
		} else {
			viewport.removeEventListener('resize', followVisibleViewport);
			viewport.removeEventListener('scroll', followVisibleViewport);
		}
	}

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
		isOpen = true;
		const active = document.activeElement;
		focusBefore = null;
		if (active instanceof HTMLElement && active !== document.body) focusBefore = active;
		// The keyboard would cover the sheet.
		if (active instanceof HTMLElement && active.closest('.buddy-composer')) active.blur();
		followVisibleViewport();
		watchVisibleViewport(true);
		await tick();
		if (!panel) return;
		focusTrap = FocusTrap.createFocusTrap(panel, {
			initialFocus: panel,
			fallbackFocus: panel,
			escapeDeactivates: false,
			allowOutsideClick: true,
			returnFocusOnDeactivate: false
		});
		focusTrap.activate();
	}

	async function closed() {
		isOpen = false;
		watchVisibleViewport(false);
		focusTrap?.deactivate();
		focusTrap = null;
		const before = focusBefore;
		focusBefore = null;
		// Once the panel is gone: a pick that already focused the composer keeps it, and
		// otherwise focus goes back where it was.
		await tick();
		const active = document.activeElement;
		const focusIsLost = !active || active === document.body;
		if (focusIsLost && before?.isConnected) before.focus();
	}

	$: if (show && !isOpen) void opened();
	$: if (!show && isOpen) void closed();

	onDestroy(() => {
		if (isOpen) void closed();
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
			style:bottom="{keyboardCover}px"
			style:--buddy-sheet-visible-height={visibleHeight === null ? null : `${visibleHeight}px`}
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
		max-height: calc(var(--buddy-sheet-visible-height, 100dvh) * 0.85);
		padding-bottom: max(16px, var(--buddy-safe-bottom, env(safe-area-inset-bottom, 0px)));
		padding-left: env(safe-area-inset-left, 0px);
		padding-right: env(safe-area-inset-right, 0px);
	}
</style>
