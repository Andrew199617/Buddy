#!/usr/bin/env node
import path from 'node:path';
import { createCompanion } from './server.mjs';

const HELP = `Buddy Companion execution host

Usage: node companion/cli.mjs --root <absolute-project-directory> [options]

  --root <path>              Repeat to grant individual project directories.
  --port <number>            Default: 8083 (Buddy uses 8082).
  --host <loopback-address>  Default: 127.0.0.1. This foundation is loopback only.
  --origin <exact-origin>    Repeat to replace the default Buddy browser origins.
  --name <host-name>         A clear name shown to browser and mobile clients.
  --write                   Enable editing existing UTF-8 files in all grants.
  --allow-host-execution    Enable whole-host commands for all grants. A working
                            directory does NOT sandbox commands or subprocesses.
  --allow-no-origin         Permit explicit non-browser clients to pair.
  --help                    Show this help.

Pairing codes, browser tokens, workspaces, and terminal sessions are memory only.
No directory is granted unless supplied with --root by the host owner.
Do not share a pairing code with an untrusted browser. Restart for a new code.
Remote/cloud access needs a reviewed authenticated transport in a future version.
`;

function parseArguments(args) {
	const result = { host: '127.0.0.1', port: 8083, roots: [], origins: [], write: false, execute: false, allowNoOrigin: false };
	for (let index = 0; index < args.length; index++) {
		const argument = args[index];
		if (argument === '--help' || argument === '-h') { result.help = true; continue; }
		if (argument === '--write') { result.write = true; continue; }
		if (argument === '--allow-host-execution') { result.execute = true; continue; }
		if (argument === '--allow-no-origin') { result.allowNoOrigin = true; continue; }
		if (!['--root', '--port', '--host', '--origin', '--name'].includes(argument)) throw new Error(`Unknown option: ${argument}`);
		const value = args[++index];
		if (!value || value.startsWith('--')) throw new Error(`${argument} requires a value.`);
		if (argument === '--root') {
			if (!path.isAbsolute(value)) throw new Error('--root requires an absolute directory path.');
			result.roots.push(value);
		}
		if (argument === '--port') {
			if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) throw new Error('--port must be an integer from 1 to 65535.');
			result.port = Number(value);
		}
		if (argument === '--host') result.host = value;
		if (argument === '--origin') result.origins.push(value);
		if (argument === '--name') result.name = value;
	}
	if (!['127.0.0.1', 'localhost', '::1'].includes(result.host)) throw new Error('--host must be 127.0.0.1, localhost, or ::1. Remote exposure is not enabled in this foundation.');
	if (!result.origins.length) result.origins = [
		'http://127.0.0.1:8081', 'http://localhost:8081',
		'http://127.0.0.1:8082', 'http://localhost:8082'
	];
	return result;
}

let companion;
try {
	const settings = parseArguments(process.argv.slice(2));
	if (settings.help) {
		process.stdout.write(HELP);
	} else {
		companion = await createCompanion({
			roots: settings.roots.map((directory) => ({ path: directory, write: settings.write, execute: settings.execute })),
			origins: settings.origins,
			hostName: settings.name,
			allowNoOrigin: settings.allowNoOrigin
		});
		await new Promise((resolve, reject) => {
			companion.server.once('error', reject);
			companion.server.listen(settings.port, settings.host, resolve);
		});
		const address = companion.server.address();
		const displayHost = settings.host === '::1' ? '[::1]' : settings.host;
		process.stdout.write(`Buddy Companion: ${companion.host.name} (${companion.host.platform})\n`);
		process.stdout.write(`Host ID: ${companion.host.id}\nExecution host: http://${displayHost}:${address.port}\n`);
		process.stdout.write(`Allowed browser origins: ${settings.origins.join(', ')}\n`);
		process.stdout.write(`Directory grants: ${settings.roots.length}; read enabled; write ${settings.write ? 'enabled' : 'disabled'}; execution ${settings.execute ? 'enabled' : 'disabled'}\n`);
		if (settings.execute) process.stdout.write('WARNING: Commands run with your host account permissions. Their working directory is not a sandbox.\n');
		process.stdout.write(`One-use pairing code (expires in 5 minutes): ${companion.pairingCode}\n`);
		let stopping = false;
		const stop = async () => {
			if (stopping) return;
			stopping = true;
			await companion.close();
		};
		process.once('SIGINT', stop);
		process.once('SIGTERM', stop);
	}
} catch (error) {
	if (companion) await companion.close();
	process.stderr.write(`Buddy Companion: ${error.message}\n`);
	process.exitCode = 1;
}
