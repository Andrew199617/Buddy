// The options a ChoicePicker shows, and how arrow keys move between them.

export type ChoiceIcon =
	| 'inherit'
	| 'automatic'
	| 'computer'
	| 'server'
	| 'detached'
	| 'plan'
	| 'chat'
	| 'read'
	| 'full';

export type ChoiceOption = {
	value: string;
	name: string;
	description: string;
	icon: ChoiceIcon;
	// Amber marks the one choice that never asks first.
	tone?: 'amber';
	disabled?: boolean;
};

// Stroke paths on a 24×24 grid.
export const CHOICE_ICON_PATHS: Record<ChoiceIcon, string[]> = {
	inherit: ['M9 14 4 9l5-5', 'M4 9h10.5a5.5 5.5 0 0 1 0 11H11'],
	automatic: [
		'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z',
		'M19 17l.8 2.2L22 20l-2.2.8L19 23l-.8-2.2L16 20l2.2-.8z'
	],
	computer: ['M3 5h18v11H3z', 'M8 20h8', 'M12 16v4'],
	server: ['M4 4h16v6H4z', 'M4 14h16v6H4z', 'M8 7h.01', 'M8 17h.01'],
	detached: ['M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z'],
	plan: ['M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z'],
	chat: ['M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z'],
	read: ['M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z', 'M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z'],
	full: ['M13 2 4 14h7l-1 8 9-12h-7z']
};

/** The option an arrow key moves to from `fromIndex`, skipping disabled ones and wrapping around. */
export function nextEnabled(options: ChoiceOption[], fromIndex: number, step: 1 | -1): number {
	const count = options.length;
	for (let moved = 1; moved <= count; moved++) {
		const index = (((fromIndex + step * moved) % count) + count) % count;
		if (!options[index].disabled) return index;
	}
	return fromIndex;
}

/** The option that takes Tab focus: the chosen one, else the first that can be chosen. */
export function focusableIndex(options: ChoiceOption[], value: string | null): number {
	const chosen = options.findIndex((option) => option.value === value && !option.disabled);
	if (chosen >= 0) return chosen;
	return options.findIndex((option) => !option.disabled);
}
