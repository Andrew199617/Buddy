import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/reasoning-chip.js', import.meta.url), 'utf8');

function eventTarget() {
	const listeners = new Map();
	return {
		addEventListener(type, callback, capture = false) {
			const registered = listeners.get(type) || [];
			registered.push({ callback, capture: capture === true });
			listeners.set(type, registered);
		},
		dispatch(type, event, capture) {
			for (const listener of listeners.get(type) || []) {
				if (capture !== undefined && listener.capture !== capture) continue;
				listener.callback(event);
			}
		}
	};
}

function createPage({ width = 390, touch = true, buddy = true, selectedLevel = '' } = {}) {
	const focusChanges = [];
	const storage = new Map();
	if (selectedLevel) storage.set('owui-local:reasoning-effort', selectedLevel);
	const document = eventTarget();

	function bounds(left, top, width, height) {
		return { left, right: left + width, top, bottom: top + height, width, height };
	}
	class Element {
		constructor(tagName) {
			Object.assign(this, eventTarget());
			this.tagName = tagName;
			this.children = [];
			this.parentElement = null;
			this.attributes = new Map();
			this.dataset = {};
			this.style = {};
			this.scrollTop = 0;
			this.layoutMeasurements = 0;
			let maxHeight = '';
			Object.defineProperty(this.style, 'maxHeight', {
				get: () => maxHeight,
				set: (value) => {
					maxHeight = value;
					if (value === '' && this.className.split(' ').includes('owui-rl-menu')) {
						// An unconstrained menu has no overflow; browsers reset its scroll position.
						this.scrollTop = 0;
						this.layoutMeasurements++;
					}
				}
			});
			this.className = '';
			this.id = '';
		}
		appendChild(child) {
			child.remove();
			child.parentElement = this;
			this.children.push(child);
			return child;
		}
		remove() {
			if (!this.parentElement) return;
			const siblings = this.parentElement.children;
			siblings.splice(siblings.indexOf(this), 1);
			this.parentElement = null;
		}
		insertAdjacentElement(position, child) {
			assert.equal(position, 'afterend');
			child.remove();
			const siblings = this.parentElement.children;
			child.parentElement = this.parentElement;
			siblings.splice(siblings.indexOf(this) + 1, 0, child);
		}
		get nextElementSibling() {
			const siblings = this.parentElement?.children || [];
			return siblings[siblings.indexOf(this) + 1] || null;
		}
		get isConnected() {
			return document.body.contains(this) || document.head.contains(this);
		}
		set innerHTML(html) {
			this.children = [];
			// The fixture only needs named descendants, not rendered SVG or option text.
			for (const match of html.matchAll(/<(\w+)[^>]*class="([^"]+)"[^>]*>/g)) {
				const child = new Element(match[1]);
				child.className = match[2];
				this.appendChild(child);
			}
		}
		setAttribute(name, value) {
			this.attributes.set(name, String(value));
		}
		getAttribute(name) {
			return this.attributes.get(name) ?? null;
		}
		matches(selector) {
			const parts = selector.split(' ');
			if (parts.length > 1) {
				return (
					this.matches(parts.at(-1)) &&
					Boolean(this.parentElement?.closest(parts.slice(0, -1).join(' ')))
				);
			}
			if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
			if (selector.startsWith('button') && this.tagName !== 'button') return false;
			if (selector === 'button') return this.tagName === 'button';
			const attributes = [...selector.matchAll(/\[([^\]=^$]+)(\^=|\$=|=)?"?([^\]"]*)"?\]/g)];
			if (!attributes.length) return false;
			return attributes.every(([, name, operator, expected]) => {
				const value = name === 'id' ? this.id : this.getAttribute(name);
				if (!operator) return value !== null;
				if (operator === '^=') return value?.startsWith(expected);
				if (operator === '$=') return value?.endsWith(expected);
				return value === expected;
			});
		}
		closest(selector) {
			for (let node = this; node; node = node.parentElement) {
				if (node.matches(selector)) return node;
			}
			return null;
		}
		contains(target) {
			for (let node = target; node; node = node.parentElement) {
				if (node === this) return true;
			}
			return false;
		}
		querySelectorAll(selector) {
			const descendants = this.children.flatMap((child) => [
				child,
				...child.querySelectorAll(selector)
			]);
			return descendants.filter((child) => child.matches(selector));
		}
		querySelector(selector) {
			return this.querySelectorAll(selector)[0] || null;
		}
		focus(options) {
			if (document.activeElement === this) return;
			focusChanges.push({ from: document.activeElement, to: this, options });
			document.activeElement = this;
		}
		getBoundingClientRect() {
			if (this.rect) return this.rect;
			const menu = this.closest('.buddy-thinking-picker');
			if (menu) {
				const left = parseFloat(menu.style.left) || 0;
				const top = parseFloat(menu.style.top) || 0;
				const width = menu.offsetWidth;
				const compact = menu.getAttribute('data-compact') === 'true';
				const wide = menu.getAttribute('data-wide') === 'true';
				const padding = compact ? 2 : 6;
				const headerHeight = compact ? 32 : 44;
				if (this === menu) return bounds(left, top, width, menu.offsetHeight);
				if (this.className === 'owui-rl-header')
					return bounds(left, top + padding, width, headerHeight);
				if (this.getAttribute('role') === 'menuitemradio') {
					const choices = this.parentElement.querySelectorAll('[role="menuitemradio"]');
					const index = choices.indexOf(this);
					let row = 0;
					let column = index;
					let columns = 7;
					if (!wide) {
						columns = 2;
						if (index >= 2 && index < 5) {
							row = 1;
							column = index - 2;
							columns = 3;
						} else if (index >= 5) {
							row = 2;
							column = index - 5;
						}
					}
					const firstRowHeight = compact ? 44 : 64;
					const gap = compact ? 2 : 6;
					let rowTop = 0;
					let height = firstRowHeight;
					if (row > 0) {
						rowTop = firstRowHeight + gap + (row - 1) * (44 + gap);
						height = 44;
					}
					return bounds(
						left + (column * width) / columns,
						top + padding + headerHeight + rowTop - menu.scrollTop,
						width / columns,
						height
					);
				}
			}
			return bounds(220, 350, 152, 32);
		}
		get offsetWidth() {
			const width = parseFloat(this.style.width) || 220;
			return Math.min(width, parseFloat(this.style.maxWidth) || width);
		}
		get naturalHeight() {
			if (!this.className.split(' ').includes('owui-rl-menu')) return 32;
			if (!this.className.split(' ').includes('buddy-thinking-picker')) return 352;
			if (this.getAttribute('data-wide') === 'true') return 82;
			if (this.getAttribute('data-compact') === 'true') return 174;
			return 222;
		}
		get offsetHeight() {
			if (!this.style.maxHeight) return this.naturalHeight;
			return Math.min(this.naturalHeight, parseFloat(this.style.maxHeight));
		}
		get clientHeight() {
			return Math.max(0, this.offsetHeight - 2);
		}
		get scrollHeight() {
			return this.naturalHeight - 2;
		}
	}

	document.body = new Element('body');
	document.head = new Element('head');
	document.readyState = 'complete';
	document.activeElement = document.body;
	document.createElement = (tagName) => new Element(tagName);
	document.getElementById = (id) => {
		const nodes = [document.body, ...allDescendants(document.body)];
		return nodes.find((node) => node.id === id) || null;
	};
	function allDescendants(node) {
		return node.children.flatMap((child) => [child, ...allDescendants(child)]);
	}

	const chat = document.body.appendChild(new Element('div'));
	chat.className = buddy ? 'buddy-chat' : 'legacy-chat';
	const header = chat.appendChild(new Element('nav'));
	header.className = 'buddy-chat-header';
	header.rect = { left: 0, right: width, top: 12, bottom: 76, width, height: 64 };
	const container = chat.appendChild(new Element('form'));
	container.id = 'message-input-container';
	const composer = container.appendChild(new Element('div'));
	composer.className = buddy ? 'buddy-composer' : 'legacy-composer';
	composer.rect = {
		left: 18,
		right: width - 18,
		top: 350,
		bottom: 415,
		width: width - 36,
		height: 65
	};
	const editor = composer.appendChild(new Element('div'));
	editor.id = 'chat-input';
	editor.setAttribute('contenteditable', 'true');
	const settings = composer.appendChild(new Element('div'));
	settings.className = 'buddy-composer-settings';
	const modelSlot = settings.appendChild(new Element('div'));
	modelSlot.className = 'buddy-composer-model';
	const modelButton = modelSlot.appendChild(new Element('button'));
	modelButton.id = 'model-selector-model-button';
	const transcript = document.body.appendChild(new Element('div'));

	const viewport = Object.assign(eventTarget(), {
		width,
		height: 420,
		offsetLeft: 5,
		offsetTop: 12
	});
	const window = Object.assign(eventTarget(), {
		innerWidth: width,
		innerHeight: 844,
		visualViewport: viewport,
		matchMedia: () => ({ matches: touch }),
		fetch: () => Promise.resolve({})
	});
	const localStorage = {
		getItem: (key) => storage.get(key),
		setItem: (key, value) => storage.set(key, value),
		removeItem: (key) => storage.delete(key)
	};
	class MutationObserver {
		observe() {}
	}
	vm.runInNewContext(source, {
		window,
		document,
		localStorage,
		Element,
		MutationObserver,
		setTimeout() {}
	});
	const chip = document.getElementById('owui-reasoning-chip');
	assert.ok(chip, 'Production script should insert its thinking chip');

	function dispatch(target, type, fields = {}) {
		const event = {
			target,
			isPrimary: true,
			button: 0,
			defaultPrevented: false,
			propagationStopped: false,
			preventDefault() {
				this.defaultPrevented = true;
			},
			stopPropagation() {
				this.propagationStopped = true;
			},
			...fields
		};
		document.dispatch(type, event, true);
		const path = [];
		for (let node = target; node; node = node.parentElement) path.push(node);
		for (const node of [...path].reverse()) node.dispatch(type, event, true);
		for (const node of path) {
			if (event.propagationStopped) break;
			node.dispatch(type, event, false);
		}
		if (!event.propagationStopped) document.dispatch(type, event, false);
		return event;
	}
	function tap(target) {
		const pointer = dispatch(target, 'pointerdown');
		// Model the native focus transfer that a canceled pointerdown must prevent.
		if (!pointer.defaultPrevented) {
			if (target.tagName === 'button' || target.getAttribute('contenteditable') === 'true')
				target.focus();
			else document.body.focus();
		}
		dispatch(target, 'click');
		return pointer;
	}
	return {
		window,
		chip,
		composer,
		header,
		editor,
		transcript,
		document,
		viewport,
		storage,
		focusChanges,
		tap,
		keydown: (key) => dispatch(document.activeElement, 'keydown', { key }),
		get menu() {
			return document.body.querySelector('.owui-rl-menu');
		}
	};
}

