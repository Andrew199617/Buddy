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

## Projects on an execution host

Buddy Runner now provides both backend provider execution and the separately
paired Companion workspace API. The configurable default is **127.0.0.1:8765**,
matching the existing Machines runner; a second Node host on 8083 is no longer
required. Its host operator explicitly approves project roots and read/write/
command capabilities. Browser paths, chat folders, provider working directories,
and provider sign-in never approve a filesystem grant.

See [the Companion guide](companion/README.md) for startup, pairing, operator
revocation and the browser/mobile transport boundary. Provider commands run on
the selected subscription machine. Choosing a Companion project does not move
those commands or change their permissions. Direct file API access stays inside
approved projects; provider read access and opt-in commands can be broader.

## Claude and ChatGPT subscriptions

Buddy can chat with Claude and OpenAI models on your Claude Pro/Max and ChatGPT
Plus/Pro plans. Usage counts against the plan's limits instead of API billing.
Buddy runs the official command-line apps, Claude Code (`claude -p`) and Codex
(`codex app-server`), on a machine: the computer running Buddy, or a Buddy
Runner on another computer (see [Machines](#machines-and-the-buddy-runner)).
Each app signs in with its own flow and keeps its own credentials on that
machine. Buddy does not read or store your password or tokens.

Anthropic's help center says `claude -p` and the Claude Agent SDK draw from
your plan's limits. OpenAI documents Codex sign-in with ChatGPT for scripted
use. Buddy does not copy the apps' tokens into its own API calls, which the
plans do not allow. Keep this for your own use: only administrators can choose
these models, and sharing a personal plan with other people breaks its terms.

Set it up under **Admin Settings → Connections → Subscriptions**:

1. Install the app if it is missing. The Claude desktop app already includes
   Claude Code, which Buddy finds automatically. Install Codex with
   `npm install -g @openai/codex`.
2. Turn on **Claude** or **ChatGPT** and choose **Sign in**.
   - Claude: open the sign-in page, approve, then paste the code it shows.
     This works from a phone.
   - ChatGPT: **Sign in on this computer** returns to the computer running
     Buddy. **Use a device code** works from any device after you allow device
     code sign-in in ChatGPT's security settings. If Codex is already signed in
     on this computer, Buddy uses that sign-in.
3. The plan's models appear in the model picker, tagged **Claude plan** or
   **ChatGPT plan**. ChatGPT shows how much of its usage limit you have used.

If you run Buddy with more than one worker (`UVICORN_WORKERS`), sign in from a
terminal instead (`claude auth login` or `codex login`); the CLIs keep the
sign-in on disk, so every worker sees it. Accounts that bill the API (an
Anthropic Console login or a Codex API key) are flagged and their models are
hidden, so a plan-tagged model never bills the API.

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
any file your account can. Buddy turns off Codex hooks and MCP servers outside
Full access. Instruction files in the folder (`CLAUDE.md`, `AGENTS.md`) can
still steer the model.

**Full access** lets the model edit files and run terminal commands on the
machine as you, in the working folder you choose (`subscriptions/workspace` in
Buddy's data folder, or `~/.buddy-runner/workspace` on a runner, by default).
Anyone who can sign in to Buddy as an administrator can then do the same.
Status lines show each command while it runs, and the reasoning block keeps a
log of them.

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

### Machines and the Buddy Runner

A machine is where Claude Code and Codex run, sign in, and work on files. The
**Machines** list under Subscriptions always has **This server**, the
computer or container running Buddy. To use another computer, such as your
Windows PC while Buddy runs in Docker, start the Buddy Runner there and add it:

1. On that computer, run `local\start-buddy-runner.ps1` from this checkout
   (or `python -m open_webui.utils.subscriptions.runner` with `backend` on
   `PYTHONPATH`). It listens on `127.0.0.1:8765` and creates its key in
   `~/.buddy-runner/key` on first start.
2. In Buddy, choose **Add machine** and enter a name, the runner address, and
   the key, then **Verify**. From Buddy in Docker Desktop, the Docker host is
   `http://host.docker.internal:8765`.
3. Open a provider's gear button and set **Runs on** to that machine. Sign in
   from there; the sign-in lives on that machine.

The runner starts the CLIs over plain pipes and streams their output to Buddy;
a process stops when Buddy's connection to it closes. Anyone holding the
runner key can run any command as the user running the runner, so it listens
only on the local loopback by default and Buddy never sends the key to the
browser.

Changing a provider's machine clears its paths and access and disables it until
you explicitly configure that machine. Removing a machine disables affected
providers and preserves the missing selection; it never switches execution to
This server. Editing a registered target also invalidates its old provider work.

## Buddy in Docker

`docker/buddy/compose.yaml` runs Buddy as two containers: the backend (with
the `local/` patches) and the production frontend server. Data lives in the
`buddy_buddy-data` volume.

```powershell
Copy-Item docker\buddy\example.env docker\buddy\.env   # then fill in WEBUI_SECRET_KEY
docker compose -f docker/buddy/compose.yaml up -d --build
```

Open `http://localhost:8082` (or the Tailscale address on port 8082). The
backend is also published on 8081. `docker compose -f docker/buddy/compose.yaml
ps` shows what is running, and `logs -f buddy` follows the server log.

To move existing data in, stop the Windows instance using it, create the
volume, and copy the data folder over it; reuse that data's
`WEBUI_SECRET_KEY` so sign-ins and connected tools keep working:

```powershell
docker compose -f docker/buddy/compose.yaml run --rm --no-deps buddy true
docker run --rm -v buddy_buddy-data:/data -v "C:\path\to\data:/source:ro" alpine sh -c "cp -a /source/. /data/"
```

Claude Code and Codex are not installed in the image. Add a Buddy Runner on
your PC as a machine so they run there with your sign-ins, repositories, and
terminal. Inside the container, `localhost` is the container itself; use
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
