<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { createFocusTrap } from 'focus-trap';
	import BuddyAvatar from '$lib/components/buddy/BuddyAvatar.svelte';
	import { WEBUI_NAME } from '$lib/stores';
	import {
		cancelOAuthConnect,
		continueOAuthConnect,
		getOAuthConnectSession,
		type OAuthConnectSession
	} from '$lib/apis/configs';

	const i18n: any = getContext('i18n');
	let connection: OAuthConnectSession | null = null;
	let continuing = false;
	let returning = false;
	let errorMessage = '';
	let dialog: HTMLDivElement;

	function refreshConnection() {
		connection = getOAuthConnectSession();
		continuing = false;
		returning = false;
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (event.ctrlKey || event.metaKey || event.altKey) {
			// Keep app shortcuts from reaching the mounted chat behind this dialog.
			event.stopImmediatePropagation();
		}
		if (event.key !== 'Escape') {
			return;
		}
		event.preventDefault();
		event.stopImmediatePropagation();
		if (!returning) {
			exitConnection();
		}
	}

	onMount(() => {
		refreshConnection();
		const focusTrap = createFocusTrap(dialog, {
			initialFocus: '#buddy-auth-exit',
			fallbackFocus: dialog,
			escapeDeactivates: false,
			returnFocusOnDeactivate: true,
			setReturnFocus: (previous) => {
				const touchKeyboard = window.matchMedia('(pointer: coarse)').matches;
				const wasEditing = previous?.matches('input, textarea, [contenteditable="true"]');
				if (touchKeyboard && wasEditing) {
					return false;
				}
				if (previous?.isConnected) {
					return previous;
				}
				if (touchKeyboard) {
					return false;
				}
				return document.getElementById('chat-input') || false;
			}
		});
		focusTrap.activate();
		document.addEventListener('keydown', handleKeyDown, true);
		return () => {
			document.removeEventListener('keydown', handleKeyDown, true);
			focusTrap.deactivate();
		};
	});

	function continueConnection() {
		errorMessage = '';
		try {
			if (!continueOAuthConnect()) {
				connection = null;
				return;
			}
			continuing = true;
		} catch {
			errorMessage = $i18n.t('The connection could not be started. You can try again.');
		}
	}

	async function exitConnection() {
		returning = true;
		errorMessage = '';
		try {
			await cancelOAuthConnect();
		} catch {
			returning = false;
			errorMessage = $i18n.t('Could not return to Buddy. Please try again.');
		}
	}
</script>

<svelte:head>
	<title>{$i18n.t('Connect')} · {$WEBUI_NAME}</title>
</svelte:head>

<svelte:window on:pageshow={refreshConnection} />

<div
	bind:this={dialog}
	class="buddy-auth-connect"
	role="dialog"
	aria-modal="true"
	aria-labelledby="buddy-auth-connect-title"
	tabindex="-1"
