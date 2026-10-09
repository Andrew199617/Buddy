import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { link, mkdtemp, mkdir, readFile, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import test from 'node:test';
import { createCompanion } from '../server.mjs';

const ORIGIN = 'http://localhost:3300';
const SECOND_ORIGIN = 'http://127.0.0.1:3300';
const executeFile = promisify(execFile);

async function fixture(t, options = {}) {
	const temporary = await mkdtemp(join(tmpdir(), 'buddy-companion-test-'));
	const root = join(temporary, 'project');
	const outside = join(temporary, 'outside');
	await mkdir(join(root, 'nested'), { recursive: true });
	await mkdir(outside);
	await writeFile(join(root, 'readme.txt'), 'project content', 'utf8');
	await writeFile(join(root, 'nested', 'note.txt'), 'nested content', 'utf8');
	await writeFile(join(outside, 'secret.txt'), 'outside secret', 'utf8');
	const { rootCapabilities = {}, ...serverOptions } = options;
	const roots = options.roots ?? [{ path: root, name: 'Test project', ...rootCapabilities }];
	const app = await createCompanion({ origins: [ORIGIN], ...serverOptions, roots });
	app.server.listen(0, '127.0.0.1');
	await once(app.server, 'listening');
	const base = `http://127.0.0.1:${app.server.address().port}`;
	const trackedProcesses = new Set();
	t.after(async () => {
		await app.close();
		for (const pid of trackedProcesses) await terminateTestProcess(pid);
		await rm(temporary, { recursive: true, force: true });
	});
	async function request(path, { token, origin = ORIGIN, method = 'GET', body, headers = {} } = {}) {
		const requestHeaders = { ...headers };
		if (origin !== undefined && origin !== null) requestHeaders.Origin = origin;
		if (token) requestHeaders.Authorization = `Bearer ${token}`;
		if (body !== undefined) requestHeaders['Content-Type'] = 'application/json';
		const response = await fetch(`${base}${path}`, {
			method,
			headers: requestHeaders,
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		const text = await response.text();
		let data;
		try {
			data = JSON.parse(text);
		} catch {
			data = { raw: text };
		}
		return { status: response.status, data, headers: response.headers };
	}
	async function pair(origin = ORIGIN) {
		const response = await request('/v1/pair', {
			method: 'POST', origin, body: { code: app.pairingCode }
		});
		assert.equal(response.status, 200, JSON.stringify(response.data));
		assert.equal(typeof response.data.token, 'string');
		return response.data.token;
	}
	async function rawRequest(path, headers) {
		return new Promise((resolve, reject) => {
			const request = httpRequest(`${base}${path}`, { headers }, (response) => {
				const chunks = [];
				response.on('data', (chunk) => chunks.push(chunk));
				response.on('end', () => resolve({
					status: response.statusCode,
					data: JSON.parse(Buffer.concat(chunks).toString('utf8'))
				}));
			});
			request.on('error', reject);
			request.end();
		});
	}
	async function grantFor(token) {
		const response = await request('/v1/grants', { token });
		assert.equal(response.status, 200);
		assert.equal(response.data.grants.length, roots.length);
		return response.data.grants[0];
	}
	return { app, base, root, outside, temporary, request, rawRequest, pair, grantFor, trackedProcesses };
}

function filePath(grantId, path) {
	return `/v1/files?${new URLSearchParams({ grantId, path })}`;
}

function directoryPath(grantId, path) {
	return `/v1/directories?${new URLSearchParams({ grantId, path })}`;
}

async function eventually(check, message, timeoutMs = 4000) {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		if (await check()) return;
		await new Promise((resolve) => setTimeout(resolve, 25));
	}
	assert.fail(message);
}

function processAlive(pid) {
	try {
		process.kill(pid, 0);
		return true;
	} catch (error) {
		if (error.code === 'ESRCH') return false;
		throw error;
	}
}

async function terminateTestProcess(pid) {
	if (!processAlive(pid)) return;
	try {
		if (process.platform === 'win32') {
			await executeFile('taskkill', ['/PID', String(pid), '/T', '/F'], { timeout: 2000 });
		} else {
			process.kill(pid, 'SIGKILL');
		}
	} catch (error) {
		if (error.code !== 'ESRCH' && processAlive(pid)) throw error;
	}
}

async function terminalFixture(t, options = {}) {
	const f = await fixture(t, options);
	const token = await f.pair();
	const grant = await f.grantFor(token);
	const workspaceResponse = await f.request('/v1/workspaces', {
		token, method: 'POST', body: { grantId: grant.id, path: 'nested' }
	});
	assert.equal(workspaceResponse.status, 200, JSON.stringify(workspaceResponse.data));
	const workspace = workspaceResponse.data.workspace;
	const response = await f.request('/v1/terminals', {
		token, method: 'POST', body: { workspaceId: workspace.id }
	});
	assert.equal(response.status, 200, JSON.stringify(response.data));
	return { ...f, token, grant, workspace, terminal: response.data.terminal };
}

async function executableFixture(t, options = {}) {
	// Root capabilities are configured by the host, never granted by a browser path.
	return terminalFixture(t, { ...options, rootCapabilities: { execute: true } });
}

async function startProcessTree(f) {
	const source = [
		"const { spawn } = require('node:child_process');",
		"const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });",
		"console.log(JSON.stringify({ parent: process.pid, child: child.pid }));",
		'setInterval(() => {}, 1000);'
	].join('\n');
	const response = await f.request(`/v1/terminals/${f.terminal.id}/commands`, {
		token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', source] }
	});
	assert.equal(response.status, 200, JSON.stringify(response.data));
	let pids;
	await eventually(async () => {
		const polled = await f.request(`/v1/terminals/${f.terminal.id}`, { token: f.token });
		if (polled.status !== 200) return false;
		const match = polled.data.terminal.output.match(/\{"parent":\d+,"child":\d+\}/);
		if (!match) return false;
		pids = JSON.parse(match[0]);
		f.trackedProcesses.add(pids.parent);
		f.trackedProcesses.add(pids.child);
		return true;
	}, 'Command did not report both process IDs');
	assert.equal(processAlive(pids.parent), true);
	assert.equal(processAlive(pids.child), true);
	return pids;
}

async function assertTreeStopped(pids) {
	await eventually(() => !processAlive(pids.parent) && !processAlive(pids.child),
		'Both command and descendant must stop');
}

test('host identity is generated by the execution host and empty roots grant nothing', async (t) => {
	const f = await fixture(t, { roots: [], hostName: 'Temporary test host' });
	const host = await f.request('/v1/host', { origin: null });
	assert.equal(host.status, 200);
	assert.equal(host.data.host.name, 'Temporary test host');
	assert.equal(typeof host.data.host.id, 'string');
	const token = await f.pair();
	const grants = await f.request('/v1/grants', { token });
	assert.deepEqual(grants.data.grants, []);
	const created = await f.request('/v1/workspaces', {
		token, method: 'POST', body: { grantId: 'client-supplied', path: f.root }
	});
	assert.ok([403, 404].includes(created.status));
});

test('all file, workspace, session and terminal APIs require a valid bearer token', async (t) => {
	const f = await fixture(t);
	const requests = [
		['/v1/grants', 'GET'], ['/v1/workspaces', 'GET'],
		[directoryPath('unknown', '.'), 'GET'], [filePath('unknown', 'readme.txt'), 'GET'],
		['/v1/files', 'PUT'], ['/v1/workspaces', 'POST'], ['/v1/terminals', 'POST'],
		['/v1/terminals/unknown', 'GET'], ['/v1/terminals/unknown', 'DELETE'],
		['/v1/terminals/unknown/commands', 'POST'], ['/v1/session', 'DELETE']
	];
	for (const [path, method] of requests) {
		for (const token of [undefined, 'invalid-token']) {
			const response = await f.request(path, { method, token, ...(method === 'POST' || method === 'PUT' ? { body: {} } : {}) });
			assert.equal(response.status, 401, `${method} ${path} must reject ${token ?? 'missing'} token`);
		}
	}
});

test('pairing requires an exact allowed Origin and does not consume the code on denied origins', async (t) => {
	const f = await fixture(t);
	for (const origin of [null, 'null', 'https://attacker.example', `${ORIGIN}.attacker.example`, 'http://localhost:3301']) {
		const response = await f.request('/v1/pair', { method: 'POST', origin, body: { code: f.app.pairingCode } });
		assert.equal(response.status, 403, `Origin ${origin} must be denied`);
	}
	await f.pair();
});

test('CORS preflight permits only exact configured origins', async (t) => {
	const f = await fixture(t);
	const response = await f.request('/v1/pair', { method: 'OPTIONS' });
	assert.ok([200, 204].includes(response.status));
	assert.equal(response.headers.get('access-control-allow-origin'), ORIGIN);
	assert.match(response.headers.get('vary') ?? '', /Origin/i);
	for (const origin of ['null', 'https://attacker.example', 'http://localhost:3300.attacker.example']) {
		const denied = await f.request('/v1/pair', { method: 'OPTIONS', origin });
		assert.equal(denied.status, 403);
		assert.notEqual(denied.headers.get('access-control-allow-origin'), origin);
	}
});

test('Host header protection rejects DNS rebinding and wrong listening ports', async (t) => {
	const f = await fixture(t);
	for (const host of ['attacker.example', `attacker.example:${f.app.server.address().port}`, 'localhost:1']) {
		const response = await f.rawRequest('/v1/host', { Host: host, Origin: ORIGIN });
		assert.equal(response.status, 403, `Host ${host} must be denied`);
	}
});

test('pairing code is one-use and bad attempts lock it', async (t) => {
	const f = await fixture(t);
	await f.pair();
	const replay = await f.request('/v1/pair', { method: 'POST', body: { code: f.app.pairingCode } });
	assert.ok([401, 403].includes(replay.status));
	const locked = await fixture(t);
	for (let attempt = 0; attempt < 8; attempt += 1) {
		const response = await locked.request('/v1/pair', { method: 'POST', body: { code: 'wrong-pairing-code' } });
		assert.ok([401, 403, 429].includes(response.status));
	}
	const afterLock = await locked.request('/v1/pair', { method: 'POST', body: { code: locked.app.pairingCode } });
	assert.ok([401, 403, 429].includes(afterLock.status));
});

test('simultaneous pairing requests can consume a code only once', async (t) => {
	const f = await fixture(t);
	const attempt = () => f.request('/v1/pair', { method: 'POST', body: { code: f.app.pairingCode } });
	const responses = await Promise.all([attempt(), attempt()]);
	assert.deepEqual(responses.map((response) => response.status).sort(), [200, 403]);
});

test('expired pairing code and expired bearer token are rejected', async (t) => {
	const expiredPair = await fixture(t, { pairingTtlMs: 25 });
	await new Promise((resolve) => setTimeout(resolve, 60));
	const response = await expiredPair.request('/v1/pair', { method: 'POST', body: { code: expiredPair.app.pairingCode } });
	assert.ok([401, 403].includes(response.status));
	const expiredToken = await fixture(t, { tokenTtlMs: 35 });
	const token = await expiredToken.pair();
	await new Promise((resolve) => setTimeout(resolve, 70));
	assert.equal((await expiredToken.request('/v1/grants', { token })).status, 401);
});

test('bearer token is bound to its pairing Origin and companion instance', async (t) => {
	const f = await fixture(t, { origins: [ORIGIN, SECOND_ORIGIN] });
	const token = await f.pair();
	for (const origin of [null, 'null', SECOND_ORIGIN, 'https://attacker.example']) {
		assert.equal((await f.request('/v1/grants', { token, origin })).status, 403);
	}
	const secondHost = await fixture(t);
	assert.equal((await secondHost.request('/v1/grants', { token })).status, 401);
});

test('relative nested folders are selectable as host-identified project workspaces', async (t) => {
	const f = await fixture(t);
	const token = await f.pair();
	const grant = await f.grantFor(token);
	assert.deepEqual(grant.capabilities, { read: true, write: false, execute: false });
	const listed = await f.request(directoryPath(grant.id, '.'), { token });
	assert.equal(listed.status, 200);
	assert.ok(listed.data.entries.some((entry) => entry.name === 'nested' && entry.type === 'directory'));
	const nested = await f.request(filePath(grant.id, 'nested/note.txt'), { token });
	assert.equal(nested.status, 200);
	assert.equal(nested.data.content, 'nested content');
	const created = await f.request('/v1/workspaces', { token, method: 'POST', body: { grantId: grant.id, path: 'nested' } });
	assert.equal(created.status, 200);
	assert.equal(created.data.workspace.hostId, f.app.host.id);
	assert.equal(created.data.workspace.grantId, grant.id);
	const workspaces = await f.request('/v1/workspaces', { token });
	assert.equal(workspaces.status, 200);
	assert.equal(workspaces.data.workspaces[0].id, created.data.workspace.id);
});

test('paths, unknown grants, and chat-folder-like IDs never grant filesystem access', async (t) => {
	const f = await fixture(t);
	const token = await f.pair();
	for (const grantId of ['missing-grant', f.root, 'chat-folder-1']) {
		assert.ok([403, 404].includes((await f.request(filePath(grantId, 'readme.txt'), { token })).status));
	}
});

test('absolute, traversal, drive-relative, UNC and mixed-separator paths are rejected', async (t) => {
	const f = await fixture(t);
	const token = await f.pair();
	const grant = await f.grantFor(token);
	const paths = [
		'../outside/secret.txt', 'nested/../../outside/secret.txt', '..\\outside\\secret.txt',
		'nested/..\\..\\outside/secret.txt', '/etc/passwd', f.outside,
		'C:\\Windows\\win.ini', 'C:/Windows/win.ini', 'C:Windows\\win.ini',
		'\\\\server\\share\\secret.txt', '\\\\?\\C:\\Windows\\win.ini', '\\Windows\\win.ini',
		'readme.txt:stream', 'readme.txt\0suffix'
	];
	for (const path of paths) {
		for (const endpoint of [filePath(grant.id, path), directoryPath(grant.id, path)]) {
			const response = await f.request(endpoint, { token });
			assert.ok([400, 403, 404].includes(response.status), `Path ${JSON.stringify(path)} must be rejected`);
			assert.ok(!JSON.stringify(response.data).includes('outside secret'));
		}
	}
});

test('actual directory symlink or Windows junction cannot escape a grant', async (t) => {
	const f = await fixture(t);
	await symlink(f.outside, join(f.root, 'escape'), process.platform === 'win32' ? 'junction' : 'dir');
	const token = await f.pair();
	const grant = await f.grantFor(token);
	for (const path of ['escape', 'escape/secret.txt']) {
		const response = await f.request(path === 'escape' ? directoryPath(grant.id, path) : filePath(grant.id, path), { token });
		assert.equal(response.status, 403);
	}
	const workspace = await f.request('/v1/workspaces', { token, method: 'POST', body: { grantId: grant.id, path: 'escape' } });
	assert.equal(workspace.status, 403);
});

test('actual hardlinks to outside files cannot be read or written through a grant', async (t) => {
	const f = await fixture(t, { rootCapabilities: { write: true } });
	await link(join(f.outside, 'secret.txt'), join(f.root, 'linked-secret.txt'));
	const token = await f.pair();
	const grant = await f.grantFor(token);
	assert.equal((await f.request(filePath(grant.id, 'linked-secret.txt'), { token })).status, 403);
	const written = await f.request('/v1/files', {
		token, method: 'PUT', body: { grantId: grant.id, path: 'linked-secret.txt', content: 'changed' }
	});
	assert.equal(written.status, 403);
	assert.equal(await readFile(join(f.outside, 'secret.txt'), 'utf8'), 'outside secret');
});

test('replacing a granted root with another directory invalidates the grant', async (t) => {
	const f = await fixture(t);
	const token = await f.pair();
	const grant = await f.grantFor(token);
	await rename(f.root, join(f.temporary, 'original-project'));
	await mkdir(f.root);
	await writeFile(join(f.root, 'readme.txt'), 'replacement content', 'utf8');
	assert.equal((await f.request(filePath(grant.id, 'readme.txt'), { token })).status, 403);
});

test('file writes require an explicit host write capability and cannot create or escape', async (t) => {
	const readOnly = await fixture(t);
	const token = await readOnly.pair();
	const grant = await readOnly.grantFor(token);
	const denied = await readOnly.request('/v1/files', { token, method: 'PUT', body: { grantId: grant.id, path: 'readme.txt', content: 'changed' } });
	assert.equal(denied.status, 403);
	assert.equal(await readFile(join(readOnly.root, 'readme.txt'), 'utf8'), 'project content');
	const writable = await fixture(t, { rootCapabilities: { write: true } });
	const writeToken = await writable.pair();
	const writeGrant = await writable.grantFor(writeToken);
	assert.equal((await writable.request('/v1/files', { token: writeToken, method: 'PUT', body: { grantId: writeGrant.id, path: 'readme.txt', content: 'after' } })).status, 200);
	assert.equal(await readFile(join(writable.root, 'readme.txt'), 'utf8'), 'after');
	await symlink(writable.outside, join(writable.root, 'escape'), process.platform === 'win32' ? 'junction' : 'dir');
	for (const path of ['new-file.txt', '../outside/secret.txt', writable.outside]) {
		assert.ok([400, 403, 404].includes((await writable.request('/v1/files', { token: writeToken, method: 'PUT', body: { grantId: writeGrant.id, path, content: 'changed' } })).status));
	}
	assert.equal((await writable.request('/v1/files', { token: writeToken, method: 'PUT', body: { grantId: writeGrant.id, path: 'escape/secret.txt', content: 'changed' } })).status, 403);
	assert.equal(await readFile(join(writable.outside, 'secret.txt'), 'utf8'), 'outside secret');
});

test('host grant revocation immediately denies files and associated workspaces', async (t) => {
	const f = await executableFixture(t);
	await f.app.revokeGrant(f.grant.id);
	assert.equal((await f.request(filePath(f.grant.id, 'readme.txt'), { token: f.token })).status, 403);
	const command = await f.request(`/v1/terminals/${f.terminal.id}/commands`, {
		token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', 'console.log(1)'] }
	});
	assert.ok([403, 404, 409].includes(command.status));
});

test('terminal sessions do not enable command execution without host execution capability', async (t) => {
	const f = await fixture(t);
	const token = await f.pair();
	const grant = await f.grantFor(token);
	const selected = await f.request('/v1/workspaces', {
		token, method: 'POST', body: { grantId: grant.id, path: 'nested' }
	});
	assert.equal(selected.status, 200);
	const response = await f.request('/v1/terminals', {
		token, method: 'POST', body: { workspaceId: selected.data.workspace.id }
	});
	assert.equal(response.status, 403);
	assert.equal((await f.request('/v1/terminals/another-client-terminal', { token })).status, 404);
	assert.equal((await f.request('/v1/terminals/another-client-terminal', { token: 'another-client-token' })).status, 401);
});

test('explicit execution runs argv in the workspace and bounds output without shell interpolation', async (t) => {
	const f = await executableFixture(t);
	const script = "console.log(process.cwd()); console.log(process.argv[1]); console.log('x'.repeat(400000));";
	const response = await f.request(`/v1/terminals/${f.terminal.id}/commands`, {
		token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', script, 'literal; & echo unsafe'] }
	});
	assert.equal(response.status, 200);
	let terminal;
	await eventually(async () => {
		const polled = await f.request(`/v1/terminals/${f.terminal.id}`, { token: f.token });
		terminal = polled.data.terminal;
		return terminal.status === 'exited';
	}, 'Bounded command did not exit');
	assert.equal(terminal.exitCode, 0);
	assert.ok(Buffer.byteLength(terminal.output, 'utf8') <= 256 * 1024);
	assert.ok(terminal.output.includes('x'));
	const restarted = await f.request(`/v1/terminals/${f.terminal.id}/commands`, {
		token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', "console.log(process.cwd()); console.log(process.argv[1]);", 'literal; & echo unsafe'] }
	});
	assert.equal(restarted.status, 200);
	await eventually(async () => {
		terminal = (await f.request(`/v1/terminals/${f.terminal.id}`, { token: f.token })).data.terminal;
		return terminal.status === 'exited';
	}, 'Second argv command did not exit');
	assert.ok(terminal.output.includes(join(f.root, 'nested')));
	assert.ok(terminal.output.includes('literal; & echo unsafe'));
});

test('workspace cwd is revalidated when a selected directory becomes an escaping junction', async (t) => {
	const f = await executableFixture(t);
	await rename(join(f.root, 'nested'), join(f.root, 'original-nested'));
	await symlink(f.outside, join(f.root, 'nested'), process.platform === 'win32' ? 'junction' : 'dir');
	const response = await f.request(`/v1/terminals/${f.terminal.id}/commands`, {
		token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', 'console.log(process.cwd())'] }
	});
	assert.equal(response.status, 403);
});

test('terminal stop kills the command and its spawned descendant', async (t) => {
	const f = await executableFixture(t);
	const pids = await startProcessTree(f);
	const busy = await f.request(`/v1/terminals/${f.terminal.id}/commands`, { token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', 'console.log(1)'] } });
	assert.equal(busy.status, 409);
	const stopped = await f.request(`/v1/terminals/${f.terminal.id}`, { token: f.token, method: 'DELETE' });
	assert.equal(stopped.status, 200);
	assert.equal(stopped.data.terminal.status, 'closed');
	await assertTreeStopped(pids);
});

test('command timeout kills the complete process tree', async (t) => {
	const f = await executableFixture(t, { commandTimeoutMs: 2000 });
	const pids = await startProcessTree(f);
	await assertTreeStopped(pids);
});

test('host shutdown kills the complete process tree', async (t) => {
	const f = await executableFixture(t);
	const pids = await startProcessTree(f);
	await f.app.close();
	await assertTreeStopped(pids);
});

test('token revocation kills terminals and denies subsequent requests', async (t) => {
	const f = await executableFixture(t);
	const pids = await startProcessTree(f);
	const response = await f.request('/v1/session', { token: f.token, method: 'DELETE' });
	assert.equal(response.status, 200);
	assert.equal(response.data.revoked, true);
	assert.equal((await f.request('/v1/grants', { token: f.token })).status, 401);
	await assertTreeStopped(pids);
});

test('token expiry kills terminals without requiring another client request', async (t) => {
	const f = await executableFixture(t, { tokenTtlMs: 3000 });
	const pids = await startProcessTree(f);
	await assertTreeStopped(pids);
	assert.equal((await f.request('/v1/grants', { token: f.token })).status, 401);
});

test('a command exiting naturally does not leave an orphaned descendant running', async (t) => {
	const f = await executableFixture(t);
	const source = [
		"const { spawn } = require('node:child_process');",
		"const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });",
		"console.log(JSON.stringify({ parent: process.pid, child: child.pid }));",
		'setTimeout(() => process.exit(0), 200);'
	].join('\n');
	const response = await f.request(`/v1/terminals/${f.terminal.id}/commands`, {
		token: f.token, method: 'POST', body: { executable: process.execPath, args: ['-e', source] }
	});
	assert.equal(response.status, 200);
	let pids;
	await eventually(async () => {
		const polled = await f.request(`/v1/terminals/${f.terminal.id}`, { token: f.token });
		const match = polled.data.terminal.output.match(/\{"parent":\d+,"child":\d+\}/);
		if (!match) return false;
		pids = JSON.parse(match[0]);
		f.trackedProcesses.add(pids.parent);
		f.trackedProcesses.add(pids.child);
		return polled.data.terminal.status === 'exited';
	}, 'Natural parent exit did not complete');
	await assertTreeStopped(pids);
});

test('active terminal sessions and retained terminal records have bounded counts', async (t) => {
	const f = await executableFixture(t);
	const ids = [f.terminal.id];
	for (let index = 1; index < 8; index += 1) {
		const response = await f.request('/v1/terminals', { token: f.token, method: 'POST', body: { workspaceId: f.workspace.id } });
		assert.equal(response.status, 200);
		ids.push(response.data.terminal.id);
	}
	assert.equal((await f.request('/v1/terminals', { token: f.token, method: 'POST', body: { workspaceId: f.workspace.id } })).status, 429);
	for (const id of ids) assert.equal((await f.request(`/v1/terminals/${id}`, { token: f.token, method: 'DELETE' })).status, 200);
	for (let index = 8; index < 64; index += 1) {
		const response = await f.request('/v1/terminals', { token: f.token, method: 'POST', body: { workspaceId: f.workspace.id } });
		assert.equal(response.status, 200);
		assert.equal((await f.request(`/v1/terminals/${response.data.terminal.id}`, { token: f.token, method: 'DELETE' })).status, 200);
	}
	assert.equal((await f.request('/v1/terminals', { token: f.token, method: 'POST', body: { workspaceId: f.workspace.id } })).status, 429);
});

test('terminal idle expiry closes a session', async (t) => {
	const f = await executableFixture(t, { sessionIdleMs: 45 });
	await new Promise((resolve) => setTimeout(resolve, 100));
	const response = await f.request(`/v1/terminals/${f.terminal.id}`, { token: f.token });
	assert.ok(response.status === 404 || response.data.terminal?.status === 'closed');
});
