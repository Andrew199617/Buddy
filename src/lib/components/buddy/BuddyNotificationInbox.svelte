<script lang="ts">
	import { getContext } from 'svelte';
	import BuddyAvatar from './BuddyAvatar.svelte';
	import {
		mobileNotifications,
		getMobileNotificationPriority,
		removeMobileNotification,
		type MobileNotification
	} from '$lib/notifications/mobile';

	const i18n: any = getContext('i18n');

	$: infoNotifications = $mobileNotifications.filter(
		(notification) => getMobileNotificationPriority(notification) === 'info'
	);

	function dismissNotification(notification: MobileNotification) {
		removeMobileNotification(notification.id, 'dismiss');
	}

	function handleAction(notification: MobileNotification, event: MouseEvent) {
		notification.action?.onClick(event);
		if (!event.defaultPrevented) {
			dismissNotification(notification);
		}
	}

	function handleCancel(notification: MobileNotification) {
		notification.cancel?.onClick?.();
		dismissNotification(notification);
	}
</script>

{#if infoNotifications.length > 0}
	<section class="buddy-notification-inbox" aria-labelledby="buddy-notification-heading">
		<h2 id="buddy-notification-heading">{$i18n.t('Notifications')}</h2>
		<ul class="buddy-notification-list">
			{#each infoNotifications as notification (notification.id)}
				<li class="buddy-inbox-card" data-notification-id={notification.id}>
					<div class="buddy-inbox-card-header">
						<BuddyAvatar size={28} decorative={true} />
						{#if notification.title && !notification.component}
							<div class="buddy-inbox-title">
								{#if typeof notification.title === 'string'}
									{notification.title}
								{:else}
									<svelte:component this={notification.title} {...notification.componentProps} />
								{/if}
							</div>
						{:else}
							<span class="buddy-inbox-title">{$i18n.t('Notification')}</span>
						{/if}
						{#if notification.dismissable !== false}
							<button
								type="button"
								class="buddy-inbox-dismiss"
								aria-label={$i18n.t('Dismiss notification')}
								on:click={() => dismissNotification(notification)}
							>
								<svg
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.5"
									aria-hidden="true"
								>
									<path d="m7 7 10 10M17 7 7 17" stroke-linecap="round" />
								</svg>
							</button>
						{/if}
					</div>
					{#if notification.component}
						<div class="buddy-inbox-content">
							<svelte:component
								this={notification.component}
								{...notification.componentProps}
								on:closeToast={() => dismissNotification(notification)}
							/>
						</div>
					{:else}
						{#if notification.description}
							<div class="buddy-inbox-description">
								{#if typeof notification.description === 'string'}
									{notification.description}
								{:else}
									<svelte:component
										this={notification.description}
										{...notification.componentProps}
									/>
								{/if}
							</div>
						{/if}
						{#if notification.action || notification.cancel}
							<div class="buddy-inbox-actions">
								{#if notification.cancel}
									<button
										type="button"
										class="buddy-inbox-cancel"
										style={notification.cancelButtonStyle}
										on:click={() => handleCancel(notification)}
									>
										{notification.cancel.label}
									</button>
								{/if}
								{#if notification.action}
									<button
										type="button"
										class="buddy-inbox-action"
										style={notification.actionButtonStyle}
										on:click={(event) => handleAction(notification, event)}
									>
										{notification.action.label}
									</button>
								{/if}
							</div>
						{/if}
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.buddy-notification-inbox {
		flex: none;
		padding: 4px 0 12px;
	}

	.buddy-notification-inbox h2 {
		margin: 0 8px 8px;
		color: var(--buddy-soft-text, #667368);
		font-size: 0.875rem;
		font-weight: 500;
	}

	.buddy-notification-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.buddy-inbox-card {
		min-width: 0;
		padding: 10px 12px 12px;
		border: 1px solid var(--buddy-edge, rgb(39 99 75 / 10%));
		border-radius: 18px;
		background: color-mix(in srgb, var(--buddy-panel, #fff) 86%, #ddf3e4);
		color: #3f4e45;
	}

	.buddy-inbox-card-header {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 36px;
	}

	.buddy-inbox-title {
		flex: 1;
		min-width: 0;
		font-size: 0.875rem;
		font-weight: 550;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.buddy-inbox-dismiss {
		display: inline-flex;
		width: 44px;
		height: 44px;
		flex: none;
		align-items: center;
		justify-content: center;
		margin: -4px -8px -4px 0;
		border-radius: 50%;
		color: var(--buddy-soft-text, #667368);
		cursor: pointer;
	}

	.buddy-inbox-dismiss svg {
		width: 18px;
		height: 18px;
	}

	.buddy-inbox-dismiss:hover,
	.buddy-inbox-cancel:hover {
		background: rgb(103 156 117 / 12%);
	}

	.buddy-inbox-description,
	.buddy-inbox-content {
		margin-top: 6px;
		color: var(--buddy-soft-text, #667368);
		font-size: 0.875rem;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.buddy-inbox-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 10px;
	}

	.buddy-inbox-action,
	.buddy-inbox-cancel {
		flex: 1;
		min-height: 44px;
		padding: 8px 12px;
		border-radius: 12px;
		font-size: 0.875rem;
		font-weight: 500;
		line-height: 1.4;
		overflow-wrap: anywhere;
		cursor: pointer;
	}

	.buddy-inbox-action {
		background: #ddf3e4;
		color: #27634b;
	}

	.buddy-inbox-action:hover {
		background: #cbe7d4;
	}

	.buddy-inbox-cancel {
		border: 1px solid var(--buddy-edge, rgb(39 99 75 / 10%));
		color: var(--buddy-soft-text, #667368);
	}

	.buddy-inbox-card button:focus-visible {
		outline: 2px solid #679c75;
		outline-offset: 2px;
	}

	:global(.dark) .buddy-inbox-card {
		background: #292f2b;
		color: #d7e4d9;
	}

	:global(.dark) .buddy-inbox-action {
		background: #334c3c;
		color: #ddf3e4;
	}

	:global(.dark) .buddy-inbox-action:hover {
		background: #405d4a;
	}
</style>
