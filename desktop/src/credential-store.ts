import fs from 'node:fs';
import path from 'node:path';

/** Electron's safeStorage: Windows DPAPI, or the macOS Keychain. */
export type Cipher = {
	isEncryptionAvailable(): boolean;
	encryptString(text: string): Buffer;
	decryptString(data: Buffer): string;
};

/**
 * This computer's machine credential, encrypted for the signed-in OS account.
 * It lets the Buddy server run commands here, so it is never written in the
 * clear and never shown to the web page.
 */
export class CredentialStore {
	constructor(
		private readonly file: string,
		private readonly cipher: Cipher
	) {}

	read(): string | null {
		let data: Buffer;
		try {
			data = fs.readFileSync(this.file);
		} catch {
			return null;
		}
		try {
			return this.cipher.decryptString(data);
		} catch {
			// Encrypted for another account or an older key; connect again.
			return null;
		}
	}

	write(credential: string): void {
		if (!this.cipher.isEncryptionAvailable()) {
			throw new Error('This computer cannot store the connection securely, so it was not connected.');
		}
		fs.mkdirSync(path.dirname(this.file), { recursive: true });
		fs.writeFileSync(this.file, this.cipher.encryptString(credential), { mode: 0o600 });
	}

	delete(): void {
		fs.rmSync(this.file, { force: true });
	}
}
