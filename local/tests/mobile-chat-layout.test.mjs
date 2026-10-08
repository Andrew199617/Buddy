import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const script = readFileSync(new URL('../web/mobile-chat-layout.js', import.meta.url), 'utf8');

function eventTarget() {
	const listeners = new Map();
	return {
		addEventListener(name, callback) {
			if (!listeners.has(name)) listeners.set(name, []);
			listeners.get(name).push(callback);
		},
		removeEventListener(name, callback) {
			const callbacks = listeners.get(name) || [];
			listeners.set(
				name,
				callbacks.filter((registered) => registered !== callback)
			);
		},
		listenerCount(name) {
			return (listeners.get(name) || []).length;
		},
		emit(name) {
			for (const callback of listeners.get(name) || []) callback();
		}
	};
}

function createPage({
	width = 390,
	touch = true,
	readyState = 'complete',
	buddyLayout = false,
	chatPresent = true
} = {}) {
	const properties = new Map();
	const classes = new Set();
	const measurements = { viewportReads: 0, rootWrites: 0 };
	const root = {
		style: {
			setProperty(name, value) {
				measurements.rootWrites++;
				properties.set(name, value);
			},
			removeProperty(name) {
				measurements.rootWrites++;
				properties.delete(name);
			}
		},
		classList: {
			add(...names) {
				measurements.rootWrites++;
				for (const name of names) classes.add(name);
			},
			remove(...names) {
				measurements.rootWrites++;
				for (const name of names) classes.delete(name);
			},
			toggle(name, enabled) {
				measurements.rootWrites++;
				if (enabled) classes.add(name);
				else classes.delete(name);
			}
		}
	};
	let scrollTop = 0;
	let composerHeight = 100;
	const messages = {
		scrollHeight: 2000,
		get clientHeight() {
			return parseFloat(properties.get('--owui-chat-viewport-height') || '844') - composerHeight;
		},
		get scrollTop() {
			return scrollTop;
		},
		set scrollTop(value) {
			scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight));
		}
	};
	Object.assign(messages, eventTarget());
	messages.scrollTop = messages.scrollHeight;
	const chat = { querySelector: () => messages };
	const styles = [];
	const document = Object.assign(eventTarget(), {
		readyState,
		documentElement: root,
		body: {},
		head: { appendChild: (style) => styles.push(style) },
		createElement: () => ({}),
		chat: chatPresent ? chat : null,
		buddyLayout: buddyLayout ? {} : null,
		querySelector() {
			return this.buddyLayout;
		},
		getElementById() {
			return this.chat;
		}
	});
	const viewport = Object.assign(eventTarget(), { height: 844, offsetTop: 0, scale: 1 });
	const frames = [];
	const window = {
		...eventTarget(),
		get innerWidth() {
			measurements.viewportReads++;
			return width;
		},
		visualViewport: viewport,
		matchMedia: () => ({ matches: touch }),
		requestAnimationFrame(callback) {
			frames.push(callback);
			return frames.length;
		}
	};
	let onMutation;
	class MutationObserver {
		constructor(callback) {
			onMutation = callback;
		}
		observe() {}
	}
	const context = vm.createContext({ window, document, MutationObserver });
	vm.runInContext(script, context);
	return {
		window,
		measurements,
		document,
		viewport,
		messages,
		properties,
		classes,
		styles,
		frames,
		mutate() {
			onMutation();
		},
		growComposer(height) {
			composerHeight = height;
			onMutation();
		},
		flush() {
			while (frames.length) frames.shift()();
		},
		reload() {
			vm.runInContext(script, context);
		}
	};
}

test('keyboard resize and viewport panning keep the latest message visible', () => {
	const page = createPage();
	page.viewport.height = 420;
	page.viewport.offsetTop = 160;
	page.viewport.emit('resize');
	page.viewport.emit('scroll');
	assert.equal(page.frames.length, 1, 'coalesce animation events');
	page.flush();
	assert.equal(page.properties.get('--owui-chat-viewport-height'), '420px');
	assert.equal(page.properties.get('--owui-chat-viewport-top'), '160px');
	assert.ok(page.classes.has('owui-chat-compact'));
	assert.equal(page.messages.scrollTop + page.messages.clientHeight, page.messages.scrollHeight);

	page.viewport.offsetTop = 200;
	page.viewport.emit('scroll');
	page.flush();
	assert.equal(page.properties.get('--owui-chat-viewport-top'), '200px');
});

