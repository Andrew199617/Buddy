<script lang="ts">
	// Create Folder / Edit Folder: a name and background image, instructions,
	// and two quiet rows that open in place: Knowledge and the Project folder.
	import { getContext, tick } from 'svelte';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '../icons/XMark.svelte';

	import { toast } from 'svelte-sonner';
	import { user, config } from '$lib/stores';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import Knowledge from '$lib/components/workspace/Models/Knowledge.svelte';
	import ProjectFolderSection from '$lib/components/projectFolders/ProjectFolderSection.svelte';
	import { getFolderById } from '$lib/apis/folders';
	const i18n = getContext('i18n');

	export let show = false;
	export let onSubmit: Function = (e) => {};

	export let folderId = null;
	export let parentId = null;
	export let edit = false;

	let folder = null;
	let name = '';
	let meta = {
		background_image_url: null
	};
	let data = {
		system_prompt: '',
		files: []
	};

	let loading = false;
	let projectSection: ProjectFolderSection;
	let knowledgeOpen = false;
	let projectOpen = false;
	let projectSummary = '';
	let projectError = '';

	$: canEditInstructions =
		$user?.role === 'admin' || ($user?.permissions?.chat?.system_prompt ?? true);
	$: knowledgeCount = (data?.files ?? []).length;
	// A save the project folder refused shows its reason where the choice is made.
	$: if (projectError) projectOpen = true;

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
		const projectSaved = await projectSection?.save();
		if (projectSaved === false) {
			loading = false;
			return;
		}

		await onSubmit({
			name,
			meta,
			data,
			parent_id: edit ? undefined : parentId
		});
		show = false;
		loading = false;
	};

	const init = async () => {
		knowledgeOpen = false;
		projectOpen = false;
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

<Modal size="md" bind:show>
	<div class="flex flex-col text-gray-900 dark:text-gray-100">
		<div class="flex items-center justify-between gap-2 pt-5 pr-5 pb-1 pl-7">
			<h2 class="text-[1.0625rem] font-semibold">
				{#if edit}
					{$i18n.t('Edit folder')}
				{:else}
					{$i18n.t('New folder')}
				{/if}
			</h2>
			<button
				type="button"
				aria-label={$i18n.t('Close')}
				class="flex size-11 items-center justify-center rounded-xl text-gray-600 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-850 md:size-10"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-[18px]'} />
			</button>
		</div>

		<form
			class="flex flex-col"
			on:submit|preventDefault={() => {
				submitHandler();
			}}
		>
			<div class="flex flex-col gap-5 px-7 pt-3">
				<input
					id="folder-background-image-input"
					type="file"
					hidden
					accept="image/*"
					on:change={readBackgroundImage}
				/>

				<div class="flex flex-col gap-1.5">
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
								class="w-full border-b-[1.5px] border-gray-200 bg-transparent pt-0.5 pb-2 text-xl font-medium outline-hidden placeholder:text-gray-400 focus:border-gray-900 dark:border-gray-800 dark:placeholder:text-gray-600 dark:focus:border-gray-100 md:text-[1.375rem]"
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

				{#if canEditInstructions}
					<div class="flex flex-col gap-2">
						<span id="folder-instructions-label" class="text-sm font-medium">
							{$i18n.t('Instructions')}
						</span>
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

				<div class="flex flex-col border-t border-gray-100 dark:border-gray-850">
					<button
						type="button"
						class="flex min-h-[60px] w-full items-center gap-3.5 border-b border-gray-100 py-3.5 text-left dark:border-gray-850"
						aria-expanded={knowledgeOpen}
						aria-controls="folder-knowledge-panel"
						on:click={() => (knowledgeOpen = !knowledgeOpen)}
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
							<span class="text-sm font-medium">{$i18n.t('Knowledge')}</span>
							<span class="text-[0.8125rem] text-gray-600 dark:text-gray-400">
								{knowledgeSummary(knowledgeCount)}
							</span>
						</span>
						<svg
							aria-hidden="true"
							class="size-[18px] shrink-0 text-gray-500 transition {knowledgeOpen
								? 'rotate-180'
								: ''}"
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
						id="folder-knowledge-panel"
						class="border-b border-gray-100 pt-1 pb-4 pl-[50px] dark:border-gray-850"
						hidden={!knowledgeOpen}
					>
						<Knowledge bind:selectedItems={data.files}>
							<span slot="label" class="sr-only">{$i18n.t('Knowledge')}</span>
						</Knowledge>
					</div>

					{#if edit && folderId}
						<button
							type="button"
							class="flex min-h-[60px] w-full items-center gap-3.5 border-b border-gray-100 py-3.5 text-left dark:border-gray-850"
							aria-expanded={projectOpen}
							aria-controls="folder-project-panel-{folderId}"
							on:click={() => (projectOpen = !projectOpen)}
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
								<span id="folder-project-heading-{folderId}" class="text-sm font-medium">
									{$i18n.t('Project folder')}
								</span>
								<span class="truncate text-[0.8125rem] text-gray-600 dark:text-gray-400">
									{projectSummary}
								</span>
							</span>
							<svg
								aria-hidden="true"
								class="size-[18px] shrink-0 text-gray-500 transition {projectOpen
									? 'rotate-180'
									: ''}"
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
							id="folder-project-panel-{folderId}"
							class="pt-1 pb-4 pl-[50px]"
							hidden={!projectOpen}
						>
							<ProjectFolderSection
								bind:this={projectSection}
								{folderId}
								labelledBy="folder-project-heading-{folderId}"
								bind:summary={projectSummary}
								bind:error={projectError}
							/>
						</div>
					{/if}
				</div>
			</div>

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
		</form>
	</div>
</Modal>
