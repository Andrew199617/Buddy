# Buddy

Buddy is the [Andrew199617/Buddy](https://github.com/Andrew199617/Buddy) fork of
Open WebUI. It adds local runtime patches, mobile chat improvements, reasoning
controls, usage details, and read-only research tools. Buddy has its own visual identity across the interface, notifications, splash
screen, and installed-app icons. The upstream license notices remain in this
repository; see [the brand guide](static/BRANDING.md) for assets and colors.

## Windows setup

The custom runtime is tested with Python 3.11 and Open WebUI **0.11.4**. From
the repository root, create the environment and install the pinned runtime:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install open-webui==0.11.4
```

`local-requirements.txt` records the dependency versions from the working
Windows installation if you need to reproduce that environment exactly.

Build the Buddy frontend with Node.js 22 (the version supported by this checkout):

```powershell
npm ci
$env:NODE_OPTIONS = '--max-old-space-size=8192'
npm run build
```

Start **Start Buddy.bat** or run `start-buddy.cmd`. Open
`http://localhost:8080` and create the first administrator account. The launcher
uses this checkout's backend and `build/` frontend with the pinned Python
runtime's dependencies and the patches in `local/`. Rebuild the frontend after
changing interface code or branding assets.

Application data and caches stay in their existing `open-webui-data/` directory,
so existing accounts and conversations continue to work. The legacy launchers
remain aliases for Buddy. The normal port 8080 launcher replaces the older
server; the parallel snapshot below can run alongside it on port 8081.

For a parallel instance, create an independent snapshot with a consistent
SQLite backup. This copies accounts, chats, settings, uploads, and the vector
store; copied active automations are paused so scheduled work is not repeated.
Model caches remain in their existing location.

```powershell
.venv\Scripts\python.exe local\snapshot_buddy_data.py
.\start-buddy.ps1 -Port 8081 -DataDir "$PWD\buddy-data-8081" -CacheDir "$PWD\open-webui-data\cache"
```

The snapshot helper refuses to overwrite an existing destination. The normal
launcher defaults remain port 8080 and `open-webui-data/`. `-PythonPath` can point
to an existing environment when launching a worktree without its own `.venv`;
that worktree still serves its own tracked backend and `build/` frontend. Keep
a copy of the existing private `.webui_secret_key` beside the worktree launcher
or provide `WEBUI_SECRET_KEY` privately when retaining existing sessions. Each
parallel instance needs its own data directory and static assets; only the
model cache is reused. Launcher aliases forward these optional parameters.

After the first sign-in, install the reasoning filter:

```powershell
.venv\Scripts\python.exe local\install_functions.py
```

The installer updates the Functions in `local/functions/` in your local
database. Re-run it after changing those files. See [local customization
details](local/README.md) for the individual features and update instructions.

## Redesign startup performance

The redesign on port 8082 runs an optimized production frontend against the
existing Buddy backend on 8081. Use `local/serve-frontend.mjs` after building;
Vite dev is intended for editing and loads substantially more browser modules.
See [the measured startup report](local/STARTUP-PERFORMANCE.md) for timings,
comparison controls, deferred features, and the exact launch command.

## Install Buddy on iPhone

For the current Buddy redesign, open `http://100.122.80.32:8082/` in Safari on
an iPhone connected to the same Tailscale network. Install from this exact
address so the home-screen app uses the redesign's port.

1. Open Buddy in Safari and sign in.
2. Tap the Share button, or open the Page Menu and choose **Share**.
3. Choose **Add to Home Screen**.
4. On iOS 26 or later, leave **Open as Web App** enabled.
5. Tap **Add**, then launch Buddy using its new home-screen icon.

If an older Buddy icon opens a different host or port, remove that home-screen
icon and add Buddy again from the address above. Changing the port changes the
app's origin; an existing installation cannot cover another port through its
manifest scope.

Buddy's manifest covers the entire app on the installed origin. External
sign-in and other links outside that scope can open an iOS browser panel.
Buddy navigation and OAuth returns should stay on the address used to install
the app. The panel's browser controls belong to iOS and cannot be hidden by
Buddy's styles.

See Apple's [home-screen installation instructions](https://support.apple.com/guide/iphone/iphea86e5236/ios)
and [web app scope guidance](https://developer.apple.com/videos/play/wwdc2023/10120/).

## MCP tool OAuth callback addresses

Buddy registers and uses the actual browser-facing request scheme, host, and port
for MCP tool OAuth, including Slack. Open the app at the address where you want
to return after sign-in. Reverse proxies must preserve the original `Host` and
configure trusted proxy handling in the application server; forwarding `/api`
and `/oauth` must not replace the browser's host with the backend target.

Loopback hosts (`localhost`, `127.0.0.1`, and `::1`) and the hostname in the
administrator's configured app URL are trusted by default. To use Tailscale or
another local hostname, set the allowed hosts before starting Buddy:

```powershell
$env:MCP_OAUTH_ALLOWED_REDIRECT_HOSTS = '100.122.80.32,sd-anvelez-03.tail83dea0.ts.net'
.\start-buddy.ps1 -Port 8081 -DataDir "$PWD\buddy-data-8081" -CacheDir "$PWD\open-webui-data\cache"
```

These host entries allow HTTP or HTTPS on any valid port. The current local
runtime uses the two Tailscale hosts shown above. Optional
`MCP_OAUTH_ALLOWED_REDIRECT_ORIGINS` accepts comma-separated full origins such as
`https://buddy.example:9443` when only a particular scheme and port should be
allowed. Each OAuth flow keeps its exact registered callback URI through token
exchange and returns to the initiating app address. Existing tool registrations
and saved tokens stay intact; Buddy registers a separate dynamic client when a
new callback address is needed. For static OAuth credentials, the provider must
also allow that exact callback URI.

## Research tools

Import each tool's JSON bundle through **Workspace → Tools → Import JSON**.
Authenticate its provider separately as described in its setup guide.

| Tool                   | Source and setup                                                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Local Perforce         | `local-p4-tool.py` and `local-p4-tool.json`; requires the Perforce CLI and an existing local workspace                             |
| Slack Research         | [Setup](slack-research-setup.md), `local-slack-research.py`, `local-slack-research.json`                                           |
| Slackdump Research     | [Setup](slackdump-research-setup.md), [research workflow](slack-research.md), `slackdump-research.py`, `slackdump-research.json`   |
| Outlook Research       | [Setup](outlook-research-setup.md), `local-outlook-research.py`, `local-outlook-research.json`                                     |
| Unified communications | [Project guide](p4-unified-communications/README.md) and [setup](p4-unified-communications/SETUP.md) for mail, calendar, and Teams |

Credentials, user databases, provider profiles, research results, logs,
virtual environments, caches, and generated visual diagnostics are ignored.
They must be configured locally on each machine.

## Validation

Run the existing customization and research-tool tests from the repository
root:

```powershell
.venv\Scripts\python.exe -m unittest discover -s local\tests -v
node --test local\tests\*.test.mjs
.venv\Scripts\python.exe -m unittest discover -s test -p test_outlook_research_tool.py -v
.venv\Scripts\python.exe -m unittest discover -s p4-unified-communications\tests -v
```

These suites use mocks and temporary files; they do not authenticate providers
or verify live account access. Additional visual checks are documented in
[the browser regression guide](local/tests/browser/README.md).
