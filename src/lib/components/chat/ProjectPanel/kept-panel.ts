// Keeps a chat's Project tab running while the side panel shows something else
// or is closed. The panel is mounted once into its own element, and that element
// is moved into whichever open panel shows the tab. Its session on the computer,
// an open file and a running command last until the chat or its folder changes.

import type { Component } from 'svelte';
import { createClassComponent } from 'svelte/legacy';

import type { FolderProject } from '$lib/apis/subscriptions';
import type { ProjectTarget } from './project-session';

export type ProjectPanelProps = {
	project: FolderProject | null;
	chatTarget: ProjectTarget | null;
	onChanged: () => void;
};

export class KeptProjectPanel {
	readonly element: HTMLDivElement;
	private readonly instance: ReturnType<typeof createClassComponent>;

	constructor(component: Component<ProjectPanelProps>, props: ProjectPanelProps, i18n: unknown) {
		this.element = document.createElement('div');
		this.element.className = 'h-full';
		this.instance = createClassComponent({
			component,
			target: this.element,
			props,
			context: new Map<string, unknown>([['i18n', i18n]])
		});
	}

	update(props: Partial<ProjectPanelProps>) {
		this.instance.$set(props);
	}

	destroy() {
		this.instance.$destroy();
		this.element.remove();
	}
}

/** Svelte action: shows the kept panel inside this spot while the spot exists. */
export function showKeptPanel(spot: HTMLElement, panel: KeptProjectPanel | null) {
	let shown = panel;
	if (shown) spot.appendChild(shown.element);

	function takeOut() {
		// Out of the page it keeps running; nothing is destroyed here.
		if (shown && shown.element.parentElement === spot) shown.element.remove();
	}

	return {
		update(next: KeptProjectPanel | null) {
			if (next === shown) return;
			takeOut();
			shown = next;
			if (shown) spot.appendChild(shown.element);
		},
		destroy: takeOut
	};
}
