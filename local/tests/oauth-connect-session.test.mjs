import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const sourceUrl = new URL('../../src/lib/apis/configs/index.ts', import.meta.url);
const sessionKey = 'buddyOAuthConnectSession';
const cancellationKey = 'buddyOAuthCancellation';
const tool = {
	id: 'tool-offline-slack',
	name: 'Slack',
	serverId: 'offline-slack',
	authType: 'mcp'
};
const origin = 'http://100.122.80.32:8082';

function memoryStorage() {
	const values = new Map();
	return {
		getItem(key) {
			return values.get(key) ?? null;
		},
		setItem(key, value) {
			values.set(key, String(value));
		},
		removeItem(key) {
			values.delete(key);
		}
	};
}

function writableOverlay() {
	let value = false;
	return {
		get value() {
			return value;
		},
		set(nextValue) {
			value = nextValue;
		},
		subscribe(subscriber) {
			subscriber(value);
			return () => {};
		}
	};
}

function createOfflineSession(path = '/c/offline-chat?view=chat#latest') {
	const sessionStorage = memoryStorage();
	const showOAuthConnect = writableOverlay();
	const localStorage = memoryStorage();
	const navigations = [];
	const opened = [];
	const fetched = [];
	let fetchHandler = () => {
		throw new Error('An offline OAuth session test attempted an unexpected network request');
	};
	let now = 1800000000000;
	const window = { location: new URL(path, origin), sessionStorage, localStorage };
	window.setTimeout = setTimeout;
	window.clearTimeout = clearTimeout;
	window.open = (url, target, features) => {
		opened.push({
			url,
			target,
			features,
			sessionAtOpen: JSON.parse(sessionStorage.getItem(sessionKey))
		});
		return null;
	};
	const goto = async (url, options) => {
		navigations.push({ url, options });
		window.location = new URL(url, origin);
	};
	class FixtureDate extends Date {
		static now() {
			return now;
		}
	}
	const compiled = ts.transpileModule(readFileSync(sourceUrl, 'utf8'), {
		compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
		fileName: sourceUrl.pathname
	});
	const module = { exports: {} };
	const dependencies = {
		'$app/navigation': { goto },
		'$lib/stores/oauth-connect': { showOAuthConnect },
		'$lib/constants': { WEBUI_BASE_URL: '', WEBUI_API_BASE_URL: '/api/v1' }
	};
	const sandbox = {
		module,
		exports: module.exports,
		window,
		sessionStorage,
		localStorage,
		URL,
		URLSearchParams,
		Date: FixtureDate,
		console,
		AbortController,
		setTimeout,
		clearTimeout,
		fetch(url, options) {
			fetched.push({ url, options });
			return fetchHandler(url, options);
		},
		require(specifier) {
			if (!Object.hasOwn(dependencies, specifier)) {
				throw new Error('Unexpected dependency in offline OAuth session test: ' + specifier);
			}
			return dependencies[specifier];
		}
	};
	vm.runInNewContext(compiled.outputText, sandbox, { filename: sourceUrl.pathname });
	return {
		helpers: module.exports,
		showOAuthConnect,
		window,
		sessionStorage,
		localStorage,
		navigations,
		opened,
		fetched,
		setFetchHandler(handler) {
			fetchHandler = handler;
		},
		navigate(path) {
			window.location = new URL(path, origin);
		},
		advanceTime(milliseconds) {
			now += milliseconds;
		}
	};
}

function storedSession(fixture) {
	return JSON.parse(fixture.sessionStorage.getItem(sessionKey));
}

function writeSession(fixture, changes) {
	const session = { ...storedSession(fixture), ...changes };
	fixture.sessionStorage.setItem(sessionKey, JSON.stringify(session));
}

async function startSession(fixture) {
	await fixture.helpers.initiateOAuthRedirect(tool);
}

