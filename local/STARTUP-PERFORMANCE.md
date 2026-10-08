# Buddy startup performance

Measured on 2026-10-08, starting from `d1b2b0d233573a64997ac6669a49593e99255eac` on `buddy-redesign`. The preceding Buddy branding, phone layout, PWA, OAuth-origin, and Exit changes are committed in that baseline.

## Run the optimized frontend

Keep the existing Buddy backend running on port 8081. Build this branch, then run the production frontend on 8082:

```powershell
$env:NODE_OPTIONS = '--max-old-space-size=8192'
npm run build
node local/serve-frontend.mjs --host 0.0.0.0 --port 8082 --backend http://127.0.0.1:8081 --allowed-host sd-anvelez-03.tail83dea0.ts.net
```

Open `http://100.122.80.32:8082/`. The frontend server serves `build/` and proxies the existing backend. It does not boot Python or create a new database. Use Vite dev for active editing; production serving is necessary for these startup gains.

Hashed app assets receive a one-year immutable cache policy. HTML, the version file, manifest, and local loader revalidate; account APIs and OAuth responses are never shared-cacheable. Text, fonts, and WebAssembly support negotiated compression. HTTP, streaming responses, WebSocket upgrades, cookies, authorization, and the browser host/port continue through the backend proxy.

## Measurement method

The authenticated homepage is the primary scenario. Each stage has five independent cold/warm pairs for each profile. A cold navigation starts a fresh browser context with an empty HTTP cache; the paired warm navigation uses a new page in that same context. The backend stays running. This measures browser startup rather than Python process boot or an external provider login.

- Desktop: 1440 × 1000, 20 Mbps download, 40 ms latency, native CPU.
- Phone: 428 × 926, DPR 3, touch/mobile Chromium, 4 Mbps download, 150 ms latency, 4× CPU slowdown. This uses iPhone 12 Pro Max viewport dimensions, but is an emulated stress profile, not a timing captured on a physical iPhone or Safari.
- Chromium 151.0.7922.34, Node 22.23.3, same machine/backend/account, English UI, light theme, and service workers blocked. No request interception or mocked APIs are used during primary sampling, preserving real HTTP cache behavior.
- The readiness milestones are splash removal, an editable composer, a selected model, and a trusted keyboard event whose unsent draft is verified after two animation frames. The last milestone is the reported time to first interaction. No model completion, message submission, or provider authorization is performed. Normal session/timezone refresh behavior is allowed.
- Primary medians and p90 use five samples per group. With the nearest-rank method, p90 is the slowest of those five observations. They are descriptive measurements rather than confidence intervals. Separate CPU-profiler diagnostics are excluded from the primary samples.

The original development-server measurement used localhost:8082. Production comparisons use the same Tailscale IP at ports 8092, 8093, and 8082, with explicit identical CDP network and CPU throttling. Dev-versus-final combines a serving-mode change and code changes. The stock-production control keeps the original code in production mode. The delivery-only stage holds the exact baseline build fixed while changing compression/cache headers; delivery-only-versus-final uses the same optimized production server and compares the runtime code changes more closely.

## Results

All **100 primary navigations passed**, with five cold/warm pairs per profile per stage. The eight separate CPU diagnostic navigations are excluded from the primary statistics. Time to first interaction means the verified unsent keystroke, after both an editable composer and a selected model are ready.

| Profile / browser cache | Original 8082 dev, median s | Unchanged production, median s | Final 8082, median / p90 s | Improvement vs unchanged production |
| ----------------------- | --------------------------: | -----------------------------: | -------------------------: | ----------------------------------: |
| Desktop / cold          |                       16.00 |                           3.05 |                1.50 / 1.53 |                               50.8% |
| Desktop / warm          |                        7.33 |                           2.84 |                0.94 / 0.98 |                               67.0% |
| Phone / cold            |                       69.83 |                          12.36 |                6.92 / 7.00 |                               44.0% |
| Phone / warm            |                       23.38 |                          10.10 |                4.89 / 5.05 |                               51.6% |

