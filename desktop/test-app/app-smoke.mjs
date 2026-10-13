// Drives the real app against a disposable Buddy test instance:
// setup page → Buddy page → connect this computer → share a folder → disconnect.
//
//   BUDDY_DESKTOP_TEST_SERVER   Buddy address, such as http://127.0.0.1:8098 (never a live instance)
//   BUDDY_DESKTOP_TEST_ACCOUNT  JSON file with {email, password} of a test account on it
//   BUDDY_DESKTOP_PYTHON        Python with the Runner's dependencies
//
// Native dialogs are answered by the test; the system browser is never opened.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron } from 'playwright-core';

const server = process.env.BUDDY_DESKTOP_TEST_SERVER;
const accountFile = process.env.BUDDY_DESKTOP_TEST_ACCOUNT;
assert.ok(server && accountFile && process.env.BUDDY_DESKTOP_PYTHON, 'Set the three BUDDY_DESKTOP_* variables');
const serverUrl = new URL(server);
assert.ok(['127.0.0.1', 'localhost'].includes(serverUrl.hostname), 'Use a local test instance');
assert.ok(!['8080', '8081', '8082', '8083'].includes(serverUrl.port), 'Never a live instance');
const account = JSON.parse(fs.readFileSync(accountFile, 'utf8'));

const appDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'buddy-desktop-smoke-'));
const sharedFolder = fs.mkdtempSync(path.join(os.tmpdir(), 'buddy-desktop-shared-'));
const steps = [];

function step(name) {
	steps.push(name);
	console.log(`✔ ${name}`);
}

async function waitFor(check, what, timeout = 30000) {
	const started = Date.now();
	for (;;) {
		const value = await check();
		if (value) return value;
		if (Date.now() - started > timeout) throw new Error(`Timed out waiting for ${what}`);
		await new Promise((resolve) => setTimeout(resolve, 250));
	}
}

const app = await electron.launch({
	args: ['.'],
	cwd: appDirectory,
	env: { ...process.env, BUDDY_DESKTOP_USER_DATA: userData }
});

try {
	await app.evaluate(({ dialog, shell }, folder) => {
		globalThis.__questions = [];
		globalThis.__opened = [];
		dialog.showMessageBox = async (...args) => {
			const options = args.at(-1);
			globalThis.__questions.push(options.message);
			return { response: 0, checkboxChecked: false };
		};
		dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [folder] });
		shell.openExternal = async (url) => {
			globalThis.__opened.push(url);
		};
	}, sharedFolder);

	const setup = await app.firstWindow();
	await setup.waitForSelector('#address');
	assert.equal(await setup.evaluate(() => typeof window.buddyDesktop), 'undefined');
	assert.equal(await setup.evaluate(() => typeof window.buddySetup), 'object');
	step('first launch asks for the Buddy address');

	await setup.fill('#address', 'http://192.168.1.20:8082');
	await setup.click('#continue');
	await setup.waitForSelector('#address-error:not(:empty)');
	assert.match(await setup.textContent('#address-error'), /https/);
	step('plain HTTP to another computer is refused');

	const buddyWindow = app.waitForEvent('window');
	await setup.fill('#address', server);
	await setup.click('#continue');
	const page = await buddyWindow;
	await page.waitForLoadState('domcontentloaded');
	await waitFor(() => page.url().startsWith(serverUrl.origin), 'the Buddy page');
	assert.equal(await page.evaluate(() => typeof window.buddyDesktop), 'object');
	step('the Buddy page gets window.buddyDesktop');

	const signedIn = await page.evaluate(async ({ email, password }) => {
		const response = await fetch('/api/v1/auths/signin', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email, password })
		});
		const data = await response.json();
		localStorage.token = data.token;
		return response.ok;
	}, account);
	assert.ok(signedIn);
	step('signed in to the test instance');

	const before = await page.evaluate(() => window.buddyDesktop.getStatus());
	assert.equal(before.state, 'not_connected');
	assert.equal(before.platform, 'win32');

	const connected = await page.evaluate(async () => {
		const response = await fetch('/api/v1/runners/enrollments', {
			method: 'POST',
			headers: { Authorization: `Bearer ${localStorage.token}` }
		});
		const enrollment = await response.json();
		return window.buddyDesktop.connectThisPC({ enrollmentToken: enrollment.token, name: 'Desktop smoke PC' });
	});
	assert.equal(JSON.stringify(connected).includes('bdr1_'), false, 'the page never sees the credential');
	const questions = await app.evaluate(() => globalThis.__questions);
	assert.match(questions[0], /run Claude Code and Codex on this computer/);
	step('connecting asks in a native dialog');

	const online = await waitFor(
		() => page.evaluate(() => window.buddyDesktop.getStatus()).then((status) => status.state === 'connected' && status),
		'the Runner to connect'
	);
	const machines = await page.evaluate(async () => {
		const response = await fetch('/api/v1/subscriptions/machines', {
			headers: { Authorization: `Bearer ${localStorage.token}` }
		});
		return (await response.json()).machines;
	});
	const machine = machines.find((candidate) => candidate.id === online.machineId);
	assert.equal(machine?.online, true);
	assert.equal(machine?.name, 'Desktop smoke PC');
	step('this computer is online in Buddy');

	const credentialFile = path.join(userData, 'machine-credential.bin');
	assert.equal(fs.readFileSync(credentialFile).includes('bdr1_'), false, 'the credential is encrypted at rest');
	step('the credential is stored encrypted');

	const shared = await page.evaluate(() => window.buddyDesktop.chooseSharedFolder());
	assert.deepEqual(shared.folders, [sharedFolder]);
	await waitFor(
		() => page.evaluate(() => window.buddyDesktop.getStatus()).then((status) => status.state === 'connected'),
		'the Runner to reconnect with the folder'
	);
	step('sharing a folder restarts the Runner with it');

	await page.evaluate(() => {
		window.location.href = 'https://example.com/elsewhere';
	});
	await waitFor(async () => (await app.evaluate(() => globalThis.__opened)).length > 0, 'the external link');
	assert.ok(page.url().startsWith(serverUrl.origin), 'the app stays on Buddy');
	step('other addresses open in the system browser, not the app');

	const disconnected = await page.evaluate(() => window.buddyDesktop.disconnectThisPC());
	assert.equal(disconnected.state, 'not_connected');
	assert.equal(fs.existsSync(credentialFile), false);
	await page.evaluate(async (machineId) => {
		await fetch(`/api/v1/subscriptions/machines/${machineId}`, {
			method: 'DELETE',
			headers: { Authorization: `Bearer ${localStorage.token}` }
		});
	}, online.machineId);
	step('disconnecting forgets the credential');
} finally {
	await app.close();
	fs.rmSync(userData, { recursive: true, force: true });
	fs.rmSync(sharedFolder, { recursive: true, force: true });
}

console.log(`${steps.length} steps passed`);
