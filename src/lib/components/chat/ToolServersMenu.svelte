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
		width: min(420px, calc(100vw - 24px));
		border: 1px solid var(--buddy-action-edge, rgba(255, 255, 255, 0.7));
		border-radius: 30px;
		background: var(--buddy-action-surface, rgba(245, 249, 246, 0.94));
		color: var(--buddy-action-text, #263d2e);
		box-shadow: 0 18px 48px rgba(17, 35, 24, 0.2);
		-webkit-backdrop-filter: blur(28px) saturate(150%);
		backdrop-filter: blur(28px) saturate(150%);
		overflow: hidden;
		overscroll-behavior: contain;
	}

	:global(.dark .buddy-available-tools-menu) {
		border-color: var(--buddy-action-edge, rgba(232, 245, 235, 0.16));
		background: var(--buddy-action-surface, rgba(49, 57, 52, 0.94));
		color: var(--buddy-action-text, #f0f5f1);
		box-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
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