test('a tool connection opens the overlay without navigating and remembers the exact chat route', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.fetched.length, 0);
	assert.equal(fixture.showOAuthConnect.value, true);
	assert.equal(fixture.window.location.pathname, '/c/offline-chat');
	assert.equal(fixture.window.location.search, '?view=chat');
	assert.equal(fixture.window.location.hash, '#latest');
	assert.equal(fixture.opened.length, 0);
	assert.equal(storedSession(fixture).returnPath, '/c/offline-chat?view=chat#latest');
	assert.equal(storedSession(fixture).authorizePath, '/oauth/clients/mcp:offline-slack/authorize');
	assert.equal(storedSession(fixture).started, false);
	assert.equal(fixture.sessionStorage.getItem('pendingOAuthToolId'), null);
});

test('Continue synchronously marks the session before navigating in the same browser window', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	const continued = fixture.helpers.continueOAuthConnect();
	assert.equal(continued, true);
	assert.equal(fixture.opened.length, 1);
	assert.equal(fixture.opened[0].sessionAtOpen.started, true);
	assert.equal(fixture.opened[0].url, '/oauth/clients/mcp:offline-slack/authorize');
	assert.equal(fixture.opened[0].target, '_self');
	assert.equal(fixture.opened[0].features, 'noopener');
	assert.equal(fixture.sessionStorage.getItem('pendingOAuthToolId'), tool.id);
	assert.equal(fixture.sessionStorage.getItem('oauthRedirectInProgressToolId'), tool.id);
});

test('Exit closes the overlay without remounting the chat and suppresses one automatic retry', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.sessionStorage.getItem(sessionKey), null);
	assert.equal(fixture.sessionStorage.getItem('pendingOAuthToolId'), null);
	assert.equal(fixture.sessionStorage.getItem('oauthRedirectInProgressToolId'), null);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
	assert.equal(fixture.helpers.consumeOAuthCancellation(), tool.id);
	assert.equal(fixture.helpers.consumeOAuthCancellation(), null);
});

test('a successful root callback returns to the prior route and retains native tool selection', async () => {
	const fixture = createOfflineSession('/c/another-chat?folder=work#draft');
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	fixture.navigate('/');
	assert.equal(await fixture.helpers.completeOAuthConnectRedirect(), 'success');
	assert.equal(fixture.navigations.at(-1).url, '/c/another-chat?folder=work#draft');
	assert.equal(fixture.sessionStorage.getItem(sessionKey), null);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.sessionStorage.getItem('pendingOAuthToolId'), tool.id);
	assert.equal(fixture.helpers.consumeOAuthCancellation(), null);
	assert.equal(await fixture.helpers.completeOAuthConnectRedirect(), null);
});

test('a denied root callback returns to the chat without enabling the tool or retriggering sign-in', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	fixture.navigate('/?error=offline-denied');
	assert.equal(await fixture.helpers.completeOAuthConnectRedirect(), 'error');
	assert.equal(fixture.navigations.at(-1).url, '/c/offline-chat?view=chat#latest');
	assert.equal(fixture.sessionStorage.getItem('pendingOAuthToolId'), null);
	assert.equal(fixture.sessionStorage.getItem('oauthRedirectInProgressToolId'), null);
	assert.equal(fixture.helpers.consumeOAuthCancellation(), tool.id);
	assert.equal(fixture.helpers.consumeOAuthCancellation(), null);
});

test('an unstarted gate record and visits to other pages never masquerade as callbacks', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	fixture.navigate('/');
	assert.equal(await fixture.helpers.completeOAuthConnectRedirect(), null);
	fixture.helpers.continueOAuthConnect();
	fixture.navigate('/notes');
	assert.equal(await fixture.helpers.completeOAuthConnectRedirect(), null);
	assert.ok(fixture.sessionStorage.getItem(sessionKey));
});

test('a new explicit connection after Exit clears the one-shot cancellation marker', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	await fixture.helpers.cancelOAuthConnect();
	assert.ok(fixture.sessionStorage.getItem(cancellationKey));
	await startSession(fixture);
	assert.equal(fixture.sessionStorage.getItem(cancellationKey), null);
	assert.equal(fixture.helpers.consumeOAuthCancellation(), null);
});

