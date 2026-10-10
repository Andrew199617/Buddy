<script lang="ts">
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import { getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getBackupOverview,
		getBackupRunLog,
		runBackupNow,
		type BackupOverview,
		type BackupRun
	} from '$lib/apis/backups';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { formatBackupSize, getBackupHealth } from '$lib/utils/backups';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	dayjs.extend(relativeTime);

	const i18n: any = getContext('i18n');

	// Poll quickly while a run is queued or running so its result shows up, slowly otherwise.
	const ACTIVE_POLL_MS = 5000;
	const IDLE_POLL_MS = 30000;

	let overview: BackupOverview | null = null;
	let loadError: string | null = null;
	let starting = false;
	// Run id -> log text, or null while the log is loading. Present means the log is open.
	let openLogs: Record<string, string | null> = {};
	let refreshTimer: ReturnType<typeof setTimeout> | null = null;
	let destroyed = false;

	$: health = overview ? getBackupHealth(overview) : { alert: null, serviceOffline: false };
	$: activeRun = overview?.active_run ?? null;

	const primaryButtonClass =
		'px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2';
	const linkButtonClass =
		'-mx-1.5 -my-1 px-1.5 py-1 text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white';

	const clearRefreshTimer = () => {
		if (refreshTimer) {
			clearTimeout(refreshTimer);
			refreshTimer = null;
		}
	};

	const scheduleRefresh = () => {
		clearRefreshTimer();
		if (destroyed) {
			return;
		}
		let delay = IDLE_POLL_MS;
		if (overview?.active_run) {
			delay = ACTIVE_POLL_MS;
		}
		refreshTimer = setTimeout(refresh, delay);
	};

	const refresh = async () => {
		clearRefreshTimer();
		try {
			overview = await getBackupOverview(localStorage.token);
			loadError = null;
		} catch (error) {
			loadError = `${error}`;
		}
		scheduleRefresh();
	};

	const startBackup = async () => {
		starting = true;
		try {
			await runBackupNow(localStorage.token);
			toast.success($i18n.t('Backup queued. The backup service starts it within 30 seconds.'));
		} catch (error) {
			toast.error(`${error}`);
		}
		starting = false;
		await refresh();
	};

	const closeLog = (runId: string) => {
		const remaining = { ...openLogs };
		delete remaining[runId];
		openLogs = remaining;
	};

	const toggleLog = async (runId: string) => {
		if (runId in openLogs) {
			closeLog(runId);
			return;
		}
		openLogs = { ...openLogs, [runId]: null };
		try {
			const result = await getBackupRunLog(localStorage.token, runId);
			openLogs = { ...openLogs, [runId]: result.log };
		} catch (error) {
			toast.error(`${error}`);
			closeLog(runId);
		}
	};

	const fromNow = (value: string | null | undefined) => {
		if (!value) {
			return $i18n.t('never');
		}
		return dayjs(value).fromNow();
	};

	const localTime = (value: string | null | undefined) => {
		if (!value) {
			return '';
		}
		return dayjs(value).format('YYYY-MM-DD HH:mm');
	};

	const statusLabel = (status: BackupRun['status']) => {
		if (status === 'succeeded') {
			return $i18n.t('Succeeded');
		}
		if (status === 'failed') {
			return $i18n.t('Failed');
		}
		if (status === 'running') {
			return $i18n.t('Running');
		}
		return $i18n.t('Queued');
	};

	const statusDotClass = (status: BackupRun['status']) => {
		if (status === 'succeeded') {
			return 'bg-emerald-500';
		}
		if (status === 'failed') {
			return 'bg-red-500';
		}
		return 'bg-sky-500 animate-pulse';
	};

	const triggerLabel = (run: BackupRun) => {
		if (run.trigger === 'scheduled') {
			return $i18n.t('Scheduled');
		}
		if (run.requested_by) {
			return $i18n.t('Manual, by {{name}}', { name: run.requested_by });
		}
		return $i18n.t('Manual');
	};

	const sizesText = (run: BackupRun) => {
		const database = $i18n.t('database {{size}}', { size: formatBackupSize(run.db_bytes) });
		const files = $i18n.t('files {{size}}', { size: formatBackupSize(run.data_bytes) });
		const settings = $i18n.t('settings {{size}}', { size: formatBackupSize(run.env_bytes) });
		return `${database} · ${files} · ${settings}`;
	};

	const runButtonLabel = (run: BackupRun | null) => {
		if (run?.status === 'running') {
			return $i18n.t('Backing up…');
		}
		if (run?.status === 'queued') {
			return $i18n.t('Queued…');
		}
		return $i18n.t('Run backup now');
	};

	onMount(refresh);

	onDestroy(() => {
		destroyed = true;
		clearRefreshTimer();
	});
