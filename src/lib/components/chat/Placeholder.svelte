<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';

	const dispatch = createEventDispatcher();

	import {
		config,
		user,
		models as _models,
		temporaryChatEnabled,
		selectedFolder
	} from '$lib/stores';
	import { refreshChatList, refreshFolderChatLists } from '$lib/stores/chatList';
	import {
		resolveLocalizedModelPromptSuggestions,
		resolveLocalizedPromptSuggestions
	} from '$lib/utils/localizedContent';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EyeSlash from '$lib/components/icons/EyeSlash.svelte';
	import MessageInput from './MessageInput.svelte';
	import FolderPlaceholder from './Placeholder/FolderPlaceholder.svelte';
	import FolderTitle from './Placeholder/FolderTitle.svelte';

	const i18n: any = getContext('i18n');

	export let createMessagePair: Function;
	export let stopResponse: Function;

	export let autoScroll = false;

	export let atSelectedModel: Model | undefined;
	export let selectedModels: [''];

	export let history;

	export let prompt = '';
	export let files = [];
	export let messageInput = null;

	export let selectedToolIds = [];
	export let selectedSkillIds = [];
	export let selectedFilterIds = [];
	export let pendingOAuthTools = [];

	export let showCommands = false;

	export let imageGenerationEnabled = false;
	export let codeInterpreterEnabled = false;
	export let webSearchEnabled = false;
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: Function = () => {};
	export let oauthRedirectHandler: Function = () => {};

	export let onUpload: Function = (e) => {};
	export let onUpdate: (data?: { file?: any }) => void = () => {};
	export let onSelect = (e) => {};
	export let onChange = (e) => {};
	export let onWebSearchToggle: Function = () => {};
	export let messageQueue: { id: string; prompt: string; files: any[] }[] = [];
	export let onQueueSendNow: (id: string) => void = () => {};
	export let onQueueEdit: (id: string) => void = () => {};
	export let onQueueDelete: (id: string) => void = () => {};
	export let askUser = {
		show: false,
		questions: [],
		allowOther: true,
		timeoutMs: null,
		onConfirm: (_value: any) => {},
		onCancel: () => {}
	};

	export let dragged = false;

	let models = [];
	export let selectedModelIdx = 0;
	let selectedSuggestionPrompts = [];

	$: models = selectedModels.map((id) => $_models.find((m) => m.id === id));
	$: selectedSuggestionPrompts =
		resolveLocalizedModelPromptSuggestions(atSelectedModel, $i18n.language) ??
		resolveLocalizedModelPromptSuggestions(models[selectedModelIdx], $i18n.language) ??
		resolveLocalizedPromptSuggestions(
			$config?.default_prompt_suggestions,
			$config?.default_prompt_suggestions_i18n ?? {},
			$i18n.language,
			(key) => $i18n.t(key)
		);

	// True when viewing a shared folder the current user doesn't own AND lacks write access
	$: folderReadOnly =
		$selectedFolder != null &&
		$selectedFolder.user_id !== $user?.id &&
		!$selectedFolder.write_access;
</script>

<div class="buddy-chat-welcome">
	<div class="buddy-welcome-body">
		{#if $temporaryChatEnabled}
			<Tooltip
				content={$i18n.t("This chat won't appear in history and your messages will not be saved.")}
				placement="top"
			>
				<div class="buddy-welcome-private">
					<EyeSlash strokeWidth="2" className="size-3.5" />{$i18n.t('Temporary Chat')}
				</div>
			</Tooltip>
		{/if}
		{#if $selectedFolder}
			<FolderTitle
				folder={$selectedFolder}
				readOnly={folderReadOnly}
				onUpdate={async () => {
					await Promise.all([refreshChatList(localStorage.token), refreshFolderChatLists(null)]);
				}}
				onDelete={async () => {
					await Promise.all([refreshChatList(localStorage.token), refreshFolderChatLists(null)]);
					selectedFolder.set(null);
				}}
			/>
			<FolderPlaceholder folder={$selectedFolder} />
		{:else}
			<div class="buddy-welcome-greeting">
				<p class="buddy-welcome-eyebrow">{$i18n.t('Good to see you.')}</p>
				<h1>{$i18n.t("What's on your mind?")}</h1>
				<p>{$i18n.t("I'm here for the big ideas and the little things.")}</p>
			</div>
			{#if selectedSuggestionPrompts.length > 0 && !prompt}
				<div class="buddy-welcome-suggestions">
					{#each selectedSuggestionPrompts.slice(0, 3) as suggestion}
						<button
							type="button"
							class="buddy-prompt-chip"
							on:click={() => onSelect({ type: 'prompt', data: suggestion.content })}
						>
							<span>{suggestion.title?.[0] || suggestion.content}</span>
							<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"
								><path
									d="M4 12 12 4M4 4h8v8"
									stroke="currentColor"
									stroke-width="1.3"
									stroke-linecap="round"
									stroke-linejoin="round"
								/></svg
							>
						</button>
					{/each}
				</div>
			{/if}
		{/if}
	</div>
	{#if !($selectedFolder && folderReadOnly)}
		<div class="buddy-welcome-composer">
			<MessageInput
				bind:this={messageInput}
				{history}
				bind:selectedModels
				bind:files
				bind:prompt
				bind:autoScroll
				bind:selectedToolIds
				bind:selectedSkillIds
				bind:selectedFilterIds
				bind:imageGenerationEnabled
				bind:codeInterpreterEnabled
				bind:webSearchEnabled
				bind:atSelectedModel
				bind:showCommands
				bind:dragged
				{pendingOAuthTools}
				{oauthRedirectHandler}
				{toolApprovalMode}
				{onToolApprovalModeChange}
				{stopResponse}
				{createMessagePair}
				placeholder={$i18n.t('Message Buddy…')}
				{onChange}
				{onUpload}
				{onUpdate}
				{messageQueue}
				{onQueueSendNow}
				{onQueueEdit}
				{onQueueDelete}
				{askUser}
				{onWebSearchToggle}
				on:chatVariables
				on:submit={(e) => {
					dispatch('submit', e.detail);
				}}
			/>
		</div>
	{/if}
</div>