test('keyboard closing restores the full height and keeps following the last message', () => {
	const page = createPage();
	page.viewport.height = 420;
	page.viewport.emit('resize');
	page.flush();
	page.viewport.height = 844;
	page.viewport.offsetTop = 0;
	page.viewport.emit('resize');
	page.flush();
	assert.equal(page.properties.get('--owui-chat-viewport-height'), '844px');
	assert.ok(!page.classes.has('owui-chat-compact'));
	assert.equal(page.messages.scrollTop + page.messages.clientHeight, page.messages.scrollHeight);
});

test('reading older messages preserves the scroll position when the keyboard opens', () => {
	const page = createPage();
	page.messages.scrollTop = 500;
	page.messages.emit('scroll');
	page.viewport.height = 420;
	page.viewport.emit('resize');
	page.flush();
	assert.equal(page.messages.scrollTop, 500);
});

test('touch phones in landscape still use the visible height', () => {
	const page = createPage({ width: 844 });
	page.viewport.height = 210;
	page.window.emit('resize');
	page.flush();
	assert.ok(page.classes.has('owui-mobile-chat'));
	assert.equal(page.properties.get('--owui-chat-viewport-height'), '210px');
});

test('desktop and pinch zoom use the normal page layout', () => {
	const desktop = createPage({ width: 1280, touch: false });
	assert.ok(!desktop.classes.has('owui-mobile-chat'));
	assert.equal(desktop.properties.size, 0);

	const phone = createPage();
	phone.viewport.scale = 2;
	phone.viewport.emit('resize');
	phone.flush();
	assert.ok(!phone.classes.has('owui-mobile-chat'));
	assert.equal(phone.properties.size, 0);
});

test('client navigation clears the chat layout and restores it on return', () => {
	const page = createPage();
	const chat = page.document.chat;
	page.document.chat = null;
	page.mutate();
	page.flush();
	assert.equal(page.classes.size, 0);
	assert.equal(page.properties.size, 0);
	page.document.chat = chat;
	page.mutate();
	page.flush();
	assert.ok(page.classes.has('owui-mobile-chat'));
});

test('waits for DOM readiness and duplicate loader execution does not add another style', () => {
	const page = createPage({ readyState: 'loading' });
	assert.equal(page.styles.length, 0);
	page.document.emit('DOMContentLoaded');
	assert.equal(page.styles.length, 1);
	page.reload();
	assert.equal(page.styles.length, 1);
});

test('an expanding draft keeps the latest message visible after the keyboard opens', () => {
	const page = createPage();
	page.viewport.height = 420;
	page.viewport.emit('resize');
	page.flush();
	page.growComposer(220);
	page.messages.emit('scroll');
	page.flush();
	assert.equal(page.messages.scrollTop + page.messages.clientHeight, page.messages.scrollHeight);
});

test('a footer or streamed reply growth keeps the latest scroll position', () => {
	const page = createPage();
	page.messages.scrollHeight += 64;
	page.messages.emit('scroll');
	page.mutate();
	page.flush();
	assert.equal(page.messages.scrollTop + page.messages.clientHeight, page.messages.scrollHeight);
});

test('content growth does not move a reader of earlier messages', () => {
	const page = createPage();
	page.messages.scrollTop = 500;
	page.messages.emit('scroll');
	page.messages.scrollHeight += 64;
	page.messages.emit('scroll');
	page.mutate();
	page.flush();
	assert.equal(page.messages.scrollTop, 500);
});

test('scrolling toward earlier messages during growth releases following', () => {
	const page = createPage();
	const earlier = page.messages.scrollTop - 100;
	page.messages.scrollHeight += 64;
	page.messages.scrollTop = earlier;
	page.messages.emit('scroll');
	page.mutate();
	page.flush();
	assert.equal(page.messages.scrollTop, earlier);
});

test('reply clearance shrinks to leave reading space above a keyboard draft', () => {
	const page = createPage();
	assert.equal(page.properties.get('--owui-chat-tail-gap'), '96px');
	page.viewport.height = 420;
	page.growComposer(320);
	page.viewport.emit('resize');
	page.flush();
	assert.equal(page.properties.get('--owui-chat-tail-gap'), '16px');
});

test('scrolling toward earlier messages during composer resizing releases following', () => {
	const page = createPage();
	const earlier = page.messages.scrollTop - 100;
	page.growComposer(200);
	page.messages.scrollTop = earlier;
	page.messages.emit('scroll');
	page.flush();
	assert.equal(page.messages.scrollTop, earlier);
});

