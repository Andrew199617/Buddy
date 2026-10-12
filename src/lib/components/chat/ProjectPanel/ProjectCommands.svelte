<script lang="ts">
	// Runs commands in the project folder from the chat side panel. Each run is
	// one program with its own arguments; there is no shell. The computer must
	// allow commands in its Buddy app, and the person confirms the risk first.
	import { getContext, onDestroy } from 'svelte';
	import { beforeNavigate } from '$app/navigation';

	import type {
		CompanionGrant,
		CompanionTerminal,
		CompanionWorkspace,
		WorkspaceClient
	} from '$lib/apis/companion';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		checkTerminalReply,
		isTransientFailure,
		mayHaveRun,
		parseArguments,
		pollDelay,
		visibleOutput
	} from './project-session';

	const i18n: any = getContext('i18n');

	export let client: WorkspaceClient;
	export let grant: CompanionGrant;
	// The project folder, '/'-separated and relative to the shared folder.
	export let root: string;
	export let computerName: string;
	export let onFailure: (cause: unknown) => string;

	let accepted = false;
	let workspace: CompanionWorkspace | null = null;
	let terminal: CompanionTerminal | null = null;
	let executable = '';
	let argumentText = '';
	let error = '';
	let notice = '';
	let starting = false;
	let running = false;
	let stopping = false;
	// The run request got no clear answer, so the command may or may not be running.
	let runUnconfirmed = false;
	// A stop got no clear answer, so the command may still be running.
	let stopUnconfirmed = false;
	let pollFailures = 0;
	let pollMessage = '';
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let terminalGeneration = 0;
	let disposed = false;

	$: allowed = grant.capabilities.execute;
	$: busy = starting || running || stopping;
	$: blocked = runUnconfirmed || stopUnconfirmed;
	$: canRun =
		terminal !== null &&
		terminal.status !== 'running' &&
		terminal.status !== 'closed' &&
		executable.trim() !== '' &&
		!busy &&
		!blocked;
	$: output = visibleOutput(terminal?.output ?? '');

	function isCurrent(generation: number, terminalId: string): boolean {
		return !disposed && generation === terminalGeneration && terminal?.id === terminalId;
	}

	function stopPolling() {
		if (pollTimer !== null) clearTimeout(pollTimer);
		pollTimer = null;
	}

	function showError(cause: unknown) {
		error = onFailure(cause);
	}

	function forgetTerminal() {
		terminalGeneration += 1;
		stopPolling();
		terminal = null;
		// Requests from the old session no longer clear their own busy flags.
		running = false;
		stopping = false;
		runUnconfirmed = false;
		stopUnconfirmed = false;
		pollFailures = 0;
		pollMessage = '';
	}

	/** Opens a command session in the project folder. */
	async function startSession() {
		if (!allowed || !accepted || busy || terminal !== null) return;
		const generation = ++terminalGeneration;
		starting = true;
		error = '';
		notice = '';
		try {
			if (workspace === null) {
				workspace = await client.createWorkspace(grant.id, root);
				if (disposed || generation !== terminalGeneration) return;
			}
			const created = await client.createTerminal(workspace.id);
			if (disposed || generation !== terminalGeneration) {
				void client.deleteTerminal(created.id).catch(() => {});
				return;
			}
			terminal = created;
		} catch (cause) {
			if (!disposed && generation === terminalGeneration) showError(cause);
		} finally {
			if (!disposed && generation === terminalGeneration) starting = false;
		}
	}

	function schedulePoll(generation: number, terminalId: string, delay = pollDelay(0)) {
		stopPolling();
		if (!isCurrent(generation, terminalId) || stopping || stopUnconfirmed) return;
		if (terminal?.status !== 'running' && !runUnconfirmed) return;
		pollTimer = setTimeout(() => {
			pollTimer = null;
			void checkStatus(generation, terminalId);
		}, delay);
	}

	function handleStatus(generation: number, terminalId: string, reply: CompanionTerminal) {
		terminal = checkTerminalReply(reply, terminalId, workspace?.id ?? '');
		pollFailures = 0;
		const settled = reply.status === 'ready' || reply.status === 'exited';
		if (runUnconfirmed && settled) {
			// A finished or idle session cannot show whether the unconfirmed run happened.
			pollMessage = $i18n.t(
				'Buddy could not confirm whether the command ran. Stop the session before starting another.'
			);
			error = pollMessage;
		} else {
			runUnconfirmed = false;
			if (error === pollMessage) error = '';
			pollMessage = '';
		}
		schedulePoll(generation, terminalId);
	}

	async function checkStatus(generation: number, terminalId: string) {
		if (!isCurrent(generation, terminalId)) return;
		try {
			const reply = await client.getTerminal(terminalId);
			if (!isCurrent(generation, terminalId)) return;
			handleStatus(generation, terminalId, reply);
		} catch (cause) {
			if (!isCurrent(generation, terminalId)) return;
			showError(cause);
			if (!isTransientFailure(cause) || stopping || stopUnconfirmed) return;
			pollFailures = Math.min(pollFailures + 1, 5);
			if (runUnconfirmed) {
				pollMessage = $i18n.t(
					'Buddy could not confirm the command yet. Run stays off; check again or stop the session.'
				);
			} else {
				pollMessage = $i18n.t('The command status is not available right now. Trying again…');
			}
			error = pollMessage;
			schedulePoll(generation, terminalId, pollDelay(pollFailures));
		}
	}

	async function checkNow() {
		const current = terminal;
		if (!current || !runUnconfirmed || busy || stopUnconfirmed) return;
		const generation = ++terminalGeneration;
		running = true;
		try {
			stopPolling();
			await checkStatus(generation, current.id);
		} finally {
			if (isCurrent(generation, current.id)) running = false;
		}
	}

	async function run() {
		const current = terminal;
		const project = workspace;
		if (!current || !project || !canRun) return;
		const args = parseArguments(argumentText);
		const generation = ++terminalGeneration;
		stopPolling();
		error = '';
		notice = '';
		running = true;
		try {
			const reply = await client.runCommand(current.id, executable.trim(), args);
			if (!isCurrent(generation, current.id)) return;
			terminal = checkTerminalReply(reply, current.id, project.id);
			schedulePoll(generation, current.id);
		} catch (cause) {
			if (!isCurrent(generation, current.id)) return;
			showError(cause);
			if (!mayHaveRun(cause) || stopping || stopUnconfirmed) return;
			runUnconfirmed = true;
			pollMessage = $i18n.t(
				'Buddy did not get an answer about the command. Checking whether it started…'
			);
			error = pollMessage;
			await checkStatus(generation, current.id);
		} finally {
			if (isCurrent(generation, current.id)) running = false;
		}
	}

	/** Resolves once the computer confirms the session stopped. */
	async function stop() {
		const current = terminal;
		if (!current || stopping) return;
		const generation = terminalGeneration;
		stopPolling();
		stopping = true;
		error = '';
		try {
			await client.deleteTerminal(current.id);
			if (!isCurrent(generation, current.id)) return;
			forgetTerminal();
			notice = $i18n.t('The command session stopped.');
		} catch (cause) {
			if (!isCurrent(generation, current.id)) return;
			stopUnconfirmed = true;
			error = $i18n.t(
				'{{name}} did not confirm the stop, so the command may still be running. Stop again, or leave this chat to end the session. {{reason}}',
				{ name: computerName, reason: onFailure(cause) }
			);
		} finally {
			if (!disposed && generation === terminalGeneration) stopping = false;
		}
	}

	// Leaving the chat ends the session, which stops a running command, so ask first.
	beforeNavigate((navigation) => {
		const commandRunning = terminal?.status === 'running' || runUnconfirmed;
		if (!commandRunning) return;
		if (navigation.type === 'leave') {
			// Closing or reloading the page: the browser asks.
			navigation.cancel();
			return;
		}
		const question = $i18n.t(
			'A command is still running on {{name}}. Leaving this chat stops it.',
			{
				name: computerName
			}
		);
		if (!confirm(question)) navigation.cancel();
	});

	onDestroy(() => {
		disposed = true;
		stopPolling();
	});
