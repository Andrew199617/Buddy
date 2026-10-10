# Buddy Runner project capabilities

Buddy uses one execution host: the Python Buddy Runner on **127.0.0.1:8765**.
The port remains configurable. This matches the existing Machines convention,
avoids a second host on 8083, and is separate from Buddy's backend 8081 and
frontend 8082. Tests use ephemeral loopback ports. No service is installed.

The Runner has two independent authorization boundaries:

| API | Client | Authority |
| --- | --- | --- |
| Provider execution (`/health`, `/run`, `/process`) | Buddy backend | Privileged Runner key; OS-account execution |
| Workspace API (`/v1/*`) | Browser, mobile, future desktop | Temporary origin-bound pairing token; explicit project grants |
| Operator controls | Host operator | Privileged Runner key; browser Origin requests refused |

The privileged key never goes to the workspace browser. Provider credentials
remain on the selected provider machine. Provider login, chat organization
folders, machine registration and a supplied path never create project grants.

## Start on the computer containing the project

Use Python 3.11 with Buddy's dependencies. From the checkout:

```powershell
# No roots, writes or workspace commands are approved by default.
local\start-buddy-runner.ps1

# Approve read-only access to exactly this project for this Runner instance.
local\start-buddy-runner.ps1 -Root 'C:\Projects\Example'

# Deliberately approve existing-file edits and whole-account commands.
local\start-buddy-runner.ps1 -Root 'C:\Projects\Example' -WriteFiles -AllowHostExecution

# A worktree can use an existing dependency environment.
local\start-buddy-runner.ps1 -PythonPath 'C:\path\to\venv\Scripts\python.exe' -Port 8766 -Root 'C:\Projects\Example'
```

The Python CLI equivalents are repeated `--root`, `--write`,
`--allow-host-execution`, `--name`, `--port`, and `--origin`. The default allowed
browser origins are exactly localhost and 127.0.0.1 on ports 8081 and 8082.
Supplying `--origin` replaces these defaults. `--allow-no-origin` explicitly
permits non-browser workspace clients; it is off by default. Non-loopback binds
are rejected. The old Node Companion CLI and server have been retired.

With approved roots, the host prints a one-use pairing code valid for five
minutes. Inspect the host name, current instance ID and endpoint in Buddy's
project UI, then pair. Tokens expire after 30 minutes and stay in client memory.
The same instance ID is shown by discovery, pairing and workspace responses.
Restart changes it and invalidates all previous tokens, grants and sessions.

## Refresh pairing and revoke access without restarting providers

A host operator can obtain a fresh one-use code through
`POST /workspace-pairing`, authenticated with the privileged Runner key from
that host. The response is `{code, expiresAt, host}`. It invalidates the previous
unused code and resets pairing attempts, while retaining existing grants,
paired sessions and provider processes. The browser must never perform this
operator request or receive that key. The code can then be entered on another
device. At most 64 paired sessions are retained.

`GET /workspace-grants` lists the operator's current grant IDs.
`DELETE /workspace-grants/{grant_id}` revokes one grant and stops its command
sessions. These are key-authenticated operator routes that reject browser Origin
requests. Revocation cannot approve or add a new root. Restart with explicit
`--root` arguments to change the approved directory set. Workspace clients
revoke only their own session through `DELETE /v1/session`.

The existing provider key is persistent and grants full execution as the Runner
account. Browser tokens and directory approvals remain temporary and separate.
Protect the Runner state directory and Buddy's configuration database as
privileged secrets; neither belongs in source control or browser storage.

## Files, folders and commands

The directory picker browses approved roots and selects a real project folder.
Direct file reads and edits use opaque grants and forward-slash relative paths.
Volume roots, traversal, drive/UNC paths, symlinks, junctions, reparse points and
multiply linked files are refused. Replacing an approved root invalidates access.
Only existing UTF-8 text files up to 1 MiB are supported; editing additionally
requires the host's explicit write approval.

