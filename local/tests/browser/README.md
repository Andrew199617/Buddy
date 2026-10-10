# Browser regression and startup measurements

The regression scripts exercise Buddy's local UI extensions with synthetic users,
models, conversations, settings, and usage data. They cover phone and desktop
layouts, text scaling, keyboard resizing, native menus, and model editing.

The regression harness serves every `/api/` response from a fixture, intercepts attempted
writes, supplies a fake session token, and blocks requests to other origins.
Only HTML and static frontend assets come from the local server. No live
account API calls are made, and no model requests are sent. The startup
benchmark described below uses a separate real authenticated workflow.

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

## Sidebar styling regressions

Build and serve the matching frontend, then point the synthetic browser checks
at its local origin:

```powershell
$env:BUDDY_UI_TEST_ORIGIN = 'http://127.0.0.1:8094'
node local/tests/browser/sidebar-styling-preview.mjs
node local/tests/browser/styling-feedback-preview.mjs
node local/tests/browser/available-tools-preview.mjs
```

These checks cover inline header search, dismissal and reopening, sidebar
controls, New Chat, responsive bottom-navigation fallbacks, reactive aggregate
unread state, and destructive menu colors and keyboard focus in both themes.
They also check the secondary-wrench Available Tools panel with synthetic tools,
expanded function details, bounded scrolling, and dismissal/reopening on desktop,
narrow phones, and a short landscape viewport.
They intercept all account writes and never click Delete. Results and PNGs are
saved under `local/tests/.qa/`. The feedback suite resolves actual store exports
from the matching production source maps; set `BUDDY_UI_TEST_BUILD` when that
build is outside the repository's `build/` directory.
On a styling-only build with the existing narrow composer/model-label overlap,
set `BUDDY_UI_TOOL_TRIGGER_KEYBOARD=1` to inspect the tools panel through keyboard
activation. Results record that mode; the normal tools suite taps the wrench.

## Production lazy features

After building the frontend and starting that matching production build, run:

```powershell
$env:BUDDY_LAZY_TEST_ORIGIN = 'http://127.0.0.1:8082'
node local/tests/browser/lazy-features-preview.mjs
```

The suite delays and rejects emitted JavaScript imports, exercises settings
requests and permission guards, closes pending optional panels, and edits code
and structured output. It also holds the full highlighting language bundle while
typing into pasted code, then verifies decorations, exact draft preservation,
and undo/redo history. Its explicit recovery check verifies that an unsent
draft survives clicking **Reload Buddy** after a cached import failure. It uses
synthetic API responses and intercepts all account writes, provider requests,
and media access. The local `build/` directory must match the served frontend
and contain source maps; those maps resolve actual production exports without
adding test hooks to the app. Results and failure screenshots are saved under
`local/tests/.qa/lazy-features/`.

Set `BUDDY_PLAYWRIGHT_MODULE` to a module path or file URL when Playwright is
provided by a bundled runtime rather than installed in this test directory.
Use `--only=case-name,another-case` to rerun selected cases.

## Real authenticated startup benchmark

`startup-benchmark.mjs` measures an authorized existing account against a real
frontend and backend. It does not route or mock browser requests. Supply a
private, short-lived JSON file containing `{ "token": "...", "expires_at": ... }`;
keep that file and the generated results in an ignored directory. The harness
reads it again for each new cold context, so an authorized token renewal can
continue a long run without discarding completed pairs. Never paste a token
into a command or attach the private auth file to a report.

The benchmark types one `b` into a fresh unsent draft and verifies the trusted
input event and editor content. It never presses Enter, submits a chat, starts
an OAuth flow, or opens a provider action. Normal app startup still runs its
own API calls, including the automatic timezone refresh POST. Run this
workflow only with authorization to access that account and normal startup
behavior. Synthetic regression results do not establish startup performance.

Use the same frozen source/build and delivery policy for repeat measurements.
To attribute improvements, compare development, frozen production with its
existing delivery, the exact same production build with optimized delivery,
and the optimized source with that same optimized delivery. Use the same
scheme and host for production stages: Chromium's compression negotiation can
differ between loopback and an HTTP tailnet address. Preserve the backend and
its caches, keep model/provider traffic idle, and stop unrelated builds and
CPU-heavy tests during primary sampling.

Run from the repository root with Node.js 22 and installed frontend dependencies.
Set `BUDDY_PLAYWRIGHT_MODULE` if Playwright comes from a bundled runtime; it may
be a module specifier or a file URL. An example five-pair capture is:

