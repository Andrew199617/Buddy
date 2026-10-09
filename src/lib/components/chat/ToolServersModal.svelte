<script lang="ts">
	import Modal from '../common/Modal.svelte';
	import ToolServersContent from './ToolServersContent.svelte';

	export let show = false;
	export let selectedToolIds = [];
</script>

<Modal bind:show size="md">
	<ToolServersContent
		{selectedToolIds}
		onClose={() => {
			show = false;
		}}
	/>
</Modal>
