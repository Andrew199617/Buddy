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
 border-radius:.5rem;padding:.25rem .45rem;font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px));line-height:1.25rem;color:#4b5563;
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
 background:#fff;color:#111827;border:1px solid rgba(229,231,235,.8);
 box-shadow:0 10px 30px -10px rgba(0,0,0,.25);font-size: calc(.8125rem + var(--buddy-font-size-offset, 2px))}
.dark .owui-rl-menu{background:#171717;color:#f3f4f6;border-color:rgba(64,64,64,.6)}
.owui-rl-menu .owui-rl-title{padding:.3rem .55rem .35rem;font-size: calc(.72rem + var(--buddy-font-size-offset, 2px));color:#6b7280}
.owui-rl-menu button{display:flex;width:100%;align-items:center;justify-content:space-between;gap:.75rem;
 padding:.38rem .55rem;border:0;border-radius:.5rem;background:transparent;color:inherit;text-align:left;cursor:pointer}
.owui-rl-menu button:hover,.owui-rl-menu button:focus-visible{background:rgba(243,244,246,1);outline:none}
.dark .owui-rl-menu button:hover,.dark .owui-rl-menu button:focus-visible{background:rgba(38,38,38,1)}
.owui-rl-menu .owui-rl-hint{color:#9ca3af;font-size: calc(.72rem + var(--buddy-font-size-offset, 2px))}
.owui-rl-menu .owui-rl-check{width:.9rem;color:#0284c7}
.dark .owui-rl-menu .owui-rl-check{color:#7dd3fc}`;

	let chip = null;
	let menu = null;

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

	function createChip() {
		const button = document.createElement('button');
		button.type = 'button';
		button.id = 'owui-reasoning-chip';
		button.className = 'owui-rl-chip';
		button.setAttribute('aria-haspopup', 'menu');
		button.setAttribute('aria-expanded', 'false');
		button.innerHTML = `${GAUGE}<span class="owui-rl-label"></span>`;
		button.addEventListener('click', (e) => {
			e.preventDefault();
			e.stopPropagation();
			menu ? closeMenu() : openMenu();
		});
		return button;
	}

	function openMenu() {
		const level = currentLevel();
		menu = document.createElement('div');
		menu.className = 'owui-rl-menu';
		menu.setAttribute('role', 'menu');
		menu.innerHTML = '<div class="owui-rl-title">Thinking level</div>';
		for (const item of LEVELS) {
			const option = document.createElement('button');
			option.type = 'button';
			option.setAttribute('role', 'menuitemradio');
			option.setAttribute('aria-checked', String(item.value === level));
			option.setAttribute('aria-label', item.hint ? `${item.label}: ${item.hint}` : item.label);
			option.dataset.value = item.value;
			option.innerHTML =
				`<span>${item.label}${item.hint ? ` <span class="owui-rl-hint">${item.hint}</span>` : ''}</span>` +
				`<span class="owui-rl-check">${item.value === level ? '&#10003;' : ''}</span>`;
			option.addEventListener('click', (e) => {
				e.preventDefault();
				e.stopPropagation();
				store.set(item.value);
				renderChip();
				closeMenu();
				chip && chip.focus();
			});
			menu.appendChild(option);
		}
		document.body.appendChild(menu);
		positionMenu();
		chip.setAttribute('aria-expanded', 'true');
		(menu.querySelector('[aria-checked="true"]') || menu.querySelector('button')).focus();
	}

	function positionMenu() {
		if (!menu || !chip) return;
		const rect = chip.getBoundingClientRect();
		const width = menu.offsetWidth;
		const height = menu.offsetHeight;
		const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
		const above = rect.top - height - 6;
		menu.style.left = `${left}px`;
		menu.style.top = `${above >= 8 ? above : rect.bottom + 6}px`;
	}

	function closeMenu() {
		if (!menu) return;
		menu.remove();
		menu = null;
		chip && chip.setAttribute('aria-expanded', 'false');
	}

	document.addEventListener(
		'pointerdown',
		(e) => {
			if (menu && !menu.contains(e.target) && e.target !== chip && !chip?.contains(e.target)) closeMenu();
		},
		true
	);
	document.addEventListener('keydown', (e) => {
		if (!menu) return;
		const items = [...menu.querySelectorAll('button')];
		const index = items.indexOf(document.activeElement);
		if (e.key === 'Escape') {
			e.preventDefault();
			closeMenu();
			chip && chip.focus();
		} else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			const step = e.key === 'ArrowDown' ? 1 : -1;
			items[(index + step + items.length) % items.length].focus();
		}
	});
	window.addEventListener('resize', positionMenu);
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
		const picker = container && container.querySelector('button[id^="model-selector-"][id$="-button"]');
		if (!picker) return null;
		let node = picker;
		for (let depth = 0; depth < 10 && node.parentElement && node.parentElement !== container; depth++) {
			const parent = node.parentElement;
			if (buttonCount(parent) - (parent.contains(chip) ? 1 : 0) > buttonCount(node)) return node;
			node = parent;
		}
		return null;
	}

	function ensureChip() {
		const slot = modelPickerSlot();
		if (!slot || slot.nextElementSibling === chip) return;
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
