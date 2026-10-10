<script context="module" lang="ts">
	import { get, writable } from 'svelte/store';
	import type { SubscriptionProviderId as BusyProviderId } from '$lib/apis/subscriptions';

	// A pending start can outlive this dialog. Keep a reopened instance from
	// starting another login before the old request has been cancelled.
	const pendingProviderActions = writable({ claude: 0, codex: 0 });

	const providerIsBusy = (providerId: BusyProviderId) =>
		get(pendingProviderActions)[providerId] > 0;

	const beginProviderAction = (providerId: BusyProviderId) => {
		pendingProviderActions.update((actions) => ({
			...actions,
			[providerId]: actions[providerId] + 1
		}));
		return () => {
			pendingProviderActions.update((actions) => ({
				...actions,
				[providerId]: actions[providerId] - 1
			}));
		};
	};
</script>

<script lang="ts">
	import { getContext, onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		cancelSubscriptionLogin,
		getSubscriptionLogin,
		startSubscriptionLogin,
		submitSubscriptionLoginCode,
		type SubscriptionProviderId,
		type SubscriptionLogin,
		type SubscriptionProvider
	} from '$lib/apis/subscriptions';
	import { copyToClipboard } from '$lib/utils';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { getHttpsSignInUrl } from './signInLink';

	const i18n: any = getContext('i18n');

	export let show = false;
	export let provider: SubscriptionProvider;
	export let onSignedIn: () => void = () => {};

	const POLL_INTERVAL_MS = 2000;

	type LoginOwner = {
		providerId: SubscriptionProviderId;
		machineId: string;
		machineRevision?: string;
	};

	let login: SubscriptionLogin = { state: 'idle' };
	let code = '';
	let busy = false;
	let loginGeneration = 0;
	let loginOwner: LoginOwner | null = null;
	let disposed = false;
	let cancellation: Promise<void> | null = null;
	let pollTimer: ReturnType<typeof setInterval> | null = null;
	let pollInFlight = false;
	$: busy = $pendingProviderActions[provider.id] > 0;
	// The CLI signs in, and a browser sign-in returns, on the machine that runs it.
	$: machineName = provider.machine?.name || $i18n.t('the selected machine');
	$: signInUrl = getHttpsSignInUrl(login.url);

	const inputClass =
		'w-full rounded-xl bg-gray-50 px-3 py-2 text-sm outline-hidden dark:bg-gray-850 placeholder:text-gray-300 dark:placeholder:text-gray-700';
	const primaryButtonClass =
		'shrink-0 whitespace-nowrap px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed';
	const secondaryButtonClass =
		'shrink-0 whitespace-nowrap px-3.5 py-1.5 text-sm font-medium rounded-full border border-gray-200 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-850 transition disabled:opacity-50 disabled:cursor-not-allowed';

	const stopPolling = () => {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	};

	// A sign-in belongs to the computer that was selected when it started.
	const ownsSelectedMachine = (owner: LoginOwner, selected: SubscriptionProvider) =>
		owner.machineId === selected.settings.machine_id &&
		owner.machineRevision === selected.machine?.revision;

	// True while this dialog still shows the attempt that sent a request.
	const isCurrentAttempt = (owner: LoginOwner, generation: number) =>
		!disposed && show && generation === loginGeneration && ownsSelectedMachine(owner, provider);

	const reportIfCurrent = (error: unknown, owner: LoginOwner, generation: number) => {
		if (isCurrentAttempt(owner, generation)) {
			toast.error(`${error}`);
		}
	};

	const cancelWaitingLogin = (owner = loginOwner): Promise<void> => {
		if (!owner) return Promise.resolve();
		if (cancellation) {
			return cancellation;
		}
		const finishAction = beginProviderAction(owner.providerId);
		cancellation = cancelSubscriptionLogin(
			localStorage.token,
			owner.providerId,
			owner.machineId,
			owner.machineRevision
		)
			.then(() => {})
			.catch(() => {})
			.finally(() => {
				finishAction();
				cancellation = null;
			});
		return cancellation;
	};

	const dismissLogin = () => {
		loginGeneration += 1;
		stopPolling();
		const waiting = login.state === 'waiting';
		const owner = loginOwner;
		loginOwner = null;
		login = { state: 'idle' };
		code = '';
		if (waiting) {
			void cancelWaitingLogin(owner);
		}
	};

	const handleLoginUpdate = (update: SubscriptionLogin, generation: number) => {
		if (!loginOwner || !isCurrentAttempt(loginOwner, generation)) {
			return;
		}
		const wasWaiting = login.state === 'waiting';
		login = update;
		const stoppedOnServer = login.state === 'idle' || login.state === 'cancelled';
		if (wasWaiting && stoppedOnServer && !login.message) {
			// Saving this provider's settings elsewhere cancels a pending sign-in on the server.
			login = {
				...login,
				message: $i18n.t(
					'This sign-in was stopped on the server, so its link or code no longer works. Start again.'
				)
			};
		}
		if (login.state !== 'waiting') {
			// Any other response still in flight belongs to the completed polling flow.
			loginGeneration += 1;
			stopPolling();
		}
		if (login.state === 'success') {
			toast.success($i18n.t('Signed in to {{name}}', { name: provider.name }));
			onSignedIn();
			show = false;
		}
	};

	const pollLogin = async () => {
		const generation = loginGeneration;
		const owner = loginOwner;
		if (disposed || !owner || !show || login.state !== 'waiting') return;
		// The server answers one request per provider at a time, and a code submission can hold
		// it for up to a minute. Skip this tick instead of queueing polls behind it.
		if (pollInFlight || providerIsBusy(owner.providerId)) return;
		pollInFlight = true;
		try {
			handleLoginUpdate(
				await getSubscriptionLogin(
					localStorage.token,
					owner.providerId,
					owner.machineId,
					owner.machineRevision
				),
				generation
			);
		} catch (error) {
			if (isCurrentAttempt(owner, generation)) {
				loginGeneration += 1;
				stopPolling();
				toast.error(`${error}`);
			}
		} finally {
			pollInFlight = false;
		}
	};

	const startPolling = () => {
		stopPolling();
		pollTimer = setInterval(pollLogin, POLL_INTERVAL_MS);
	};

	const startLogin = async (method: 'browser' | 'device') => {
		if (disposed || !show || providerIsBusy(provider.id)) {
			return;
		}
		const generation = ++loginGeneration;
		const owner = {
			providerId: provider.id,
			machineId: provider.settings.machine_id,
			machineRevision: provider.machine?.revision
		};
		loginOwner = owner;
		const finishAction = beginProviderAction(provider.id);
		code = '';
		try {
			const update = await startSubscriptionLogin(
				localStorage.token,
				owner.providerId,
				method,
				owner.machineId,
				owner.machineRevision
			);
			if (!isCurrentAttempt(owner, generation)) {
				if (update.state === 'waiting') {
					await cancelWaitingLogin(owner);
				}
				return;
			}
			login = update;
			if (login.state === 'waiting') {
				startPolling();
			}
		} catch (error) {
			reportIfCurrent(error, owner, generation);
		} finally {
			finishAction();
		}
	};

	const submitCode = async () => {
		const owner = loginOwner;
		const generation = loginGeneration;
		if (!owner || !isCurrentAttempt(owner, generation) || providerIsBusy(provider.id)) {
			return;
		}
		const finishAction = beginProviderAction(provider.id);
		try {
			handleLoginUpdate(
				await submitSubscriptionLoginCode(
					localStorage.token,
					owner.providerId,
					code,
					owner.machineId,
					owner.machineRevision
				),
				generation
			);
		} catch (error) {
			reportIfCurrent(error, owner, generation);
		} finally {
			finishAction();
		}
	};

	const close = () => {
		show = false;
	};

	const copyUserCode = async () => {
		if (login.user_code && (await copyToClipboard(login.user_code))) {
			toast.success($i18n.t('Copied to clipboard'));
		}
	};

	$: if (!show) {
		dismissLogin();
	}
	$: if (loginOwner && !ownsSelectedMachine(loginOwner, provider)) {
		show = false;
		dismissLogin();
	}

	onDestroy(() => {
		disposed = true;
		dismissLogin();
	});