The production control uses the exact baseline source/build. Its delivery-only comparison uses that same frozen build with the new compression/cache server. That stage improved warm loads, but its cold phone median was **13.21 s versus 12.36 s** for stock production; it did not improve every case. Those observations are retained. The first code-deferred candidate reached 9.90 / 5.95 s cold/warm on the phone. Startup batching and the profiled mobile cleanup guards then reached 6.92 / 4.89 s.

The final splash disappears at **0.90 / 0.40 s** cold/warm on desktop and **4.11 / 1.89 s** on the phone profile. The composer and selected model become ready at **1.39 / 0.84 s** on desktop and **6.01 / 3.90 s** on the phone. Reported first-interaction values also include the trusted focus/keystroke and paint verification.

| Initial chat resource measure                               | Baseline production | First deferred candidate | Final batched candidate |
| ----------------------------------------------------------- | ------------------: | -----------------------: | ----------------------: |
| Static JavaScript dependency files                          |                 161 |                      147 |                       9 |
| JavaScript requests observed by the real startup harness    |                 163 |                      150 |                      12 |
| Static JavaScript bytes, raw                                |           6,179,917 |                2,772,883 |               2,724,663 |
| Sum of gzip bytes for static JavaScript                     |           1,847,530 |                  865,822 |                 802,486 |
| Cold phone transfer for requests started before interaction |           2,884,885 |                1,571,206 |               1,436,988 |

Static app JavaScript fell **56.6% in summed gzip size**; measured cold phone wire transfer fell **50.2%**. The static closure excludes workers and dynamic imports. Runtime script counts also include the loader, translation, and error-node dependencies. Transfer totals use final CDP encoded bytes for requests started before the verified draft and observed through the 1.5 s settle period; they include headers and may include trailing bytes from those requests. MB in the detailed table is decimal.

The custom blocking measure is the sum of `max(long-task duration - 50 ms, 0)` for tasks started between navigation and verified draft. It is not Lighthouse TBT or INP. Phone cold/warm blocking fell from **3,927 / 3,394 ms** in the production baseline to **2,440 / 2,573 ms** in the final version.

### All primary milestone measurements

The complete stage table below exposes every recorded group, including the intermediate candidate and the slower cold delivery-only comparison.

