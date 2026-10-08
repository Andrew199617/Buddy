<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { page } from '$app/stores';
	import { config, showSidebar, user, type SessionUser } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ChatIcon from '$lib/components/icons/ChatBubbleOval.svelte';
	import KnowledgeIcon from '$lib/components/icons/BookOpen.svelte';
	import NotesIcon from '$lib/components/layout/Sidebar/icons/Notes.svelte';
	import AutomationsIcon from '$lib/components/layout/Sidebar/icons/Clock.svelte';
	import WorkspaceIcon from '$lib/components/layout/Sidebar/icons/Workspace.svelte';

	export let keyboardOpen = false;
	export let viewportHeight: number | null = null;
	export let viewportTop = 0;

	type DockFeatures = {
		enable_notes?: boolean;
		enable_automations?: boolean;
		enable_plugins?: boolean;
	};

	const i18n: any = getContext('i18n');

	$: dockFeatures = $config?.features as DockFeatures | undefined;
	$: pathname = $page.url.pathname;
	$: admin = $user?.role === 'admin';
	$: notesVisible =
		(dockFeatures?.enable_notes ?? false) &&
		(admin || ($user?.permissions?.features?.notes ?? true));
	$: knowledgeVisible = admin || Boolean($user?.permissions?.workspace?.knowledge);
	$: automationsVisible =
		(dockFeatures?.enable_automations ?? false) &&
		(admin || Boolean($user?.permissions?.features?.automations));
	$: workspaceHref = getWorkspaceDestination($user, dockFeatures);
	$: compactDock =
		countVisibleLinks(notesVisible, knowledgeVisible, automationsVisible, workspaceHref) < 4;
	$: knowledgeActive = matchesRoute(pathname, '/workspace/knowledge');
	$: workspaceActive = matchesRoute(pathname, '/workspace') && !knowledgeActive;
	$: chatActive =
		pathname === '/' ||
		matchesRoute(pathname, '/c') ||
		matchesRoute(pathname, '/folders') ||
		matchesRoute(pathname, '/channels');

	function matchesRoute(path: string, route: string) {
		return path === route || path.startsWith(`${route}/`);
	}

	function getWorkspaceDestination(
		currentUser: SessionUser | undefined,
		currentFeatures: DockFeatures | undefined
	): string | null {
		if (currentUser?.role === 'admin' || currentUser?.permissions?.workspace?.models) {
			return '/workspace/models';
		}
		if (currentUser?.permissions?.workspace?.prompts) {
			return '/workspace/prompts';
		}
		if (currentFeatures?.enable_plugins && currentUser?.permissions?.workspace?.tools) {
			return '/workspace/tools';
		}
		if (currentUser?.permissions?.workspace?.skills) {
			return '/workspace/skills';
		}
		return null;
	}

	function countVisibleLinks(
		notes: boolean,
		knowledge: boolean,
		automations: boolean,
		workspace: string | null
	) {
		let count = 1;
		if (notes) {
			count += 1;
		}
		if (knowledge) {
			count += 1;
		}
		if (automations) {
			count += 1;
		}
		if (workspace) {
			count += 1;
		}
		return count;
	}

	function handleNavigation() {
		showSidebar.set(false);
	}

	function acceptsKeyboardInput(element: Element | null) {
		if (!(element instanceof HTMLElement)) {
			return false;
		}
		if (element.isContentEditable || element instanceof HTMLTextAreaElement) {
			return true;
		}
		if (element instanceof HTMLInputElement) {
			const nonKeyboardTypes = [
				'button',
				'checkbox',
				'radio',
				'range',
				'submit',
				'reset',
				'file',
				'color'
			];
			return !nonKeyboardTypes.includes(element.type);
		}
		return false;
	}

	onMount(() => {
		const viewport = window.visualViewport;
		const primaryPointer = window.matchMedia('(pointer: coarse)');
		const touchCapable =
			navigator.maxTouchPoints > 0 || window.matchMedia('(any-pointer: coarse)').matches;
		let fullViewportHeight = window.innerHeight;
		let focusUpdateTimer: ReturnType<typeof setTimeout> | undefined;

		const updateKeyboardVisibility = () => {
			const inputFocused = acceptsKeyboardInput(document.activeElement);
			const visibleHeight = viewport?.height ?? window.innerHeight;
			if (!inputFocused) {
				fullViewportHeight = window.innerHeight;
			}
			const viewportReducedWithinWindow = window.innerHeight - visibleHeight > 140;
			const keyboardLayout = primaryPointer.matches || viewportReducedWithinWindow;
			const keyboardHeightDelta = fullViewportHeight - visibleHeight > 140;
			keyboardOpen = touchCapable && inputFocused && keyboardLayout && keyboardHeightDelta;
			viewportHeight = visibleHeight;
			viewportTop = viewport?.offsetTop ?? 0;
		};

		const scheduleFocusUpdate = () => {
			clearTimeout(focusUpdateTimer);
			focusUpdateTimer = setTimeout(updateKeyboardVisibility, 0);
		};

		const resetOrientation = () => {
			fullViewportHeight = window.innerHeight;
			updateKeyboardVisibility();
		};

		viewport?.addEventListener('resize', updateKeyboardVisibility);
		viewport?.addEventListener('scroll', updateKeyboardVisibility);
		window.addEventListener('resize', updateKeyboardVisibility);
		window.addEventListener('orientationchange', resetOrientation);
		document.addEventListener('focusin', scheduleFocusUpdate);
		document.addEventListener('focusout', scheduleFocusUpdate);
		updateKeyboardVisibility();

		return () => {
			clearTimeout(focusUpdateTimer);
			viewport?.removeEventListener('resize', updateKeyboardVisibility);
			viewport?.removeEventListener('scroll', updateKeyboardVisibility);
			window.removeEventListener('resize', updateKeyboardVisibility);
			window.removeEventListener('orientationchange', resetOrientation);
			document.removeEventListener('focusin', scheduleFocusUpdate);
			document.removeEventListener('focusout', scheduleFocusUpdate);
			keyboardOpen = false;
			viewportHeight = null;
			viewportTop = 0;
		};
	});
