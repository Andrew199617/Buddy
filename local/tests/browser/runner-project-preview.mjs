/** Real disposable Runner projects; Buddy/provider APIs remain synthetic and blocked. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { createSession, origin, workspace } from './lazy-feature-fixture.mjs';

assert.ok(process.env.BUDDY_LAZY_TEST_ORIGIN, 'An explicitly owned preview origin is required');
const preview = new URL(origin);
assert.equal(preview.hostname, '127.0.0.1');
assert.ok(!['8081', '8082', '8083', '8765'].includes(preview.port), 'Do not test live services');
const python = process.env.BUDDY_RUNNER_TEST_PYTHON;
assert.ok(python, 'BUDDY_RUNNER_TEST_PYTHON must identify the existing test environment');
const commandNode = process.env.BUDDY_RUNNER_TEST_NODE || process.execPath;
const outputDirectory = resolve(workspace, 'local/tests/.qa/runner-projects');
const fixtureParent = resolve(outputDirectory, 'fixtures');
mkdirSync(fixtureParent, { recursive: true });
const results = [];

const fixtureScript = `
import asyncio, json, sys
from pathlib import Path
from aiohttp import web
from open_webui.utils.subscriptions import runner

async def main():
    base = Path(sys.argv[1])
    app = runner.build_app('fixture-runner-key', base / 'state', roots=[
        {'path': str(base / 'readonly'), 'name': 'Read-only', 'write': False, 'execute': False},
        {'path': str(base / 'project'), 'name': 'Editable', 'write': True, 'execute': True},
    ], origins=[sys.argv[2]], host_name='Disposable Runner PC')
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    capabilities = app['workspace_capabilities']
    print(json.dumps({'endpoint': 'http://127.0.0.1:' + str(port), 'code': capabilities.pairing_code, 'host': capabilities.host}), flush=True)
    try:
        await asyncio.to_thread(sys.stdin.readline)
    finally:
        await server.cleanup()

asyncio.run(main())
`;

async function waitUntil(predicate, message, timeout = 10000) {
	const until = Date.now() + timeout;
	while (Date.now() < until) {
		if (await predicate()) return;
		await new Promise((resolveWait) => setTimeout(resolveWait, 40));
	}
	throw new Error(message);
}

async function startFixture() {
	const directory = mkdtempSync(resolve(fixtureParent, 'runner-'));
	for (const name of ['readonly', 'project']) {
		mkdirSync(resolve(directory, name));
		writeFileSync(resolve(directory, name, 'notes.txt'), 'Original disposable project text\n');
		writeFileSync(resolve(directory, name, 'another.txt'), 'Another disposable file\n');
	}
	mkdirSync(resolve(directory, 'project/second'));
	const child = spawn(python, ['-B', '-c', fixtureScript, directory, origin], {
		cwd: workspace,
		env: { ...process.env, PYTHONPATH: resolve(workspace, 'backend'), PYTHONIOENCODING: 'utf-8' },
		windowsHide: true,
		stdio: ['pipe', 'pipe', 'pipe']
	});
	let stdout = '';
	let stderr = '';
	child.stdout.on('data', (value) => {
		stdout += value;
	});
	child.stderr.on('data', (value) => {
		stderr += value;
	});
	let launchError = null;
	child.on('error', (error) => {
		launchError = error;
	});
	const exit = new Promise((resolveExit) => child.once('exit', resolveExit));
	async function close() {
		if (child.exitCode === null && !child.killed) child.stdin.end('\n');
		await Promise.race([exit, new Promise((resolveWait) => setTimeout(resolveWait, 10000))]);
		if (child.exitCode === null && !child.killed) {
			child.kill();
			await exit;
		}
		const target = resolve(directory);
		assert.ok(
			target.startsWith(fixtureParent + sep),
			'Cleanup stays within disposable fixture parent'
		);
		rmSync(target, { recursive: true, force: true });
	}
	try {
		await waitUntil(
			() => {
				if (launchError) throw launchError;
				if (child.exitCode !== null) throw new Error('Fixture exited before ready: ' + stderr);
				return stdout.includes('\n');
			},
			'Disposable Runner did not become ready',
			20000
		);
		const banner = JSON.parse(stdout.slice(0, stdout.indexOf('\n')));
		return { ...banner, directory, close };
	} catch (error) {
		await close();
		throw error;
	}
}

async function makeSession(browser, profile, fixture, intercept = null, delayedPath = null) {
	const hostRequests = [];
	const unexpected = [];
	const subscriptionRequests = [];
	const session = await createSession(browser, {
		...profile,
		beforeNavigation: async (context) => {
			if (delayedPath) {
				await context.addInitScript(
					({ endpoint, path }) => {
						const nativeFetch = window.fetch.bind(window);
						let workspaceResponses = 0;
						window.fetch = async (...args) => {
							const response = await nativeFetch(...args);
							const requestUrl = args[0] instanceof Request ? args[0].url : String(args[0]);
							let matches = requestUrl === endpoint + path;
							let expectedMethod = 'POST';
							if (path === 'SECOND_WORKSPACE_PATH') {
								matches = requestUrl === endpoint + '/v1/workspaces' && args[1]?.method === 'POST';
								if (matches) matches = ++workspaceResponses === 2;
							}
							if (path === 'COMMAND_PATH') {
								matches =
									requestUrl.startsWith(endpoint + '/v1/terminals/') &&
									requestUrl.endsWith('/commands');
							}
							if (path === 'STOP_PATH') {
								matches =
									requestUrl.startsWith(endpoint + '/v1/terminals/') &&
									!requestUrl.endsWith('/commands');
								expectedMethod = 'DELETE';
							}
							if (path === 'BROWSE_PATH') {
								matches = requestUrl.startsWith(endpoint + '/v1/directories?');
								expectedMethod = 'GET';
							}
							if (matches && args[1]?.method === expectedMethod) {
								const nativeJson = response.json.bind(response);
								response.json = async () => {
									const body = await nativeJson();
									window.__qaDelayedBodyReady = true;
									await new Promise((release) => {
										window.__qaReleaseDelayedBody = release;
									});
									return body;
								};
							}
							return response;
						};
					},
					{ endpoint: fixture.endpoint, path: delayedPath }
				);
			}
			await context.route('**/*', async (route) => {
				const request = route.request();
				const url = new URL(request.url());
				if (url.origin === fixture.endpoint) {
					assert.ok(url.pathname.startsWith('/v1/'), 'Only project API on the disposable host');
					const headers = request.headers();
					assert.ok(!headers.cookie, 'Buddy cookies never go to Runner');
					assert.notEqual(headers.authorization, 'Bearer buddy-design-fake-token');
					assert.notEqual(headers.authorization, 'Bearer fixture-runner-key');
					hostRequests.push({
						method: request.method(),
						path: url.pathname,
						authorization: headers.authorization
					});
					if (intercept && (await intercept(route, url))) return;
					await route.continue();
					return;
				}
				if (url.origin !== origin) {
					unexpected.push(request.method() + ' ' + url.origin + url.pathname);
					await route.abort('blockedbyclient');
					return;
				}
				if (url.pathname.startsWith('/api/v1/subscriptions')) {
					subscriptionRequests.push(url.pathname);
					assert.equal(
						url.pathname,
						'/api/v1/subscriptions/machines',
						'Do not probe provider sign-in/status'
					);
					assert.equal(request.method(), 'GET');
					await route.fulfill({
						json: {
							machines: [
								{
									id: 'fixture-pc',
									name: 'Disposable Runner PC',
									url: 'http://host.docker.internal:8765',
									browser_url: fixture.endpoint
								},
								{
									id: 'backend-only',
									name: 'Backend-only computer',
									url: 'http://host.docker.internal:8765',
									browser_url: null
								}
							]
						}
					});
					return;
				}
				await route.fallback();
			});
		}
	});
	await session.page.goto(origin + '/companion', { waitUntil: 'domcontentloaded' });
	await session.page.locator('#companion-endpoint').waitFor();
	await session.page
		.locator('#runner-computer option[value="fixture-pc"]')
		.waitFor({ state: 'attached' });
	return { ...session, hostRequests, unexpected, subscriptionRequests };
}

