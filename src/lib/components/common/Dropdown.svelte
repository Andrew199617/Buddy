<script lang="ts">
	import { flyAndScale } from '$lib/utils/transitions';
	import { tick } from 'svelte';
	import {
		getMobileComposerMenuMaxHeight,
		getMobileComposerMenuPosition
	} from '$lib/utils/mobile-composer-menu';
	import { mobile } from '$lib/stores';

	/** Whether the dropdown is open */
	export let show = false;

	/** Side to open on: 'bottom' | 'top' */
	export let side = 'bottom';

	/** Alignment: 'start' | 'end' */
	export let align = 'start';

	/** Close when clicking outside */
	export let closeOnOutsideClick = true;

	/** Called when open/close state changes */
	export let onOpenChange: (state: boolean) => void = () => {};

	/** CSS classes for the dropdown content container */
	export let contentClass = '';

	/** Max height for the dropdown content */
	export let maxHeight = 'min(32rem, calc(100dvh - 2rem))';

	/** Side offset in px */
	export let sideOffset = 4;

	/** Position against the visual viewport, e.g. when the mobile keyboard is open */
	export let visualViewportAware = false;
	export let preserveComposerFocus = false;
	export let composerMenu = 'dropdown';
	/** Plus and Tools use an adaptive, frosted menu on mobile. */
	export let mobileActionMenu = false;
	// The popup's role, for popups that are not menus, such as a dialog with a search field.
	export let contentRole = 'menu';
	export let contentLabel = '';

	let triggerEl: HTMLElement | null = null;
	let contentEl: HTMLElement | null = null;
	let previouslyFocused: HTMLElement | null = null;
	let shouldFocusContent = false;
	let positionFrame: number | undefined;
	let settleTimers: number[] = [];
	let resolvedMaxHeight = maxHeight;
	let lastContentHeight = 0;
	let menuOpenGeneration = 0;

	/** Svelte action: moves the node to document.body and keeps it positioned as it resizes */
	function portal(node: HTMLElement) {
		document.body.appendChild(node);

		// Content can grow after the dropdown is positioned - a submenu is opened, or an
		// async list finishes loading - which would otherwise leave it overflowing the
		// viewport. Compare scrollHeight (the natural content height) so that clamping
		// max-height here cannot feed back into another reposition.
		let lastRenderedHeight = 0;
		let lastRenderedWidth = 0;
		const resizeObserver = new ResizeObserver(() => {
			const sameNaturalHeight = node.scrollHeight === lastContentHeight;
			if (mobileActionMenu) {
				if (
					sameNaturalHeight &&
					node.offsetHeight === lastRenderedHeight &&
					node.offsetWidth === lastRenderedWidth
				) {
					return;
				}
				lastRenderedHeight = node.offsetHeight;
				lastRenderedWidth = node.offsetWidth;
			} else if (sameNaturalHeight) {
				return;
			}
			lastContentHeight = node.scrollHeight;
			schedulePositionUpdate();
		});
		resizeObserver.observe(node);

		const viewport = window.visualViewport;
		const watchViewport =
			visualViewportAware || mobileActionMenu || Boolean(mobileComposerActionAnchor());
		if (watchViewport) {
			viewport?.addEventListener('resize', scheduleSettledPositionUpdates);
			viewport?.addEventListener('scroll', schedulePositionUpdate);
		}

		let anchorResizeObserver: ResizeObserver | null = null;
		if ((mobileActionMenu || mobileComposerActionAnchor()) && triggerEl) {
			anchorResizeObserver = new ResizeObserver(schedulePositionUpdate);
			anchorResizeObserver.observe(triggerEl);
			const composer = triggerEl.closest('.buddy-composer');
			if (composer) anchorResizeObserver.observe(composer);
		}

		return {
			destroy() {
				resizeObserver.disconnect();
				anchorResizeObserver?.disconnect();
				if (watchViewport) {
					viewport?.removeEventListener('resize', scheduleSettledPositionUpdates);
					viewport?.removeEventListener('scroll', schedulePositionUpdate);
				}
				lastContentHeight = 0;
				if (node.parentNode) {
					node.parentNode.removeChild(node);
				}
			}
		};
	}

	function getActiveComposerInput(): Element | null {
		const activeElement = document.activeElement;
		if (activeElement?.closest('#chat-input')) {
			return activeElement;
		}
		if (
			activeElement?.matches('input, textarea, select, [contenteditable="true"]') &&
			activeElement.closest('[data-composer-menu]')
		) {
			return activeElement;
		}
		return null;
	}

	function shouldKeepComposerFocus(): boolean {
		if (!preserveComposerFocus) {
			return false;
		}
		const touchLayout = $mobile || window.matchMedia('(pointer: coarse)').matches;
		if (!touchLayout) return false;
		const input = getActiveComposerInput();
		if (!input) return false;
		if (mobileActionMenu && input.closest('#chat-input')) {
			const composer = triggerEl?.closest('.buddy-composer');
			// Initial desktop-style autofocus can leave an idle mobile draft quietly focused.
			if (composer?.getAttribute('data-expanded') === 'false') return false;
		}
		return true;
	}

	function preserveInputFocus(event: PointerEvent): void {
		if (!event.isPrimary || event.button !== 0 || !shouldKeepComposerFocus()) {
			return;
		}
		const target = event.target;
		if (!(target instanceof Element)) {
			return;
		}
		if (!target.closest('input, textarea, select, [contenteditable="true"]')) {
			event.preventDefault();
		}
	}

	function handleContentPointerDown(event: PointerEvent): void {
		preserveInputFocus(event);
		event.stopPropagation();
	}

	function handleContentKeydown(event: KeyboardEvent): void {
		if (!mobileActionMenu || event.key !== 'Escape') return;
		// The menu owns Escape before the page's composer shortcut can refocus the draft.
		event.stopPropagation();
		closeDropdown();
	}

	/** Svelte action: captures the first child element as the trigger reference */
	function trigger(node: HTMLElement) {
		triggerEl = (node.firstElementChild as HTMLElement | null) || node;
		function handleClick(e: MouseEvent) {
			e.preventDefault();
			toggleOpen();
		}
		function handleKeydown(e: KeyboardEvent) {
			if (e.key === 'Enter' || e.key === ' ') {
				e.preventDefault();
				toggleOpen();
			}
		}
		node.addEventListener('pointerdown', preserveInputFocus);
		node.addEventListener('pointerdown', preserveMobileActionMenuFocus);
		node.addEventListener('click', handleClick);
		node.addEventListener('keydown', handleKeydown);
		return {
			destroy() {
				node.removeEventListener('pointerdown', preserveInputFocus);
				node.removeEventListener('pointerdown', preserveMobileActionMenuFocus);
				node.removeEventListener('click', handleClick);
				node.removeEventListener('keydown', handleKeydown);
			}
		};
	}

	/**
	 * Height the content wants, independent of any max-height already applied here.
	 * Measuring offsetHeight alone would feed the previous clamp back into the next
	 * calculation, so the dropdown could flip between clamped and unclamped on every
	 * repositioning pass.
	 */
	function naturalContentHeight() {
		if (!contentEl) return 0;
		return Math.max(contentEl.scrollHeight || 0, contentEl.offsetHeight || 0);
	}

	function visualViewportRect() {
		const viewport = window.visualViewport;
		return {
			left: viewport?.offsetLeft ?? 0,
			top: viewport?.offsetTop ?? 0,
			width: viewport?.width ?? window.innerWidth,
			height: viewport?.height ?? window.innerHeight
		};
	}

	function positionContentDefault() {
		if (!triggerEl || !contentEl) return;
		const rect = triggerEl.getBoundingClientRect();
		resolvedMaxHeight = maxHeight;

		contentEl.style.position = 'fixed';
		contentEl.style.zIndex = '9999';

		const contentHeight = naturalContentHeight();
		const spaceBelow = window.innerHeight - rect.bottom - sideOffset;
		const spaceAbove = rect.top - sideOffset;

		// Auto-flip: prefer the requested side, but flip if not enough space
		let openAbove = side === 'top';
		if (side === 'bottom' && spaceBelow < contentHeight && spaceAbove > spaceBelow) {
			openAbove = true;
		} else if (side === 'top' && spaceAbove < contentHeight && spaceBelow > spaceAbove) {
			openAbove = false;
		}

		if (openAbove) {
			contentEl.style.bottom = `${window.innerHeight - rect.top + sideOffset}px`;
			contentEl.style.top = 'auto';
		} else {
			contentEl.style.top = `${rect.bottom + sideOffset}px`;
			contentEl.style.bottom = 'auto';
		}

		if (align === 'end') {
			let right = window.innerWidth - rect.right;
			// Shift if overflowing left edge
			const contentWidth = contentEl.offsetWidth || 0;
			if (right + contentWidth > window.innerWidth) {
				right = window.innerWidth - contentWidth - 16;
			}
			contentEl.style.right = `${Math.max(16, right)}px`;
			contentEl.style.left = 'auto';
		} else {
			let left = rect.left;
			// Shift if overflowing right edge
			const contentWidth = contentEl.offsetWidth || 0;
			if (left + contentWidth + 16 > window.innerWidth) {
				left = window.innerWidth - contentWidth - 16;
			}
			contentEl.style.left = `${Math.max(16, left)}px`;
			contentEl.style.right = 'auto';
		}
	}

	function positionContentVisualViewport() {
		if (!triggerEl || !contentEl) return;
		let anchor = triggerEl;
		if (preserveComposerFocus) {
			const composer = triggerEl.closest<HTMLElement>('.buddy-composer');
			if (composer) {
				anchor = composer;
			}
		}
		const rect = anchor.getBoundingClientRect();
		const viewport = visualViewportRect();
		const viewportRight = viewport.left + viewport.width;
		const viewportBottom = viewport.top + viewport.height;
		const pad = 8;

		contentEl.style.position = 'fixed';
		contentEl.style.zIndex = '9999';

		const contentHeight = naturalContentHeight();
		const spaceBelow = viewportBottom - rect.bottom - sideOffset - pad;
		const spaceAbove = rect.top - viewport.top - sideOffset - pad;

		// Auto-flip: prefer the requested side, but flip if not enough space
		let openAbove = side === 'top';
		if (side === 'bottom' && spaceBelow < contentHeight && spaceAbove > spaceBelow) {
			openAbove = true;
		} else if (side === 'top' && spaceAbove < contentHeight && spaceBelow > spaceAbove) {
			openAbove = false;
		}

		const availableHeight = Math.max(0, openAbove ? spaceAbove : spaceBelow);
		const constrainedHeight = contentHeight
			? Math.min(contentHeight, availableHeight)
			: contentHeight;
		const preferredTop = openAbove
			? rect.top - constrainedHeight - sideOffset
			: rect.bottom + sideOffset;
		const contentWidth = contentEl.offsetWidth || 0;
		const preferredLeft = align === 'end' && contentWidth ? rect.right - contentWidth : rect.left;
		const maxLeft = contentWidth ? viewportRight - contentWidth - pad : preferredLeft;

		contentEl.style.top = `${Math.max(
			viewport.top + pad,
			Math.min(preferredTop, viewportBottom - pad - constrainedHeight)
		)}px`;
		contentEl.style.bottom = 'auto';
		contentEl.style.left = `${Math.max(viewport.left + pad, Math.min(preferredLeft, maxLeft))}px`;
		contentEl.style.right = 'auto';
		resolvedMaxHeight =
			contentHeight > availableHeight ? `min(${maxHeight}, ${availableHeight}px)` : maxHeight;
		contentEl.style.maxHeight = resolvedMaxHeight;
	}

	function positionContentActionMenu(): void {
		if (!triggerEl || !contentEl) return;
		const button = triggerEl.querySelector<HTMLElement>('button') || triggerEl;
		const buttonRect = button.getBoundingClientRect();
		const viewport = visualViewportRect();
		const viewportRight = viewport.left + viewport.width;
		const viewportBottom = viewport.top + viewport.height;
		const pad = 8;
		const gap = 8;
		const viewportHeight = Math.max(0, viewport.height - pad * 2);
		const spaceAboveButton = Math.max(0, buttonRect.top - viewport.top - gap - pad);
		const minimumUsefulHeight = 160;
		let availableHeight = Math.min(spaceAboveButton, viewportHeight);
		// Leave the trigger available for a second tap when there is usable space above it.
		// Very tight viewports use the full visible area so the menu never collapses to nothing.
		if (availableHeight < minimumUsefulHeight) {
			availableHeight = viewportHeight;
		}

		contentEl.style.position = 'fixed';
		contentEl.style.zIndex = '9999';
		resolvedMaxHeight = `min(${maxHeight}, ${availableHeight}px)`;
		contentEl.style.maxHeight = resolvedMaxHeight;

		// Measure after the cap is applied; scrollHeight can describe much taller hidden content.
		const renderedHeight = contentEl.offsetHeight;
		const renderedWidth = contentEl.offsetWidth;
		let preferredLeft = buttonRect.left;
		if (align === 'end') preferredLeft = buttonRect.right - renderedWidth;
		const preferredTop = buttonRect.top - renderedHeight - gap;
		const maxTop = viewportBottom - pad - renderedHeight;
		const maxLeft = viewportRight - pad - renderedWidth;

		contentEl.style.top = `${Math.max(viewport.top + pad, Math.min(preferredTop, maxTop))}px`;
		contentEl.style.bottom = 'auto';
		contentEl.style.left = `${Math.max(viewport.left + pad, Math.min(preferredLeft, maxLeft))}px`;
		contentEl.style.right = 'auto';
	}

	function mobileComposerActionAnchor(): HTMLElement | null {
		if (!$mobile) {
			return null;
		}
		if (!triggerEl) return null;
		let button = triggerEl.querySelector<HTMLElement>('button');
		if (!button && triggerEl.matches('button')) {
			button = triggerEl;
		}
		if (!button?.matches('#input-menu-button, #integration-menu-button, #available-tools-button'))
			return null;
		if (!button.closest('.buddy-chat .buddy-composer')) return null;
		return button;
	}

	function mobileComposerActionInputFocused(): boolean {
		const button = mobileComposerActionAnchor();
		if (!button) return false;
		const composer = button.closest('.buddy-composer');
		if (composer?.getAttribute('data-expanded') === 'false') return false;
		return Boolean(document.activeElement?.closest('#chat-input'));
	}

	function mobileComposerActionIsEditing(): boolean {
		if (mobileComposerActionInputFocused()) return true;
		const activeElement = document.activeElement;
		if (
			activeElement?.matches('input, textarea, [contenteditable="true"]') &&
			contentEl?.contains(activeElement)
		) {
			return true;
		}
		const viewport = window.visualViewport;
		// The keyboard can remain visible while Safari moves focus away from the editor.
		return Boolean(viewport && window.innerHeight - viewport.height > 140);
	}

	function preserveMobileActionMenuFocus(event: PointerEvent): void {
		if (!event.isPrimary || event.button !== 0 || !mobileComposerActionInputFocused()) return;
		event.preventDefault();
	}

	function mobileComposerMenuViewport(button: HTMLElement) {
		const viewport = visualViewportRect();
		const shell = button.closest('.buddy-shell');
		if (!shell) return viewport;
		const shellRect = shell.getBoundingClientRect();
		if (shellRect.width === 0 || shellRect.height === 0) return viewport;
		// Anchor and shell rectangles share client coordinates even when iOS pans the page.
		return {
			left: shellRect.left,
			top: shellRect.top,
			width: Math.min(viewport.width, shellRect.width),
			height: Math.min(viewport.height, shellRect.height)
		};
	}

	function mobileMenuFixedOrigin(): DOMRect {
		const probe = document.createElement('div');
		probe.setAttribute('aria-hidden', 'true');
		probe.style.cssText =
			'position: fixed; top: 0; left: 0; width: 0; height: 0; visibility: hidden; pointer-events: none;';
		document.body.appendChild(probe);
		const origin = probe.getBoundingClientRect();
		probe.remove();
		return origin;
	}

	function positionMobileComposerActionMenu(): boolean {
		const button = mobileComposerActionAnchor();
		if (!button || !contentEl) return false;
		const anchor = button.getBoundingClientRect();
		// A closing composer can hide its tools before the outgoing menu is removed.
		if (!button.isConnected || anchor.width === 0 || anchor.height === 0) return true;
		const viewport = mobileComposerMenuViewport(button);
		const editing = mobileComposerActionIsEditing();
		const availableHeight = getMobileComposerMenuMaxHeight(anchor, viewport, editing);
		resolvedMaxHeight = `min(${maxHeight}, ${availableHeight}px)`;
		contentEl.style.position = 'fixed';
		contentEl.style.zIndex = '9999';
		contentEl.style.maxHeight = resolvedMaxHeight;
		const menuSize = { width: contentEl.offsetWidth, height: contentEl.offsetHeight };
		let menuAlign: 'start' | 'end' = 'start';
		if (align === 'end') {
			menuAlign = 'end';
		}
		const position = getMobileComposerMenuPosition(anchor, viewport, menuSize, menuAlign, editing);
		const fixedOrigin = mobileMenuFixedOrigin();
		contentEl.style.top = `${position.top - fixedOrigin.top}px`;
		contentEl.style.left = `${position.left - fixedOrigin.left}px`;
		contentEl.style.bottom = 'auto';
		contentEl.style.right = 'auto';
		return true;
	}

	function cancelPositionUpdates(): void {
		if (positionFrame != null) {
			cancelAnimationFrame(positionFrame);
			positionFrame = undefined;
		}
		for (const timer of settleTimers) window.clearTimeout(timer);
		settleTimers = [];
	}

	function positionContent() {
		if (!show || !contentEl?.isConnected) return;
		if (positionMobileComposerActionMenu()) return;
		if (mobileActionMenu) {
			positionContentActionMenu();
		} else if (visualViewportAware) {
			positionContentVisualViewport();
		} else {
			positionContentDefault();
		}
	}

	function schedulePositionUpdate() {
		if (!show) return;
		if (positionFrame != null) cancelAnimationFrame(positionFrame);
		positionFrame = requestAnimationFrame(() => {
			positionFrame = undefined;
			positionContent();
		});
	}

	function scheduleSettledPositionUpdates() {
		if (!show) return;
		for (const timer of settleTimers) window.clearTimeout(timer);
		settleTimers = [];
		schedulePositionUpdate();
		for (const delay of [50, 150, 300]) {
			settleTimers.push(window.setTimeout(schedulePositionUpdate, delay));
		}
	}

	async function afterOpen() {
		const generation = ++menuOpenGeneration;
		await tick();
		if (!show || generation !== menuOpenGeneration || !contentEl?.isConnected) return;
		positionContent();

		// Re-check after transition renders real dimensions
		if (visualViewportAware || mobileActionMenu || mobileComposerActionAnchor()) {
			scheduleSettledPositionUpdates();
		} else {
			setTimeout(positionContent, 50);
		}

		if (shouldFocusContent) {
			shouldFocusContent = false;
			if (!shouldKeepComposerFocus()) {
				if (!mobileComposerActionInputFocused()) {
					contentEl?.focus();
				}
			}
		}
	}

	function openDropdown(focusContent = false) {
		if (show) return;
		if (focusContent) {
			previouslyFocused =
				document.activeElement instanceof HTMLElement ? document.activeElement : null;
			shouldFocusContent = !shouldKeepComposerFocus();
		}
		show = true;
		onOpenChange(true);
	}

	function closeDropdown(restoreFocus = !!contentEl?.contains(document.activeElement)) {
		if (!show) return;
		menuOpenGeneration += 1;
		cancelPositionUpdates();
		show = false;
		onOpenChange(false);
		shouldFocusContent = false;

		if (restoreFocus && previouslyFocused?.isConnected) {
			previouslyFocused.focus({ preventScroll: true });
		}
		previouslyFocused = null;
	}

	function toggleOpen() {
		if (show) {
			closeDropdown();
		} else {
			openDropdown(true);
		}
	}

	// React to external show changes (e.g. bind:show toggled by parent component)
	$: if (show) {
		afterOpen();
	} else {
		cancelPositionUpdates();
	}

	function handleWindowPointerDown(event: PointerEvent) {
		if (!show || !closeOnOutsideClick) return;
		if (!(event.target instanceof Node)) return;
		if (triggerEl?.contains(event.target)) return;
		if (contentEl?.contains(event.target)) return;
		closeDropdown(false);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && show) {
			closeDropdown();
		}
	}

	/** Close the dropdown programmatically */
	export function close() {
		closeDropdown();
	}

	import { onMount, onDestroy } from 'svelte';

	let onPointerDown: ((e: PointerEvent) => void) | undefined;
	onMount(() => {
		onPointerDown = (e) => handleWindowPointerDown(e);
		document.addEventListener('pointerdown', onPointerDown, true);
	});
	onDestroy(() => {
		cancelPositionUpdates();
		if (onPointerDown) {
			document.removeEventListener('pointerdown', onPointerDown, true);
		}
	});
</script>

<svelte:window
	on:keydown={handleKeydown}
	on:scroll|capture={positionContent}
	on:resize={positionContent}
/>

<span
	use:trigger
	style="display: contents; cursor: pointer;"
	role="button"
	aria-haspopup="true"
	aria-expanded={show}
>
	<slot />
</span>

{#if show}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<div
		use:portal
		bind:this={contentEl}
		data-composer-menu={preserveComposerFocus ? composerMenu : undefined}
		data-composer-anchor={mobileActionMenu ? 'trigger' : undefined}
		class={contentClass}
		class:buddy-action-menu={mobileActionMenu}
		role={contentRole}
		aria-label={contentLabel || undefined}
		tabindex="-1"
		style:max-height={resolvedMaxHeight}
		style:overflow-y="auto"
		transition:flyAndScale
		on:click={(e) => e.stopPropagation()}
		on:pointerdown={handleContentPointerDown}
		on:keydown={handleContentKeydown}
	>
		<slot name="content" />
	</div>
{/if}
