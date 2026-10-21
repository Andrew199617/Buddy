<script lang="ts">
	// The workers card under an Ultra reply's status: each worker the orchestrator
	// started, with the sub-workers it started under it, what each one is doing, and
	// a Stop for each one still running. The card stays open while workers run, also
	// after the reply has ended, and collapses to what the reply used once they have
	// all ended. On desktop a row opens in place to show the worker's report; on
	// phones the rows are 44px tall and each opens a bottom sheet.
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import equal from 'fast-deep-equal';

	import { getChatById } from '$lib/apis/chats';
	import { stopChatWorker, WorkerNotRunningError } from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import CheckCircle from '$lib/components/icons/CheckCircle.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { mobile } from '$lib/stores';

	import RunTargetSheet from '../RunTarget/RunTargetSheet.svelte';
	import {
		canStopWorkers,
		formatElapsed,
		newlyEnded,
		runningWorkers,
		savedReplyWorkers,
		savedWorkers,
		stopControl,
		stopFailedMessage,
		workerDetails,
		workerLine,
		workerPill,
		workerRows,
		workersCardOpens,
		workersCardPhase,
		workersCardTitle,
		workersElapsed,
		workersEndedAnnouncement,
		workerStateLabel,
		type StopControl,
		type StopRequest,
		type WorkerItem,
		type WorkersCardPhase,
		type WorkersSnapshot
	} from '../Ultra/ultra';

	const i18n: any = getContext('i18n');

	// The reply's message.workers snapshot, as the server sends and saves it.
	export let workers: unknown;
	// Whether the reply's own text has ended. Its workers may still be running.
	export let replyDone = false;
	export let chatId = '';
	export let messageId = '';
	// Whether this is the person's own chat. Only its owner may stop its workers.
	export let ownChat = false;
	// The reply's model, which workers without a model of their own run on.
	export let chatModelName: string | null = null;

	// How long the live region stays quiet after it has spoken.
	const ANNOUNCE_EVERY_MS = 5000;

	// The message is copied whenever any of it changes, such as each streamed word,
	// so the card follows only snapshots that differ.
	let snapshot: unknown = null;
	$: if (!equal(workers, snapshot)) snapshot = workers;

	// The reply's saved workers, read again when Stop found a worker no longer running,
	// in case the chat missed the snapshot that ended it. Shown until the next snapshot
	// comes, which is at least as new.
	let readAgain: WorkersSnapshot | null = null;
	let readAgainReplaces: unknown = null;

	$: items = savedWorkers(shownSnapshot(snapshot, readAgain, readAgainReplaces));
	$: rows = workerRows(items);
	$: running = runningWorkers(items);
	$: phase = workersCardPhase(items, replyDone);

	// Seconds since 1970, like the snapshot's times. It ticks while workers run.
	let now = nowInSeconds();
	let clock: ReturnType<typeof setInterval> | undefined;
	$: keepTime(running > 0);
	$: title = workersCardTitle($i18n, items, phase, now);
	$: elapsed = elapsedText(phase, items, now);

	// The card opens and closes with its phase; in between, the person decides.
	let open = false;
	let openedFor: WorkersCardPhase | null = null;
	$: if (phase !== openedFor) {
		openedFor = phase;
		open = workersCardOpens(phase);
	}

	$: canStop = canStopWorkers(ownChat, chatId);
	let stopRequests: Record<string, StopRequest> = {};

	let coarsePointer = false;
	// The phone layout, as the composer decides it: a narrow window or a touch screen.
	$: touchLayout = $mobile || coarsePointer;
	// Desktop rows open in place; phone rows open a sheet.
	let expandedRows: string[] = [];
	let showSheet = false;
	let sheetWorkerId: string | null = null;
	$: sheetWorker = items.find((item) => item.id === sheetWorkerId) ?? null;
	$: sheetTitle = sheetWorker?.label ?? $i18n.t('Worker');
	$: if (!touchLayout) showSheet = false;

	// The live region names workers as they end, a few at a time.
	let announcement = '';
	let earlierItems: WorkerItem[] | null = null;
	let unannounced: WorkerItem[] = [];
	let announceTimer: ReturnType<typeof setTimeout> | undefined;
	let lastAnnouncedAt = 0;
	$: noteEndedWorkers(items);

	$: listId = `ultra-workers-${messageId}`;

	let headerSize = '';
	let rowSize = '';
	let stopSize = '';
	$: if (touchLayout) {
		headerSize = 'min-h-11';
		rowSize = 'min-h-11';
		stopSize = 'min-h-11 min-w-11 px-3 text-sm';
	} else {
		headerSize = 'min-h-9';
		rowSize = 'min-h-9';
		stopSize = 'h-7 px-2.5 text-xs';
	}

	function nowInSeconds(): number {
		return Date.now() / 1000;
	}

	function shownSnapshot(
		fromChat: unknown,
		again: WorkersSnapshot | null,
		replaced: unknown
	): unknown {
		if (again !== null && replaced === fromChat) return again;
		return fromChat;
	}

	function keepTime(workersRunning: boolean) {
		now = nowInSeconds();
		if (workersRunning && clock === undefined) {
			clock = setInterval(() => {
				now = nowInSeconds();
			}, 1000);
		}
		if (!workersRunning && clock !== undefined) {
			clearInterval(clock);
			clock = undefined;
		}
	}

	function elapsedText(cardPhase: WorkersCardPhase, workerItems: WorkerItem[], at: number) {
		// A finished card's title already says how long its workers took.
		if (cardPhase !== 'working' && cardPhase !== 'still-running') return null;
		const seconds = workersElapsed(workerItems, at);
		if (seconds === null) return null;
		return formatElapsed(seconds);
	}

	function noteEndedWorkers(current: WorkerItem[]) {
		const earlier = earlierItems;
		earlierItems = current;
		// Workers that had ended before the card showed are not news.
		if (earlier === null) return;
		const ended = newlyEnded(earlier, current);
		if (ended.length === 0) return;
		unannounced = [...unannounced, ...ended];
		if (announceTimer !== undefined) return;
		const wait = Math.max(0, lastAnnouncedAt + ANNOUNCE_EVERY_MS - Date.now());
		announceTimer = setTimeout(announceEndedWorkers, wait);
	}

	function announceEndedWorkers() {
		announceTimer = undefined;
		announcement = workersEndedAnnouncement($i18n, unannounced);
		unannounced = [];
		lastAnnouncedAt = Date.now();
	}

	function setStopRequest(workerId: string, request: StopRequest | null) {
		const next = { ...stopRequests };
		if (request === null) {
			delete next[workerId];
		} else {
			next[workerId] = request;
		}
		stopRequests = next;
	}

	async function stopWorker(worker: WorkerItem) {
		if (stopControl(worker, canStop, stopRequests[worker.id]) !== 'ready') return;
		setStopRequest(worker.id, 'sending');
		try {
			await stopChatWorker(localStorage.token, chatId, worker.id);
		} catch (error) {
			if (error instanceof WorkerNotRunningError) {
				// It ended as Stop was pressed, which is no error: show how it ended.
				setStopRequest(worker.id, 'not-running');
				await readWorkersAgain();
				return;
			}
			setStopRequest(worker.id, null);
			toast.error(stopFailedMessage($i18n, worker.label, error));
			return;
		}
		// It shows as running until the reply reports it stopped.
		setStopRequest(worker.id, 'sent');
	}

	async function readWorkersAgain() {
		const replaced = snapshot;
		const chat = await getChatById(localStorage.token, chatId).catch(() => null);
		const saved = savedReplyWorkers(chat, messageId);
		if (saved === null || snapshot !== replaced) return;
		readAgain = saved;
		readAgainReplaces = replaced;
	}

	function toggleRow(workerId: string) {
		if (expandedRows.includes(workerId)) {
			expandedRows = expandedRows.filter((id) => id !== workerId);
		} else {
			expandedRows = [...expandedRows, workerId];
		}
	}

	function openSheet(workerId: string) {
		sheetWorkerId = workerId;
		showSheet = true;
	}

	function stopText(control: StopControl): string {
		if (control === 'stopping') return $i18n.t('Stopping…');
		return $i18n.t('Stop');
	}

	function stopLabel(worker: WorkerItem, control: StopControl): string {
		if (control === 'stopping') return $i18n.t('Stopping {{worker}}…', { worker: worker.label });
		return $i18n.t('Stop {{worker}}', { worker: worker.label });
	}

	onMount(() => {
		const pointerQuery = window.matchMedia('(pointer: coarse)');
		function updatePointer() {
			coarsePointer = pointerQuery.matches;
		}
		updatePointer();
		pointerQuery.addEventListener('change', updatePointer);
		return () => pointerQuery.removeEventListener('change', updatePointer);
	});

	onDestroy(() => {
		if (clock !== undefined) clearInterval(clock);
		if (announceTimer !== undefined) clearTimeout(announceTimer);
	});

	const rowButtonClass =
		'flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2 py-1 text-left outline-hidden hover:bg-gray-100/70 focus-visible:ring-2 focus-visible:ring-sky-500/60 dark:hover:bg-gray-800/60';
	const stopButtonClass =
		'shrink-0 rounded-full border border-gray-200 font-medium text-gray-700 outline-hidden hover:border-red-300 hover:text-red-600 focus-visible:ring-2 focus-visible:ring-sky-500/60 disabled:cursor-default disabled:opacity-60 disabled:hover:border-gray-200 disabled:hover:text-gray-700 dark:border-gray-700 dark:text-gray-200 dark:hover:border-red-500/50 dark:hover:text-red-400';
