<script lang="ts">
	// The composer's Ultra button. With Ultra on, the chat's plan model orchestrates
	// workers for each message instead of doing the work itself. The menu turns it on
	// and picks the workers' model and thinking level; the composer saves the choice
	// with the chat and sends it with the next message. On phones and short windows
	// each list opens in place with a Back header, so the menu stays short above the
	// keyboard.
	import { getContext, tick } from 'svelte';

	import type { SubscriptionAccess } from '$lib/apis/subscriptions';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DropdownMenu from '$lib/components/common/DropdownMenu.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import { mobile } from '$lib/stores';

	import {
		ultraButtonLabel,
		workerEffortOptions,
		workerLimitLine,
		workerModelOptions,
		workersAccessLine,
		workersAccessNote,
		workersModel,
		type UltraAvailability,
		type UltraChange,
		type UltraModel,
		type UltraOption,
		type UltraState
	} from './ultra';

	const i18n: any = getContext('i18n');

	// The chat's choice as the composer shows it, after fitUltra.
	export let state: UltraState;
	// Whether this chat can use Ultra, and why not.
	export let availability: UltraAvailability;
	// The chat's one selected model, and the model list the workers' models come from.
	export let chatModel: UltraModel | null = null;
	export let models: UltraModel[] = [];
	// The access the chat's next reply runs with. Its workers get the same.
	export let access: SubscriptionAccess = 'chat';
	// An admin's cap on workers at once; null for no limit.
	export let maxWorkers: number | null = null;
	// While a reply is generating, changes apply to the next message.
	export let generating = false;
	// Called with each edit, for the composer to merge into the chat's saved choice.
	export let onChange: (change: UltraChange) => void = () => {};
	export let preserveComposerFocus = false;
	export let onClose: () => void = () => {};

	type MenuView = 'main' | 'model' | 'effort';

	// Windows shorter than this show each list on its own view, as phones do.
	const SHORT_WINDOW_HEIGHT = 640;
	// Three rows. With less room above the composer, as in a short landscape window
	// with the keyboard up, the phone menu covers the composer instead.
	const SMALLEST_ROOM = 132;

	let show = false;
	let view: MenuView = 'main';
	let compact = false;
	let menuElement: HTMLElement | null = null;

	$: active = state.on && availability.available;
	$: modelOptions = workerModelOptions($i18n, chatModel, models);
	$: effortOptions = workerEffortOptions($i18n, workersModel(state, chatModel, models));
	$: modelChoice = chosenLabel(modelOptions, state.worker_model);
	$: effortChoice = chosenLabel(effortOptions, state.worker_effort);
	$: triggerLabel = ultraButtonLabel($i18n, state, availability, chatModel, models);
	$: accessLine = workersAccessLine($i18n, access);
	$: accessNote = workersAccessNote($i18n, chatModel);
	$: limitLine = workerLimitLine($i18n, maxWorkers);
	// The lists show together on wider screens, and one at a time on phones.
	$: showModelList = availability.available && (!compact || view === 'model');
	$: showEffortList = availability.available && (!compact || view === 'effort');
	$: if (!availability.available) view = 'main';

	function chosenLabel(options: UltraOption[], value: string | null): string {
		const chosen = options.find((option) => option.value === value);
		if (chosen) return chosen.label;
		return options[0]?.label ?? '';
	}

	function listsOneAtATime(): boolean {
		if ($mobile || preserveComposerFocus) return true;
		const height = window.visualViewport?.height ?? window.innerHeight;
		return height < SHORT_WINDOW_HEIGHT;
	}

	function openChanged(open: boolean) {
		if (open) {
			view = 'main';
			compact = listsOneAtATime();
			return;
		}
		onClose();
	}

	/**
	 * Keyboard users keep their place: the chosen row of a list takes focus, and Back
	 * returns it to the row that opened the list. On phones the draft keeps focus, so
	 * the keyboard stays open.
	 */
	async function showView(next: MenuView) {
		const previous = view;
		const focusInMenu = menuElement?.contains(document.activeElement) ?? false;
		view = next;
		await tick();
		if (!focusInMenu || !menuElement) return;
		let selector = "[aria-checked='true']";
		if (next === 'main') selector = `[data-opens='${previous}']`;
		menuElement.querySelector<HTMLElement>(selector)?.focus();
	}

	function toggleUltra() {
		if (!availability.available) return;
		onChange({ on: !active });
	}

	function chooseWorkerModel(value: string | null) {
		onChange({ worker_model: value });
		if (compact) void showView('main');
	}

	function chooseWorkerEffort(value: string | null) {
		onChange({ worker_effort: value });
		if (compact) void showView('main');
	}

	// On, the chip is tinted. Its text keeps at least 4.5:1 against the tint in both themes.
	let triggerClass = '';
	$: if (active) {
		triggerClass =
			'border-sky-200/40 bg-sky-50 text-sky-700 hover:bg-sky-100 dark:border-sky-500/20 dark:bg-sky-400/10 dark:text-sky-300 dark:hover:bg-sky-600/10';
	} else {
		triggerClass =
			'border-transparent bg-transparent text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800';
	}

	const rowClass =
		'flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left text-[0.8125rem] hover:bg-gray-50/60 focus-visible:bg-gray-50/60 dark:hover:bg-gray-800/60 dark:focus-visible:bg-gray-800/60';
	const sectionClass =
		'px-2 pb-1 pt-1.5 text-[0.625rem] uppercase tracking-wider text-gray-500 dark:text-gray-400';
</script>

