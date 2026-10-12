<script lang="ts">
	// "Project folder" in a chat folder's settings: where new chats in the folder
	// run, in which folder, which folders they are offered, and their access.
	// Each person chooses for themselves; each chat can still change it before
	// its first message.
	import { getContext, onMount, tick } from 'svelte';

	import {
		clearFolderProject,
		getFolderProject,
		getNewFolderProject,
		saveFolderProject,
		type FolderProject,
		type ProjectFolderChoice,
		type ProjectFolderComputer,
		type SubscriptionAccess
	} from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { mobile, showSettings } from '$lib/stores';
	import { thisPcMachineId } from '$lib/desktop/this-pc';
	import ChoicePicker from './ChoicePicker.svelte';
	import ProjectFolderPicker from './ProjectFolderPicker.svelte';
	import { announceProjectFolderChange } from './project-folder-changes';
	import type { ChoiceOption } from './choice-picker';
	import {
		MAX_OFFERED_PER_COMPUTER,
		RUN_ON_AUTOMATIC,
		RUN_ON_DETACHED,
		RUN_ON_INHERIT,
		choiceFromDraft,
		copyDraft,
		draftFromProject,
		draftProblem,
		draftWithOffered,
		draftWithoutOffered,
		draftWithRunOn,
		emptyDraft,
		focusWasLost,
		planDefaultAccess,
		projectSummary,
		runOnValue,
		sameDraft,
		type ProjectDraft,
		type ProjectSummary
	} from './project-folders';

	const i18n: any = getContext('i18n');

	// The chat folder; null while it is being created.
	export let folderId: string | null;
	// The parent of a folder being created: what the new folder follows.
	export let parentId: string | null = null;
	// The id of the heading that names this section where it is shown.
	export let labelledBy = '';
	// Set when the draft differs from what is saved.
	export let dirty = false;
	export let error = '';
	// Set when the choice could not be loaded; there is then nothing to save.
	export let loadError = '';
	// What the folder dialog's collapsed row says.
	export let summary = '';
	// Set while a folder picker fills the section; Escape and Back close it first.
	export let picking = false;
	// Set once loaded, when this person may set where new chats run.
	export let available = false;

	type Picker = { computer: ProjectFolderComputer; purpose: 'folder' | 'offered' };

	let project: FolderProject | null = null;
	let hidden = false;
	let loading = true;
	let draft: ProjectDraft = emptyDraft('inherit');
	let saved: ProjectDraft = emptyDraft('inherit');
	let picker: Picker | null = null;
	// Why loading again after Settings → Machines failed; what was loaded before still saves.
	let refreshError = '';
	let sectionElement: HTMLElement | null = null;

	// Ids stay unique when a new folder's dialog and another folder's are both mounted.
	$: idPrefix = folderId ?? 'new';
	$: runOnId = `project-run-on-${idPrefix}`;
	$: accessId = `project-access-${idPrefix}`;
	$: dirty = !sameDraft(draft, saved);
	$: picking = picker !== null;
	$: available = !loading && !loadError && !hidden && project !== null;
	$: planDefault = project ? planDefaultAccess(project) : null;
	$: chosenComputer = findComputer(project, draft.machineId);
	$: choosingFolders = draft.mode === 'auto' || draft.mode === 'folder';
	$: summary = describeSummary(project ? projectSummary(draft, project) : null, planDefault);
	$: runOnOptions = project ? describeRunOn(project, $thisPcMachineId) : [];
	$: accessOptions = describeAccess(planDefault, chosenComputer);
	$: effectiveAccess = draft.access || planDefault;

	function findComputer(
		current: FolderProject | null,
		machineId: string
	): ProjectFolderComputer | null {
		if (!current || !machineId) return null;
		return current.computers.find((computer) => computer.machine_id === machineId) ?? null;
	}

	/** Shows a loaded project; the caller decides what happens to the draft. */
	function showProject(loaded: FolderProject | null) {
		// Null: not allowed to use computers, or the folder is gone; nothing to choose here.
		hidden = loaded === null;
		project = loaded;
		if (loaded) saved = draftFromProject(loaded);
	}

	function startFromSaved() {
		draft = copyDraft(saved);
	}

	async function fetchProject(): Promise<FolderProject | null> {
		if (folderId) return getFolderProject(localStorage.token, folderId);
		return getNewFolderProject(localStorage.token, parentId);
	}

	async function load() {
		loading = true;
		loadError = '';
		try {
			showProject(await fetchProject());
			startFromSaved();
		} catch (cause) {
			project = null;
			loadError = `${cause}`;
		} finally {
			loading = false;
		}
	}

	onMount(load);

	/** Loads again after Settings → Machines, keeping a choice the person has not saved yet. */
	async function refresh() {
		let loaded: FolderProject | null;
		try {
			loaded = await fetchProject();
		} catch (cause) {
			refreshError = $i18n.t('The project folder could not be loaded: {{reason}}', {
				reason: `${cause}`
			});
			return;
		}
		refreshError = '';
		// Checked once the answer is back: the person may have chosen something meanwhile.
		const unsaved = !sameDraft(draft, saved);
		showProject(loaded);
		if (!unsaved) startFromSaved();
	}

	/** Puts keyboard focus back in the section when what had it was replaced. */
	async function keepFocusInSection() {
		await tick();
		if (!focusWasLost() || !sectionElement) return;
		const trigger = document.getElementById(`${runOnId}-trigger`);
		const chosen = sectionElement.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]');
		let target: HTMLElement | null = trigger ?? chosen;
		if (!target) target = sectionElement.querySelector<HTMLElement>('button');
		target?.focus();
	}

	// Set while Settings → Machines is open from here; plans and computers may change there.
	let waitingForSettings = false;

	function openMachineSettings() {
		waitingForSettings = true;
		showSettings.set('machines');
	}

	async function settingsClosed() {
		waitingForSettings = false;
		await refresh();
		if (folderId) announceProjectFolderChange(folderId);
		await keepFocusInSection();
	}

	$: if (waitingForSettings && !$showSettings) void settingsClosed();

	function accessName(access: SubscriptionAccess): string {
		if (access === 'full') return $i18n.t('Full access');
		if (access === 'read') return $i18n.t('Read files');
		return $i18n.t('Chat only');
	}

	function computerName(computer: ProjectFolderComputer): string {
		if (computer.kind === 'server') return $i18n.t('Buddy server');
		return computer.name;
	}

	function computerNote(computer: ProjectFolderComputer, thisPc: string | null): string {
		if (computer.machine_id === thisPc) return $i18n.t('This PC');
		if (computer.kind === 'server') return $i18n.t('Inside the Buddy server');
		if (computer.kind === 'address') return $i18n.t('Added by address');
		if (computer.online === false) return $i18n.t('Offline');
		return $i18n.t('Your computer');
	}

	function computerIcon(computer: ProjectFolderComputer): 'server' | 'computer' {
		if (computer.kind === 'server') return 'server';
		return 'computer';
	}

	function inheritedDescription(choice: ProjectFolderChoice): string {
		if (choice.mode === 'chat') return $i18n.t('Detached');
		const parts: string[] = [];
		const computer = findComputer(project, choice.machine_id);
		if (choice.mode === 'folder') {
			const path = choice.paths[choice.machine_id]?.path ?? Object.values(choice.paths)[0]?.path;
			if (path) parts.push(path);
		} else {
			parts.push($i18n.t('AI chooses the folder'));
		}
		if (computer) parts.push(computerName(computer));
		if (choice.access) parts.push(accessName(choice.access));
		return parts.join(' · ');
	}

	function describeRunOn(current: FolderProject, thisPc: string | null): ChoiceOption[] {
		const options: ChoiceOption[] = [];
		// A folder whose instructions others can change needs its own choice.
		if (!current.shared_instructions) {
			if (current.inherited) {
				options.push({
					value: RUN_ON_INHERIT,
					name: $i18n.t('Same as “{{name}}”', { name: current.inherited.folder_name }),
					description: inheritedDescription(current.inherited),
					icon: 'inherit'
				});
			} else {
				options.push({
					value: RUN_ON_INHERIT,
					name: $i18n.t('Not set'),
					description: $i18n.t('New chats here start detached until you choose.'),
					icon: 'inherit'
				});
			}
		}
		options.push({
			value: RUN_ON_AUTOMATIC,
			name: $i18n.t('Automatic'),
			description: $i18n.t('This PC in the Buddy app, otherwise the computer you used last.'),
			icon: 'automatic'
		});
		for (const computer of current.computers) {
			options.push({
				value: computer.machine_id,
				name: computerName(computer),
				description: computerNote(computer, thisPc),
				icon: computerIcon(computer)
			});
		}
		options.push({
			value: RUN_ON_DETACHED,
			name: $i18n.t('Detached'),
			description: $i18n.t('Chat only. No files or commands.'),
			icon: 'detached'
		});
		return options;
	}

	function describeAccess(
		plans: SubscriptionAccess | null,
		computer: ProjectFolderComputer | null
	): ChoiceOption[] {
		let planDefaultName = $i18n.t('Plan default');
		if (plans)
			planDefaultName = $i18n.t('Plan default ({{access}})', { access: accessName(plans) });
		let where = $i18n.t('your computer');
		if (computer) where = computerName(computer);
		return [
			{
				value: '',
				name: planDefaultName,
				description: $i18n.t('What your plans start with. Each chat can still change it.'),
				icon: 'plan'
			},
			{
				value: 'chat',
				name: $i18n.t('Chat only'),
				description: $i18n.t('No files or commands.'),
				icon: 'chat'
			},
			{
				value: 'read',
				name: $i18n.t('Read files'),
				description: $i18n.t('Reads files in the folder and changes nothing.'),
				icon: 'read'
			},
			{
				value: 'full',
				name: $i18n.t('Full access'),
				description: $i18n.t('Edits files and runs commands on {{name}} as your account.', {
					name: where
				}),
				icon: 'full',
				tone: 'amber'
			}
		];
	}

	function describeSummary(
		current: ProjectSummary | null,
		plans: SubscriptionAccess | null
	): string {
		if (!current) return '';
		if (current.kind === 'inherit') {
			return $i18n.t('Same as “{{name}}”', { name: current.folderName });
		}
		if (current.kind === 'unset') return $i18n.t('Not set');
		if (current.kind === 'unchosen') return $i18n.t('Waiting for your choice');
		if (current.kind === 'detached') return $i18n.t('Detached');
		let folder = $i18n.t('AI chooses');
		if (current.folder) folder = current.folder;
		let computer = $i18n.t('Automatic');
		if (current.computer) computer = computerName(current.computer);
		let access = $i18n.t('Plan default');
		if (current.access) access = accessName(current.access);
		else if (plans) access = $i18n.t('Plan default ({{access}})', { access: accessName(plans) });
		return [folder, computer, access].join(' · ');
	}

	function chooseRunOn(value: string) {
		draft = draftWithRunOn(draft, value);
	}

	function chooseAccess(value: string) {
		draft = { ...draft, access: value as SubscriptionAccess | '' };
	}

	function setFolderMode(mode: 'auto' | 'folder') {
		draft = { ...draft, mode };
	}

	async function onFolderModeKeydown(event: KeyboardEvent) {
		const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'];
		if (!keys.includes(event.key)) return;
		event.preventDefault();
		let next: 'auto' | 'folder' = 'auto';
		if (draft.mode === 'auto') next = 'folder';
		setFolderMode(next);
		await tick();
		document.getElementById(`project-folder-mode-${next}-${idPrefix}`)?.focus();
	}

	function segmentClass(selected: boolean): string {
		const base = 'min-h-11 rounded-lg px-2 text-sm font-medium transition md:min-h-9';
		if (selected) return `${base} bg-white shadow-sm dark:bg-gray-900`;
		return `${base} text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100`;
	}

	function pickerStartPath(current: Picker): string {
		if (current.purpose !== 'folder') return '';
		return draft.paths[current.computer.machine_id] ?? '';
	}

	function pathProblem(machineId: string): string | null {
		return project?.own?.paths[machineId]?.problem ?? null;
	}

	/** The computers the "A specific folder" list shows: the chosen one, or all of them. */
	function folderComputers(
		current: FolderProject,
		chosen: ProjectFolderComputer | null
	): ProjectFolderComputer[] {
		if (chosen) return [chosen];
		return current.computers;
	}

	function openPicker(computer: ProjectFolderComputer, purpose: Picker['purpose']) {
		picker = { computer, purpose };
	}

	/** Closes the folder picker; returns focus to the button that opened it. */
	export async function closePicker() {
		const closing = picker;
		picker = null;
		await tick();
		if (!closing) return;
		const machineId = closing.computer.machine_id;
		let opener = `project-folder-change-${machineId}`;
		if (closing.purpose === 'offered') opener = `project-folder-offer-${machineId}`;
		document.getElementById(opener)?.focus();
	}

	function pickPath(path: string) {
		if (!picker) return;
		const machineId = picker.computer.machine_id;
		if (picker.purpose === 'offered') {
			draft = draftWithOffered(draft, machineId, path);
		} else {
			draft = { ...draft, mode: 'folder', paths: { ...draft.paths, [machineId]: path } };
		}
		void closePicker();
	}

	function removeOffered(machineId: string, path: string) {
		draft = draftWithoutOffered(draft, machineId, path);
	}

	function offeredFull(machineId: string): boolean {
		return (draft.offered[machineId] ?? []).length >= MAX_OFFERED_PER_COMPUTER;
	}

	function needsFolderMessage(machineId: string): string {
		const computer = findComputer(project, machineId);
		if (computer) {
			return $i18n.t('Choose a folder on {{name}}, or let the AI choose.', {
				name: computerName(computer)
			});
		}
		return $i18n.t('Choose a folder, or let the AI choose.');
	}

	/** Whether the draft can be saved; when it can't, says why in `error`. */
	export function check(): boolean {
		if (loadError || hidden || !project || !dirty) return true;
		// Nothing chosen yet, so there is nothing to save.
		if (draft.mode === 'unchosen') return true;
		const problem = draftProblem(draft);
		if (!problem) return true;
		error = needsFolderMessage(problem.machineId);
		return false;
	}

	/**
	 * Saves the choice; returns false when it could not be saved, with the reason in
	 * `error`. A folder being created passes its new id.
	 */
	export async function save(targetFolderId: string | null = folderId): Promise<boolean> {
		if (loadError || hidden || !project || !dirty || !targetFolderId) return true;
		const mode = draft.mode;
		if (mode === 'unchosen') return true;
		if (!check()) return false;
		error = '';
		try {
			if (mode === 'inherit') {
				project = await clearFolderProject(localStorage.token, targetFolderId);
			} else {
				const choice = choiceFromDraft(draft, mode, project.computers);
				project = await saveFolderProject(localStorage.token, targetFolderId, choice);
			}
			showProject(project);
			startFromSaved();
			announceProjectFolderChange(targetFolderId);
			return true;
		} catch (cause) {
			error = `${cause}`;
			return false;
		}
	}