test('Buddy mounting releases legacy viewport styles and returning restores legacy layout', () => {
	const page = createPage();
	assert.ok(page.classes.has('owui-mobile-chat'));
	assert.ok(page.properties.has('--owui-chat-viewport-height'));

	page.document.buddyLayout = {};
	page.mutate();
	page.flush();
	assert.equal(page.classes.size, 0);
	assert.equal(page.properties.size, 0);

	page.messages.scrollTop = 500;
	page.viewport.height = 420;
	page.viewport.offsetTop = 160;
	page.viewport.emit('resize');
	page.flush();
	assert.equal(page.classes.size, 0, 'native Buddy retains control during keyboard resizing');
	assert.equal(page.properties.size, 0);
	assert.equal(page.messages.scrollTop, 500, 'legacy patch does not move Buddy messages');

	page.document.buddyLayout = null;
	page.mutate();
	page.flush();
	assert.ok(page.classes.has('owui-mobile-chat'));
	assert.ok(page.classes.has('owui-chat-compact'));
	assert.equal(page.properties.get('--owui-chat-viewport-height'), '420px');
	assert.equal(page.properties.get('--owui-chat-viewport-top'), '160px');
});

test('Buddy startup and repeated mutations never read the viewport or rewrite legacy root styles', () => {
	const page = createPage({ buddyLayout: true });
	assert.deepEqual(page.measurements, { viewportReads: 0, rootWrites: 0 });
	for (let update = 0; update < 10; update++) {
		page.mutate();
		page.document.emit('focusin');
		page.viewport.emit('resize');
		page.flush();
	}
	assert.deepEqual(page.measurements, { viewportReads: 0, rootWrites: 0 });
	assert.equal(page.messages.listenerCount('scroll'), 0);
});

test('an absent chat leaves viewport and root styles untouched until legacy chat returns', () => {
	const page = createPage({ chatPresent: false });
	for (let update = 0; update < 10; update++) {
		page.mutate();
		page.flush();
	}
	assert.deepEqual(page.measurements, { viewportReads: 0, rootWrites: 0 });
	page.document.chat = { querySelector: () => page.messages };
	page.mutate();
	page.flush();
	assert.ok(page.classes.has('owui-mobile-chat'));
	assert.equal(page.messages.listenerCount('scroll'), 1);
});

test('legacy to Buddy performs one cleanup and restores keyboard behavior after returning', () => {
	const page = createPage();
	const writesBeforeBuddy = page.measurements.rootWrites;
	const readsBeforeBuddy = page.measurements.viewportReads;
	assert.equal(page.messages.listenerCount('scroll'), 1);
	page.document.buddyLayout = {};
	page.mutate();
	page.flush();
	assert.equal(page.measurements.rootWrites - writesBeforeBuddy, 5);
	assert.equal(page.measurements.viewportReads, readsBeforeBuddy);
	assert.equal(page.messages.listenerCount('scroll'), 0);
	const writesAfterCleanup = page.measurements.rootWrites;
	for (let update = 0; update < 10; update++) {
		page.mutate();
		page.document.emit('focusin');
		page.flush();
	}
	assert.equal(page.measurements.rootWrites, writesAfterCleanup);
	assert.equal(page.measurements.viewportReads, readsBeforeBuddy);

	page.document.buddyLayout = null;
	page.viewport.height = 420;
	page.viewport.offsetTop = 160;
	page.mutate();
	page.flush();
	assert.equal(page.messages.listenerCount('scroll'), 1);
	assert.ok(page.classes.has('owui-chat-compact'));
	assert.equal(page.properties.get('--owui-chat-viewport-height'), '420px');
	assert.equal(page.properties.get('--owui-chat-viewport-top'), '160px');
});

test('pinch zoom performs one cleanup and normal scale reactivates legacy tracking', () => {
	const page = createPage();
	page.viewport.scale = 2;
	page.viewport.emit('resize');
	page.flush();
	const writesAfterCleanup = page.measurements.rootWrites;
	page.mutate();
	page.flush();
	assert.equal(page.measurements.rootWrites, writesAfterCleanup);
	assert.equal(page.messages.listenerCount('scroll'), 0);
	page.viewport.scale = 1;
	page.viewport.emit('resize');
	page.flush();
	assert.ok(page.classes.has('owui-mobile-chat'));
	assert.equal(page.messages.listenerCount('scroll'), 1);
});
