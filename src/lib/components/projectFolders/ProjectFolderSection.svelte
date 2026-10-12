<script lang="ts">
	// "Project folder" in a chat folder's settings: where Claude and ChatGPT
	// work for chats in it. Each person chooses for themselves.
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		clearFolderProject,
		getFolderProject,
		saveFolderProject,
		type FolderPlan,
		type FolderProject,
		type ProjectFolderChoice,
		type ProjectFolderComputer
	} from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { showSettings } from '$lib/stores';
	import { accessGrantedMessage } from '$lib/components/admin/Settings/Connections/plan-access';
	import PlanAccessRequest from './PlanAccessRequest.svelte';
	import ProjectFolderPicker from './ProjectFolderPicker.svelte';
	import { announceProjectFolderChange } from './project-folder-changes';
	import {
		anyFullAccess,
		claudeReadHint,
		draftFromProject,
		fileAccessNeed,
		focusWasLost,
		plansToAllow,
		plansWithoutComputer,
		sameDraft,
		waitsForOwnChoice,
		type FileAccess,
		type FileAccessNeed,
		type ProjectDraft
	} from './project-folders';

	const i18n: any = getContext('i18n');

	export let folderId: string;
	// Set when the draft differs from what is saved.
	export let dirty = false;
	export let error = '';
	// Set when the choice could not be loaded; there is then nothing to save.
	export let loadError = '';

	let project: FolderProject | null = null;
	let hidden = false;
	let loading = true;
	let draft: ProjectDraft = { mode: 'auto', paths: {} };
	let saved: ProjectDraft = { mode: 'auto', paths: {} };
	let pickingFor: ProjectFolderComputer | null = null;
	// Why loading again after a plan change failed; what was loaded before still saves.
	let refreshError = '';
	// Why an Allow was refused, when its request could not show it.
	let refusal = '';
	// The dialog can close while an Allow is still saving.
	let destroyed = false;

	onDestroy(() => {
		destroyed = true;
	});
	let sectionElement: HTMLElement | null = null;

	$: dirty = !sameDraft(draft, saved);
	$: need = project ? fileAccessNeed(project) : 'ready';
	$: allowablePlans = project ? plansToAllow(project) : [];
	$: homelessPlans = project ? plansWithoutComputer(project) : [];
	// The request under the choices hides while picking a folder, and when the folder is just chat.
	$: compactHidden = pickingFor !== null || draft.mode === 'chat';

	/** Shows a loaded project; the caller decides what happens to the draft. */
	function showProject(loaded: FolderProject | null) {
		// Null: not allowed to use computers, or the folder is gone; nothing to choose here.
		hidden = loaded === null;
		project = loaded;
		if (loaded) saved = draftFromProject(loaded);
	}

	function startFromSaved() {
		draft = { mode: saved.mode, paths: { ...saved.paths } };
	}

	async function load() {
		loading = true;
		loadError = '';
		try {
			showProject(await getFolderProject(localStorage.token, folderId));
			startFromSaved();
		} catch (cause) {
			project = null;
			loadError = `${cause}`;
		} finally {
			loading = false;
		}
	}

	onMount(load);

	/** Loads again after a plan changed, keeping a choice the person has not saved yet. */
	async function refresh() {
		refusal = '';
		let loaded: FolderProject | null;
		try {
			loaded = await getFolderProject(localStorage.token, folderId);
		} catch (cause) {
			refreshError = $i18n.t('The project folder could not be loaded: {{reason}}', {
				reason: `${cause}`
			});
			return;
		}
		refreshError = '';
		// Checked once the plans are back: the person may have chosen something meanwhile.
		const unsaved = !sameDraft(draft, saved);
		showProject(loaded);
		if (!unsaved) startFromSaved();
	}

	/** Puts keyboard focus back in the section when what had it was replaced. */
	async function keepFocusInSection() {
		await tick();
		if (!focusWasLost() || !sectionElement) return;
		const name = `project-folder-mode-${folderId}`;
		const choices = Array.from(
			sectionElement.querySelectorAll<HTMLInputElement>(`input[name="${name}"]`)
		);
		let target: HTMLElement | null = choices.find((choice) => choice.checked) ?? choices[0] ?? null;
		if (!target) target = sectionElement.querySelector<HTMLElement>('button');
		target?.focus();
	}

	// Set while Settings → Machines is open from here; plans may change there.
	let waitingForSettings = false;

	function openMachineSettings() {
		waitingForSettings = true;
		showSettings.set('machines');
	}

	async function settingsClosed() {
		waitingForSettings = false;
		await refresh();
		announceProjectFolderChange(folderId);
		await keepFocusInSection();
	}

	$: if (waitingForSettings && !$showSettings) void settingsClosed();

	function machineSettingsReason(currentNeed: FileAccessNeed, homeless: FolderPlan[]): string {
		if (currentNeed === 'turn_on') {
			return $i18n.t(
				'No AI plan is turned on. Turn one on in Settings → Machines to let the AI work in a folder on your computer.'
			);
		}
		if (currentNeed === 'computer') {
			return $i18n.t(
				'The plans you turned on have no computer they can use. Choose one in Settings → Machines.'
			);
		}
		// Beside a Chat-only plan that can be allowed here.
		if (homeless.length === 1) {
			return $i18n.t('{{name}} has no computer it can use. Choose one in Settings → Machines.', {
				name: homeless[0].name
			});
		}
		return $i18n.t(
			'Some of your plans have no computer they can use. Choose one in Settings → Machines.'
		);
	}

	function refusalMessage(plan: FolderPlan, access: FileAccess, reason: string): string {
		const now = project?.plans.find((candidate) => candidate.id === plan.id);
		// The server can save the access and then turn the plan off, when a running
		// reply cannot be confirmed stopped.
		if (now && now.access === access && !now.enable) {
			return $i18n.t('{{name}} was given the access but then turned off: {{reason}}', {
				name: plan.name,
				reason
			});
		}
		return $i18n.t('{{name}} was not given access: {{reason}}', { name: plan.name, reason });
	}

	async function planRefused(plan: FolderPlan, access: FileAccess, reason: string) {
		const now = project?.plans.find((candidate) => candidate.id === plan.id);
		// The save can go through while its answer is lost; the reloaded plan shows it.
		if (now && now.enable && now.access === access) {
			const computerName = now.machine?.name ?? plan.machine?.name ?? '';
			toast.success(accessGrantedMessage($i18n, plan.name, access, computerName));
			await planAllowed();
			return;
		}
		const message = refusalMessage(plan, access, reason);
		// Closed, or the reload found the section has nothing to show (no access, folder gone).
		if (destroyed || hidden || !project) {
			toast.error(message);
			return;
		}
		refusal = message;
		await keepFocusInSection();
	}

	async function planAllowed() {
		// The folder page chip and a chat's Project tab list this folder's computers too.
		announceProjectFolderChange(folderId);
		await keepFocusInSection();
	}

	function computerProviders(computer: ProjectFolderComputer): string {
		return computer.providers.map((provider) => provider.name).join(', ');
	}

	function inheritedSummary(choice: ProjectFolderChoice): string {
		if (choice.mode === 'chat') return $i18n.t('No files, just chat');
		if (choice.mode === 'auto') return $i18n.t('Let the AI choose');
		const parts = [];
		for (const [machineId, saved] of Object.entries(choice.paths)) {
			const computer = project?.computers.find((candidate) => candidate.machine_id === machineId);
			parts.push(`${computer?.name ?? machineId} · ${saved.path}`);
		}
		return parts.join(', ');
	}

	function pathProblem(machineId: string): string | null {
		return project?.own?.paths[machineId]?.problem ?? null;
	}

	async function closePicker() {
		const machineId = pickingFor?.machine_id;
		pickingFor = null;
		await tick();
		// Back where the person was: that computer's Change button.
		if (machineId) document.getElementById(`project-folder-change-${machineId}`)?.focus();
	}

	function choosePath(path: string) {
		if (!pickingFor) return;
		draft = { mode: 'folder', paths: { ...draft.paths, [pickingFor.machine_id]: path } };
		void closePicker();
	}

	function setMode(mode: ProjectDraft['mode']) {
		draft = { mode, paths: draft.paths };
	}

	/** Saves the choice; returns false when it could not be saved, with the reason in `error`. */
	export async function save(): Promise<boolean> {
		if (loadError || hidden || !project || !dirty) return true;
		const mode = draft.mode;
		// Nothing chosen yet, so there is nothing to save.
		if (mode === 'unchosen') return true;
		error = '';
		try {
			if (mode === 'inherit') {
				project = await clearFolderProject(localStorage.token, folderId);
			} else {
				let paths: Record<string, string> = {};
				if (mode === 'folder') paths = draft.paths;
				project = await saveFolderProject(localStorage.token, folderId, { mode, paths });
			}
			showProject(project);
			startFromSaved();
			announceProjectFolderChange(folderId);
			return true;
		} catch (cause) {
			error = `${cause}`;
			return false;
		}
	}
