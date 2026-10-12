<script lang="ts">
	// Where a started chat runs, read-only, with what's wrong if anything, and "New chat here".
	import { getContext } from 'svelte';

	import type { ChatRunTargetDetails, SubscriptionAccess } from '$lib/apis/subscriptions';
	import { lastSegment } from '$lib/components/projectFolders/project-folders';

	const i18n: any = getContext('i18n');

	export let details: ChatRunTargetDetails;
	// False for a chat from before targets: this is where its next plan message runs.
	export let bound = true;
	export let onNewChatHere: () => void;

	function accessName(access: SubscriptionAccess): string {
		if (access === 'chat') return $i18n.t('Chat only');
		if (access === 'read') return $i18n.t('Read files');
		return $i18n.t('Full access');
	}

	$: computerName = details.computer?.name ?? '';
	$: folderName = folderText(details);

	function folderText(target: ChatRunTargetDetails): string {
		if (target.folder_mode === 'path' && target.path) return lastSegment(target.path);
		return $i18n.t('a folder the AI chooses');
	}

	function summary(target: ChatRunTargetDetails): string {
		if (target.kind === 'detached') {
			return $i18n.t('This chat runs Detached: chat only, with no computer, folder or files.');
		}
		const values = {
			computer: computerName,
			folder: folderName,
			access: accessName(target.access)
		};
		if (!bound) {
			return $i18n.t('This chat will run on {{computer}}, in {{folder}}, with {{access}}.', values);
		}
		return $i18n.t('This chat runs on {{computer}}, in {{folder}}, with {{access}}.', values);
	}

	function problemText(target: ChatRunTargetDetails): string {
		if (target.problem === 'removed') {
			return $i18n.t('{{name}} was removed from Buddy.', { name: computerName });
		}
		if (target.problem === 'reset') {
			return $i18n.t('{{name}} was set up again after this chat started.', { name: computerName });
		}
		if (target.problem === 'offline') {
			return $i18n.t('{{name}} is offline. Open the Buddy app on it.', { name: computerName });
		}
		if (target.problem === 'server_off') return $i18n.t('Buddy server is turned off for AI plans.');
		return '';
	}

	$: problem = problemText(details);
</script>

<div class="flex max-w-[22rem] flex-col gap-2 p-2 text-[0.8125rem]">
	<p>{summary(details)}</p>
	{#if details.note_message}
		<p class="text-amber-700 dark:text-amber-400">{details.note_message}</p>
	{/if}
	{#if problem}
		<p class="text-red-700 dark:text-red-400" role="alert">{problem}</p>
	{/if}
	<p class="text-[0.6875rem] text-gray-500 dark:text-gray-400">
		{$i18n.t('Where a chat runs is set when it starts.')}
	</p>
	<button
		type="button"
		class="min-h-11 self-start rounded-lg bg-gray-100 px-3 text-[0.8125rem] font-medium hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 md:min-h-8"
		on:click={onNewChatHere}
	>
		{$i18n.t('New chat here')}
	</button>
</div>