</script>

<Modal size="sm" bind:show>
	<div class="px-5 pt-4 pb-5 dark:text-gray-200">
		<div class="flex justify-between items-center pb-2">
			<h1 class="text-lg font-medium font-primary dark:text-gray-100">
				{$i18n.t('Sign in to {{name}}', { name: provider.name })}
			</h1>
			<button class="self-center" aria-label={$i18n.t('Close modal')} on:click={close}>
				<XMark className="size-5" />
			</button>
		</div>

		<p class="text-xs text-gray-500 dark:text-gray-400">
			{#if provider.id === 'claude'}
				{$i18n.t(
					'Use your Claude Pro or Max account. Claude Code keeps the sign-in on the computer it runs on ({{machine}}); Buddy never sees your password or tokens.',
					{ machine: machineName }
				)}
			{:else}
				{$i18n.t(
					'Use your ChatGPT Plus, Pro, or Business account. Codex keeps the sign-in on the computer it runs on ({{machine}}); Buddy never sees your password or tokens.',
					{ machine: machineName }
				)}
			{/if}
		</p>

		<div class="mt-4 flex flex-col gap-3 text-sm">
			{#if login.state === 'idle' || login.state === 'error' || login.state === 'cancelled'}
				{#if login.state === 'error' && login.message}
					<div
						class="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-300"
					>
						{login.message}
					</div>
				{:else if login.message}
					<div
						class="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
					>
						{login.message}
					</div>
				{/if}

				{#if provider.id === 'claude'}
					<button
						class="{primaryButtonClass} self-start"
						type="button"
						disabled={busy}
						on:click={() => startLogin('browser')}
					>
						{$i18n.t('Get sign-in link')}
					</button>
				{:else}
					<div class="flex flex-col gap-2">
						<button
							class="{primaryButtonClass} self-start"
							type="button"
							disabled={busy}
							on:click={() => startLogin('browser')}
						>
							{$i18n.t('Get sign-in link')}
						</button>
						<p class="text-xs text-gray-500 dark:text-gray-400">
							{$i18n.t(
								'The sign-in page returns to the computer running Codex ({{machine}}), so open the link in a browser on that computer. From any other device, use a device code instead.',
								{ machine: machineName }
							)}
						</p>
						<button
							class="{secondaryButtonClass} self-start"
							type="button"
							disabled={busy}
							on:click={() => startLogin('device')}
						>
							{$i18n.t('Use a device code instead')}
						</button>
						<p class="text-xs text-gray-500 dark:text-gray-400">
							{$i18n.t(
								'Works from any device. Device code sign-in must be allowed in ChatGPT under Settings → Security.'
							)}
						</p>
					</div>
				{/if}
			{:else if login.state === 'waiting'}
				{#if login.url}
					<div class="flex flex-col gap-1">
						<div class="text-xs text-gray-500">{$i18n.t('1. Open the sign-in page')}</div>
						{#if signInUrl}
							<a
								class="{primaryButtonClass} self-start"
								href={signInUrl}
								target="_blank"
								rel="noopener noreferrer"
							>
								{$i18n.t('Open {{name}} sign-in', { name: provider.name })}
							</a>
						{:else}
							<div
								class="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-300"
							>
								{$i18n.t(
									'Buddy did not open this sign-in address because it is not HTTPS. Check the CLI on {{machine}}.',
									{ machine: machineName }
								)}
								<span class="mt-1 block break-all font-mono">{login.url}</span>
							</div>
						{/if}
					</div>
				{/if}

				{#if login.user_code}
					<div class="flex flex-col gap-1">
						<div class="text-xs text-gray-500">{$i18n.t('2. Enter this code on that page')}</div>
						<button
							class="self-start rounded-xl bg-gray-50 px-3 py-2 font-mono text-lg tracking-widest dark:bg-gray-850"
							type="button"
							title={$i18n.t('Copy')}
							on:click={copyUserCode}
						>
							{login.user_code}
						</button>
					</div>
				{/if}

				{#if login.needs_code}
					<form class="flex flex-col gap-1" on:submit|preventDefault={submitCode}>
						<label class="text-xs text-gray-500" for="subscription-login-code">
							{$i18n.t('2. After approving, paste the code shown on the page')}
						</label>
						<div class="flex gap-2">
							<input
								id="subscription-login-code"
								class={inputClass}
								bind:value={code}
								autocomplete="off"
								spellcheck="false"
								placeholder={$i18n.t('Paste code')}
							/>
							<button class={primaryButtonClass} type="submit" disabled={busy || !code.trim()}>
								{$i18n.t('Finish')}
							</button>
						</div>
					</form>
				{/if}

				<div class="flex items-center gap-2 text-xs text-gray-500">
					<Spinner className="size-3.5" />
					{login.message ?? $i18n.t('Waiting for you to finish signing in…')}
				</div>
			{/if}
		</div>
	</div>
</Modal>
