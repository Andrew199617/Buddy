<script lang="ts">
	// How much the AI may do in a new chat: chat only, read files, or full access.
	import { getContext } from 'svelte';

	import type {
		RunTargetOptions,
		SubscriptionAccess,
		SubscriptionProviderId
	} from '$lib/apis/subscriptions';
	import { showSettings } from '$lib/stores';
	import { defaultAccess, folderLabel, type RunTargetDraft } from './run-target';

	const i18n: any = getContext('i18n');

	export let draft: RunTargetDraft;
	export let options: RunTargetOptions;
	export let providers: SubscriptionProviderId[] = [];
	export let computerName = '';
	export let onPick: (access: SubscriptionAccess) => void;
	export let onAcknowledge: (folderId: string | null) => void;
	// Inside a menu popup it is one group; on its own, as in the phone sheet, it is the menu.
	export let inMenu = true;
	export let onClose: () => void = () => {};

	const CHOICES: SubscriptionAccess[] = ['chat', 'read', 'full'];

	$: detached = draft.kind === 'detached';
	$: sharedFolder = options.chat_folder?.shared_instructions ? options.chat_folder : null;
	$: locked = sharedFolder !== null && draft.acknowledgedFolderId === null;
	$: aiChooses = folderLabel(draft).kind === 'auto';

	function accessName(access: SubscriptionAccess): string {
		if (access === 'chat') return $i18n.t('Chat only');
		if (access === 'read') return $i18n.t('Read files');
		return $i18n.t('Full access');
	}

	function accessDescription(access: SubscriptionAccess): string {
		if (access === 'chat') return $i18n.t('No files or commands.');
		if (access === 'read') return $i18n.t('Reads files in the folder and changes nothing.');
		return $i18n.t('Edits files and runs commands on {{name}} as your account.', {
			name: computerName
		});
	}

	function planName(provider: SubscriptionProviderId): string {
		if (provider === 'codex') return 'ChatGPT';
		return 'Claude';
	}

	function changeDefault() {
		onClose();
		showSettings.set('machines');
	}

	function acknowledge(event: Event) {
		const ticked = (event.currentTarget as HTMLInputElement).checked;
		if (ticked && sharedFolder) {
			onAcknowledge(sharedFolder.id);
		} else {
			onAcknowledge(null);
		}
	}

	$: firstPlan =
		providers[0] ?? (Object.keys(options.plan_access ?? {})[0] as SubscriptionProviderId);
</script>

<div
	class="flex flex-col gap-0.5 p-1"
	role={inMenu ? 'group' : 'menu'}
	aria-label={$i18n.t('Access')}
>
	{#if detached}
		<p class="px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t("Detached chats can't use files.")}
		</p>
	{/if}
	{#if sharedFolder && !detached}
		<div
			class="mx-1 mb-1 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
		>
			<p>
				{$i18n.t(
					'Someone else can change the instructions in “{{folder}}”. With files on, they could steer the AI on {{name}}.',
					{ folder: sharedFolder.name, name: computerName }
				)}
			</p>
			<label class="mt-1.5 flex min-h-11 items-center gap-2 md:min-h-8">
				<input
					type="checkbox"
					role="menuitemcheckbox"
					aria-checked={draft.acknowledgedFolderId !== null}
					checked={draft.acknowledgedFolderId !== null}
					on:change={acknowledge}
				/>
				{$i18n.t('Use files in this chat anyway')}
			</label>
		</div>
	{/if}
	{#each CHOICES as access}
		<button
			type="button"
			role="menuitemradio"
			aria-checked={draft.access === access}
			disabled={detached || (locked && access !== 'chat')}
			class="flex min-h-11 w-full flex-col items-start justify-center rounded-lg px-2 text-start hover:bg-gray-50 disabled:opacity-40 disabled:hover:bg-transparent dark:hover:bg-gray-800/60 md:min-h-9 {draft.access ===
			access
				? 'bg-gray-50 dark:bg-gray-800/60'
				: ''}"
			on:click={() => onPick(access)}
		>
			<span class="text-[0.8125rem] font-medium">{accessName(access)}</span>
			<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{accessDescription(access)}
			</span>
			{#if access === 'read' && providers.includes('codex')}
				<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('ChatGPT can also read other folders on {{name}}.', { name: computerName })}
				</span>
			{/if}
			{#if access === 'read' && aiChooses && providers.includes('claude')}
				<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Claude reads only inside its start folder.')}
				</span>
			{/if}
		</button>
	{/each}
	{#if firstPlan}
		<button
			type="button"
			role="menuitem"
			class="mt-0.5 min-h-11 rounded-lg px-2 text-start text-xs text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800/60 md:min-h-9 md:text-[0.6875rem]"
			on:click={changeDefault}
		>
			{$i18n.t('Default for {{plan}}: {{access}} · Change default', {
				plan: planName(firstPlan),
				access: accessName(defaultAccess(options, [firstPlan]))
			})}
		</button>
	{/if}
</div>
