<script lang="ts">
	// Opens a chat folder's project folder on one Buddy app computer, through
	// Buddy, and shows its files and commands. The computer still decides what
	// is shared and whether editing and commands are allowed.
	import { getContext, onDestroy, onMount } from 'svelte';

	import { CompanionError, type CompanionGrant } from '$lib/apis/companion';
	import { RelayCompanionClient } from '$lib/apis/companion-relay';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { grantContaining, lastSegment } from '$lib/components/projectFolders/project-folders';
	import ProjectCommands from './ProjectCommands.svelte';
	import ProjectFiles from './ProjectFiles.svelte';
	import { isReadableGrant, type ProjectTarget } from './project-session';

	const i18n: any = getContext('i18n');

	export let target: ProjectTarget;

	type Phase = 'opening' | 'ready' | 'not_shared' | 'failed' | 'ended';

	let client: RelayCompanionClient | null = null;
	let phase: Phase = 'opening';
	let message = '';
	let grant: CompanionGrant | null = null;
	let root = '';
	let view: 'files' | 'commands' = 'files';
	let disposed = false;

	$: computerName = target.computer.name;
	$: projectName = lastSegment(target.path);

	function describeFailure(cause: unknown): string {
		if (cause instanceof Error && cause.name === 'AbortError') {
			return $i18n.t('{{name}} did not answer in time. Try again.', { name: computerName });
		}
		if (cause instanceof TypeError) {
			return $i18n.t('Cannot reach Buddy. Check your connection and try again.');
		}
		if (cause instanceof Error) return cause.message;
		return $i18n.t('The request to {{name}} failed.', { name: computerName });
	}

	function endConnection(reason: string) {
		const ended = client;
		client = null;
		grant = null;
		phase = 'ended';
		message = reason;
		void ended?.disconnect().catch(() => {});
	}

	function signInEnded(cause: unknown): boolean {
		return cause instanceof CompanionError && cause.status === 401;
	}

	/** Shown by the files and commands views; a sign-in that ended closes the connection. */
	function handleFailure(cause: unknown): string {
		if (signInEnded(cause)) {
			const reason = $i18n.t('Your Buddy sign-in ended. Sign in again, then open the project.');
			endConnection(reason);
			return reason;
		}
		return describeFailure(cause);
	}

	async function open() {
		const opened = new RelayCompanionClient(target.computer.machine_id, localStorage.token);
		client = opened;
		phase = 'opening';
		message = '';
		grant = null;
		try {
			const grants = await opened.getGrants();
			if (disposed || client !== opened) return;
			const match = grantContaining(
				grants.filter(isReadableGrant),
				target.path,
				target.computer.platform
			);
			if (!match) {
				phase = 'not_shared';
				return;
			}
			grant = match.grant;
			root = match.relative;
			phase = 'ready';
		} catch (cause) {
			if (disposed || client !== opened) return;
			if (signInEnded(cause)) {
				handleFailure(cause);
				return;
			}
			phase = 'failed';
			message = describeFailure(cause);
		}
	}

	function reopen() {
		const previous = client;
		client = null;
		void previous?.disconnect().catch(() => {});
		void open();
	}

	/** Leaving or reloading the page skips onDestroy, so its session on the computer ends here too. */
	function endOnPageHide() {
		if (!client) return;
		endConnection(
			$i18n.t('This page was left, so its session on {{name}} ended.', { name: computerName })
		);
	}

	onMount(() => {
		void open();
		window.addEventListener('pagehide', endOnPageHide);
		return () => window.removeEventListener('pagehide', endOnPageHide);
	});

	onDestroy(() => {
		disposed = true;
		// Ending the session also stops any command still running there.
		void client?.disconnect().catch(() => {});
	});
</script>

<div class="flex h-full min-h-0 flex-col">
	{#if phase === 'opening'}
		<div class="flex items-center gap-2 px-3 py-2 text-xs text-gray-500">
			<Spinner className="size-3.5" />
			{$i18n.t('Opening {{name}} on {{computer}}…', { name: projectName, computer: computerName })}
		</div>
	{:else if phase === 'not_shared'}
		<div class="flex flex-col gap-2 px-3 py-2 text-xs text-gray-600 dark:text-gray-300">
			<p>
				{$i18n.t(
					'To browse it here, share this folder or one that contains it in the Buddy app on {{computer}}. Claude and ChatGPT can still work in it.',
					{ computer: computerName }
				)}
			</p>
			<button
				type="button"
				class="min-h-11 self-start rounded-lg px-3 font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
				on:click={reopen}
			>
				{$i18n.t('Try again')}
			</button>
		</div>
	{:else if phase === 'failed' || phase === 'ended'}
		<div class="flex flex-col gap-2 px-3 py-2 text-xs">
			<p role="alert" class="text-red-600 dark:text-red-400">{message}</p>
			<button
				type="button"
				class="min-h-11 self-start rounded-lg px-3 font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
				on:click={reopen}
			>
				{$i18n.t('Open again')}
			</button>
		</div>
	{:else if client && grant}
		<div class="flex shrink-0 gap-1 px-2 pb-2" role="tablist" aria-label={$i18n.t('Project')}>
			<button
				type="button"
				role="tab"
				aria-selected={view === 'files'}
				class="min-h-11 flex-1 rounded-lg text-sm md:min-h-8 {view === 'files'
					? 'bg-gray-100 text-gray-800 dark:bg-gray-850 dark:text-gray-100'
					: 'text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-850'}"
				on:click={() => (view = 'files')}
			>
				{$i18n.t('Files')}
			</button>
			<button
				type="button"
				role="tab"
				aria-selected={view === 'commands'}
				class="min-h-11 flex-1 rounded-lg text-sm md:min-h-8 {view === 'commands'
					? 'bg-gray-100 text-gray-800 dark:bg-gray-850 dark:text-gray-100'
					: 'text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-850'}"
				on:click={() => (view = 'commands')}
			>
				{$i18n.t('Commands')}
			</button>
		</div>

		<!-- Both views stay open, so switching keeps an open file and a running command. -->
		{#key client}
			<div class="min-h-0 flex-1" class:hidden={view !== 'files'}>
				<ProjectFiles
					{client}
					{grant}
					{root}
					{projectName}
					{computerName}
					onFailure={handleFailure}
				/>
			</div>
			<div class="min-h-0 flex-1" class:hidden={view !== 'commands'}>
				<ProjectCommands {client} {grant} {root} {computerName} onFailure={handleFailure} />
			</div>
		{/key}
	{/if}
</div>
