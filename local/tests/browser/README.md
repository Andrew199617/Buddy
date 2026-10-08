# Browser regression scripts

These scripts exercise Buddy's local UI extensions with synthetic users,
models, conversations, settings, and usage data. They cover phone and desktop
layouts, text scaling, keyboard resizing, native menus, and model editing.

The harness serves every `/api/` response from a fixture, intercepts attempted
writes, supplies a fake session token, and blocks requests to other origins.
Only HTML and static frontend assets come from the local server. No live
account API calls are made, and no model requests are sent.

## Setup

Use Node.js 20 or newer. These scripts were authored against the Windows
`.venv` installation of Open WebUI **0.11.4** and its bundled frontend. Install
that runtime and the local extensions as described in [the local README](../../README.md).
Keep the frontend server running at `http://localhost:8080` in another terminal:

```powershell
.\start-open-webui.ps1
```

The launcher applies the local loader scripts that these checks expect.
Install the separate browser-test dependencies from the repository root:

```powershell
Set-Location local\tests\browser
npm install
npx playwright install chromium webkit
Set-Location ..\..\..
```

Playwright is pinned to 1.62.1. These dependencies belong to this test directory;
the root frontend package does not need them.

## Run

Run these commands from the repository root. Assertions fail with a nonzero
exit status, and scripts print scenario details for inspection.

```powershell
node local/tests/browser/mobile-ui-preview.mjs
node local/tests/browser/mobile-scroll-preview.mjs
node local/tests/browser/chat-header-preview.mjs
node local/tests/browser/chat-actions-preview.mjs
node local/tests/browser/mobile-settings-preview.mjs
node local/tests/browser/model-activity-preview.mjs
node local/tests/browser/model-edit-preview.mjs
node local/tests/browser/final-mobile-regression.mjs
```

For a WebKit capture of the model picker and main phone layout, run:

```powershell
node local/tests/browser/mobile-ui-preview.mjs --webkit
```

Screenshots, failure diagnostics, and result JSON are written to the ignored
`local/tests/.qa/` directory. The harness creates that directory when needed.
The files already there are local artifacts, so repeated runs can replace
generated results with matching names.

The `mobile-ui-preview.mjs` command captures screens for visual review. The
other scripts assert geometry and interaction behavior. The scrolling script's
Apple rendering scenario simulates the vendor branch in Chromium; use the
explicit WebKit command above to exercise that browser engine.

For an additional native baseline capture, `mobile-ui-preview.mjs`,
`mobile-settings-preview.mjs`, and `model-edit-preview.mjs` accept `--baseline`.
The launcher can still inject other local extensions during those captures.
