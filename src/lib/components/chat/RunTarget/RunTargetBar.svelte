<script lang="ts">
	// Where a new chat runs, right above the message box: a computer (or Detached), a folder, and
	// how much the AI may do. Editable until the first message; read-only after it.
	import { getContext } from 'svelte';

	import {
		getRunTargetReadiness,
		getSuggestedFolders,
		type ChatRunTarget,
		type ChatRunTargetDetails,
		type FolderSuggestion,
		type RunTargetComputer,
		type RunTargetFolder,
		type RunTargetOptions,
		type RunTargetPlanState,
		type SubscriptionAccess,
		type SubscriptionProviderId
	} from '$lib/apis/subscriptions';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import RunTargetAccessMenu from './RunTargetAccessMenu.svelte';
	import RunTargetComputerMenu from './RunTargetComputerMenu.svelte';
	import RunTargetFolderMenu from './RunTargetFolderMenu.svelte';
	import RunTargetInfo from './RunTargetInfo.svelte';
	import RunTargetSheet from './RunTargetSheet.svelte';
	import {
		chooseComputer,
		computerPlace,
		computerReadiness,
		folderLabel,
		type RunTargetDraft
	} from './run-target';

	const i18n: any = getContext('i18n');

	export let options: RunTargetOptions;
	export let draft: RunTargetDraft | null = null;
	export let target: ChatRunTarget | null = null;
	export let editable = false;
	// The plans behind the selected models; empty when a model that isn't a plan is selected.
	export let providers: SubscriptionProviderId[] = [];
	export let thisPcMachineId: string | null = null;
	// The phone layout: one summary chip and a sheet. The composer turns it on for touch screens.
	export let touchLayout = false;
	export let onChange: (draft: RunTargetDraft) => void = () => {};
	export let onNewChatHere: (details: ChatRunTargetDetails | null) => void = () => {};
	export let onFocusComposer: () => void = () => {};

	let showComputer = false;
	let showFolder = false;
	let showAccess = false;
	let showInfo = false;
	let showSheet = false;
	// Fresh checks made when the person picks a computer, by computer and plan.
	let freshStates: Record<string, Partial<Record<SubscriptionProviderId, RunTargetPlanState>>> = {};
	let suggestionsByComputer: Record<string, FolderSuggestion[]> = {};
	// Computers whose suggestions are on their way, so each is asked once at a time.
	const prefetching = new Set<string>();

	$: details = targetDetails(target);
	$: forPlansOnly = providers.length === 0;
	$: selectedComputer = computerFor(draft?.machineId ?? null, options);
	$: if (editable && selectedComputer) void prefetchSuggestions(selectedComputer);

	function targetDetails(chatTarget: ChatRunTarget | null): ChatRunTargetDetails | null {
		if (!chatTarget) return null;
		if (chatTarget.bound) return chatTarget;
		return chatTarget.would_bind;
	}

	function computerFor(machineId: string | null, from: RunTargetOptions): RunTargetComputer | null {
		if (!machineId) return null;
		return from.computers?.find((computer) => computer.machine_id === machineId) ?? null;
	}

	/** The computer with any fresh checks laid over what it last reported. */
	function withFreshStates(computer: RunTargetComputer): RunTargetComputer {
		const fresh = freshStates[computer.machine_id];
		if (!fresh) return computer;
		const merged = { ...computer.providers };
		for (const provider of Object.keys(fresh) as SubscriptionProviderId[]) {
			const state = fresh[provider];
			if (state) merged[provider] = { state, message: merged[provider]?.message ?? null };
		}
		return { ...computer, providers: merged };
	}

	function stateFor(computer: RunTargetComputer): RunTargetPlanState {
		return computerReadiness(withFreshStates(computer), providers);
	}

	async function prefetchSuggestions(computer: RunTargetComputer) {
		const machineId = computer.machine_id;
		if (computer.folders !== 'on' || suggestionsByComputer[machineId]) return;
		if (prefetching.has(machineId)) return;
		prefetching.add(machineId);
		try {
			const found = await getSuggestedFolders(localStorage.token, machineId);
			if (found.state === 'ok') {
				suggestionsByComputer = { ...suggestionsByComputer, [machineId]: found.folders };
			}
		} catch {
			// The menu asks again when it opens.
		} finally {
			prefetching.delete(machineId);
		}
	}

	async function checkReadiness(computer: RunTargetComputer) {
		const machineId = computer.machine_id;
		let checked = providers.filter((provider) => computer.providers[provider] !== undefined);
		if (checked.length === 0) checked = Object.keys(computer.providers) as SubscriptionProviderId[];
		for (const provider of checked) {
			freshStates = {
				...freshStates,
				[machineId]: { ...freshStates[machineId], [provider]: 'checking' }
			};
		}
		for (const provider of checked) {
			let state: RunTargetPlanState = 'unknown';
			try {
				state = (await getRunTargetReadiness(localStorage.token, provider, machineId)).state;
			} catch {
				state = 'unknown';
			}
			freshStates = {
				...freshStates,
				[machineId]: { ...freshStates[machineId], [provider]: state }
			};
		}
	}

	function pickComputer(machineId: string | null) {
		if (!draft) return;
		const next = chooseComputer(draft, options, providers, machineId);
		onChange(next);
		showComputer = false;
		const computer = computerFor(machineId, options);
		if (computer) void checkReadiness(computer);
	}

	function pickFolder(folder: RunTargetFolder, machineId: string) {
		if (!draft) return;
		let next = draft;
		if (draft.machineId !== machineId) next = chooseComputer(draft, options, providers, machineId);
		onChange({ ...next, folder, touched: { ...next.touched, folder: true } });
		showFolder = false;
		showSheet = false;
		onFocusComposer();
	}

	function pickAccess(access: SubscriptionAccess) {
		if (!draft) return;
		onChange({ ...draft, access, touched: { ...draft.touched, access: true } });
		showAccess = false;
	}

	function acknowledge(folderId: string | null) {
		if (!draft) return;
		let access = draft.access;
		if (folderId === null) access = 'chat';
		onChange({ ...draft, acknowledgedFolderId: folderId, access });
	}

	function computerChipLabel(computer: RunTargetComputer | null): string {
		const place = computerPlace(computer, thisPcMachineId);
		if (place === 'local') return $i18n.t('Local');
		if (place === 'server') return $i18n.t('Buddy server');
		if (place === 'remote' && computer) {
			return $i18n.t('Remote · {{name}}', { name: computer.name });
		}
		return $i18n.t('Detached');
	}

	function folderChipLabel(current: RunTargetDraft): string {
		const label = folderLabel(current);
		if (label.kind === 'path') return label.name;
		if (label.kind === 'auto') return $i18n.t('AI chooses');
		return $i18n.t('No folder');
	}

	function accessChipLabel(access: SubscriptionAccess): string {
		if (access === 'chat') return $i18n.t('Chat only');
		if (access === 'read') return $i18n.t('Read files');
		return $i18n.t('Full access');
	}

	function accessShortLabel(access: SubscriptionAccess): string {
		if (access === 'chat') return $i18n.t('Chat');
		if (access === 'read') return $i18n.t('Read');
		return $i18n.t('Full');
	}

	function summaryLabel(current: RunTargetDraft): string {
		const computer = computerFor(current.machineId, options);
		let computerText = computerChipLabel(computer);
		if (computer && computerPlace(computer, thisPcMachineId) === 'remote') {
			computerText = computer.name;
		}
		if (current.kind === 'detached') return computerText;
		return `${computerText} · ${folderChipLabel(current)} · ${accessShortLabel(current.access)}`;
	}

	function boundSummary(target: ChatRunTargetDetails): string {
		if (target.kind === 'detached') return $i18n.t('Detached');
		let folderText = $i18n.t('AI chooses');
		if (target.folder_mode === 'path' && target.path) {
			folderText = target.path.split(/[\\/]/).filter(Boolean).pop() ?? target.path;
		}
		return `${target.computer?.name ?? ''} · ${folderText} · ${accessShortLabel(target.access)}`;
	}

	function newChatHere() {
		showInfo = false;
		showSheet = false;
		onNewChatHere(details);
	}

	$: chipClass = `buddy-run-target-chip flex h-7 min-w-0 items-center gap-1 rounded-lg px-2 text-[0.8125rem] transition ${
		forPlansOnly || !editable
			? 'text-gray-400 dark:text-gray-500'
			: 'text-gray-600 dark:text-gray-300'
	} hover:bg-gray-50/60 dark:hover:bg-gray-800/40`;
	$: selectedReady = selectedComputer ? stateFor(selectedComputer) === 'ready' : true;
	$: folderHelp = draft ? folderLabel(draft) : null;
	$: computerName =
		selectedComputer?.kind === 'server' ? $i18n.t('Buddy server') : (selectedComputer?.name ?? '');