test('phone drafting retains editor focus while opening, selecting, and toggling thinking', () => {
	const page = createPage();
	page.editor.focus();
	page.focusChanges.length = 0;
	assert.equal(page.tap(page.chip).defaultPrevented, true);
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(page.menu.getAttribute('data-composer-menu'), 'thinking');
	const medium = page.menu
		.querySelectorAll('[role="menuitemradio"]')
		.find((button) => button.dataset.value === 'medium');
	assert.equal(page.tap(medium).defaultPrevented, true);
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(page.menu, null);
	assert.equal(page.storage.get('owui-local:reasoning-effort'), 'medium');
	page.tap(page.chip);
	page.tap(page.chip);
	assert.equal(page.menu, null);
	assert.equal(page.chip.getAttribute('aria-expanded'), 'false');
	assert.deepEqual(page.focusChanges, [], 'Opening controls must never briefly blur the draft');
});

test('thinking Escape preserves draft focus and editor arrows remain available', () => {
	const page = createPage();
	page.editor.focus();
	page.tap(page.chip);
	assert.equal(page.keydown('ArrowDown').defaultPrevented, false);
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(page.keydown('Escape').defaultPrevented, true);
	assert.equal(page.menu, null);
	assert.equal(page.document.activeElement, page.editor);
});

