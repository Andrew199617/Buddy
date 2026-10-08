<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import Spinner from './Spinner.svelte';

	const i18n: Writable<I18n> = getContext('i18n');
	export let feature: string;
	export let error = false;
	export let compact = false;
	export let onRetry: () => void | Promise<void>;
	export let onClose: (() => void) | null = null;

	function reloadBuddy() {
		window.location.reload();
	}
</script>

<div class="lazy-feature-status" class:compact aria-busy={!error}>
	{#if error}
		<p role="alert">
			{$i18n.t('Could not load {{feature}}. Please try again.', { feature: feature })}
		</p>
		<p class="lazy-feature-guidance">
			{$i18n.t('If retry does not help, reload Buddy to finish loading this feature.')}
		</p>
	{:else}
		<div aria-hidden="true"><Spinner className="size-5" /></div>
		<p role="status">{$i18n.t('Loading {{feature}}…', { feature: feature })}</p>
	{/if}
	{#if error || onClose}
		<div class="lazy-feature-actions">
			{#if error}
				<button type="button" class="lazy-feature-retry" on:click={onRetry}>
					{$i18n.t('Retry')}
				</button>
				<button type="button" on:click={reloadBuddy}>{$i18n.t('Reload Buddy')}</button>
			{/if}
			{#if onClose}
				<button type="button" on:click={onClose}>{$i18n.t('Close')}</button>
			{/if}
		</div>
	{/if}
</div>

<style>
	.lazy-feature-status {
		display: flex;
		width: 100%;
		min-height: 12rem;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 14px;
		padding: 24px;
		color: var(--buddy-soft-text, #788579);
		text-align: center;
	}
	.lazy-feature-status.compact {
		min-height: 0;
		gap: 8px;
		padding: 12px;
	}
	.lazy-feature-status p {
		font-size: 14px;
		line-height: 1.5;
	}
	.lazy-feature-status .lazy-feature-guidance {
		font-size: 12px;
	}
	.lazy-feature-actions {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: 8px;
	}
	.lazy-feature-actions button {
		min-width: 72px;
		min-height: 44px;
		padding: 8px 16px;
		border: 1px solid var(--buddy-edge, rgb(39 99 75 / 12%));
		border-radius: 999px;
		background: var(--buddy-panel, #fff);
		color: inherit;
		font-size: 14px;
		cursor: pointer;
	}
	.lazy-feature-actions .lazy-feature-retry {
		background: #27634b;
		color: #fff;
	}
	.lazy-feature-actions button:focus-visible {
		outline: 2px solid #76a98b;
		outline-offset: 3px;
	}
</style>
