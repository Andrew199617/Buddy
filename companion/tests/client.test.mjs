import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

// Exercise the shipped browser helper without starting Vite or changing its source.
const source = await readFile(new URL('../../src/lib/apis/companion.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
	compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 }
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled.outputText).toString('base64')}`;
const { CompanionClient, CompanionError, DEFAULT_COMPANION_ENDPOINT, normalizeCompanionEndpoint } =
	await import(moduleUrl);

const host = { id: 'host-test', name: 'Test execution PC', platform: 'test', version: '1' };

function jsonResponse(body, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

function pairResponse(token = 'temporary-companion-token') {
	return jsonResponse({ token, host, expiresAt: Date.now() + 60_000 });
}

async function withFetch(handler, run) {
	const original = globalThis.fetch;
	globalThis.fetch = handler;
	try {
		await run();
	} finally {
		globalThis.fetch = original;
	}
}

function assertPrivateRequest(options) {
	assert.equal(options.credentials, 'omit', 'Buddy cookies must never accompany host requests');
	assert.equal(options.redirect, 'error', 'a host must not redirect a bearer token');
	assert.equal(options.referrerPolicy, 'no-referrer');
	assert.equal(options.mode, 'cors');
	assert.ok(options.signal instanceof AbortSignal);
}

test('endpoint rules allow local HTTP and configurable remote HTTPS', () => {
	assert.equal(DEFAULT_COMPANION_ENDPOINT, 'http://127.0.0.1:8083');
	const valid = new Map([
		[' http://127.0.0.1:3301/ ', 'http://127.0.0.1:3301'],
		['http://localhost:3301', 'http://localhost:3301'],
		['http://[::1]:3301/', 'http://[::1]:3301'],
		['http://127.255.0.1:4400', 'http://127.255.0.1:4400'],
		['http://127.1:3301', 'http://127.0.0.1:3301'],
		['https://Execution.Example:4400/', 'https://execution.example:4400'],
		['https://192.168.1.20:3301', 'https://192.168.1.20:3301']
	]);
	for (const [input, expected] of valid) {
		assert.equal(normalizeCompanionEndpoint(input), expected, input);
	}
	// On a phone, localhost still identifies that phone. Never rewrite it to another PC.
	assert.equal(new CompanionClient('http://localhost:3301').endpoint, 'http://localhost:3301');
});

test('endpoint rules reject unsafe schemes, remote HTTP, credentials, and URL suffixes', () => {
	const invalid = [
		'',
		'localhost:3301',
		'ftp://localhost:3301',
		'ws://localhost:3301',
		'http://execution.example:3301',
		'http://192.168.1.20:3301',
		'http://0.0.0.0:3301',
		'http://[::]:3301',
		'http://[::ffff:127.0.0.1]:3301',
		'http://localhost.example:3301',
		'http://127.0.0.1.example.com:3301',
		'http://127.attacker.example:3301',
		'https://user:password@execution.example:3301',
		'https://user%40name@execution.example:3301',
		'https://execution.example:3301/project',
		'https://execution.example:3301/../',
		'https://execution.example:3301?token=secret',
		'https://execution.example:3301/#secret',
		'https://execution.example:3301/?',
		'https://execution.example:3301/#',
		'http://localhost:65536',
		'https://'
	];
	for (const input of invalid) {
		assert.throws(() => normalizeCompanionEndpoint(input), Error, input);
		assert.throws(() => new CompanionClient(input), Error, input);
	}
});

test('pairing keeps its token in one client and never forwards Buddy or model credentials', async () => {
	const requests = [];
	const originalLocalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
	const originalSessionStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
	const forbiddenStorage = {
		getItem() {
			throw new Error('Companion must not read Buddy/model credentials');
		},
		setItem() {
			throw new Error('Companion must not persist pairing credentials');
		},
		removeItem() {
			throw new Error('Companion must not alter Buddy/model credentials');
		}
	};
	Object.defineProperty(globalThis, 'localStorage', {
		value: forbiddenStorage,
		configurable: true
	});
	Object.defineProperty(globalThis, 'sessionStorage', {
		value: forbiddenStorage,
		configurable: true
	});
	try {
		await withFetch(
			async (url, options) => {
				requests.push({ url, options });
				assertPrivateRequest(options);
				if (url.endsWith('/v1/pair')) return pairResponse();
				if (url.endsWith('/v1/host')) return jsonResponse({ host });
				return jsonResponse({ grants: [] });
			},
			async () => {
				const client = new CompanionClient();
				assert.deepEqual(await client.getHost(), host);
				const pairing = await client.pair('123456');
				assert.deepEqual(pairing.host, host);
				assert.ok(Number.isFinite(pairing.expiresAt));
				assert.equal(Object.hasOwn(pairing, 'token'), false);
				await client.getGrants();
				await new CompanionClient().getGrants();
			}
		);
	} finally {
		if (originalLocalStorage)
			Object.defineProperty(globalThis, 'localStorage', originalLocalStorage);
		else delete globalThis.localStorage;
		if (originalSessionStorage)
			Object.defineProperty(globalThis, 'sessionStorage', originalSessionStorage);
		else delete globalThis.sessionStorage;
	}
	assert.equal(requests[0].options.headers.Authorization, undefined);
	assert.equal(requests[1].options.headers.Authorization, undefined);
	assert.deepEqual(JSON.parse(requests[1].options.body), { code: '123456' });
	assert.equal(requests[2].options.headers.Authorization, 'Bearer temporary-companion-token');
	assert.equal(
		requests[3].options.headers.Authorization,
		undefined,
		'new clients must pair independently'
	);
	assert.equal(requests[2].options.headers.Cookie, undefined);
});

test('directory, file, and terminal identifiers stay in their intended request fields', async () => {
	const requests = [];
	const grantId = 'grant/&?';
	const path = 'folder/file name?#.txt';
	const terminalId = 'terminal/a ?#';
	await withFetch(
		async (url, options) => {
			requests.push({ url: new URL(url), options });
			if (url.endsWith('/v1/pair')) return pairResponse();
			if (options.method === 'DELETE') return new Response(null, { status: 204 });
			if (url.includes('/v1/terminals')) return jsonResponse({ terminal: { id: terminalId } });
			return jsonResponse({ path, content: 'hello', entries: [] });
		},
		async () => {
			const client = new CompanionClient('https://execution.example:4400');
			await client.pair('123456');
			await client.getDirectory(grantId, path);
			await client.getFile(grantId, path);
			await client.putFile(grantId, path, 'Unicode: café\nnext line');
			await client.runCommand(terminalId, 'node', ['-e', 'console.log("a & b")']);
			await client.getTerminal(terminalId);
			await client.deleteTerminal(terminalId);
		}
	);
	for (const request of requests) {
		assert.equal(request.url.origin, 'https://execution.example:4400');
		assertPrivateRequest(request.options);
	}
	for (const request of [requests[1], requests[2]]) {
		assert.equal(request.url.searchParams.get('grantId'), grantId);
		assert.equal(request.url.searchParams.get('path'), path);
		assert.equal([...request.url.searchParams].length, 2);
	}
	assert.equal(requests[3].options.method, 'PUT');
	assert.deepEqual(JSON.parse(requests[3].options.body), {
		grantId,
		path,
		content: 'Unicode: café\nnext line'
	});
	assert.equal(
		requests[4].url.pathname,
		`/v1/terminals/${encodeURIComponent(terminalId)}/commands`
	);
	assert.deepEqual(JSON.parse(requests[4].options.body), {
		executable: 'node',
		args: ['-e', 'console.log("a & b")']
	});
	assert.equal(requests[5].url.pathname, `/v1/terminals/${encodeURIComponent(terminalId)}`);
	assert.equal(requests[6].url.pathname, requests[5].url.pathname);
	assert.equal(requests[6].options.method, 'DELETE');
});

test('host errors retain status and safe messages while network errors remain visible', async () => {
	let requestNumber = 0;
	const networkError = new TypeError('Network unavailable');
	await withFetch(
		async () => {
			requestNumber += 1;
			if (requestNumber === 1)
				return jsonResponse({ error: 'Directory access is not granted.' }, 403);
			if (requestNumber === 2) return new Response('not JSON', { status: 401 });
			if (requestNumber === 3) throw networkError;
			return jsonResponse({ grants: [] });
		},
		async () => {
			const client = new CompanionClient();
			await assert.rejects(client.getGrants(), (error) => {
				assert.ok(error instanceof CompanionError);
				assert.equal(error.status, 403);
				assert.equal(error.message, 'Directory access is not granted.');
				return true;
			});
			await assert.rejects(client.getGrants(), (error) => {
				assert.ok(error instanceof CompanionError);
				assert.equal(error.status, 401);
				assert.equal(error.message, 'Companion request failed (401).');
				return true;
			});
			await assert.rejects(client.getGrants(), (error) => error === networkError);
			assert.deepEqual(await client.getGrants(), []);
		}
	);
});

test('a stalled request is aborted after a bounded timeout and its timer is cleared', async () => {
	const originalSetTimeout = globalThis.setTimeout;
	const originalClearTimeout = globalThis.clearTimeout;
	const timers = new Map();
	let nextTimerId = 1;
	globalThis.setTimeout = (callback, delay) => {
		const id = nextTimerId++;
		timers.set(id, { callback, delay });
		return id;
	};
	globalThis.clearTimeout = (id) => {
		timers.delete(id);
	};
	try {
		await withFetch(
			async (url, options) => {
				return new Promise((resolve, reject) => {
					options.signal.addEventListener(
						'abort',
						() => reject(new DOMException('Aborted', 'AbortError')),
						{ once: true }
					);
				});
			},
			async () => {
				const client = new CompanionClient();
				const pending = assert.rejects(client.getGrants(), { name: 'AbortError' });
				assert.equal(timers.size, 1);
				const timer = [...timers.values()][0];
				assert.ok(timer.delay > 0 && timer.delay <= 30_000);
				timer.callback();
				await pending;
				assert.equal(timers.size, 0);
			}
		);
	} finally {
		globalThis.setTimeout = originalSetTimeout;
		globalThis.clearTimeout = originalClearTimeout;
	}
});

test('invalid pairing responses do not authenticate subsequent requests', async () => {
	const requests = [];
	const invalidResponses = [
		{ token: 'invalid-response-token', expiresAt: 123, host: {} },
		{ token: 123, expiresAt: 123, host },
		{ token: '', expiresAt: 123, host },
		{ token: 'invalid-response-token', expiresAt: '123', host },
		{ token: 'invalid-response-token', expiresAt: 123, host: { ...host, id: 123 } }
	];
	let pairIndex = 0;
	await withFetch(
		async (url, options) => {
			requests.push({ url, options });
			if (url.endsWith('/v1/pair')) {
				return jsonResponse(invalidResponses[pairIndex++]);
			}
			return jsonResponse({ grants: [] });
		},
		async () => {
			const client = new CompanionClient();
			for (const response of invalidResponses) {
				await assert.rejects(
					client.pair('123456'),
					/invalid pairing response/i,
					JSON.stringify(response)
				);
				await client.getGrants();
			}
		}
	);
	assert.equal(pairIndex, invalidResponses.length);
	for (const request of requests) {
		assert.equal(request.options.headers.Authorization, undefined);
	}
});

test('disconnect aborts pending requests, revokes once, and prevents future requests', async () => {
	const requests = [];
	await withFetch(
		async (url, options) => {
			requests.push({ url, options });
			if (url.endsWith('/v1/pair')) return pairResponse();
			if (url.endsWith('/v1/session')) return new Response(null, { status: 204 });
			return new Promise((resolve, reject) => {
				options.signal.addEventListener(
					'abort',
					() => reject(new DOMException('Aborted', 'AbortError')),
					{ once: true }
				);
			});
		},
		async () => {
			const client = new CompanionClient();
			await client.pair('123456');
			const pending = assert.rejects(client.getGrants(), { name: 'AbortError' });
			await client.disconnect();
			await pending;
			await client.disconnect();
			await assert.rejects(client.getGrants(), /connection is closed/i);
		}
	);
	assert.equal(requests.length, 3);
	assert.equal(requests[1].options.signal.aborted, true);
	assert.equal(requests[2].url, `${DEFAULT_COMPANION_ENDPOINT}/v1/session`);
	assert.equal(requests[2].options.method, 'DELETE');
	assert.equal(requests[2].options.headers.Authorization, 'Bearer temporary-companion-token');
	assert.equal(requests[2].options.keepalive, true);
	assertPrivateRequest(requests[2].options);
});

test('disconnect revokes a pairing token that arrives after cancellation', async () => {
	const requests = [];
	let resolvePair;
	await withFetch(
		async (url, options) => {
			requests.push({ url, options });
			if (url.endsWith('/v1/pair')) {
				// A late response can race AbortController cancellation at the transport boundary.
				return new Promise((resolve) => {
					resolvePair = resolve;
				});
			}
			return new Response(null, { status: 204 });
		},
		async () => {
			const client = new CompanionClient();
			const pending = assert.rejects(client.pair('123456'), /connection changed while pairing/i);
			await client.disconnect();
			resolvePair(pairResponse('late-token'));
			await pending;
			await assert.rejects(client.getGrants(), /connection is closed/i);
		}
	);
	assert.equal(requests.length, 2);
	assert.equal(requests[0].options.signal.aborted, true);
	assert.equal(requests[1].url, `${DEFAULT_COMPANION_ENDPOINT}/v1/session`);
	assert.equal(requests[1].options.headers.Authorization, 'Bearer late-token');
});

test('an expired session can disconnect, while failed revocation is reported and stays closed', async () => {
	for (const status of [401, 503]) {
		let requestCount = 0;
		await withFetch(
			async (url) => {
				requestCount += 1;
				if (url.endsWith('/v1/pair')) return pairResponse();
				return new Response(null, { status });
			},
			async () => {
				const client = new CompanionClient();
				await client.pair('123456');
				if (status === 401) {
					await client.disconnect();
				} else {
					await assert.rejects(client.disconnect(), (error) => {
						assert.ok(error instanceof CompanionError);
						assert.equal(error.status, 503);
						return true;
					});
				}
				await assert.rejects(client.getGrants(), /connection is closed/i);
				await client.disconnect();
				assert.equal(requestCount, 2, 'disconnected clients must not retry with forgotten tokens');
			}
		);
	}
});
