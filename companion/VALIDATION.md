# Companion implementation validation

## Checkout and coordination

### Stacked PR integration

Andrew subsequently requested comparison against Claude's existing PR #2,
`claude/multi-model-login-subscription-964962`, verified at
`f49190df94df336559a9dbc707eda4a4660ce451`, instead of the removed remote
`buddy-redesign` branch. The isolated companion branch was rebased onto that
exact commit, then refreshed again onto Claude's review-fix commit
`bbd02e04c2e13a02ac8d791c0269279070334a1b` when it advanced during validation,
without writing Claude's branch or checkout. Both guide sections
are retained; the UI and subscription guide explicitly distinguish provider
commands on Buddy's backend from the paired companion host. Provider workspace
strings do not become grants, and credentials remain separate. Styling owns
the combined local runtime and any 8081/8082 replacement; this task owns only
the grant-free 8083 host. The original validation below remains historical
evidence; final new-base validation follows here.

The current Claude checkout and PR head both match `bbd02e0` and are clean.
The earlier in-progress deletion snapshot below predates that committed PR and
does not describe its final state. Semantic review found no duplicated provider
adapter or authentication implementation: subscription login/streaming stays
in Claude's modules; companion pairing/grants remain the dedicated host API.

### Final validation on Claude's committed PR head

All checks below ran against the final companion code stacked directly on
`bbd02e04c2e13a02ac8d791c0269279070334a1b`, using Node **22.23.3** and
Python **3.11.16**. The only later changes are this validation record.

| Check | Final result |
| --- | --- |
| Companion security/client/lifecycle suite, after the final CLI origin change | **38 passed, 0 failed, 0 skipped**, 15.858 seconds |
| Existing local Node tests | **145 passed, 0 failed, 0 skipped**, 3.174 seconds |
| Claude offline subscription tests | **30 passed, 0 failed, 0 skipped**, 0.265 seconds |
| Production frontend build | **Exit 0**, 2m 38s |
| Rebuilt frontend desktop/phone smoke on temporary loopback 3313 | **2/2 passed, exit 0**; zero page errors or horizontal overflow |
| Default CLI exact-origin probe on temporary loopback 3314 | Four loopback origins on 8081/8082 return **200**; old 8080, lookalike and foreign origins return **403**; unauthenticated grants return **401** |
| Full Svelte/type check, companion stack | **Exit 1**, 6,906 errors/193 warnings in 346 files |
| Full Svelte/type check, clean Claude `bbd02e0` baseline | **Exit 1**, the same 6,906 errors/193 warnings in 346 files |
| Normalized diagnostic comparison against that clean baseline | **Zero new diagnostics**; companion API/page have zero, Sidebar retains its existing 82 |

The type check does not pass. The comparison treats equivalent ordering of
existing `Response`/`AbortController` union members as equal; no diagnostics
were suppressed. Subscription tests use fake providers, temporary directories
and a Python stdin/stdout fixture, without live accounts, provider requests or
the live Buddy database. Browser tests use synthetic Buddy responses and
disposable companion hosts. They verify file edits, selected-directory argv
commands, stop/revocation, host identity and credential separation. Neither
the real 8083 host nor real project grants are used.

Task-local evidence: `build-claude-bbd-stack.log`,
`stack-bbd-companion-tests.log`, `stack-bbd-local-node-tests.log`,
`stack-bbd-subscription-tests.log`, `stack-bbd-browser-smoke.log`,
`check-claude-bbd-stack.log`, `check-claude-bbd-baseline.log`,
`check-claude-bbd-stack-comparison.json` and
`stack-cli-origins.json`. Browser evidence is in ignored
`local/tests/.qa/companion/`; its final result records origin 3313. Prior
8082 evidence is archived in `companion-prior-live-8083/`; the earlier f491
stack smoke is archived in `companion-f491-stack-preview-8083/`.
Temporary 3313/3314
listeners were stopped after validation. Styling owns final combined source,
build and 8081/8082 runtime validation; this task does not restart them.

The default host now allows exactly `http://localhost:8081`,
`http://127.0.0.1:8081`, `http://localhost:8082` and
`http://127.0.0.1:8082`. This covers direct Buddy backend hosting and its
separate frontend while retaining exact-origin checks and mandatory pairing.
Port 8083 remains configurable and loopback-only, with no grants or enabled
write/execution capabilities unless explicitly supplied at startup.

### Original foundation validation (historical)

- Base: `buddy-redesign`, refreshed GitHub head
  `68bf5aeba5bbde9a635d9bb93a062e0829c8d2bf`.
- Isolated task checkout: `Buddy-work`; branch `codex/companion-foundation`.
- The existing main checkout's changed `AGENTS.md` was preserved. Companion
  implementation and tests were isolated in this task checkout. The styling
  task owns the coordinated, validated frontend application; existing data,
  dependencies and branches are preserved. Claude's checkout was read-only.
