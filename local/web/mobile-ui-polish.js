/* Mobile proportions and an adaptive, native-state model picker. */
(function () {
	'use strict';

	if (typeof window === 'undefined' || window.__owuiMobileUiPolish) return;
	window.__owuiMobileUiPolish = true;

	const root = document.documentElement;
	const touchScreen = window.matchMedia('(pointer: coarse)');
	const viewport = window.visualViewport;
	const css = `
html.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) {
	/* Use a readable phone baseline while retaining larger accessibility scales. */
	font-size: calc(16px * max(1, var(--app-text-scale, 1)));
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar[role="navigation"] {
	font-size: calc(.9375rem + var(--buddy-font-size-offset, 2px));
	line-height: 1.4;
	top: var(--owui-chat-viewport-top, 0px);
	height: var(--owui-chat-viewport-height, 100dvh);
	max-height: var(--owui-chat-viewport-height, 100dvh);
	min-height: 0;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar > div {
	height: 100%;
	max-height: 100%;
	min-height: 0;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar-webui-name {
	font-size: calc(1rem + var(--buddy-font-size-offset, 2px));
	font-weight: 500;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar-chat-item {
	display: flex;
	align-items: center;
	min-height: 44px;
	padding-block: 10px;
	font-size: calc(.9375rem + var(--buddy-font-size-offset, 2px));
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar-chat-item div[dir="auto"] {
	height: auto;
	line-height: 1.4;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar-chat-item input {
	font-size: calc(1rem + var(--buddy-font-size-offset, 2px));
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar-chat-item-menu button {
	width: 40px;
	height: 40px;
	justify-content: center;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar .sidebar button,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar .sidebar > a:first-child {
	min-width: 44px;
	min-height: 44px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar .sidebar button svg,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar-chat-item-menu button svg {
	width: 20px;
	height: 20px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar [id^="sidebar-"][id$="-button"] {
	min-height: 44px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar [id^="sidebar-"][id$="-button"] div {
	font-size: calc(.9375rem + var(--buddy-font-size-offset, 2px));
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar [id^="sidebar-"][id$="-button"] svg {
	width: 20px;
	height: 20px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar .sidebar button [class*="text-[0.8125rem]"] {
	font-size: calc(.9375rem + var(--buddy-font-size-offset, 2px));
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #sidebar .sidebar button img {
	width: 28px;
	height: 28px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #chat-container nav.drag-region button {
	min-width: 40px;
	min-height: 44px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #chat-container nav.drag-region button svg {
	width: 20px;
	height: 20px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #messages-container .markdown-prose {
	font-size: calc(1rem + var(--buddy-font-size-offset, 2px)) !important;
	line-height: 1.6;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #chat-input {
	line-height: 1.5;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #messages-container .buttons button {
	min-width: 40px;
	min-height: 40px;
	display: inline-flex;
	align-items: center;
	justify-content: center;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #messages-container .buttons button svg {
	width: 20px;
	height: 20px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #input-menu-button,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #integration-menu-button,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #voice-input-button,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #send-message-button,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #chat-variables-button,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #owui-reasoning-chip {
	width: 40px;
	height: 40px;
	min-width: 40px;
	padding: 8px;
	display: inline-flex;
	align-items: center;
	justify-content: center;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #input-menu-button svg,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #integration-menu-button svg,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #voice-input-button svg,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #chat-variables-button svg,
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #owui-reasoning-chip svg {
	width: 20px;
	height: 20px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #send-message-button svg {
	width: 22px;
	height: 22px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #owui-reasoning-chip .owui-rl-label {
	display: none;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) .owui-mobile-toolbar {
	flex-wrap: wrap;
	align-items: center;
	column-gap: 4px;
	row-gap: 4px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) .owui-toolbar-leading {
	flex: 0 0 auto;
	min-width: var(--owui-toolbar-leading-width, 48px);
	margin-inline-start: 0;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) .owui-toolbar-trailing {
	flex-shrink: 0;
	max-width: 100%;
	margin-inline-start: auto;
	margin-inline-end: 0;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) .owui-toolbar-trailing > div:first-child {
	max-width: clamp(64px, calc(100vw - 280px), 160px);
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) .owui-composer-primary {
	width: 40px;
	height: 40px;
	min-width: 40px;
	display: inline-flex;
	align-items: center;
	justify-content: center;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) .owui-composer-primary svg {
	width: 22px;
	height: 22px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #message-input-container button[id^="model-selector-"] {
	min-height: 40px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #message-input-container button[id^="model-selector-"] > div {
	font-size: calc(.875rem + var(--buddy-font-size-offset, 2px));
	padding-inline: 4px;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #message-input-container button[id^="model-selector-"] svg {
	width: 14px;
	height: 14px;
}
.owui-model-picker-header {
	display: none;
}
html.owui-model-screen-open, html.owui-model-screen-open body {
	overflow: hidden;
}
.owui-model-picker-phone {
	left: 0 !important;
	top: var(--owui-picker-top) !important;
	width: 100% !important;
	height: var(--owui-picker-height) !important;
	z-index: 10000 !important;
}
.owui-model-picker-phone > .owui-model-picker-panel {
	width: 100% !important;
	max-width: 100% !important;
	height: 100% !important;
	max-height: 100% !important;
	border: 0;
	border-radius: 0;
	box-shadow: none;
	padding: env(safe-area-inset-top, 0px) 12px env(safe-area-inset-bottom, 0px);
	transform: none !important;
}
.owui-model-picker-phone .owui-model-picker-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 8px;
	flex-shrink: 0;
	min-height: 56px;
	margin-bottom: 8px;
	border-bottom: 1px solid #e5e7eb;
}
.dark .owui-model-picker-phone .owui-model-picker-header {
	border-color: #374151;
}
.owui-model-picker-header h2 {
	font-size: calc(1.0625rem + var(--buddy-font-size-offset, 2px));
	font-weight: 600;
	line-height: 1.4;
	margin: 0;
}
.owui-model-picker-header button {
	min-width: 44px;
	min-height: 44px;
	border-radius: 12px;
	font-size: calc(.9375rem + var(--buddy-font-size-offset, 2px));
	font-weight: 500;
	display: inline-flex;
	align-items: center;
	justify-content: center;
}
.owui-model-picker-header svg {
	width: 22px;
	height: 22px;
}
.owui-model-picker-phone .owui-model-picker-search {
	min-height: 48px;
	margin: 0 0 12px;
	padding: 0 12px;
	border-radius: 14px;
	background: #f3f4f6;
	gap: 8px;
}
.dark .owui-model-picker-phone .owui-model-picker-search {
	background: #1f2937;
}
.owui-model-picker-phone #model-search-input {
	font-size: calc(1rem + var(--buddy-font-size-offset, 2px));
	min-width: 0;
}
.owui-model-picker-phone .owui-model-picker-search > svg {
	width: 20px;
	height: 20px;
}
.owui-model-picker-phone .owui-model-picker-search button {
	min-height: 36px;
	min-width: 36px;
	font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px));
}
.owui-model-picker-phone .owui-model-picker-search button svg {
	width: 18px;
	height: 18px;
}
.owui-model-picker-phone [role="listbox"] {
	/* JS virtualization uses 32px local rows; zoom gives 44px touch rows. */
	--owui-model-row-zoom: 1.375;
	zoom: var(--owui-model-row-zoom);
	max-height: none !important;
	overscroll-behavior-y: contain;
}
.owui-model-picker-phone [role="listbox"] [role="option"] {
	height: 32px !important;
	min-height: 32px;
	font-size: calc(calc(1rem / var(--owui-model-row-zoom)) + var(--buddy-font-size-offset, 2px) / var(--owui-model-row-zoom));
	line-height: 1.4;
	border-radius: 10px;
}
.owui-model-picker-phone [role="option"] img {
	width: calc(24px / var(--owui-model-row-zoom));
	height: calc(24px / var(--owui-model-row-zoom));
}
.owui-model-picker-phone [role="option"] svg {
	width: calc(18px / var(--owui-model-row-zoom));
	height: calc(18px / var(--owui-model-row-zoom));
}
.owui-model-picker-phone [role="option"] [class*="group-hover/item:opacity-100"] {
	opacity: 1;
}
.owui-model-picker-phone [role="option"] button {
	width: calc(40px / var(--owui-model-row-zoom));
	min-width: calc(40px / var(--owui-model-row-zoom));
	height: calc(40px / var(--owui-model-row-zoom));
	align-items: center;
	justify-content: center;
}
.owui-model-picker-child {
	z-index: 10001 !important;
}
.owui-model-picker-child .app-dropdown-menu > button {
	min-height: 40px;
	font-size: calc(.875rem + var(--buddy-font-size-offset, 2px));
}
.owui-model-picker-phone [role="option"] [class*="text-[0.6875rem]"] {
	font-size: calc(calc(.75rem / var(--owui-model-row-zoom)) + var(--buddy-font-size-offset, 2px) / var(--owui-model-row-zoom));
}
.owui-model-picker-phone .owui-model-picker-footer {
	min-height: 44px;
	padding-block: 4px;
}
.owui-model-picker-phone .owui-model-picker-footer button {
	min-height: 40px;
	font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px));
}
.owui-model-picker-segment {
	left: var(--owui-picker-left) !important;
	top: var(--owui-picker-top) !important;
	transform: translate(-50%, -50%);
}
.owui-model-picker-segment > .owui-model-picker-panel {
	max-width: var(--owui-picker-width) !important;
	max-height: var(--owui-picker-height) !important;
}
@media (max-width: 374px) {
	.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #message-input-container > div:last-child {
		margin-inline: 0;
	}
	.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)) #message-input-container button[id^="model-selector-"] {
		max-width: 88px;
	}
}
`;

	let picker = null;
	let pendingFrame = null;
	let background = null;
	let backgroundWasInert = false;

	function restoreBackground() {
		if (background) background.inert = backgroundWasInert;
		background = null;
		root.classList.remove('owui-model-screen-open');
	}

	function restorePickerFocus(previousPicker) {
		if (!previousPicker.phone || previousPicker.openedUrl !== window.location.href) return;
		if (!previousPicker.trigger?.isConnected) return;
		const active = document.activeElement;
		if (active === document.body || previousPicker.portal.contains(active)) {
			previousPicker.trigger.focus({ preventScroll: true });
		}
	}

	function closePicker() {
		if (!picker) return;
		picker.search.blur();
		picker.portal.dispatchEvent(new KeyboardEvent('keydown', {
			key: 'Escape', code: 'Escape', bubbles: true, cancelable: true
		}));
		restoreBackground();
		picker.trigger?.focus({ preventScroll: true });
	}

	function getOpenChild() {
		if (!picker) return null;
		for (const entry of picker.children.values()) {
			if (entry.portal.isConnected && entry.expandedElement.getAttribute('aria-expanded') === 'true') {
				return entry;
			}
		}
		return null;
	}

	function trackChildTrigger(event) {
		if (!picker?.phone || !(event.target instanceof Element)) return;
		if (event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
		const button = event.target.closest('button');
		if (!button || !picker.portal.contains(button)) return;
		const expandedElement = button.hasAttribute('aria-expanded')
			? button
			: button.closest('[aria-haspopup][aria-expanded]');
		if (!expandedElement) return;
		const inSearch = button.closest('.owui-model-picker-search');
		const option = button.closest('[role="option"]');
		if (!inSearch && (!option || option === button)) return;
		picker.childTrigger = { trigger: button, expandedElement };
	}

	function closeChildOnEscape(event) {
		if (!picker?.phone || event.key !== 'Escape' || event.target === picker.portal) return;
		const child = getOpenChild();
		if (!child) return;
		event.preventDefault();
		event.stopPropagation();
		// The original trigger owns the native dropdown's open state.
		child.trigger.click();
		child.trigger.focus({ preventScroll: true });
	}

	function trapPickerFocus(event) {
		if (!picker?.phone || event.key !== 'Tab') return;
		const child = getOpenChild();
		const container = child?.portal || picker.portal;
		const focusable = Array.from(container.querySelectorAll(
			'button:not([disabled]), input:not([disabled]), [tabindex="0"]'
		)).filter((element) => element.getClientRects().length > 0);
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (child && !container.contains(document.activeElement)) {
			event.preventDefault();
			if (event.shiftKey) last?.focus();
			else first?.focus();
		} else if (event.shiftKey && document.activeElement === first) {
			event.preventDefault();
			last?.focus();
		} else if (!event.shiftKey && document.activeElement === last) {
			event.preventDefault();
			first?.focus();
		}
	}

	function adoptChildPortals() {
		if (!picker?.phone) return;
		for (const [portal, entry] of picker.children) {
			if (portal.isConnected) continue;
			picker.children.delete(portal);
			if (document.activeElement === document.body && entry.trigger.isConnected) {
				entry.trigger.focus({ preventScroll: true });
			}
		}
		const owner = picker.childTrigger;
		if (!owner?.trigger.isConnected || owner.expandedElement.getAttribute('aria-expanded') !== 'true') return;
		for (const portal of Array.from(document.body.children)) {
			if (picker.originalPortals.has(portal) || portal.style.position !== 'fixed') continue;
			const filterMenu = portal.querySelector('.model-selector-child-menu');
			if (!filterMenu && portal.getAttribute('role') !== 'menu') continue;
			const entry = { portal, trigger: owner.trigger, expandedElement: owner.expandedElement };
			picker.children.set(portal, entry);
			portal.classList.add('owui-model-picker-child');
			// Native portal cleanup removes its actual parent, so adoption preserves its lifecycle.
			picker.portal.appendChild(portal);
		}
	}

	function releaseChildPortals() {
		if (!picker) return;
		for (const portal of picker.children.keys()) {
			portal.classList.remove('owui-model-picker-child');
			if (portal.isConnected && portal.parentElement === picker.portal) document.body.appendChild(portal);
		}
		picker.children.clear();
	}

	function createPicker(search) {
		const panel = search.parentElement.parentElement;
		const portal = panel.parentElement;
		if (portal.parentElement !== document.body || portal.style.position !== 'fixed') return null;

		const header = document.createElement('div');
		header.className = 'owui-model-picker-header';
		const back = document.createElement('button');
		back.type = 'button';
		back.setAttribute('aria-label', 'Close model picker');
		back.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>';
		back.addEventListener('click', closePicker);
		const title = document.createElement('h2');
		title.id = 'owui-model-picker-title';
		title.textContent = 'Models';
		const done = document.createElement('button');
		done.type = 'button';
		done.textContent = 'Done';
		done.addEventListener('click', closePicker);
		header.append(back, title, done);
		panel.prepend(header);
		panel.classList.add('owui-model-picker-panel');
		search.parentElement.classList.add('owui-model-picker-search');
		const footer = Array.from(panel.children).find((element) =>
			element !== search.parentElement && element.matches('.shrink-0') && element.querySelector('button')
		);
		footer?.classList.add('owui-model-picker-footer');
		portal.addEventListener('keydown', trapPickerFocus);
		const trigger = document.querySelector('button[id^="model-selector-"][aria-expanded="true"]');
		return {
			search, panel, portal, header, back, trigger,
			phone: false,
			openedUrl: window.location.href,
			childTrigger: null,
			children: new Map(),
			originalPortals: new Set(document.body.children)
		};
	}

	function polishComposer() {
		const container = document.getElementById('message-input-container');
		const toolbar = container?.lastElementChild;
		if (!toolbar?.classList.contains('justify-between')) return;
		const leading = toolbar.firstElementChild;
		const trailing = toolbar.lastElementChild;
		if (!leading || !trailing) return;
		toolbar.classList.add('owui-mobile-toolbar');
		leading.classList.add('owui-toolbar-leading');
		trailing.classList.add('owui-toolbar-trailing');
		const leadingControls = leading.querySelectorAll(
			'#input-menu-button, #integration-menu-button, #model-valves-button'
		);
		leading.style.setProperty('--owui-toolbar-leading-width', `${leadingControls.length * 40 + 8}px`);
		const primaryAction = Array.from(trailing.querySelectorAll('button')).at(-1);
		primaryAction?.classList.add('owui-composer-primary');
	}

	function getSegments() {
		const segments = window.viewport?.segments || viewport?.segments;
		if (!segments || segments.length < 2) return [];
		return Array.from(segments);
	}

	function setPickerPresentation(mobile) {
		if (!picker) return;
		if (picker.trigger?.getAttribute('aria-expanded') === 'false') {
			restoreBackground();
			return;
		}
		const segments = getSegments();
		const phone = mobile && window.innerWidth < 640 && segments.length === 0;
		const enteringPhone = phone && !picker.phone;
		picker.phone = phone;
		picker.portal.classList.toggle('owui-model-picker-phone', phone);
		picker.portal.classList.toggle('owui-model-picker-segment', mobile && segments.length > 1);
		const top = viewport?.offsetTop || 0;
		const height = viewport?.height || window.innerHeight;
		picker.portal.style.setProperty('--owui-picker-top', `${top}px`);
		picker.portal.style.setProperty('--owui-picker-height', `${height}px`);

		if (phone) {
			picker.portal.setAttribute('role', 'dialog');
			picker.portal.setAttribute('aria-modal', 'true');
			picker.portal.setAttribute('aria-labelledby', 'owui-model-picker-title');
			root.classList.add('owui-model-screen-open');
			if (!background) {
				background = document.querySelector('.app');
				backgroundWasInert = background?.inert || false;
				if (background) background.inert = true;
			}
			if (enteringPhone) picker.back.focus({ preventScroll: true });
			adoptChildPortals();
			return;
		}

		restoreBackground();
		releaseChildPortals();
		picker.portal.removeAttribute('role');
		picker.portal.removeAttribute('aria-modal');
		picker.portal.removeAttribute('aria-labelledby');
		if (mobile && segments.length > 1) {
			const trigger = picker.trigger?.getBoundingClientRect();
			const segment = segments.find((item) => trigger &&
				trigger.left + trigger.width / 2 >= item.left &&
				trigger.left + trigger.width / 2 <= item.right &&
				trigger.top + trigger.height / 2 >= item.top &&
				trigger.top + trigger.height / 2 <= item.bottom
			) || segments[0];
			const visibleTop = Math.max(segment.top, top);
			const visibleBottom = Math.min(segment.bottom, top + height);
			picker.portal.style.setProperty('--owui-picker-left', `${segment.left + segment.width / 2}px`);
			picker.portal.style.setProperty('--owui-picker-top', `${(visibleTop + visibleBottom) / 2}px`);
			picker.portal.style.setProperty('--owui-picker-width', `${Math.max(0, segment.width - 32)}px`);
			picker.portal.style.setProperty('--owui-picker-height', `${Math.max(0, visibleBottom - visibleTop - 32)}px`);
		}
	}

	function update() {
		pendingFrame = null;
		const mobile = (window.innerWidth < 768 || touchScreen.matches) && (!viewport || viewport.scale <= 1.05);
		const buddyLayout = document.querySelector('.buddy-chat, .buddy-shell');
		// Keep the adaptive model picker, while Buddy controls its own shell and composer.
		root.classList.toggle('owui-mobile-ui', mobile && !buddyLayout);
		if (!buddyLayout) polishComposer();
		const search = document.getElementById('model-search-input');
		if (picker && (picker.search !== search || !picker.portal.isConnected)) {
			const previousPicker = picker;
			restoreBackground();
			releaseChildPortals();
			picker = null;
			restorePickerFocus(previousPicker);
		}
		if (!picker && search) picker = createPicker(search);
		setPickerPresentation(mobile);
	}

	function scheduleUpdate() {
		if (pendingFrame !== null) return;
		pendingFrame = window.requestAnimationFrame(update);
	}

	function start() {
		const style = document.createElement('style');
		style.id = 'owui-mobile-ui-polish-style';
		style.textContent = css;
		document.head.appendChild(style);
		window.addEventListener('resize', scheduleUpdate);
		window.addEventListener('pageshow', scheduleUpdate);
		document.addEventListener('pointerdown', trackChildTrigger, true);
		document.addEventListener('keydown', trackChildTrigger, true);
		document.addEventListener('keydown', closeChildOnEscape, true);
		viewport?.addEventListener('resize', scheduleUpdate);
		viewport?.addEventListener('scroll', scheduleUpdate);
		const observer = new MutationObserver(scheduleUpdate);
		observer.observe(document.body, { childList: true, subtree: true });
		update();
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', start, { once: true });
	} else {
		start();
	}
})();
