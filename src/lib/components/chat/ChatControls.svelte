<script context="module" lang="ts">
	let savedTab: 'controls' | 'files' | 'overview' = 'controls';
</script>

<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { createLazyComponent } from '$lib/utils/lazy-component';
	import LazyFeatureStatus from '$lib/components/common/LazyFeatureStatus.svelte';
	import {
		config,
		terminalServers,
		showControls,
		showCallOverlay,
		showArtifacts,
		showEmbeds,
		settings,
		showFileNavPath,
		selectedTerminalId,
		user
	} from '$lib/stores';

	import Controls from './Controls/Controls.svelte';
	import Drawer from '../common/Drawer.svelte';
	import ResizableSidePanel from '../common/ResizableSidePanel.svelte';
	import { isSavedChatId } from '$lib/utils/chatId';

	const i18n: Writable<I18n> = getContext('i18n');

	function importCallOverlay() {
		return import('./MessageInput/CallOverlay.svelte');
	}
	function importArtifacts() {
		return import('./Artifacts.svelte');
	}
	function importEmbeds() {
		return import('./ChatControls/Embeds.svelte');
	}
	function importFileNav() {
		return import('./FileNav.svelte');
	}
	function importPyodideFileNav() {
		return import('./PyodideFileNav.svelte');
	}
	function importOverview() {
		return import('./Overview.svelte');
	}

	const callOverlayFeature = createLazyComponent(importCallOverlay);
	const artifactsFeature = createLazyComponent(importArtifacts);
	const embedsFeature = createLazyComponent(importEmbeds);
	const fileNavFeature = createLazyComponent(importFileNav);
	const pyodideFileNavFeature = createLazyComponent(importPyodideFileNav);
	const overviewFeature = createLazyComponent(importOverview);

	export let history;
	export let models = [];

	export let chatId = null;
	export let chatUser = null;

	export let chatFiles = [];
	export let params = {};

	export let eventTarget: EventTarget;
	export let submitPrompt: Function;
	export let stopResponse: Function;
	export let showMessage: Function;
	export let files;
	export let modelId;

	export let codeInterpreterEnabled = false;

	let largeScreen = false;
	let dragged = false;
	let mounted = false;
	let controlsWidth = 350;

	// Tab state for Controls+Files panel
	let activeTab = savedTab;
	// svelte-ignore reactive_declaration_module_script_dependency
	$: {
		savedTab = activeTab;
	}

	$: hasMessages = history?.messages && Object.keys(history.messages).length > 0;

	$: showControlsTab = $user?.role === 'admin' || ($user?.permissions?.chat?.controls ?? true);
	const chatContext = (terminal: any) => terminal?.contexts?.chat ?? {};
	const chatContextAvailable = (terminal: any) => chatContext(terminal) !== false;
	const chatContextNeedsSavedChat = (terminal: any) =>
		chatContext(terminal)?.context_id === 'chat_id';
	$: selectedSystemTerminal = ($terminalServers ?? []).find(
		(t) => t.id && t.id === $selectedTerminalId
	);
	$: selectedSystemTerminalAvailable =
		selectedSystemTerminal &&
		chatContextAvailable(selectedSystemTerminal) &&
		!(chatContextNeedsSavedChat(selectedSystemTerminal) && !isSavedChatId(chatId));
	$: terminalFilesAvailable = !!(
		$selectedTerminalId &&
		(selectedSystemTerminalAvailable ||
			(!selectedSystemTerminal &&
				($user?.role === 'admin' || ($user?.permissions?.features?.direct_tool_servers ?? true))))
	);
	$: showFilesTab =
		terminalFilesAvailable ||
		(codeInterpreterEnabled && $config?.code?.interpreter_engine !== 'jupyter');
	$: showOverviewTab = hasMessages;

	// Tab fallback: if active tab becomes hidden, switch to next available
	$: if (!showOverviewTab && activeTab === 'overview') activeTab = 'controls';
	$: if (!showFilesTab && activeTab === 'files') activeTab = 'controls';
	$: if (!showControlsTab && activeTab === 'controls') {
		if (showFilesTab) activeTab = 'files';
		else if (showOverviewTab) activeTab = 'overview';
	}

	// Auto-close if there are no visible tabs
	$: if (!showControlsTab && !showFilesTab && !showOverviewTab) {
		showControls.set(false);
	}

	// Auto-switch to Files tab when display_file is triggered
	$: if ($showFileNavPath && terminalFilesAvailable) {
		activeTab = 'files';
		showControls.set(true);
	}

	// Keep Files selected when a terminal is active; opening the panel is handled by selection UI.
	$: if ($selectedTerminalId && terminalFilesAvailable) {
		activeTab = 'files';
	}

	// Clear selected direct terminal if user lost permission
	$: if (
		$selectedTerminalId &&
		$terminalServers !== null &&
		!($terminalServers ?? []).some((t) => t.id && t.id === $selectedTerminalId) &&
		!($user?.role === 'admin' || ($user?.permissions?.features?.direct_tool_servers ?? true))
	) {
		selectedTerminalId.set(null);
	}

	const handleMediaQuery = async (e) => {
		if (e.matches) {
			largeScreen = true;
			if ($showCallOverlay) {
				showCallOverlay.set(false);
				await tick();
				showCallOverlay.set(true);
			}
		} else {
			largeScreen = false;
			if ($showCallOverlay) {
				showCallOverlay.set(false);
				await tick();
				showCallOverlay.set(true);
			}
		}
	};

	const onMouseDown = () => {
		dragged = true;
	};
	const onMouseUp = () => {
		dragged = false;
	};

	onMount(() => {
		const mediaQuery = window.matchMedia('(min-width: 1024px)');
		mediaQuery.addEventListener('change', handleMediaQuery);
		handleMediaQuery(mediaQuery);

		let isDestroyed = false;

		const init = async () => {
			await tick();

			if (isDestroyed) return;

			setTimeout(() => {
				mounted = true;
			}, 0);
		};
		init();

		document.addEventListener('mousedown', onMouseDown);
		document.addEventListener('mouseup', onMouseUp);

		return () => {
			isDestroyed = true;
			mounted = false;
			if (!largeScreen) {
				showControls.set(false);
			}
			mediaQuery.removeEventListener('change', handleMediaQuery);
			document.removeEventListener('mousedown', onMouseDown);
			document.removeEventListener('mouseup', onMouseUp);
		};
	});

	const closeHandler = () => {
		if (!largeScreen) {
			showControls.set(false);
		}
		showArtifacts.set(false);
		showEmbeds.set(false);
		if ($showCallOverlay) showCallOverlay.set(false);
	};

	$: if (mounted && !chatId) closeHandler();

	// Helper: is a "special" full-screen panel active?
	$: specialPanel = $showCallOverlay || $showArtifacts || $showEmbeds;

	// Import only the feature that the currently visible panel needs.
	$: if ($showControls) {
		if ($showCallOverlay) {
			void callOverlayFeature.load();
		} else if ($showEmbeds) {
			void embedsFeature.load();
		} else if ($showArtifacts) {
			void artifactsFeature.load();
		} else if (activeTab === 'overview') {
			void overviewFeature.load();
		} else if (activeTab === 'files' && terminalFilesAvailable && $selectedTerminalId) {
			void fileNavFeature.load();
		} else if (activeTab === 'files' && codeInterpreterEnabled) {
			void pyodideFileNavFeature.load();
		}
	}

	function closeFeaturePanel() {
		showControls.set(false);
	}
