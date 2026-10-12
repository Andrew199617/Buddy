<script lang="ts">
	// Create Folder / Edit Folder: a name and background image, instructions,
	// and two quiet rows that open in place: Knowledge and the Project folder.
	import { getContext, tick } from 'svelte';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '../icons/XMark.svelte';

	import { toast } from 'svelte-sonner';
	import { user, config, mobile } from '$lib/stores';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import Knowledge from '$lib/components/workspace/Models/Knowledge.svelte';
	import ProjectFolderSection from '$lib/components/projectFolders/ProjectFolderSection.svelte';
	import { getFolderById } from '$lib/apis/folders';
	const i18n: any = getContext('i18n');

	type FolderForm = {
		name: string;
		meta: FolderMeta;
		data: FolderData;
		parent_id?: string | null;
	};
	// Kept whole on save: they also hold settings set elsewhere, like the folder's icon.
	type FolderMeta = { background_image_url: string | null; [key: string]: unknown };
	type FolderData = { system_prompt: string; files: any[]; [key: string]: unknown };

	export let show = false;
	// Saves the folder; when creating, resolves to the new folder, or null if none was made.
	export let onSubmit: (form: FolderForm) => unknown = () => null;

	export let folderId = null;
	export let parentId = null;
	export let edit = false;

	let folder: { name?: string; meta?: FolderMeta; data?: FolderData } | null = null;
	let name = '';
	let meta: FolderMeta = {
		background_image_url: null
	};
	let data: FolderData = {
		system_prompt: '',
		files: []
	};

	let loading = false;
	let projectSection: ProjectFolderSection;
	let knowledgeOpen = false;
	let projectOpen = false;
	let projectSummary = '';
	let projectError = '';
	let projectLoadError = '';
	let dialogRoot: HTMLElement | null = null;
	let projectPicking = false;
	// Phones show the folder on one screen and each row's settings on their own.
	type Screen = 'main' | 'knowledge' | 'project';
	let screen: Screen = 'main';
	// A new folder starts with just its name; each part shows once its tile is chosen.
	type Part = 'instructions' | 'knowledge' | 'project';
	let revealed: Record<Part, boolean> = { instructions: false, knowledge: false, project: false };
	// Set once the project folder section knows this person may use computers.
	let projectAvailable = false;
	type Tile = { part: Part; name: string; description: string; icon: string[] };

	$: canEditInstructions =
		$user?.role === 'admin' || ($user?.permissions?.chat?.system_prompt ?? true);
	$: knowledgeCount = (data?.files ?? []).length;
	// A save the project folder refused shows its reason where the choice is made.
	$: formId = `folder-form-${folderId ?? 'new'}`;
	$: projectKey = folderId ?? 'new';
	// Edit Folder holds the section for its folder; Create Folder for the one it will make.
	$: projectHosted = !edit || Boolean(folderId);
	$: tiles = describeTiles(revealed, edit, canEditInstructions, projectAvailable);
	$: if (!$mobile) screen = 'main';
	$: layout = layoutFor(
		$mobile,
		shownOnMain($mobile, screen) && (edit || revealed.knowledge || revealed.project)
	);

	const submitHandler = async () => {
		loading = true;

		if ((data?.files ?? []).some((file) => file.status === 'uploading')) {
			toast.error($i18n.t('Please wait until all files are uploaded.'));
			loading = false;
			return;
		}

		// Check folder max file count limit
		const maxFileCount = $config?.features?.folder_max_file_count ?? '';
		if (maxFileCount && (data?.files ?? []).length > maxFileCount) {
			toast.error(
				$i18n.t('Maximum number of files per folder is {{max}}.', { max: maxFileCount ?? 0 })
			);
			loading = false;
			return;
		}

		// The project folder is saved first: if it is refused, nothing else changes.
		// A new folder's is checked now and saved once the folder exists.
		let projectReady = true;
		if (edit) projectReady = (await projectSection?.save()) !== false;
		else if (projectSection) projectReady = projectSection.check();
		if (!projectReady) {
			// Show why, every time: on a phone the reason is on the project screen.
			openProjectSettings();
			loading = false;
			return;
		}

		const result = await onSubmit({
			name,
			meta,
			data,
			parent_id: edit ? undefined : parentId
		});
		// A folder that could not be made keeps the dialog open, with what was typed.
		if (!edit && !result) {
			loading = false;
			return;
		}
		const created = result as { id?: string } | null;
		if (!edit && created?.id) await saveNewFolderProject(created.id);
		show = false;
		loading = false;
	};

	const init = async () => {
		projectError = '';
		knowledgeOpen = false;
		projectOpen = false;
		screen = 'main';
		revealed = { instructions: false, knowledge: false, project: false };
		if (folderId) {
			folder = await getFolderById(localStorage.token, folderId).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			name = folder?.name ?? '';
			meta = folder?.meta || {
				background_image_url: null
			};
			data = folder?.data || {
				system_prompt: '',
				files: []
			};
		}

		focusInput();
	};

	const focusInput = async () => {
		// On a phone that would bring up the keyboard before the person asked.
		if ($mobile) return;
		await tick();
		const input = document.getElementById('folder-name') as HTMLInputElement;
		if (input) {
			input.focus();
			input.select();
		}
	};

	function chooseBackgroundImage() {
		document.getElementById('folder-background-image-input')?.click();
	}

	function removeBackgroundImage() {
		meta.background_image_url = null;
	}

	function readBackgroundImage(event: Event) {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		const accepted = ['image/gif', 'image/webp', 'image/jpeg', 'image/png'];
		if (!file || !accepted.includes(file.type)) {
			// Clear the input so the same file can be tried again.
			input.value = '';
			return;
		}
		const reader = new FileReader();
		reader.onload = (loaded) => {
			meta.background_image_url = `${loaded.target?.result}`;
		};
		reader.readAsDataURL(file);
	}

	function imageTileStyle(url: string | null): string {
		if (!url) return '';
		return `background-image: url(${url})`;
	}

	function imageTileLabel(url: string | null): string {
		if (url) return $i18n.t('Change background image');
		return $i18n.t('Add background image');
	}

	/** A new folder's project defaults are saved once it exists. */
	async function saveNewFolderProject(newFolderId: string) {
		if (!projectSection) return;
		const saved = await projectSection.save(newFolderId);
		if (saved) return;
		toast.error(
			$i18n.t(
				'“{{name}}” was created, but its project folder wasn’t saved: {{reason}} Open Edit folder to try again.',
				{ name: name.trim(), reason: projectError }
			)
		);
	}

	const TILE_ICONS: Record<Part, string[]> = {
		instructions: ['M4 6h16M4 12h10M4 18h7'],
		knowledge: ['M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z', 'M4 21V5', 'M9 8h6'],
		project: [
			'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
			'M9 12l2 2-2 2',
			'M13 16h3'
		]
	};

	function describeTiles(
		shown: Record<Part, boolean>,
		editing: boolean,
		instructionsAllowed: boolean,
		projectAllowed: boolean
	): Tile[] {
		if (editing) return [];
		const result: Tile[] = [];
		if (instructionsAllowed && !shown.instructions) {
			result.push({
				part: 'instructions',
				name: $i18n.t('Instructions'),
				description: $i18n.t('How chats behave'),
				icon: TILE_ICONS.instructions
			});
		}
		if (!shown.knowledge) {
			result.push({
				part: 'knowledge',
				name: $i18n.t('Knowledge'),
				description: $i18n.t('Files to draw on'),
				icon: TILE_ICONS.knowledge
			});
		}
		if (projectAllowed && !shown.project) {
			result.push({
				part: 'project',
				name: $i18n.t('Project folder'),
				description: $i18n.t('Where the AI works'),
				icon: TILE_ICONS.project
			});
		}
		return result;
	}

	function partShown(part: Part, editing: boolean, shown: Record<Part, boolean>): boolean {
		return editing || shown[part];
	}

	async function revealPart(part: Part) {
		revealed = { ...revealed, [part]: true };
		if (part === 'instructions') {
			await tick();
			document.querySelector<HTMLElement>(`#${formId}-instructions textarea`)?.focus();
			return;
		}
		if ($mobile) {
			await openScreen(part);
			return;
		}
		if (part === 'knowledge') knowledgeOpen = true;
		else projectOpen = true;
		await tick();
		document.getElementById(`${formId}-row-${part}`)?.focus();
	}

	function openProjectSettings() {
		revealed = { ...revealed, project: true };
		projectOpen = true;
		if ($mobile) screen = 'project';
	}

	/** Leaving the project settings also leaves a folder picker open in them. */
	async function leavePicker() {
		if (projectPicking) await projectSection?.closePicker(false);
	}

	async function toggleProjectRow() {
		if (projectOpen) await leavePicker();
		projectOpen = !projectOpen;
	}

	async function openScreen(next: Screen) {
		screen = next;
		await tick();
		document.getElementById(`${formId}-back`)?.focus();
	}

	async function backToFolder() {
		const leaving = screen;
		await leavePicker();
		screen = 'main';
		await tick();
		document.getElementById(`${formId}-row-${leaving}`)?.focus();
	}

	function projectRowSummary(summary: string, loadError: string): string {
		if (loadError) return $i18n.t('Couldn’t load');
		return summary;
	}

	// People who may not use computers have nothing to set here; a load error still shows.
	function projectRowShown(available: boolean, loadError: string): boolean {
		return available || Boolean(loadError);
	}

	function shownOnMain(phone: boolean, current: Screen): boolean {
		return !phone || current === 'main';
	}

	function panelShown(phone: boolean, current: Screen, panel: Screen, open: boolean): boolean {
		if (phone) return current === panel;
		return open;
	}

	type Layout = {
		modalSize: 'full' | 'md';
		modalContainer: string;
		modalSurface: string;
		root: string;
		tiles: string;
		form: string;
		body: string;
		rows: string;
		knowledgePanel: string;
		projectPanel: string;
	};

	// A full-screen sheet on phones, a centered dialog elsewhere.
	function layoutFor(phone: boolean, onMain: boolean): Layout {
		let rows = '';
		if (onMain) rows = 'border-t border-gray-100 dark:border-gray-850';
		if (phone) {
			return {
				modalSize: 'full',
				modalContainer: '',
				modalSurface: 'h-full bg-white dark:bg-gray-900',
				root: 'h-full',
				tiles: 'grid-cols-1',
				form: 'min-h-0 flex-1 overflow-y-auto',
				body: 'px-5 pt-5 pb-[max(env(safe-area-inset-bottom),2rem)]',
				rows,
				knowledgePanel: '',
				projectPanel: ''
			};
		}
		return {
			modalSize: 'md',
			modalContainer: 'p-3',
			modalSurface: 'bg-white dark:bg-gray-900 rounded-4xl',
			root: '',
			tiles: 'grid-cols-3',
			form: '',
			body: 'px-7 pt-3',
			rows,
			knowledgePanel: 'border-b border-gray-100 pt-1 pb-4 pl-[50px] dark:border-gray-850',
			projectPanel: 'pt-1 pb-4 pl-[50px]'
		};
	}

	// Phones go to a screen; wider screens open the row in place.
	function rowChevronClass(phone: boolean, open: boolean): string {
		if (phone) return '-rotate-90';
		if (open) return 'rotate-180';
		return '';
	}

	function screenTitle(current: Screen): string {
		if (current === 'knowledge') return $i18n.t('Knowledge');
		if (current === 'project') return $i18n.t('Project folder');
		if (edit) return $i18n.t('Edit folder');
		return $i18n.t('New folder');
	}

	/** Whether this dialog is the top one, not under Settings or a confirmation opened from it. */
	function isTopDialog(): boolean {
		const own = dialogRoot?.closest('.modal');
		const modals = document.getElementsByClassName('modal');
		return Boolean(own) && modals[modals.length - 1] === own;
	}

	/** Escape first leaves a folder picker or a phone screen, before it closes the dialog. */
	function stepBackOnEscape(event: KeyboardEvent) {
		if (!show || event.key !== 'Escape' || !isTopDialog()) return;
		if (projectPicking) {
			event.stopPropagation();
			event.preventDefault();
			void projectSection?.closePicker();
			return;
		}
		if ($mobile && screen !== 'main') {
			event.stopPropagation();
			event.preventDefault();
			void backToFolder();
		}
	}

	function knowledgeSummary(count: number): string {
		if (count === 0) return $i18n.t('None yet');
		if (count === 1) return $i18n.t('1 source');
		return $i18n.t('{{count}} sources', { count });
	}

	$: if (show) {
		init();
	}

	$: if (!show && !edit) {
		name = '';
		meta = {
			background_image_url: null
		};
		data = {
			system_prompt: '',
			files: []
		};
	}
