<script lang="ts">
	import SidebarToggleTooltip from '$lib/components/layout/SidebarToggleTooltip.svelte';
	import { getContext } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		WEBUI_NAME,
		banners,
		chatId,
		config,
		mobile,
		settings,
		showControls,
		showSidebar,
		temporaryChatEnabled,
		user
	} from '$lib/stores';
	import BuddyAvatar from '$lib/components/buddy/BuddyAvatar.svelte';
	import { mobileNotifications, getMobileNotificationPriority } from '$lib/notifications/mobile';
	import ShareChatModal from '../chat/ShareChatModal.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Menu from '$lib/components/layout/Navbar/Menu.svelte';
	import Banner from '../common/Banner.svelte';
	import ChatBubbleDotted from '../icons/ChatBubbleDotted.svelte';
	import ChatBubbleDottedChecked from '../icons/ChatBubbleDottedChecked.svelte';
	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import ChatPlus from '../icons/ChatPlus.svelte';
	import Knobs from '../icons/Knobs.svelte';
	import { isTemporaryChatId } from '$lib/utils/chatId';

	const i18n: any = getContext('i18n');

	export let initNewChat: Function;
	export let readOnly = false;
	export let shareEnabled = false;
	export let scrollTop = 0;
	export let scrollToTop: (() => void) | null = null;
	export let buddyState: 'idle' | 'thinking' | 'responding' = 'idle';
	export let chat: any;
	export let history;
	export let title = '';
	export let onSaveTempChat: () => void | Promise<void>;
	export let archiveChatHandler: (id: string) => void;
	export let deleteChatHandler: (id: string) => void;
	export let moveChatHandler: (id: string, folderId: string) => void;

	$: infoNotificationCount = $mobileNotifications.filter(
		(notification) => getMobileNotificationPriority(notification) === 'info'
	).length;

	let closedBannerIds: string[] = [];
	let showShareChatModal = false;
	let canToggleTemporaryChat = true;
	let canUseControls = true;
	let canDeleteChat = true;
	let avatarStatus = '';
	let saveTemporaryChatHandler: (() => void | Promise<void>) | null = null;

	$: {
		canToggleTemporaryChat = true;
		if ($user?.role === 'user') {
			canToggleTemporaryChat =
				($user?.permissions?.chat?.temporary ?? true) &&
				!($user?.permissions?.chat?.temporary_enforced ?? false);
		}
		canUseControls = $user?.role === 'admin' || ($user?.permissions?.chat?.controls ?? true);
		canDeleteChat = $user?.role === 'admin' || ($user?.permissions?.chat?.delete ?? true);
	}

	$: {
		saveTemporaryChatHandler = null;
		if (!readOnly && canToggleTemporaryChat && chat?.id && $temporaryChatEnabled) {
			saveTemporaryChatHandler = onSaveTempChat;
		}
	}

	$: {
		avatarStatus = '';
		if (buddyState === 'thinking') {
			avatarStatus = $i18n.t('Thinking…');
		} else if (buddyState === 'responding') {
			avatarStatus = $i18n.t('Responding');
		}
	}

	const getDismissedBannerIds = (): string[] => {
		try {
			return JSON.parse(localStorage.getItem('dismissedBannerIds') ?? '[]');
		} catch {
			return [];
		}
	};

	function toggleSidebar() {
		showSidebar.set(!$showSidebar);
	}

	function startNewChat() {
		initNewChat();
	}

	function toggleControls() {
		showControls.set(!$showControls);
	}

	async function toggleTemporaryChat() {
		if (($settings?.temporaryChatByDefault ?? false) && $temporaryChatEnabled) {
			await temporaryChatEnabled.set(null);
		} else {
			await temporaryChatEnabled.set(!$temporaryChatEnabled);
		}
		if ($page.url.pathname !== '/') {
			await goto('/');
		}
		if ($temporaryChatEnabled) {
			window.history.replaceState(window.history.state, '', '?temporary-chat=true');
		} else {
			window.history.replaceState(window.history.state, '', location.pathname);
		}
	}

	function shareCurrentChat() {
		showShareChatModal = !showShareChatModal;
	}

	function archiveCurrentChat() {
		archiveChatHandler(chat.id);
	}

	function deleteCurrentChat() {
		deleteChatHandler(chat.id);
	}
