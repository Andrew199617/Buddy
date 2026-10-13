<script lang="ts">
	// One choice from a short list. On wider screens the chosen option shows as a
	// card that opens the list right below it; phones show the list itself. The
	// list is a radio group. Nothing floats over the dialog, so its focus trap and
	// scrolling keep working.
	import { tick } from 'svelte';

	import {
		CHOICE_ICON_PATHS,
		focusableIndex,
		nextEnabled,
		type ChoiceOption
	} from './choice-picker';

	// Unique on the page; the label, card and options take their ids from it.
	export let id: string;
	export let label: string;
	export let options: ChoiceOption[];
	export let value: string | null;
	// Phones show the list with no card to open.
	export let stacked = false;
	// What the card says before anything is chosen.
	export let placeholder = '';
	export let onChange: (value: string) => void;

	let open = false;
	let container: HTMLElement;
	// Set while a pointer press is under way. The list then closes on the click, not as
	// focus moves at the press: the dialog is centered, so closing the list early moves
	// what is being clicked. Safari doesn't focus a clicked button either.
	let pressing = false;

	$: current = options.find((option) => option.value === value) ?? null;
	$: listShown = stacked || open;
	$: tabStop = focusableIndex(options, value);

	function optionId(index: number): string {
		return `${id}-option-${index}`;
	}

	function focusOption(index: number) {
		document.getElementById(optionId(index))?.focus();
	}

	async function toggleList() {
		if (open) {
			open = false;
			return;
		}
		open = true;
		await tick();
		focusOption(tabStop);
	}

	async function choose(option: ChoiceOption, close: boolean) {
		if (option.disabled) return;
		onChange(option.value);
		if (!close || stacked) return;
		open = false;
		await tick();
		document.getElementById(`${id}-trigger`)?.focus();
	}

	function onOptionKeydown(event: KeyboardEvent, index: number) {
		let step: 1 | -1 | 0 = 0;
		if (event.key === 'ArrowDown' || event.key === 'ArrowRight') step = 1;
		if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') step = -1;
		if (step !== 0) {
			event.preventDefault();
			const next = nextEnabled(options, index, step);
			void choose(options[next], false);
			focusOption(next);
			return;
		}
		if (event.key === 'Enter' || event.key === ' ') {
			// Inside the folder dialog's form, Enter must not submit it.
			event.preventDefault();
			void choose(options[index], true);
		}
	}

	function onFocusOut(event: FocusEvent) {
		if (stacked || !open || pressing) return;
		const next = event.relatedTarget as Node | null;
		if (next && container.contains(next)) return;
		open = false;
	}

	function onPressStart() {
		pressing = true;
	}

	function onPressEnd() {
		pressing = false;
	}

	// A click anywhere else closes the list, once it has reached what was clicked.
	function closeOnClickOutside(event: MouseEvent) {
		if (stacked || !open) return;
		if (container?.contains(event.target as Node)) return;
		open = false;
	}

	async function closeOnEscape(event: KeyboardEvent) {
		if (stacked || !open || event.key !== 'Escape') return;
		if (!container?.contains(document.activeElement)) return;
		// Only the list closes, not the dialog around it.
		event.stopPropagation();
		event.preventDefault();
		open = false;
		await tick();
		document.getElementById(`${id}-trigger`)?.focus();
	}

	function tileClass(option: ChoiceOption): string {
		if (option.tone === 'amber') {
			return 'bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300';
		}
		return 'bg-gray-100 text-gray-700 dark:bg-gray-850 dark:text-gray-300';
	}

	function descriptionClass(option: ChoiceOption): string {
		if (option.tone === 'amber') return 'text-amber-800 dark:text-amber-300';
		return 'text-gray-600 dark:text-gray-400';
	}

	function listClass(stacked: boolean): string {
		if (stacked) return 'overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800';
		return 'rounded-xl border border-gray-200 bg-white p-1.5 shadow-lg dark:border-gray-800 dark:bg-gray-900';
	}

	function rowClass(option: ChoiceOption, value: string | null, stacked: boolean): string {
		const classes = [
			'flex min-h-[52px] w-full items-center gap-3 px-2.5 py-2 text-left transition'
		];
		if (stacked) classes.push('border-b border-gray-100 last:border-b-0 dark:border-gray-850');
		else classes.push('rounded-lg');
		if (option.value === value) classes.push('bg-gray-50 dark:bg-gray-850');
		else classes.push('hover:bg-gray-50 dark:hover:bg-gray-850');
		if (option.disabled) classes.push('cursor-not-allowed opacity-50');
		return classes.join(' ');
	}
