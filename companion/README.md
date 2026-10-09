# Buddy companion

The companion is a separate execution host. Buddy's browser/mobile UI connects
directly to its versioned JSON API; a future desktop UI can use the same API.
Run the host on the computer containing the project: Andrew's PC initially,
another PC or a private cloud machine later. Model-provider login, Buddy account
authentication, and chat organization folders remain separate from host pairing
and filesystem permissions.

## Initial local setup

Use Node.js 22 from the Buddy checkout. The companion needs no npm dependencies.

```powershell
# Start safely with no project grants and command execution disabled.
node companion/cli.mjs

# Read-only access to this explicitly chosen project, for this process only.
node companion/cli.mjs --root "C:\Projects\MyProject"

# Override the port and the exact browser origin when needed.
node companion/cli.mjs --port 8084 --origin http://localhost:8082 --root "C:\Projects\MyProject"
```

Port **8083** is Andrew's chosen companion default next to the running Buddy
frontend on **8082** and backend on **8081**. A read-only listener check found
8083 available before local setup. Plane already owns ports 3300 and 3301 and
was left running. The CLI binds `127.0.0.1` and fails clearly if the requested
port is occupied; `--port` remains configurable. Tests use ephemeral loopback
ports. Existing Buddy launchers are unchanged.

Open **Companion** in Buddy's sidebar, inspect the machine name, host ID and
endpoint, then enter the short-lived one-time code printed in the host's local
terminal. The default allowed Buddy origins are the exact `localhost` and
`127.0.0.1` origins on ports 8081 and 8082, covering the integrated Buddy app and
its separate frontend. Supply `--origin` for a different actual browser origin.
Pairing grants access only to the roots the host operator supplied. No roots
are approved by default. Browser paths, uploaded folders, chat folder names,
and pairing alone never add host directories.

The picker browses within approved roots and selects a real project directory.
Text files can be read; updating an existing file requires the explicit host
`--write` capability. Symlinks/junctions and multiply linked files are refused,
including links inside an approved root. Filesystem-volume roots cannot be
granted. This deliberately excludes linked project directories in this first
foundation.

## Commands and terminal sessions

Execution is off by default. To enable it deliberately for the supplied roots:

```powershell
node companion/cli.mjs --root "C:\Projects\MyProject" --allow-host-execution
```

**Commands run with the companion process's OS permissions and can access
beyond the selected directory. A working directory is not an OS sandbox.**
Read/write API checks enforce directory scope; command execution is a separate
capability. An OS sandbox or dedicated low-privilege execution account is still
needed before treating arbitrary commands as directory-confined.

The UI creates an owned command session in the selected project and accepts an
executable plus a JSON string-array of arguments. The host uses argv execution
without an implicit shell. Pipe-backed sessions support output, successive
commands and stop/close; they are not a PTY or a persistent interactive shell.
Shell state such as `cd` and environment changes does not survive between
commands. Limits are 1 MiB text files, 256 KiB retained command output, 60-second
commands, eight active sessions, 64 retained sessions and 10-minute idle expiry.
Token expiry, disconnect, root revocation and shutdown clean up sessions.
Windows uses a transient hidden Windows PowerShell supervisor with a native
kill-on-close Job Object; Windows PowerShell is required for execution. POSIX
uses process groups. No helper is installed persistently.

## Other computers, phones and future desktop clients

`127.0.0.1` on a phone refers to the phone. It does not reach Andrew's PC. Choose
the authenticated host's HTTPS endpoint when a private remote transport is
configured. The UI always displays the execution host and endpoint beside the
active project, file and command session.

Keep the companion on loopback on whichever execution machine owns the project.
A later private HTTPS reverse proxy/tunnel can reach that loopback listener,
rewrite upstream `Host` to its actual loopback address/port, preserve `Origin`,
and allow only the exact Buddy browser origin configured with `--origin`.
The client accepts HTTPS endpoints on other computers and HTTP only on loopback.
The host address and port are configurable independently of the Buddy app.

This foundation does not configure a tunnel, TLS, firewall rule, public bind,
desktop package or persistent service. Remote deployment and device networking
need a separate approved setup. Model logins can change independently without
changing host permissions or passing provider credentials to the companion.

## Security and lifecycle

- Random host identity, pairing code and bearer sessions exist in memory for
  one host process. Codes are expiring and single-use with bounded attempts;
  tokens expire after 30 minutes and are bound to the pairing browser origin. Restart to
  issue a new code. Multi-device simultaneous pairing is future work.
- Tokens stay in the client instance, never browser storage, URLs or Buddy's
  database. Reload/navigation requires a fresh host pairing. Disconnect attempts
  immediate revocation; unexpected network loss is bounded by server idle/token
  expiry rather than a guaranteed immediate signal.
- Exact Origin and Host validation defend cross-site requests and DNS rebinding.
  Requests without Origin require explicit `--allow-no-origin` operator opt-in
  for non-browser clients. CORS is not treated as authentication.
- Every directory, file and workspace operation validates an opaque grant,
  relative path, canonical containment and unchanged root identity. Absolute,
  traversal, drive, UNC and link escapes are denied. Grants cannot be registered
  through the network API.
- No persistent credentials, grants or real project access were created during
  implementation. Tests use only disposable fixture directories and processes.

## Validation

```powershell
npm run test:companion
node --test local/tests/*.test.mjs
npm run check
npm run build
```

The tests exercise the actual HTTP host with temporary directories, explicit
origins and disposable command children. See `VALIDATION.md` for the exact final
results, environment and known repository-wide type-check limitations.
