# Buddy for Windows

The Buddy desktop app opens your Buddy in its own window and can connect this
computer to it, so Claude Code and Codex run here with your sign-ins and
files. People never start the Runner themselves; the app runs it in the
background. See [BUDDY.md](../BUDDY.md#connect-a-computer-with-the-buddy-app)
for what connecting means for users.

Windows is built and tested here. Mac is a follow-up: the code avoids
Windows-only APIs except the Runner build and the `claude setup-token`
terminal.

## How it works

- **Main process** (`src/main.ts`): the window, the first-launch
  "Your Buddy address" page, the taskbar icon, start at sign-in, and the
  bridge requests.
- **Bridge** (`src/preload.ts`, `src/ipc.ts`): `window.buddyDesktop`, which
  the web app reads through `src/lib/desktop/bridge.ts`. Keep the two in
  step.
- **Service** (`src/desktop-service.ts`): connecting, shared folders and
  permissions, each waiting for the person's answer in a Windows dialog
  (`src/native-dialogs.ts`).
- **Runner supervisor** (`src/runner-supervisor.ts`): starts
  `buddy-runner.exe run --connection-stdin`, reads its status lines, restarts
  it if it crashes, and stops it gracefully.
- **Storage**: `%APPDATA%\Buddy\settings.json` holds the address, shared
  folders and permissions. `machine-credential.bin` holds the computer's
  credential, encrypted with `safeStorage` (DPAPI). The Runner's data and log
  are `runner\` and `runner.log`.

## Security model

- The window shows only the Buddy address. Other links open in the system
  browser, and browser permissions (microphone, notifications, clipboard
  writes, fullscreen) are granted only to that address.
- `window.buddyDesktop` exists only on that address. The main process checks
  the sending frame again for every request and validates its arguments.
- The machine credential never reaches the web page. The page gets a
  single-use token from Buddy; the main process trades it for the credential
  itself and hands it to the Runner on stdin.
- Shared folders and the edit and command permissions live only in the app.
  Turning one on, and connecting, each show a Windows dialog the page cannot
  answer.
- Whoever controls the Buddy server can run Claude Code and Codex on a
  connected computer as its Windows account. The connect dialog says so.

## Develop

From this folder, with Node.js and the repository's Python environment:

```powershell
npm install
$env:BUDDY_DESKTOP_PYTHON = "..\.venv\Scripts\python.exe"   # runs backend\buddy_runner
npm start
```

| Variable | Use |
| --- | --- |
| `BUDDY_DESKTOP_PYTHON` | Python that runs the repository's Runner in development |
| `BUDDY_DESKTOP_RUNNER` | A `buddy-runner.exe` to use instead, such as one from `build-runner.ps1` |
| `BUDDY_DESKTOP_USER_DATA` | A throwaway data folder (development only) |

## Test

```powershell
npm test   # unit tests, no Electron window
```

The app smoke test drives the real app against a disposable Buddy: setup,
connecting this computer, sharing a folder, the address lock and
disconnecting. Native dialogs are answered by the test. It refuses the live
instance's ports.

```powershell
$env:BUDDY_DESKTOP_TEST_SERVER = "http://127.0.0.1:8098"
$env:BUDDY_DESKTOP_TEST_ACCOUNT = "<path to {email, password} of a test account>"
$env:BUDDY_DESKTOP_PYTHON = "..\.venv\Scripts\python.exe"
npm run test:app
```

## Build the installer

```powershell
.\build-runner.ps1 -Python ..\.venv\Scripts\python.exe   # resources\runner\buddy-runner.exe
npm run dist                                               # release\Buddy Setup <version>.exe
```

`build-runner.ps1` freezes `backend\buddy_runner` with PyInstaller in its own
environment (`runner-requirements.txt`) and checks the result starts.
`npm run dist` builds an NSIS installer for the current user, with the Runner
inside. Run `build-runner.ps1` again whenever `backend\buddy_runner` changes.

The installer is not signed yet, so Windows SmartScreen warns on first run.
Signing needs a code-signing certificate configured for electron-builder.

## Known limits

- Adding or removing a shared folder, or changing a permission, restarts the
  Runner, which stops a Claude or ChatGPT reply in progress on this computer.
- Sign-in pages on another address (single sign-on) open in the system
  browser, not the app, so the app supports Buddy's own accounts for now.
- Buddy keeps connections in one worker's memory, so the server must run a
  single worker.
