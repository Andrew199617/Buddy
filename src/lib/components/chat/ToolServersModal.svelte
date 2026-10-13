<script lang="ts">
	import Modal from '../common/Modal.svelte';
	import ToolServersContent from './ToolServersContent.svelte';

	export let show = false;
	export let selectedToolIds = [];
</script>

<Modal bind:show size="md" className="buddy-available-tools-dialog">
	<ToolServersContent
		{selectedToolIds}
		onClose={() => {
			show = false;
		}}
	/>
</Modal>

<style>
	:global(.buddy-available-tools-dialog) {
		max-height: min(48rem, calc(100dvh - 40px));
		border-radius: 28px;
		background: #f5f9f6;
		overflow: hidden;
	}

	:global(.dark .buddy-available-tools-dialog) {
		background: #313934;
	}
</style>