async function inspectAndPair(page, fixture) {
	await page.locator('#runner-computer').selectOption('fixture-pc');
	await page.getByRole('button', { name: 'Inspect host', exact: true }).click();
	await page.getByText('Host ID: ' + fixture.host.id, { exact: true }).waitFor();
	await page.locator('#companion-code').fill(fixture.code);
	await page.getByRole('button', { name: 'Pair host', exact: true }).click();
	await page.locator('#companion-grant').waitFor();
}

function sessionAuthorization(requests) {
	return requests.find((request) => request.path === '/v1/grants' && request.method === 'GET')
		?.authorization;
}

async function checkLayout(session) {
	assert.deepEqual(session.errors, [], 'No uncaught page errors');
	assert.deepEqual(session.unexpected, [], 'No external or provider requests');
	assert.equal(session.completions.length, 0, 'No model completions');
	assert.equal(
		await session.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),
		false,
		'No horizontal page overflow'
	);
	assert.ok(!(await session.page.locator('body').innerText()).includes('#< CLIXML'));
}

async function lifecycle(session, fixture, profile) {
	const { page } = session;
	if (profile.mobile) {
		await page.locator('#sidebar-toggle-button').click();
		await waitUntil(
			() => page.evaluate(() => document.activeElement?.id === 'buddy-sidebar-close'),
			'Drawer receives focus'
		);
		assert.equal(
			await page.locator('#sidebar-toggle-button').count(),
			0,
			'Outside opener hidden with drawer open'
		);
		await page.keyboard.press('Escape');
		await waitUntil(
			() => page.evaluate(() => document.activeElement?.id === 'sidebar-toggle-button'),
			'Escape returns focus to project page opener'
		);
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').click();
		await waitUntil(
			() => page.evaluate(() => document.activeElement?.id === 'sidebar-toggle-button'),
			'Inside close returns focus to project page opener'
		);
	}
	assert.equal(await page.locator('#companion-endpoint').inputValue(), 'http://127.0.0.1:8765');
	assert.equal(
		session.hostRequests.length,
		0,
		'Model/Buddy identity gives no automatic project session'
	);
	await page.locator('#runner-computer').selectOption('backend-only');
	assert.equal(
		await page.locator('#companion-endpoint').inputValue(),
		'',
		'Never substitute host.docker.internal for a browser URL'
	);
	assert.equal(session.hostRequests.length, 0);
	await inspectAndPair(page, fixture);
	await page.getByRole('button', { name: 'File notes.txt', exact: true }).click();
	await page.locator('#companion-file-content').waitFor();
	assert.equal(
		await page.locator('#companion-file-content').evaluate((element) => element.readOnly),
		true
	);
	assert.equal(await page.getByText('Read-only text file', { exact: true }).count(), 1);
	const readGrant = await page.locator('#companion-grant').inputValue();
	const authorization = sessionAuthorization(session.hostRequests);
	assert.ok(authorization);
	const denied = await fetch(fixture.endpoint + '/v1/files', {
		method: 'PUT',
		headers: { Origin: origin, Authorization: authorization, 'Content-Type': 'application/json' },
		body: JSON.stringify({ grantId: readGrant, path: 'notes.txt', content: 'must not write' })
	});
	assert.equal(denied.status, 403, 'Actual host denies read-only writes');
	assert.equal(
		readFileSync(resolve(fixture.directory, 'readonly/notes.txt'), 'utf8'),
		'Original disposable project text\n'
	);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'File notes.txt', exact: true }).click();
	await page.locator('#companion-file-content').waitFor();
	assert.equal(
		await page.locator('#companion-file-content').evaluate((element) => element.readOnly),
		true,
		'Host write capability still needs explicit editing choice'
	);
	await page
		.getByLabel('Allow editing this file on Disposable Runner PC.', { exact: true })
		.check();
	await page
		.locator('#companion-file-content')
		.fill('Edited by disposable Runner browser fixture\n');
	await page.getByRole('button', { name: 'Save to execution host', exact: true }).click();
	await waitUntil(
		() =>
			readFileSync(resolve(fixture.directory, 'project/notes.txt'), 'utf8') ===
			'Edited by disposable Runner browser fixture\n',
		'Actual file edit persisted'
	);
	await page.getByRole('button', { name: 'File another.txt', exact: true }).click();
	await page.locator('#companion-file-content').waitFor();
	assert.equal(
		await page.locator('#companion-file-content').evaluate((element) => element.readOnly),
		true,
		'Editing acknowledgment cannot carry to another file'
	);
	assert.equal(
		await page
			.getByLabel('Allow editing this file on Disposable Runner PC.', { exact: true })
			.isChecked(),
		false
	);
	assert.equal(
		await page.getByRole('button', { name: 'Save to execution host', exact: true }).isDisabled(),
		true
	);
	await page.screenshot({ path: resolve(outputDirectory, profile.name + '-scoped-edit.png') });
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(
			JSON.stringify(['-e', "console.log('runner-fixture-command'); console.log(process.cwd());"])
		);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page.getByText('exited · exit 0', { exact: true }).waitFor();
	assert.ok(
		(await page.getByLabel('Command output', { exact: true }).innerText()).includes(
			resolve(fixture.directory, 'project')
		)
	);
	await page
		.locator('#companion-arguments')
		.fill(
			JSON.stringify(['-e', "console.log('runner-fixture-running'); setInterval(() => {}, 1000);"])
		);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page.getByText('running', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	const terminalRequest = session.hostRequests.findLast(
		(request) => request.method === 'DELETE' && request.path.startsWith('/v1/terminals/')
	);
	const stopped = await fetch(fixture.endpoint + terminalRequest.path, {
		headers: { Origin: origin, Authorization: authorization }
	});
	assert.equal((await stopped.json()).terminal.status, 'closed');
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await page
		.getByText('Disconnected. The host session and its commands were revoked.', { exact: true })
		.waitFor();
	assert.equal(
		(
			await fetch(fixture.endpoint + '/v1/grants', {
				headers: { Origin: origin, Authorization: authorization }
			})
		).status,
		401
	);
	assert.equal(await page.locator('#companion-file-content').count(), 0);
	assert.equal(await page.locator('#companion-executable').count(), 0);
	const storage = await page.evaluate(() =>
		JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } })
	);
	assert.ok(
		!storage.includes(authorization.slice('Bearer '.length)),
		'Project token never enters browser storage'
	);
	await checkLayout(session);
	await page.screenshot({ path: resolve(outputDirectory, profile.name + '-revoked.png') });
}