</script>

{#snippet stateIcon(worker: WorkerItem)}
	<span class="flex size-4 shrink-0 items-center justify-center" aria-hidden="true">
		{#if worker.state === 'running'}
			<Spinner className="size-3.5 text-sky-500 dark:text-sky-300" />
		{:else if worker.state === 'done'}
			<CheckCircle className="size-4 text-emerald-600 dark:text-emerald-400" strokeWidth="2" />
		{:else if worker.state === 'failed'}
			<XMark className="size-4 text-red-500 dark:text-red-400" />
		{:else}
			<span class="size-2.5 rounded-[3px] bg-gray-400 dark:bg-gray-500"></span>
		{/if}
	</span>
{/snippet}

{#snippet pillTag(pill: string)}
	<span
		class="max-w-[50%] shrink-0 truncate rounded-full bg-gray-100 px-1.5 text-[0.6875rem] leading-4 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
	>
		{pill}
	</span>
{/snippet}

<!-- On phones the pill starts the second line, so the label keeps the width it needs. -->
{#snippet rowSummary(worker: WorkerItem)}
	{@const pill = workerPill($i18n, worker, chatModelName)}
	{@render stateIcon(worker)}
	<span class="flex min-w-0 flex-1 flex-col">
		<span class="flex min-w-0 items-center gap-1.5">
			<span class="sr-only">{workerStateLabel($i18n, worker.state)}:</span>
			<span class="min-w-0 truncate text-[0.8125rem] font-medium text-gray-800 dark:text-gray-100">
				{worker.label}
			</span>
			{#if pill && !touchLayout}
				{@render pillTag(pill)}
			{/if}
		</span>
		<span class="flex min-w-0 items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
			{#if pill && touchLayout}
				{@render pillTag(pill)}
			{/if}
			<span class="min-w-0 truncate">{workerLine($i18n, worker, stopRequests[worker.id])}</span>
		</span>
	</span>
{/snippet}

{#snippet workerRow(worker: WorkerItem)}
	{@const control = stopControl(worker, canStop, stopRequests[worker.id])}
	{@const details = workerDetails(worker, stopRequests[worker.id])}
	{@const expanded = expandedRows.includes(worker.id)}
	{@const detailsId = `${listId}-${worker.id}`}
	<div class="flex min-w-0 items-center gap-1">
		{#if touchLayout}
			<button
				type="button"
				class="{rowButtonClass} {rowSize}"
				aria-haspopup="dialog"
				on:click={() => openSheet(worker.id)}
			>
				{@render rowSummary(worker)}
				<ChevronRight className="size-3.5 shrink-0 text-gray-400" strokeWidth="2" />
			</button>
		{:else if details !== null}
			<button
				type="button"
				class="{rowButtonClass} {rowSize}"
				aria-expanded={expanded}
				aria-controls={expanded ? detailsId : undefined}
				on:click={() => toggleRow(worker.id)}
			>
				{@render rowSummary(worker)}
				<ChevronDown
					className="size-3 shrink-0 text-gray-400 transition-transform {expanded
						? 'rotate-180'
						: ''}"
					strokeWidth="2"
				/>
			</button>
		{:else}
			<div class="flex min-w-0 flex-1 items-center gap-2 px-2 py-1 {rowSize}">
				{@render rowSummary(worker)}
			</div>
		{/if}
		{#if control !== 'none'}
			<button
				type="button"
				class="{stopButtonClass} {stopSize}"
				aria-label={stopLabel(worker, control)}
				disabled={control === 'stopping'}
				on:click={() => stopWorker(worker)}
			>
				{stopText(control)}
			</button>
		{/if}
	</div>
	{#if !touchLayout && expanded && details !== null}
		<!-- A div: app.css shows paragraphs in list items inline, which would indent only the
			report's first line. -->
		<div
			id={detailsId}
			class="mb-1 ml-8 mr-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-gray-600 dark:text-gray-300"
		>
			{details}
		</div>
	{/if}
{/snippet}

<div
	class="buddy-ultra-workers my-1.5 w-full min-w-0 rounded-2xl border border-gray-100 bg-gray-50/50 dark:border-gray-800 dark:bg-gray-900/30"
>
	{#if phase === 'none'}
		<div class="flex items-center gap-2 px-3 py-2 text-sm text-gray-500 dark:text-gray-400">
			<Sparkles className="size-4 shrink-0" strokeWidth="1.75" />
			<span class="min-w-0 truncate">{title}</span>
		</div>
	{:else}
		<button
			type="button"
			class="flex w-full min-w-0 items-center gap-2 rounded-2xl px-3 text-left text-sm outline-hidden focus-visible:ring-2 focus-visible:ring-sky-500/60 {headerSize}"
			aria-expanded={open}
			aria-controls={open ? listId : undefined}
			on:click={() => (open = !open)}
		>
			<Sparkles className="size-4 shrink-0 text-sky-500 dark:text-sky-300" strokeWidth="1.75" />
			<span class="min-w-0 flex-1 py-1 font-medium leading-snug text-gray-800 dark:text-gray-100">
				{title}
			</span>
			{#if elapsed}
				<span class="shrink-0 text-xs tabular-nums text-gray-500 dark:text-gray-400">
					{elapsed}
				</span>
			{/if}
			<ChevronDown
				className="size-3.5 shrink-0 text-gray-400 transition-transform {open ? 'rotate-180' : ''}"
				strokeWidth="2"
			/>
		</button>
		{#if open}
			<ul id={listId} class="flex flex-col gap-0.5 px-1 pb-1.5" aria-label={$i18n.t('Workers')}>
				{#each rows as row (row.worker.id)}
					<li class="min-w-0">
						{@render workerRow(row.worker)}
						{#if row.subWorkers.length > 0}
							<ul
								class="ml-4 flex flex-col gap-0.5 border-l border-gray-200 pl-1.5 dark:border-gray-700"
								aria-label={$i18n.t('Started by {{worker}}', { worker: row.worker.label })}
							>
								{#each row.subWorkers as subWorker (subWorker.id)}
									<li class="min-w-0">
										{@render workerRow(subWorker)}
									</li>
								{/each}
							</ul>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
	<div class="sr-only" aria-live="polite" aria-atomic="true">{announcement}</div>
</div>

{#if touchLayout}
	<RunTargetSheet bind:show={showSheet} title={sheetTitle}>
		{#if sheetWorker}
			{@const worker = sheetWorker}
			{@const control = stopControl(worker, canStop, stopRequests[worker.id])}
			{@const pill = workerPill($i18n, worker, chatModelName)}
			<div class="flex flex-col gap-3 px-2 pb-2 text-sm">
				<div class="flex min-w-0 flex-wrap items-center gap-2 text-gray-600 dark:text-gray-300">
					{@render stateIcon(worker)}
					<span>{workerStateLabel($i18n, worker.state)}</span>
					{#if pill}
						<span class="rounded-full bg-gray-100 px-2 text-xs leading-5 dark:bg-gray-800">
							{pill}
						</span>
					{/if}
				</div>
				{#if worker.state === 'running'}
					<p class="whitespace-pre-wrap break-words text-gray-600 dark:text-gray-300">
						{workerLine($i18n, worker, stopRequests[worker.id])}
					</p>
				{:else if worker.summary}
					<p
						class="whitespace-pre-wrap break-words leading-relaxed text-gray-800 dark:text-gray-100"
					>
						{worker.summary}
					</p>
				{/if}
				{#if control !== 'none'}
					<button
						type="button"
						class="min-h-11 w-full rounded-xl border border-gray-200 px-4 text-sm font-medium text-red-600 outline-hidden focus-visible:ring-2 focus-visible:ring-sky-500/60 disabled:opacity-60 dark:border-gray-700 dark:text-red-400"
						disabled={control === 'stopping'}
						on:click={() => stopWorker(worker)}
					>
						{#if control === 'stopping'}
							{$i18n.t('Stopping…')}
						{:else}
							{$i18n.t('Stop worker')}
						{/if}
					</button>
				{/if}
			</div>
		{/if}
	</RunTargetSheet>
{/if}