</script>

{#if loading}
	<div class="flex items-center gap-2 py-2 text-xs text-gray-500">
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
		aria-labelledby="project-folder-heading"
		class="flex flex-col gap-2"
	>
		<div>
			<h3 id="project-folder-heading" class="text-xs text-gray-500">{$i18n.t('Project folder')}</h3>
			<p class="mt-0.5 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{$i18n.t(
					'Where Claude and ChatGPT work for chats in this folder. Other models never touch your computer.'
				)}
			</p>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-500">
				{$i18n.t('Just for you. People you share this folder with choose their own.')}
			</p>
		</div>

		{#if pickingFor}
			<ProjectFolderPicker
				computer={pickingFor}
				initialPath={draft.paths[pickingFor.machine_id] ?? ''}
				onBack={closePicker}
				onPick={choosePath}
			/>
		{:else if need !== 'ready'}
			{#if need === 'allow'}
				<PlanAccessRequest
					plans={allowablePlans}
					reload={refresh}
					onAllowed={planAllowed}
					onRefused={planRefused}
				/>
			{/if}
			{#if need !== 'allow' || homelessPlans.length > 0}
				<div class="flex flex-col gap-1.5">
					<p class="text-xs text-gray-600 dark:text-gray-300">
						{machineSettingsReason(need, homelessPlans)}
					</p>
					<button
						type="button"
						class="min-h-11 self-start rounded-lg border border-gray-100 px-3 text-xs font-medium hover:bg-gray-100 dark:border-gray-850 dark:hover:bg-gray-850 md:min-h-8"
						on:click={openMachineSettings}
					>
						{$i18n.t('Open Settings → Machines')}
					</button>
				</div>
			{/if}
		{:else}
			<div class="flex flex-col gap-1.5" role="radiogroup" aria-labelledby="project-folder-heading">
				{#if waitsForOwnChoice(project)}
					<p class="text-[0.6875rem] text-amber-700 dark:text-amber-400">
						{$i18n.t(
							"This folder adds instructions or files that someone else can change, so its chats don't use files on your computers until you choose here."
						)}
					</p>
				{/if}
				{#if project.inherited && !project.shared_instructions}
					<label
						class="flex min-h-11 cursor-pointer items-start gap-2 rounded-xl border border-gray-100 p-2.5 dark:border-gray-850"
					>
						<input
							type="radio"
							name="project-folder-mode-{folderId}"
							class="mt-1"
							checked={draft.mode === 'inherit'}
							on:change={() => setMode('inherit')}
						/>
						<span class="min-w-0">
							<span class="block text-sm">
								{$i18n.t('Same as “{{name}}”', { name: project.inherited.folder_name })}
							</span>
							<span class="block break-all text-[0.6875rem] text-gray-500">
								{inheritedSummary(project.inherited)}
							</span>
						</span>
					</label>
				{/if}

				<label
					class="flex min-h-11 cursor-pointer items-start gap-2 rounded-xl border border-gray-100 p-2.5 dark:border-gray-850"
				>
					<input
						type="radio"
						name="project-folder-mode-{folderId}"
						class="mt-1"
						checked={draft.mode === 'auto'}
						on:change={() => setMode('auto')}
					/>
					<span class="min-w-0">
						<span class="block text-sm">{$i18n.t('Let the AI choose')}</span>
						<span class="block text-[0.6875rem] text-gray-500">
							{$i18n.t(
								'Starts in your start folder on each computer. With Full access, Claude and ChatGPT can work in any folder you mention.'
							)}
						</span>
					</span>
				</label>

				<div class="rounded-xl border border-gray-100 p-2.5 dark:border-gray-850">
					<label class="flex min-h-11 cursor-pointer items-start gap-2 md:min-h-0">
						<input
							type="radio"
							name="project-folder-mode-{folderId}"
							class="mt-1"
							checked={draft.mode === 'folder'}
							on:change={() => setMode('folder')}
						/>
						<span class="min-w-0">
							<span class="block text-sm">{$i18n.t('A specific folder')}</span>
							<span class="block text-[0.6875rem] text-gray-500">
								{$i18n.t('Start in an exact folder on your computer.')}
							</span>
						</span>
					</label>
					{#if draft.mode === 'folder'}
						<ul class="mt-2 flex flex-col gap-1.5 pl-6">
							{#each project.computers as computer (computer.machine_id)}
								<li class="flex flex-col gap-1 md:flex-row md:items-center md:gap-2">
									<div class="min-w-0 flex-1">
										<div class="text-sm">
											{computer.name}
											{#if computer.providers.length}
												<span class="text-[0.6875rem] text-gray-500">
													· {computerProviders(computer)}</span
												>
											{/if}
										</div>
										{#if draft.paths[computer.machine_id]}
											<div
												class="break-all font-mono text-[0.6875rem] text-gray-600 dark:text-gray-300"
												dir="ltr"
											>
												{draft.paths[computer.machine_id]}
											</div>
										{:else}
											<div class="text-[0.6875rem] text-gray-400">{$i18n.t('Not set')}</div>
										{/if}
										{#if pathProblem(computer.machine_id) === 'computer_changed' && draft.paths[computer.machine_id] === saved.paths[computer.machine_id]}
											<div class="text-[0.6875rem] text-amber-700 dark:text-amber-400">
												{$i18n.t('{{name}} was set up again. Choose the folder again.', {
													name: computer.name
												})}
											</div>
										{/if}
									</div>
									<button
										type="button"
										class="min-h-11 w-full shrink-0 rounded-lg px-3 text-xs font-medium hover:bg-gray-100 dark:hover:bg-gray-850 md:min-h-8 md:w-auto"
										id="project-folder-change-{computer.machine_id}"
										on:click={() => (pickingFor = computer)}
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

				<label
					class="flex min-h-11 cursor-pointer items-start gap-2 rounded-xl border border-gray-100 p-2.5 dark:border-gray-850"
				>
					<input
						type="radio"
						name="project-folder-mode-{folderId}"
						class="mt-1"
						checked={draft.mode === 'chat'}
						on:change={() => setMode('chat')}
					/>
					<span class="min-w-0">
						<span class="block text-sm">{$i18n.t('No files, just chat')}</span>
						<span class="block text-[0.6875rem] text-gray-500">
							{$i18n.t(
								'Chats here never read or change files on your computers, whatever your plans allow.'
							)}
						</span>
					</span>
				</label>
			</div>

			<div class="flex flex-col gap-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
				{#if claudeReadHint(project) && (draft.mode === 'auto' || draft.mode === 'inherit')}
					<p>
						{$i18n.t(
							'With Read files, Claude reads only inside the folder it starts in. Choose a specific folder so it can read your project.'
						)}
					</p>
				{/if}
				{#if anyFullAccess(project) && draft.mode !== 'chat'}
					<p>
						{$i18n.t(
							"A project folder is where Claude and ChatGPT start, not a fence. With Full access they can still reach other folders, and the folder's own CLAUDE.md, AGENTS.md, settings and hooks run as you. Only choose folders you trust."
						)}
					</p>
				{/if}
				<p>
					{$i18n.t(
						'Changing this starts a fresh Claude or ChatGPT session for chats already in this folder. Their history comes along.'
					)}
				</p>
			</div>
		{/if}

		<!-- Another plan already opens folders; these could too. Hidden while picking a
		     folder, and moot when this folder is just chat, but kept mounted so an Allow
		     in progress stays busy. -->
		{#if need === 'ready' && allowablePlans.length > 0}
			<div hidden={compactHidden}>
				<PlanAccessRequest
					compact
					hidden={compactHidden}
					plans={allowablePlans}
					reload={refresh}
					onAllowed={planAllowed}
					onRefused={planRefused}
				/>
			</div>
		{/if}

		{#if refusal}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{refusal}</p>
		{/if}
		{#if refreshError}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{refreshError}</p>
		{/if}
		{#if error}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
		{/if}
	</section>
{/if}