</script>

<div class="flex flex-col h-full justify-between text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.admin.backups.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if overview === null && loadError === null}
			<div class="flex justify-center py-6"><Spinner className="size-4" /></div>
		{:else if overview === null}
			<p class="text-xs text-red-700 dark:text-red-400">{loadError}</p>
		{:else}
			{#if loadError}
				<p class="mb-3 text-[0.6875rem] text-amber-700 dark:text-amber-400">
					{$i18n.t('Could not refresh: {{error}}', { error: loadError })}
				</p>
			{/if}

			{#if !overview.available}
				<div
					class="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2.5 text-xs text-gray-600 dark:border-gray-850 dark:bg-gray-900 dark:text-gray-400"
				>
					<p class="font-medium text-gray-900 dark:text-white">
						{#if overview.configured}
							{$i18n.t('The backup service has not reported yet.')}
						{:else}
							{$i18n.t('Backups are not set up for this instance.')}
						{/if}
					</p>
					<p class="mt-1">
						{$i18n.t(
							'Nightly Perforce backups run in the release Docker stack (the backup service). See “Backups (release only)” in AGENTS.md.'
						)}
					</p>
				</div>
			{:else}
				{#if health.alert}
					<div
						role="alert"
						class="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-800 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-200"
					>
						<p class="font-medium">
							{#if health.alert === 'failed'}
								{$i18n.t('The latest backup failed')}
							{:else if health.alert === 'never'}
								{$i18n.t('No backup has succeeded yet')}
							{:else}
								{$i18n.t('No successful backup in the last 26 hours')}
							{/if}
						</p>
						{#if health.alert === 'failed' && overview.latest_run}
							<p class="mt-1 break-words whitespace-pre-wrap">
								{overview.latest_run.error ?? $i18n.t('No error message was recorded.')}
							</p>
							<p class="mt-1 text-red-700/80 dark:text-red-300/80">
								{$i18n.t('Failed {{time}}', { time: fromNow(overview.latest_run.finished_at) })}
							</p>
						{/if}
						{#if overview.last_success}
							<p class="mt-1 text-red-700/80 dark:text-red-300/80">
								{$i18n.t('Last success {{time}}', { time: fromNow(overview.last_success.finished_at) })}
							</p>
						{/if}
					</div>
				{/if}

				{#if health.serviceOffline}
					<div
						role="status"
						class="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
					>
						<p class="font-medium">{$i18n.t('The backup service is not responding')}</p>
						<p class="mt-1">
							{$i18n.t(
								'Last check-in {{time}}. Scheduled and queued backups wait until it is running again; check the backup container in Docker.',
								{ time: fromNow(overview.scheduler?.last_heartbeat_at) }
							)}
						</p>
					</div>
				{/if}

				<AdminSettingSection title={$i18n.t('settings.admin.backups.sections.status.title')} first>
					<AdminSettingRow label={$i18n.t('settings.admin.backups.lastSuccess.label')}>
						<span
							class="text-xs text-right text-gray-900 dark:text-white"
							title={localTime(overview.last_success?.finished_at)}
						>
							{#if overview.last_success}
								{fromNow(overview.last_success.finished_at)}
								{#if overview.last_success.changelist}
									· {$i18n.t('change {{number}}', { number: overview.last_success.changelist })}
								{/if}
							{:else}
								{$i18n.t('never')}
							{/if}
						</span>
					</AdminSettingRow>
					{#if overview.last_success}
						<p class="-mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{sizesText(overview.last_success)}
						</p>
					{/if}

					<AdminSettingRow label={$i18n.t('settings.admin.backups.schedule.label')}>
						<span class="text-xs text-right text-gray-900 dark:text-white">
							{$i18n.t('Daily at {{time}} ({{timezone}})', {
								time: overview.schedule?.time ?? '–',
								timezone: overview.schedule?.timezone ?? '–'
							})}
						</span>
					</AdminSettingRow>
					{#if overview.scheduler?.next_run_at}
						<p class="-mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{$i18n.t('Next run {{time}} ({{local}} your time)', {
								time: fromNow(overview.scheduler.next_run_at),
								local: localTime(overview.scheduler.next_run_at)
							})}
						</p>
					{/if}

					<AdminSettingRow label={$i18n.t('settings.admin.backups.destination.label')}>
						<span class="text-xs text-right break-all text-gray-900 dark:text-white">
							{overview.schedule?.depot_path ?? '–'}
						</span>
					</AdminSettingRow>
					<p class="-mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{overview.schedule?.retention ?? ''}
					</p>

					<AdminSettingRow label={$i18n.t('settings.admin.backups.service.label')}>
						<span class="flex items-center gap-1.5 text-xs text-gray-900 dark:text-white">
							<span
								class="size-1.5 rounded-full {overview.scheduler?.online
									? 'bg-emerald-500'
									: 'bg-amber-500'}"
								aria-hidden="true"
							></span>
							{#if overview.scheduler?.online}
								{$i18n.t('Online')}
							{:else}
								{$i18n.t('Not responding')}
							{/if}
						</span>
					</AdminSettingRow>

					<div class="flex flex-wrap items-center justify-end gap-2 pt-1">
						<button
							type="button"
							class={primaryButtonClass}
							disabled={starting || activeRun !== null}
							on:click={startBackup}
						>
							{#if starting || activeRun}
								<Spinner className="size-3.5" />
							{/if}
							{runButtonLabel(activeRun)}
						</button>
					</div>
				</AdminSettingSection>

				<AdminSettingSection title={$i18n.t('settings.admin.backups.sections.history.title')}>
					{#if overview.runs.length === 0}
						<p class="text-xs text-gray-500">{$i18n.t('No backups yet.')}</p>
					{:else}
						<ul class="flex flex-col divide-y divide-gray-100 dark:divide-gray-850">
							{#each overview.runs as run (run.id)}
								<li class="py-2 first:pt-0">
									<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
										<span class="size-1.5 shrink-0 rounded-full {statusDotClass(run.status)}" aria-hidden="true"
										></span>
										<span class="text-gray-900 dark:text-white">{statusLabel(run.status)}</span>
										<span class="text-gray-500" title={localTime(run.created_at)}>
											{fromNow(run.created_at)}
										</span>
										<span class="text-gray-400 dark:text-gray-600">· {triggerLabel(run)}</span>
										{#if run.changelist}
											<span class="text-gray-400 dark:text-gray-600">
												· {$i18n.t('change {{number}}', { number: run.changelist })}
											</span>
										{/if}
										{#if run.duration_seconds !== null}
											<span class="text-gray-400 dark:text-gray-600">· {run.duration_seconds} s</span>
										{/if}
										{#if run.status === 'succeeded' || run.status === 'failed'}
											<button
												type="button"
												class="ml-auto {linkButtonClass}"
												aria-expanded={run.id in openLogs}
												on:click={() => toggleLog(run.id)}
											>
												{#if run.id in openLogs}
													{$i18n.t('Hide log')}
												{:else}
													{$i18n.t('Show log')}
												{/if}
											</button>
										{/if}
									</div>
									{#if run.status === 'failed' && run.error}
										<p class="mt-1 break-words text-[0.6875rem] text-red-700 dark:text-red-400">
											{run.error}
										</p>
									{/if}
									{#if run.status === 'succeeded'}
										<p class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
											{sizesText(run)}
										</p>
									{/if}
									{#if run.id in openLogs}
										{#if openLogs[run.id] === null}
											<div class="mt-2"><Spinner className="size-3.5" /></div>
										{:else}
											<pre
												class="mt-2 max-h-64 overflow-auto rounded-lg bg-gray-50 p-2 text-[0.625rem] leading-relaxed text-gray-700 dark:bg-gray-900 dark:text-gray-300">{openLogs[
													run.id
												]}</pre>
										{/if}
									{/if}
								</li>
							{/each}
						</ul>
					{/if}
				</AdminSettingSection>
			{/if}
		{/if}
	</div>
</div>
