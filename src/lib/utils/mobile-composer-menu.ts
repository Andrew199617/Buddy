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
	height: number;
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
	if (focused) {
		const focusedHeight = viewport.height * 0.6;
		return Math.min(visibleHeight, focusedHeight);
	}

	const spaceAboveAnchor = Math.max(0, anchor.top - viewport.top - anchorGap - viewportGutter);
	let availableHeight = Math.min(spaceAboveAnchor, visibleHeight);

	// Tight idle viewports use the visible area so the menu still has room to scroll.
	if (availableHeight < minimumUsefulHeight) {
		availableHeight = visibleHeight;
	}

	return availableHeight;
}

export function getMobileComposerMenuPosition(
	anchor: MenuAnchor,
	viewport: MenuViewport,
	size: MenuSize,
	align: 'start' | 'end',
	focused = false
): { top: number; left: number } {
	let preferredTop = anchor.top - size.height - anchorGap;
	if (focused) {
		const anchorCenter = anchor.top + anchor.height / 2;
		preferredTop = anchorCenter - size.height / 2;
	}

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

/** The visible area around a phone composer, in client pixels. */
export type ComposerSpace = {
	// The top of the visible area: the chat header's top edge, or the visual viewport's top.
	visibleTop: number;
	// The bottom of the visible area: the visual viewport's bottom edge, above the keyboard.
	visibleBottom: number;
	composerTop: number;
	composerBottom: number;
};

export type ComposerMenuRoom = {
	// True when the menu covers the composer because too little room is left above it.
	coversComposer: boolean;
	// The tallest the menu may be.
	maxHeight: number;
};

// Model and Thinking open this far above the composer too.
const composerGap = 6;

/**
 * The room for a menu opened from a phone composer: above the composer, up to the
 * top of the chat's header, which the menu may cover. When that room is shorter
 * than minimumHeight, as in a short landscape window with the keyboard up, the
 * menu covers the composer instead, from the top of the visible area down to the
 * composer's bottom edge, so its rows stay reachable.
 */
export function getComposerMenuRoom(space: ComposerSpace, minimumHeight: number): ComposerMenuRoom {
	const highestTop = space.visibleTop + viewportGutter;
	const roomAbove = Math.max(0, space.composerTop - composerGap - highestTop);
	if (roomAbove >= minimumHeight) {
		return { coversComposer: false, maxHeight: roomAbove };
	}

	const lowestBottom = Math.min(space.visibleBottom - viewportGutter, space.composerBottom);
	return { coversComposer: true, maxHeight: Math.max(0, lowestBottom - highestTop) };
}

/**
 * The top edge of a menu in the room getComposerMenuRoom gave it, once the menu
 * shows at menuHeight: just above the composer, or at the top of the visible area
 * when it covers the composer.
 */
export function getComposerMenuTop(
	space: ComposerSpace,
	room: ComposerMenuRoom,
	menuHeight: number
): number {
	const highestTop = space.visibleTop + viewportGutter;
	if (room.coversComposer) return highestTop;
	return Math.max(highestTop, space.composerTop - composerGap - menuHeight);
}
