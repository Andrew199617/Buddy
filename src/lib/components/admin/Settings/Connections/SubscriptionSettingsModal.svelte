<script lang="ts">
	import { getContext } from 'svelte';

	import type {
		SubscriptionAccess,
		SubscriptionMachine,
		SubscriptionProvider,
		SubscriptionSettings
	} from '$lib/apis/subscriptions';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n: any = getContext('i18n');

	export let show = false;
	export let provider: SubscriptionProvider;
	export let machines: SubscriptionMachine[] = [];
	// Resolves to true when the settings were saved.
	export let onSave: (settings: Partial<SubscriptionSettings>) => Promise<boolean> = async () =>
		true;

	let access: SubscriptionAccess = 'chat';
	let machineId = 'local';
	let workspace = '';
	let cliPath = '';
	let saving = false;

	const inputClass =
		'w-full rounded-xl bg-gray-50 px-3 py-2 text-sm outline-hidden dark:bg-gray-850 placeholder:text-gray-300 dark:placeholder:text-gray-700';

	const getReadDescription = (providerId: string) => {
		if (providerId === 'codex') {
			return $i18n.t(
				"Can run read-only commands. Codex's read-only sandbox does not limit reads to the working folder, so it can read any file your account can. No edits."
			);
		}
		return $i18n.t(
			'Can read and search files in the working folder and the web. No edits or commands.'
		);
	};

	$: accessOptions = [
		{
			value: 'chat',
			label: $i18n.t('Chat only'),
			description: $i18n.t('No access to files or the terminal.')
		},
		{
			value: 'read',
			label: $i18n.t('Read files'),
			description: getReadDescription(provider.id)
		},
		{
			value: 'full',
			label: $i18n.t('Full access'),
			description: $i18n.t(
				'Can edit files and run terminal commands on the machine as you, without asking first.'
			)
		}
	];

	const loadSettings = () => {
		access = provider.settings.access;
		machineId = provider.settings.machine_id;
		workspace = provider.settings.workspace;
		cliPath = provider.settings.cli_path;
	};

	$: if (show) {
		loadSettings();
	}

	const save = async () => {
		saving = true;
		const saved = await onSave({
			access,
			machine_id: machineId,
			workspace: workspace.trim(),
			cli_path: cliPath.trim()
		});
		saving = false;
		if (saved) {
			show = false;
		}
	};
</script>

<Modal size="sm" bind:show>
	<div class="px-5 pt-4 pb-5 dark:text-gray-200">
		<div class="flex justify-between items-center pb-3">
			<h1 class="text-lg font-medium font-primary dark:text-gray-100">
				{$i18n.t('{{name}} subscription', { name: provider.name })}
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

		<form class="flex flex-col gap-4 text-sm" on:submit|preventDefault={save}>
			<div class="flex flex-col gap-1">
				<label class="text-xs text-gray-500" for="subscription-machine">{$i18n.t('Runs on')}</label>
				<select id="subscription-machine" class={inputClass} bind:value={machineId}>
					{#each machines as machine (machine.id)}
						<option value={machine.id}>{machine.name}</option>
					{/each}
				</select>
				<p class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t(
						'The machine where the CLI runs, signs in, and works on files. Add machines under Machines.'
					)}
				</p>
			</div>

			<fieldset class="flex flex-col gap-1.5">
				<legend class="mb-1 text-xs text-gray-500">{$i18n.t('What the model can do')}</legend>
				{#each accessOptions as option}
					<label
						class="flex cursor-pointer gap-2.5 rounded-xl border px-3 py-2 transition {access ===
						option.value
							? 'border-gray-400 dark:border-gray-500'
							: 'border-gray-100 dark:border-gray-850'}"
					>
						<input
							class="mt-1"
							type="radio"
							name="subscription-access"
							value={option.value}
							bind:group={access}
						/>
						<span class="flex flex-col">
							<span class="font-medium">{option.label}</span>
							<span class="text-xs text-gray-500 dark:text-gray-400">{option.description}</span>
						</span>
					</label>
				{/each}
				{#if access === 'full'}
					<p class="text-xs text-amber-700 dark:text-amber-400">
						{$i18n.t(
							'Anyone who can sign in to Buddy as an administrator can then run commands on that machine.'
						)}
					</p>
				{/if}
			</fieldset>

			<div class="flex flex-col gap-1">
				<label class="text-xs text-gray-500" for="subscription-workspace">
					{$i18n.t('Working folder')}
				</label>
				<input
					id="subscription-workspace"
					class={inputClass}
					bind:value={workspace}
					autocomplete="off"
					spellcheck="false"
					placeholder={provider.default_workspace}
				/>
				<p class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t(
						'Where Read files and Full access work, such as a project folder. Chat only uses an empty folder.'
					)}
				</p>
			</div>

			<div class="flex flex-col gap-1">
				<label class="text-xs text-gray-500" for="subscription-cli-path">
					{#if provider.id === 'claude'}
						{$i18n.t('Claude Code path')}
					{:else}
						{$i18n.t('Codex path')}
					{/if}
				</label>
				<input
					id="subscription-cli-path"
					class={inputClass}
					bind:value={cliPath}
					autocomplete="off"
					spellcheck="false"
					placeholder={provider.status?.cli_path ?? $i18n.t('Detected automatically')}
				/>
				<p class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t('Leave empty to find it automatically.')}
				</p>
			</div>

			<div class="flex justify-end">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex items-center gap-2 disabled:opacity-50"
					type="submit"
					disabled={saving}
				>
					{$i18n.t('Save')}
					{#if saving}
						<Spinner className="size-3.5" />
					{/if}
				</button>
			</div>
		</form>
	</div>
</Modal>
