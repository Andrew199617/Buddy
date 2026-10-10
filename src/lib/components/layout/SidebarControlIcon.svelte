<script lang="ts">
	export let expanded = false;
	export let className = 'size-6';
</script>

<!-- The drawer stays on the physical left edge in both LTR and RTL layouts. -->
<svg
	aria-hidden="true"
	xmlns="http://www.w3.org/2000/svg"
	fill="none"
	viewBox="0 0 24 24"
	stroke-width="1.5"
	stroke="currentColor"
	stroke-linecap="round"
	stroke-linejoin="round"
	class={className}
>
	<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2Z" />
	<path d="M9.5 21V3" />
	{#if expanded}
		<path d="M7.25 10L5.5 12L7.25 14" />
	{:else}
		<path d="M5.5 10L7.25 12L5.5 14" />
	{/if}
</svg>
