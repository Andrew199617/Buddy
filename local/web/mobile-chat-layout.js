/*
 * Keep mobile replies visible above the composer and inside the keyboard viewport.
 * Loaded through /static/loader.js by local/owui_local_patches.py.
 * iOS resizes and pans visualViewport while leaving 100vh/100dvh unchanged.
 */
(function () {
	'use strict';

	if (typeof window === 'undefined' || window.__owuiMobileChatLayout) return;
	if (!window.visualViewport) return;
	window.__owuiMobileChatLayout = true;

	const viewport = window.visualViewport;
	const root = document.documentElement;
	const touchScreen = window.matchMedia('(pointer: coarse)');
	const css = `
html.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)), html.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) body {
	overflow: hidden;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) .app {
	position: fixed;
	top: var(--owui-chat-viewport-top);
	left: 0;
	right: 0;
	height: var(--owui-chat-viewport-height);
	overflow: hidden;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) .app > div,
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #chat-container {
	height: 100% !important;
	max-height: 100% !important;
	min-height: 0;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) .app > div {
	overflow: hidden;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #chat-pane,
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #messages-container {
	min-height: 0;
	overscroll-behavior-y: contain;
}
/* Keep one conversation scroll surface and give its content real bounds.
   Fixed-height wrappers with overflowing replies can paint blank on iOS. */
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #chat-pane:has(#messages-container [role="log"]) {
	overflow: hidden;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #messages-container:has([role="log"]) > .h-full,
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #messages-container:has([role="log"]) > .h-full > .h-full {
	height: auto !important;
	min-height: 0;
	flex: none;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #messages-container section:has(> [role="log"]) + .pb-18 {
	padding-bottom: var(--owui-chat-tail-gap) !important;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #chat-input-container {
	max-height: min(24rem, calc(var(--owui-chat-viewport-height) * 0.4), var(--owui-chat-editor-limit, 24rem));
	overflow-y: auto;
	overscroll-behavior-y: contain;
}
.owui-mobile-chat:not(:has(.buddy-chat, .buddy-shell)) #chat-input {
	/* Prevent iOS focus zoom from pushing the conversation off-screen. */
	font-size: max(1rem, 16px) !important;
}
/* Keep full response details tappable without spending two lines on their summary. */
.owui-chat-compact:not(:has(.buddy-chat, .buddy-shell)) .owui-run-footer {
	flex-wrap: nowrap;
	margin-block: 0;
}
.owui-chat-compact:not(:has(.buddy-chat, .buddy-shell)) .owui-run-footer .owui-run-model {
	flex: 1 1 auto;
}
.owui-chat-compact:not(:has(.buddy-chat, .buddy-shell)) .owui-run-footer time {
	flex: none;
}
.owui-chat-compact:not(:has(.buddy-chat, .buddy-shell)) .owui-run-footer .owui-run-count {
	flex: 0 1 auto;
	min-width: 0;
	max-width: 55%;
	overflow: hidden;
	text-overflow: ellipsis;
}
.owui-mobile-ui:not(:has(.buddy-chat, .buddy-shell)).owui-chat-compact:not(:has(.buddy-chat, .buddy-shell)) .owui-toolbar-trailing > div:first-child {
	max-width: clamp(48px, calc(100vw - 280px), 160px);
}
.owui-chat-compact:not(:has(.buddy-chat, .buddy-shell)) #chat-pane > .flex.items-center.h-full > div {
	padding-top: 1rem;
	padding-bottom: 1rem;
	transform: none;
}`;

	let pendingFrame = null;
	let layoutActive = false;
	let messagesElement = null;
	let messagesHeight = 0;
	let messagesScrollHeight = 0;
	let messagesScrollTop = 0;
	let followingLatest = false;
	let messagesObserver = null;
	if (typeof ResizeObserver === 'function') {
		messagesObserver = new ResizeObserver(scheduleLayout);
	}

	function rememberMessagesGeometry() {
		if (!messagesElement) return;
		messagesHeight = messagesElement.clientHeight;
		messagesScrollHeight = messagesElement.scrollHeight;
		messagesScrollTop = messagesElement.scrollTop;
	}

	function onMessagesScroll() {
		if (!messagesElement) return;
		const height = messagesElement.clientHeight;
		const scrollHeight = messagesElement.scrollHeight;
		const scrollTop = messagesElement.scrollTop;
		const atBottom = scrollHeight - scrollTop - height <= 8;
		const sizeChanged = height !== messagesHeight || scrollHeight !== messagesScrollHeight;
		if (sizeChanged) {
			// Content/keyboard resizing can emit scroll before the next layout frame.
			// Keep following unless the user moved toward earlier messages.
			const movedEarlier = height <= messagesHeight &&
				scrollHeight >= messagesScrollHeight && scrollTop < messagesScrollTop - 8;
			if (atBottom) followingLatest = true;
			else if (movedEarlier) followingLatest = false;
			scheduleLayout();
			return;
		}
		followingLatest = atBottom;
		messagesScrollTop = scrollTop;
	}

	function watchMessages(messages) {
		if (messages === messagesElement) return;
		if (messagesElement) messagesElement.removeEventListener('scroll', onMessagesScroll);
		if (messagesObserver) messagesObserver.disconnect();
		messagesElement = messages;
		messagesHeight = 0;
		messagesScrollHeight = 0;
		messagesScrollTop = 0;
		followingLatest = false;
		if (!messages) return;
		rememberMessagesGeometry();
		followingLatest = messagesScrollHeight - messagesScrollTop - messagesHeight <= 8;
		messages.addEventListener('scroll', onMessagesScroll, { passive: true });
		if (messagesObserver) {
			messagesObserver.observe(messages);
			if (messages.firstElementChild) messagesObserver.observe(messages.firstElementChild);
		}
	}

	function clearLayout() {
		if (!layoutActive) return;
		layoutActive = false;
		watchMessages(null);
		root.classList.remove('owui-mobile-chat', 'owui-chat-compact');
		root.style.removeProperty('--owui-chat-viewport-height');
		root.style.removeProperty('--owui-chat-viewport-top');
		root.style.removeProperty('--owui-chat-tail-gap');
		root.style.removeProperty('--owui-chat-editor-limit');
	}

	function updateLayout() {
		pendingFrame = null;
		// Buddy's native shell owns keyboard sizing, composer clearance, and scrolling.
		if (document.querySelector('.buddy-chat, .buddy-shell')) {
			clearLayout();
			return;
		}
		const chat = document.getElementById('chat-container');
		if (!chat) {
			clearLayout();
			return;
		}
		const mobile = window.innerWidth < 768 || touchScreen.matches;
		if (!mobile || viewport.scale > 1.05 || viewport.height <= 0) {
			clearLayout();
			return;
		}

		layoutActive = true;
		const messages = chat.querySelector('#messages-container');
		watchMessages(messages);

		root.style.setProperty('--owui-chat-viewport-height', `${viewport.height}px`);
		root.style.setProperty('--owui-chat-viewport-top', `${viewport.offsetTop}px`);
		root.classList.add('owui-mobile-chat');
		root.classList.toggle('owui-chat-compact', viewport.height < 500);

		// Preserve the latest message through keyboard and composer resizing.
		// Reading earlier messages keeps the user's existing scroll position.
		if (messages) {
			const editor = chat.querySelector('#chat-input-container');
			if (viewport.height < 500 && editor && editor !== messages) {
				// Reserve room for the reply, actions, and its details when a draft grows.
				// The draft itself remains scrollable inside the composer.
				const readingHeight = Math.min(240, Math.max(144, viewport.height * 0.45));
				const availableHeight = messages.clientHeight + editor.clientHeight - readingHeight;
				const editorLimit = Math.max(48, Math.min(viewport.height * 0.4, availableHeight));
				root.style.setProperty('--owui-chat-editor-limit', `${editorLimit}px`);
			} else {
				root.style.removeProperty('--owui-chat-editor-limit');
			}
			// Leave a little more room below full-screen replies; reclaim space when
			// the keyboard or a long draft leaves only a small conversation viewport.
			let tailGap = 16;
			if (viewport.height >= 500) {
				tailGap = Math.min(96, Math.max(16, Math.round(messages.clientHeight * 0.15)));
			}
			root.style.setProperty('--owui-chat-tail-gap', `${tailGap}px`);
			if (followingLatest) messages.scrollTop = messages.scrollHeight;
			rememberMessagesGeometry();
		}
	}

	function scheduleLayout() {
		if (pendingFrame !== null) return;
		pendingFrame = window.requestAnimationFrame(updateLayout);
	}

	function start() {
		const style = document.createElement('style');
		style.id = 'owui-mobile-chat-layout-style';
		style.textContent = css;
		document.head.appendChild(style);

		viewport.addEventListener('resize', scheduleLayout);
		viewport.addEventListener('scroll', scheduleLayout);
		window.addEventListener('resize', scheduleLayout);
		window.addEventListener('pageshow', scheduleLayout);
		document.addEventListener('focusin', scheduleLayout);
		document.addEventListener('focusout', scheduleLayout);

		const observer = new MutationObserver(scheduleLayout);
		observer.observe(document.body, { childList: true, subtree: true, characterData: true });
		updateLayout();
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', start, { once: true });
	} else {
		start();
	}
})();