</script>

<div
	class="flex h-full min-h-0 flex-col gap-2 overflow-y-auto overscroll-contain px-3 pb-[max(env(safe-area-inset-bottom),0.75rem)]"
>
	{#if !allowed}
		<p class="text-xs text-gray-600 dark:text-gray-300">
			{$i18n.t(
				'Commands are off on {{name}}. Turn on Allow running commands in the Buddy app there to run them here.',
				{ name: computerName }
			)}
		</p>
	{:else}
		{#if error}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
		{/if}
		{#if notice}
			<p role="status" class="text-xs text-gray-600 dark:text-gray-300">{notice}</p>
		{/if}

		{#if terminal === null}
			<p class="text-xs text-gray-600 dark:text-gray-300">
				{$i18n.t(
					'Commands run on {{name}} as your account, starting in the project folder. They can reach anything your account can, not only this folder.',
					{ name: computerName }
				)}
			</p>
			<label class="flex min-h-11 cursor-pointer items-center gap-2 text-xs md:min-h-0">
				<input type="checkbox" class="mt-0.5" bind:checked={accepted} disabled={busy} />
				<span
					>{$i18n.t('I understand. Let me run commands on {{name}}.', { name: computerName })}</span
				>
			</label>
			<button
				type="button"
				class="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-9"
				disabled={!accepted || busy}
				on:click={startSession}
			>
				{$i18n.t('Start a command session')}
				{#if starting}<Spinner className="size-3.5" />{/if}
			</button>
		{:else}
			<form class="flex flex-col gap-2" on:submit|preventDefault={run}>
				<label class="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('Program')}
					<input
						class="min-h-11 rounded-xl bg-gray-50 px-3 font-mono text-base outline-hidden dark:bg-gray-850 md:min-h-9 md:text-sm"
						bind:value={executable}
						placeholder="npm"
						dir="ltr"
						autocapitalize="off"
						autocorrect="off"
						spellcheck="false"
						autocomplete="off"
						disabled={busy || blocked || terminal.status === 'running'}
					/>
				</label>
				<label class="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('Arguments, one per line')}
					<textarea
						class="min-h-20 resize-y rounded-xl bg-gray-50 px-3 py-2 font-mono text-base outline-hidden dark:bg-gray-850 md:text-sm"
						bind:value={argumentText}
						placeholder={'run\ntest'}
						dir="ltr"
						autocapitalize="off"
						spellcheck="false"
						autocomplete="off"
						disabled={busy || blocked || terminal.status === 'running'}
					></textarea>
				</label>
				<div class="flex flex-wrap gap-2">
					<button
						type="submit"
						class="flex min-h-11 items-center gap-2 rounded-xl bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900 md:min-h-9"
						disabled={!canRun}
					>
						{$i18n.t('Run')}
						{#if running}<Spinner className="size-3.5" />{/if}
					</button>
					{#if runUnconfirmed}
						<button
							type="button"
							class="min-h-11 rounded-xl px-3 text-sm hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-850 md:min-h-9"
							disabled={busy || stopUnconfirmed}
							on:click={checkNow}
						>
							{$i18n.t('Check again')}
						</button>
					{/if}
					<button
						type="button"
						class="flex min-h-11 items-center gap-2 rounded-xl px-3 text-sm hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-850 md:min-h-9"
						disabled={stopping}
						on:click={stop}
					>
						{$i18n.t('Stop session')}
						{#if stopping}<Spinner className="size-3.5" />{/if}
					</button>
				</div>
			</form>

			<div class="flex items-center justify-between text-[0.6875rem] text-gray-500">
				<span role="status">
					{#if terminal.status === 'running'}
						{$i18n.t('Running')}
					{:else if terminal.status === 'exited' && terminal.exitCode !== null}
						{$i18n.t('Finished with exit code {{code}}', { code: terminal.exitCode })}
					{:else if terminal.status === 'closed'}
						{$i18n.t('Session closed')}
					{:else}
						{$i18n.t('Ready')}
					{/if}
				</span>
				<span>{$i18n.t('Latest 256 KiB')}</span>
			</div>
			<pre
				class="min-h-40 flex-1 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-gray-50 p-2.5 font-mono text-xs dark:bg-gray-850"
				aria-label={$i18n.t('Command output')}
				dir="ltr">{output || $i18n.t('Output appears here.')}</pre>
		{/if}
	{/if}
</div>