{#snippet choiceList(
	title: string,
	options: UltraOption[],
	chosen: string | null,
	choose: (value: string | null) => void
)}
	{#if compact}
		<div class="sticky top-0 z-10 bg-white dark:bg-gray-850">
			<button type="button" class="{rowClass} font-medium" on:click={() => showView('main')}>
				<ChevronLeft className="size-3.5 shrink-0" strokeWidth="2" />
				<span class="sr-only">{$i18n.t('Back')}</span>
				<span class="min-w-0 flex-1 truncate">{title}</span>
			</button>
		</div>
	{:else}
		<div class={sectionClass}>{title}</div>
	{/if}
	<div class="flex flex-col" role="group" aria-label={title}>
		{#each options as option (option.value ?? '')}
			<button
				type="button"
				role="menuitemradio"
				aria-checked={option.value === chosen}
				class={rowClass}
				on:click={() => choose(option.value)}
			>
				<span class="min-w-0 flex-1 truncate">{option.label}</span>
				{#if option.value === chosen}
					<Check className="size-3.5 shrink-0" strokeWidth="2" />
				{/if}
			</button>
		{/each}
	</div>
{/snippet}

<!-- A compact row that opens a list in place. The choice gets a line of its own under
	the row's name, so a long model name shows whole. -->
{#snippet listOpener(opens: MenuView, title: string, choice: string)}
	<button type="button" class={rowClass} data-opens={opens} on:click={() => showView(opens)}>
		<span class="flex min-w-0 flex-1 flex-col">
			<span>{title}</span>
			<span class="truncate text-xs leading-snug text-gray-500 dark:text-gray-400">
				{choice}
			</span>
		</span>
		<ChevronRight className="size-3.5 shrink-0" strokeWidth="2" />
	</button>
{/snippet}

<Dropdown
	bind:show
	align="start"
	side="top"
	{preserveComposerFocus}
	composerMenu="ultra"
	visualViewportAware={preserveComposerFocus}
	minRoomAboveComposer={SMALLEST_ROOM}
	onOpenChange={openChanged}
>
	<Tooltip content={triggerLabel} placement="top" touch={false}>
		<button
			type="button"
			id="ultra-menu-button"
			class="buddy-ultra-chip flex h-[1.875rem] shrink-0 items-center gap-1 rounded-full border px-2 text-[0.8125rem] outline-hidden transition-colors focus:outline-hidden focus-visible:ring-2 focus-visible:ring-sky-500/60 {triggerClass}"
			aria-label={triggerLabel}
			aria-haspopup="menu"
			aria-expanded={show}
			data-on={active}
		>
			<Sparkles className="size-4 shrink-0" strokeWidth="1.75" />
			<span class="buddy-ultra-label">{$i18n.t('Ultra')}</span>
		</button>
	</Tooltip>

	<!-- The menu scrolls itself, within the height the dropdown leaves it. The dropdown
		can't: Svelte resets its overflow when the opening animation ends. -->
	<div slot="content" class="max-h-[inherit]">
		<DropdownMenu
			className="buddy-ultra-menu w-72 max-w-[calc(100vw-2rem)] max-h-[inherit] overflow-y-auto overscroll-contain"
		>
			<div bind:this={menuElement} class="flex flex-col">
				{#if view === 'main'}
					<button
						type="button"
						class="{rowClass} disabled:cursor-not-allowed disabled:opacity-60"
						aria-pressed={active}
						aria-describedby={availability.available ? undefined : 'ultra-menu-unavailable'}
						disabled={!availability.available}
						on:click={toggleUltra}
					>
						<Sparkles className="size-4 shrink-0" strokeWidth="1.75" />
						<span class="flex min-w-0 flex-1 flex-col">
							<span class="font-medium">{$i18n.t('Ultra')}</span>
							<span class="text-xs leading-snug text-gray-500 dark:text-gray-400">
								{$i18n.t('Hands the work to workers')}
							</span>
						</span>
						<div class="shrink-0" inert>
							<Switch state={active} />
						</div>
					</button>

					{#if !availability.available}
						<p
							id="ultra-menu-unavailable"
							class="px-2 pb-2 pt-1 text-xs text-gray-600 dark:text-gray-300"
						>
							{availability.reason}
						</p>
					{:else if compact}
						{@render listOpener('model', $i18n.t('Worker model'), modelChoice)}
						{@render listOpener('effort', $i18n.t('Worker thinking'), effortChoice)}
					{:else}
						<hr class="mx-1 my-0.5 border-gray-100 dark:border-gray-800" />
					{/if}
				{/if}

				{#if showModelList}
					{@render choiceList(
						$i18n.t('Worker model'),
						modelOptions,
						state.worker_model,
						chooseWorkerModel
					)}
				{/if}
				{#if showEffortList}
					{@render choiceList(
						$i18n.t('Worker thinking'),
						effortOptions,
						state.worker_effort,
						chooseWorkerEffort
					)}
				{/if}

				{#if view === 'main' && availability.available}
					<hr class="mx-1 my-0.5 border-gray-100 dark:border-gray-800" />
					<div
						class="flex flex-col gap-1 px-2 pb-1.5 pt-1 text-xs leading-snug text-gray-500 dark:text-gray-400"
					>
						<p>{accessLine}</p>
						{#if accessNote}
							<p>{accessNote}</p>
						{/if}
						<p>{limitLine}</p>
						{#if generating}
							<p class="font-medium text-gray-700 dark:text-gray-200">
								{$i18n.t('Changes apply to your next message')}
							</p>
						{/if}
					</div>
				{/if}
			</div>
		</DropdownMenu>
	</div>
</Dropdown>
