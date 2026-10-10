<script lang="ts">
	import { resolveLocalizedResource } from '$lib/utils/localizedContent';
	import { getContext } from 'svelte';
	import { toolServers, tools } from '$lib/stores';

	import Collapsible from '../common/Collapsible.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	export let onClose: () => void = () => {};
	export let selectedToolIds = [];
	export let preserveComposerFocus = false;

	let selectedTools = [];

	$: selectedTools = ($tools ?? []).filter((tool) => selectedToolIds.includes(tool.id));

	const i18n = getContext('i18n');

	function handleContentPointerDown(event: PointerEvent): void {
		if (!preserveComposerFocus) {
			return;
		}

		event.stopPropagation();

		if (!event.isPrimary || event.button !== 0) {
			return;
		}

		const activeElement = document.activeElement;
		if (!(activeElement instanceof HTMLElement) || !activeElement.closest('#chat-input')) {
			return;
		}

		const target = event.target;
		if (!(target instanceof Element)) {
			return;
		}

		if (!target.closest('input, textarea, select, [contenteditable="true"]')) {
			event.preventDefault();
		}
	}

	function authStatus(tool) {
		if (tool?.authenticated === false) {
			return {
				label: $i18n.t('Auth required'),
				dot: 'bg-amber-500',
				pill: 'text-amber-700 dark:text-amber-300'
			};
		}

		if (tool?.authenticated === true) {
			return {
				label: $i18n.t('Connected'),
				dot: 'bg-green-500',
				pill: 'text-green-700 dark:text-green-300'
			};
		}

		return null;
	}
</script>

<div
	class="available-tools-content"
	role="presentation"
	data-composer-menu={preserveComposerFocus ? 'available-tools' : undefined}
	on:pointerdown={handleContentPointerDown}
