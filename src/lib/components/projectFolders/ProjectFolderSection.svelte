<script lang="ts">
	// "Project folder" in a chat folder's settings: where Claude and ChatGPT
	// work for chats in it. Each person chooses for themselves.
	import { getContext, onMount } from 'svelte';

	import {
		clearFolderProject,
		getFolderProject,
		saveFolderProject,
		type FolderProject,
		type ProjectFolderChoice,
		type ProjectFolderComputer
	} from '$lib/apis/subscriptions';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ProjectFolderPicker from './ProjectFolderPicker.svelte';
	import {
		anyFullAccess,
		claudeReadHint,
		draftFromProject,
		sameDraft,
		waitsForOwnChoice,
		type ProjectDraft
	} from './project-folders';

	const i18n: any = getContext('i18n');

	export let folderId: string;
	// Set when the draft differs from what is saved.
	export let dirty = false;
	export let error = '';

	let project: FolderProject | null = null;
	let hidden = false;
	let loading = true;
	let draft: ProjectDraft = { mode: 'auto', paths: {} };
	let saved: ProjectDraft = { mode: 'auto', paths: {} };
	let pickingFor: ProjectFolderComputer | null = null;

	$: dirty = !sameDraft(draft, saved);
	$: workingComputers =
		project?.computers.filter((computer) => computer.providers.length > 0) ?? [];
	$: noFilePlans = project !== null && project.has_plans && workingComputers.length === 0;

	async function load() {
		loading = true;
		try {
			project = await getFolderProject(localStorage.token, folderId);
			hidden = !project.has_plans;
			saved = draftFromProject(project);
			draft = { mode: saved.mode, paths: { ...saved.paths } };
		} catch {
			// Not allowed to use computers, or the folder is gone: nothing to choose here.
			hidden = true;
		} finally {
			loading = false;
		}
	}

	onMount(load);

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

	function choosePath(path: string) {
		if (!pickingFor) return;
		draft = { mode: 'folder', paths: { ...draft.paths, [pickingFor.machine_id]: path } };
		pickingFor = null;
	}

	function setMode(mode: ProjectDraft['mode']) {
		draft = { mode, paths: draft.paths };
	}

	/** Saves the choice; returns false when it could not be saved, with the reason in `error`. */
	export async function save(): Promise<boolean> {
		if (hidden || !project || !dirty) return true;
		error = '';
		try {
			if (draft.mode === 'inherit') {
				project = await clearFolderProject(localStorage.token, folderId);
			} else {
				let paths: Record<string, string> = {};
				if (draft.mode === 'folder') paths = draft.paths;
				project = await saveFolderProject(localStorage.token, folderId, {
					mode: draft.mode,
					paths
				});
			}
			saved = draftFromProject(project);
			draft = { mode: saved.mode, paths: { ...saved.paths } };
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
{:else if !hidden && project}
	<section aria-labelledby="project-folder-heading" class="flex flex-col gap-2">
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
				onBack={() => (pickingFor = null)}
				onPick={choosePath}
			/>
		{:else if noFilePlans}
			<p class="text-xs text-gray-600 dark:text-gray-300">
				{$i18n.t(
					"Your plans are set to Chat only, so they don't open folders. Change their access in Settings → Machines."
				)}
			</p>
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

		{#if error}
			<p role="alert" class="text-xs text-red-600 dark:text-red-400">{error}</p>
		{/if}
	</section>
{/if}