async function capabilityMismatch(session) {
	const { page } = session;
	await page.locator('#runner-computer').selectOption('fixture-pc');
	await page.getByRole('button', { name: 'Inspect host', exact: true }).click();
	await page
		.getByRole('alert')
		.filter({ hasText: 'does not support Buddy Runner projects protocol version 1' })
		.waitFor();
	assert.equal(session.hostRequests.filter((request) => request.path === '/v1/pair').length, 0);
	assert.equal(
		await page.getByRole('button', { name: 'Pair host', exact: true }).isDisabled(),
		true
	);
	await checkLayout(session);
}

async function changedPair(session, fixture) {
	const { page } = session;
	await page.locator('#runner-computer').selectOption('fixture-pc');
	await page.getByRole('button', { name: 'Inspect host', exact: true }).click();
	await page.getByText('Host ID: ' + fixture.host.id, { exact: true }).waitFor();
	await page.locator('#companion-code').fill(fixture.code);
	await page.getByRole('button', { name: 'Pair host', exact: true }).click();
	await page.getByRole('alert').filter({ hasText: 'computer changed during pairing' }).waitFor();
	await waitUntil(
		() =>
			session.hostRequests.some(
				(request) => request.path === '/v1/session' && request.method === 'DELETE'
			),
		'Rejected pairing token is revoked'
	);
	assert.equal(await page.locator('#companion-grant').count(), 0);
	assert.equal(await page.getByText('Host ID: ' + fixture.host.id, { exact: true }).count(), 0);
	const rejectedToken = session.hostRequests.find(
		(request) => request.path === '/v1/session' && request.method === 'DELETE'
	).authorization;
	assert.equal(
		(
			await fetch(fixture.endpoint + '/v1/grants', {
				headers: { Origin: origin, Authorization: rejectedToken }
			})
		).status,
		401
	);
	await checkLayout(session);
}

async function lateFile(session, fixture, delayed) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.getByRole('button', { name: 'File notes.txt', exact: true }).click();
	await waitUntil(() => delayed.held !== null, 'File response held');
	await page.locator('#runner-computer').selectOption('backend-only');
	delayed.release();
	await page.waitForTimeout(150);
	assert.equal(
		await page.locator('#companion-file-content').count(),
		0,
		'Old computer response cannot restore file or approvals'
	);
	assert.equal(await page.locator('#companion-grant').count(), 0);
	assert.equal(await page.locator('#companion-endpoint').inputValue(), '');
	await checkLayout(session);
}

async function releaseDelayedBody(page) {
	await page.evaluate(() => {
		window.__qaReleaseDelayedBody?.();
	});
}

async function verifyRevoked(session, fixture, authorization) {
	assert.ok(authorization, 'A real disposable project session token is required');
	await waitUntil(
		() =>
			session.hostRequests.some(
				(request) => request.path === '/v1/session' && request.method === 'DELETE'
			),
		'Old computer session revoked'
	);
	await waitUntil(
		async () =>
			(
				await fetch(fixture.endpoint + '/v1/grants', {
					headers: { Origin: origin, Authorization: authorization }
				})
			).status === 401,
		'Actual old host rejects the revoked session'
	);
}

async function latePair(session, fixture) {
	const { page } = session;
	await page.locator('#runner-computer').selectOption('fixture-pc');
	await page.getByRole('button', { name: 'Inspect host', exact: true }).click();
	await page.getByText('Host ID: ' + fixture.host.id, { exact: true }).waitFor();
	await page.locator('#companion-code').fill(fixture.code);
	await page.getByRole('button', { name: 'Pair host', exact: true }).click();
	await page.waitForFunction(() => window.__qaDelayedBodyReady === true);
	await page.locator('#runner-computer').selectOption('backend-only');
	await releaseDelayedBody(page);
	await waitUntil(
		() =>
			session.hostRequests.some(
				(request) => request.path === '/v1/session' && request.method === 'DELETE'
			),
		'Late accepted pairing credential is revoked'
	);
	const authorization = session.hostRequests.find(
		(request) => request.path === '/v1/session' && request.method === 'DELETE'
	).authorization;
	await verifyRevoked(session, fixture, authorization);
	assert.equal(await page.locator('#companion-grant').count(), 0);
	assert.equal(
		await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).count(),
		0
	);
	assert.equal(await page.locator('#companion-endpoint').inputValue(), '');
	await checkLayout(session);
}

