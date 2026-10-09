<script lang="ts">
	import { APP_NAME, WEBUI_BASE_URL } from '$lib/constants';

	export let name = APP_NAME;
	export let showName = true;
	export let className = '';
	export let markClass = 'size-9';
</script>

<span class="buddy-brand inline-flex items-center gap-2.5 {className}">
	<img
		src="{WEBUI_BASE_URL}/static/buddy-mark.svg"
		class="shrink-0 {markClass}"
		alt={showName ? '' : name}
		draggable="false"
	/>
	{#if showName}
		<span class="buddy-wordmark">{name}</span>
	{/if}
</span>
