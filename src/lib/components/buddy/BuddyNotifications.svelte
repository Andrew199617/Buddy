<script lang="ts">
	import { onMount } from 'svelte';
	import { mobile, showSidebar, user } from '$lib/stores';
	import { page } from '$app/stores';
	import {
		mobileNotifications,
		getMobileNotificationPriority,
		clearMobileNotifications
	} from '$lib/notifications/mobile';
	import BuddyNotificationCard from './BuddyNotificationCard.svelte';

	let headerBottom = 12;
	let composerTop = 600;
	let availableHeight = 500;
	let frame: number | undefined;
	let resizeObserver: ResizeObserver;
	let header: Element | null = null;
	let composer: Element | null = null;
	let mounted = false;
	let previousUserId: string | undefined;

	$: urgent = $mobileNotifications.find((item) => getMobileNotificationPriority(item) === 'urgent');
	$: highLevel = $mobileNotifications.find(
		(item) => getMobileNotificationPriority(item) === 'high-level'
	);
	$: showHighLevel = Boolean(highLevel && (!urgent || availableHeight >= 240));
	$: if (mounted && $page.url.pathname) scheduleGeometry();
	$: if (mounted && (urgent || highLevel)) scheduleGeometry();
	$: if (previousUserId !== $user?.id) {
		if (previousUserId) clearMobileNotifications();
		previousUserId = $user?.id;
	}

	function updateGeometry() {
		frame = undefined;
		const nextHeader = document.querySelector('.buddy-chat-header');
		const nextComposer = document.querySelector('.buddy-chat .buddy-composer');
		if (nextHeader !== header || nextComposer !== composer) {
			resizeObserver.disconnect();
			header = nextHeader;
			composer = nextComposer;
			if (header) resizeObserver.observe(header);
			if (composer) resizeObserver.observe(composer);
		}
		const viewport = window.visualViewport;
		const viewportTop = viewport?.offsetTop ?? 0;
		const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
		headerBottom = Math.max(
			viewportTop + 12,
			(header?.getBoundingClientRect().bottom ?? viewportTop) + 12
		);
		composerTop = composer?.getBoundingClientRect().top ?? viewportBottom - 24;
		availableHeight = Math.max(0, composerTop - headerBottom - 12);
	}

	function scheduleGeometry() {
		if (!mounted || frame !== undefined) return;
		frame = requestAnimationFrame(updateGeometry);
	}

	onMount(() => {
		mounted = true;
		resizeObserver = new ResizeObserver(scheduleGeometry);
		const observer = new MutationObserver(scheduleGeometry);
		observer.observe(document.body, { childList: true, subtree: true });
		window.addEventListener('resize', scheduleGeometry);
		window.visualViewport?.addEventListener('resize', scheduleGeometry);
		window.visualViewport?.addEventListener('scroll', scheduleGeometry);
		updateGeometry();
		return () => {
			mounted = false;
			if (frame !== undefined) cancelAnimationFrame(frame);
			resizeObserver.disconnect();
			observer.disconnect();
			window.removeEventListener('resize', scheduleGeometry);
			window.visualViewport?.removeEventListener('resize', scheduleGeometry);
			window.visualViewport?.removeEventListener('scroll', scheduleGeometry);
		};
	});
</script>

{#if $mobile && !$showSidebar}
	{#if showHighLevel && highLevel}
		<div
			class="buddy-notification-host high-level-host"
			style:top={`${headerBottom}px`}
			style:max-height={`${urgent ? availableHeight * 0.35 : availableHeight}px`}
			data-notification-priority="high-level"
		>
			{#key highLevel.revision}
				<BuddyNotificationCard notification={highLevel} />
			{/key}
		</div>
	{/if}
	{#if urgent}
		<div
			class="buddy-notification-host urgent-host"
			style:top={`${composerTop - 12}px`}
			style:max-height={`${showHighLevel ? availableHeight * 0.6 : availableHeight}px`}
			data-notification-priority="urgent"
		>
			{#key urgent.revision}
				<BuddyNotificationCard notification={urgent} urgent compact={availableHeight < 120} />
			{/key}
		</div>
	{/if}
{/if}

<style>
	.buddy-notification-host {
		position: fixed;
		left: 16px;
		right: 16px;
		z-index: 60;
		pointer-events: none;
	}
	.urgent-host {
		transform: translateY(-100%);
	}
	@media (min-width: 768px) {
		.buddy-notification-host {
			display: none;
		}
	}
</style>
