/**
 * Browser smoke for the built Buddy companion page.
 * Serve the matching build on loopback first; all Buddy APIs are synthetic.
 * BUDDY_LAZY_TEST_ORIGIN defaults to http://127.0.0.1:3313.
 * BUDDY_PLAYWRIGHT_MODULE may name an installed Playwright module URL.
 * Each viewport gets a disposable loopback host and temporary project roots.
 */
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createCompanion } from './server.mjs';

process.env.BUDDY_LAZY_TEST_ORIGIN ||= 'http://127.0.0.1:3313';
const { createSession, origin } = await import('../local/tests/browser/lazy-feature-fixture.mjs');
const workspace = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outputDirectory = resolve(workspace, 'local/tests/.qa/companion');
const defaultPlaywright = new URL('../local/tests/browser/node_modules/playwright/index.mjs', import.meta.url).href;
const { chromium } = await import(process.env.BUDDY_PLAYWRIGHT_MODULE || defaultPlaywright);
const results = [];

async function geometry(page) {
	return page.evaluate(() => {
		const panel = document.querySelector('.companion-page');
		const bounds = panel.getBoundingClientRect();
		const controls = [...panel.querySelectorAll('input, select, textarea, button, .panel')];
		const outside = controls.filter((control) => {
			const box = control.getBoundingClientRect();
			return box.width > 0 && (box.left < bounds.left - 1 || box.right > bounds.right + 1);
		});
		return {
			viewport: { width: innerWidth, height: innerHeight },
			page: { x: bounds.x, width: bounds.width, height: bounds.height },
			documentOverflow: document.documentElement.scrollWidth > innerWidth + 1,
			panelOverflow: panel.scrollWidth > panel.clientWidth + 1,
			controlsOutsidePanel: outside.map((control) => control.id || control.textContent.trim()),
			projectColumns: getComputedStyle(document.querySelector('.workspace-grid')).gridTemplateColumns
		};
	});
}

function assertGeometry(measurement, name) {
	assert.equal(measurement.documentOverflow, false, `${name}: document must not scroll horizontally`);
	assert.equal(measurement.panelOverflow, false, `${name}: companion panel must not overflow`);
	assert.deepEqual(measurement.controlsOutsidePanel, [], `${name}: controls must fit the panel`);
}

async function screenshot(page, name, section) {
	if (section) await page.locator(section).scrollIntoViewIfNeeded();
	else await page.locator('.companion-page').evaluate((panel) => { panel.scrollTop = 0; });
	await page.screenshot({ path: resolve(outputDirectory, name) });
}

async function inspectAndPair(page, app, endpoint) {
	await page.locator('#companion-endpoint').fill(endpoint);
	await page.getByRole('button', { name: 'Inspect host', exact: true }).click();
	await page.locator('.host-identity').getByText(app.host.name, { exact: true }).waitFor();
	assert.ok((await page.locator('.host-identity').innerText()).includes(app.host.id));
	assert.ok((await page.locator('.host-identity').innerText()).includes(endpoint));
	await page.locator('#companion-code').fill(app.pairingCode);
	const pairing = page.waitForResponse((response) => {
		return response.url() === endpoint + '/v1/pair' && response.request().method() === 'POST';
	});
	await page.getByRole('button', { name: 'Pair host', exact: true }).click();
	const paired = await (await pairing).json();
	await page.locator('#companion-grant').waitFor();
	await page.locator('.connection-panel .status').getByText('Paired', { exact: true }).waitFor();
	assert.equal(await page.locator('#companion-code').count(), 0, 'Pairing code field disappears after pairing');
	const stored = await page.evaluate((token) => {
		return [...Object.values(localStorage), ...Object.values(sessionStorage)].some((value) => value.includes(token));
	}, paired.token);
	assert.equal(stored, false, 'Companion credential must not be persisted in browser storage');
	return paired.token;
}

async function browseNestedProject(page, expectedPath, hostId) {
	await page.locator('.directory-list').getByRole('button', { name: 'Folder nested', exact: true }).click();
	await page.locator('.directory-toolbar code').getByText('nested', { exact: true }).waitFor();
	await page.getByRole('button', { name: 'Use this directory as project', exact: true }).click();
	await page.locator('.selected-project').getByText('Selected project: nested', { exact: true }).waitFor();
	const project = await page.locator('.selected-project').innerText();
	assert.ok(project.includes(expectedPath), 'Workspace must identify the real selected project directory');
	assert.ok(project.includes(hostId), 'Workspace must identify its execution host');
	await page.locator('.directory-list').getByRole('button', { name: 'File note.txt', exact: true }).click();
	await page.locator('#companion-file-content').waitFor();
}