| Stage / profile / cache            |   N | Splash s med / p90 | Editable s med / p90 | Model s med / p90 | Verified draft s med / p90 | Wire MB median | Critical scripts median | TBT ms median |
| ---------------------------------- | --: | -----------------: | -------------------: | ----------------: | -------------------------: | -------------: | ----------------------: | ------------: |
| dev-baseline / desktop-cold        |   5 |      15.41 / 15.51 |        15.90 / 16.01 |     15.90 / 16.01 |              16.00 / 16.13 |         29.641 |                     819 |           193 |
| dev-baseline / desktop-warm        |   5 |        6.74 / 6.77 |          7.24 / 7.28 |       7.24 / 7.28 |                7.33 / 7.36 |          0.248 |                     819 |           160 |
| dev-baseline / mobile-cold         |   5 |      65.99 / 66.20 |        68.59 / 68.80 |     68.59 / 68.81 |              69.83 / 70.09 |         29.643 |                     819 |          4699 |
| dev-baseline / mobile-warm         |   5 |      20.07 / 20.32 |        22.25 / 22.66 |     22.25 / 22.67 |              23.38 / 23.93 |          0.250 |                     819 |          4015 |
| production-baseline / desktop-cold |   5 |        2.47 / 2.72 |          2.94 / 3.21 |       2.94 / 3.21 |                3.05 / 3.33 |          2.883 |                     163 |           111 |
| production-baseline / desktop-warm |   5 |        2.24 / 2.29 |          2.73 / 2.80 |       2.73 / 2.80 |                2.84 / 2.91 |          0.044 |                     163 |            97 |
| production-baseline / mobile-cold  |   5 |        8.95 / 8.99 |        11.24 / 11.40 |     11.24 / 11.40 |              12.36 / 12.54 |          2.885 |                     163 |          3927 |
| production-baseline / mobile-warm  |   5 |        7.08 / 7.17 |          9.01 / 9.14 |       9.01 / 9.14 |              10.10 / 10.29 |          0.046 |                     163 |          3394 |
| delivery-baseline / desktop-cold   |   5 |        2.44 / 2.46 |          2.90 / 2.95 |       2.90 / 2.95 |                3.00 / 3.06 |          2.516 |                     163 |            94 |
| delivery-baseline / desktop-warm   |   5 |        0.63 / 0.65 |          1.08 / 1.12 |       1.08 / 1.12 |                1.19 / 1.23 |          0.024 |                     163 |            72 |
| delivery-baseline / mobile-cold    |   5 |       9.48 / 11.84 |        11.74 / 14.16 |     11.74 / 14.16 |              13.21 / 15.69 |          2.519 |                     163 |          4789 |
| delivery-baseline / mobile-warm    |   5 |        3.71 / 4.13 |          5.90 / 6.78 |       5.90 / 6.78 |                7.20 / 7.94 |          0.026 |                     163 |          4282 |
| optimized-candidate / desktop-cold |   5 |        2.05 / 2.09 |          2.56 / 2.60 |       2.56 / 2.60 |                2.67 / 2.72 |          1.569 |                     150 |           100 |
| optimized-candidate / desktop-warm |   5 |        0.44 / 0.49 |          0.96 / 1.01 |       0.96 / 1.01 |                1.06 / 1.10 |          0.026 |                     150 |           103 |
| optimized-candidate / mobile-cold  |   5 |        6.76 / 7.05 |          8.62 / 9.61 |       8.62 / 9.61 |               9.90 / 10.86 |          1.571 |                     150 |          2743 |
| optimized-candidate / mobile-warm  |   5 |        2.43 / 2.66 |          4.79 / 5.17 |       4.79 / 5.17 |                5.95 / 6.35 |          0.028 |                     150 |          3303 |
| optimized-batched / desktop-cold   |   5 |        0.90 / 0.92 |          1.39 / 1.44 |       1.39 / 1.44 |                1.50 / 1.53 |          1.435 |                      12 |            57 |
| optimized-batched / desktop-warm   |   5 |        0.40 / 0.40 |          0.84 / 0.87 |       0.84 / 0.87 |                0.94 / 0.98 |          0.026 |                      12 |            43 |
| optimized-batched / mobile-cold    |   5 |        4.11 / 4.16 |          6.01 / 6.09 |       6.01 / 6.09 |                6.92 / 7.00 |          1.437 |                      12 |          2440 |
| optimized-batched / mobile-warm    |   5 |        1.89 / 1.96 |          3.90 / 4.02 |       3.90 / 4.02 |                4.89 / 5.05 |          0.028 |                      12 |          2573 |

### What caused the delay

The original dev server fetched **819 JavaScript resources and about 29.64 MB** during cold startup. In its desktop cold waterfall the first backend configuration request started around **15.2 s**, then completed in roughly **60 ms** with the configured network delay. Most of that logo wait happened before the backend was asked for configuration.

Unthrottled backend probes measured configuration at 7.45 ms median / 8.31 ms p90. Authenticated session/config reads were approximately 8-9 ms; model listing was about 27 ms cached and 302 ms on the first read. A concurrent batch of seven startup reads took about 426 ms, with model/provider listing taking most of that time. The running backend was not responsible for a repeated ten-second browser logo wait. Cold Python process boot is a separate event and is outside these navigation measurements.

The eager production import graph included Settings and its editors, all 194 Highlight.js languages, CodeMirror through code tools and terminal previews, xterm, and hidden optional chat controls. The Inter variable font was 804,612 raw bytes; negotiated gzip delivers 423,473 bytes without changing the font. PDFs, math rendering, HEIC conversion, Mermaid, Vega, and browser model/wasm features were already dynamically imported; this change does not claim new savings for those existing boundaries.

Separating shared keyboard names was not sufficient to remove CodeMirror: the graph exposed a real `StructuredOutputRenderer → TerminalOutputFile → FilePreview → code editor` path. The new terminal-output wrapper breaks that eager path while displaying the filename immediately.

### Separate CPU diagnostics

A diagnostic phone pair for each production stage identifies work; these are single cold/warm samples with profiler overhead, not the five-run primary medians. Source maps and the original loader texts are preserved for the earlier stages.

