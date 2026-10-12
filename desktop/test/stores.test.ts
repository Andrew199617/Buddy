import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { CredentialStore, type Cipher } from '../src/credential-store';
import { defaultSettings, parseSettings, SettingsStore } from '../src/settings-store';

function temporaryFolder(): string {
	return fs.mkdtempSync(path.join(os.tmpdir(), 'buddy-desktop-test-'));
}

// Stands in for safeStorage: reversible, but never the plain text on disk.
const fakeCipher: Cipher = {
	isEncryptionAvailable: () => true,
	encryptString: (text) => Buffer.from(text, 'utf8').reverse(),
	decryptString: (data) => Buffer.from(data).reverse().toString('utf8')
};

test('settings round-trip and start with nothing shared or allowed', () => {
	const folder = temporaryFolder();
	const store = new SettingsStore(path.join(folder, 'settings.json'));
	assert.deepEqual(store.load(), defaultSettings());
	const shared = path.join(folder, 'project');
	store.save({
		serverUrl: 'https://buddy.example.com',
		machine: { id: 'machine-1', name: 'Studio PC' },
		folders: [shared],
		allowEdits: true,
		allowCommands: false
	});
	assert.deepEqual(store.load().folders, [shared]);
	assert.equal(store.load().allowEdits, true);
	fs.rmSync(folder, { recursive: true, force: true });
});

test('unreadable or tampered settings fall back to the safe defaults', () => {
	assert.deepEqual(parseSettings('not json'), defaultSettings());
	const tampered = parseSettings(
		JSON.stringify({ folders: ['relative/path', 42], allowCommands: 'yes', machine: { name: 'x' } })
	);
	assert.deepEqual(tampered.folders, []);
	assert.equal(tampered.allowCommands, false);
	assert.equal(tampered.machine, null);
});

test('the credential is stored encrypted and can be forgotten', () => {
	const folder = temporaryFolder();
	const file = path.join(folder, 'credential.bin');
	const store = new CredentialStore(file, fakeCipher);
	assert.equal(store.read(), null);
	store.write('bdr1_fixture-credential');
	assert.equal(fs.readFileSync(file).includes('bdr1_fixture-credential'), false);
	assert.equal(store.read(), 'bdr1_fixture-credential');
	store.delete();
	assert.equal(store.read(), null);
	fs.rmSync(folder, { recursive: true, force: true });
});

test('without secure storage the credential is not saved at all', () => {
	const folder = temporaryFolder();
	const file = path.join(folder, 'credential.bin');
	const store = new CredentialStore(file, { ...fakeCipher, isEncryptionAvailable: () => false });
	assert.throws(() => store.write('bdr1_fixture-credential'), /securely/);
	assert.equal(fs.existsSync(file), false);
	fs.rmSync(folder, { recursive: true, force: true });
});

test('a credential that cannot be decrypted reads as missing', () => {
	const folder = temporaryFolder();
	const file = path.join(folder, 'credential.bin');
	fs.writeFileSync(file, 'garbage');
	const failing: Cipher = {
		...fakeCipher,
		decryptString: () => {
			throw new Error('wrong key');
		}
	};
	assert.equal(new CredentialStore(file, failing).read(), null);
	fs.rmSync(folder, { recursive: true, force: true });
});