async function lateWorkspace(session, fixture) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	const authorization = sessionAuthorization(session.hostRequests);
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.waitForFunction(() => window.__qaDelayedBodyReady === true);
	await page.locator('#runner-computer').selectOption('backend-only');
	await releaseDelayedBody(page);
	await verifyRevoked(session, fixture, authorization);
	assert.equal(await page.locator('.selected-project').count(), 0);
	assert.equal(await page.locator('#companion-grant').count(), 0);
	assert.equal(await page.locator('#companion-file-content').count(), 0);
	await checkLayout(session);
}

async function workspaceTransition(session, fixture) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Folder second', exact: true }).click();
	const workspaceResponse = page.waitForResponse(
		(response) =>
			response.url() === fixture.endpoint + '/v1/workspaces' &&
			response.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.waitForFunction(() => window.__qaDelayedBodyReady === true);
	const selectedWorkspace = (await (await workspaceResponse).json()).workspace;
	const acknowledgment = page.getByLabel('I understand execution has this host-level access.', {
		exact: true
	});
	const createSession = page.getByRole('button', { name: 'Create command session', exact: true });
	assert.equal(
		await acknowledgment.isDisabled(),
		true,
		'No execution consent during project transition'
	);
	assert.equal(
		await createSession.isDisabled(),
		true,
		'No terminal creation during project transition'
	);
	assert.equal(await page.locator('#companion-executable').count(), 0);
	// Dispatch queued events as well: handler guards must protect against an earlier UI event.
	await acknowledgment.evaluate((element) => {
		element.checked = true;
		element.dispatchEvent(new Event('change', { bubbles: true }));
	});
	await createSession.dispatchEvent('click');
	await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 100)));
	assert.equal(
		session.hostRequests.filter(
			(request) => request.method === 'POST' && request.path === '/v1/terminals'
		).length,
		0,
		'Pending project selection cannot create a terminal in the old project'
	);
	assert.equal(
		session.hostRequests.filter(
			(request) => request.method === 'POST' && request.path.endsWith('/commands')
		).length,
		0,
		'Pending project selection cannot start a command in the old project'
	);
	await releaseDelayedBody(page);
	await page.getByText('Selected project: second', { exact: true }).waitFor();
	assert.equal(
		await acknowledgment.isChecked(),
		false,
		'The new project needs fresh execution consent'
	);
	await acknowledgment.check();
	const terminalResponse = page.waitForResponse(
		(response) =>
			response.url() === fixture.endpoint + '/v1/terminals' &&
			response.request().method() === 'POST'
	);
	await createSession.click();
	const createdTerminal = (await (await terminalResponse).json()).terminal;
	assert.equal(
		createdTerminal.workspaceId,
		selectedWorkspace.id,
		'Terminal belongs to the confirmed project'
	);
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(JSON.stringify(['-e', 'console.log(process.cwd())']));
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page.getByText('exited · exit 0', { exact: true }).waitFor();
	const actualTerminal = await fetch(fixture.endpoint + '/v1/terminals/' + createdTerminal.id, {
		headers: { Origin: origin, Authorization: sessionAuthorization(session.hostRequests) }
	});
	assert.equal(actualTerminal.status, 200);
	assert.ok(
		(await actualTerminal.json()).terminal.output.includes(
			resolve(fixture.directory, 'project/second')
		),
		'Actual command runs in the confirmed new directory'
	);
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	const authorization = sessionAuthorization(session.hostRequests);
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function lateCommand(session, fixture, unmount) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(
			JSON.stringify(['-e', "console.log('held-fixture-command'); setInterval(() => {}, 1000);"])
		);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page.waitForFunction(() => window.__qaDelayedBodyReady === true);
	const authorization = sessionAuthorization(session.hostRequests);
	if (unmount) {
		await page.evaluate(() => {
			const link = document.createElement('a');
			link.id = 'qa-return-to-chat';
			link.href = '/c/buddy-design-chat';
			link.textContent = 'Return to synthetic chat';
			link.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;background:white;color:black';
			document.body.append(link);
		});
		await page.locator('#qa-return-to-chat').click();
		await page.locator('#chat-input').waitFor();
	} else {
		await page.locator('#runner-computer').selectOption('backend-only');
	}
	await releaseDelayedBody(page);
	await verifyRevoked(session, fixture, authorization);
	assert.equal(
		await page.locator('#companion-executable').count(),
		0,
		'Late accepted command cannot restore the old terminal'
	);
	assert.equal(await page.locator('.selected-project').count(), 0);
	await checkLayout(session);
}

async function prepareRunningCommand(session, fixture) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(
			JSON.stringify(['-e', "console.log('stop-retry-fixture'); setInterval(() => {}, 1000);"])
		);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page.getByText('running', { exact: true }).waitFor();
	return sessionAuthorization(session.hostRequests);
}

