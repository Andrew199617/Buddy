export type MenuViewport = {
	left: number;
	top: number;
	width: number;
	height: number;
};

export type MenuAnchor = {
	left: number;
	right: number;
	top: number;
};

export type MenuSize = {
	width: number;
	height: number;
};

const viewportGutter = 8;
const anchorGap = 8;
const minimumUsefulHeight = 160;

export function getMobileComposerMenuMaxHeight(
	anchor: MenuAnchor,
	viewport: MenuViewport,
	focused: boolean
): number {
	const visibleHeight = Math.max(0, viewport.height - viewportGutter * 2);
	const spaceAboveAnchor = Math.max(0, anchor.top - viewport.top - anchorGap - viewportGutter);
	let availableHeight = Math.min(spaceAboveAnchor, visibleHeight);

	// Tight viewports use the visible area so the menu still has room to scroll.
	if (availableHeight < minimumUsefulHeight) {
		availableHeight = visibleHeight;
	}

	if (focused) {
		const compactHeight = Math.min(240, Math.max(96, viewport.height / 2));
		availableHeight = Math.min(availableHeight, compactHeight);
	}

	return availableHeight;
}

export function getMobileComposerMenuPosition(
	anchor: MenuAnchor,
	viewport: MenuViewport,
	size: MenuSize,
	align: 'start' | 'end'
): { top: number; left: number } {
	const preferredTop = anchor.top - size.height - anchorGap;
	let preferredLeft = anchor.left;
	if (align === 'end') {
		preferredLeft = anchor.right - size.width;
	}

	const minimumTop = viewport.top + viewportGutter;
	const minimumLeft = viewport.left + viewportGutter;
	const maximumTop = viewport.top + viewport.height - size.height - viewportGutter;
	const maximumLeft = viewport.left + viewport.width - size.width - viewportGutter;
	const top = Math.max(minimumTop, Math.min(preferredTop, maximumTop));
	const left = Math.max(minimumLeft, Math.min(preferredLeft, maximumLeft));

	return { top: top, left: left };
}
