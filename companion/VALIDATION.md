# Canonical Buddy Runner validation

The consolidation is based on Claude PR #2 at
`50a3d32fde17f08a34907f43f86761d2689710aa`, refreshed immediately before final
validation. Backend PR #3 preserves its original history by merging that base;
it changes no `src` files relative to the base. PR #4 owns the combined browser
UI and browser/mobile validation. Neither checkout writes Claude's shared ref.

## Current validation

The final Python **3.11.16** aggregate ran seven isolated suites against unchanged
production and test sources: **222 tests, 218 passed, 4 POSIX-only skips, zero
failures**, exit 0. Before/after SHA256 hashes matched for every production Python
module and fixture. No code changed after this run.

| Check | Result |
| --- | --- |
| Offline subscriptions and CLI discovery | 41 passed, 3.655s |
| Machine permissions and metadata | 20 passed, 1.354s |
| Provider host transitions, router guards and deleted-selection recovery | 60 passed, 4.555s |
| Native containment and temporary-file ownership | 29 run: 27 passed, 2 POSIX skips; 28.418s |
| Remote cleanup and tracked startup integration | 19 passed, 8.372s |
| Workspace pairing, grants, files and terminal races | 43 run: 41 passed, 2 POSIX skips; 25.473s |
| Runner API boundaries and shutdown lifecycle | 10 passed, 4.456s |
| Backend checkout production frontend build, Node 22.23.3 | Exit 0, 2m45s |
| CLI help | Exit 0; canonical configurable port 8765 and explicit roots/options |
| CLI `--host 0.0.0.0` | Expected exit 2 before state/key creation; no listener |
| Full frontend type check | Known failing 50a baseline: 6,906 errors / 193 warnings; no type-check pass claimed |
| Combined PR #4 frontend diagnostics | 6,901 errors / 193 warnings; zero new normalized diagnostics, five removed errors; still fails |
| Combined PR #4 styling fixtures | 17/17 passed on the final build |
| Combined PR #4 provider browser fixtures | 88/88 passed; synthetic provider responses |
| Combined PR #4 actual disposable Runner desktop/phone fixtures | 30/30 passed; seven runtime dependency hashes match final source |
| Diff and scope | `git diff --check` passes; zero `src` changes relative to 50a |

The build invokes Vite directly against the existing dependency installation;
the unrelated Pyodide download preparation is not run. Existing upstream build
warnings remain. Combined UI production build and diagnostics are checked
separately by PR #4.

Task-local evidence is `runner-consolidation-validation.json`, the seven
`runner-consolidation-test_*.log` files and
`runner-consolidation-node22-build.log`. The first aggregate caught a real race
between monitor-owned temporary-file deletion and synchronous kill. The fix
holds deletion ownership until unlink completes, retains failed paths for retry
and is covered by deterministic parallel-cleanup fixtures. The failing run is
retained separately; the results above are from the subsequent unchanged-source
run. A final source review then caught a deleted-machine reselection failure;
the config-only recovery fix and five regressions are included in the final
222-test run above. A sandbox-limited attempt failed on test-owned temporary
directory permissions; the subsequent disposable/offline aggregate ran outside
that filesystem sandbox and passed against unchanged sources.

Combined browser/mobile validation remains owned by PR #4. Its final build
index SHA256 is
`826F029B6AABB75EAD7DB081919E4A591471569960765A537669576E53D80CBE`.
The owner confirmed all 17 styling, 88 provider and 30 actual disposable Runner
cases passed, reviewed 26 screenshots, and verified zero remaining Runner
fixture directories/processes. The service-only recovery correction leaves all
seven Runner runtime dependency hashes and frontend source hashes unchanged.
These browser checks do not authenticate to real providers or validate a live
deployment. The owner's temporary preview has been stopped.

## Tested boundaries

Fixtures use in-memory configuration, disposable project/state directories,
ephemeral loopback servers and Python child processes. They do not authenticate
to providers, inspect real account credentials, mutate the live database, pair
with the live Runner or grant access to real projects.

Coverage includes exact Origin and Host validation, independent provider and
browser credentials, one-use pairing, expiry and revocation, host instance
identity, relative traversal and device paths, real Windows junction/hardlink
escapes, pinned ancestor handles, root replacement and filesystem races. Files
remain existing UTF-8 text up to 1 MiB; commands remain explicit argv execution.

Lifecycle fixtures verify native Windows Job Object descendants on natural
leader exit, cancellation, timeout, disconnect, stop and shutdown. Remote tests
require an explicit native-cleanup acknowledgement, reject transport loss as
proof of cleanup, bound the receiver's second stdout buffer and cover failed or
cancelled startup. Concurrent terminal stop closes command admission before
awaiting cleanup. Provider fixtures exercise stale IDs/revisions, same-ID target
changes, serialized actions, old cache generations, cleanup failure, missing
legacy registration verification and the single-worker execution requirement.

## Limits and historical evidence

Native Windows validation does not establish POSIX runtime coverage. POSIX code
uses pinned directory descriptors and process groups; deliberately detached
processes require stronger OS isolation. A command's selected working directory
is not an OS sandbox. Provider commands and opted-in workspace commands execute
as the host account and may access beyond file API grants.

The host stays loopback-only. Remote phone/PC/cloud use needs a separately
approved private HTTPS transport; no transport, firewall, persistent service,
live credential, deployment or Docker/volume change is performed here. Provider
execution and host changes require one Buddy backend worker.

The earlier Node Companion commit `287162ae665156174778c349bc83c4d8871234c1`
and its bbd/8083 test logs are historical evidence only. Its duplicate host and
Node test/launch scripts are retired by this consolidation. Those old results
do not validate the current Runner implementation or a later combined build.

See [the guide](README.md) for reproducible isolated test commands and the
operator-only pairing refresh that preserves running provider processes.