async function stopRetry(session, fixture, mode) {
	const { page } = session;
	const authorization = await prepareRunningCommand(session, fixture);
	const oldGrant = await page.locator('#companion-grant').inputValue();
	async function attemptStop() {
		if (mode === 'grant-stop-retry')
			await page.locator('#companion-grant').selectOption({ label: 'Read-only' });
		else if (mode === 'workspace-stop-retry')
			await page
				.getByRole('button', { name: 'Use this directory as project', exact: true })
				.click();
		else await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	}
	await attemptStop();
	await page.getByRole('alert').filter({ hasText: 'Stop was not confirmed' }).waitFor();
	assert.equal(
		await page.locator('#companion-executable').count(),
		1,
		'Unconfirmed stop retains command handle'
	);
	assert.equal(
		await page.getByRole('button', { name: 'Run command', exact: true }).isDisabled(),
		true
	);
	assert.equal(
		await page.getByRole('button', { name: 'Create command session', exact: true }).count(),
		0
	);
	assert.equal(
		await page.locator('#companion-grant').inputValue(),
		oldGrant,
		'Grant changes wait for confirmed stop'
	);
	const terminalPath = session.hostRequests.findLast(
		(request) => request.method === 'DELETE' && request.path.startsWith('/v1/terminals/')
	).path;
	const running = await fetch(fixture.endpoint + terminalPath, {
		headers: { Origin: origin, Authorization: authorization }
	});
	assert.equal(
		(await running.json()).terminal.status,
		'running',
		'Actual command still runs after injected transport failure'
	);
	await attemptStop();
	await waitUntil(
		() =>
			page
				.locator('#companion-executable')
				.count()
				.then((count) => count === 0),
		'Retry confirms stop before discarding handle'
	);
	const stopped = await fetch(fixture.endpoint + terminalPath, {
		headers: { Origin: origin, Authorization: authorization }
	});
	assert.equal((await stopped.json()).terminal.status, 'closed');
	if (mode === 'grant-stop-retry')
		assert.notEqual(await page.locator('#companion-grant').inputValue(), oldGrant);
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function delayedStop(session, fixture) {
	const { page } = session;
	const authorization = await prepareRunningCommand(session, fixture);
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.waitForFunction(() => window.__qaDelayedBodyReady === true);
	assert.equal(
		await page.locator('#companion-executable').count(),
		1,
		'Stop handle remains until closed acknowledgment is received'
	);
	assert.equal(await page.locator('.terminal-panel button[type="submit"]').isDisabled(), true);
	assert.equal(
		await page.getByRole('button', { name: 'Create command session', exact: true }).count(),
		0
	);
	assert.equal(await page.locator('#companion-grant').isDisabled(), true);
	const terminalPath = session.hostRequests.findLast(
		(request) => request.method === 'DELETE' && request.path.startsWith('/v1/terminals/')
	).path;
	const stopped = await fetch(fixture.endpoint + terminalPath, {
		headers: { Origin: origin, Authorization: authorization }
	});
	assert.equal(
		(await stopped.json()).terminal.status,
		'closed',
		'Actual host stopped before acknowledgment delivery'
	);
	await releaseDelayedBody(page);
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	assert.equal(await page.locator('#companion-executable').count(), 0);
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function disconnectFailure(session, fixture) {
	const { page } = session;
	await page.locator('#runner-computer').selectOption('fixture-pc');
	await page.getByRole('button', { name: 'Inspect host', exact: true }).click();
	await page.getByText('Host ID: ' + fixture.host.id, { exact: true }).waitFor();
	await page.locator('#companion-code').fill(fixture.code);
	await page.getByRole('button', { name: 'Pair host', exact: true }).click();
	await page.waitForFunction(() => window.__qaDelayedBodyReady === true);
	const authorization = sessionAuthorization(session.hostRequests);
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await page.getByRole('alert').filter({ hasText: 'host could not confirm revocation' }).waitFor();
	assert.equal(
		await page
			.getByText('Disconnected. The host session and its commands were revoked.', { exact: true })
			.count(),
		0,
		'Duplicate disconnect cannot falsely report successful revocation'
	);
	assert.equal(
		session.hostRequests.filter(
			(request) => request.method === 'DELETE' && request.path === '/v1/session'
		).length,
		1,
		'One shared revocation request'
	);
	assert.equal(
		(
			await fetch(fixture.endpoint + '/v1/grants', {
				headers: { Origin: origin, Authorization: authorization }
			})
		).status,
		200,
		'Injected failed transport did not reach actual host revocation'
	);
	await releaseDelayedBody(page);
	assert.equal(await page.locator('#companion-grant').count(), 0);
	await checkLayout(session);
}

async function operationConflict(session, fixture) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'File notes.txt', exact: true }).click();
	await page
		.getByLabel('Allow editing this file on Disposable Runner PC.', { exact: true })
		.check();
	const draft = 'Unsaved disposable draft survives operation conflict';
	await page.locator('#companion-file-content').fill(draft);
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	const createdResponse = page.waitForResponse(
		(response) =>
			response.url() === fixture.endpoint + '/v1/terminals' &&
			response.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	const created = await (await createdResponse).json();
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(JSON.stringify(['-e', "console.log('must-not-run-during-conflict')"]));
	const authorization = sessionAuthorization(session.hostRequests);
	const commandPath = '/v1/terminals/' + encodeURIComponent(created.terminal.id) + '/commands';
	const started = await fetch(fixture.endpoint + commandPath, {
		method: 'POST',
		headers: { Origin: origin, Authorization: authorization, 'Content-Type': 'application/json' },
		body: JSON.stringify({
			executable: commandNode,
			args: ['-e', "console.log('actual-busy-fixture'); setInterval(() => {}, 1000);"]
		})
	});
	assert.equal(started.status, 200);
	assert.equal((await started.json()).terminal.status, 'running');
	const conflictResponse = page.waitForResponse(
		(response) =>
			response.url() === fixture.endpoint + commandPath && response.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	assert.equal(
		(await conflictResponse).status(),
		409,
		'Actual Runner rejects a second command while running'
	);
	await page
		.getByRole('alert')
		.filter({ hasText: /already running/i })
		.waitFor();
	assert.equal(await page.locator('#companion-file-content').inputValue(), draft);
	assert.equal(
		await page
			.getByLabel('Allow editing this file on Disposable Runner PC.', { exact: true })
			.isChecked(),
		true
	);
	assert.equal(await page.locator('#companion-grant').count(), 1);
	assert.equal(
		session.hostRequests.filter(
			(request) => request.path === '/v1/session' && request.method === 'DELETE'
		).length,
		0
	);
	assert.equal(
		await page.getByRole('button', { name: 'Stop and remove session', exact: true }).isDisabled(),
		false
	);
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	assert.equal(await page.locator('#companion-file-content').inputValue(), draft);
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function transientPoll(session, fixture) {
	const { page } = session;
	const authorization = await prepareRunningCommand(session, fixture);
	await page
		.getByRole('alert')
		.filter({ hasText: 'Command status is temporarily unavailable' })
		.waitFor();
	await waitUntil(
		() =>
			session.hostRequests.filter(
				(request) => request.method === 'GET' && request.path.startsWith('/v1/terminals/')
			).length >= 2,
		'Transient failed poll is retried'
	);
	await waitUntil(
		() =>
			page
				.getByRole('alert')
				.count()
				.then((count) => count === 0),
		'Successful real poll clears retry message'
	);
	assert.equal(
		await page.getByRole('button', { name: 'Stop and remove session', exact: true }).isDisabled(),
		false
	);
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function uncertainCommand(session, fixture, allowStatusRecovery, profile, scenario) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'File notes.txt', exact: true }).click();
	await page
		.getByLabel('Allow editing this file on Disposable Runner PC.', { exact: true })
		.check();
	await page.locator('#companion-file-content').fill('Unsaved command recovery draft\n');
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(
			JSON.stringify([
				'-e',
				"console.log('accepted-with-lost-response'); setInterval(() => {}, 1000);"
			])
		);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page.getByText('status unconfirmed', { exact: true }).waitFor();
	await page
		.getByRole('alert')
		.filter({ hasText: 'Command acceptance is still unconfirmed' })
		.waitFor();
	assert.equal(await page.locator('.terminal-panel button[type="submit"]').isDisabled(), true);
	assert.equal(
		await page.getByRole('button', { name: 'Stop and remove session', exact: true }).isDisabled(),
		false
	);
	assert.equal(
		await page.locator('#companion-file-content').inputValue(),
		'Unsaved command recovery draft\n'
	);
	await page.locator('.terminal-panel form').dispatchEvent('submit');
	await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 100)));
	assert.equal(
		session.hostRequests.filter(
			(request) => request.method === 'POST' && request.path.endsWith('/commands')
		).length,
		1,
		'An unconfirmed accepted command cannot be submitted twice'
	);
	const authorization = sessionAuthorization(session.hostRequests);
	const commandRequest = session.hostRequests.find(
		(request) => request.method === 'POST' && request.path.endsWith('/commands')
	);
	const running = await fetch(fixture.endpoint + commandRequest.path.replace(/\/commands$/, ''), {
		headers: { Origin: origin, Authorization: authorization }
	});
	assert.equal(
		(await running.json()).terminal.status,
		'running',
		'Actual Runner accepted the request before response loss'
	);
	await page.screenshot({ path: resolve(outputDirectory, profile.name + '-' + scenario + '.png') });
	allowStatusRecovery();
	await page.getByRole('button', { name: 'Check command status', exact: true }).click();
	await page.getByText('running', { exact: true }).waitFor();
	assert.equal(await page.getByText('status unconfirmed', { exact: true }).count(), 0);
	assert.equal(await page.getByRole('alert').count(), 0);
	assert.equal(
		await page.locator('#companion-file-content').inputValue(),
		'Unsaved command recovery draft\n'
	);
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function recoveryOverlap(session, fixture, delayed, profile, scenario) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	await page.locator('#companion-executable').fill(commandNode);
	await page
		.locator('#companion-arguments')
		.fill(
			JSON.stringify([
				'-e',
				"console.log('recovery-overlap'); const timer = setInterval(() => { if (require('node:fs').existsSync('finish-command')) { clearInterval(timer); process.exit(0); } }, 25);"
			])
		);
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await waitUntil(
		() => delayed.held !== null,
		'Automatic recovery reads actual running state before its reply is held'
	);
	const authorization = sessionAuthorization(session.hostRequests);
	const commandRequest = session.hostRequests.find(
		(request) => request.method === 'POST' && request.path.endsWith('/commands')
	);
	const terminalPath = commandRequest.path.replace(/\/commands$/, '');
	writeFileSync(resolve(fixture.directory, 'project/finish-command'), 'finish disposable command');
	await waitUntil(async () => {
		const response = await fetch(fixture.endpoint + terminalPath, {
			headers: { Origin: origin, Authorization: authorization }
		});
		return (await response.json()).terminal.status === 'exited';
	}, 'Actual disposable command exits before manual status check');
	await page.getByRole('button', { name: 'Check command status', exact: true }).click();
	await page
		.getByRole('alert')
		.filter({ hasText: 'The command request remains unconfirmed' })
		.waitFor();
	const recoveryMessage = await page.getByRole('alert').innerText();
	delayed.release();
	await waitUntil(() => delayed.completed === true, 'Older automatic status response is released');
	await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 1000)));
	assert.equal(
		await page.getByText('status unconfirmed', { exact: true }).count(),
		1,
		'Older running state cannot replace the newer uncertainty decision'
	);
	assert.equal(
		await page.getByRole('alert').innerText(),
		recoveryMessage,
		'Older status failure cannot replace newer manual recovery'
	);
	assert.equal(
		await page.getByRole('button', { name: 'Run command', exact: true }).isDisabled(),
		true
	);
	await page.screenshot({ path: resolve(outputDirectory, profile.name + '-' + scenario + '.png') });
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

