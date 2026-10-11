type ComposerControlOptions = {
	isMobile: () => boolean;
	onInteractionChange: (active: boolean) => void;
};

type ComposerControl = {
	element: HTMLElement;
	allowScroll: boolean;
	activate: boolean;
};

type InputInteraction = {
	control: HTMLElement;
	input: HTMLElement;
};

type TouchInteraction = InputInteraction & {
	identifier: number;
	startX: number;
	startY: number;
	moved: boolean;
	allowScroll: boolean;
	activate: boolean;
};

type TouchClick = MouseEvent & {
	pointerType?: string;
	sourceCapabilities?: { firesTouchEvents: boolean };
};

const COMPOSER_REGION = '.buddy-composer';
const COMPOSER_MENUS = '[data-composer-menu], .model-selector-child-menu';
const COMPOSER_POPUP_TRIGGERS =
	'#input-menu-button, #integration-menu-button, #owui-reasoning-chip, button[id^="model-selector-"][id$="-button"], [aria-haspopup="true"], [aria-haspopup="menu"], [aria-haspopup="listbox"], [aria-haspopup="dialog"]';
const EDITABLE_CONTROLS = 'input, textarea, select, [contenteditable="true"]';
const ACTION_INPUTS =
	'input[type="checkbox"], input[type="radio"], input[type="button"], input[type="submit"], input[type="reset"]';
const ACTION_CONTROLS =
	'button, a[href], [role="button"], [role="option"], [role="switch"], [role="checkbox"], [role="radio"], [role="tab"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]';

