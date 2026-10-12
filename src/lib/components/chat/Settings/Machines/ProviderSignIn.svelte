<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteSubscriptionCredential,
		saveClaudePlanToken,
		saveSubscriptionApiKey,
		useMachineSignIn,
		useStoredSubscriptionCredential,
		type StoredSubscriptionCredential,
		type SubscriptionCredentialMode,
		type SubscriptionProvider
	} from '$lib/apis/subscriptions';
	import { getDesktop } from '$lib/desktop/bridge';

	const i18n: any = getContext('i18n');

	export let provider: SubscriptionProvider;
	export let policy: { plan_sign_in: boolean; api_keys: boolean };
	// Reloads the providers after the sign-in changed.
	export let onChanged: () => Promise<void> = async () => {};

	const desktop = getDesktop();

	let selectedMode: SubscriptionCredentialMode = 'machine';
	let observedMode: SubscriptionCredentialMode | null = null;
	let secretDraft = '';
	let busy = false;

	$: savedMode = provider.credential?.mode ?? 'machine';
	$: stored = provider.credential?.stored ?? {};
	// Follow the saved choice whenever the provider reloads with a different one.
	$: if (savedMode !== observedMode) {
		observedMode = savedMode;
		selectedMode = savedMode;
		secretDraft = '';
	}
	$: isClaude = provider.id === 'claude';
	$: cliName = isClaude ? 'Claude Code' : 'Codex';
	$: apiKeyLabel = isClaude ? $i18n.t('Anthropic API key') : $i18n.t('OpenAI API key');
	$: storedForSelection = selectedMode === 'machine' ? null : (stored[selectedMode] ?? null);
	let secretPlaceholder = '';
	$: if (storedForSelection) {
		secretPlaceholder = $i18n.t('Paste a new one to replace it');
	} else if (selectedMode === 'plan_token') {
		secretPlaceholder = 'sk-ant-oat01-…';
	} else {
		secretPlaceholder = $i18n.t('Paste your key');
	}

	const formatDate = (seconds: number) =>
		new Date(seconds * 1000).toLocaleDateString(undefined, {
			year: 'numeric',
			month: 'short',
			day: 'numeric'
		});

	const describeStored = (entry: StoredSubscriptionCredential) => {
		const parts = [$i18n.t('Saved {{fingerprint}}', { fingerprint: entry.fingerprint ?? '' })];
		if (entry.expires_at) {
			parts.push($i18n.t('expires {{date}}', { date: formatDate(entry.expires_at) }));
		}
		return parts.join(' · ');
	};

	const run = async (action: () => Promise<unknown>, success: string) => {
		if (busy) return;
		busy = true;
		try {
			await action();
			toast.success(success);
			await onChanged();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const chooseMode = async (mode: SubscriptionCredentialMode) => {
		selectedMode = mode;
		secretDraft = '';
		if (mode === savedMode) return;
		if (mode === 'machine') {
			await run(
				() => useMachineSignIn(localStorage.token, provider.id),
				$i18n.t('{{name}} now uses the sign-in on your computer', { name: provider.name })
			);
			return;
		}
		// A stored token or key is used again at once; otherwise the user pastes one first.
		if (stored[mode]) {
			await run(
				() => useStoredSubscriptionCredential(localStorage.token, provider.id, mode),
				$i18n.t('{{name}} sign-in saved', { name: provider.name })
			);
		}
	};

	const saveSecret = async () => {
		const secret = secretDraft.trim();
		if (!secret) return;
		if (selectedMode === 'plan_token') {
			await run(
				() => saveClaudePlanToken(localStorage.token, secret),
				$i18n.t('Claude token saved')
			);
		} else {
			await run(
				() => saveSubscriptionApiKey(localStorage.token, provider.id, secret),
				$i18n.t('{{label}} saved', { label: apiKeyLabel })
			);
		}
		secretDraft = '';
	};

	const removeStored = async (kind: 'plan_token' | 'api_key') => {
		await run(
			() => deleteSubscriptionCredential(localStorage.token, provider.id, kind),
			$i18n.t('Removed')
		);
	};

	const openTokenSetup = async () => {
		try {
			await desktop?.openClaudeTokenSetup();
		} catch (error) {
			toast.error(`${error}`);
		}
	};
</script>

<fieldset class="mt-2 flex flex-col gap-1.5" disabled={busy}>
	<legend class="mb-1 text-xs text-gray-500 dark:text-gray-400">
		{$i18n.t('How {{name}} signs in', { name: provider.name })}
	</legend>

	{#if policy.plan_sign_in}
		<label
			class="flex min-h-11 cursor-pointer items-start gap-2.5 rounded-xl px-1 py-1.5 md:min-h-0"
		>
			<input
				class="mt-1"
				type="radio"
				name="sign-in-{provider.id}"
				checked={selectedMode === 'machine'}
				on:change={() => chooseMode('machine')}
			/>
			<span class="flex flex-col">
				<span>{$i18n.t("Your computer's sign-in")}</span>
				<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Uses the {{cli}} sign-in on the computer chosen above, with your plan.', {
						cli: cliName
					})}
				</span>
			</span>
		</label>

		{#if isClaude}
			<label
				class="flex min-h-11 cursor-pointer items-start gap-2.5 rounded-xl px-1 py-1.5 md:min-h-0"
			>
				<input
					class="mt-1"
					type="radio"
					name="sign-in-{provider.id}"
					checked={selectedMode === 'plan_token'}
					on:change={() => chooseMode('plan_token')}
				/>
				<span class="flex flex-col">
					<span>{$i18n.t('Claude token, for all your computers')}</span>
					<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
						{$i18n.t(
							'Sign in once with your Claude plan: a token from claude setup-token lasts a year and works on every computer you connect.'
						)}
					</span>
				</span>
			</label>
		{/if}
	{/if}

	{#if policy.api_keys}
		<label
			class="flex min-h-11 cursor-pointer items-start gap-2.5 rounded-xl px-1 py-1.5 md:min-h-0"
		>
			<input
				class="mt-1"
				type="radio"
				name="sign-in-{provider.id}"
				checked={selectedMode === 'api_key'}
				on:change={() => chooseMode('api_key')}
			/>
			<span class="flex flex-col">
				<span>{apiKeyLabel}</span>
				<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Your own key, billed per use by the provider, on every computer you connect.')}
				</span>
			</span>
		</label>
	{/if}

	{#if selectedMode !== 'machine'}
		<div class="ml-6 flex flex-col gap-1.5">
			{#if storedForSelection}
				<div
					class="flex flex-wrap items-center gap-x-2 text-[0.6875rem] text-gray-500 dark:text-gray-400"
				>
					<span>{describeStored(storedForSelection)}</span>
					<button
						type="button"
						class="min-h-11 rounded-lg px-2 text-xs underline-offset-4 hover:underline md:min-h-0"
						on:click={() => removeStored(selectedMode === 'plan_token' ? 'plan_token' : 'api_key')}
					>
						{$i18n.t('Remove')}
					</button>
				</div>
				{#if storedForSelection.expires_soon}
					<p class="text-[0.6875rem] text-amber-700 dark:text-amber-400">
						{$i18n.t('This token expires soon. Create a new one and paste it here.')}
					</p>
				{/if}
			{/if}

			{#if selectedMode === 'plan_token' && desktop}
				<button
					type="button"
					class="min-h-11 self-start rounded-lg px-2 text-xs font-medium transition hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
					on:click={openTokenSetup}
				>
					{$i18n.t('Open a terminal to create a token')}
				</button>
			{:else if selectedMode === 'plan_token'}
				<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t(
						'On a computer with Claude Code, run claude setup-token in a terminal, approve it in the browser, and paste the token it prints.'
					)}
				</p>
			{/if}

			<form class="flex flex-col gap-1.5 sm:flex-row" on:submit|preventDefault={saveSecret}>
				<label class="sr-only" for="secret-{provider.id}">
					{selectedMode === 'plan_token' ? $i18n.t('Claude token') : apiKeyLabel}
				</label>
				<input
					id="secret-{provider.id}"
					class="min-h-11 w-full rounded-xl bg-gray-50 px-3 py-2 text-base outline-hidden dark:bg-gray-850 md:min-h-0 md:text-sm"
					type="password"
					autocomplete="off"
					autocapitalize="off"
					spellcheck="false"
					placeholder={secretPlaceholder}
					bind:value={secretDraft}
				/>
				<button
					type="submit"
					class="min-h-11 shrink-0 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white transition disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-0 md:py-2"
					disabled={!secretDraft.trim() || busy}
				>
					{$i18n.t('Save')}
				</button>
			</form>
		</div>
	{/if}
</fieldset>