async function delayedCommandAdmission(session, fixture, admission, profile, scenario) {
	const { page } = session;
	await inspectAndPair(page, fixture);
	await page.locator('#companion-grant').selectOption({ label: 'Editable' });
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.getByText('Selected project: project', { exact: true }).waitFor();
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	await page.locator('#companion-executable').fill(commandNode);
	let previousStatus = 'ready';
	let expectedCommandRequests = 1;
	if (scenario === 'delayed-command-admission-exited') {
		await page
			.locator('#companion-arguments')
			.fill(JSON.stringify(['-e', "console.log('prior-acknowledged-command')"]));
		await page.getByRole('button', { name: 'Run command', exact: true }).click();
		await page.getByText('exited · exit 0', { exact: true }).waitFor();
		assert.equal(
			await page.getByRole('button', { name: 'Run command', exact: true }).isDisabled(),
			false,
			'A valid acknowledged command remains reusable'
		);
		previousStatus = 'exited';
		expectedCommandRequests = 2;
	}
	let code = "console.log('delayed-admission'); setInterval(() => {}, 1000);";
	if (scenario === 'delayed-command-admission-fast')
		code = "console.log('fast-unacknowledged-command')";
	await page.locator('#companion-arguments').fill(JSON.stringify(['-e', code]));
	await page.getByRole('button', { name: 'Run command', exact: true }).click();
	await page
		.getByRole('alert')
		.filter({ hasText: 'The command request remains unconfirmed' })
		.waitFor();
	assert.ok(admission.request, 'Disposable request is queued before actual Runner admission');
	const authorization = sessionAuthorization(session.hostRequests);
	assert.equal(admission.request.authorization, authorization);
	const terminalPath = admission.request.path.replace(/\/commands$/, '');
	const snapshot = await fetch(fixture.endpoint + terminalPath, {
		headers: { Origin: origin, Authorization: authorization }
	});
	assert.equal(
		(await snapshot.json()).terminal.status,
		previousStatus,
		'Actual ready or prior exited snapshot precedes admission of the queued new command'
	);
	assert.equal(
		await page.getByRole('button', { name: 'Run command', exact: true }).isDisabled(),
		true
	);
	await page.locator('.terminal-panel form').dispatchEvent('submit');
	await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 100)));
	assert.equal(
		session.hostRequests.filter(
			(request) => request.method === 'POST' && request.path.endsWith('/commands')
		).length,
		expectedCommandRequests,
		'An unrelated ready/exited snapshot cannot permit resubmission'
	);
	admission.delivering = true;
	const acknowledged = await fetch(fixture.endpoint + admission.request.path, {
		method: 'POST',
		headers: { Origin: origin, Authorization: authorization, 'Content-Type': 'application/json' },
		body: JSON.stringify(admission.request.body)
	});
	assert.equal(
		acknowledged.status,
		200,
		'Original queued request is admitted by the actual disposable Runner'
	);
	const acceptedTerminal = (await acknowledged.json()).terminal;
	if (scenario === 'delayed-command-admission-fast') {
		await waitUntil(async () => {
			const response = await fetch(fixture.endpoint + terminalPath, {
				headers: { Origin: origin, Authorization: authorization }
			});
			const actual = (await response.json()).terminal;
			return actual.status === 'exited' && actual.exitCode === 0;
		}, 'Fast admitted job finishes before the browser observes its running state');
	}
	admission.delivered = true;
	for (const release of admission.statusWaiters.splice(0)) release();
	await page.getByRole('button', { name: 'Check command status', exact: true }).click();
	if (scenario === 'delayed-command-admission-fast') {
		await waitUntil(
			async () =>
				(await page.getByLabel('Command output', { exact: true }).innerText()).includes(
					'fast-unacknowledged-command'
				),
			'The browser inspects actual fast-job completion without observing running'
		);
		await page
			.getByRole('alert')
			.filter({ hasText: 'The command request remains unconfirmed' })
			.waitFor();
		assert.equal(await page.getByText('status unconfirmed', { exact: true }).count(), 1);
		assert.equal(
			await page.getByRole('button', { name: 'Run command', exact: true }).isDisabled(),
			true,
			'A fast lost-ack job requires Stop/new session when running was never observed'
		);
	} else {
		await page.getByText('running', { exact: true }).waitFor();
		assert.equal(
			await page.getByRole('alert').count(),
			0,
			'Observing the newly admitted running job resolves submission uncertainty'
		);
	}
	await page.screenshot({ path: resolve(outputDirectory, profile.name + '-' + scenario + '.png') });
	await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
	await page.getByText('The command session was stopped and removed.', { exact: true }).waitFor();
	await page
		.getByLabel('I understand execution has this host-level access.', { exact: true })
		.check();
	const freshResponse = page.waitForResponse(
		(response) =>
			response.url() === fixture.endpoint + '/v1/terminals' &&
			response.request().method() === 'POST'
	);
	await page.getByRole('button', { name: 'Create command session', exact: true }).click();
	const freshTerminal = (await (await freshResponse).json()).terminal;
	assert.notEqual(
		freshTerminal.id,
		acceptedTerminal.id,
		'Confirmed Stop permits a fresh command session'
	);
	await page.locator('#companion-executable').waitFor();
	await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
	await verifyRevoked(session, fixture, authorization);
	await checkLayout(session);
}