>
	<div class="available-tools-header">
		<h2>{$i18n.t('Available Tools')}</h2>
		<button
			type="button"
			class="available-tools-close"
			aria-label={$i18n.t('Close')}
			on:click={onClose}
		>
			<XMark className={'size-5'} />
		</button>
	</div>

	<div class="available-tools-body">
		{#if selectedTools.length > 0}
			{#if $toolServers.length > 0}
				<h3 class="available-tools-section-title">{$i18n.t('Tools')}</h3>
			{/if}

			<div class="available-tools-list">
				{#each selectedTools as tool}
					{@const status = authStatus(tool)}
					{@const toolSpecs = tool?.specs ?? []}
					<Collapsible
						className="available-tools-entry"
						buttonClassName="available-tools-row"
						chevronClassName="size-4 shrink-0"
						chevron={toolSpecs.length > 0}
						disabled={toolSpecs.length === 0}
					>
						<div class="min-w-0 flex-1">
							<div class="available-tools-name">
								{resolveLocalizedResource(tool, $i18n.language)}
							</div>
							<div class="available-tools-meta">
								{#if status}
									<span class="inline-flex items-center gap-1.5 {status.pill}">
										<span class="size-1.5 rounded-full {status.dot} shrink-0"></span>
										{status.label}
									</span>
								{/if}
								{#if toolSpecs.length > 0}
									<span>
										{toolSpecs.length}
									</span>
								{/if}
							</div>

							{#if resolveLocalizedResource(tool, $i18n.language, 'description')}
								<div class="available-tools-description">
									{resolveLocalizedResource(tool, $i18n.language, 'description')}
								</div>
							{/if}
						</div>

						<div slot="content" class="available-tools-functions">
							{#if toolSpecs.length > 0}
								{#each toolSpecs as toolSpec}
									<div class="available-tools-function">
										<div class="available-tools-function-name">
											{toolSpec?.name ?? toolSpec?.function?.name}
										</div>
										{#if toolSpec?.description ?? toolSpec?.function?.description}
											<div class="available-tools-description">
												{toolSpec?.description ?? toolSpec?.function?.description}
											</div>
										{/if}
									</div>
								{/each}
							{/if}
						</div>
					</Collapsible>
				{/each}
			</div>
		{/if}

		{#if $toolServers.length > 0}
			<h3 class="available-tools-section-title">{$i18n.t('Tool Servers')}</h3>

			<div class="available-tools-server-info available-tools-description">
				<!-- LICENSE covers this Open WebUI wordmark.
				Do not alter, remove, obscure, or replace it except as LICENSE permits:
				https://docs.openwebui.com/license. -->
				{$i18n.t('Buddy can use tools provided by any OpenAPI server.')} <br /><a
					class="underline"
					href="https://github.com/open-webui/openapi-servers"
					target="_blank">{$i18n.t('Learn more about OpenAPI tool servers.')}</a
				>
			</div>
			<div class="available-tools-list">
				{#each $toolServers as toolServer}
					<Collapsible
						className="available-tools-entry"
						buttonClassName="available-tools-row"
						chevronClassName="size-4 shrink-0"
						chevron
					>
						<div class="min-w-0 flex-1">
							<div class="available-tools-name">
								{toolServer?.openapi?.info?.title} - v{toolServer?.openapi?.info?.version}
							</div>

							<div class="available-tools-description">
								{toolServer?.openapi?.info?.description}
							</div>

							<div class="available-tools-description">
								{toolServer?.url}
							</div>
						</div>

						<div slot="content" class="available-tools-functions">
							{#each toolServer?.specs ?? [] as tool_spec}
								<div class="available-tools-function">
									<div class="available-tools-function-name">
										{tool_spec?.name}
									</div>

									<div class="available-tools-description">
										{tool_spec?.description}
									</div>
								</div>
							{/each}
						</div>
					</Collapsible>
				{/each}
			</div>
		{/if}
	</div>
</div>

<style>
	.available-tools-content {
		--tools-text: #263d2e;
		--tools-muted: #607267;
		--tools-edge: rgb(52 91 63 / 12%);
		--tools-fill: rgb(52 91 63 / 4%);
		--tools-hover: rgb(52 91 63 / 9%);
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
		max-height: inherit;
		color: var(--tools-text);
	}

	:global(.dark) .available-tools-content {
		--tools-text: #f0f5f1;
		--tools-muted: #a9b7ad;
		--tools-edge: rgb(232 245 235 / 14%);
		--tools-fill: rgb(232 245 235 / 4%);
		--tools-hover: rgb(232 245 235 / 9%);
	}

	.available-tools-header {
		display: flex;
		flex-shrink: 0;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 12px 12px 12px 20px;
		border-bottom: 1px solid var(--tools-edge);
	}

	.available-tools-header h2 {
		margin: 0;
		font-size: 17px;
		font-weight: 600;
		line-height: 1.4;
	}

	.available-tools-close {
		display: flex;
		width: 44px;
		height: 44px;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 50%;
		color: var(--tools-muted);
	}

	.available-tools-close:hover,
	.available-tools-close:focus-visible,
	.available-tools-content :global(.available-tools-row:not([aria-disabled='true']):hover),
	.available-tools-content :global(.available-tools-row:focus-visible) {
		background: var(--tools-hover);
		color: var(--tools-text);
	}

	.available-tools-close:focus-visible,
	.available-tools-content :global(.available-tools-row:focus-visible) {
		outline: 2px solid #679c75;
		outline-offset: -2px;
	}

	.available-tools-body {
		min-height: 0;
		overflow-y: auto;
		overflow-x: hidden;
		overscroll-behavior: contain;
		padding: 16px;
		scrollbar-width: thin;
	}

	.available-tools-list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.available-tools-content :global(.available-tools-entry) {
		min-width: 0;
		border: 1px solid var(--tools-edge);
		border-radius: 16px;
		background: var(--tools-fill);
	}

	.available-tools-content :global(.available-tools-row) {
		width: 100%;
		min-height: 48px;
		padding: 12px;
		border-radius: 16px;
	}

	.available-tools-name,
	.available-tools-function-name {
		font-size: 14px;
		font-weight: 500;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.available-tools-meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 8px;
		margin-top: 4px;
		font-size: 12px;
		line-height: 1.5;
		color: var(--tools-muted);
	}

	.available-tools-description {
		margin-top: 4px;
		font-size: 13px;
		line-height: 1.6;
		color: var(--tools-muted);
		overflow-wrap: anywhere;
		white-space: normal;
	}

	.available-tools-functions {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin: 0 12px 12px;
		padding: 12px 0 0;
		border-top: 1px solid var(--tools-edge);
	}

	.available-tools-function {
		min-width: 0;
	}

	.available-tools-section-title {
		margin: 20px 0 12px;
		font-size: 14px;
		font-weight: 600;
	}

	.available-tools-section-title:first-child {
		margin-top: 0;
	}

	.available-tools-server-info {
		margin-bottom: 12px;
	}

	@media (max-width: 767px) {
		.available-tools-header {
			padding: 8px 8px 8px 16px;
		}

		.available-tools-body {
			padding: 12px;
		}

		.available-tools-name,
		.available-tools-function-name {
			font-size: 15px;
		}

		.available-tools-description {
			font-size: 14px;
		}
	}
</style>