/** Keep mobile composer controls in the input's touch gesture, including Safari's lost clicks. */
export function installComposerControlInteractions(
	form: HTMLElement,
	options: ComposerControlOptions
): () => void {
	const document = form.ownerDocument;
	const window = document.defaultView!;
	let touchInteraction: TouchInteraction | null = null;
	let pointerInteraction: InputInteraction | null = null;
	let mouseInteraction: InputInteraction | null = null;
	let touchGestureActive = false;
	let duplicateClick: { control: HTMLElement; expiresAt: number } | null = null;
	let interactionActive = false;
	let releaseTimer: number | undefined;

	function isEditableControl(element: Element): boolean {
		return element.matches(EDITABLE_CONTROLS) && !element.matches(ACTION_INPUTS);
	}

	function ownedRegion(target: Element): HTMLElement | null {
		const menu = target.closest<HTMLElement>(COMPOSER_MENUS);
		if (menu) return menu;
		const composer = target.closest<HTMLElement>(COMPOSER_REGION);
		if (composer && form.contains(composer)) return composer;
		return null;
	}

	function allowsScrolling(target: HTMLElement, region: HTMLElement): boolean {
		for (let element: HTMLElement | null = target; element; element = element.parentElement) {
			const style = window.getComputedStyle(element);
			const scrollsHorizontally =
				(style.overflowX === 'auto' || style.overflowX === 'scroll') &&
				element.scrollWidth > element.clientWidth;
			const scrollsVertically =
				(style.overflowY === 'auto' || style.overflowY === 'scroll') &&
				element.scrollHeight > element.clientHeight;
			if (scrollsHorizontally || scrollsVertically) return true;
			if (element === region) break;
		}
		return false;
	}

	function findControl(target: EventTarget | null): ComposerControl | null {
		if (!(target instanceof Element)) return null;
		const region = ownedRegion(target);
		if (!region) return null;

		// Inert Switch decorations belong to their clickable row, not to the nested toggle.
		const inertDecoration = target.closest<HTMLElement>('[inert]');
		if (inertDecoration) {
			if (inertDecoration.contains(region) || !inertDecoration.parentElement) return null;
			target = inertDecoration.parentElement;
		}
		const element = target as Element;
		const editable = element.closest(EDITABLE_CONTROLS);
		if (editable && isEditableControl(editable)) return null;
		const label = element.closest<HTMLLabelElement>('label');
		if (label?.control && isEditableControl(label.control)) return null;

		let control = element.closest<HTMLElement>(ACTION_CONTROLS + ', ' + ACTION_INPUTS);
		if (!control && label?.control && label.control.matches(ACTION_INPUTS)) {
			control = label;
		}
		if (control && !region.contains(control)) control = null;

		const isMenu = region.matches(COMPOSER_MENUS);
		let allowScroll = isMenu;
		if (!allowScroll) {
			const scrollTarget = element.closest<HTMLElement>('*');
			if (scrollTarget) allowScroll = allowsScrolling(scrollTarget, region);
		}
		if (!control) return { element: region, allowScroll: allowScroll, activate: false };
		const popupTrigger = control.closest(COMPOSER_POPUP_TRIGGERS);
		if (!isMenu && popupTrigger && region.contains(popupTrigger)) {
			// Safari can blur after touchstart; a popup tap must cancel that default action.
			allowScroll = false;
		}
		const disabled =
			control.matches(':disabled') || control.closest('[aria-disabled="true"], [inert]');
		return { element: control, allowScroll: allowScroll, activate: !disabled };
	}

	function focusedInput(): HTMLElement | null {
		const activeElement = document.activeElement;
		if (!(activeElement instanceof HTMLElement)) return null;
		const input = activeElement.closest<HTMLElement>(EDITABLE_CONTROLS);
		if (input && isEditableControl(input) && ownedRegion(input)) return input;
		return null;
	}

	function inputForControl(control: HTMLElement): HTMLElement | null {
		const input = focusedInput();
		if (input) return input;
		if (pointerInteraction?.control === control) return pointerInteraction.input;
		return null;
	}

	function setInteractionActive(active: boolean): void {
		if (releaseTimer !== undefined) {
			window.clearTimeout(releaseTimer);
			releaseTimer = undefined;
		}
		if (interactionActive === active) return;
		interactionActive = active;
		options.onInteractionChange(active);
	}

	function cancelInteraction(): void {
		touchGestureActive = false;
		touchInteraction = null;
		pointerInteraction = null;
		mouseInteraction = null;
		setInteractionActive(false);
	}

	function releaseAfterClick(): void {
		if (releaseTimer !== undefined) window.clearTimeout(releaseTimer);
		// Let Svelte and menu observers render the open state before releasing the collapse lock.
		releaseTimer = window.setTimeout(() => {
			releaseTimer = undefined;
			cancelInteraction();
		}, 0);
	}

	function restoreInput(input: HTMLElement): void {
		if (input.isConnected && document.activeElement !== input) {
			input.focus({ preventScroll: true });
		}
	}

	function handleFocusOut(event: FocusEvent): void {
		if (!touchGestureActive) return;
		const interaction = touchInteraction || pointerInteraction;
		if (!interaction || event.target !== interaction.input || !interaction.input.isConnected) {
			return;
		}
		const nextTarget = event.relatedTarget;
		if (nextTarget instanceof Element) {
			const editable = nextTarget.closest(EDITABLE_CONTROLS);
			if (editable && isEditableControl(editable)) {
				cancelInteraction();
				return;
			}
			if (nextTarget !== document.body && !findControl(nextTarget)) {
				cancelInteraction();
				return;
			}
		}
		// Recover during Safari's default blur, before keyboard resizing moves the touched option.
		restoreInput(interaction.input);
	}

	function handlePointerDown(event: PointerEvent): void {
		if (event.pointerType === 'mouse') duplicateClick = null;
		if (!options.isMobile() || event.pointerType !== 'touch' || !event.isPrimary) return;
		const control = findControl(event.target);
		const input = focusedInput();
		if (!control || !input) {
			duplicateClick = null;
			cancelInteraction();
			return;
		}
		// Capture before WebKit can blur the editor despite pointerdown cancellation.
		pointerInteraction = { control: control.element, input: input };
		touchGestureActive = true;
		setInteractionActive(true);
	}

	function handleTouchStart(event: TouchEvent): void {
		if (!options.isMobile() || event.touches.length !== 1) {
			duplicateClick = null;
			cancelInteraction();
			return;
		}
		const control = findControl(event.target);
		const input = control && inputForControl(control.element);
		if (!control || !input) {
			duplicateClick = null;
			cancelInteraction();
			return;
		}
		const touch = event.touches[0];
		touchInteraction = {
			control: control.element,
			input: input,
			identifier: touch.identifier,
			startX: touch.clientX,
			startY: touch.clientY,
			moved: false,
			allowScroll: control.allowScroll,
			activate: control.activate
		};
		duplicateClick = null;
		touchGestureActive = true;
		setInteractionActive(true);
		if (!control.allowScroll) {
			event.preventDefault();
		}
		// Safari can blur on pointerdown before touchstart, including inside scrolling menus.
		restoreInput(input);
	}

	function handleTouchMove(event: TouchEvent): void {
		if (!touchInteraction) return;
		const touch = Array.from(event.touches).find(
			(point) => point.identifier === touchInteraction?.identifier
		);
		if (!touch || event.touches.length !== 1) {
			cancelInteraction();
			return;
		}
		const distance = Math.hypot(
			touch.clientX - touchInteraction.startX,
			touch.clientY - touchInteraction.startY
		);
		if (distance > 10) touchInteraction.moved = true;
	}

	function handleTouchEnd(event: TouchEvent): void {
		touchGestureActive = false;
		const interaction = touchInteraction;
		if (!interaction) return;
		touchInteraction = null;
		const touch = Array.from(event.changedTouches).find(
			(point) => point.identifier === interaction.identifier
		);
		if (!touch || event.touches.length !== 0 || interaction.moved) {
			releaseAfterClick();
			return;
		}
		const distance = Math.hypot(
			touch.clientX - interaction.startX,
			touch.clientY - interaction.startY
		);
		const hit = findControl(document.elementFromPoint(touch.clientX, touch.clientY));
		if (
			distance > 10 ||
			hit?.element !== interaction.control ||
			!interaction.control.isConnected ||
			!interaction.input.isConnected
		) {
			releaseAfterClick();
			return;
		}
		event.preventDefault();
		pointerInteraction = null;
		mouseInteraction = null;
		restoreInput(interaction.input);
		if (interaction.activate) {
			duplicateClick = { control: interaction.control, expiresAt: Date.now() + 750 };
			// Canceling touchstart can suppress Safari's native click, so activate exactly once here.
			interaction.control.click();
		}
		releaseAfterClick();
	}

	function handleMouseDown(event: MouseEvent): void {
		if (!options.isMobile() || event.button !== 0) return;
		const control = findControl(event.target);
		if (control && isDuplicateTouchClick(event, control.element)) {
			event.preventDefault();
			return;
		}
		const input = control && inputForControl(control.element);
		if (!control || !input) return;
		event.preventDefault();
		mouseInteraction = { control: control.element, input: input };
		setInteractionActive(true);
		restoreInput(input);
	}

	function handleMouseUp(event: MouseEvent): void {
		if (mouseInteraction && event.button === 0) {
			// A drag can finish outside its button without generating a matching click.
			releaseAfterClick();
		}
	}

	function isDuplicateTouchClick(event: TouchClick, control: HTMLElement): boolean {
		if (!duplicateClick || event.detail === 0 || duplicateClick.control !== control) return false;
		if (Date.now() > duplicateClick.expiresAt) {
			duplicateClick = null;
			return false;
		}
		if (event.pointerType) return event.pointerType === 'touch';
		if (event.sourceCapabilities) return event.sourceCapabilities.firesTouchEvents;
		// Older Safari click events omit both properties; match only the captured control briefly.
		return true;
	}

	function handleClick(event: MouseEvent): void {
		const control = findControl(event.target);
		if (!control) return;
		if (isDuplicateTouchClick(event, control.element)) {
			duplicateClick = null;
			event.preventDefault();
			event.stopImmediatePropagation();
			releaseAfterClick();
			return;
		}
		const interaction = mouseInteraction || pointerInteraction;
		if (interaction?.control === control.element) {
			restoreInput(interaction.input);
			releaseAfterClick();
		}
	}

	function handleCancel(): void {
		duplicateClick = null;
		cancelInteraction();
	}

	document.addEventListener('focusout', handleFocusOut, true);
	document.addEventListener('pointerdown', handlePointerDown, true);
	document.addEventListener('pointercancel', handleCancel, true);
	document.addEventListener('touchstart', handleTouchStart, { capture: true, passive: false });
	document.addEventListener('touchmove', handleTouchMove, { capture: true, passive: true });
	document.addEventListener('touchend', handleTouchEnd, { capture: true, passive: false });
	document.addEventListener('touchcancel', handleCancel, true);
	document.addEventListener('mousedown', handleMouseDown, true);
	document.addEventListener('mouseup', handleMouseUp, true);
	document.addEventListener('click', handleClick, true);

	function dispose(): void {
		document.removeEventListener('focusout', handleFocusOut, true);
		document.removeEventListener('pointerdown', handlePointerDown, true);
		document.removeEventListener('pointercancel', handleCancel, true);
		document.removeEventListener('touchstart', handleTouchStart, true);
		document.removeEventListener('touchmove', handleTouchMove, true);
		document.removeEventListener('touchend', handleTouchEnd, true);
		document.removeEventListener('touchcancel', handleCancel, true);
		document.removeEventListener('mousedown', handleMouseDown, true);
		document.removeEventListener('mouseup', handleMouseUp, true);
		document.removeEventListener('click', handleClick, true);
		duplicateClick = null;
		cancelInteraction();
	}

	return dispose;
}
