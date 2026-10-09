<script lang="ts" context="module">
	import { createLazyComponent } from '$lib/utils/lazy-component';

	const importCodeBlock = () => import('./CodeBlock.svelte');
	const codeBlockFeature = createLazyComponent(importCodeBlock);
</script>

<script lang="ts">
	import { onMount } from 'svelte';
	import LazyFeatureStatus from '$lib/components/common/LazyFeatureStatus.svelte';

	export let code = '';
	export let collapsed = false;

	onMount(() => {
		void codeBlockFeature.load();
	});
</script>

{#if $codeBlockFeature.component}
	<svelte:component this={$codeBlockFeature.component} {code} {collapsed} {...$$restProps} />
{:else}
	{#if !collapsed}
		<pre class="overflow-x-auto whitespace-pre-wrap p-4 text-sm"><code>{code}</code></pre>
	{/if}
	<LazyFeatureStatus
		feature="Code tools"
		compact={true}
		error={$codeBlockFeature.error}
		onRetry={codeBlockFeature.load}
	/>
{/if}
