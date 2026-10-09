import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';
import { promises as files } from 'node:fs';
import { randomBytes, randomUUID, createHash, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { TextDecoder } from 'node:util';

const FILE_BYTES = 1024 * 1024;
const OUTPUT_BYTES = 256 * 1024;
const MAX_TERMINALS = 8;
const MAX_WORKSPACES = 64;
const UTF8 = new TextDecoder('utf-8', { fatal: true });

class RequestError extends Error {
	constructor(status, message) {
		super(message);
		this.status = status;
	}
}

function fail(status, message) {
	throw new RequestError(status, message);
}

function duration(value, fallback, name) {
	if (value === undefined) return fallback;
	if (!Number.isFinite(value) || value <= 0 || value > 2147483647) {
		throw new Error(`${name} must be a positive duration in milliseconds.`);
	}
	return value;
}

function digest(value) {
	return createHash('sha256').update(value).digest();
}

function sameFile(left, right) {
	return left.dev === right.dev && left.ino === right.ino;
}

function samePath(left, right) {
	if (process.platform === 'win32') return left.toLowerCase() === right.toLowerCase();
	return left === right;
}

function within(root, target) {
	const relative = path.relative(root, target);
	return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function relativePath(value) {
	if (typeof value !== 'string') fail(400, 'A relative path is required.');
	if (value === '' || value === '.') return '';
	if (value.length > 4096 || /[\\:\x00-\x1f\x7f<>"|?*]/.test(value) || value.startsWith('/')) {
		fail(403, 'Only safe, forward-slash relative paths are allowed.');
	}
	const segments = value.split('/');
	for (const segment of segments) {
		if (!segment || segment === '.' || segment === '..' || /[ .]$/.test(segment)) {
			fail(403, 'Path traversal and ambiguous path segments are not allowed.');
		}
		if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(segment)) {
			fail(403, 'Reserved device paths are not allowed.');
		}
	}
	return segments.join('/');
}

async function noSymlinkAncestors(absolutePath) {
	const parsed = path.parse(absolutePath);
	let current = parsed.root;
	for (const segment of absolutePath.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
		current = path.join(current, segment);
		const stat = await files.lstat(current, { bigint: true });
		if (stat.isSymbolicLink()) fail(403, 'Symbolic links and junctions cannot be granted or followed.');
	}
}

async function makeGrant(specification) {
	if (!specification || typeof specification.path !== 'string' || !path.isAbsolute(specification.path)) {
		throw new Error('Each grant must name an absolute project-directory path.');
	}
	const requestedPath = path.resolve(specification.path);
	await noSymlinkAncestors(requestedPath);
	const canonicalPath = await files.realpath(requestedPath);
	const stat = await files.lstat(canonicalPath, { bigint: true });
	if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Each grant must be a real directory.');
	if (samePath(canonicalPath, path.parse(canonicalPath).root)) throw new Error('Grant a project directory, not an entire filesystem volume.');
	const name = specification.name ?? path.basename(canonicalPath);
	if (typeof name !== 'string' || !name.trim() || name.length > 200) throw new Error('Grant names must be nonempty text.');
	return {
		id: randomUUID(),
		name,
		path: canonicalPath,
		requestedPath,
		stat,
		capabilities: { read: true, write: specification.write === true, execute: specification.execute === true }
	};
}

function publicGrant(grant) {
	return { id: grant.id, name: grant.name, path: grant.path, capabilities: { ...grant.capabilities } };
}

async function checkRoot(grant) {
	await noSymlinkAncestors(grant.requestedPath);
	const canonicalPath = await files.realpath(grant.requestedPath);
	const stat = await files.lstat(grant.path, { bigint: true });
	if (!samePath(canonicalPath, grant.path) || !sameFile(stat, grant.stat) || !stat.isDirectory() || stat.isSymbolicLink()) {
		fail(403, 'The granted directory changed. Restart the companion and grant it again.');
	}
}

async function resolveGrantedPath(grant, input) {
	const relative = relativePath(input);
	await checkRoot(grant);
	let absolute = grant.path;
	let stat = grant.stat;
	for (const segment of relative.split('/').filter(Boolean)) {
		absolute = path.join(absolute, segment);
		stat = await files.lstat(absolute, { bigint: true });
		if (stat.isSymbolicLink()) fail(403, 'Symbolic links and junctions cannot be followed.');
	}
	const canonicalPath = await files.realpath(absolute);
	if (!within(grant.path, canonicalPath) || !samePath(absolute, canonicalPath)) {
		fail(403, 'The path is outside the granted directory.');
	}
	return { relative, absolute: canonicalPath, stat };
}

async function openGrantedFile(grant, input, writable) {
	const resolved = await resolveGrantedPath(grant, input);
	if (!resolved.stat.isFile()) fail(400, 'The requested path must be an existing regular file.');
	if (resolved.stat.nlink > 1) fail(403, 'Hard-linked files cannot be accessed through a directory grant.');
	if (resolved.stat.size > FILE_BYTES) fail(413, 'Files are limited to 1 MiB.');
	const flags = writable ? fs.constants.O_RDWR : fs.constants.O_RDONLY;
	const handle = await files.open(resolved.absolute, flags | (fs.constants.O_NOFOLLOW ?? 0));
	try {
		const openedStat = await handle.stat({ bigint: true });
		const checked = await resolveGrantedPath(grant, resolved.relative);
		if (!openedStat.isFile() || openedStat.nlink > 1 || checked.stat.nlink > 1 || !sameFile(openedStat, resolved.stat) || !sameFile(openedStat, checked.stat)) {
			fail(403, 'The file changed while it was being opened.');
		}
		return { handle, resolved };
	} catch (error) {
		await handle.close();
		throw error;
	}
}

function decodeFile(bytes) {
	try {
		if (bytes.includes(0)) fail(415, 'Only UTF-8 text files are supported.');
		return UTF8.decode(bytes);
	} catch (error) {
		if (error instanceof RequestError) throw error;
		fail(415, 'Only UTF-8 text files are supported.');
	}
}

async function readTextFile(handle) {
	const buffer = Buffer.alloc(FILE_BYTES + 1);
	let length = 0;
	while (length < buffer.length) {
		const read = await handle.read(buffer, length, buffer.length - length, length);
		if (read.bytesRead === 0) break;
		length += read.bytesRead;
	}
	if (length > FILE_BYTES) fail(413, 'Files are limited to 1 MiB.');
	return decodeFile(buffer.subarray(0, length));
}

async function replaceFileText(handle, content) {
	const bytes = Buffer.from(content);
	let written = 0;
	while (written < bytes.length) {
		const result = await handle.write(bytes, written, bytes.length - written, written);
		if (result.bytesWritten === 0) throw new Error('The file write did not make progress.');
		written += result.bytesWritten;
	}
	await handle.truncate(bytes.length);
}

async function readJson(request) {
	if (!/^application\/json(?:\s*;|$)/i.test(request.headers['content-type'] ?? '')) {
		fail(415, 'Send an application/json request body.');
	}
	let length = 0;
	const chunks = [];
	for await (const chunk of request) {
		length += chunk.length;
		if (length > FILE_BYTES + 65536) fail(413, 'The request body is too large.');
		chunks.push(chunk);
	}
	let result;
	try {
		result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
	} catch {
		fail(400, 'The request body must be valid JSON.');
	}
	if (!result || typeof result !== 'object' || Array.isArray(result)) fail(400, 'The request body must be a JSON object.');
	return result;
}

function send(response, status, value) {
	response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
	response.end(JSON.stringify(value));
}

function publicWorkspace(workspace) {
	return {
		id: workspace.id,
		name: workspace.name,
		hostId: workspace.hostId,
		grantId: workspace.grantId,
		path: workspace.path
	};
}

function publicTerminal(terminal) {
	return {
		id: terminal.id,
		workspaceId: terminal.workspaceId,
		status: terminal.status,
		output: terminal.output.toString('utf8'),
		exitCode: terminal.exitCode
	};
}

function appendOutput(terminal, chunk) {
	terminal.output = Buffer.concat([terminal.output, Buffer.from(chunk)]);
	if (terminal.output.length > OUTPUT_BYTES) terminal.output = terminal.output.subarray(terminal.output.length - OUTPUT_BYTES);
}

// Windows has no Node Job Object API. This transient supervisor assigns the actual
// executable to a kill-on-close job while suspended, then resumes it. Closing the
// supervisor also closes its job handle, including children whose parent exited.
const WINDOWS_SUPERVISOR = `
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$nativeSource = @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class BuddyCommandJob {
  [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
    public long ProcessTime, JobTime; public uint Flags; public UIntPtr MinWorking, MaxWorking;
    public uint ActiveProcesses; public UIntPtr Affinity; public uint Priority, Scheduling;
  }
  [StructLayout(LayoutKind.Sequential)] struct IoCounters {
    public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
  }
  [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
    public BasicLimits Basic; public IoCounters Io; public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct Startup {
    public uint Size; public string Reserved, Desktop, Title;
    public uint X,Y,XSize,YSize,XChars,YChars,Fill,Flags; public ushort Show,ReservedSize;
    public IntPtr ReservedPointer, Input, Output, Error;
  }
  [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {
    public IntPtr Process, Thread; public uint ProcessId, ThreadId;
  }
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr a, string n);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr j, int c, ref ExtendedLimits l, uint s);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string a, StringBuilder c, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref Startup s, out ProcessInfo p);
  [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
  [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr t);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h, uint ms);
  [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr h, out uint c);
  [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr h, uint c);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  static string Quote(string value) {
    var result = new StringBuilder(); result.Append('"'); int slashes = 0;
    foreach (char c in value) {
      if (c == (char)92) { slashes++; continue; }
      if (c == '"') { result.Append((char)92, slashes * 2 + 1); result.Append(c); slashes = 0; continue; }
      result.Append((char)92, slashes); slashes = 0; result.Append(c);
    }
    result.Append((char)92, slashes * 2); result.Append('"'); return result.ToString();
  }
  static Exception NativeError(string action) {
    return new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), action);
  }
  public static int Run(string executable, string[] args, string cwd) {
    IntPtr job = CreateJobObject(IntPtr.Zero, null); ProcessInfo process = new ProcessInfo();
    if (job == IntPtr.Zero) throw NativeError("Cannot create command job");
    try {
      var limits = new ExtendedLimits(); limits.Basic.Flags = 0x2000;
      if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits))) throw NativeError("Cannot protect command job");
      var startup = new Startup(); startup.Size = (uint)Marshal.SizeOf(startup); startup.Flags = 0x100;
      startup.Input = GetStdHandle(-10); startup.Output = GetStdHandle(-11); startup.Error = GetStdHandle(-12);
      var command = new StringBuilder(Quote(executable)); foreach (string arg in args) { command.Append(' '); command.Append(Quote(arg)); }
      if (!CreateProcess(null, command, IntPtr.Zero, IntPtr.Zero, true, 0x08000004, IntPtr.Zero, cwd, ref startup, out process)) throw NativeError("Cannot start executable");
      if (!AssignProcessToJobObject(job, process.Process)) { TerminateProcess(process.Process, 1); throw NativeError("Cannot assign command to protected job"); }
      if (ResumeThread(process.Thread) == 0xffffffff) { TerminateProcess(process.Process, 1); throw new Exception("Cannot resume command"); }
      WaitForSingleObject(process.Process, 0xffffffff); uint exitCode;
      if (!GetExitCodeProcess(process.Process, out exitCode)) throw NativeError("Cannot read command result");
      return unchecked((int)exitCode);
    } finally {
      CloseHandle(job);
      if (process.Thread != IntPtr.Zero) CloseHandle(process.Thread);
      if (process.Process != IntPtr.Zero) CloseHandle(process.Process);
    }
  }
}
'@
try {
  Add-Type -TypeDefinition $nativeSource
  $spec = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:BUDDY_COMPANION_COMMAND)) | ConvertFrom-Json
  exit [BuddyCommandJob]::Run([string]$spec.executable, [string[]]$spec.args, [string]$spec.cwd)
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
`;

function spawnCommand(executable, args, cwd) {
	const options = { cwd, shell: false, windowsHide: true, detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'] };
	if (process.platform !== 'win32') return spawn(executable, args, options);
	const powershell = path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
	const encodedScript = Buffer.from(WINDOWS_SUPERVISOR, 'utf16le').toString('base64');
	options.env = {
		...process.env,
		BUDDY_COMPANION_COMMAND: Buffer.from(JSON.stringify({ executable, args, cwd })).toString('base64')
	};
	return spawn(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-OutputFormat', 'Text', '-EncodedCommand', encodedScript], options);
}

async function killProcessTree(child) {
	if (!child?.pid) return;
	if (process.platform !== 'win32') {
		try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
		return;
	}
	// Terminating the transient supervisor closes its native Job handle. Windows
	// then terminates every command descendant, including detached grandchildren.
	child.kill('SIGKILL');
}

function validateCommand(body) {
	if (typeof body.executable !== 'string' || !body.executable || body.executable.length > 4096 || /[\x00-\x1f]/.test(body.executable)) {
		fail(400, 'Provide an executable name or path.');
	}
	if (!Array.isArray(body.args) || body.args.length > 128 || body.args.some((arg) => typeof arg !== 'string' || arg.includes('\0'))) {
		fail(400, 'Command args must be an array of at most 128 strings.');
	}
	if (Buffer.byteLength(JSON.stringify(body)) > 24000) fail(413, 'The command is too large.');
}

/** Create an unbound execution host. Grants and credentials exist only in memory. */
export async function createCompanion(options = {}) {
	const tokenTtlMs = duration(options.tokenTtlMs, 30 * 60 * 1000, 'tokenTtlMs');
	const pairingTtlMs = duration(options.pairingTtlMs, 5 * 60 * 1000, 'pairingTtlMs');
	const commandTimeoutMs = duration(options.commandTimeoutMs, 60 * 1000, 'commandTimeoutMs');
	const sessionIdleMs = duration(options.sessionIdleMs, 10 * 60 * 1000, 'sessionIdleMs');
	const origins = new Set(options.origins ?? []);
	for (const origin of origins) {
		let parsed;
		try { parsed = new URL(origin); } catch { throw new Error('Origins must be exact HTTP(S) origins.'); }
		if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin !== origin) throw new Error('Origins must be exact HTTP(S) origins without a path.');
	}
	const grants = new Map();
	for (const specification of options.roots ?? []) {
		const grant = await makeGrant(specification);
		grants.set(grant.id, grant);
	}
	const host = {
		id: randomUUID(),
		name: options.hostName ?? os.hostname(),
		platform: process.platform,
		version: '0.1.0'
	};
	if (typeof host.name !== 'string' || !host.name.trim() || host.name.length > 200) throw new Error('Host name must be nonempty text.');
	const pairingCode = randomBytes(18).toString('base64url');
	const pairingDigest = digest(pairingCode);
	const pairingExpiresAt = Date.now() + pairingTtlMs;
	let pairingUsed = false;
	let pairingAttempts = 0;
	let closing = false;
	const tokens = new Map();
	const workspaces = new Map();
	const terminals = new Map();
	const pendingCleanup = new Set();

	function trackCleanup(promise) {
		pendingCleanup.add(promise);
		promise.finally(() => pendingCleanup.delete(promise)).catch(() => {});
		return promise;
	}

	async function stopTerminal(terminal) {
		terminal.status = 'closed';
		clearTimeout(terminal.timer);
		const child = terminal.child;
		if (!child) return;
		await killProcessTree(child);
		await terminal.completion;
	}

	async function revokeToken(token) {
		tokens.delete(token.key);
		const cleanup = [];
		for (const terminal of terminals.values()) {
			if (terminal.owner === token.key) cleanup.push(stopTerminal(terminal));
		}
		for (const [id, workspace] of workspaces) {
			if (workspace.owner === token.key) workspaces.delete(id);
		}
		await Promise.allSettled(cleanup);
		for (const [id, terminal] of terminals) {
			if (terminal.owner === token.key) terminals.delete(id);
		}
	}

	async function revokeGrant(id) {
		if (!grants.delete(id)) return false;
		const cleanup = [];
		for (const terminal of terminals.values()) {
			const workspace = workspaces.get(terminal.workspaceId);
			if (workspace?.grantId === id) cleanup.push(stopTerminal(terminal));
		}
		await Promise.allSettled(cleanup);
		for (const [workspaceId, workspace] of workspaces) {
			if (workspace.grantId === id) {
				for (const [terminalId, terminal] of terminals) {
					if (terminal.workspaceId === workspaceId) terminals.delete(terminalId);
				}
				workspaces.delete(workspaceId);
			}
		}
		return true;
	}

	function requestOrigin(request, allowMissing = false) {
		const origin = request.headers.origin;
		if (origin === undefined && (allowMissing || options.allowNoOrigin === true)) return null;
		if (typeof origin !== 'string' || !origins.has(origin)) fail(403, 'This browser origin is not allowed.');
		return origin;
	}

	function validateHost(request) {
		const address = server.address();
		const supplied = request.headers.host;
		if (!address || typeof address === 'string' || typeof supplied !== 'string') fail(403, 'Invalid execution-host address.');
		const allowedHosts = [`127.0.0.1:${address.port}`, `localhost:${address.port}`, `[::1]:${address.port}`];
		if (!allowedHosts.includes(supplied.toLowerCase())) fail(403, 'The Host header must identify this loopback execution host.');
		const remote = request.socket.remoteAddress;
		if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(remote)) fail(403, 'This foundation accepts loopback connections only.');
	}

	function authenticate(request, origin) {
		const authorization = request.headers.authorization ?? '';
		if (typeof authorization !== 'string' || !/^Bearer [A-Za-z0-9_-]{43}$/.test(authorization)) fail(401, 'Pair this browser with the execution host first.');
		const key = digest(authorization.slice(7)).toString('hex');
		const token = tokens.get(key);
		if (!token) fail(401, 'The companion session is invalid or revoked.');
		if (Date.now() >= token.expiresAt) {
			trackCleanup(revokeToken(token));
			fail(401, 'The companion session expired. Restart the host to pair again.');
		}
		if (token.origin !== origin) fail(403, 'This session belongs to a different browser origin.');
		return token;
	}

	function requireGrant(id) {
		if (typeof id !== 'string' || !grants.has(id)) fail(403, 'A valid host-approved directory grant is required.');
		return grants.get(id);
	}

	function requireWorkspace(id, token) {
		const workspace = workspaces.get(id);
		if (!workspace || workspace.owner !== token.key) fail(404, 'Workspace not found in this session.');
		return workspace;
	}

	function requireTerminal(id, token) {
		const terminal = terminals.get(id);
		if (!terminal || terminal.owner !== token.key) fail(404, 'Terminal not found in this session.');
		if (terminal.status !== 'closed' && Date.now() - terminal.lastUsed >= sessionIdleMs) {
			trackCleanup(stopTerminal(terminal));
			fail(410, 'The idle terminal session expired.');
		}
		terminal.lastUsed = Date.now();
		return terminal;
	}

	async function runCommand(terminal, workspace, body) {
		validateCommand(body);
		if (terminal.status === 'closed') fail(410, 'The terminal session is closed.');
		if (terminal.child) fail(409, 'A command is already running in this terminal.');
		const grant = requireGrant(workspace.grantId);
		if (!grant.capabilities.execute) fail(403, 'Host execution was not approved for this grant.');
		const resolved = await resolveGrantedPath(grant, workspace.relative);
		if (!resolved.stat.isDirectory() || !sameFile(resolved.stat, workspace.stat)) fail(403, 'The workspace directory changed. Select it again.');
		if (!tokens.has(terminal.owner) || !grants.has(workspace.grantId) || terminal.status === 'closed') fail(403, 'The session or directory grant was revoked.');
		if (terminal.child) fail(409, 'A command is already running in this terminal.');
		terminal.status = 'running';
		terminal.exitCode = null;
		terminal.output = Buffer.alloc(0);
		const child = spawnCommand(body.executable, body.args, resolved.absolute);
		terminal.child = child;
		child.stdin.on('error', () => {});
		child.stdin.end();
		child.stdout.on('data', (chunk) => appendOutput(terminal, chunk));
		child.stderr.on('data', (chunk) => appendOutput(terminal, chunk));
		let spawnFailure = false;
		child.once('error', (error) => {
			spawnFailure = true;
			appendOutput(terminal, `Cannot start command: ${error.message}\n`);
		});
		terminal.completion = new Promise((resolve) => {
			child.once('exit', () => {
				if (process.platform !== 'win32') trackCleanup(killProcessTree(child));
			});
			child.once('close', (code) => {
				clearTimeout(terminal.timer);
				terminal.child = null;
				terminal.exitCode = code;
				if (spawnFailure) terminal.exitCode = 1;
				if (terminal.status !== 'closed') terminal.status = 'exited';
				resolve();
			});
		});
		terminal.timer = setTimeout(() => {
			appendOutput(terminal, '\nCommand time limit reached.\n');
			trackCleanup(stopTerminal(terminal));
		}, commandTimeoutMs);
		terminal.timer.unref();
		return publicTerminal(terminal);
	}

	async function route(request, response) {
		validateHost(request);
		if (closing) fail(503, 'The execution host is shutting down.');
		const url = new URL(request.url, 'http://companion.invalid');
		const isHost = request.method === 'GET' && url.pathname === '/v1/host';
		const origin = requestOrigin(request, isHost);
		response.setHeader('Cache-Control', 'no-store');
		response.setHeader('X-Content-Type-Options', 'nosniff');
		response.setHeader('Vary', 'Origin');
		if (origin) response.setHeader('Access-Control-Allow-Origin', origin);
		if (request.method === 'OPTIONS') {
			const method = request.headers['access-control-request-method'];
			if (method && !['GET', 'POST', 'PUT', 'DELETE'].includes(method)) fail(403, 'This CORS method is not allowed.');
			const headers = (request.headers['access-control-request-headers'] ?? '').toLowerCase().split(',').map((header) => header.trim()).filter(Boolean);
			if (headers.some((header) => !['authorization', 'content-type'].includes(header))) fail(403, 'This CORS header is not allowed.');
			response.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE');
			response.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
			send(response, 200, { ok: true });
			return;
		}
		if (isHost) { send(response, 200, { host }); return; }
		if (request.method === 'POST' && url.pathname === '/v1/pair') {
			if (pairingUsed || Date.now() >= pairingExpiresAt || pairingAttempts >= 8) fail(403, 'Pairing is unavailable. Restart the host for a fresh code.');
			const body = await readJson(request);
			pairingAttempts++;
			if (pairingAttempts > 8) fail(403, 'Pairing is unavailable. Restart the host for a fresh code.');
			if (typeof body.code !== 'string' || body.code.length > 100 || !timingSafeEqual(digest(body.code), pairingDigest)) fail(403, 'Invalid pairing code.');
			// Recheck after awaiting the body so simultaneous requests cannot consume one code twice.
			if (pairingUsed || Date.now() >= pairingExpiresAt) fail(403, 'Pairing is unavailable. Restart the host for a fresh code.');
			pairingUsed = true;
			const tokenValue = randomBytes(32).toString('base64url');
			const key = digest(tokenValue).toString('hex');
			const token = { key, origin, expiresAt: Date.now() + tokenTtlMs };
			tokens.set(key, token);
			send(response, 200, { token: tokenValue, expiresAt: token.expiresAt, host });
			return;
		}
		const token = authenticate(request, origin);
		if (request.method === 'DELETE' && url.pathname === '/v1/session') {
			await revokeToken(token);
			send(response, 200, { revoked: true });
			return;
		}
		if (request.method === 'GET' && url.pathname === '/v1/grants') {
			const approved = [];
			for (const grant of grants.values()) { await checkRoot(grant); approved.push(publicGrant(grant)); }
			send(response, 200, { grants: approved });
			return;
		}
		if (request.method === 'GET' && url.pathname === '/v1/directories') {
			const grant = requireGrant(url.searchParams.get('grantId'));
			const resolved = await resolveGrantedPath(grant, url.searchParams.get('path') ?? '');
			if (!resolved.stat.isDirectory()) fail(400, 'The requested path must be a directory.');
			const listing = await files.readdir(resolved.absolute, { withFileTypes: true });
			if (listing.length > 5000) fail(413, 'This directory has too many entries.');
			const checked = await resolveGrantedPath(grant, resolved.relative);
			if (!sameFile(checked.stat, resolved.stat)) fail(403, 'The directory changed while being listed.');
			const entries = [];
			for (const entry of listing) {
				if (entry.isSymbolicLink() || (!entry.isDirectory() && !entry.isFile())) continue;
				const relative = resolved.relative ? `${resolved.relative}/${entry.name}` : entry.name;
				try {
					const safe = await resolveGrantedPath(grant, relative);
					if (safe.stat.isDirectory()) entries.push({ name: entry.name, path: relative, type: 'directory' });
					else if (safe.stat.isFile()) entries.push({ name: entry.name, path: relative, type: 'file' });
				} catch (error) {
					if (!(error instanceof RequestError) && !['ENOENT', 'ENOTDIR'].includes(error.code)) throw error;
				}
			}
			entries.sort((left, right) => left.type.localeCompare(right.type) || left.name.localeCompare(right.name));
			send(response, 200, { path: resolved.relative, entries });
			return;
		}
		if (request.method === 'GET' && url.pathname === '/v1/files') {
			const grant = requireGrant(url.searchParams.get('grantId'));
			const opened = await openGrantedFile(grant, url.searchParams.get('path'), false);
			try {
				const content = await readTextFile(opened.handle);
				send(response, 200, { path: opened.resolved.relative, content });
			} finally { await opened.handle.close(); }
			return;
		}
		if (request.method === 'PUT' && url.pathname === '/v1/files') {
			const body = await readJson(request);
			const grant = requireGrant(body.grantId);
			if (!grant.capabilities.write) fail(403, 'File writing was not approved for this grant.');
			if (typeof body.content !== 'string' || body.content.includes('\0')) fail(400, 'Provide UTF-8 text content.');
			if (Buffer.byteLength(body.content) > FILE_BYTES) fail(413, 'Files are limited to 1 MiB.');
			const opened = await openGrantedFile(grant, body.path, true);
			try {
				await readTextFile(opened.handle);
				const checked = await resolveGrantedPath(grant, opened.resolved.relative);
				const currentStat = await opened.handle.stat({ bigint: true });
				if (currentStat.nlink > 1 || checked.stat.nlink > 1 || !sameFile(checked.stat, currentStat)) fail(403, 'The file changed while being edited.');
				if (!tokens.has(token.key) || !grants.has(grant.id)) fail(403, 'The session or directory grant was revoked.');
				await replaceFileText(opened.handle, body.content);
				send(response, 200, { path: opened.resolved.relative, content: body.content });
			} finally { await opened.handle.close(); }
			return;
		}
		if (request.method === 'POST' && url.pathname === '/v1/workspaces') {
			const body = await readJson(request);
			const grant = requireGrant(body.grantId);
			const resolved = await resolveGrantedPath(grant, body.path);
			if (!resolved.stat.isDirectory()) fail(400, 'A workspace must be a real directory.');
			for (const workspace of workspaces.values()) {
				if (workspace.owner === token.key && workspace.grantId === grant.id && workspace.relative === resolved.relative) {
					send(response, 200, { workspace: publicWorkspace(workspace) }); return;
				}
			}
			if (workspaces.size >= MAX_WORKSPACES) fail(429, 'The workspace limit was reached.');
			const workspace = {
				id: randomUUID(), name: path.basename(resolved.absolute), hostId: host.id,
				grantId: grant.id, path: resolved.absolute, relative: resolved.relative,
				stat: resolved.stat, owner: token.key
			};
			workspaces.set(workspace.id, workspace);
			send(response, 200, { workspace: publicWorkspace(workspace) });
			return;
		}
		if (request.method === 'GET' && url.pathname === '/v1/workspaces') {
			send(response, 200, { workspaces: [...workspaces.values()].filter((workspace) => workspace.owner === token.key).map(publicWorkspace) });
			return;
		}
		if (request.method === 'POST' && url.pathname === '/v1/terminals') {
			const body = await readJson(request);
			const workspace = requireWorkspace(body.workspaceId, token);
			const grant = requireGrant(workspace.grantId);
			if (!grant.capabilities.execute) fail(403, 'Host execution was not approved for this grant.');
			await resolveGrantedPath(grant, workspace.relative);
			if (!tokens.has(token.key) || !grants.has(grant.id)) fail(403, 'The session or directory grant was revoked.');
			if (terminals.size >= 64) fail(429, 'The terminal history limit was reached. Restart the host to clear it.');
			if ([...terminals.values()].filter((terminal) => terminal.status !== 'closed').length >= MAX_TERMINALS) fail(429, 'The terminal session limit was reached.');
			const terminal = {
				id: randomUUID(), workspaceId: workspace.id, owner: token.key,
				status: 'ready', output: Buffer.alloc(0), exitCode: null, child: null,
				lastUsed: Date.now(), timer: null, completion: Promise.resolve()
			};
			terminals.set(terminal.id, terminal);
			send(response, 200, { terminal: publicTerminal(terminal) });
			return;
		}
		const terminalMatch = /^\/v1\/terminals\/([a-f0-9-]+)(\/commands)?$/.exec(url.pathname);
		if (terminalMatch) {
			const terminal = requireTerminal(terminalMatch[1], token);
			if (request.method === 'GET' && !terminalMatch[2]) { send(response, 200, { terminal: publicTerminal(terminal) }); return; }
			if (request.method === 'DELETE' && !terminalMatch[2]) { await stopTerminal(terminal); send(response, 200, { terminal: publicTerminal(terminal) }); return; }
			if (request.method === 'POST' && terminalMatch[2]) {
				const body = await readJson(request);
				const workspace = requireWorkspace(terminal.workspaceId, token);
				const result = await runCommand(terminal, workspace, body);
				send(response, 200, { terminal: result }); return;
			}
		}
		fail(404, 'Companion endpoint not found.');
	}

	const server = http.createServer((request, response) => {
		route(request, response).catch((error) => {
			if (response.writableEnded) return;
			let status = 500;
			let message = 'The execution host could not complete this request.';
			if (error instanceof RequestError) { status = error.status; message = error.message; }
			else if (['ENOENT', 'ENOTDIR'].includes(error.code)) { status = 404; message = 'The granted file or directory was not found.'; }
			else if (['EACCES', 'EPERM', 'ELOOP'].includes(error.code)) { status = 403; message = 'Filesystem access was refused.'; }
			send(response, status, { error: message });
		});
	});
	server.headersTimeout = 10000;
	server.requestTimeout = 15000;
	server.keepAliveTimeout = 1000;
	const sweepPeriod = Math.max(10, Math.min(1000, tokenTtlMs, sessionIdleMs));
	const sweep = setInterval(() => {
		const now = Date.now();
		for (const token of tokens.values()) {
			if (now >= token.expiresAt) trackCleanup(revokeToken(token));
		}
		for (const terminal of terminals.values()) {
			if (terminal.status !== 'closed' && now - terminal.lastUsed >= sessionIdleMs) trackCleanup(stopTerminal(terminal));
		}
	}, sweepPeriod);
	sweep.unref();
	let closePromise;
	async function close() {
		if (closePromise) return closePromise;
		closing = true;
		clearInterval(sweep);
		closePromise = (async () => {
			await Promise.allSettled([...terminals.values()].map(stopTerminal));
			await Promise.allSettled([...pendingCleanup]);
			tokens.clear(); workspaces.clear(); terminals.clear(); grants.clear();
			if (server.listening) {
				await new Promise((resolve) => { server.close(resolve); server.closeIdleConnections?.(); });
			}
		})();
		return closePromise;
	}
	return { server, pairingCode, host, close, revokeGrant };
}