</script>

{#if loading}
	<div class="flex items-center gap-2 py-2 text-xs text-gray-600 dark:text-gray-400">
		<Spinner className="size-3.5" />
		{$i18n.t('Loading project folder…')}
	</div>
{:else if loadError}
	<div class="flex flex-col gap-1 text-xs">
		<p role="alert" class="text-red-600 dark:text-red-400">
			{$i18n.t('The project folder could not be loaded: {{reason}}', { reason: loadError })}
		</p>
		<button
			type="button"
			class="min-h-11 self-start rounded-lg px-3 font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8"
			on:click={load}
		>
			{$i18n.t('Try again')}
		</button>
	</div>
{:else if !hidden && project}
	<section
		bind:this={sectionElement}
		aria-labelledby={labelledBy || undefined}
		class="flex flex-col gap-4"
	>
		{#if picker}
			<ProjectFolderPicker
				computer={picker.computer}
				initialPath={pickerStartPath(picker)}
				onBack={closePicker}
				onPick={pickPath}
			/>
		{:else}
			<p class="text-[0.8125rem] leading-relaxed text-gray-600 dark:text-gray-400">
				{$i18n.t(
					'Where the AI works in new chats from this folder. People you share it with choose their own.'
				)}
			</p>

			{#if draft.mode === 'unchosen'}
				<p
					class="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
				>
					{$i18n.t(
						'Someone else can change this folder’s instructions or files, so its new chats stay detached until you choose here.'
					)}
				</p>
			{/if}

			{#if !project.has_plans}
				<div class="flex flex-col gap-1.5">
					<p class="text-xs text-gray-700 dark:text-gray-300">
						{$i18n.t(
							'No AI plan is turned on. Turn one on in Settings → Machines to let the AI work in a folder on your computer.'
						)}
					</p>
					<button
						type="button"
						class="min-h-11 self-start rounded-lg border border-gray-200 px-3 text-xs font-medium hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-850 md:min-h-8"
						on:click={openMachineSettings}
					>
						{$i18n.t('Open Settings → Machines')}
					</button>
				</div>
			{:else if project.computers.length === 0}
				<div class="flex flex-col gap-1.5">
					<p class="text-xs text-gray-700 dark:text-gray-300">
						{$i18n.t('No computers yet. Connect one with the Buddy app.')}
					</p>
					<button
						type="button"
						class="min-h-11 self-start rounded-lg border border-gray-200 px-3 text-xs font-medium hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-850 md:min-h-8"
						on:click={openMachineSettings}
					>
						{$i18n.t('Connect a computer…')}
					</button>
				</div>
			{/if}

			<ChoicePicker
				id={runOnId}
				label={$i18n.t('Run on')}
				options={runOnOptions}
				value={runOnValue(draft)}
				stacked={$mobile}
				placeholder={$i18n.t('Choose where new chats run')}
				onChange={chooseRunOn}
			/>

			{#if draft.mode === 'chat'}
				<p class="text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t(
						'New chats here start detached: chat only, no files or commands. Each chat can still pick a computer before its first message.'
					)}
				</p>
			{/if}

			{#if choosingFolders}
				<div class="flex flex-col gap-2">
					<span
						id="project-folder-mode-label-{idPrefix}"
						class="text-xs font-medium text-gray-800 dark:text-gray-100"
					>
						{$i18n.t('Folder')}
					</span>
					<div
						role="radiogroup"
						aria-labelledby="project-folder-mode-label-{idPrefix}"
						class="grid grid-cols-2 gap-1 rounded-xl bg-gray-100 p-1 dark:bg-gray-850"
					>
						<button
							type="button"
							role="radio"
							id="project-folder-mode-auto-{idPrefix}"
							aria-checked={draft.mode === 'auto'}
							tabindex={draft.mode === 'auto' ? 0 : -1}
							class={segmentClass(draft.mode === 'auto')}
							on:click={() => setFolderMode('auto')}
							on:keydown={onFolderModeKeydown}
						>
							{$i18n.t('Let the AI choose')}
						</button>
						<button
							type="button"
							role="radio"
							id="project-folder-mode-folder-{idPrefix}"
							aria-checked={draft.mode === 'folder'}
							tabindex={draft.mode === 'folder' ? 0 : -1}
							class={segmentClass(draft.mode === 'folder')}
							on:click={() => setFolderMode('folder')}
							on:keydown={onFolderModeKeydown}
						>
							{$i18n.t('A specific folder')}
						</button>
					</div>

					{#if draft.mode === 'auto'}
						<p class="text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t(
								'Starts in its start folder on the computer the chat runs on. With Full access, the AI can work in any folder you mention.'
							)}
						</p>
					{:else}
						<ul class="flex flex-col gap-1.5">
							{#each folderComputers(project, chosenComputer) as computer (computer.machine_id)}
								<li
									class="flex flex-col gap-1.5 rounded-xl border border-gray-200 px-3 py-2 dark:border-gray-800 md:flex-row md:items-center md:gap-2"
								>
									<div class="min-w-0 flex-1">
										{#if !chosenComputer}
											<div class="text-sm">{computerName(computer)}</div>
										{/if}
										{#if draft.paths[computer.machine_id]}
											<div
												class="break-all font-mono text-xs text-gray-800 dark:text-gray-200"
												dir="ltr"
											>
												{draft.paths[computer.machine_id]}
											</div>
										{:else}
											<div class="text-xs text-gray-600 dark:text-gray-400">
												{$i18n.t('Not set')}
											</div>
										{/if}
										{#if pathProblem(computer.machine_id) === 'computer_changed' && draft.paths[computer.machine_id] === saved.paths[computer.machine_id]}
											<div class="text-xs text-amber-700 dark:text-amber-400">
												{$i18n.t('{{name}} was set up again. Choose the folder again.', {
													name: computerName(computer)
												})}
											</div>
										{/if}
									</div>
									<button
										type="button"
										class="min-h-11 w-full shrink-0 rounded-lg border border-gray-200 px-3 text-xs font-medium hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-850 md:min-h-8 md:w-auto"
										id="project-folder-change-{computer.machine_id}"
										on:click={() => openPicker(computer, 'folder')}
									>
										{#if draft.paths[computer.machine_id]}
											{$i18n.t('Change')}
										{:else}
											{$i18n.t('Choose folder')}
										{/if}
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				</div>

				{#if project.computers.length > 0}
					<div class="flex flex-col gap-2">
						<div>
							<span class="text-xs font-medium text-gray-800 dark:text-gray-100">
								{$i18n.t('Folders to offer')}
							</span>
							<p class="text-xs text-gray-600 dark:text-gray-400">
								{$i18n.t('More folders new chats here can pick from, up to 5 on each computer.')}
							</p>
						</div>
						{#each folderComputers(project, chosenComputer) as computer (computer.machine_id)}
							{#if (draft.offered[computer.machine_id] ?? []).length > 0}
								<ul class="flex flex-col gap-1" aria-label={computerName(computer)}>
									{#each draft.offered[computer.machine_id] as path (path)}
										<li
											class="flex items-center gap-2 rounded-lg bg-gray-50 py-1 pr-1 pl-3 dark:bg-gray-850"
										>
											<span
												class="min-w-0 flex-1 break-all font-mono text-xs text-gray-800 dark:text-gray-200"
												dir="ltr"
											>
												{path}
											</span>
											{#if !chosenComputer}
												<span class="shrink-0 text-xs text-gray-600 dark:text-gray-400">
													{computerName(computer)}
												</span>
											{/if}
											<button
												type="button"
												class="flex size-11 shrink-0 items-center justify-center rounded-lg text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 md:size-8"
												aria-label={$i18n.t('Remove {{path}}', { path })}
												on:click={() => removeOffered(computer.machine_id, path)}
											>
												<svg
													aria-hidden="true"
													class="size-3.5"
													viewBox="0 0 24 24"
													fill="none"
													stroke="currentColor"
													stroke-width="2.4"
													stroke-linecap="round"
												>
													<path d="M6 6l12 12M18 6L6 18" />
												</svg>
											</button>
										</li>
									{/each}
								</ul>
							{/if}
							<button
								type="button"
								id="project-folder-offer-{computer.machine_id}"
								class="min-h-11 self-start rounded-lg border border-dashed border-gray-300 px-3 text-xs font-medium hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-850 md:min-h-8"
								disabled={offeredFull(computer.machine_id)}
								on:click={() => openPicker(computer, 'offered')}
							>
								{#if chosenComputer || project.computers.length === 1}
									{$i18n.t('Add folder…')}
								{:else}
									{$i18n.t('Add a folder on {{name}}…', { name: computerName(computer) })}
								{/if}
							</button>
						{/each}
					</div>
				{/if}

				<ChoicePicker
					id={accessId}
					label={$i18n.t('Access')}
					options={accessOptions}
					value={draft.access}
					stacked={$mobile}
					onChange={chooseAccess}
				/>

				{#if effectiveAccess === 'full'}
					<p class="text-xs text-amber-800 dark:text-amber-300">
						{#if chosenComputer?.kind === 'server'}
							{$i18n.t('Inside the Buddy server, Full access can change Buddy’s own files.')}
						{:else}
							{$i18n.t(
								'A project folder is where the AI starts, not a fence. With Full access it can reach other folders, and the folder’s own CLAUDE.md, AGENTS.md, settings and hooks run as you. Only choose folders you trust.'
							)}
						{/if}
					</p>
				{:else if effectiveAccess === 'read' && draft.mode === 'auto'}
					<p class="text-xs text-gray-600 dark:text-gray-400">
						{$i18n.t(
							'With Read files, the AI may read only inside the folder it starts in. Choose a specific folder so it can read your project.'
						)}
					</p>
				{/if}
			{/if}

			<p class="text-xs text-gray-600 dark:text-gray-400">
				{$i18n.t(
					'Just for you. Subfolders use these unless they set their own. Each chat can still change them before its first message; chats already started keep where they run.'
				)}
			</p>
		{/if}

		{#if refreshError}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{refreshError}</p>
		{/if}
		{#if error}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
		{/if}
	</section>
{/if}