test('a transcript tap dismisses thinking without returning focus to the editor', () => {
	const page = createPage();
	page.editor.focus();
	page.tap(page.chip);
	page.focusChanges.length = 0;
	assert.equal(page.tap(page.transcript).defaultPrevented, false);
	assert.equal(page.menu, null);
	assert.equal(page.document.activeElement, page.document.body);
	assert.equal(page.focusChanges.length, 1);
	assert.equal(page.focusChanges[0].to, page.document.body);
});

test('desktop thinking retains option autofocus, arrows, and Escape return to the chip', () => {
	const page = createPage({ width: 1200, touch: false });
	page.editor.focus();
	assert.equal(page.tap(page.chip).defaultPrevented, false);
	const options = page.menu.querySelectorAll('[role="menuitemradio"]');
	assert.equal(page.document.activeElement, options[0]);
	assert.equal(page.keydown('ArrowDown').defaultPrevented, true);
	assert.equal(page.document.activeElement, options[1]);
	page.keydown('ArrowUp');
	assert.equal(page.document.activeElement, options[0]);
	page.keydown('ArrowUp');
	assert.equal(page.document.activeElement, options.at(-1));
	page.keydown('Escape');
	assert.equal(page.menu, null);
	assert.equal(page.document.activeElement, page.chip);
});