</script>

{#if !largeScreen}
	{#if $showControls}
		<Drawer
			show={$showControls}
			onClose={() => showControls.set(false)}
			className="min-h-[100dvh] !bg-white dark:!bg-gray-850"
		>
			<div class="h-[100dvh] flex flex-col">
				{#if $showCallOverlay}
					<div
						class="h-full max-h-[100dvh] bg-white text-gray-700 dark:bg-black dark:text-gray-300 flex justify-center"
					>
						{#if $callOverlayFeature.component}
							<svelte:component
								this={$callOverlayFeature.component}
								bind:files
								{submitPrompt}
								{stopResponse}
								{modelId}
								{chatId}
								{eventTarget}
								on:close={() => showControls.set(false)}
							/>
						{:else}
							<LazyFeatureStatus
								feature={$i18n.t('Call')}
								error={$callOverlayFeature.error}
								onRetry={callOverlayFeature.load}
								onClose={closeFeaturePanel}
							/>
						{/if}
					</div>
				{:else if $showEmbeds}
					{#if $embedsFeature.component}
						<svelte:component this={$embedsFeature.component} />
					{:else}
						<LazyFeatureStatus
							feature={$i18n.t('Web')}
							error={$embedsFeature.error}
							onRetry={embedsFeature.load}
							onClose={closeFeaturePanel}
						/>
					{/if}
				{:else if $showArtifacts}
					{#if $artifactsFeature.component}
						<svelte:component this={$artifactsFeature.component} {history} />
					{:else}
						<LazyFeatureStatus
							feature={$i18n.t('Artifacts')}
							error={$artifactsFeature.error}
							onRetry={artifactsFeature.load}
							onClose={closeFeaturePanel}
						/>
					{/if}
				{:else}
					<!-- Controls + Files tabs -->
					<div class="flex flex-col h-full min-h-0">
						<!-- Tab bar -->
						<div class="flex items-center justify-between px-2 pt-2 pb-2 shrink-0">
							<div class="flex gap-1 min-w-0 overflow-x-auto scrollbar-hidden">
								{#if showControlsTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'controls'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'controls')}
									>
										{$i18n.t('Controls')}
									</button>
								{/if}
								{#if showFilesTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'files'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'files')}
									>
										{$i18n.t('Files')}
									</button>
								{/if}
								{#if showOverviewTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'overview'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'overview')}
									>
										{$i18n.t('Overview')}
									</button>
								{/if}
							</div>
							<button
								class="p-1 rounded-lg text-gray-500 dark:text-gray-400"
								on:click={() => showControls.set(false)}
								aria-label={$i18n.t('Close')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.5"
									class="size-4"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
								</svg>
							</button>
						</div>

						<div
							class="flex-1 min-h-0 {activeTab === 'overview'
								? 'h-full'
								: activeTab === 'controls'
									? 'overflow-y-auto px-3 pt-1'
									: ''}"
						>
							{#if activeTab === 'overview'}
								{#if $overviewFeature.component}
									<svelte:component
										this={$overviewFeature.component}
										{history}
										{chatUser}
										onNodeClick={(e) => {
											const node = e.node;
											showMessage(node.data.message, true);
										}}
									/>
								{:else}
									<LazyFeatureStatus
										feature={$i18n.t('Overview')}
										error={$overviewFeature.error}
										onRetry={overviewFeature.load}
										onClose={closeFeaturePanel}
									/>
								{/if}
							{:else if activeTab === 'files' && terminalFilesAvailable && $selectedTerminalId}
								{#if $fileNavFeature.component}
									<svelte:component this={$fileNavFeature.component} {chatId} />
								{:else}
									<LazyFeatureStatus
										feature={$i18n.t('Files')}
										error={$fileNavFeature.error}
										onRetry={fileNavFeature.load}
										onClose={closeFeaturePanel}
									/>
								{/if}
							{:else if activeTab === 'files' && codeInterpreterEnabled}
								{#if $pyodideFileNavFeature.component}
									<svelte:component this={$pyodideFileNavFeature.component} />
								{:else}
									<LazyFeatureStatus
										feature={$i18n.t('Files')}
										error={$pyodideFileNavFeature.error}
										onRetry={pyodideFileNavFeature.load}
										onClose={closeFeaturePanel}
									/>
								{/if}
							{:else}
								<Controls embed={true} {models} bind:chatFiles bind:params />
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</Drawer>
	{/if}
{:else}
	<ResizableSidePanel
		open={$showControls}
		bind:width={controlsWidth}
		minWidth={350}
		minSiblingWidth={360}
		closeOnDragBelowMinWidth
		onClose={() => showControls.set(false)}
		storageKey="chatControlsSize"
		className="h-full z-10 bg-white dark:bg-gray-900"
	>
		<div class="flex h-full max-h-full min-h-full">
			<div
				class="w-full {specialPanel && !$showCallOverlay
					? ' '
					: 'bg-white dark:bg-gray-900'} z-40 pointer-events-auto {activeTab === 'files'
					? ''
					: 'overflow-y-auto'} scrollbar-hidden"
				id="controls-container"
			>
				{#if $showCallOverlay}
					<div class="w-full h-full flex justify-center">
						{#if $callOverlayFeature.component}
							<svelte:component
								this={$callOverlayFeature.component}
								bind:files
								{submitPrompt}
								{stopResponse}
								{modelId}
								{chatId}
								{eventTarget}
								on:close={() => showControls.set(false)}
							/>
						{:else}
							<LazyFeatureStatus
								feature={$i18n.t('Call')}
								error={$callOverlayFeature.error}
								onRetry={callOverlayFeature.load}
								onClose={closeFeaturePanel}
							/>
						{/if}
					</div>
				{:else if $showEmbeds}
					{#if $embedsFeature.component}
						<svelte:component this={$embedsFeature.component} overlay={dragged} />
					{:else}
						<LazyFeatureStatus
							feature={$i18n.t('Web')}
							error={$embedsFeature.error}
							onRetry={embedsFeature.load}
							onClose={closeFeaturePanel}
						/>
					{/if}
				{:else if $showArtifacts}
					{#if $artifactsFeature.component}
						<svelte:component this={$artifactsFeature.component} {history} overlay={dragged} />
					{:else}
						<LazyFeatureStatus
							feature={$i18n.t('Artifacts')}
							error={$artifactsFeature.error}
							onRetry={artifactsFeature.load}
							onClose={closeFeaturePanel}
						/>
					{/if}
				{:else}
					<!-- Controls + Files tabs -->
					<div class="flex flex-col h-full min-h-0">
						<!-- Tab bar -->
						<div class="flex items-center justify-between px-2 pt-2 pb-2 shrink-0">
							<div class="flex gap-1 min-w-0 overflow-x-auto scrollbar-hidden">
								{#if showControlsTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'controls'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'controls')}
									>
										{$i18n.t('Controls')}
									</button>
								{/if}
								{#if showFilesTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'files'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'files')}
									>
										{$i18n.t('Files')}
									</button>
								{/if}
								{#if showOverviewTab}
									<button
										class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab ===
										'overview'
											? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
											: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => (activeTab = 'overview')}
									>
										{$i18n.t('Overview')}
									</button>
								{/if}
							</div>
							<button
								class="p-1 rounded-lg text-gray-500 dark:text-gray-400"
								on:click={() => showControls.set(false)}
								aria-label={$i18n.t('Close')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.5"
									class="size-4"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
								</svg>
							</button>
						</div>

						<div
							class="flex-1 min-h-0 {activeTab === 'overview'
								? 'h-full'
								: activeTab === 'controls'
									? 'overflow-y-auto px-3 pt-1'
									: ''}"
						>
							{#if activeTab === 'overview'}
								{#if $overviewFeature.component}
									<svelte:component
										this={$overviewFeature.component}
										{history}
										{chatUser}
										onNodeClick={(e) => {
											const node = e.node;
											if (node?.data?.message?.favorite) {
												history.messages[node.data.message.id].favorite = true;
											} else {
												history.messages[node.data.message.id].favorite = null;
											}
											showMessage(node.data.message, true);
										}}
									/>
								{:else}
									<LazyFeatureStatus
										feature={$i18n.t('Overview')}
										error={$overviewFeature.error}
										onRetry={overviewFeature.load}
										onClose={closeFeaturePanel}
									/>
								{/if}
							{:else if activeTab === 'files' && terminalFilesAvailable && $selectedTerminalId}
								{#if $fileNavFeature.component}
									<svelte:component this={$fileNavFeature.component} overlay={dragged} {chatId} />
								{:else}
									<LazyFeatureStatus
										feature={$i18n.t('Files')}
										error={$fileNavFeature.error}
										onRetry={fileNavFeature.load}
										onClose={closeFeaturePanel}
									/>
								{/if}
							{:else if activeTab === 'files' && codeInterpreterEnabled}
								{#if $pyodideFileNavFeature.component}
									<svelte:component this={$pyodideFileNavFeature.component} overlay={dragged} />
								{:else}
									<LazyFeatureStatus
										feature={$i18n.t('Files')}
										error={$pyodideFileNavFeature.error}
										onRetry={pyodideFileNavFeature.load}
										onClose={closeFeaturePanel}
									/>
								{/if}
							{:else}
								<Controls embed={true} {models} bind:chatFiles bind:params />
							{/if}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</ResizableSidePanel>
{/if}
