import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(
	new URL('../../src/lib/utils/composer-controls.ts', import.meta.url),
	'utf8'
);
const compiled = ts.transpileModule(source, {
	compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;

function eventTarget() {
	const listeners = new Map();
	return {
		addEventListener(type, callback, options = false) {
			const capture = options === true || options.capture === true;
			const registered = listeners.get(type) || [];
			registered.push({ callback, capture, passive: options.passive === true });
			listeners.set(type, registered);
		},
		removeEventListener(type, callback, options = false) {
			const capture = options === true || options.capture === true;
			listeners.set(
				type,
				(listeners.get(type) || []).filter(
					(entry) => entry.callback !== callback || entry.capture !== capture
				)
			);
		},
		dispatch(type, event, capture) {
			for (const listener of [...(listeners.get(type) || [])]) {
				if (listener.capture !== capture || event.immediateStopped) continue;
				event.passive = listener.passive;
				listener.callback(event);
				event.passive = false;
			}
		},
		listenerCount() {
			return [...listeners.values()].reduce((count, entries) => count + entries.length, 0);
		}
	};
}

function createPage({ mobile = true, install = true } = {}) {
	const document = eventTarget();
	const timers = new Map();
	let timerId = 0;
	let now = 1000;
	let controlActive = false;
	let composerFocused = true;
	let menuOpen = false;
	let expanded = true;
	let physicalTarget = null;
	const activations = new Map();
	const focusChanges = [];
	const lockChanges = [];

	function updateExpanded() {
		expanded = composerFocused || controlActive || menuOpen;
	}
	class Element {
		constructor(tagName, id = '') {
			Object.assign(this, eventTarget());
			this.tagName = tagName;
			this.id = id;
			this.children = [];
			this.parentElement = null;
			this.attributes = new Map();
			this.className = '';
			this.scrollWidth = 0;
			this.clientWidth = 0;
			this.scrollHeight = 0;
			this.clientHeight = 0;
			this.style = { overflowX: 'visible', overflowY: 'visible' };
			this.checked = false;
		}
		get ownerDocument() {
			return document;
		}
		get isConnected() {
			return document.body.contains(this);
		}
		appendChild(child) {
			child.parentElement = this;
			this.children.push(child);
			return child;
		}
		setAttribute(name, value) {
			this.attributes.set(name, String(value));
		}
		getAttribute(name) {
			return this.attributes.get(name) ?? null;
		}
		matches(selector) {
			return selector.split(',').some((part) => {
				part = part.trim();
				if (part === ':disabled') return this.getAttribute('disabled') !== null;
				if (part.startsWith('#')) return this.id === part.slice(1);
				if (part.startsWith('.')) return this.className.split(' ').includes(part.slice(1));
				const tag = part.match(/^[a-z]+/)?.[0];
				if (tag && tag !== this.tagName) return false;
				const attributes = [...part.matchAll(/\[([^\]=^$]+)(\^=|\$=|=)?"?([^\]"]*)"?\]/g)];
				return attributes.every(([, name, operator, expected]) => {
					const value = name === 'id' ? this.id : this.getAttribute(name);
					if (!operator) return value !== null;
					if (operator === '^=') return value?.startsWith(expected);
					if (operator === '$=') return value?.endsWith(expected);
					return value === expected;
				});
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
		focus() {
			if (document.activeElement === this) return;
			const previous = document.activeElement;
			document.activeElement = this;
			focusChanges.push(this);
			if (previous !== document.body) dispatch(previous, 'focusout', { relatedTarget: this });
			dispatch(this, 'focusin', { relatedTarget: previous });
		}
		blur() {
			if (document.activeElement !== this) return;
			document.activeElement = document.body;
			focusChanges.push(document.body);
			dispatch(this, 'focusout', { relatedTarget: null });
		}
		click() {
			dispatch(this, 'click', { detail: 0 });
		}
	}

	document.body = new Element('body');
	const form = document.body.appendChild(new Element('form'));
	const composer = form.appendChild(new Element('div'));
	composer.className = 'buddy-composer';
	const toolbar = composer.appendChild(new Element('div'));
	const model = toolbar.appendChild(new Element('button', 'model-selector-model-button'));
	const thinking = toolbar.appendChild(new Element('button', 'owui-reasoning-chip'));
	const editor = composer.appendChild(new Element('div', 'chat-input'));
	editor.setAttribute('contenteditable', 'true');
	const plus = composer.appendChild(new Element('button', 'input-menu-button'));
	const tools = composer.appendChild(new Element('button', 'integration-menu-button'));
	const blank = composer.appendChild(new Element('div'));
	const disabled = composer.appendChild(new Element('button'));
	disabled.setAttribute('disabled', '');
	const disabledIcon = disabled.appendChild(new Element('svg'));
	const toggle = composer.appendChild(new Element('div'));
	toggle.setAttribute('role', 'switch');
	const toggleIcon = toggle.appendChild(new Element('span'));
	const row = composer.appendChild(new Element('button'));
	const inertDecoration = row.appendChild(new Element('div'));
	inertDecoration.setAttribute('inert', '');
	const inertToggle = inertDecoration.appendChild(new Element('button'));
	inertToggle.setAttribute('role', 'switch');
	const checkbox = composer.appendChild(new Element('input'));
	checkbox.setAttribute('type', 'checkbox');
	const checkboxLabel = composer.appendChild(new Element('label'));
	checkboxLabel.control = checkbox;
	const textInput = composer.appendChild(new Element('input'));
	textInput.setAttribute('type', 'text');
	const textLabel = composer.appendChild(new Element('label'));
	textLabel.control = textInput;
	const select = composer.appendChild(new Element('select'));
	const fileInput = composer.appendChild(new Element('input'));
	fileInput.setAttribute('type', 'file');
	const upload = composer.appendChild(new Element('button'));
	const scrollRow = composer.appendChild(new Element('div'));
	scrollRow.style.overflowX = 'auto';
	scrollRow.clientWidth = 100;
	scrollRow.scrollWidth = 300;
	const scrollAction = scrollRow.appendChild(new Element('button'));
	const scrollBlank = scrollRow.appendChild(new Element('div'));
	const terminalPopup = scrollRow.appendChild(new Element('span'));
	terminalPopup.setAttribute('aria-haspopup', 'true');
	const terminal = terminalPopup.appendChild(new Element('button'));
	const other = form.appendChild(new Element('button'));
	const menu = document.body.appendChild(new Element('div'));
	menu.setAttribute('data-composer-menu', 'model-picker');
	const option = menu.appendChild(new Element('button'));
	const padding = menu.appendChild(new Element('div'));
	const search = menu.appendChild(new Element('input', 'model-search-input'));
	const toolsMenu = document.body.appendChild(new Element('div'));
	toolsMenu.setAttribute('data-composer-menu', 'integrations');
	const toolsToggle = toolsMenu.appendChild(new Element('div'));
	toolsToggle.setAttribute('role', 'menuitemcheckbox');
	const disabledOption = toolsMenu.appendChild(new Element('button'));
	disabledOption.setAttribute('aria-disabled', 'true');
	const childMenu = document.body.appendChild(new Element('div'));
	childMenu.className = 'model-selector-child-menu';
	const childOption = childMenu.appendChild(new Element('button'));
	const outside = document.body.appendChild(new Element('div'));
	const dialogInput = document.body.appendChild(new Element('input'));
	document.activeElement = editor;
	document.elementFromPoint = () => {
		if (toolbar.contains(physicalTarget) && !expanded) return form;
		return physicalTarget;
	};
	document.defaultView = {
		visualViewport: Object.assign(eventTarget(), {
			height: 320,
			width: 428,
			offsetTop: 0,
			offsetLeft: 0
		}),
		getComputedStyle(element) {
			return element.style;
		},
		setTimeout(callback) {
			timerId += 1;
			timers.set(timerId, callback);
			return timerId;
		},
		clearTimeout(id) {
			timers.delete(id);
		}
	};
	document.addEventListener('focusout', (event) => {
		if (event.target === editor || event.target === search) {
			if (controlActive) return;
			composerFocused = false;
			updateExpanded();
		}
	});
	document.addEventListener('focusin', (event) => {
		if (event.target === editor || event.target === search) {
			composerFocused = true;
			updateExpanded();
		}
	});
	for (const control of [
		model,
		thinking,
		plus,
		tools,
		option,
		childOption,
		other,
		disabled,
		toggle,
		row,
		inertToggle,
		checkbox,
		checkboxLabel,
		upload,
		fileInput,
		scrollAction,
		terminal,
		toolsToggle,
		disabledOption
	]) {
		activations.set(control, 0);
		control.addEventListener('click', () => {
			activations.set(control, activations.get(control) + 1);
			if (control === model || control === thinking || control === plus || control === tools) {
				menuOpen = !menuOpen;
			}
			if (control === option || control === childOption) menuOpen = false;
			updateExpanded();
		});
	}
	const module = { exports: {} };
	vm.runInNewContext(compiled, {
		module,
		exports: module.exports,
		Element,
		HTMLElement: Element,
		Date: { now: () => now }
	});
	const { installComposerControlInteractions } = module.exports;
	const originalListenerCount = document.listenerCount();
	let dispose = () => {};
	if (install)
		dispose = installComposerControlInteractions(form, {
			isMobile: () => mobile,
			onInteractionChange(active) {
				controlActive = active;
				lockChanges.push(active);
				if (
					!active &&
					!menuOpen &&
					document.activeElement !== editor &&
					document.activeElement !== search
				) {
					composerFocused = false;
				}
				updateExpanded();
			}
		});

	function dispatch(target, type, fields = {}) {
		const event = {
			target,
			type,
			isPrimary: true,
			button: 0,
			detail: 1,
			cancelable: true,
			defaultPrevented: false,
			propagationStopped: false,
			immediateStopped: false,
			preventDefault() {
				if (this.cancelable && !this.passive) this.defaultPrevented = true;
			},
			stopImmediatePropagation() {
				this.immediateStopped = true;
				this.propagationStopped = true;
			},
			...fields
		};
		document.dispatch(type, event, true);
		const path = [];
		for (let node = target; node; node = node.parentElement) path.push(node);
		for (const node of [...path].reverse()) {
			if (!event.propagationStopped) node.dispatch(type, event, true);
		}
		for (const node of path) {
			if (!event.propagationStopped) node.dispatch(type, event, false);
		}
		if (!event.propagationStopped) document.dispatch(type, event, false);
		if (type === 'click' && !event.defaultPrevented) {
			if (target.matches('input[type="checkbox"]')) {
				target.checked = !target.checked;
				dispatch(target, 'change');
			}
			if (target.matches('label') && target.control) target.control.click();
		}
		return event;
	}
	const point = (identifier = 1, x = 20, y = 20) => ({ identifier, clientX: x, clientY: y });
	function start(control, { pointer = true, touches = [point()], nativeBlur = false } = {}) {
		physicalTarget = control;
		if (pointer) dispatch(control, 'pointerdown', { pointerType: 'touch' });
		const event = dispatch(control, 'touchstart', { touches, changedTouches: touches });
		// Safari can blur to body without focusing its tapped button before native click synthesis.
		if (nativeBlur && !event.defaultPrevented) document.activeElement.blur();
		return event;
	}
	function end(control, { touch = point(), touches = [], synthesize = true } = {}) {
		const event = dispatch(control, 'touchend', { touches, changedTouches: [touch] });
		if (!event.defaultPrevented && synthesize) {
			const target = document.elementFromPoint(touch.clientX, touch.clientY);
			if (target !== form)
				dispatch(target, 'click', {
					pointerType: 'touch',
					sourceCapabilities: { firesTouchEvents: true }
				});
		}
		return event;
	}
	function flush() {
		while (timers.size) {
			const queued = [...timers.values()];
			timers.clear();
			for (const callback of queued) callback();
		}
	}
	return {
		document,
		form,
		toolbar,
		composer,
		model,
		thinking,
		editor,
		other,
		plus,
		tools,
		blank,
		disabled,
		disabledIcon,
		toggle,
		toggleIcon,
		row,
		inertToggle,
		checkbox,
		checkboxLabel,
		textInput,
		textLabel,
		select,
		fileInput,
		upload,
		scrollRow,
		scrollAction,
		scrollBlank,
		terminal,
		toolsToggle,
		disabledOption,
		menu,
		option,
		padding,
		search,
		childOption,
		outside,
		dialogInput,
		focusChanges,
		lockChanges,
		dispatch,
		point,
		start,
		end,
		flush,
		dispose,
		count: (control) => activations.get(control),
		setHit: (target) => {
			physicalTarget = target;
		},
		setMenuOpen: (value) => {
			menuOpen = value;
			updateExpanded();
		},
		advanceTime: (milliseconds) => {
			now += milliseconds;
		},
		expanded: () => expanded,
		active: () => controlActive,
		originalListenerCount
	};
}

test('fixture reproduces Safari blank-composer collapse and lost native click without helper', () => {
	const page = createPage({ install: false });
	page.start(page.model, { nativeBlur: true });
	assert.equal(page.document.activeElement, page.document.body);
	assert.equal(page.expanded(), false);
	page.end(page.model);
	assert.equal(page.count(page.model), 0);
});

for (const name of ['model', 'thinking', 'plus', 'tools']) {
	test(`Safari ${name} touchstart prevents blur and suppressed native click is activated exactly once`, () => {
		const page = createPage();
		const control = page[name];
		const start = page.start(control, { nativeBlur: true });
		assert.equal(start.defaultPrevented, true);
		assert.equal(page.document.activeElement, page.editor);
		assert.equal(page.expanded(), true);
		assert.equal(page.count(control), 0);
		const end = page.end(control);
		assert.equal(end.defaultPrevented, true);
		assert.equal(page.count(control), 1);
		assert.equal(page.document.activeElement, page.editor);
		assert.equal(page.active(), true, 'collapse lock survives the action until rendering settles');
		page.flush();
		assert.equal(page.active(), false);
		assert.equal(page.expanded(), true);
		assert.deepEqual(page.focusChanges, []);
	});
}

test('early pointer ownership recovers Safari blur before touchstart while preserving the row', () => {
	const page = createPage();
	page.dispatch(page.model, 'pointerdown', { pointerType: 'touch' });
	page.editor.blur();
	assert.equal(page.expanded(), true);
	page.start(page.model, { pointer: false });
	assert.equal(page.document.activeElement, page.editor);
	page.end(page.model);
	assert.equal(page.count(page.model), 1);
});

test('a duplicate compatibility mousedown/click is suppressed without a stuck collapse lock', () => {
	const page = createPage();
	page.start(page.model);
	page.end(page.model);
	const down = page.dispatch(page.model, 'mousedown', {
		sourceCapabilities: { firesTouchEvents: true }
	});
	assert.equal(down.defaultPrevented, true);
	const click = page.dispatch(page.model, 'click', { pointerType: 'touch' });
	assert.equal(click.defaultPrevented, true);
	assert.equal(page.count(page.model), 1);
	page.flush();
	assert.equal(page.active(), false);
});

test('duplicate guard permits detail-zero activation, other controls, later mouse clicks, and expiry', () => {
	const page = createPage();
	page.start(page.model);
	page.end(page.model);
	page.flush();
	page.model.click();
	assert.equal(page.count(page.model), 2);
	page.dispatch(page.thinking, 'click', { pointerType: 'touch' });
	assert.equal(page.count(page.thinking), 1);
	page.dispatch(page.model, 'pointerdown', { pointerType: 'mouse' });
	page.dispatch(page.model, 'click', { pointerType: 'mouse' });
	assert.equal(page.count(page.model), 3);
	page.start(page.model);
	page.end(page.model);
	page.flush();
	page.advanceTime(751);
	page.dispatch(page.model, 'click');
	assert.equal(page.count(page.model), 5);
});

for (const name of ['option', 'childOption', 'toolsToggle']) {
	test(`${name} permits list scrolling at start but cancels a tap end and preserves input focus`, () => {
		const page = createPage();
		page.setMenuOpen(true);
		const control = page[name];
		assert.equal(page.start(control).defaultPrevented, false);
		assert.equal(page.end(control).defaultPrevented, true);
		assert.equal(page.count(control), 1);
		assert.equal(page.document.activeElement, page.editor);
		page.flush();
		assert.equal(page.expanded(), true);
	});
}

test('portal padding keeps the keyboard on tap without activating an option', () => {
	const page = createPage();
	page.setMenuOpen(true);
	assert.equal(page.start(page.padding).defaultPrevented, false);
	assert.equal(page.end(page.padding).defaultPrevented, true);
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(page.count(page.option), 0);
});

test('movement over ten pixels leaves portal scrolling native and never selects an option', () => {
	const page = createPage();
	page.setMenuOpen(true);
	assert.equal(page.start(page.option).defaultPrevented, false);
	const move = page.dispatch(page.option, 'touchmove', { touches: [page.point(1, 20, 50)] });
	assert.equal(move.defaultPrevented, false);
	const end = page.end(page.option, { touch: page.point(1, 20, 50), synthesize: false });
	assert.equal(end.defaultPrevented, false);
	assert.equal(page.count(page.option), 0);
	page.flush();
	assert.equal(page.active(), false);
});

test('end movement and a different hit target never activate the captured button', () => {
	const page = createPage();
	page.start(page.model);
	page.end(page.model, { touch: page.point(1, 31, 20), synthesize: false });
	assert.equal(page.count(page.model), 0);
	page.start(page.model);
	page.setHit(page.thinking);
	page.end(page.model, { synthesize: false });
	assert.equal(page.count(page.model), 0);
});

test('canceled and multi-touch gestures never activate controls', () => {
	const page = createPage();
	page.start(page.model);
	page.dispatch(page.model, 'touchcancel');
	page.end(page.model, { synthesize: false });
	assert.equal(page.count(page.model), 0);
	assert.equal(page.active(), false);
	page.start(page.model);
	page.start(page.model, { pointer: false, touches: [page.point(), page.point(2)] });
	page.end(page.model, { synthesize: false });
	assert.equal(page.count(page.model), 0);
	assert.equal(page.active(), false);
});

test('search takes native focus and subsequent option tap retains that input through activation', () => {
	const page = createPage();
	page.setMenuOpen(true);
	assert.equal(page.start(page.search).defaultPrevented, false);
	page.search.focus();
	page.end(page.search);
	assert.equal(page.document.activeElement, page.search);
	page.start(page.option);
	page.end(page.option);
	assert.equal(page.count(page.option), 1);
	assert.equal(page.document.activeElement, page.search);
});

test('outside and form controls outside the Buddy composer remain native without forced return', () => {
	const page = createPage();
	page.start(page.model);
	assert.equal(page.start(page.outside, { pointer: false }).defaultPrevented, false);
	assert.equal(page.active(), false);
	page.editor.blur();
	page.end(page.model, { synthesize: false });
	assert.equal(page.document.activeElement, page.document.body);
	assert.equal(page.count(page.model), 0);
	page.editor.focus();
	assert.equal(page.start(page.other).defaultPrevented, false);
	assert.equal(page.dispatch(page.other, 'mousedown').defaultPrevented, false);
});

test('mousedown fallback cancels focus transfer and native click still activates once', () => {
	const page = createPage();
	assert.equal(page.dispatch(page.model, 'mousedown').defaultPrevented, true);
	page.dispatch(page.model, 'click', { pointerType: 'mouse' });
	assert.equal(page.count(page.model), 1);
	assert.equal(page.document.activeElement, page.editor);
	page.flush();
	assert.equal(page.active(), false);
});

test('intentional dialog focus from a menu action survives compatibility events and release', () => {
	const page = createPage();
	page.setMenuOpen(true);
	page.option.addEventListener('click', () => page.dialogInput.focus());
	page.start(page.option);
	page.end(page.option);
	assert.equal(page.document.activeElement, page.dialogInput);
	page.dispatch(page.option, 'mousedown', { sourceCapabilities: { firesTouchEvents: true } });
	page.dispatch(page.option, 'click', { pointerType: 'touch' });
	page.flush();
	assert.equal(page.count(page.option), 1);
	assert.equal(page.document.activeElement, page.dialogInput);
});

test('desktop events remain native and disposal removes every helper listener and pending lock', () => {
	const desktop = createPage({ mobile: false });
	assert.equal(desktop.start(desktop.model).defaultPrevented, false);
	assert.equal(desktop.dispatch(desktop.model, 'mousedown').defaultPrevented, false);
	desktop.end(desktop.model);
	assert.equal(desktop.count(desktop.model), 1);
	assert.deepEqual(desktop.lockChanges, []);
	const page = createPage();
	page.start(page.model);
	page.dispose();
	page.flush();
	assert.equal(page.active(), false);
	assert.equal(page.document.listenerCount(), page.originalListenerCount);
	assert.equal(page.start(page.model).defaultPrevented, false);
});

test('a mobile mouse drag released outside its control clears the lock without activating or restoring focus', () => {
	const page = createPage();
	assert.equal(page.dispatch(page.model, 'mousedown').defaultPrevented, true);
	assert.equal(page.active(), true);
	assert.equal(page.dispatch(page.outside, 'mouseup').defaultPrevented, false);
	page.flush();
	assert.equal(page.active(), false);
	assert.equal(page.count(page.model), 0);
	assert.equal(page.document.activeElement, page.editor);
	assert.deepEqual(page.focusChanges, []);
	page.dispatch(page.outside, 'pointerdown', { pointerType: 'mouse' });
	page.editor.blur();
	assert.equal(
		page.expanded(),
		false,
		'a later outside interaction can collapse the blank composer'
	);
});

test('empty composer space preserves draft focus without activating any action', () => {
	const page = createPage();
	assert.equal(page.start(page.blank, { nativeBlur: true }).defaultPrevented, true);
	assert.equal(page.end(page.blank).defaultPrevented, true);
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(page.count(page.plus), 0);
	assert.equal(page.count(page.tools), 0);
	page.flush();
	assert.equal(page.active(), false);
	assert.equal(page.expanded(), true);
});

for (const name of ['disabledIcon', 'disabledOption']) {
	test(`${name} preserves input focus without activating its disabled button`, () => {
		const page = createPage();
		const control = page[name];
		page.start(control, { nativeBlur: name === 'disabledIcon' });
		assert.equal(page.end(control).defaultPrevented, true);
		assert.equal(page.count(page.disabled), 0);
		assert.equal(page.count(page.disabledOption), 0);
		assert.equal(page.document.activeElement, page.editor);
	});
}

test('toggle wrappers and inert row decorations activate their actual owner exactly once', () => {
	const page = createPage();
	page.start(page.toggleIcon, { nativeBlur: true });
	page.end(page.toggleIcon);
	assert.equal(page.count(page.toggle), 1);
	page.flush();
	page.start(page.inertToggle, { nativeBlur: true });
	page.end(page.inertToggle);
	assert.equal(page.count(page.row), 1);
	assert.equal(page.count(page.inertToggle), 0);
	assert.equal(page.document.activeElement, page.editor);
});

test('native checkbox and its label retain native default toggle and change exactly once', () => {
	const page = createPage();
	let changes = 0;
	page.checkbox.addEventListener('change', () => changes++);
	page.start(page.checkbox, { nativeBlur: true });
	page.end(page.checkbox);
	assert.equal(page.count(page.checkbox), 1);
	assert.equal(page.checkbox.checked, true);
	assert.equal(changes, 1);
	page.dispatch(page.checkbox, 'click', { pointerType: 'touch' });
	assert.equal(page.checkbox.checked, true, 'compatibility click cannot toggle it twice');
	page.flush();
	page.start(page.checkboxLabel, { nativeBlur: true });
	page.end(page.checkboxLabel);
	assert.equal(page.count(page.checkboxLabel), 1);
	assert.equal(page.count(page.checkbox), 2);
	assert.equal(page.checkbox.checked, false);
	assert.equal(changes, 2);
	assert.equal(page.document.activeElement, page.editor);
});

test('native text input, its label, select, and file input keep deliberate native focus behavior', () => {
	for (const name of ['textInput', 'textLabel', 'select', 'fileInput']) {
		const page = createPage();
		const control = page[name];
		assert.equal(page.start(control).defaultPrevented, false, name);
		assert.equal(page.dispatch(control, 'mousedown').defaultPrevented, false, name);
		assert.equal(page.active(), false, name);
		page.textInput.focus();
		page.end(control);
		assert.equal(page.document.activeElement, page.textInput, name);
	}
});

test('composer text input is retained by a later owned control gesture', () => {
	const page = createPage();
	page.textInput.focus();
	page.start(page.plus, { nativeBlur: true });
	page.end(page.plus);
	assert.equal(page.document.activeElement, page.textInput);
	assert.equal(page.count(page.plus), 1);
});

test('scrollable inline actions allow swipes and still activate an unmoved tap exactly once', () => {
	const page = createPage();
	assert.equal(page.start(page.scrollAction).defaultPrevented, false);
	assert.equal(
		page.dispatch(page.scrollAction, 'touchmove', { touches: [page.point(1, 70, 20)] })
			.defaultPrevented,
		false
	);
	assert.equal(
		page.end(page.scrollAction, { touch: page.point(1, 70, 20), synthesize: false })
			.defaultPrevented,
		false
	);
	assert.equal(page.count(page.scrollAction), 0);
	page.flush();
	assert.equal(page.start(page.scrollAction).defaultPrevented, false);
	assert.equal(page.end(page.scrollAction).defaultPrevented, true);
	assert.equal(page.count(page.scrollAction), 1);
	assert.equal(page.document.activeElement, page.editor);
});

test('an owned upload action opens its native file input once and intentional focus survives release', () => {
	const page = createPage();
	page.upload.addEventListener('click', () => {
		page.fileInput.click();
		page.dialogInput.focus();
	});
	page.start(page.upload, { nativeBlur: true });
	page.end(page.upload);
	assert.equal(page.count(page.upload), 1);
	assert.equal(page.count(page.fileInput), 1);
	page.dispatch(page.upload, 'mousedown', { sourceCapabilities: { firesTouchEvents: true } });
	page.dispatch(page.upload, 'click', { pointerType: 'touch' });
	page.flush();
	assert.equal(page.count(page.upload), 1);
	assert.equal(page.count(page.fileInput), 1);
	assert.equal(page.document.activeElement, page.dialogInput);
});

test('all owned composer surfaces are untouched on desktop', () => {
	for (const name of [
		'plus',
		'tools',
		'blank',
		'disabledIcon',
		'toggleIcon',
		'checkbox',
		'scrollAction'
	]) {
		const page = createPage({ mobile: false });
		assert.equal(page.start(page[name]).defaultPrevented, false, name);
		assert.equal(page.dispatch(page[name], 'mousedown').defaultPrevented, false, name);
		assert.deepEqual(page.lockChanges, [], name);
	}
});

test('Safari portal swipe restores a searched input before scrolling begins without selecting', () => {
	const page = createPage();
	page.setMenuOpen(true);
	page.search.focus();
	page.dispatch(page.option, 'pointerdown', { pointerType: 'touch' });
	page.search.blur();
	assert.equal(page.document.activeElement, page.search, 'native blur is recovered immediately');
	assert.equal(page.active(), true, 'early pointer ownership holds the composer open');

	const start = page.start(page.option, { pointer: false });
	assert.equal(start.defaultPrevented, false, 'touchstart leaves list scrolling native');
	assert.equal(
		page.document.activeElement,
		page.search,
		'captured search focus is restored synchronously before any movement'
	);
	const move = page.dispatch(page.option, 'touchmove', { touches: [page.point(1, 20, 65)] });
	assert.equal(move.defaultPrevented, false);
	const end = page.end(page.option, { touch: page.point(1, 20, 65), synthesize: false });
	assert.equal(end.defaultPrevented, false, 'a swipe remains a native gesture');
	assert.equal(page.count(page.option), 0);
	page.flush();
	assert.equal(page.active(), false);
	assert.equal(page.document.activeElement, page.search);
	assert.equal(page.expanded(), true);
});

for (const name of ['plus', 'tools', 'model', 'thinking', 'terminal']) {
	test(`overflowing toolbar popup ${name} prevents Safari native-default blur and activates exactly once`, () => {
		const page = createPage();
		const control = page[name];
		if (name !== 'terminal') page.scrollRow.appendChild(control);
		assert.ok(page.scrollRow.scrollWidth > page.scrollRow.clientWidth);

		const start = page.start(control, { nativeBlur: true });
		assert.equal(
			start.defaultPrevented,
			true,
			'the popup button cancels the focus-changing default action'
		);
		assert.equal(page.document.activeElement, page.editor);
		assert.deepEqual(page.focusChanges, [], 'keyboard focus never leaves the editor');
		assert.equal(page.end(control).defaultPrevented, true);
		assert.equal(page.count(control), 1);
		page.dispatch(control, 'click', { pointerType: 'touch' });
		assert.equal(page.count(control), 1, 'a compatibility click cannot activate the popup twice');
		page.flush();
		assert.equal(page.active(), false);
		assert.equal(page.document.activeElement, page.editor);
	});
}

test('moved and canceled overflowing popup gestures retain focus and never activate', () => {
	const page = createPage();
	page.scrollRow.appendChild(page.tools);
	assert.equal(page.start(page.tools, { nativeBlur: true }).defaultPrevented, true);
	page.dispatch(page.tools, 'touchmove', { touches: [page.point(1, 65, 20)] });
	page.end(page.tools, { touch: page.point(1, 65, 20), synthesize: false });
	page.flush();
	assert.equal(page.count(page.tools), 0);
	assert.equal(page.active(), false);
	assert.equal(page.document.activeElement, page.editor);

	page.start(page.tools, { nativeBlur: true });
	page.dispatch(page.tools, 'touchcancel');
	page.end(page.tools, { synthesize: false });
	page.flush();
	assert.equal(page.count(page.tools), 0);
	assert.equal(page.active(), false);
	assert.deepEqual(page.focusChanges, []);
});

test('overflowing toolbar background and portaled submenu controls preserve native scrolling', () => {
	const page = createPage();
	assert.equal(page.start(page.scrollBlank).defaultPrevented, false);
	assert.equal(
		page.dispatch(page.scrollBlank, 'touchmove', { touches: [page.point(1, 65, 20)] })
			.defaultPrevented,
		false
	);
	assert.equal(
		page.end(page.scrollBlank, { touch: page.point(1, 65, 20), synthesize: false })
			.defaultPrevented,
		false
	);
	page.flush();
	assert.equal(page.count(page.tools), 0);
	assert.equal(page.active(), false);

	page.option.setAttribute('aria-haspopup', 'menu');
	assert.equal(
		page.start(page.option).defaultPrevented,
		false,
		'portaled submenu rows remain scrollable'
	);
	page.dispatch(page.option, 'touchmove', { touches: [page.point(1, 20, 65)] });
	assert.equal(
		page.end(page.option, { touch: page.point(1, 20, 65), synthesize: false }).defaultPrevented,
		false
	);
	page.flush();
	assert.equal(page.count(page.option), 0);
	assert.equal(page.active(), false);
	assert.equal(page.document.activeElement, page.editor);
});

function replayKeyboardViewportShiftOnBlur(page) {
	const viewport = page.document.defaultView.visualViewport;
	const phases = [];
	viewport.addEventListener('resize', () => {
		phases.push('keyboard viewport expands');
		page.setHit(page.childOption);
	});
	viewport.addEventListener('scroll', () => {
		phases.push('viewport pan moves the portal');
	});
	page.document.addEventListener('focusout', (event) => {
		if (event.target !== page.editor || page.document.activeElement === page.editor) return;
		phases.push('native default blur loses editor');
		viewport.height = 600;
		viewport.offsetTop = 12;
		viewport.dispatch('resize', { type: 'resize' }, false);
		viewport.dispatch('scroll', { type: 'scroll' }, false);
	});
	return phases;
}

test('portal native-default blur is recovered before keyboard viewport changes can retarget the tap', () => {
	const page = createPage();
	page.setMenuOpen(true);
	const phases = replayKeyboardViewportShiftOnBlur(page);
	const start = page.start(page.option, { nativeBlur: true });
	assert.equal(start.defaultPrevented, false, 'list scrolling remains native');
	assert.equal(page.document.activeElement, page.editor, 'focus is recovered before touchend');
	assert.deepEqual(phases, [], 'viewport expansion and portal retargeting never start');
	assert.equal(page.end(page.option).defaultPrevented, true);
	assert.equal(page.count(page.option), 1);
	assert.equal(page.count(page.childOption), 0);
	page.flush();
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(page.active(), false);
});

test('portal native-default blur recovery survives a native swipe and cancellation without selection', () => {
	const page = createPage();
	page.setMenuOpen(true);
	const phases = replayKeyboardViewportShiftOnBlur(page);
	assert.equal(page.start(page.option, { nativeBlur: true }).defaultPrevented, false);
	assert.equal(page.document.activeElement, page.editor);
	assert.equal(
		page.dispatch(page.option, 'touchmove', { touches: [page.point(1, 20, 65)] }).defaultPrevented,
		false
	);
	page.dispatch(page.option, 'pointercancel', { pointerType: 'touch' });
	page.dispatch(page.option, 'touchcancel');
	page.end(page.option, { touch: page.point(1, 20, 65), synthesize: false });
	page.flush();
	assert.deepEqual(phases, []);
	assert.equal(page.count(page.option), 0);
	assert.equal(page.active(), false);
	assert.equal(page.document.activeElement, page.editor);
	page.editor.blur();
	assert.equal(
		page.document.activeElement,
		page.document.body,
		'cancellation releases focus ownership'
	);
});

test('an ongoing option gesture yields to deliberate search, file, and dialog focus', () => {
	for (const name of ['search', 'fileInput', 'dialogInput']) {
		const page = createPage();
		page.setMenuOpen(true);
		page.start(page.option);
		page[name].focus();
		assert.equal(page.active(), false, name);
		page.end(page.option, { synthesize: false });
		page.flush();
		assert.equal(page.document.activeElement, page[name], name);
		assert.equal(page.count(page.option), 0, 'the abandoned gesture cannot steal new input focus');
	}
});

test('owned checkbox focus during its touch gesture retains the editor and native toggle', () => {
	const page = createPage();
	page.start(page.checkbox);
	page.checkbox.focus();
	assert.equal(page.document.activeElement === page.editor, true);
	assert.equal(page.active(), true, 'an action input does not yield keyboard ownership');
	assert.equal(page.end(page.checkbox).defaultPrevented, true);
	assert.equal(page.count(page.checkbox), 1);
	assert.equal(page.checkbox.checked, true);
	page.flush();
	assert.equal(page.active(), false);
	assert.equal(page.document.activeElement === page.editor, true);
});