- Claude's exact local branch is
  `claude/multi-model-login-subscription-964962`, at the same base HEAD with
  zero commits ahead/behind. Earlier read-only snapshots were clean. It changed
  during validation: the final snapshot contained five modified tracked source
  files, 21 tracked backend/static deletions and seven untracked entries.
  Subscription adapters, admin endpoints/UI and tests are now visible, but were
  not executed or validated by this task. No current file overlap with the
  companion implementation was found.

Claude's new code adds admin `/api/v1/subscriptions` login/config/status/code/
cancel/logout endpoints; Claude Code and Codex CLI adapters; model discovery,
usage reporting, multimodal streaming and conversation reuse; three subscription
UI components; and newly added `local/tests/test_subscriptions.py`. The result
of that test is unverified. The 21 static deletions need review when its work
finishes; this task did not restore or modify them.

Those adapters currently execute on **Buddy's backend computer**, with a
separately configured workspace string. Selecting a remote companion project
does not route subscription CLIs to that host. Future integration must map an
identified execution host to approved workspaces and capabilities explicitly.
Model login remains independent of companion pairing; a configured path grants
nothing through this companion. Claude's separate full-access modes bypass CLI
approval/sandbox restrictions and are not activated or wired to the companion
by this task.

## Final code checks

Windows validation uses a checksum-verified portable **Node.js v22.23.3** in the
task workspace, matching Buddy's supported Node 22 major version. It does not
replace the computer's installed Node. Existing frontend dependencies were
copied into the isolated checkout; the companion itself has no dependencies.

| Check | Result |
| --- | --- |
| `node --test companion/tests/*.test.mjs` | **38 passed, 0 failed, 0 skipped**, 16.900 seconds after the 8083 update |
| `node --test local/tests/*.test.mjs` | **145 passed, 0 failed, 0 skipped**, 3.574 seconds after the 8083 update |
| `node --check companion/server.mjs` and `node --check companion/cli.mjs` | Exit 0 |
| `node companion/cli.mjs --help` | Exit 0; default port 8083 and loopback/capability controls displayed |
| `node companion/cli.mjs --host 0.0.0.0` | Expected exit 1; public binding rejected |
| Earlier `node companion/cli.mjs --port 3301` with no roots | Expected exit 1; existing Plane listener reported as `EADDRINUSE` and left running |
| Second final `node companion/cli.mjs` while 8083 is occupied | Expected exit 1/`EADDRINUSE`; the first host and its identity remained intact |
| Final no-root startup on `127.0.0.1:8083` | Running with zero grants, writes disabled and execution disabled; exact Buddy origins on 8082 |
| Read-only running-host probe | Host metadata 200 for both configured origins; unauthenticated grants/terminal 401; foreign origin 403 |
| Companion Svelte compiler | Zero warnings |
| `git diff --check` | Exit 0 |

Host integration tests use disposable project roots, loopback ephemeral ports,
memory-only pairing and temporary command descendants. Coverage includes
unauthenticated/invalid/revoked/expired sessions, single-use pairing and bounded
attempts, exact and missing/null/foreign/lookalike Origins, raw Host spoofing,
preflight checks, relative traversal and Windows drive/UNC syntax, actual
junction and hardlink escapes, changed root/workspace identity, no grants,
read/write/execute capabilities, file bounds, session ownership, argv execution
without an implicit shell, bounded output and session counts, idle expiry, and
complete descendant cleanup after stop, timeout, revoke, expiry, shutdown and
natural command-parent exit.

Client tests exercise endpoint restrictions, spoofed loopback domains,
credential isolation, errors and malformed responses, timeout/cancellation,
disconnect and late pairing cleanup. No live provider, account or persistent
credentials are used.

## Frontend checks

The final production build, `node node_modules/vite/bin/vite.js build` with an
8 GiB heap limit, completed with **exit 0** and the static adapter wrote `build/`.
Vite reported 2m 44s for the final 8083 build. Existing upstream accessibility, unused
exports and large-chunk warnings remain. The unrelated Pyodide download
preparation step was not run.

The final browser smoke, `node companion/browser-smoke.mjs`, completed with
**2/2 scenarios passed, exit 0**, at desktop 1440×1000 and phone 390×844. It used
the final built UI, synthetic Buddy account/API responses, actual ephemeral
companion hosts and disposable project files. It verified default port 8083,
unpaired access gating, host identity and pairing, memory-only tokens and no
Buddy-token forwarding, nested project selection, read-only and execution
denials, actual file edits, execution acknowledgement, a real Node command with
exit 0 and the selected working directory, stop and token revocation. Both
viewports had zero uncaught page errors, document/panel horizontal overflow or
controls outside the panel. Final screenshots were visually inspected; the
Windows supervisor output is plain text, with an explicit regression assertion
against CLIXML chatter.

Browser evidence is saved in the ignored `local/tests/.qa/companion/` folder:
`results.json` and desktop/phone connected, project, file and command PNGs.
Set `BUDDY_PLAYWRIGHT_MODULE` when using an existing external Playwright
installation. The temporary frontend on loopback 3313 was stopped afterward.

