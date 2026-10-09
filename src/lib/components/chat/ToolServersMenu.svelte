<script lang="ts">
	import { mobile } from '$lib/stores';
	import Dropdown from '../common/Dropdown.svelte';
	import ToolServersContent from './ToolServersContent.svelte';
	import ToolServersModal from './ToolServersModal.svelte';

	export let show = false;
	export let selectedToolIds = [];

	function closeMenu(): void {
		show = false;
	}
</script>

{#if $mobile}
	<Dropdown
		bind:show
		side="top"
		align="start"
		visualViewportAware
		contentClass="buddy-action-menu buddy-available-tools-menu"
	>
		<slot />
		<ToolServersContent
			slot="content"
			{selectedToolIds}
			preserveComposerFocus
			onClose={closeMenu}
		/>
	</Dropdown>
{:else}
	<slot />
	<ToolServersModal bind:show {selectedToolIds} />
{/if}

<style>
	:global(.buddy-available-tools-menu) {
		width: min(312px, calc(100vw - 16px));
		border: 1px solid var(--buddy-action-edge, rgba(255, 255, 255, 0.7));
		border-radius: 30px;
		background: var(--buddy-action-surface, rgba(245, 249, 246, 0.94));
		color: var(--buddy-action-text, #263d2e);
		box-shadow: 0 18px 48px rgba(17, 35, 24, 0.2);
		-webkit-backdrop-filter: blur(28px) saturate(150%);
		backdrop-filter: blur(28px) saturate(150%);
		overflow-x: hidden;
		overscroll-behavior: contain;
	}

	:global(.dark .buddy-available-tools-menu) {
		border-color: var(--buddy-action-edge, rgba(232, 245, 235, 0.16));
		background: var(--buddy-action-surface, rgba(49, 57, 52, 0.94));
		color: var(--buddy-action-text, #f0f5f1);
		box-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
	}

	:global(.buddy-available-tools-menu .available-tools-content > div:first-child) {
		padding: 8px 12px 0 16px;
		color: inherit;
	}

	:global(.buddy-available-tools-menu .available-tools-content > div:first-child > div) {
		font-size: 16px;
	}

	:global(.buddy-available-tools-menu .available-tools-content > div:first-child > button) {
		display: flex;
		width: 44px;
		height: 44px;
		align-items: center;
		justify-content: center;
		border-radius: 50%;
	}

	:global(.buddy-available-tools-menu [role='button']) {
		min-height: 44px;
		border-radius: 12px;
	}

	:global(.buddy-available-tools-menu [role='button']:not([aria-disabled='true']):hover),
	:global(.buddy-available-tools-menu [role='button']:focus-visible) {
		background: var(--buddy-action-hover, rgba(52, 91, 63, 0.09));
	}

	:global(.buddy-available-tools-menu [class*='text-xs']) {
		font-size: 13px;
	}

	:global(.buddy-available-tools-menu [class*='text-gray-500']),
	:global(.buddy-available-tools-menu [class*='text-gray-600']) {
		color: var(--buddy-action-muted, #66796c);
	}

	:global(.dark .buddy-available-tools-menu [class*='text-gray-500']),
	:global(.dark .buddy-available-tools-menu [class*='text-gray-600']) {
		color: var(--buddy-action-muted, #a9b7ad);
	}

	@media (prefers-reduced-transparency: reduce) {
		:global(.buddy-available-tools-menu) {
			background: #f5f9f6;
			backdrop-filter: none;
		}
		:global(.dark .buddy-available-tools-menu) {
			background: #313934;
		}
	}
</style>