async function requestHost(endpoint, token, pathname) {
	return fetch(endpoint + pathname, {
		headers: { Origin: origin, Authorization: `Bearer ${token}` }
	});
}

async function runViewport(browser, mobile) {
	const name = mobile ? 'phone' : 'desktop';
	const temporary = await mkdtemp(join(tmpdir(), 'buddy-companion-browser-'));
	const readRoot = join(temporary, 'read-only-project');
	const writeRoot = join(temporary, 'editable-project');
	const readProject = join(readRoot, 'nested');
	const writeProject = join(writeRoot, 'nested');
	await mkdir(readProject, { recursive: true });
	await mkdir(writeProject, { recursive: true });
	await writeFile(join(readProject, 'note.txt'), 'Read-only browser fixture\n');
	await writeFile(join(writeProject, 'note.txt'), 'Editable browser fixture\n');
	let app;
	let session;
	const hostRequests = [];
	const result = { name, passed: false, checks: [] };
	results.push(result);
	try {
		app = await createCompanion({
			hostName: `Disposable ${name} execution host`,
			origins: [origin],
			roots: [
				{ path: readRoot, name: 'Read-only fixture' },
				{ path: writeRoot, name: 'Editable execution fixture', write: true, execute: true }
			],
			commandTimeoutMs: 15000
		});
		app.server.listen(0, '127.0.0.1');
		await once(app.server, 'listening');
		const endpoint = `http://127.0.0.1:${app.server.address().port}`;
		session = await createSession(browser, {
			mobile,
			beforeNavigation: async (context) => {
				await context.route(endpoint + '/**', async (route) => {
					const request = route.request();
					const headers = request.headers();
					assert.notEqual(headers.authorization, 'Bearer buddy-design-fake-token', 'Buddy token must never reach the host');
					hostRequests.push(request.method() + ' ' + new URL(request.url()).pathname);
					await route.continue();
				});
			}
		});
		const { page } = session;
		page.setDefaultTimeout(15000);
		await page.goto(origin + '/companion', { waitUntil: 'domcontentloaded' });
		await page.locator('#companion-endpoint').waitFor();
		assert.equal(await page.locator('#companion-endpoint').inputValue(), 'http://127.0.0.1:8083');
		assert.equal(await page.locator('#companion-grant').count(), 0, 'Unpaired browser cannot see grants');
		assert.ok((await page.locator('.project-panel').innerText()).includes('A supplied path alone grants nothing.'));
		result.checks.push('default port 8083, unpaired access gated, chat folders distinct from grants');
		const token = await inspectAndPair(page, app, endpoint);
		result.checks.push('real loopback host inspection and pairing, host identity visible, memory-only credential');
		await page.locator('.capabilities').getByText('Execute disabled', { exact: true }).waitFor();
		await browseNestedProject(page, readProject, app.host.id);
		assert.equal(await page.locator('#companion-file-content').inputValue(), 'Read-only browser fixture\n');
		assert.equal(await page.locator('#companion-file-content').getAttribute('readonly'), '');
		assert.equal(await page.getByRole('button', { name: 'Save to execution host', exact: true }).count(), 0);
		assert.equal(await page.getByRole('button', { name: 'Create command session', exact: true }).count(), 0);
		assert.ok((await page.locator('.terminal-panel').innerText()).includes('This folder grant has no execute permission.'));
		result.readOnlyGeometry = await geometry(page);
		assertGeometry(result.readOnlyGeometry, name);
		result.checks.push('approved-root browsing and nested workspace selection, read-only file and execution denial');
		await screenshot(page, `${name}-connected.png`);
		await screenshot(page, `${name}-project.png`, '.project-panel');
		await page.locator('#companion-grant').selectOption({ label: 'Editable execution fixture' });
		await page.locator('.capabilities').getByText('Write allowed', { exact: true }).waitFor();
		await browseNestedProject(page, writeProject, app.host.id);
		assert.equal(await page.locator('#companion-file-content').getAttribute('readonly'), null);
		const edited = `Edited by disposable ${name} browser smoke\n`;
		await page.locator('#companion-file-content').fill(edited);
		const saveResponse = page.waitForResponse((response) => {
			return response.url() === endpoint + '/v1/files' && response.request().method() === 'PUT';
		});
		await page.getByRole('button', { name: 'Save to execution host', exact: true }).click();
		assert.equal((await saveResponse).status(), 200);
		assert.equal(await readFile(join(writeProject, 'note.txt'), 'utf8'), edited);
		result.checks.push('explicit write capability edits the actual temporary file');
		await screenshot(page, `${name}-file.png`, '.file-panel');
		const createButton = page.getByRole('button', { name: 'Create command session', exact: true });
		assert.equal(await createButton.isDisabled(), true, 'Execution requires separate risk acknowledgement');
		await page.getByRole('checkbox', { name: 'I understand execution has this host-level access.', exact: true }).check();
		const creating = page.waitForResponse((response) => {
			return response.url() === endpoint + '/v1/terminals' && response.request().method() === 'POST';
		});
		await createButton.click();
		const created = await (await creating).json();
		await page.locator('#companion-executable').fill(process.execPath);
		await page.locator('#companion-arguments').fill(JSON.stringify([
			'-e', 'console.log("companion-browser-smoke"); console.log(process.cwd());'
		]));
		await page.getByRole('button', { name: 'Run command', exact: true }).click();
		await page.locator('.terminal-output').getByText(/companion-browser-smoke/).waitFor({ timeout: 30000 });
		await page.locator('.terminal-panel .status').getByText('exited · exit 0', { exact: true }).waitFor({ timeout: 30000 });
		const commandOutput = await page.locator('.terminal-output').innerText();
		assert.ok(commandOutput.includes(writeProject));
		assert.equal(commandOutput.includes('#< CLIXML'), false, 'Windows supervisor progress must not pollute command output');
		result.commandGeometry = await geometry(page);
		assertGeometry(result.commandGeometry, name);
		result.checks.push('execution acknowledgement gated, disposable Node command exits 0 in selected workspace');
		await screenshot(page, `${name}-command.png`, '.terminal-panel');
		await page.getByRole('button', { name: 'Stop and remove session', exact: true }).click();
		await page.getByRole('button', { name: 'Create command session', exact: true }).waitFor();
		const stopped = await requestHost(endpoint, token, '/v1/terminals/' + created.terminal.id);
		assert.equal(stopped.status, 200);
		assert.equal((await stopped.json()).terminal.status, 'closed');
		await page.getByRole('button', { name: 'Disconnect and revoke', exact: true }).click();
		await page.locator('.connection-panel .status').getByText('Disconnected', { exact: true }).waitFor();
		await page.getByRole('status').getByText('Disconnected. The host session and its commands were revoked.', { exact: true }).waitFor();
		assert.equal((await requestHost(endpoint, token, '/v1/grants')).status, 401);
		assert.equal(await page.locator('#companion-grant').count(), 0);
		result.checks.push('UI stop closes the actual session; disconnect revokes token and clears grant UI');
		assert.deepEqual(session.errors, [], 'Browser must have no uncaught page errors');
		result.hostRequests = hostRequests;
		result.passed = true;
		await rm(resolve(outputDirectory, `${name}-failure.png`), { force: true });
	} catch (error) {
		result.error = error.stack || error.message;
		if (session) {
			await session.page.screenshot({ path: resolve(outputDirectory, `${name}-failure.png`) }).catch(() => {});
			result.pageErrors = session.errors;
			result.pageText = await session.page.locator('.companion-page').innerText().catch(() => 'Companion page unavailable');
		}
	} finally {
		await session?.context.close();
		await app?.close();
		// This path is created by this run under the OS temporary directory.
		await rm(temporary, { recursive: true, force: true });
	}
}

await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
	await runViewport(browser, false);
	await runViewport(browser, true);
} finally {
	await browser.close();
	await writeFile(resolve(outputDirectory, 'results.json'), JSON.stringify({ origin, results }, null, 2));
}
const failed = results.filter((result) => !result.passed).length;
console.log(JSON.stringify({ passed: results.length - failed, failed, origin, outputDirectory, results }, null, 2));
if (failed) process.exitCode = 1;
