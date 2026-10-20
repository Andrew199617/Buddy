import assert from 'node:assert/strict';
import test from 'node:test';

import { enrollComputer, ENROLL_PATH, unenrollComputer, UNENROLL_PATH } from '../src/enrollment';

const host = { name: 'Studio PC', platform: 'win32', version: '0.1.0' };

function reply(status: number, body: unknown) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

test('trades the token for the machine credential', async () => {
	const requests: { url: string; init?: RequestInit }[] = [];
	const enrolled = await enrollComputer('https://buddy.example.com', 'bde1_token', host, async (url, init) => {
		requests.push({ url, init });
		return reply(200, { machine_id: 'machine-1', credential: 'bdr1_secret', name: 'Studio PC' });
	});
	assert.deepEqual(enrolled, { machineId: 'machine-1', credential: 'bdr1_secret', name: 'Studio PC' });
	const [request] = requests;
	assert.equal(request.url, `https://buddy.example.com${ENROLL_PATH}`);
	assert.equal(request.init?.method, 'POST');
	assert.equal(request.init?.redirect, 'error');
	// The token travels in the body, never in the address.
	assert.equal(request.url.includes('bde1_token'), false);
	assert.deepEqual(JSON.parse(String(request.init?.body)), { token: 'bde1_token', name: 'Studio PC', host });
});

test("reports Buddy's own reason when it refuses", async () => {
	const refuse = async () => reply(400, { detail: 'That connection request expired. Try again.' });
	await assert.rejects(enrollComputer('https://buddy.example.com', 'bde1_old', host, refuse), /expired/);
});

test('refuses an incomplete reply', async () => {
	const incomplete = async () => reply(200, { machine_id: 'machine-1' });
	await assert.rejects(enrollComputer('https://buddy.example.com', 'bde1_token', host, incomplete), /incomplete/);
});

test('explains an unreachable server', async () => {
	const offline = async () => {
		throw new TypeError('fetch failed');
	};
	await assert.rejects(enrollComputer('https://buddy.example.com', 'bde1_token', host, offline), /Could not reach/);
});

test('removes this computer with its own credential, in the body', async () => {
	const requests: { url: string; init?: RequestInit }[] = [];
	await unenrollComputer('https://buddy.example.com', 'machine-1', 'bdr1_secret', async (url, init) => {
		requests.push({ url, init });
		return reply(200, { removed: true });
	});
	const [request] = requests;
	assert.equal(request.url, `https://buddy.example.com${UNENROLL_PATH}`);
	assert.equal(request.init?.redirect, 'error');
	assert.equal(request.url.includes('bdr1_secret'), false);
	assert.deepEqual(JSON.parse(String(request.init?.body)), { machine_id: 'machine-1', credential: 'bdr1_secret' });
});

test('a computer the server no longer has counts as removed', async () => {
	const gone = async () => reply(404, { detail: 'This computer was removed from Buddy.' });
	await unenrollComputer('https://buddy.example.com', 'machine-1', 'bdr1_secret', gone);
});

test('other failures to remove are reported', async () => {
	const busy = async () => reply(503, { detail: 'Buddy is restarting.' });
	await assert.rejects(unenrollComputer('https://buddy.example.com', 'machine-1', 'bdr1_secret', busy), /restarting/);
	const offline = async () => {
		throw new TypeError('fetch failed');
	};
	await assert.rejects(unenrollComputer('https://buddy.example.com', 'machine-1', 'bdr1_secret', offline), /Could not reach Buddy/);
});
