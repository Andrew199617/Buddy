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

## Projects on your computers

The **Projects** page opens folders on a computer you connected with the Buddy
app (see [Connect a computer](#connect-a-computer-with-the-buddy-app)). Buddy
relays the page's requests over that computer's own connection, so it works
from a phone or another browser with no address or pairing code. The computer
still decides what is possible: only folders shared in the app appear, and
editing files or running commands works only after you allow it there.

Under **Advanced**, the page can still pair with a Buddy Runner started on its
own, by address and one-time code; see [the Runner guide](companion/README.md).
Choosing a project never changes which computer Claude and ChatGPT run on, how
they sign in, or what they may do. Direct file access stays inside shared
folders; provider read access and opt-in commands can be broader.

## Claude and ChatGPT subscriptions

Buddy chats with Claude and OpenAI models through the official command-line
apps, Claude Code (`claude -p`) and Codex (`codex app-server`). Each person
uses their own Claude or ChatGPT plan, or their own API key, on computers they
connect themselves. Only that person can choose their models; nobody else sees
or uses them.

Anthropic's help center says `claude -p` and the Claude Agent SDK draw from
your plan's limits. OpenAI documents Codex sign-in with ChatGPT for scripted
use. Buddy never copies the apps' own tokens into its API calls and never reads
their credential files (`~/.claude/.credentials.json`, `~/.codex/auth.json`).
It stores a token or key only when you paste one in, encrypted. A plan is
personal: sign in only with your own.

### Connect a computer with the Buddy app

1. Install **Buddy for Windows** on the computer (Mac is coming). An
   administrator can show its download link in Settings → Machines; to build
   the installer, see [the desktop app guide](desktop/README.md).
2. Open the app, enter your Buddy address, and sign in.
3. In **Settings → Machines**, choose **Connect this computer** and confirm in
   the app's own dialog. The computer appears under **Your computers** and stays
   connected while the app runs. **Start Buddy when you sign in** keeps it
   available from your phone.

The app connects out to Buddy over one WebSocket, so the computer needs no open
port and no certificate. Only the Buddy server needs an ordinary HTTPS address;
plain HTTP is accepted only when Buddy runs on the same computer.

- **The connection's credential** is encrypted for your Windows account by the
  app. Buddy keeps only its hash and never shows it to the web page.
- **Shared folders** and the **Allow editing files** and **Allow running
  commands** switches are chosen in the app, with Windows dialogs, so the Buddy
  server cannot widen them.
- **Whoever controls the Buddy server** can run Claude Code and Codex on a
  connected computer as your account. The app says so before connecting.
- **Disconnect** from Settings → Machines or the app's taskbar icon. Removing a
  computer from the list in Buddy disconnects its app too.

Connected computers need a single Buddy worker (`UVICORN_WORKERS=1`, the
default): their connections live in that worker's memory.

### Set up Claude and ChatGPT

Under **Settings → Machines → AI plans**:

1. Install the CLI on the computer if it is missing. The Claude desktop app
   already includes Claude Code, which Buddy finds automatically. Install Codex
   with `npm install -g @openai/codex`.
2. Turn on **Claude** or **ChatGPT**, open the gear button, and choose the
   computer it **Runs on**.
3. Choose how it signs in:
   - **Your computer's sign-in** uses the CLI's own sign-in on that computer.
     **Sign in** starts it from Buddy. Claude shows a page to approve and a
     code to paste back, which works from a phone. ChatGPT's **Get sign-in
     link** must open in a browser on that computer; **Use a device code
     instead** works anywhere after you allow device code sign-in in ChatGPT's
     security settings.
   - **Claude token, for all your computers**: run `claude setup-token` once
     and paste the token (in the app, **Open a terminal** starts it for you).
     It lasts a year and works on every computer you connect. Buddy warns 30
     days before it expires.
   - **Anthropic API key** or **OpenAI API key**: billed per use by the
     provider, on every computer you connect. Codex gets the key for each
     session without saving it on the computer.
4. The models appear in your model picker, tagged **Claude plan** or
   **ChatGPT plan**. ChatGPT shows how much of its usage limit you have used.

In **Your computer's sign-in**, an account that bills the API (an Anthropic
Console login or a Codex API key login) is flagged and its models are hidden,
so a plan-tagged model never bills the API by surprise.

Tokens and keys are encrypted with `BUDDY_SUBSCRIPTION_SECRET_KEY`, which by
default is the key that protects tool sign-ins (and through it,
`WEBUI_SECRET_KEY`). To change it, set the new key and list the old one in
`BUDDY_SUBSCRIPTION_OLD_SECRET_KEYS` until everyone has saved their
credentials again. The browser only ever sees a fingerprint and the expiry
date.

### Policy for administrators

**Admin Settings → Connections → Subscriptions** sets:

- **Who can connect computers and use AI plans**: everyone, selected groups,
  or administrators only. Computers of people who are no longer allowed
  disconnect at once.
- **Plan sign-in**: turn it off before offering Buddy publicly. Anthropic does
  not allow third-party products to offer Claude plan sign-in without its
  approval. API keys stay available.
- **Personal API keys**.
- **Run on this server**: lets the instance owner (the first administrator)
  also run the CLIs inside the Buddy server, as **This server**.
- **Buddy for Windows download link**: shown in Settings → Machines to people
  using a browser.

**Everyone's computers** lists each connected computer with its owner and
status, without any secrets; **Remove** disconnects it. Cloud computers, a
Runner container per person, are designed but not built yet; see
[the cloud Runner design](companion/CLOUD_RUNNERS.md).

### What the models may do

The gear button sets what the models may do:

| Setting     | Claude Code                                        | Codex                              |
| ----------- | -------------------------------------------------- | ---------------------------------- |
| Chat only   | No tools; Buddy's chat instructions                | No command, file, or MCP tools     |
| Read files  | Read, search, and web tools in the working folder  | Read-only commands; no MCP servers |
| Full access | All tools, including the terminal, without prompts | No sandbox, no approval prompts    |

In **Read files**, Claude Code's file tools stay inside the working folder,
and it ignores the folder's settings, hooks, MCP servers, and skills. Codex's
read-only sandbox blocks writes but cannot limit reads to one folder (custom
read rules need Codex's elevated Windows sandbox), so a Codex model can read
any file your account can. Buddy turns off Codex hooks in every setting and
Codex MCP servers outside Full access. Instruction files in the folder
(`CLAUDE.md`, `AGENTS.md`) can still steer the model.

**Full access** lets the model edit files and run terminal commands on the
computer as you, in the working folder you choose (by default `workspace` in
the Runner's data folder: `%APPDATA%\Buddy\runner\workspace` for the app,
`~/.buddy-runner/workspace` for a Runner started on its own, or
`subscriptions/workspace` in Buddy's data folder on This server). Status lines
show each command while it runs, and the reasoning block keeps a log of them.

A model someone else shared with you, for example in a preset, runs on your
own plan and computer in **Chat only**.

Notes:

- Title, tag, and follow-up generation run as short, tool-free turns at the
  lowest reasoning level. To keep them off your plan, set a **Task Model**
  in Admin Settings → Interface.
- A chat keeps one CLI session while you continue it, so the provider's prompt
  cache applies. Editing or regenerating an earlier message starts a fresh
  session that is given the chat so far.
- The thinking chip sets the reasoning effort. Off uses the lowest level.
- Codex threads use your Codex `AGENTS.md`. Buddy disables Codex's desktop
  plugins (computer use, browser, apps) for its threads.
- These models cannot receive Buddy's native tool definitions, so Buddy uses
  prompt-based (legacy) function calling for them. Attached files, knowledge,
  web search, and research tools still reach the model as context.

### Older setup: a Runner by address

Before the app, the instance owner ran the Buddy Runner by hand and added it by
address. This keeps working until production has moved to the app, and is
listed as **Your Runner addresses (older setup)** in admin Connections:

1. On that computer, run `local\start-buddy-runner.ps1` from this checkout in
   PowerShell. From Command Prompt, or where PowerShell scripts are disabled,
   run `local\start-buddy-runner.cmd` with the same parameters. (Without the
   launchers, run `python -P -m buddy_runner` with
   `backend` on `PYTHONPATH`; `-P` stops modules in the current folder from
   loading.) It listens on `127.0.0.1:8765` and creates its key in
   `~/.buddy-runner/key` on first start. On Windows, that folder and key must
   be private to your account. A folder from an earlier version can be shared
   through your profile, and the Runner then refuses to start; fix it once
   with the steps in [the Runner guide](companion/README.md#keep-the-runner-key-private).
2. In Buddy, choose **Add machine** and enter a name, the runner address, and
   the key, then **Verify**. From Buddy in Docker Desktop, the Docker host is
   `https://host.docker.internal:8765`, after configuring Runner TLS and the
   backend's application-specific certificate trust. Every non-loopback
   connection requires verified HTTPS, including Docker and Tailscale names
   and addresses. See [the HTTPS setup](companion/TLS_SETUP.md).
3. Open a provider's gear button and set **Runs on** to that machine.

Anyone holding that Runner's key can run any command as the user running it,
so it listens only on the local loopback by default and Buddy never sends the
key to the browser. Changing a provider's machine clears its paths and access
and disables it until you configure that machine. Removing a machine disables
affected providers and never switches execution to This server.

## Buddy in Docker

`docker/buddy/compose.yaml` runs Buddy as two containers: the backend (with
the `local/` patches) and the production frontend server. Data lives in the
`buddy_buddy-data` volume.

```powershell
Copy-Item docker\buddy\example.env docker\buddy\.env   # then fill in WEBUI_SECRET_KEY
docker compose -f docker/buddy/compose.yaml up -d --build
```

Open `http://localhost:8082` (or the Tailscale address on port 8082). Add a
Tailscale host name to `BUDDY_ALLOWED_HOSTS` in `docker/buddy/.env`, and the
Tailscale IP as well if MCP sign-ins go through it. The backend is also published on 8081. `docker compose -f docker/buddy/compose.yaml
ps` shows what is running, and `logs -f buddy` follows the server log.

If Buddy trusts a Runner's certificate through `docker/buddy/runner-tls.yaml`
(see [the HTTPS setup](companion/TLS_SETUP.md)), add that overlay after
`compose.yaml` to every compose command in this section, including the
`up -d --build` you run to update. Setting `BUDDY_RUNNER_CA_HOST_FILE` in
`.env` alone does not add it, and a command without the overlay recreates the
backend without the Runner CA, so every HTTPS Runner then fails certificate
verification:

```powershell
docker compose -f docker/buddy/compose.yaml -f docker/buddy/runner-tls.yaml up -d --build
```

To move existing data in, stop the Windows instance using it (and Buddy in
Docker if it is running: `docker compose -f docker/buddy/compose.yaml stop`),
create the volume, and copy the data folder over it; reuse that data's
`WEBUI_SECRET_KEY` so sign-ins and connected tools keep working:

```powershell
docker compose -f docker/buddy/compose.yaml run --rm --no-deps --build buddy true
docker run --rm -v buddy_buddy-data:/data -v "C:\path\to\data:/source:ro" alpine sh -c "cp -a /source/. /data/"
```

Buddy stores each upload's full path, so the copied database still points at
the Windows folder and earlier uploads will not open. Point those paths at the
container's data folder, passing the same Windows folder you copied. Check the
dry run first: `matching_paths` counts the uploads it will update, and
`missing_files` should be 0. Then apply it and start Buddy:

```powershell
docker compose -f docker/buddy/compose.yaml run --rm --no-deps buddy python /app/local/update_upload_paths.py --old-data-dir "C:\path\to\data" --dry-run
docker compose -f docker/buddy/compose.yaml run --rm --no-deps buddy python /app/local/update_upload_paths.py --old-data-dir "C:\path\to\data"
# With the Runner TLS overlay, add -f docker/buddy/runner-tls.yaml after compose.yaml.
docker compose -f docker/buddy/compose.yaml up -d --build
```

The Windows folder is left unchanged as a backup. Do not start the Windows
instance on it again: it would run the same automations as Buddy in Docker.

Claude Code and Codex are not installed in the image. Connect your PC with the
Buddy app, using `http://localhost:8082` as its Buddy address, so they run there
with your sign-ins, repositories, and terminal (see
[Connect a computer](#connect-a-computer-with-the-buddy-app)). Inside the
container, `localhost` is the container itself; use
`http://host.docker.internal:<port>` for services on your PC, such as a
llama.cpp server.

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
