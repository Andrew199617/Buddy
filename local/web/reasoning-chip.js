/*
 * Inline thinking-level chip for the Open WebUI chat input.
 *
 * Appended to /static/loader.js at startup by local/owui_local_patches.py.
 * Adds a chip next to the model picker. The chosen level is sent as the
 * chat's native `reasoning_effort` param, the same one Chat Controls >
 * Advanced Params sets. "Default" sends nothing extra, so the model's or
 * chat's own setting applies.
 *
 * Fails safe: if Open WebUI's markup changes and the model picker can't be
 * found, the chip just doesn't appear. Chat requests are only rewritten when
 * a level is picked.
 */
(function () {
	'use strict';

	const STORAGE_KEY = 'owui-local:reasoning-effort';
	const LEVELS = [
		{ value: '', label: 'Default', hint: 'Model or chat setting' },
		{ value: 'none', label: 'Off', hint: 'No thinking' },
		{ value: 'low', label: 'Low' },
		{ value: 'medium', label: 'Medium' },
		{ value: 'high', label: 'High' },
		{ value: 'xhigh', label: 'Extra high' },
		{ value: 'max', label: 'Max' }
	];
	const COMPLETIONS_URL = /\/api\/chat\/completions(?:[?#]|$)/;

	/** Add the level to a chat completion request body (a JSON string). */
	function withReasoningEffort(body, level) {
		if (!level || typeof body !== 'string') return body;
		const payload = JSON.parse(body);
		payload.params = { ...(payload.params || {}), reasoning_effort: level };
		return JSON.stringify(payload);
	}

	if (typeof module !== 'undefined' && module.exports) {
		module.exports = { withReasoningEffort, LEVELS, COMPLETIONS_URL };
	}
	if (typeof window === 'undefined' || window.__owuiReasoningChip) return;
	window.__owuiReasoningChip = true;

	const store = {
		get() {
			try {
				const value = localStorage.getItem(STORAGE_KEY) || '';
				return LEVELS.some((l) => l.value === value) ? value : '';
			} catch {
				return '';
			}
		},
		set(value) {
			try {
				if (value) localStorage.setItem(STORAGE_KEY, value);
				else localStorage.removeItem(STORAGE_KEY);
			} catch {
				/* private mode: keep it for this page only */
			}
			sessionLevel = value;
		}
	};
	let sessionLevel = store.get();
	const currentLevel = () => sessionLevel;

	// --- requests -----------------------------------------------------------

	const originalFetch = window.fetch;
	window.fetch = function (input, init) {
		try {
			const url = typeof input === 'string' ? input : input && input.url;
			const level = currentLevel();
			if (
				level &&
				init &&
				COMPLETIONS_URL.test(url || '') &&
				String(init.method || 'GET').toUpperCase() === 'POST'
			) {
				init = { ...init, body: withReasoningEffort(init.body, level) };
			}
		} catch (e) {
			console.warn('[reasoning chip] left request unchanged:', e);
		}
		return originalFetch.call(this, input, init);
	};

	// --- UI -----------------------------------------------------------------

	const GAUGE =
		'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ' +
		'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
		'<path d="M3.5 16.5a8.5 8.5 0 1 1 17 0"/><path class="needle" d="M12 16.5l4.25-5"/>' +
		'<circle cx="12" cy="16.5" r="1.25"/></svg>';
	const NEEDLE_ANGLE = { '': -50, none: -90, low: -60, medium: -25, high: 15, xhigh: 50, max: 85 };

	const css = `
.owui-rl-chip{display:inline-flex;align-items:center;gap:.3rem;flex-shrink:0;align-self:center;
 border-radius:.5rem;padding:.25rem .45rem;font-size: calc(.8125rem + var(--buddy-font-size-offset, 0px));line-height:1.25rem;color:#4b5563;
 background:transparent;border:0;cursor:pointer;white-space:nowrap;transition:background-color .1s,color .1s}
.owui-rl-chip:hover{background:rgba(249,250,251,.6);color:#374151}
.owui-rl-chip svg{width:1rem;height:1rem}
.owui-rl-chip .needle{transform-origin:12px 16.5px;transition:transform .15s}
.owui-rl-chip[data-set="true"]{color:#0284c7}
.owui-rl-chip:focus-visible{outline:2px solid #0ea5e9;outline-offset:1px}
.dark .owui-rl-chip{color:#d1d5db}
.dark .owui-rl-chip:hover{background:rgba(31,41,55,.5);color:#e5e7eb}
.dark .owui-rl-chip[data-set="true"]{color:#7dd3fc}
@media (max-width:640px){.owui-rl-chip .owui-rl-label{display:none}}
.owui-rl-menu{position:fixed;z-index:9999;min-width:11rem;padding:.3rem;border-radius:.75rem;
 background:#fff;color:#111827;border:1px solid rgba(229,231,235,.8);overflow-y:auto;overscroll-behavior:contain;
 box-shadow:0 10px 30px -10px rgba(0,0,0,.25);font-size: calc(.8125rem + var(--buddy-font-size-offset, 0px))}
.dark .owui-rl-menu{background:#171717;color:#f3f4f6;border-color:rgba(64,64,64,.6)}
.owui-rl-menu .owui-rl-title{padding:.3rem .55rem .35rem;font-size: calc(.72rem + var(--buddy-font-size-offset, 0px));color:#6b7280}
.owui-rl-menu button{display:flex;width:100%;align-items:center;justify-content:space-between;gap:.75rem;
 padding:.38rem .55rem;border:0;border-radius:.5rem;background:transparent;color:inherit;text-align:left;cursor:pointer}
.owui-rl-menu button:hover,.owui-rl-menu button:focus-visible{background:rgba(243,244,246,1);outline:none}
.dark .owui-rl-menu button:hover,.dark .owui-rl-menu button:focus-visible{background:rgba(38,38,38,1)}
.owui-rl-menu .owui-rl-hint{color:#9ca3af;font-size: calc(.72rem + var(--buddy-font-size-offset, 0px))}
.owui-rl-menu .owui-rl-check{width:.9rem;color:#0284c7}
.dark .owui-rl-menu .owui-rl-check{color:#7dd3fc}
@media (max-width:640px){.owui-rl-menu[data-composer-menu] button{min-height:44px}}
.buddy-composer-settings .owui-rl-chip .owui-rl-label{display:inline}
.owui-rl-menu.buddy-thinking-picker {
 min-width:0;
 box-sizing:border-box;
 padding:6px;
 border-radius:16px;
 border-color:var(--buddy-picker-edge,rgba(39,99,75,.14));
 background:var(--buddy-picker-surface,#ffffff);
 color:var(--buddy-picker-text,#31483b);
}
.dark .owui-rl-menu.buddy-thinking-picker {
 border-color:var(--buddy-picker-edge,rgba(231,245,235,.12));
 background:var(--buddy-picker-surface,#292e2b);
 color:var(--buddy-picker-text,#e4eee7);
}
.buddy-thinking-picker .owui-rl-header {
 position:sticky;
 top:0;
 z-index:1;
 display:flex;
 align-items:center;
 justify-content:space-between;
 min-height:44px;
 padding:0 4px 4px 8px;
 background:inherit;
}
.buddy-thinking-picker .owui-rl-title {
 padding:0;
 color:inherit;
 font-size:calc(.875rem + var(--buddy-font-size-offset,0px));
 font-weight:600;
}
.buddy-thinking-picker button.owui-rl-close {
 width:40px;
 min-height:40px;
 justify-content:center;
 padding:0;
 border-radius:50%;
 color:var(--buddy-picker-muted,#667368);
}
.buddy-thinking-picker .owui-rl-close svg { width:16px; height:16px; }
.buddy-thinking-picker .owui-rl-options {
 display:grid;
 grid-template-columns:repeat(6,minmax(0,1fr));
 gap:6px;
}
.buddy-thinking-picker .owui-rl-options button {
 grid-column:span 2;
 min-height:44px;
 padding:9px 10px;
 border:1px solid var(--buddy-picker-edge,rgba(39,99,75,.14));
 border-radius:11px;
 font-size:calc(.875rem + var(--buddy-font-size-offset,0px));
}
.buddy-thinking-picker .owui-rl-options button:nth-child(1),
.buddy-thinking-picker .owui-rl-options button:nth-child(2),
.buddy-thinking-picker .owui-rl-options button:nth-child(6),
.buddy-thinking-picker .owui-rl-options button:nth-child(7) { grid-column:span 3; }
.buddy-thinking-picker[data-compact="true"] { padding:2px; }
.buddy-thinking-picker[data-compact="true"] .owui-rl-header {
 min-height:32px;
 padding:0 2px 0 6px;
}
.buddy-thinking-picker[data-compact="true"] button.owui-rl-close {
 width:32px;
 min-height:32px;
}
.buddy-thinking-picker[data-compact="true"] .owui-rl-options { gap:2px; }
.buddy-thinking-picker[data-compact="true"] .owui-rl-options button { padding:4px 6px; }
.buddy-thinking-picker[data-compact="true"] .owui-rl-hint { display:none; }
.buddy-thinking-picker[data-wide="true"] .owui-rl-options {
 grid-template-columns:repeat(7,minmax(0,1fr));
}
.buddy-thinking-picker[data-wide="true"] .owui-rl-options button:nth-child(n) { grid-column:auto; }
.buddy-thinking-picker .owui-rl-option-label { display:flex; min-width:0; flex-direction:column; gap:2px; }
.buddy-thinking-picker .owui-rl-hint {
 color:var(--buddy-picker-muted,#667368);
 font-size:calc(.6875rem + var(--buddy-font-size-offset,0px));
 line-height:1.3;
}
.buddy-thinking-picker button:hover,
.buddy-thinking-picker button:focus-visible { background:var(--buddy-picker-hover,#f0f5f0); }
.buddy-thinking-picker .owui-rl-options [aria-checked="true"] {
 border-color:var(--buddy-picker-selected-edge,rgba(69,128,87,.35));
 background:var(--buddy-picker-selected,#e8f2e9);
}
.buddy-thinking-picker .owui-rl-check { color:var(--buddy-picker-text,#31483b); }
.dark .buddy-thinking-picker .owui-rl-hint,
.dark .buddy-thinking-picker button.owui-rl-close { color:var(--buddy-picker-muted,#9daa9f); }
.dark .buddy-thinking-picker button:hover,
.dark .buddy-thinking-picker button:focus-visible { background:var(--buddy-picker-hover,#333e36); }
.dark .buddy-thinking-picker .owui-rl-options [aria-checked="true"] {
 border-color:var(--buddy-picker-selected-edge,rgba(153,194,164,.35));
 background:var(--buddy-picker-selected,#354d3b);
}
.dark .buddy-thinking-picker .owui-rl-check { color:var(--buddy-picker-text,#e4eee7); }`;

	let chip = null;
	let menu = null;
	let menuEditor = null;

	function labelFor(value) {
		return (LEVELS.find((l) => l.value === value) || LEVELS[0]).label;
	}

	// Only called on insert and on change: the MutationObserver would see every text update.
	function renderChip() {
		if (!chip) return;
		const level = currentLevel();
		chip.dataset.set = String(Boolean(level));
		chip.querySelector('.owui-rl-label').textContent = level ? labelFor(level) : 'Thinking';
		chip.querySelector('.needle').style.transform = `rotate(${NEEDLE_ANGLE[level] ?? 0}deg)`;
		chip.title = `Thinking level: ${labelFor(level)}${level ? '' : ' (model or chat setting)'}`;
		chip.setAttribute('aria-label', chip.title);
	}

	function mobileComposer() {
		const touchLayout = window.matchMedia('(pointer: coarse)').matches || window.innerWidth < 768;
		if (!touchLayout) return null;
		return chip?.closest('.buddy-composer') || null;
	}

	function focusedDraftEditor() {
		if (!mobileComposer()) return null;
		const activeElement = document.activeElement;
		const editor = document.getElementById('chat-input');
		if (!editor || editor.getAttribute('contenteditable') !== 'true') return null;
		if (activeElement === editor || editor.contains(activeElement)) return editor;
		return null;
	}
	function preserveDraftFocus(event) {
		if (!event.isPrimary || event.button !== 0) return;
		const editor = focusedDraftEditor();
		if (editor) event.preventDefault();
	}

	function restoreMenuFocus(editor) {
		if (editor?.isConnected) {
			editor.focus({ preventScroll: true });
		} else if (chip?.isConnected) {
			chip.focus({ preventScroll: true });
		}
	}

	function createChip() {
		const button = document.createElement('button');
		button.type = 'button';
		button.id = 'owui-reasoning-chip';
		button.className = 'owui-rl-chip';
		button.setAttribute('aria-haspopup', 'menu');
		button.setAttribute('aria-expanded', 'false');
		button.innerHTML = `${GAUGE}<span class="owui-rl-label"></span>`;
		button.addEventListener('pointerdown', preserveDraftFocus);
		button.addEventListener('click', (e) => {
			e.preventDefault();
			e.stopPropagation();
			menu ? closeMenu() : openMenu();
		});
		return button;
	}

	function createMobileMenuHeader() {
		const header = document.createElement('div');
		header.className = 'owui-rl-header';
		const title = document.createElement('div');
		title.className = 'owui-rl-title';
		title.textContent = 'Thinking';
		const closeButton = document.createElement('button');
		closeButton.type = 'button';
		closeButton.className = 'owui-rl-close';
		closeButton.setAttribute('aria-label', 'Close thinking picker');
		closeButton.innerHTML =
			'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
		function handleCloseClick(event) {
			event.preventDefault();
			event.stopPropagation();
			const editor = menuEditor;
			closeMenu();
			restoreMenuFocus(editor);
		}
		closeButton.addEventListener('click', handleCloseClick);
		header.appendChild(title);
		header.appendChild(closeButton);
		return header;
	}

	function openMenu() {
		const level = currentLevel();
		const mobile = Boolean(mobileComposer());
		menuEditor = focusedDraftEditor();
		menu = document.createElement('div');
		menu.className = 'owui-rl-menu';
		if (chip.closest('.buddy-composer')) menu.setAttribute('data-composer-menu', 'thinking');
		menu.addEventListener('pointerdown', preserveDraftFocus);
		menu.setAttribute('role', 'menu');
		let optionsContainer = menu;
		if (mobile) {
			menu.className = 'owui-rl-menu buddy-thinking-picker buddy-composer-picker';
			menu.appendChild(createMobileMenuHeader());
			optionsContainer = document.createElement('div');
			optionsContainer.className = 'owui-rl-options';
			menu.appendChild(optionsContainer);
		} else {
			menu.innerHTML = '<div class="owui-rl-title">Thinking level</div>';
		}
		for (const item of LEVELS) {
			const option = document.createElement('button');
			option.type = 'button';
			option.setAttribute('role', 'menuitemradio');
			option.setAttribute('aria-checked', String(item.value === level));
			option.setAttribute('aria-label', item.hint ? `${item.label}: ${item.hint}` : item.label);
			option.dataset.value = item.value;
			let hintMarkup = '';
			if (item.hint) hintMarkup = `<span class="owui-rl-hint">${item.hint}</span>`;
			let labelClass = '';
			if (mobile) {
				labelClass = ' class="owui-rl-option-label"';
			} else if (hintMarkup) {
				hintMarkup = ` ${hintMarkup}`;
			}
			const labelMarkup = `<span${labelClass}>${item.label}${hintMarkup}</span>`;
			let checkmark = '';
			if (item.value === level) checkmark = '&#10003;';
			option.innerHTML = `${labelMarkup}<span class="owui-rl-check">${checkmark}</span>`;
			option.addEventListener('click', (e) => {
				e.preventDefault();
				e.stopPropagation();
				store.set(item.value);
				renderChip();
				const editor = menuEditor;
				closeMenu();
				restoreMenuFocus(editor);
			});
			optionsContainer.appendChild(option);
		}
		document.body.appendChild(menu);
		positionMenu();
		if (mobile) revealSelectedOption();
		chip.setAttribute('aria-expanded', 'true');
		if (!menuEditor) {
			menu.querySelector('[aria-checked="true"]').focus();
		}
	}

	function revealSelectedOption() {
		const selected = menu?.querySelector('[aria-checked="true"]');
		const header = menu?.querySelector('.owui-rl-header');
		if (!selected || !header) return;
		if (menu.scrollHeight <= menu.clientHeight) return;
		const menuRect = menu.getBoundingClientRect();
		const optionRect = selected.getBoundingClientRect();
		const visibleTop = header.getBoundingClientRect().bottom;
		const visibleBottom = menuRect.bottom - 6;
		if (optionRect.bottom > visibleBottom) {
			menu.scrollTop += optionRect.bottom - visibleBottom;
		} else if (optionRect.top < visibleTop) {
			menu.scrollTop -= visibleTop - optionRect.top;
		}
	}
	function positionMobileMenu(composer, viewport) {
		const padding = 8;
		const gap = 6;
		const rect = composer.getBoundingClientRect();
		const header = composer.closest('.buddy-chat')?.querySelector('.buddy-chat-header');
		let visibleTop = viewport.top;
		if (header) visibleTop = Math.max(visibleTop, header.getBoundingClientRect().bottom);
		const width = Math.max(0, Math.min(rect.width, viewport.width - padding * 2));
		const maxLeft = viewport.left + viewport.width - width - padding;
		const left = Math.max(viewport.left + padding, Math.min(rect.left, maxLeft));
		const viewportBottom = viewport.top + viewport.height - padding;
		const composerBottom = Math.min(rect.top - gap, viewportBottom);
		const spaceAbove = Math.max(0, composerBottom - visibleTop - padding);
		const wide = width >= 640;
		const compact = wide || spaceAbove < 230;
		menu.setAttribute('data-compact', String(compact));
		menu.setAttribute('data-wide', String(wide));
		menu.style.width = `${width}px`;
		const naturalHeight = menu.offsetHeight;
		const overlay = spaceAbove < naturalHeight;
		menu.setAttribute('data-overlay', String(overlay));
		let availableHeight = spaceAbove;
		let top = composerBottom - naturalHeight;
		if (overlay) {
			// Short landscape keyboards can leave only a heading above the composer.
			// Use the visible area below navigation so every thinking choice stays reachable.
			availableHeight = Math.max(0, viewportBottom - visibleTop - padding);
			top = visibleTop + padding;
		}
		menu.style.maxHeight = `${availableHeight}px`;
		if (!overlay) top = composerBottom - menu.offsetHeight;
		menu.style.left = `${left}px`;
		menu.style.top = `${Math.max(visibleTop + padding, top)}px`;
	}
	function positionMenu() {
		if (!menu || !chip) return;
		const scrollTop = menu.scrollTop;
		const rect = chip.getBoundingClientRect();
		const viewport = window.visualViewport;
		const viewportLeft = viewport?.offsetLeft ?? 0;
		const viewportTop = viewport?.offsetTop ?? 0;
		const viewportWidth = viewport?.width ?? window.innerWidth;
		const viewportHeight = viewport?.height ?? window.innerHeight;
		const viewportBottom = viewportTop + viewportHeight;
		const padding = 8;
		const gap = 6;
		menu.style.maxWidth = `${Math.max(0, viewportWidth - padding * 2)}px`;
		menu.style.maxHeight = '';
		const composer = mobileComposer();
		if (composer) {
			positionMobileMenu(composer, {
				left: viewportLeft,
				top: viewportTop,
				width: viewportWidth,
				height: viewportHeight
			});
			menu.scrollTop = scrollTop;
			return;
		}
		const spaceAbove = Math.max(0, rect.top - viewportTop - padding - gap);
		const spaceBelow = Math.max(0, viewportBottom - rect.bottom - padding - gap);
		const openAbove = spaceAbove >= menu.offsetHeight || spaceAbove >= spaceBelow;
		const viewportAvailableHeight = Math.max(0, viewportHeight - padding * 2);
		const availableHeight = Math.min(viewportAvailableHeight, openAbove ? spaceAbove : spaceBelow);
		menu.style.maxHeight = `${availableHeight}px`;
		const width = menu.offsetWidth;
		const height = Math.min(menu.offsetHeight, availableHeight);
		const maxLeft = viewportLeft + viewportWidth - width - padding;
		const left = Math.max(viewportLeft + padding, Math.min(rect.right - width, maxLeft));
		let top = rect.bottom + gap;
		if (openAbove) top = rect.top - height - gap;
		menu.style.left = `${left}px`;
		const maxTop = viewportBottom - height - padding;
		menu.style.top = `${Math.max(viewportTop + padding, Math.min(top, maxTop))}px`;
		// Removing the height limit for measurement can reset the menu's scroll position.
		menu.scrollTop = scrollTop;
	}

	function handleLayoutScroll(event) {
		if (menu && event.target instanceof Element && menu.contains(event.target)) return;
		positionMenu();
	}

	function closeMenu() {
		if (!menu) return;
		menu.remove();
		menu = null;
		menuEditor = null;
		chip && chip.setAttribute('aria-expanded', 'false');
	}

	document.addEventListener(
		'pointerdown',
		(e) => {
			if (menu && !menu.contains(e.target) && e.target !== chip && !chip?.contains(e.target))
				closeMenu();
		},
		true
	);
	document.addEventListener('keydown', (e) => {
		if (!menu) return;
		const items = [...menu.querySelectorAll('[role="menuitemradio"]')];
		const index = items.indexOf(document.activeElement);
		if (e.key === 'Escape') {
			e.preventDefault();
			const editor = menuEditor;
			closeMenu();
			restoreMenuFocus(editor);
		} else if (!menuEditor && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
			e.preventDefault();
			const step = e.key === 'ArrowDown' ? 1 : -1;
			items[(index + step + items.length) % items.length].focus();
		}
	});
	window.addEventListener('resize', positionMenu);
	window.visualViewport?.addEventListener('resize', positionMenu);
	window.visualViewport?.addEventListener('scroll', positionMenu);
	window.addEventListener('scroll', handleLayoutScroll, true);
	window.addEventListener('storage', (e) => {
		if (e.key === STORAGE_KEY) {
			sessionLevel = store.get();
			renderChip();
		}
	});

	const buttonCount = (el) => el.querySelectorAll('button').length + (el.matches('button') ? 1 : 0);

	/**
	 * The model picker's slot in the input bar: its highest wrapper that holds no
	 * other buttons, i.e. the element sitting beside the mic/send buttons.
	 */
	function modelPickerSlot() {
		const container = document.getElementById('message-input-container');
		const picker =
			container && container.querySelector('button[id^="model-selector-"][id$="-button"]');
		if (!picker) return null;
		const settingsSlot = picker.closest('.buddy-composer-settings .buddy-composer-model');
		if (settingsSlot) return settingsSlot;
		let node = picker;
		for (
			let depth = 0;
			depth < 10 && node.parentElement && node.parentElement !== container;
			depth++
		) {
			const parent = node.parentElement;
			if (buttonCount(parent) - (parent.contains(chip) ? 1 : 0) > buttonCount(node)) return node;
			node = parent;
		}
		return null;
	}

	function ensureChip() {
		const slot = modelPickerSlot();
		if (!slot) return;
		if (chip && slot.nextElementSibling === chip) return;
		if (!chip) chip = createChip();
		closeMenu();
		slot.insertAdjacentElement('afterend', chip);
		renderChip();
	}

	function start() {
		const style = document.createElement('style');
		style.id = 'owui-reasoning-chip-style';
		style.textContent = css;
		document.head.appendChild(style);

		// setTimeout, not requestAnimationFrame: rAF never fires in a background tab.
		let queued = false;
		new MutationObserver(() => {
			if (queued) return;
			queued = true;
			setTimeout(() => {
				queued = false;
				ensureChip();
			}, 50);
		}).observe(document.body, { childList: true, subtree: true });
		ensureChip();
	}

	if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
	else start();
})();