test('expired and future-dated records cannot start provider navigation', async () => {
	const expired = createOfflineSession();
	await startSession(expired);
	expired.advanceTime(30 * 60 * 1000);
	assert.equal(expired.helpers.continueOAuthConnect(), false);
	assert.equal(expired.opened.length, 0);
	assert.equal(expired.sessionStorage.getItem(sessionKey), null);

	const future = createOfflineSession();
	await startSession(future);
	writeSession(future, { createdAt: storedSession(future).createdAt + 1 });
	assert.equal(future.helpers.getOAuthConnectSession(), null);
	assert.equal(future.opened.length, 0);
});

test('malformed storage is removed and cannot open a provider', () => {
	const fixture = createOfflineSession();
	fixture.sessionStorage.setItem(sessionKey, '{broken');
	assert.equal(fixture.helpers.getOAuthConnectSession(), null);
	assert.equal(fixture.sessionStorage.getItem(sessionKey), null);
	assert.equal(fixture.helpers.continueOAuthConnect(), false);
	assert.equal(fixture.opened.length, 0);
});

test('cross-origin or query-bearing authorization paths are rejected', async () => {
	const crossOrigin = createOfflineSession();
	await startSession(crossOrigin);
	writeSession(crossOrigin, {
		authorizePath: 'https://foreign.example.invalid/oauth/clients/mcp:slack/authorize'
	});
	assert.equal(crossOrigin.helpers.continueOAuthConnect(), false);
	assert.equal(crossOrigin.opened.length, 0);

	const queryPath = createOfflineSession();
	await startSession(queryPath);
	writeSession(queryPath, {
		authorizePath: '/oauth/clients/mcp:slack/authorize?redirect_uri=foreign'
	});
	assert.equal(queryPath.helpers.getOAuthConnectSession(), null);
});

test('cross-origin and self-gate return paths safely fall back to root', async () => {
	const crossOrigin = createOfflineSession();
	await startSession(crossOrigin);
	writeSession(crossOrigin, { returnPath: '//foreign.example.invalid/c/stolen' });
	await crossOrigin.helpers.cancelOAuthConnect();
	assert.equal(crossOrigin.navigations.at(-1).url, '/');

	const selfGate = createOfflineSession();
	await startSession(selfGate);
	writeSession(selfGate, { returnPath: '/auth/connect?again=yes' });
	await selfGate.helpers.cancelOAuthConnect();
	assert.equal(selfGate.navigations.at(-1).url, '/');
});

test('same-origin absolute return URLs normalize to an internal route', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	writeSession(fixture, { returnPath: origin + '/workspace/tools?tab=all#row' });
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.navigations.at(-1).url, '/workspace/tools?tab=all#row');
});

test('expired and malformed cancellation records are consumed without suppressing an unrelated tool', async () => {
	const expired = createOfflineSession();
	await startSession(expired);
	await expired.helpers.cancelOAuthConnect();
	expired.advanceTime(30 * 60 * 1000);
	assert.equal(expired.helpers.consumeOAuthCancellation(), null);
	assert.equal(expired.sessionStorage.getItem(cancellationKey), null);

	const malformed = createOfflineSession();
	malformed.sessionStorage.setItem(cancellationKey, 'not-json');
	assert.equal(malformed.helpers.consumeOAuthCancellation(), null);
	assert.equal(malformed.sessionStorage.getItem(cancellationKey), null);
});

test('Exit before Continue never sends a cancellation request even with an account token', async () => {
	const fixture = createOfflineSession();
	fixture.localStorage.token = 'offline-bearer-token';
	await startSession(fixture);
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.fetched.length, 0);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
});

test('Exit after Continue clears markers before cancelling only this app registration', async () => {
	const fixture = createOfflineSession();
	fixture.localStorage.token = 'offline-bearer-token';
	fixture.setFetchHandler(async (url, options) => {
		assert.equal(fixture.sessionStorage.getItem(sessionKey), null);
		assert.equal(fixture.sessionStorage.getItem('pendingOAuthToolId'), null);
		assert.equal(fixture.sessionStorage.getItem('oauthRedirectInProgressToolId'), null);
		assert.equal(options.method, 'POST');
		assert.equal(options.headers.Authorization, 'Bearer offline-bearer-token');
		return { ok: true };
	});
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.fetched.length, 1);
	assert.equal(fixture.fetched[0].url, '/oauth/clients/mcp:offline-slack/cancel');
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
	assert.equal(fixture.helpers.consumeOAuthCancellation(), tool.id);
});