test('thinking menu stays within the keyboard viewport as it resizes and pans', () => {
	const page = createPage();
	page.editor.focus();
	page.tap(page.chip);
	function assertWithinViewport() {
		const left = parseFloat(page.menu.style.left);
		const top = parseFloat(page.menu.style.top);
		assert.ok(left >= page.viewport.offsetLeft + 8);
		assert.ok(left + page.menu.offsetWidth <= page.viewport.offsetLeft + page.viewport.width - 8);
		assert.ok(top >= page.viewport.offsetTop + 8);
		assert.ok(top + page.menu.offsetHeight <= page.viewport.offsetTop + page.viewport.height - 8);
	}
	assertWithinViewport();
	const initialTop = page.menu.style.top;
	page.viewport.height = 230;
	page.viewport.offsetTop = 160;
	page.viewport.dispatch('resize', {});
	assertWithinViewport();
	assert.notEqual(page.menu.style.top, initialTop);
	page.viewport.offsetTop = 170;
	page.viewport.offsetLeft = 15;
	page.viewport.dispatch('scroll', {});
	assertWithinViewport();
	assert.equal(page.document.activeElement, page.editor);
});
test('phone thinking without a focused draft keeps normal menu focus behavior', () => {
	const page = createPage();
	assert.equal(page.tap(page.chip).defaultPrevented, false);
	const options = page.menu.querySelectorAll('[role="menuitemradio"]');
	assert.equal(page.document.activeElement, options[0]);
	page.tap(options.find((button) => button.dataset.value === 'high'));
	assert.equal(page.document.activeElement, page.chip);
	assert.equal(page.menu, null);
	assert.equal(page.chip.querySelector('.owui-rl-label').textContent, 'High');
});

test('scrolling thinking options does not remeasure or reset the menu', () => {
	const page = createPage();
	page.viewport.height = 180;
	page.editor.focus();
	page.tap(page.chip);
	page.menu.scrollTop = 6;
	const measurements = page.menu.layoutMeasurements;
	page.window.dispatch('scroll', { target: page.menu });
	assert.equal(page.menu.layoutMeasurements, measurements);
	assert.equal(page.menu.scrollTop, 6);
	const option = page.menu.querySelectorAll('[role="menuitemradio"]')[0];
	page.window.dispatch('scroll', { target: option });
	assert.equal(page.menu.layoutMeasurements, measurements);
	assert.equal(page.menu.scrollTop, 6);
});

test('viewport and page repositioning preserve the scrolled thinking option list', () => {
	const page = createPage();
	page.viewport.height = 180;
	page.editor.focus();
	page.tap(page.chip);
	page.menu.scrollTop = 6;
	const measurements = page.menu.layoutMeasurements;
	page.viewport.height = 230;
	page.viewport.offsetTop = 160;
	page.viewport.dispatch('resize', {});
	assert.equal(page.menu.layoutMeasurements, measurements + 1);
	assert.equal(page.menu.scrollTop, 6);
	page.viewport.dispatch('scroll', {});
	assert.equal(page.menu.scrollTop, 6);
	page.window.dispatch('scroll', { target: page.transcript });
	assert.equal(page.menu.layoutMeasurements, measurements + 3);
	assert.equal(page.menu.scrollTop, 6);
	assert.equal(page.document.activeElement, page.editor);
});

test('mobile thinking uses composer width above the draft and stays below navigation', () => {
	const page = createPage();
	page.editor.focus();
	page.tap(page.chip);
	assert.ok(page.menu.className.split(' ').includes('buddy-composer-picker'));
	assert.equal(parseFloat(page.menu.style.width), page.composer.rect.width);
	assert.equal(parseFloat(page.menu.style.left), page.composer.rect.left);
	assert.equal(
		parseFloat(page.menu.style.top) + page.menu.offsetHeight,
		page.composer.rect.top - 6
	);
	assert.ok(parseFloat(page.menu.style.top) >= page.header.rect.bottom + 8);
	const options = page.menu.querySelector('.owui-rl-options');
	assert.deepEqual(
		options.querySelectorAll('[role="menuitemradio"]').map((option) => option.dataset.value),
		['', 'none', 'low', 'medium', 'high', 'xhigh', 'max']
	);
	assert.ok(options.querySelector('[aria-checked="true"]').querySelector('.owui-rl-hint'));
	page.header.rect.bottom = 220;
	page.viewport.dispatch('resize', {});
	assert.equal(page.menu.getAttribute('data-overlay'), 'true');
	assert.ok(parseFloat(page.menu.style.top) >= 228);
	assert.ok(
		parseFloat(page.menu.style.top) + page.menu.offsetHeight <=
			page.viewport.offsetTop + page.viewport.height - 8
	);
});