The styling task owns the coordinated local frontend integration. It copied
the companion API/page into its combined snapshot of the current dirty Buddy
source and final styling changes, and merged exactly one Companion link into
the shared sidebar. Read-only SHA-256 checks confirmed that the copied API/page
match this task's final 8083 source. Its combined production build completed
with exit 0 on Node 22. The full companion desktop/phone smoke was repeated
against that exact combined build on temporary loopback 3313: **2/2 passed,
exit 0**, with the same security, editing, command and layout assertions.
`local/tests/.qa/companion/` now contains the combined-build evidence; the
standalone evidence is preserved in `companion-standalone-8083/` beside it.
The companion task stopped that temporary frontend after validation.

The styling task then applied the reviewed combined source and build to the
existing Buddy frontend on 8082. Its application record confirms the unchanged
`buddy-redesign` base commit, preserves unrelated existing changes and retains
the previous build in `.cache/buddy-styling-previous-build-20261009-160720`.
The new `build/index.html` SHA-256 is
`CE9EF4752893F79BF1B02CE5EDEFE8C338DAFE2F4CC26402F72D2C9D7DE7F22F`.
The full companion smoke was run once more against the actual served frontend
at `http://127.0.0.1:8082`: **2/2 passed, exit 0**, on supported Node 22 with
desktop 1440x1000 and phone 390x844. All Buddy account/provider API requests
were synthetic; the companion tests used ephemeral hosts and disposable roots,
never the live grant-free host. The final screenshots were visually inspected.
The current `local/tests/.qa/companion/results.json` records origin 8082;
earlier combined-preview evidence is in `companion-combined-preview-8083/`.
Independent read-only post-application checks confirmed that the served index,
live build and combined build share that SHA-256; the served route manifest
includes `/companion`; the live API/page match this task's source exactly; and
the live sidebar contains exactly one Companion link. Of the 37 original
files in the preservation manifest, 36 remain byte-identical and only the
intentionally updated `DropdownMenu.svelte` differs. The previous build backup
exists. Frontend PID 35004, backend 8081 and the existing frontend binding were
unchanged; the companion adds only its loopback 8083 listener.

Repository-wide Svelte/type checking has thousands of existing failures. The
final comparison checks whether the new companion files add diagnostics rather
than presenting that repository-wide check as passing. An initial check had
6,907 errors/193 warnings, including a temporary navigation diagnostic before
the new route types were generated. After route synchronization, the check had
6,906 errors/193 warnings with zero diagnostics in the two new companion files;
the sidebar retained its 82 existing diagnostics. The final post-fix type check
returned **exit 1**, 6,906 errors/193 warnings in 346 files; comparison found no
new diagnostics in the companion files or sidebar. The check was repeated after
the 8083 update with the same 6,906 errors/193 warnings in 346 files and zero
diagnostics in the new companion API/page. This check did not pass.

## Changed files

- `companion/server.mjs`, `companion/cli.mjs`: independent host and CLI.
- `src/lib/apis/companion.ts`, `src/routes/(app)/companion/+page.svelte`: typed
  client and responsive browser/mobile interface.
- `src/lib/components/layout/Sidebar.svelte`: Companion navigation link.
- `companion/tests/server.test.mjs`, `companion/tests/client.test.mjs`,
  `companion/browser-smoke.mjs`: security, lifecycle, client and browser tests.
- `package.json`: companion startup/test scripts.
- `BUDDY.md`, `companion/README.md`, `companion/VALIDATION.md`: setup,
  architecture, limitations and validation evidence.

## Limits and access state

This is a reviewable foundation, not a configured remote deployment. The host
is loopback-only; remote/mobile access requires a separately approved private
HTTPS transport. One client can pair per host start. Terminal sessions use
pipes without PTY or persistent shell state. Commands are separately enabled
whole-host execution under the companion process account, not an OS filesystem
sandbox. API checks do not isolate the service from a malicious local process
running under that same OS account. POSIX process-group handling is implemented
but this validation host is Windows; POSIX lifecycle behavior remains untested.

Initial implementation and local validation created no real project grant,
persistent credential, persistent service, firewall change, new public listener,
push, PR or merge. Andrew later explicitly authorized publishing this tested
companion scope as a separate stacked draft PR targeting Claude PR #2's head
branch, `claude/multi-model-login-subscription-964962`; that
publication does not change host grants, capabilities or deployment. The local
companion startup defaults to loopback 8083 with zero grants and execution
disabled. Runtime availability is verified separately after combined
integration; earlier foreground host lifetime is not assumed. Andrew
explicitly selected configurable port 8083 after verification
that Buddy uses frontend 8082/backend 8081 and Plane owns 3300/3301. Existing
Plane listeners were left intact. Temporary test listeners are stopped after
validation. Remote transport and real host access require a separate approved
setup.