test('Exit after Continue without an account token still clears and returns locally', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.fetched.length, 0);
	assert.equal(fixture.sessionStorage.getItem(sessionKey), null);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
});

test('a refused cancellation request cannot trap the browser at the connect gate', async () => {
	const fixture = createOfflineSession();
	fixture.localStorage.token = 'offline-bearer-token';
	fixture.setFetchHandler(async () => {
		throw new Error('Offline cancellation failure');
	});
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.fetched.length, 1);
	assert.equal(fixture.sessionStorage.getItem(sessionKey), null);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
	assert.equal(fixture.helpers.consumeOAuthCancellation(), tool.id);
});

test('a stalled cancellation request is aborted and the original route still opens', async () => {
	const fixture = createOfflineSession();
	fixture.localStorage.token = 'offline-bearer-token';
	fixture.setFetchHandler(
		(url, options) =>
			new Promise((resolve, reject) => {
				options.signal.addEventListener('abort', () => reject(new Error('Offline abort')), {
					once: true
				});
			})
	);
	await startSession(fixture);
	fixture.helpers.continueOAuthConnect();
	const cancellation = fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
	await cancellation;
	assert.equal(fixture.fetched.length, 1);
	assert.equal(fixture.fetched[0].options.signal.aborted, true);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
	assert.equal(fixture.helpers.consumeOAuthCancellation(), tool.id);
});

test('normal new-chat Start and Exit preserve the root route and draft storage', async () => {
	const fixture = createOfflineSession('/?new=true#draft');
	const draft = JSON.stringify({ prompt: 'An unsent offline draft', files: ['offline-file'] });
	fixture.localStorage.setItem('draft-chat-new', draft);
	await startSession(fixture);
	assert.equal(fixture.showOAuthConnect.value, true);
	assert.equal(fixture.navigations.length, 0);
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.showOAuthConnect.value, false);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.window.location.href, origin + '/?new=true#draft');
	assert.equal(fixture.localStorage.getItem('draft-chat-new'), draft);
	assert.equal(fixture.fetched.length, 0);
});

test('a reload restores the pending overlay without opening a provider or navigating the chat', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	fixture.showOAuthConnect.set(false);
	fixture.helpers.restoreOAuthConnectOverlay();
	assert.equal(fixture.showOAuthConnect.value, true);
	assert.equal(fixture.navigations.length, 0);
	assert.equal(fixture.opened.length, 0);
	assert.equal(fixture.fetched.length, 0);
	assert.equal(fixture.window.location.href, origin + '/c/offline-chat?view=chat#latest');
});

test('the legacy direct gate returns to the exact prior chat using one replace navigation', async () => {
	const fixture = createOfflineSession();
	await startSession(fixture);
	fixture.navigate('/auth/connect');
	fixture.showOAuthConnect.set(false);
	fixture.helpers.restoreOAuthConnectOverlay();
	assert.equal(fixture.showOAuthConnect.value, false);
	await fixture.helpers.cancelOAuthConnect();
	assert.equal(fixture.navigations.length, 1);
	assert.equal(fixture.navigations[0].url, '/c/offline-chat?view=chat#latest');
	assert.equal(fixture.navigations[0].options.replaceState, true);
	assert.equal(fixture.fetched.length, 0);
});

test('missing-session Exit stays on the current chat and only leaves a legacy gate page', async () => {
	const currentChat = createOfflineSession();
	currentChat.showOAuthConnect.set(true);
	await currentChat.helpers.cancelOAuthConnect();
	assert.equal(currentChat.navigations.length, 0);
	assert.equal(currentChat.showOAuthConnect.value, false);
	assert.equal(currentChat.window.location.pathname, '/c/offline-chat');

	const legacyGate = createOfflineSession('/auth/connect');
	await legacyGate.helpers.cancelOAuthConnect();
	assert.equal(legacyGate.navigations.length, 1);
	assert.equal(legacyGate.navigations[0].url, '/');
	assert.equal(legacyGate.navigations[0].options.replaceState, true);
});