</script>

<svelte:window on:keydown|capture={stepBackOnEscape} />

<Modal
	size={layout.modalSize}
	containerClassName={layout.modalContainer}
	className={layout.modalSurface}
	bind:show
>
	<div bind:this={dialogRoot} class="flex flex-col text-gray-900 dark:text-gray-100 {layout.root}">
		{#if $mobile}
			<div
				class="grid shrink-0 grid-cols-3 items-center border-b border-gray-100 px-2 pt-[max(env(safe-area-inset-top),0.75rem)] pb-2 dark:border-gray-850"
			>
				{#if screen === 'main'}
					<button
						type="button"
						class="h-11 justify-self-start px-3 text-base text-gray-700 dark:text-gray-300"
						on:click={() => {
							show = false;
						}}
					>
						{$i18n.t('Cancel')}
					</button>
				{:else}
					<button
						type="button"
						id="{formId}-back"
						class="flex h-11 items-center gap-0.5 justify-self-start px-2.5 text-base text-gray-700 dark:text-gray-300"
						aria-label={$i18n.t('Back to folder')}
						on:click={backToFolder}
					>
						<svg
							aria-hidden="true"
							class="size-5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<path d="M15 6l-6 6 6 6" />
						</svg>
						{$i18n.t('Folder')}
					</button>
				{/if}
				<h2 class="truncate text-center text-base font-semibold">{screenTitle(screen)}</h2>
				{#if screen === 'main'}
					<button
						type="submit"
						form={formId}
						class="flex h-11 items-center gap-2 justify-self-end rounded-full bg-gray-900 px-5 text-[0.9375rem] font-medium text-white disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900"
						disabled={loading}
					>
						{#if edit}
							{$i18n.t('Save')}
						{:else}
							{$i18n.t('Create')}
						{/if}
						{#if loading}
							<Spinner className="size-4" />
						{/if}
					</button>
				{/if}
			</div>
		{:else}
			<div class="flex items-center justify-between gap-2 pt-5 pr-5 pb-1 pl-7">
				<h2 class="text-[1.0625rem] font-semibold">{screenTitle('main')}</h2>
				<button
					type="button"
					aria-label={$i18n.t('Close')}
					class="flex size-10 items-center justify-center rounded-xl text-gray-600 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850"
					on:click={() => {
						show = false;
					}}
				>
					<XMark className={'size-[18px]'} />
				</button>
			</div>
		{/if}

		<form
			id={formId}
			class="flex flex-col {layout.form}"
			on:submit|preventDefault={() => {
				submitHandler();
			}}
		>
			<div class="flex flex-col gap-5 {layout.body}">
				<input
					id="folder-background-image-input"
					type="file"
					hidden
					accept="image/*"
					on:change={readBackgroundImage}
				/>

				<div class="flex flex-col gap-1.5" hidden={!shownOnMain($mobile, screen)}>
					<div class="flex items-center gap-3.5">
						<button
							type="button"
							class="flex size-[52px] shrink-0 items-center justify-center overflow-hidden rounded-[14px] border border-dashed border-gray-300 bg-gray-50 bg-cover bg-center text-gray-600 transition hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-850 dark:text-gray-400"
							style={imageTileStyle(meta?.background_image_url)}
							aria-label={imageTileLabel(meta?.background_image_url)}
							on:click={chooseBackgroundImage}
						>
							{#if !meta?.background_image_url}
								<svg
									aria-hidden="true"
									class="size-5"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.8"
									stroke-linecap="round"
									stroke-linejoin="round"
								>
									<rect x="3" y="4" width="18" height="16" rx="3" />
									<circle cx="9" cy="10" r="1.6" />
									<path d="M21 16l-5-5-8 9" />
								</svg>
							{/if}
						</button>
						<label class="flex min-w-0 flex-1 flex-col gap-1">
							<span class="text-xs text-gray-600 dark:text-gray-400">{$i18n.t('Name')}</span>
							<input
								id="folder-name"
								class="w-full min-w-0 border-b-[1.5px] border-gray-200 bg-transparent pt-0.5 pb-2 text-xl font-medium outline-hidden placeholder:text-gray-400 focus:border-gray-900 dark:border-gray-800 dark:placeholder:text-gray-600 dark:focus:border-gray-100 md:text-[1.375rem]"
								type="text"
								bind:value={name}
								placeholder={$i18n.t('Folder name')}
								autocomplete="off"
							/>
						</label>
					</div>
					{#if meta?.background_image_url}
						<button
							type="button"
							class="min-h-11 self-start rounded-lg px-2 text-xs text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850 md:min-h-8"
							on:click={removeBackgroundImage}
						>
							{$i18n.t('Remove background image')}
						</button>
					{/if}
				</div>

				{#if canEditInstructions && partShown('instructions', edit, revealed)}
					<div
						id="{formId}-instructions"
						class="flex flex-col gap-2"
						hidden={!shownOnMain($mobile, screen)}
					>
						<span class="text-sm font-medium">{$i18n.t('Instructions')}</span>
						<Textarea
							className="w-full rounded-[14px] border border-gray-200 bg-gray-50 px-3.5 py-3 text-base leading-relaxed outline-hidden placeholder:text-gray-400 dark:border-gray-800 dark:bg-gray-850 dark:placeholder:text-gray-600 md:text-sm"
							ariaLabel={$i18n.t('Instructions')}
							placeholder={$i18n.t(
								'How should chats in this folder behave? For example: answer as a friendly code reviewer.'
							)}
							rows={3}
							minSize={92}
							maxSize={200}
							bind:value={data.system_prompt}
						/>
					</div>
				{/if}

				{#if tiles.length > 0}
					<div class="flex flex-col gap-1.5" hidden={!shownOnMain($mobile, screen)}>
						<span class="pb-0.5 text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('Optional — you can add these later')}
						</span>
						<div class="grid gap-2 {layout.tiles}">
							{#each tiles as tile (tile.part)}
								<button
									type="button"
									class="flex min-h-[92px] flex-col items-start gap-2.5 rounded-[14px] border border-gray-200 bg-gray-50 p-3.5 text-left transition hover:bg-gray-100 dark:border-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 max-md:min-h-16 max-md:flex-row max-md:items-center"
									on:click={() => revealPart(tile.part)}
								>
									<svg
										aria-hidden="true"
										class="size-[18px] shrink-0 text-gray-700 dark:text-gray-300"
										viewBox="0 0 24 24"
										fill="none"
										stroke="currentColor"
										stroke-width="1.8"
										stroke-linecap="round"
										stroke-linejoin="round"
									>
										{#each tile.icon as path}<path d={path} />{/each}
									</svg>
									<span class="flex flex-col gap-0.5">
										<span class="text-sm font-medium">{tile.name}</span>
										<span class="text-xs text-gray-600 dark:text-gray-400">
											{tile.description}
										</span>
									</span>
								</button>
							{/each}
						</div>
					</div>
				{/if}

				<div class="flex flex-col {layout.rows}">
					<button
						type="button"
						id="{formId}-row-knowledge"
						class="flex min-h-16 w-full items-center gap-3.5 border-b border-gray-100 py-3 text-left dark:border-gray-850 md:min-h-[60px] md:py-3.5"
						hidden={!shownOnMain($mobile, screen) || !partShown('knowledge', edit, revealed)}
						aria-expanded={$mobile ? undefined : knowledgeOpen}
						aria-controls="{formId}-knowledge-panel"
						on:click={() => {
							if ($mobile) void openScreen('knowledge');
							else knowledgeOpen = !knowledgeOpen;
						}}
					>
						<span
							class="flex size-9 shrink-0 items-center justify-center rounded-[10px] bg-gray-100 text-gray-700 dark:bg-gray-850 dark:text-gray-300"
						>
							<svg
								aria-hidden="true"
								class="size-[18px]"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="1.8"
								stroke-linecap="round"
								stroke-linejoin="round"
							>
								<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" />
								<path d="M4 21V5" />
								<path d="M9 8h6" />
							</svg>
						</span>
						<span class="flex min-w-0 flex-1 flex-col gap-0.5">
							<span class="text-[0.9375rem] font-medium md:text-sm">{$i18n.t('Knowledge')}</span>
							<span class="text-[0.8125rem] text-gray-600 dark:text-gray-400">
								{knowledgeSummary(knowledgeCount)}
							</span>
						</span>
						<svg
							aria-hidden="true"
							class="size-[18px] shrink-0 text-gray-500 transition {rowChevronClass(
								$mobile,
								knowledgeOpen
							)}"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<path d="M6 9l6 6 6-6" />
						</svg>
					</button>
					<div
						id="{formId}-knowledge-panel"
						class={layout.knowledgePanel}
						hidden={!partShown('knowledge', edit, revealed) ||
							!panelShown($mobile, screen, 'knowledge', knowledgeOpen)}
					>
						<Knowledge bind:selectedItems={data.files} phoneTouchTargets>
							<span slot="label" class="sr-only">{$i18n.t('Knowledge')}</span>
						</Knowledge>
					</div>

					{#if projectHosted}
						<button
							type="button"
							id="{formId}-row-project"
							class="flex min-h-16 w-full items-center gap-3.5 border-b border-gray-100 py-3 text-left dark:border-gray-850 md:min-h-[60px] md:py-3.5"
							hidden={!shownOnMain($mobile, screen) ||
								!partShown('project', edit, revealed) ||
								!projectRowShown(projectAvailable, projectLoadError)}
							aria-expanded={$mobile ? undefined : projectOpen}
							aria-controls="folder-project-panel-{projectKey}"
							on:click={() => {
								if ($mobile) void openScreen('project');
								else void toggleProjectRow();
							}}
						>
							<span
								class="flex size-9 shrink-0 items-center justify-center rounded-[10px] bg-gray-100 text-gray-700 dark:bg-gray-850 dark:text-gray-300"
							>
								<svg
									aria-hidden="true"
									class="size-[18px]"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="1.8"
									stroke-linecap="round"
									stroke-linejoin="round"
								>
									<path
										d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
									/>
									<path d="M9 12l2 2-2 2" />
									<path d="M13 16h3" />
								</svg>
							</span>
							<span class="flex min-w-0 flex-1 flex-col gap-0.5">
								<span
									id="folder-project-heading-{projectKey}"
									class="text-[0.9375rem] font-medium md:text-sm"
								>
									{$i18n.t('Project folder')}
								</span>
								<span class="truncate text-[0.8125rem] text-gray-600 dark:text-gray-400">
									{projectRowSummary(projectSummary, projectLoadError)}
								</span>
							</span>
							<svg
								aria-hidden="true"
								class="size-[18px] shrink-0 text-gray-500 transition {rowChevronClass(
									$mobile,
									projectOpen
								)}"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								stroke-width="2"
								stroke-linecap="round"
								stroke-linejoin="round"
							>
								<path d="M6 9l6 6 6-6" />
							</svg>
						</button>
						<!-- Kept mounted while closed, so the summary and an unsaved choice stay. -->
						<div
							id="folder-project-panel-{projectKey}"
							class={layout.projectPanel}
							hidden={!partShown('project', edit, revealed) ||
								!panelShown($mobile, screen, 'project', projectOpen)}
						>
							<ProjectFolderSection
								bind:this={projectSection}
								{folderId}
								{parentId}
								labelledBy="folder-project-heading-{projectKey}"
								bind:summary={projectSummary}
								bind:error={projectError}
								bind:loadError={projectLoadError}
								bind:picking={projectPicking}
								bind:available={projectAvailable}
							/>
						</div>
					{/if}
				</div>
			</div>

			{#if !$mobile}
				<div class="flex justify-end gap-2 px-6 pt-5 pb-6">
					<button
						type="button"
						class="h-11 rounded-full px-[18px] text-sm font-medium text-gray-700 transition hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-850"
						on:click={() => {
							show = false;
						}}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						class="flex h-11 items-center gap-2 rounded-full bg-gray-900 px-6 text-sm font-medium text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
						type="submit"
						disabled={loading}
					>
						{#if edit}
							{$i18n.t('Save')}
						{:else}
							{$i18n.t('Create')}
						{/if}
						{#if loading}
							<Spinner className="size-4" />
						{/if}
					</button>
				</div>
			{/if}
		</form>
	</div>
</Modal>