</script>

<div
	class="buddy-run-target-bar flex min-w-0 flex-1 items-center gap-0.5"
	role="group"
	aria-label={$i18n.t('Where this chat runs')}
>
	{#if editable && draft}
		{#if touchLayout}
			<button
				type="button"
				class="buddy-run-target-summary flex min-h-11 w-full min-w-0 items-center gap-2 rounded-xl px-3 text-start text-sm text-gray-700 dark:text-gray-200"
				aria-haspopup="dialog"
				aria-expanded={showSheet}
				on:click={() => (showSheet = true)}
			>
				{#if !selectedReady}
					<span class="size-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true"></span>
				{/if}
				<span class="min-w-0 flex-1 truncate"><bdi>{summaryLabel(draft)}</bdi></span>
				<ChevronDown className="size-3 shrink-0" strokeWidth="2.5" />
			</button>
			<RunTargetSheet bind:show={showSheet} title={$i18n.t('Where this chat runs')}>
				<RunTargetComputerMenu
					{options}
					{draft}
					{providers}
					{thisPcMachineId}
					{stateFor}
					inMenu={false}
					onPick={pickComputer}
					onClose={() => (showSheet = false)}
				/>
				<RunTargetAccessMenu
					{draft}
					{options}
					{providers}
					{computerName}
					inMenu={false}
					onPick={pickAccess}
					onAcknowledge={acknowledge}
					onClose={() => (showSheet = false)}
				/>
				{#if selectedComputer}
					{#key selectedComputer.machine_id}
						<RunTargetFolderMenu
							{options}
							{draft}
							computer={selectedComputer}
							{thisPcMachineId}
							prefetched={suggestionsByComputer[selectedComputer.machine_id] ?? null}
							autofocus={false}
							onPick={pickFolder}
						/>
					{/key}
				{/if}
			</RunTargetSheet>
		{:else}
			{#if forPlansOnly}
				<span class="shrink-0 px-1 text-[0.75rem] text-gray-400 dark:text-gray-500">
					{$i18n.t('For Claude and ChatGPT')}
				</span>
			{/if}
			<Dropdown
				bind:show={showComputer}
				side="top"
				align="start"
				preserveComposerFocus
				composerMenu="run-target"
				visualViewportAware
			>
				<button type="button" class={chipClass} aria-haspopup="menu" aria-expanded={showComputer}>
					{#if !selectedReady}
						<span class="size-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true"></span>
					{/if}
					<span class="max-w-[10rem] truncate"
						><bdi>{computerChipLabel(selectedComputer)}</bdi></span
					>
					<ChevronDown className="size-2.5 shrink-0" strokeWidth="2.5" />
				</button>
				<div
					slot="content"
					class="w-72 rounded-xl border border-gray-100 bg-white shadow-lg dark:border-gray-800 dark:bg-gray-850"
				>
					<RunTargetComputerMenu
						{options}
						{draft}
						{providers}
						{thisPcMachineId}
						{stateFor}
						onPick={pickComputer}
						onClose={() => (showComputer = false)}
					/>
				</div>
			</Dropdown>
			{#if selectedComputer}
				<Dropdown
					bind:show={showFolder}
					side="top"
					align="start"
					preserveComposerFocus
					composerMenu="run-target"
					visualViewportAware
					contentRole="dialog"
					contentLabel={$i18n.t('Folder')}
				>
					<button
						type="button"
						class={chipClass}
						aria-haspopup="dialog"
						aria-expanded={showFolder}
						title={folderHelp?.kind === 'path' ? folderHelp.path : undefined}
					>
						<span class="max-w-[16rem] truncate"><bdi>{folderChipLabel(draft)}</bdi></span>
						<ChevronDown className="size-2.5 shrink-0" strokeWidth="2.5" />
					</button>
					<div
						slot="content"
						class="max-h-[min(28rem,calc(100dvh-6rem))] w-[22rem] max-w-[calc(100vw-1rem)] overflow-y-auto rounded-xl border border-gray-100 bg-white shadow-lg dark:border-gray-800 dark:bg-gray-850"
					>
						{#key selectedComputer.machine_id}
							<RunTargetFolderMenu
								{options}
								{draft}
								computer={selectedComputer}
								{thisPcMachineId}
								prefetched={suggestionsByComputer[selectedComputer.machine_id] ?? null}
								onPick={pickFolder}
							/>
						{/key}
					</div>
				</Dropdown>
			{:else}
				<button type="button" class={chipClass} disabled aria-disabled="true">
					<span class="truncate">{folderChipLabel(draft)}</span>
				</button>
			{/if}
			<Dropdown
				bind:show={showAccess}
				side="top"
				align="start"
				preserveComposerFocus
				composerMenu="run-target"
				visualViewportAware
			>
				<button type="button" class={chipClass} aria-haspopup="menu" aria-expanded={showAccess}>
					{#if options.chat_folder?.shared_instructions && draft.acknowledgedFolderId === null && draft.kind === 'computer'}
						<LockClosed className="size-3 shrink-0" />
					{/if}
					<span class="truncate">{accessChipLabel(draft.access)}</span>
					<ChevronDown className="size-2.5 shrink-0" strokeWidth="2.5" />
				</button>
				<div
					slot="content"
					class="w-80 rounded-xl border border-gray-100 bg-white shadow-lg dark:border-gray-800 dark:bg-gray-850"
				>
					<RunTargetAccessMenu
						{draft}
						{options}
						{providers}
						{computerName}
						onPick={pickAccess}
						onAcknowledge={acknowledge}
						onClose={() => (showAccess = false)}
					/>
				</div>
			</Dropdown>
		{/if}
	{:else if details}
		{#if touchLayout}
			<button
				type="button"
				class="buddy-run-target-summary flex min-h-11 w-full min-w-0 items-center gap-2 rounded-xl px-3 text-start text-sm text-gray-500 dark:text-gray-400"
				aria-haspopup="dialog"
				aria-expanded={showSheet}
				on:click={() => (showSheet = true)}
			>
				<LockClosed className="size-3 shrink-0" />
				{#if details.problem}
					<span class="size-1.5 shrink-0 rounded-full bg-red-500" aria-hidden="true"></span>
				{/if}
				<span class="min-w-0 flex-1 truncate"><bdi>{boundSummary(details)}</bdi></span>
			</button>
			<RunTargetSheet bind:show={showSheet} title={$i18n.t('Where this chat runs')}>
				<RunTargetInfo {details} bound={target?.bound ?? false} onNewChatHere={newChatHere} />
			</RunTargetSheet>
		{:else}
			<Dropdown
				bind:show={showInfo}
				side="top"
				align="start"
				preserveComposerFocus
				composerMenu="run-target"
				visualViewportAware
				contentRole="dialog"
				contentLabel={$i18n.t('Where this chat runs')}
			>
				<button
					type="button"
					class="buddy-run-target-chip flex h-7 min-w-0 items-center gap-1.5 rounded-lg px-2 text-[0.8125rem] text-gray-400 hover:bg-gray-50/60 dark:text-gray-500 dark:hover:bg-gray-800/40"
					aria-haspopup="dialog"
					aria-expanded={showInfo}
				>
					{#if details.problem}
						<span class="size-1.5 shrink-0 rounded-full bg-red-500" aria-hidden="true"></span>
					{/if}
					{#if !target?.bound}
						<span class="shrink-0">{$i18n.t('Will run on')}</span>
					{/if}
					<span class="min-w-0 truncate"><bdi>{boundSummary(details)}</bdi></span>
				</button>
				<div
					slot="content"
					class="rounded-xl border border-gray-100 bg-white shadow-lg dark:border-gray-800 dark:bg-gray-850"
				>
					<RunTargetInfo {details} bound={target?.bound ?? false} onNewChatHere={newChatHere} />
				</div>
			</Dropdown>
		{/if}
	{/if}
</div>