</script>

<nav
	class="buddy-dock"
	class:buddy-dock-hidden={keyboardOpen}
	class:buddy-dock-compact={compactDock}
	aria-label={$i18n.t('Main navigation')}
	aria-hidden={keyboardOpen}
	inert={keyboardOpen || $showSidebar}
>
	<Tooltip content={$i18n.t('Chat')} touch={false}>
		<a
			href="/"
			class="buddy-dock-link"
			class:active={chatActive}
			aria-label={$i18n.t('Chat')}
			aria-current={chatActive ? 'page' : undefined}
			on:click={handleNavigation}
		>
			<ChatIcon className="size-[1.375rem]" />
		</a>
	</Tooltip>

	{#if notesVisible}
		<Tooltip content={$i18n.t('Notes')} touch={false}>
			<a
				href="/notes"
				class="buddy-dock-link"
				class:active={matchesRoute(pathname, '/notes')}
				aria-label={$i18n.t('Notes')}
				aria-current={matchesRoute(pathname, '/notes') ? 'page' : undefined}
				on:click={handleNavigation}
			>
				<NotesIcon className="size-[1.375rem]" />
			</a>
		</Tooltip>
	{/if}

	{#if knowledgeVisible}
		<Tooltip content={$i18n.t('Knowledge')} touch={false}>
			<a
				href="/workspace/knowledge"
				class="buddy-dock-link"
				class:active={knowledgeActive}
				aria-label={$i18n.t('Knowledge')}
				aria-current={knowledgeActive ? 'page' : undefined}
				on:click={handleNavigation}
			>
				<KnowledgeIcon className="size-[1.375rem]" strokeWidth="1.5" />
			</a>
		</Tooltip>
	{/if}

	{#if automationsVisible}
		<Tooltip content={$i18n.t('Automations')} touch={false}>
			<a
				href="/automations"
				class="buddy-dock-link"
				class:active={matchesRoute(pathname, '/automations')}
				aria-label={$i18n.t('Automations')}
				aria-current={matchesRoute(pathname, '/automations') ? 'page' : undefined}
				on:click={handleNavigation}
			>
				<AutomationsIcon className="size-[1.375rem]" />
			</a>
		</Tooltip>
	{/if}

	{#if workspaceHref}
		<Tooltip content={$i18n.t('Workspace')} touch={false}>
			<a
				href={workspaceHref}
				class="buddy-dock-link"
				class:active={workspaceActive}
				aria-label={$i18n.t('Workspace')}
				aria-current={workspaceActive ? 'page' : undefined}
				on:click={handleNavigation}
			>
				<WorkspaceIcon className="size-[1.375rem]" />
			</a>
		</Tooltip>
	{/if}
</nav>

<style>
	.buddy-dock {
		position: fixed;
		bottom: calc(16px + env(safe-area-inset-bottom));
		left: 50%;
		z-index: 30;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 6px;
		width: min(420px, calc(100vw - 32px));
		max-width: 480px;
		box-sizing: border-box;
		height: 64px;
		padding: 9px 14px;
		border: 1px solid var(--buddy-edge, rgb(37 78 59 / 8%));
		border-radius: 999px;
		background: var(--buddy-glass, rgb(255 255 255 / 80%));
		box-shadow:
			0 8px 32px rgb(20 45 31 / 7%),
			inset 0 1px 0 rgb(255 255 255 / 25%);
		backdrop-filter: blur(22px) saturate(135%);
		-webkit-backdrop-filter: blur(22px) saturate(135%);
		transform: translateX(-50%);
		transition:
			opacity 150ms ease,
			transform 150ms ease;
	}

	.buddy-dock-compact {
		width: auto;
		max-width: calc(100vw - 32px);
		justify-content: center;
		gap: 18px;
	}

	.buddy-dock-hidden {
		visibility: hidden;
		opacity: 0;
		pointer-events: none;
		transform: translate(-50%, 16px);
	}

	.buddy-dock-link {
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
		width: 44px;
		height: 44px;
		border-radius: 50%;
		color: var(--buddy-soft-text, #788579);
		transition:
			color 150ms ease,
			background 150ms ease;
	}

	.buddy-dock-link:hover {
		color: #254e3b;
		background: rgb(118 169 139 / 10%);
	}

	.buddy-dock-link.active {
		color: #254e3b;
		background: #e7efe7;
	}

	.buddy-dock-link:focus-visible {
		outline: 2px solid #76a98b;
		outline-offset: 3px;
	}

	:global(.dark) .buddy-dock-link:hover,
	:global(.dark) .buddy-dock-link.active {
		color: #bfdfc8;
		background: rgb(156 201 170 / 13%);
	}

	@media (prefers-reduced-motion: reduce) {
		.buddy-dock,
		.buddy-dock-link {
			transition: none;
		}
	}

	@media (forced-colors: active) {
		.buddy-dock {
			border-color: CanvasText;
			background: Canvas;
		}
		.buddy-dock-link.active {
			outline: 2px solid Highlight;
		}
	}
</style>
