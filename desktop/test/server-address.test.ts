import assert from 'node:assert/strict';
import test from 'node:test';

import { checkBuddyServer, isLoopbackHost, normalizeServerAddress } from '../src/server-address';
import { allowsPermission, isSameOrigin, isWebLink } from '../src/web-security';

test('a Buddy address becomes its origin', () => {
	assert.equal(normalizeServerAddress(' https://buddy.example.com/ '), 'https://buddy.example.com');
	assert.equal(normalizeServerAddress('buddy.example.com'), 'https://buddy.example.com');
	assert.equal(normalizeServerAddress('https://buddy.example.com:8443'), 'https://buddy.example.com:8443');
});

test('plain HTTP is allowed only for Buddy on this computer', () => {
	assert.equal(normalizeServerAddress('http://localhost:8082'), 'http://localhost:8082');
	assert.equal(normalizeServerAddress('http://127.0.0.1:8092'), 'http://127.0.0.1:8092');
	assert.throws(() => normalizeServerAddress('http://192.168.1.20:8082'), /https:\/\//);
	assert.throws(() => normalizeServerAddress('http://buddy.example.com'), /https:\/\//);
});

test('an address with a path, credentials or another scheme is refused', () => {
	assert.throws(() => normalizeServerAddress('https://buddy.example.com/chat'), /without a path/);
	assert.throws(() => normalizeServerAddress('https://me:secret@buddy.example.com'), /user name/);
	assert.throws(() => normalizeServerAddress('file:///C:/buddy'), /https/);
	assert.throws(() => normalizeServerAddress(''), /Enter your Buddy address/);
});

test('only real loopback names count as this computer', () => {
	assert.equal(isLoopbackHost('127.10.0.1'), true);
	assert.equal(isLoopbackHost('[::1]'), true);
	assert.equal(isLoopbackHost('127.0.0.1.example.com'), false);
	assert.equal(isLoopbackHost('128.0.0.1'), false);
	assert.equal(isLoopbackHost('127.0.0.300'), false);
});

test('a Buddy server is recognised by its config', async () => {
	const answer = async () => new Response(JSON.stringify({ name: 'Buddy', version: '1' }), { status: 200 });
	assert.equal(await checkBuddyServer('https://buddy.example.com', answer), 'Buddy');
	const other = async () => new Response('<html></html>', { status: 200 });
	await assert.rejects(checkBuddyServer('https://example.com', other), /No Buddy server/);
	const offline = async () => {
		throw new TypeError('fetch failed');
	};
	await assert.rejects(checkBuddyServer('https://example.com', offline), /Could not reach/);
});

test('only the Buddy origin gets the app and its browser permissions', () => {
	const origin = 'https://buddy.example.com';
	assert.equal(isSameOrigin('https://buddy.example.com/c/1', origin), true);
	assert.equal(isSameOrigin('https://buddy.example.com.evil.test/', origin), false);
	assert.equal(isSameOrigin('not a url', origin), false);
	assert.equal(allowsPermission('media', 'https://buddy.example.com/', origin), true);
	assert.equal(allowsPermission('media', 'https://evil.test/', origin), false);
	assert.equal(allowsPermission('geolocation', 'https://buddy.example.com/', origin), false);
	assert.equal(isWebLink('https://docs.example.com'), true);
	assert.equal(isWebLink('file:///C:/Windows/system32/calc.exe'), false);
});
