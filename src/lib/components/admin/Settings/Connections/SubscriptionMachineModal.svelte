<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteSubscriptionMachine,
		saveSubscriptionMachine,
		verifySubscriptionMachine,
		type SubscriptionMachine,
		type SubscriptionMachineInfo
	} from '$lib/apis/subscriptions';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n: any = getContext('i18n');

	export let show = false;
	// The machine being edited, or null to add one.
	export let machine: SubscriptionMachine | null = null;
	export let onChanged: (machines: SubscriptionMachine[]) => void = () => {};

	const DEFAULT_URL = 'https://host.docker.internal:8765';

	let name = '';
	let url = '';
	let key = '';
	let info: SubscriptionMachineInfo | null = null;
	let busy = false;

	const inputClass =
		'w-full rounded-xl bg-gray-50 px-3 py-2 text-sm outline-hidden dark:bg-gray-850 placeholder:text-gray-300 dark:placeholder:text-gray-700';
	const secondaryButtonClass =
		'shrink-0 whitespace-nowrap px-3.5 py-1.5 text-sm font-medium rounded-full border border-gray-200 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-850 transition disabled:opacity-50';

	const loadForm = () => {
		name = machine?.name ?? '';
		url = machine?.url ?? DEFAULT_URL;
		key = '';
		info = null;
	};

	$: if (show) {
		loadForm();
	}

	const runAction = async (action: () => Promise<void>) => {
		busy = true;
		try {
			await action();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const verify = () =>
		runAction(async () => {
			info = await verifySubscriptionMachine(localStorage.token, url.trim(), key.trim());
		});

	const save = () =>
		runAction(async () => {
			const result = await saveSubscriptionMachine(localStorage.token, {
				id: machine?.id,
				name: name.trim(),
				url: url.trim(),
				key: key.trim() || undefined
			});
			toast.success($i18n.t('Saved {{name}}', { name: result.machine.name }));
			onChanged(result.machines);
			show = false;
		});

	const remove = () =>
		runAction(async () => {
			if (!machine) {
				return;
			}
			const result = await deleteSubscriptionMachine(localStorage.token, machine.id);
			toast.success($i18n.t('Removed {{name}}', { name: machine.name }));
			onChanged(result.machines);
			show = false;
		});
</script>

<Modal size="sm" bind:show>
	<div class="px-5 pt-4 pb-5 dark:text-gray-200">
		<div class="flex justify-between items-center pb-2">
			<h1 class="text-lg font-medium font-primary dark:text-gray-100">
				{#if machine}
					{$i18n.t('Edit machine')}
				{:else}
					{$i18n.t('Add machine')}
				{/if}
			</h1>
			<button
				class="self-center"
				aria-label={$i18n.t('Close modal')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-5" />
			</button>
		</div>

		<p class="text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t(
				'A machine runs Claude Code and Codex for Buddy, with its own sign-ins, files, and terminal. Start the Buddy Runner on that computer; its key is in ~/.buddy-runner/key.'
			)}
		</p>

		<form class="mt-4 flex flex-col gap-3 text-sm" on:submit|preventDefault={save}>
			<div class="flex flex-col gap-1">
				<label class="text-xs text-gray-500" for="machine-name">{$i18n.t('Name')}</label>
				<input
					id="machine-name"
					class={inputClass}
					bind:value={name}
					placeholder={$i18n.t('My PC')}
					autocomplete="off"
					required
				/>
			</div>

			<div class="flex flex-col gap-1">
				<label class="text-xs text-gray-500" for="machine-url">{$i18n.t('Runner address')}</label>
				<input
					id="machine-url"
					class={inputClass}
					bind:value={url}
					placeholder={DEFAULT_URL}
					autocomplete="off"
					spellcheck="false"
					required
				/>
				<p class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t(
						'Use HTTPS with a certificate trusted by Buddy. In Docker, host.docker.internal reaches the computer running Docker; Buddy must be able to reach the Runner port.'
					)}
				</p>
			</div>

			<div class="flex flex-col gap-1">
				<label class="text-xs text-gray-500" for="machine-key">{$i18n.t('Runner key')}</label>
				<input
					id="machine-key"
					class={inputClass}
					type="password"
					bind:value={key}
					placeholder={machine ? $i18n.t('Leave empty to keep the saved key') : ''}
					autocomplete="off"
					spellcheck="false"
				/>
			</div>

			{#if info}
				<div
					class="rounded-xl bg-gray-50 px-3 py-2 text-xs text-gray-600 dark:bg-gray-850 dark:text-gray-300"
				>
					{$i18n.t('Connected to {{host}} ({{platform}})', {
						host: info.hostname,
						platform: info.platform
					})}
				</div>
			{/if}

			<div class="flex items-center justify-between gap-2 pt-1">
				<div>
					{#if machine}
						<button
							class="px-1 py-1.5 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:underline transition"
							type="button"
							disabled={busy}
							on:click={remove}
						>
							{$i18n.t('Remove')}
						</button>
					{/if}
				</div>
				<div class="flex items-center gap-2">
					<button
						class={secondaryButtonClass}
						type="button"
						disabled={busy || !url.trim() || !key.trim()}
						on:click={verify}
					>
						{$i18n.t('Verify')}
					</button>
					<button
						class="flex items-center gap-2 px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
						type="submit"
						disabled={busy}
					>
						{$i18n.t('Save')}
						{#if busy}
							<Spinner className="size-3.5" />
						{/if}
					</button>
				</div>
			</div>
		</form>
	</div>
</Modal>
