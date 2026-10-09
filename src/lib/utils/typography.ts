// Canvas-based UI needs the same responsive font offset as app CSS.
export function getBuddyFontSize(baseSize: number): number {
	if (typeof window === 'undefined') {
		return baseSize;
	}

	const rootStyle = window.getComputedStyle(document.documentElement);
	const offsetValue = rootStyle.getPropertyValue('--buddy-font-size-offset');
	const offset = Number.parseFloat(offsetValue) || 0;
	return baseSize + offset;
}