test('mobile thinking clamps composer width to a narrow panned viewport', () => {
	const page = createPage({ width: 320 });
	page.viewport.offsetLeft = 40;
	page.viewport.width = 220;
	page.editor.focus();
	page.tap(page.chip);
	assert.equal(parseFloat(page.menu.style.width), 204);
	assert.equal(parseFloat(page.menu.style.left), 48);
	assert.ok(parseFloat(page.menu.style.left) + page.menu.offsetWidth <= 252);
});

test('mobile thinking close preserves the focused draft and chosen level', () => {
	const page = createPage();
	page.editor.focus();
	page.tap(page.chip);
	const close = page.menu.querySelector('.owui-rl-close');
	assert.equal(close.getAttribute('aria-label'), 'Close thinking picker');
	page.focusChanges.length = 0;
	assert.equal(page.tap(close).defaultPrevented, true);
	assert.equal(page.menu, null);
	assert.equal(page.document.activeElement, page.editor);
	assert.deepEqual(page.focusChanges, []);
	assert.equal(page.storage.get('owui-local:reasoning-effort'), undefined);
});

test('desktop thinking keeps the compact chip-anchored list without mobile chrome', () => {
	const page = createPage({ width: 1200, touch: false });
	page.tap(page.chip);
	assert.equal(page.menu.className, 'owui-rl-menu');
	assert.equal(page.menu.querySelector('.owui-rl-header'), null);
	assert.equal(page.menu.querySelector('.owui-rl-options'), null);
	assert.equal(page.menu.style.width, undefined);
	assert.equal(
		parseFloat(page.menu.style.left) + page.menu.offsetWidth,
		page.chip.getBoundingClientRect().right
	);
});

test('compact portrait opens all thinking labels without scrolling away Default or Off', () => {
	for (const selectedLevel of ['high', 'max']) {
		const page = createPage({ selectedLevel });
		page.composer.rect.top = 265;
		page.editor.focus();
		page.focusChanges.length = 0;
		page.tap(page.chip);
		assert.equal(page.menu.getAttribute('data-compact'), 'true');
		assert.equal(page.menu.getAttribute('data-overlay'), 'false');
		assert.equal(page.menu.scrollTop, 0);
		const header = page.menu.querySelector('.owui-rl-header');
		for (const option of page.menu.querySelectorAll('[role="menuitemradio"]')) {
			const rect = option.getBoundingClientRect();
			assert.ok(rect.top >= header.getBoundingClientRect().bottom);
			assert.ok(rect.bottom <= page.menu.getBoundingClientRect().bottom);
			assert.ok(rect.height >= 44);
		}
		assert.equal(page.menu.querySelector('[aria-checked="true"]').dataset.value, selectedLevel);
		assert.equal(page.document.activeElement, page.editor);
		assert.deepEqual(page.focusChanges, []);
	}
});

test('landscape with only a heading of space above the composer exposes all seven choices', () => {
	const page = createPage({ width: 844 });
	page.viewport.height = 260;
	page.viewport.offsetTop = 0;
	page.header.rect.bottom = 110;
	page.composer.rect = { left: 9, right: 835, top: 169, bottom: 303, width: 826, height: 134 };
	page.editor.focus();
	page.focusChanges.length = 0;
	page.tap(page.chip);
	assert.equal(page.menu.getAttribute('data-wide'), 'true');
	assert.equal(page.menu.getAttribute('data-overlay'), 'true');
	assert.equal(page.menu.scrollTop, 0);
	assert.equal(parseFloat(page.menu.style.top), 118);
	const header = page.menu.querySelector('.owui-rl-header');
	const choices = page.menu.querySelectorAll('[role="menuitemradio"]');
	assert.equal(choices.length, 7);
	for (const option of choices) {
		const rect = option.getBoundingClientRect();
		const centerX = rect.left + rect.width / 2;
		const centerY = rect.top + rect.height / 2;
		assert.ok(centerX >= page.viewport.offsetLeft + 8);
		assert.ok(centerX <= page.viewport.offsetLeft + page.viewport.width - 8);
		assert.ok(rect.top >= header.getBoundingClientRect().bottom);
		assert.ok(centerY <= page.viewport.height - 8);
		assert.ok(rect.height >= 44);
	}
	assert.match(choices[0].getAttribute('aria-label'), /Model or chat setting/);
	assert.match(choices[1].getAttribute('aria-label'), /No thinking/);
	assert.equal(page.document.activeElement, page.editor);
	assert.deepEqual(page.focusChanges, []);
});