| Sampled JavaScript self time, cold / warm ms | First deferred candidate | Final candidate |
| -------------------------------------------- | -----------------------: | --------------: |
| Legacy mobile layout update                  |                197 / 154 |           2 / 3 |
| Closed Settings update                       |                  75 / 83 |           0 / 2 |
| All nine local loader scripts                |                344 / 293 |       189 / 183 |

The final phone diagnostic still spends about 1.9-2.1 s inclusively in Svelte effects/batching, about 0.55-0.58 s of additive self time across the editor family, and about 1.0-1.1 s in browser style calculation. Inclusive frames overlap and must not be added together. The readiness probe was small (roughly 12-22 ms inclusive in the earlier candidate); the verified-draft metric also includes Playwright native focus/actionability. Core chat/editor hydration and browser rendering remain the principal warm-start costs. The rich composer, model/permission gates, and normal editing behavior are preserved.

### Provenance and validation

- Baseline commit: `d1b2b0d233573a64997ac6669a49593e99255eac`.
- First deferred source manifest: `d474314111364ae200878b84a71c07b56afd72eb61f128736f4555de43af3c3c`.
- Final source manifest: `232ae186cab2e11180b39cb22cd087de9e9a199f91dca003a00f54488e561736`.
- Original public loader: `5e051fcf099d5f59a72de256fdc0a4e54ab8ea900fe0f875d5272430ead476b7`.
- Final public loader: `a7b848a3f09604565b720eea5345d4bb2a738717e554d4238e3336321e2351f5`. All nine scripts remain; only the two profiled cleanup regions changed, with the upstream prefix preserved. The loader was refreshed atomically without restarting Python or changing account data.
- Production build passed; 88 focused Node tests and 29 offline Authlib tests passed. The final 15-case production feature suite and existing light/dark OAuth Exit regressions passed. Primary navigations had zero page errors, external browser HTTP requests, or non-startup writes. Backend model listing may make provider metadata reads; no model generation or provider authorization was performed.

Private credentials stay out of these artifacts. Primary raw data, manifests, frozen builds, CPU attributions, screenshots, and the interactive report are local ignored artifacts under `.cache/buddy-performance/`. The main performance scope is the authenticated empty-chat homepage; large existing conversations, physical iPhone Safari, external OAuth-provider time, service-worker behavior, and Python cold boot were not part of the primary timing scenario.

## What changed

Settings and its editors, optional chat controls (calls, overview, artifacts, embeds, file browsers), response code tools, and the structured-output editor load on use. Each deferred feature has a visible loading state, close/cancel where applicable, and error recovery. Plain response code remains readable before its tools arrive. Settings tab requests and permission checks are preserved.

The rich composer keeps its normal editing capabilities. Full Highlight.js/Lowlight language grammars load only when code needs highlighting; plain text remains editable while that happens. A metadata-only ProseMirror transaction refreshes decorations without replacing editor views or changing the document, undo history, or collaboration state.

Guarded static-import grouping combines the core into boot, shell, and chat bundles. It excludes dynamic imports and rejects optional editors/grammars that would become eager through a shared barrel. Tiny facades and the shared keyboard helper stay separate. The two profiled mobile cleanup paths return early when Buddy already owns the layout or Settings is closed.

Only the active Day.js date locale loads. UI translation and English fallback loads overlap, as do session authentication and authenticated backend configuration. Startup shows phase text instead of an unexplained logo. Buddy stays pinned at the top while essential model/settings data is prepared. Essential model and permissions gates remain in place before chat is ready.

## Reproduce and inspect

The reusable harnesses and commands are documented in [the browser test README](tests/browser/README.md). The interactive local report includes individual sanitized resource waterfalls, all readiness milestones, long tasks, and cold/warm distributions. Private short-lived benchmark credentials stay in ignored files and are absent from reports and Git.

Production regressions exercise delayed and failed feature imports, settings permission/tab behavior, cancelled pending calls, file/terminal views, code editing, structured output, composer draft/undo preservation, and explicit reload recovery. Existing OAuth cancellation checks verify that Exit leaves the mounted chat, URL, scroll position, selected tools, and draft intact.
