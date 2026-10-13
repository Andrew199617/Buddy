<script lang="ts">
	import { goto } from '$app/navigation';
	import { config, user } from '$lib/stores';
	import { onMount } from 'svelte';
	import { getWorkspaceLandingDestination } from '$lib/components/buddy/navigation';

	onMount(() => {
		const destination = getWorkspaceLandingDestination($user, $config?.features);
		goto(destination ?? '/', { replaceState: true });
	});
</script>