const { chromium } = await import(process.env.BUDDY_PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true });
try {
	for (const profile of [
		{ name: 'desktop', mobile: false, theme: 'light' },
		{ name: 'phone', mobile: true, theme: 'dark' }
	]) {
		for (const scenario of [
			'lifecycle',
			'capability-mismatch',
			'changed-pair',
			'late-file',
			'late-pair',
			'late-workspace',
			'workspace-transition',
			'late-command-switch',
			'late-command-unmount',
			'stop-retry',
			'grant-stop-retry',
			'workspace-stop-retry',
			'stop-delay',
			'disconnect-failure',
			'operation-conflict',
			'uncertain-command-transport',
			'uncertain-command-http',
			'uncertain-command-json',
			'uncertain-command-shape',
			'recovery-overlap-running',
			'recovery-overlap-error',
			'delayed-command-admission-ready',
			'delayed-command-admission-exited',
			'delayed-command-admission-fast',
			'transient-poll'
		]) {
			let fixture = null;
			let session = null;
			const delayed = { held: null, release: () => {} };
			const admission = { request: null, delivering: false, delivered: false, statusWaiters: [] };
			try {
				fixture = await startFixture();
				let injectedStopFailure = false;
				let injectedPollFailure = false;
				let allowStatusRecovery = false;
				let recoveryReads = 0;
				let admissionSubmissions = 0;
				const interceptor = async (route, url) => {
					if (
						scenario.startsWith('delayed-command-admission-') &&
						route.request().method() === 'POST' &&
						url.pathname.endsWith('/commands')
					) {
						admissionSubmissions += 1;
						if (scenario === 'delayed-command-admission-exited' && admissionSubmissions === 1)
							return false;
						admission.request = {
							path: url.pathname,
							body: route.request().postDataJSON(),
							authorization: route.request().headers().authorization
						};
						await route.fulfill({
							status: 503,
							json: { error: 'Synthetic request transport failed before queued Runner admission' }
						});
						return true;
					}
					if (
						scenario === 'delayed-command-admission-fast' &&
						admission.delivering &&
						!admission.delivered &&
						route.request().method() === 'GET' &&
						url.pathname.startsWith('/v1/terminals/')
					) {
						await new Promise((release) => admission.statusWaiters.push(release));
					}
					if (
						scenario.startsWith('recovery-overlap-') &&
						route.request().method() === 'POST' &&
						url.pathname.endsWith('/commands')
					) {
						const response = await route.fetch();
						assert.equal(response.status(), 200);
						await route.abort('failed');
						return true;
					}
					if (
						scenario.startsWith('recovery-overlap-') &&
						route.request().method() === 'GET' &&
						url.pathname.startsWith('/v1/terminals/')
					) {
						recoveryReads += 1;
						if (recoveryReads === 1) {
							await route.fulfill({ status: 503, json: { error: 'Initial recovery unavailable' } });
							return true;
						}
						if (recoveryReads === 2) {
							const response = await route.fetch();
							assert.equal((await response.json()).terminal.status, 'running');
							await new Promise((release) => {
								delayed.release = release;
								delayed.held = route;
							});
							if (scenario === 'recovery-overlap-error')
								await route.fulfill({
									status: 503,
									json: { error: 'Older automatic recovery failed' }
								});
							else await route.fulfill({ response });
							delayed.completed = true;
							return true;
						}
					}
					if (
						scenario.startsWith('uncertain-command-') &&
						route.request().method() === 'POST' &&
						url.pathname.endsWith('/commands')
					) {
						const response = await route.fetch();
						assert.equal(
							response.status(),
							200,
							'Actual disposable command starts before reply loss'
						);
						if (scenario === 'uncertain-command-transport') await route.abort('failed');
						else if (scenario === 'uncertain-command-json')
							await route.fulfill({ status: 200, contentType: 'application/json', body: '{' });
						else if (scenario === 'uncertain-command-shape')
							await route.fulfill({ status: 200, json: {} });
						else
							await route.fulfill({
								status: 503,
								json: { error: 'Injected lost command reply after actual acceptance' }
							});
						return true;
					}
					if (
						scenario.startsWith('uncertain-command-') &&
						!allowStatusRecovery &&
						route.request().method() === 'GET' &&
						url.pathname.startsWith('/v1/terminals/')
					) {
						if (scenario === 'uncertain-command-shape')
							await route.fulfill({ status: 200, json: { terminal: { status: 'unknown' } } });
						else
							await route.fulfill({
								status: 503,
								json: { error: 'Injected unavailable command status' }
							});
						return true;
					}
					if (
						scenario === 'disconnect-failure' &&
						route.request().method() === 'DELETE' &&
						url.pathname === '/v1/session'
					) {
						await route.fulfill({
							status: 503,
							json: { error: 'Injected disposable revocation transport failure' }
						});
						return true;
					}
					if (
						scenario === 'transient-poll' &&
						!injectedPollFailure &&
						route.request().method() === 'GET' &&
						url.pathname.startsWith('/v1/terminals/')
					) {
						injectedPollFailure = true;
						await route.fulfill({
							status: 503,
							json: { error: 'Injected disposable poll transport failure' }
						});
						return true;
					}
					if (
						scenario.endsWith('stop-retry') &&
						!injectedStopFailure &&
						route.request().method() === 'DELETE' &&
						url.pathname.startsWith('/v1/terminals/')
					) {
						injectedStopFailure = true;
						await route.fulfill({
							status: 503,
							json: { error: 'Injected disposable stop transport failure' }
						});
						return true;
					}
					if (scenario === 'capability-mismatch' && url.pathname === '/v1/capabilities') {
						const response = await route.fetch();
						await route.fulfill({
							response,
							json: { ...(await response.json()), protocol: 'incompatible' }
						});
						return true;
					}
					if (scenario === 'changed-pair' && url.pathname === '/v1/pair') {
						const response = await route.fetch();
						const body = await response.json();
						await route.fulfill({
							response,
							json: { ...body, host: { ...body.host, id: 'different-process-instance' } }
						});
						return true;
					}
					if (scenario === 'late-file' && url.pathname === '/v1/files') {
						const response = await route.fetch();
						await new Promise((release) => {
							delayed.release = release;
							delayed.held = route;
						});
						await route.fulfill({ response }).catch(() => {});
						return true;
					}
					return false;
				};
				let delayedPath = null;
				if (scenario === 'late-pair') delayedPath = '/v1/pair';
				if (scenario === 'late-workspace') delayedPath = '/v1/workspaces';
				if (scenario === 'workspace-transition') delayedPath = 'SECOND_WORKSPACE_PATH';
				if (scenario.startsWith('late-command-')) delayedPath = 'COMMAND_PATH';
				if (scenario === 'stop-delay') delayedPath = 'STOP_PATH';
				if (scenario === 'disconnect-failure') delayedPath = 'BROWSE_PATH';
				session = await makeSession(browser, profile, fixture, interceptor, delayedPath);
				if (scenario === 'lifecycle') await lifecycle(session, fixture, profile);
				if (scenario === 'capability-mismatch') await capabilityMismatch(session);
				if (scenario === 'changed-pair') await changedPair(session, fixture);
				if (scenario === 'late-file') await lateFile(session, fixture, delayed);
				if (scenario === 'late-pair') await latePair(session, fixture);
				if (scenario === 'late-workspace') await lateWorkspace(session, fixture);
				if (scenario === 'workspace-transition') await workspaceTransition(session, fixture);
				if (scenario.startsWith('late-command-'))
					await lateCommand(session, fixture, scenario === 'late-command-unmount');
				if (scenario.endsWith('stop-retry')) await stopRetry(session, fixture, scenario);
				if (scenario === 'stop-delay') await delayedStop(session, fixture);
				if (scenario === 'disconnect-failure') await disconnectFailure(session, fixture);
				if (scenario === 'operation-conflict') await operationConflict(session, fixture);
				if (scenario.startsWith('uncertain-command-'))
					await uncertainCommand(
						session,
						fixture,
						() => {
							allowStatusRecovery = true;
						},
						profile,
						scenario
					);
				if (scenario.startsWith('recovery-overlap-'))
					await recoveryOverlap(session, fixture, delayed, profile, scenario);
				if (scenario.startsWith('delayed-command-admission-'))
					await delayedCommandAdmission(session, fixture, admission, profile, scenario);
				if (scenario === 'transient-poll') await transientPoll(session, fixture);
				results.push({
					profile: profile.name,
					scenario,
					passed: true,
					hostRequests: session.hostRequests.map(({ method, path }) => ({ method, path }))
				});
				console.log(JSON.stringify({ profile: profile.name, scenario, passed: true }));
			} catch (error) {
				if (session)
					await session.page
						.screenshot({
							path: resolve(outputDirectory, profile.name + '-' + scenario + '-failure.png')
						})
						.catch(() => {});
				results.push({
					profile: profile.name,
					scenario,
					passed: false,
					error: error.stack || error.message
				});
				console.log(
					JSON.stringify({ profile: profile.name, scenario, passed: false, error: error.message })
				);
			} finally {
				delayed.release();
				for (const release of admission.statusWaiters.splice(0)) release();
				await session?.context.close();
				await fixture?.close();
			}
		}
	}
} finally {
	await browser.close();
	writeFileSync(resolve(outputDirectory, 'results.json'), JSON.stringify(results, null, 2));
}
const failed = results.filter((result) => !result.passed);
console.log(
	JSON.stringify(
		{ passed: results.length - failed.length, failed: failed.length, results },
		null,
		2
	)
);
if (failed.length) process.exitCode = 1;