**Opt-in commands run as the host OS account and can access beyond the selected
project. Their working directory is not a sandbox.** Provider read access can
also be broader: Codex read-only mode blocks writes but permits reads elsewhere
on disk. Those permissions are independent of project file API grants.

Command sessions accept an executable plus a JSON string-array of arguments,
without an implicit shell. They use pipes, not a PTY or persistent shell state.
Limits include 256 KiB retained output, 60-second commands, eight active sessions,
64 retained sessions and ten-minute idle expiry. Disconnect, token expiry, grant
revocation, stop and shutdown dispose owned commands. Native Windows Job Objects
contain descendants even after their leader exits; no PowerShell supervisor is
needed. POSIX uses process groups. Deliberately detached POSIX processes require
stronger OS isolation; no account-wide command API is a filesystem sandbox.

Provider JSON pipes retain separate stderr and longer-lived stdin. Their pending
output and short-command captures are bounded; timeouts and disconnects dispose
the process tree. A Runner shutdown closes active provider processes as well.
If a connected startup fails before a process identity or verified stop arrives,
the provider retains an unconfirmed cleanup state and refuses new execution.
The host operator must stop/restart that Runner, then restart Buddy's backend to
clear that state. Ordinary failures to connect do not create this state. Failed
cleanup retains the known process handle. Native cleanup can retry verification;
an unconfirmed remote stop after transport loss needs operator recovery.

## Browser, phone, Docker and other computers

A registered machine's `url` is reached by Buddy's backend, for example
`http://host.docker.internal:8765` from Docker Desktop. Its optional `browser_url`
is a separately configured HTTPS origin or loopback HTTP origin. It is never
inferred from the backend address. Registry metadata contains no key or grants.

A phone's 127.0.0.1 addresses the phone itself. Remote PC/cloud access needs an
approved private HTTPS transport to the execution host's loopback listener,
rewriting its upstream Host header and preserving the browser Origin. No TLS,
VPN, firewall, public listener, persistent service or Docker change is performed
by this implementation. Future desktop clients can use the same versioned API.

Discovery is `GET /v1/capabilities`; it advertises API support and observed host
identity, not permission. Actual file/write/execute authority comes only from
paired `GET /v1/grants`. Choosing a provider machine clears inherited access and
paths; removing one disables its providers instead of silently using This server.
After deletion, explicitly select a different computer or This server. The saved
selection remains disabled with chat-only access and empty paths; enable it in a
separate save. An unconfirmed old-process cleanup still blocks that recovery.
Remote mutating provider actions include the expected saved machine ID and
opaque revision, so stale UI requests fail before acting on a replaced machine
even when its ID stays the same. Legacy registrations need verification and
saving before remote mutations. Changing a backend URL, key or observed host
instance disables affected providers, clears inherited access and paths, and
quiesces old operations. Editing only the name or browser address grants nothing
and retains the execution revision.

Provider execution and host transitions require a single Buddy backend worker.
The server refuses new provider execution and mutations with multiple workers,
because process ownership and cancellation cannot be coordinated across them.

## Validation

Run isolated Python fixtures; they create temporary state/project directories,
ephemeral loopback servers and disposable Python children. They do not call real
providers, read account credentials or approve live directories:

```powershell
.venv\Scripts\python.exe -B -m unittest discover -s local\tests -p 'test_runner*.py' -v
.venv\Scripts\python.exe -B -m unittest discover -s local\tests -p test_process_containment.py -v
.venv\Scripts\python.exe -B -m unittest discover -s local\tests -p test_remote_process_cleanup.py -v
.venv\Scripts\python.exe -B -m unittest discover -s local\tests -p 'test_subscription*.py' -v
.venv\Scripts\python.exe -B -m unittest discover -s local\tests -p test_provider_host_transitions.py -v
```

See [validation evidence](VALIDATION.md). Native Windows coverage does not imply
POSIX runtime coverage. The UI and combined desktop/mobile fixtures are delivered
in the companion styling stack; backend PR #3 intentionally contains no src UI.