>
	<header class="buddy-auth-header">
		<button
			type="button"
			id="buddy-auth-exit"
			class="buddy-auth-exit"
			on:click={exitConnection}
			disabled={returning}
		>
			<svg
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="1.6"
				aria-hidden="true"
			>
				<path d="m14 7-5 5 5 5" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
			<span>{$i18n.t('Exit')}</span>
		</button>
		<div class="buddy-auth-header-center">
			<BuddyAvatar
				size={60}
				state={returning || continuing ? 'thinking' : 'idle'}
				decorative={true}
			/>
			<div class="buddy-auth-name"><span>{$WEBUI_NAME}</span></div>
		</div>
	</header>

	<div class="buddy-auth-body">
		<section class="buddy-auth-card">
			{#if connection}
				<h1 id="buddy-auth-connect-title">
					{$i18n.t('Connect {{tool}}', { tool: connection.toolName })}
				</h1>
				<p>
					{$i18n.t(
						'Continue to sign in with your provider, or exit to return to what you were doing.'
					)}
				</p>
				<button
					type="button"
					id="buddy-auth-continue"
					class="buddy-auth-continue"
					on:click={continueConnection}
					disabled={continuing || returning}
				>
					{$i18n.t('Continue')}
				</button>
			{:else}
				<h1 id="buddy-auth-connect-title">{$i18n.t('No connection in progress')}</h1>
				<p>{$i18n.t('Exit to return to Buddy and start a new connection when you are ready.')}</p>
			{/if}
			<div class="buddy-auth-status" role="status" aria-live="polite">
				{#if returning}
					{$i18n.t('Returning to your conversation…')}
				{:else if continuing}
					{$i18n.t('Opening sign-in…')}
				{/if}
			</div>
			{#if errorMessage}
				<p class="buddy-auth-error" role="alert">{errorMessage}</p>
			{/if}
		</section>
	</div>
</div>

<style>
	.buddy-auth-connect {
		--buddy-safe-top: env(safe-area-inset-top, 0px);
		--buddy-safe-right: env(safe-area-inset-right, 0px);
		--buddy-safe-bottom: env(safe-area-inset-bottom, 0px);
		--buddy-safe-left: env(safe-area-inset-left, 0px);
		position: fixed;
		inset: 0;
		z-index: 100000;
		display: flex;
		flex-direction: column;
		min-height: 0;
		overflow: hidden;
		background: #faf8f5;
		color: #3f4e45;
	}

	.buddy-auth-header {
		position: relative;
		z-index: 1;
		flex: none;
		height: calc(110px + var(--buddy-safe-top));
	}

	.buddy-auth-header-center {
		position: absolute;
		top: calc(10px + var(--buddy-safe-top));
		left: 50%;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 7px;
		max-width: 42%;
		transform: translateX(-50%);
	}

	.buddy-auth-body {
		display: flex;
		flex: 1;
		min-height: 0;
		overflow: auto;
		box-sizing: border-box;
		padding: 24px calc(24px + var(--buddy-safe-right)) calc(32px + var(--buddy-safe-bottom))
			calc(24px + var(--buddy-safe-left));
	}

	.buddy-auth-exit {
		position: absolute;
		top: calc(18px + var(--buddy-safe-top));
		left: calc(14px + var(--buddy-safe-left));
		z-index: 1;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		min-width: 76px;
		min-height: 44px;
		padding: 8px 12px;
		border: 1px solid rgb(39 99 75 / 12%);
		border-radius: 999px;
		background: rgb(255 255 255 / 76%);
		color: #53635a;
		font-size: 14px;
		cursor: pointer;
	}

	.buddy-auth-exit svg {
		width: 18px;
		height: 18px;
	}

	.buddy-auth-card {
		display: flex;
		flex-direction: column;
		align-items: center;
		width: 100%;
		max-width: 360px;
		margin: auto;
		text-align: center;
	}

	.buddy-auth-name {
		display: flex;
		align-items: center;
		max-width: 100%;
		min-height: 24px;
		padding: 2px 12px;
		border: 1px solid rgb(39 99 75 / 9%);
		border-radius: 999px;
		background: rgb(255 255 255 / 66%);
		color: #3f4e45;
		font-size: 12px;
		font-weight: 550;
		letter-spacing: 0.025em;
	}

	.buddy-auth-name span {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	h1 {
		margin-top: 0;
		font-size: 23px;
		font-weight: 550;
		line-height: 1.25;
	}

	p {
		margin-top: 12px;
		color: #788579;
		font-size: 14px;
		line-height: 1.65;
	}

	.buddy-auth-continue {
		min-height: 44px;
		min-width: 160px;
		margin-top: 28px;
		padding: 10px 24px;
		border-radius: 999px;
		background: #27634b;
		color: #fff;
		font-size: 14px;
		font-weight: 550;
		cursor: pointer;
	}

	button:focus-visible {
		outline: 2px solid #679c75;
		outline-offset: 4px;
	}

	button:disabled {
		cursor: default;
		opacity: 0.6;
	}

	.buddy-auth-status {
		min-height: 22px;
		margin-top: 18px;
		color: #788579;
		font-size: 12px;
	}

	.buddy-auth-error {
		color: #a14c35;
	}

	:global(.dark) .buddy-auth-connect {
		background: #111512;
		color: #d7e4d9;
	}

	:global(.dark) .buddy-auth-exit {
		border-color: rgb(221 243 228 / 12%);
		background: rgb(55 63 57 / 76%);
		color: #b6c7bb;
	}

	:global(.dark) .buddy-auth-name {
		border-color: rgb(221 243 228 / 10%);
		background: rgb(57 66 59 / 58%);
		color: #d7e4d9;
	}

	:global(.dark) p,
	:global(.dark) .buddy-auth-status {
		color: #9daa9f;
	}

	:global(.dark) .buddy-auth-error {
		color: #e89578;
	}
</style>
