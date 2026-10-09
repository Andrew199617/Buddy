<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import BuddyAvatar from './BuddyAvatar.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import {
		removeMobileNotification,
		getMobileNotificationDuration,
		type MobileNotification
	} from '$lib/notifications/mobile';

	export let notification: MobileNotification;
	export let urgent = false;
	export let compact = false;
	$: inlineAction =
		compact &&
		urgent &&
		!notification.component &&
		(notification.action || notification.dismissable !== false);
	const i18n: any = getContext('i18n');
	let timer: ReturnType<typeof setTimeout> | undefined;
	let remaining = 4000;
	let startedAt = 0;

	function pauseTimer() {
		if (!timer) return;
		clearTimeout(timer);
		timer = undefined;
		remaining = Math.max(0, remaining - (Date.now() - startedAt));
	}

	function resumeTimer() {
		if (timer || !Number.isFinite(remaining)) return;
		startedAt = Date.now();
		timer = setTimeout(() => {
			removeMobileNotification(notification.id, 'auto');
		}, remaining);
	}

	function dismiss() {
		removeMobileNotification(notification.id, 'dismiss');
	}

	function acknowledge(event: MouseEvent) {
		notification.action?.onClick(event);
		if (!event.defaultPrevented) dismiss();
	}

	function cancel() {
		notification.cancel?.onClick?.();
		dismiss();
	}

	onMount(() => {
		remaining = getMobileNotificationDuration(notification);
		if (notification.dismissable === false) {
			remaining = Infinity;
		}
		resumeTimer();
		return pauseTimer;
	});
</script>

<section
	class="buddy-notification-card"
	class:urgent
	class:compact
	role={urgent ? 'alert' : 'status'}
	aria-atomic="true"
	on:pointerenter={pauseTimer}
	on:pointerleave={resumeTimer}
	on:focusin={pauseTimer}
	on:focusout={(event) => {
		if (!event.currentTarget.contains(event.relatedTarget as Node)) resumeTimer();
	}}
>
	{#if notification.component}
		<div class="custom-notification">
			<svelte:component
				this={notification.component}
				{...notification.componentProps}
				on:closeToast={dismiss}
			/>
		</div>
	{:else}
		<div class="notification-heading">
			<BuddyAvatar size={32} decorative />
			<div class="notification-copy">
				<div class="notification-title">
					{#if typeof notification.title === 'string'}
						{notification.title}
					{:else if notification.title}
						<svelte:component this={notification.title} {...notification.componentProps} />
					{/if}
				</div>
				{#if notification.description && !urgent}
					<div class="notification-description">
						{#if typeof notification.description === 'string'}
							{notification.description}
						{:else}
							<svelte:component this={notification.description} {...notification.componentProps} />
						{/if}
					</div>
				{/if}
			</div>
			{#if inlineAction}
				<button class="notification-inline-action" type="button" on:click={acknowledge}>
					{notification.action?.label ?? $i18n.t('Got it')}
				</button>
			{:else if notification.dismissable !== false && notification.type !== 'loading'}
				<button
					class="notification-close"
					type="button"
					on:click={dismiss}
					aria-label={$i18n.t('Dismiss notification')}
				>
					<XMark className="size-4" />
				</button>
			{/if}
		</div>
		{#if notification.description && urgent}
			<div class="notification-description urgent-description">
				{#if typeof notification.description === 'string'}
					{notification.description}
				{:else}
					<svelte:component this={notification.description} {...notification.componentProps} />
				{/if}
			</div>
		{/if}
		{#if notification.type === 'loading'}
			<div class="notification-progress" aria-hidden="true"></div>
		{/if}
	{/if}
	{#if !inlineAction && (notification.action || (urgent && notification.dismissable !== false))}
		<button class="notification-action" type="button" on:click={acknowledge}>
			{notification.action?.label ?? $i18n.t('Got it')}
		</button>
	{/if}
	{#if notification.cancel}
		<button class="notification-cancel" type="button" on:click={cancel}>
			{notification.cancel.label}
		</button>
	{/if}
</section>

<style>
	.buddy-notification-card {
		pointer-events: auto;
		color: #25392d;
		background: #f4f7f0;
		border: 1px solid #d7dfd2;
		border-radius: 22px;
		padding: 12px;
		box-shadow: 0 10px 30px rgb(20 32 22 / 12%);
		overflow-y: auto;
		overscroll-behavior: contain;
		max-height: inherit;
	}
	:global(.dark) .buddy-notification-card {
		color: #e2e9df;
		background: #222a25;
		border-color: #3c463d;
		box-shadow: 0 12px 32px rgb(0 0 0 / 28%);
	}
	.notification-heading {
		display: flex;
		gap: 10px;
		align-items: center;
	}
	.notification-copy {
		flex: 1;
		min-width: 0;
	}
	.notification-title {
		font-size: calc(15px + var(--buddy-font-size-offset, 0px));
		line-height: 1.4;
		overflow-wrap: anywhere;
	}
	.notification-description {
		margin-top: 4px;
		font-size: calc(13px + var(--buddy-font-size-offset, 0px));
		line-height: 1.5;
		color: #687462;
		overflow-wrap: anywhere;
	}
	:global(.dark) .notification-description {
		color: #a3ada1;
	}
	.notification-close {
		flex: none;
		width: 44px;
		height: 44px;
		display: grid;
		place-items: center;
		border-radius: 50%;
	}
	.notification-close:hover,
	.notification-cancel:hover {
		background: rgb(125 145 121 / 12%);
	}
	.urgent {
		padding: 16px;
		border-radius: 26px;
	}
	.urgent-description {
		margin: 10px 0 14px;
		font-size: calc(15px + var(--buddy-font-size-offset, 0px));
	}
	.notification-action {
		width: 100%;
		min-height: 44px;
		margin-top: 12px;
		padding: 10px 16px;
		background: #dce9d9;
		border-radius: 16px;
		font-size: calc(15px + var(--buddy-font-size-offset, 0px));
	}
	:global(.dark) .notification-action {
		background: #2e4435;
		color: #e2e9df;
	}
	.notification-cancel {
		width: 100%;
		min-height: 44px;
		margin-top: 4px;
		border-radius: 16px;
	}
	button:focus-visible {
		outline: 2px solid #6c9674;
		outline-offset: 2px;
	}
	.custom-notification :global([role='status']) {
		min-width: 0;
		border: 0;
		border-radius: 0;
		padding: 8px 44px 8px 0;
		background: transparent;
		color: inherit;
	}
	.custom-notification :global(button) {
		top: 0;
		left: auto;
		right: 0;
		opacity: 1;
		width: 44px;
		height: 44px;
		display: grid;
		place-items: center;
		background: transparent;
	}
	.custom-notification :global(img) {
		width: 32px;
		height: 32px;
	}
	.notification-progress {
		height: 3px;
		border-radius: 3px;
		background: #8bab87;
		margin-top: 10px;
		animation: pulse 1.5s ease-in-out infinite;
	}
	@keyframes pulse {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.notification-progress {
			animation: none;
		}
	}
	.compact {
		padding: 0 8px;
		border-radius: 14px;
	}
	.compact .notification-heading {
		min-height: 44px;
	}
	.compact .notification-title {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.compact .notification-description {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
	}
	.notification-inline-action {
		flex: none;
		min-height: 44px;
		max-width: 45%;
		padding: 0 8px;
		color: #426f4e;
		font-size: 13px;
	}
	:global(.dark) .notification-inline-action {
		color: #bdd9b6;
	}
</style>
