<script lang="ts">
	import { getContext } from 'svelte';
	import BuddyBrand from '$lib/components/common/BuddyBrand.svelte';
	import { WEBUI_NAME } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';

	const i18n: any = getContext('i18n');

	export let show = true;
	export let getStartedHandler = () => {};
</script>

{#if show}
	<div class="buddy-welcome fixed inset-0 z-[100] min-h-screen w-full overflow-y-auto">
		<div class="relative z-10 flex min-h-screen flex-col px-6 py-8 sm:px-12 sm:py-10 lg:px-20">
			<header>
				<BuddyBrand name={$WEBUI_NAME} className="text-2xl" markClass="size-10" />
			</header>

			<main class="flex flex-1 items-center gap-12 py-16">
				<div class="max-w-2xl">
					<p class="buddy-welcome-eyebrow mb-5 text-sm font-medium">
						{$i18n.t('Your AI companion')}
					</p>
					<h1
						class="m-0 text-4xl leading-[1.08] font-medium tracking-tight sm:text-6xl lg:text-7xl"
					>
						{$i18n.t('A little help. A lot of possibility.')}
					</h1>
					<p class="mt-6 max-w-lg text-base leading-relaxed sm:text-lg">
						{$i18n.t(
							'Meet Buddy, your AI companion for ideas, answers, and getting things done. Bring your favorite models and make yourself at home.'
						)}
					</p>
					<button
						class="buddy-primary-button mt-9 inline-flex items-center justify-center gap-3 rounded-full px-7 py-3.5 text-sm font-medium transition"
						on:click={getStartedHandler}
					>
						{$i18n.t('Get started')}
						<svg
							class="size-4"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.8"
							aria-hidden="true"
						>
							<path d="m14 7 5 5-5 5M5 12h14" stroke-linecap="round" stroke-linejoin="round" />
						</svg>
					</button>
				</div>

				<div class="buddy-welcome-art hidden flex-1 justify-center lg:flex" aria-hidden="true">
					<img src="{WEBUI_BASE_URL}/static/buddy-mark.svg" class="w-full max-w-96" alt="" />
				</div>
			</main>

			<footer class="text-sm opacity-70">{$i18n.t('Your ideas, with a Buddy.')}</footer>
		</div>
	</div>
{/if}