```powershell
node local/tests/browser/startup-benchmark.mjs `
  --url http://100.122.80.32:8082/ `
  --auth-file .cache/buddy-performance/benchmark-auth.json `
  --output .cache/buddy-performance/results `
  --label optimized-candidate `
  --revision source-revision `
  --runs 5
```

Add `--resume true` to keep passed pairs in the matching report and retry an
incomplete pair. Resume requires the same URL, revision, profiles, and
CPU-diagnostic mode. `--profiles desktop` or `--profiles mobile` limits a run;
by default both profiles run sequentially. A report with any failed navigation
exits nonzero. Output is checkpointed after every cold/warm pair.

The fixed desktop profile is 1440 × 1000 at DPR 1, CPU 1×, 20 Mbps down / 5 Mbps
up, and 40 ms latency. The phone profile is 428 × 926 at DPR 3 with touch, CPU
4×, 4 Mbps down / 1 Mbps up, and 150 ms latency. Both use Chromium, light mode,
en-US, and blocked service workers. The phone profile is an emulation rather
than a measurement from a physical iPhone.

Each cold run uses a fresh private browser context and clears its HTTP cache.
The warm run opens a new page in the same context and retains HTTP cache; it
is neither a reload nor a back/forward cache restoration. Backend caches are
retained. The four readiness milestones are navigation to splash removal,
visible editable composer, a mounted enabled native selected-model control,
and a first draft keystroke verified in the editor after two animation frames.
The selected-model control may be in the initially collapsed model row; it is
not required to be visible. A trusted input-event timestamp is also recorded.

Every group reports the median and nearest-rank p90. With five samples, p90 is
the observed maximum, not a stable estimate of a population tail. Critical
request totals include resources initiated by verified draft readiness;
completed transfer sizes are collected through the following 1500 ms tail.
Script counts are fetched script resources, not source modules inside a
bundle. The custom navigation-to-verified-draft blocking metric is
`sum(max(longTask.duration - 50 ms, 0))` for long tasks starting in that window.
It is not Lighthouse TBT or INP. CDP browser CPU totals include the capture tail
and are separate from the sampled critical-path diagnostic below.

The JSON schema is `startup-metrics.schema.json`. Sanitized output retains
path/origin, method, timing, byte counts, status and derived cache/compression
policy. It drops URL queries/fragments, redacts UUID path identifiers, and
never captures raw request headers, response bodies, storage snapshots, account
content, HAR files or browser traces. Inspect errors and external-request
counts before accepting a dataset.

## Separate CPU attribution and report

Run CPU sampling only after primary captures finish. Its overhead must not be
included in the timing comparison; use a distinct label and one phone pair:

```powershell
node local/tests/browser/startup-benchmark.mjs `
  --url http://100.122.80.32:8082/ `
  --auth-file .cache/buddy-performance/benchmark-auth.json `
  --output .cache/buddy-performance/results `
  --label candidate-cpu-diagnostic `
  --revision source-revision `
  --profiles mobile --runs 1 --cpu-profile true

node local/tests/browser/startup-profile.mjs `
  --input .cache/buddy-performance/results/candidate-cpu-diagnostic.json `
  --build build `
  --output .cache/buddy-performance/results/candidate-cpu-attribution.json
```

For a frozen baseline, point `--build` to that exact frozen build directory.
The profile helper reads matching source maps locally and emits top source and
function sample durations. It never emits source content. An optional
`--loader path/to/public-loader.js` snapshot maps loader frames to matching
`local/web/` script lines; verify its hash matches the loader served during
measurement. Script boundaries are accepted only when the complete current
local script text matches the snapshot. For an older loader snapshot after
source edits, add `--loader-sources path/to/frozen-web-scripts` and supply the
matching frozen script files. A shared backend loader can change independently
of a frozen frontend build, so preserve its bytes and hash with each stage.
Self time is additive; inclusive rows overlap. Idle/runtime samples and profiler overhead
remain in the diagnostic and should not be described as precise CPU usage.

Generate a self-contained interactive comparison from the sanitized datasets:

```powershell
node local/tests/browser/startup-report.mjs `
  .cache/buddy-performance/results/production-baseline.json `
  .cache/buddy-performance/results/delivery-baseline.json `
  .cache/buddy-performance/results/optimized-candidate.json `
  --output .cache/buddy-performance/results/startup-report.html
```

The report shows source provenance and origins, readiness milestones, transfer
sizes, long tasks, API durations, and a filterable waterfall for each captured
navigation. Diagnostic reports can also be supplied; their runs appear in the
waterfall selector but are excluded from the primary comparison table. Open
the local HTML file after measurement, so its rendering does not compete with
primary sampling.
