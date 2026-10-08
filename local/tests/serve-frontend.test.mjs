import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { createServer, request } from 'node:http';
import { basename, dirname, join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { after, before, test } from 'node:test';
import { brotliDecompressSync, gunzipSync } from 'node:zlib';
import { createFrontendServer, selectEncoding } from '../serve-frontend.mjs';

const html = '<!doctype html><title>Buddy fixture</title>' + '<p>Buddy</p>'.repeat(300);
const javascript = 'export const buddy = "fixture";\n'.repeat(400);
const font = Buffer.from(Array.from({ length: 32768 }, (_, index) => index % 251));
let directory;
let backend;
let frontend;
let frontendPort;
let streamFinished = false;
let websocketHost;
const backendSockets = new Set();

function rawGet(path, headers = {}, method = 'GET') {
	return new Promise((resolveResponse, reject) => {
		const req = request(
			{ host: '127.0.0.1', port: frontendPort, path, headers, method },
			(response) => {
				const chunks = [];
				response.on('data', (chunk) => chunks.push(chunk));
				response.on('end', () =>
					resolveResponse({
						status: response.statusCode,
						headers: response.headers,
						body: Buffer.concat(chunks)
					})
				);
				response.on('error', reject);
			}
		);
		req.on('error', reject);
		req.end();
	});
}

function fakeBackend(req, res) {
	if (req.url === '/api/stream') {
		streamFinished = false;
		res.writeHead(200, { 'Content-Type': 'text/event-stream' });
		res.write('data: first\n\n');
		setTimeout(() => {
			streamFinished = true;
			res.end('data: second\n\n');
		}, 150);
		return;
	}
	if (req.url === '/static/loader.js') {
		res.setHeader('Content-Type', 'text/javascript');
		res.end('window.fixtureLoaderScriptCount = 9;');
		return;
	}
	if (req.url === '/manifest.json') {
		res.setHeader('Content-Type', 'application/json');
		res.end(
			JSON.stringify({ name: 'Buddy', id: '/', scope: '/', start_url: '/', host: req.headers.host })
		);
		return;
	}
	res.setHeader('Content-Type', 'application/json');
	res.setHeader('Set-Cookie', 'fixture-session=value; HttpOnly; SameSite=Lax');
	res.end(
		JSON.stringify({
			host: req.headers.host,
			authorization: req.headers.authorization,
			cookie: req.headers.cookie,
			forwarded: req.headers.forwarded,
			forwardedProto: req.headers['x-forwarded-proto'],
			forwardedFor: req.headers['x-forwarded-for']
		})
	);
}

before(async () => {
	directory = await mkdtemp(join(tmpdir(), 'buddy-frontend-test-'));
	await mkdir(join(directory, '_app/immutable'), { recursive: true });
	await mkdir(join(directory, 'static/fonts'), { recursive: true });
	await writeFile(join(directory, 'index.html'), html);
	await writeFile(join(directory, '_app/immutable/buddy.hash.js'), javascript);
	await writeFile(join(directory, '_app/version.json'), JSON.stringify({ version: 'fixture' }));
	await writeFile(join(directory, 'static/fonts/Inter-Variable.ttf'), font);
	await writeFile(join(directory, 'static/fonts/font.woff2'), font);
	backend = createServer(fakeBackend);
	backend.on('connection', (socket) => {
		backendSockets.add(socket);
		socket.on('close', () => backendSockets.delete(socket));
	});
	backend.on('upgrade', (req, socket) => {
		websocketHost = req.headers.host;
		const accept = createHash('sha1')
			.update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
			.digest('base64');
		socket.write(
			`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`
		);
		const message = Buffer.from('buddy-websocket');
		socket.write(Buffer.concat([Buffer.from([0x81, message.length]), message]));
		socket.on('data', () => socket.end(Buffer.from([0x88, 0])));
	});
	backend.listen(0, '127.0.0.1');
	await once(backend, 'listening');
	frontend = await createFrontendServer({
		frontendDir: directory,
		backendUrl: `http://127.0.0.1:${backend.address().port}`,
		host: '127.0.0.1',
		port: 0,
		allowedHosts: ['buddy.test'],
		logLevel: 'silent'
	});
	frontendPort = frontend.httpServer.address().port;
});

after(async () => {
	frontend?.httpServer.closeAllConnections();
	if (frontend) await new Promise((resolveClose) => frontend.httpServer.close(resolveClose));
	for (const socket of backendSockets) socket.destroy();
	if (backend) await new Promise((resolveClose) => backend.close(resolveClose));
	if (
		dirname(directory) !== resolve(tmpdir()) ||
		!basename(directory).startsWith('buddy-frontend-test-')
	)
		throw new Error('Unexpected test fixture path');
	await rm(directory, { recursive: true, force: true });
});

test('encoding negotiation honors q values and HTTP gzip-only clients', () => {
	assert.equal(selectEncoding('br,gzip'), 'br');
	assert.equal(selectEncoding('gzip, deflate'), 'gzip');
	assert.equal(selectEncoding('br;q=0,gzip;q=1'), 'gzip');
	assert.equal(selectEncoding('br;q=.5,gzip;q=.8'), 'gzip');
	assert.equal(selectEncoding('identity;q=1,br;q=.5'), 'identity');
	assert.equal(selectEncoding('br;q=0,gzip;q=0,identity;q=0'), null);
	assert.equal(selectEncoding(''), 'identity');
});

test('hashed assets support Brotli, gzip, identity and immutable caching', async () => {
	const brotli = await rawGet('/_app/immutable/buddy.hash.js', { 'Accept-Encoding': 'br,gzip' });
	assert.equal(brotli.status, 200);
	assert.equal(brotli.headers['content-encoding'], 'br');
	assert.equal(brotliDecompressSync(brotli.body).toString(), javascript);
	assert.match(brotli.headers['cache-control'], /max-age=31536000.*immutable/);
	assert.equal(brotli.headers.vary, 'Accept-Encoding');
	const gzip = await rawGet('/_app/immutable/buddy.hash.js', { 'Accept-Encoding': 'gzip' });
	assert.equal(gzip.headers['content-encoding'], 'gzip');
	assert.equal(gunzipSync(gzip.body).toString(), javascript);
	const identity = await rawGet('/_app/immutable/buddy.hash.js', { 'Accept-Encoding': 'identity' });
	assert.equal(identity.headers['content-encoding'], undefined);
	assert.equal(identity.headers.vary, 'Accept-Encoding');
	assert.equal(identity.body.toString(), javascript);
	assert.notEqual(identity.headers.etag, brotli.headers.etag);
});

test('compressed and identity ETags revalidate their own representation', async () => {
	const first = await rawGet('/_app/immutable/buddy.hash.js', { 'Accept-Encoding': 'br' });
	const matching = await rawGet('/_app/immutable/buddy.hash.js', {
		'Accept-Encoding': 'br',
		'If-None-Match': first.headers.etag
	});
	assert.equal(matching.status, 304);
	assert.equal(matching.headers.vary, 'Accept-Encoding');
	assert.equal(matching.body.length, 0);
	const otherEncoding = await rawGet('/_app/immutable/buddy.hash.js', {
		'Accept-Encoding': 'identity',
		'If-None-Match': first.headers.etag
	});
	assert.equal(otherEncoding.status, 200);
});

test('font compression is lossless and nonfingerprinted fonts revalidate', async () => {
	const compressed = await rawGet('/static/fonts/Inter-Variable.ttf', {
		'Accept-Encoding': 'gzip'
	});
	assert.equal(compressed.headers['content-encoding'], 'gzip');
	assert.deepEqual(gunzipSync(compressed.body), font);
	assert.equal(compressed.headers['cache-control'], 'no-cache');
	const modernFont = await rawGet('/static/fonts/font.woff2', { 'Accept-Encoding': 'br,gzip' });
	assert.equal(modernFont.headers['content-encoding'], undefined);
	assert.deepEqual(modernFont.body, font);
});

test('SPA HTML and mutable version metadata revalidate', async () => {
	const page = await rawGet('/c/fixture-conversation', { 'Accept-Encoding': 'br' });
	assert.equal(page.status, 200);
	assert.equal(page.headers['cache-control'], 'no-cache');
	assert.equal(brotliDecompressSync(page.body).toString(), html);
	const version = await rawGet('/_app/version.json');
	assert.equal(version.status, 200);
	assert.equal(version.headers['cache-control'], 'no-cache');
});

test('HEAD and identity byte ranges preserve lengths', async () => {
	const head = await rawGet('/_app/immutable/buddy.hash.js', { 'Accept-Encoding': 'gzip' }, 'HEAD');
	assert.equal(head.status, 200);
	assert.equal(head.headers['content-encoding'], 'gzip');
	assert.equal(head.body.length, 0);
	assert.ok(Number(head.headers['content-length']) > 0);
	const range = await rawGet('/_app/immutable/buddy.hash.js', {
		Range: 'bytes=0-7',
		'Accept-Encoding': 'gzip'
	});
	assert.equal(range.status, 206);
	assert.equal(range.headers['content-encoding'], undefined);
	assert.equal(range.body.toString(), javascript.slice(0, 8));
	assert.match(range.headers['content-range'], /^bytes 0-7\//);
	assert.equal(
		(await rawGet('/_app/immutable/buddy.hash.js', { Range: 'bytes=999999-' })).status,
		416
	);
});

test('missing assets, hidden/traversing paths and unacceptable encodings fail clearly', async () => {
	assert.equal((await rawGet('/_app/immutable/missing.js')).status, 404);
	assert.equal((await rawGet('/static/%2e%2e%2f%2e%2e%2fsecret')).status, 404);
	assert.equal((await rawGet('/.private')).status, 404);
	assert.equal((await rawGet('/%zz')).status, 400);
	assert.equal(
		(
			await rawGet('/_app/immutable/buddy.hash.js', {
				'Accept-Encoding': 'identity;q=0,br;q=0,gzip;q=0'
			})
		).status,
		406
	);
	assert.equal((await rawGet('/', { Host: 'untrusted.test' })).status, 403);
});

test('API and OAuth proxy retain Host, cookies and auth while replacing forged forwarding', async () => {
	for (const path of ['/api/echo', '/oauth/echo']) {
		const response = await rawGet(path, {
			Host: `buddy.test:${frontendPort}`,
			Authorization: 'Bearer fixture-token',
			Cookie: 'fixture-cookie=value',
			Forwarded: 'host=forged.test',
			'X-Forwarded-Proto': 'https',
			'X-Forwarded-For': '203.0.113.42'
		});
		assert.equal(response.status, 200);
		assert.equal(response.headers['cache-control'], 'private, no-store');
		assert.ok(response.headers['set-cookie']);
		const body = JSON.parse(response.body);
		assert.equal(body.host, `buddy.test:${frontendPort}`);
		assert.equal(body.authorization, 'Bearer fixture-token');
		assert.equal(body.cookie, 'fixture-cookie=value');
		assert.equal(body.forwarded, undefined);
		assert.equal(body.forwardedProto, 'http');
		assert.equal(body.forwardedFor, '127.0.0.1');
	}
});

test('loader and PWA manifest come from the backend on the same frontend origin', async () => {
	const loader = await rawGet('/static/loader.js');
	assert.equal(loader.body.toString(), 'window.fixtureLoaderScriptCount = 9;');
	assert.equal(loader.headers['cache-control'], 'no-cache');
	const manifest = await rawGet('/manifest.json');
	const body = JSON.parse(manifest.body);
	assert.equal(body.host, `127.0.0.1:${frontendPort}`);
	assert.equal(body.scope, '/');
	assert.equal(manifest.headers['cache-control'], 'no-cache');
});

test('proxied event streams deliver their first chunk before completion', async () => {
	await new Promise((resolveStream, reject) => {
		const req = request(
			{
				host: '127.0.0.1',
				port: frontendPort,
				path: '/api/stream',
				headers: { 'Accept-Encoding': 'br,gzip' }
			},
			(response) => {
				assert.equal(response.headers['content-encoding'], undefined);
				response.once('data', (chunk) => {
					assert.equal(streamFinished, false);
					assert.match(chunk.toString(), /first/);
				});
				response.on('end', resolveStream);
				response.on('error', reject);
			}
		);
		req.on('error', reject);
		req.end();
	});
});

test('WebSocket upgrades retain the frontend Host and transfer native messages', async () => {
	const socket = new WebSocket(`ws://127.0.0.1:${frontendPort}/ws/fixture`);
	const [message] = await once(socket, 'message', { signal: AbortSignal.timeout(3000) });
	assert.equal(message.data, 'buddy-websocket');
	assert.equal(websocketHost, `127.0.0.1:${frontendPort}`);
	const closed = once(socket, 'close', { signal: AbortSignal.timeout(3000) });
	socket.close();
	await closed;
});