</script>

<svelte:window
	on:keydown|capture={closeOnEscape}
	on:pointerdown|capture={onPressStart}
	on:pointerup|capture={onPressEnd}
	on:pointercancel|capture={onPressEnd}
	on:click|capture={closeOnClickOutside}
/>

<div bind:this={container} class="flex flex-col gap-1.5" on:focusout={onFocusOut}>
	<span id="{id}-label" class="text-xs font-medium text-gray-800 dark:text-gray-100">{label}</span>

	{#if !stacked}
		<button
			type="button"
			id="{id}-trigger"
			class="flex min-h-14 w-full items-center gap-3 rounded-xl border border-gray-200 bg-white px-3 py-2 text-left transition hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-900 dark:hover:bg-gray-850"
			aria-labelledby="{id}-label {id}-current"
			aria-expanded={open}
			aria-controls="{id}-list"
			on:click={toggleList}
		>
			{#if current}
				<span
					class="flex size-8 shrink-0 items-center justify-center rounded-lg {tileClass(current)}"
				>
					<svg
						aria-hidden="true"
						class="size-4"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						{#each CHOICE_ICON_PATHS[current.icon] as path}<path d={path} />{/each}
					</svg>
				</span>
				<span id="{id}-current" class="flex min-w-0 flex-1 flex-col">
					<span class="text-sm font-medium text-gray-900 dark:text-gray-100">{current.name}</span>
					<span class="text-xs [overflow-wrap:anywhere] {descriptionClass(current)}"
						>{current.description}</span
					>
				</span>
			{:else}
				<span id="{id}-current" class="min-w-0 flex-1 text-sm text-gray-600 dark:text-gray-400">
					{placeholder}
				</span>
			{/if}
			<svg
				aria-hidden="true"
				class="size-4 shrink-0 text-gray-500"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<path d="M7 10l5-5 5 5M7 14l5 5 5-5" />
			</svg>
		</button>
	{/if}

	{#if listShown}
		<div
			id="{id}-list"
			role="radiogroup"
			aria-labelledby="{id}-label"
			class="flex flex-col {listClass(stacked)}"
		>
			{#each options as option, index (option.value)}
				<button
					type="button"
					id={optionId(index)}
					role="radio"
					aria-checked={option.value === value}
					aria-disabled={option.disabled ? 'true' : undefined}
					tabindex={index === tabStop ? 0 : -1}
					class={rowClass(option, value, stacked)}
					on:click={() => choose(option, true)}
					on:keydown={(event) => onOptionKeydown(event, index)}
				>
					<span
						class="flex size-8 shrink-0 items-center justify-center rounded-lg {tileClass(option)}"
					>
						<svg
							aria-hidden="true"
							class="size-4"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							{#each CHOICE_ICON_PATHS[option.icon] as path}<path d={path} />{/each}
						</svg>
					</span>
					<span class="flex min-w-0 flex-1 flex-col">
						<span class="text-sm font-medium text-gray-900 dark:text-gray-100">{option.name}</span>
						<span class="text-xs [overflow-wrap:anywhere] {descriptionClass(option)}"
							>{option.description}</span
						>
					</span>
					{#if option.value === value}
						<svg
							aria-hidden="true"
							class="size-4 shrink-0 text-gray-900 dark:text-gray-100"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2.4"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<path d="M5 12l5 5 9-10" />
						</svg>
					{/if}
				</button>
			{/each}
		</div>
	{/if}
</div>