</script>

<ShareChatModal bind:show={showShareChatModal} chatId={$chatId} />

<button id="new-chat-button" class="hidden" on:click={startNewChat} aria-label={$i18n.t('New Chat')}
></button>
{#if chat?.id && !readOnly && !$temporaryChatEnabled && canDeleteChat}
	<button
		id="delete-chat-button"
		class="hidden"
		on:click={deleteCurrentChat}
		aria-label={$i18n.t('Delete')}
	></button>
{/if}

<nav
	class="buddy-chat-header drag-region"
	class:buddy-header-scrolled={scrollTop > 8}
	aria-label={$i18n.t('Chat actions')}
>
	<div id="navbar-bg-gradient-to-b" class="buddy-header-fade" aria-hidden="true"></div>

	<div class="buddy-header-left">
		<SidebarToggleTooltip>
			<button
				type="button"
				id="sidebar-toggle-button"
				aria-controls="sidebar"
				class="buddy-circle-button no-drag"
				on:click={toggleSidebar}
				aria-expanded={$showSidebar}
				aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
				aria-describedby={$mobile && infoNotificationCount > 0
					? 'buddy-sidebar-notification-status'
					: undefined}
			>
				{#if $mobile && infoNotificationCount > 0}
					<span class="buddy-notification-dot" aria-hidden="true"></span>
					<span id="buddy-sidebar-notification-status" class="sr-only">
						{$i18n.t('{{count}} notifications need your attention', {
							count: infoNotificationCount
						})}
					</span>
				{/if}
				<svg
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					stroke-width="1.6"
					aria-hidden="true"
				>
					<path d="M5 8H19M5 16H19" stroke-linecap="round" />
				</svg>
			</button>
		</SidebarToggleTooltip>
		<Tooltip content={$i18n.t('New Chat')}>
			<button
				type="button"
				class="buddy-quiet-button no-drag"
				on:click={startNewChat}
				aria-label={$i18n.t('New Chat')}
			>
				<ChatPlus className="size-[18px]" strokeWidth="1.5" />
			</button>
		</Tooltip>
	</div>

	<div class="buddy-header-center" title={title || chat?.chat?.title || $WEBUI_NAME}>
		<BuddyAvatar state={buddyState} size={60} decorative={true} />
		<div class="buddy-name-pill"><span>{$WEBUI_NAME}</span></div>
		<span class="sr-only" role="status" aria-live="polite">{avatarStatus}</span>
	</div>

	<div class="buddy-header-right">
		{#if shareEnabled && chat && (chat.id || $temporaryChatEnabled)}
			<Menu
				{chat}
				{shareEnabled}
				{readOnly}
				{scrollToTop}
				{saveTemporaryChatHandler}
				shareHandler={shareCurrentChat}
				archiveChatHandler={archiveCurrentChat}
				deleteChatHandler={deleteCurrentChat}
				{moveChatHandler}
			>
				<button
					type="button"
					id="chat-context-menu-button"
					class="buddy-circle-button buddy-actions-button no-drag"
					aria-label={$i18n.t('Chat actions')}
				>
					<EllipsisHorizontal className="size-5" strokeWidth="1.6" />
				</button>
			</Menu>
		{/if}
		<div class="buddy-secondary-controls">
			{#if !readOnly && canToggleTemporaryChat}
				{#if !chat?.id}
					<Tooltip content={$i18n.t('Temporary Chat')}>
						<button
							type="button"
							id="temporary-chat-button"
							class="buddy-quiet-button no-drag"
							class:buddy-control-active={$temporaryChatEnabled}
							on:click={toggleTemporaryChat}
							aria-pressed={$temporaryChatEnabled ?? false}
							aria-label={$i18n.t('Temporary Chat')}
						>
							{#if $temporaryChatEnabled}
								<ChatBubbleDottedChecked className="size-[18px]" strokeWidth="1.5" />
							{:else}
								<ChatBubbleDotted className="size-[18px]" strokeWidth="1.5" />
							{/if}
						</button>
					</Tooltip>
				{/if}
			{/if}
			{#if canUseControls}
				<Tooltip content={$i18n.t('Controls')}>
					<button
						type="button"
						class="buddy-quiet-button no-drag"
						class:buddy-control-active={$showControls}
						on:click={toggleControls}
						aria-pressed={$showControls}
						aria-label={$i18n.t('Controls')}
					>
						<Knobs className="size-[18px]" strokeWidth="1.3" />
					</button>
				</Tooltip>
			{/if}
		</div>
	</div>

	{#if $temporaryChatEnabled && isTemporaryChatId($chatId)}
		<div class="buddy-temporary-note">{$i18n.t('Temporary Chat')}</div>
	{/if}
	<div class="buddy-header-banners absolute top-[100%] left-0 right-0 h-fit">
		{#if !history.currentId && !$chatId && ($banners.length > 0 || ($config?.license_metadata?.type ?? null) === 'trial' || (($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats))}
			<div class=" w-full z-30">
				<div
					class=" flex flex-col gap-1 w-full max-h-28 overflow-y-auto overscroll-contain md:max-h-none md:overflow-visible"
				>
					{#if ($config?.license_metadata?.type ?? null) === 'trial'}
						<Banner
							banner={{
								type: 'info',
								title: $i18n.t('Trial License'),
								content: $i18n.t(
									'You are currently using a trial license. Please contact support to upgrade your license.'
								)
							}}
						/>
					{/if}

					{#if ($config?.license_metadata?.seats ?? null) !== null && $config?.user_count > $config?.license_metadata?.seats}
						<Banner
							banner={{
								type: 'error',
								title: $i18n.t('License Error'),
								content: $i18n.t(
									'Exceeded the number of seats in your license. Please contact support to increase the number of seats.'
								)
							}}
						/>
					{/if}

					{#each $banners.filter((b) => ![...getDismissedBannerIds(), ...closedBannerIds].includes(b.id)) as banner (banner.id)}
						<Banner
							{banner}
							on:dismiss={(e) => {
								const bannerId = e.detail;

								if (banner.dismissible) {
									localStorage.setItem(
										'dismissedBannerIds',
										JSON.stringify(
											[bannerId, ...getDismissedBannerIds()].filter((id) =>
												$banners.find((b) => b.id === id)
											)
										)
									);
								} else {
									closedBannerIds = [...closedBannerIds, bannerId];
								}
							}}
						/>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</nav>

<style>
	.buddy-chat-header {
		position: sticky;
		top: 0;
		z-index: 30;
		width: 100%;
		height: 110px;
		min-height: 110px;
		flex-shrink: 0;
		pointer-events: none;
	}

	.buddy-header-banners {
		pointer-events: auto;
	}

	.buddy-header-fade {
		position: absolute;
		inset: 0 0 -26px;
		z-index: -1;
		background: linear-gradient(
			to bottom,
			color-mix(in srgb, var(--buddy-stage, #faf8f5) 98%, transparent) 0%,
			color-mix(in srgb, var(--buddy-stage, #faf8f5) 88%, transparent) 54%,
			transparent 100%
		);
		pointer-events: none;
	}

	.buddy-header-center {
		position: absolute;
		top: 10px;
		left: 50%;
		display: flex;
		transform: translateX(-50%);
		flex-direction: column;
		align-items: center;
		gap: 7px;
		max-width: 42%;
		pointer-events: auto;
	}

	.buddy-name-pill {
		display: flex;
		align-items: center;
		max-width: 100%;
		min-height: 24px;
		padding: 2px 12px;
		border: 1px solid rgb(39 99 75 / 9%);
		border-radius: 999px;
		background: rgb(255 255 255 / 66%);
		box-shadow: 0 2px 7px rgb(39 99 75 / 3%);
		color: #3f4e45;
		font-size: 12px;
		font-weight: 550;
		letter-spacing: 0.025em;
		backdrop-filter: blur(12px);
	}

	.buddy-name-pill span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.buddy-header-left,
	.buddy-header-right {
		position: absolute;
		top: 18px;
		display: flex;
		flex-direction: row;
		align-items: center;
		gap: 4px;
		pointer-events: auto;
	}

	.buddy-header-left {
		left: 22px;
	}
	.buddy-header-right {
		right: 22px;
		flex-direction: row-reverse;
	}

	.buddy-circle-button,
	.buddy-quiet-button {
		display: inline-flex;
		width: 44px;
		height: 44px;
		flex: none;
		align-items: center;
		justify-content: center;
		border-radius: 50%;
		color: #53635a;
		cursor: pointer;
		transition:
			background 160ms ease,
			color 160ms ease,
			transform 160ms ease;
		-webkit-app-region: no-drag;
	}

	.buddy-circle-button {
		position: relative;
		border: 1px solid rgb(39 99 75 / 12%);
		background: rgb(255 255 255 / 66%);
		box-shadow: 0 3px 10px rgb(31 62 45 / 4%);
		backdrop-filter: blur(12px);
	}

	.buddy-notification-dot {
		position: absolute;
		top: 1px;
		right: 1px;
		width: 10px;
		height: 10px;
		border: 2px solid var(--buddy-stage, #faf8f5);
		border-radius: 50%;
		background: #3b82f6;
		box-shadow: 0 0 0 1px rgb(59 130 246 / 14%);
	}
	.buddy-circle-button :global(svg),
	.buddy-quiet-button :global(svg) {
		width: 20px;
		height: 20px;
	}
	.buddy-actions-button {
		background: rgb(255 255 255 / 36%);
		box-shadow: none;
	}
	.buddy-quiet-button {
		color: #7b8780;
	}

	.buddy-circle-button:hover,
	.buddy-quiet-button:hover {
		background: rgb(221 243 228 / 70%);
		color: #27634b;
	}

	.buddy-circle-button:active,
	.buddy-quiet-button:active {
		transform: scale(0.95);
	}
	.buddy-circle-button:focus-visible,
	.buddy-quiet-button:focus-visible {
		outline: 2px solid #679c75;
		outline-offset: 3px;
	}
	.buddy-control-active {
		color: #27634b;
		background: rgb(221 243 228 / 60%);
	}

	.buddy-header-scrolled .buddy-circle-button {
		box-shadow: 0 3px 12px rgb(31 62 45 / 7%);
	}

	.buddy-secondary-controls {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.buddy-temporary-note {
		position: absolute;
		left: 50%;
		top: 105px;
		transform: translateX(-50%);
		font-size: 10px;
		color: #7b8780;
		white-space: nowrap;
	}

	:global(.dark) .buddy-name-pill {
		color: #d7e4d9;
		background: rgb(57 66 59 / 58%);
		border-color: rgb(221 243 228 / 10%);
	}
	:global(.dark) .buddy-circle-button {
		color: #b6c7bb;
		background: rgb(55 63 57 / 58%);
		border-color: rgb(221 243 228 / 12%);
	}
	:global(.dark) .buddy-quiet-button {
		color: #879b8c;
	}
	:global(.dark) .buddy-circle-button:hover,
	:global(.dark) .buddy-quiet-button:hover {
		background: rgb(103 156 117 / 20%);
		color: #ddf3e4;
	}
	:global(.dark) .buddy-control-active {
		color: #ddf3e4;
		background: rgb(103 156 117 / 18%);
	}

	/* Keep the native menu target accessible when older local web scripts load. */
	.buddy-chat-header :global(#chat-context-menu-button) {
		width: 44px !important;
		height: 44px !important;
		border-radius: 50% !important;
		border-color: rgb(39 99 75 / 12%);
		background: rgb(255 255 255 / 36%);
		color: #53635a;
	}

	.buddy-chat-header :global(#chat-context-menu-button:hover) {
		background: rgb(221 243 228 / 70%);
		color: #27634b;
	}

	:global(.dark) .buddy-chat-header :global(#chat-context-menu-button) {
		background: rgb(55 63 57 / 58%);
		border-color: rgb(221 243 228 / 12%);
		color: #b6c7bb;
	}

	:global(.dark) .buddy-chat-header :global(#chat-context-menu-button:hover) {
		background: rgb(103 156 117 / 20%);
		color: #ddf3e4;
	}

	@media (max-width: 640px) {
		.buddy-header-left {
			left: 14px;
		}
		.buddy-header-right {
			right: 14px;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.buddy-circle-button,
		.buddy-quiet-button {
			transition: none;
		}
	}
</style>
