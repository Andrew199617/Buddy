# Buddy local customizations

Buddy's runtime customizations live in this tracked folder. User settings and
installed Functions live in Buddy's private database. The launcher applies
the patches and rewrites the bundled `loader.js` hook on each start; no manual
edits to `.venv` are needed. See [Buddy setup](../BUDDY.md) for installation.

The pinned runtime dependencies are Open WebUI **0.11.4**; the launcher runs
Buddy's tracked backend and built frontend. Response usage tracking checks that
version and the upstream function contracts before installing its hooks.

| File                           | What it does                                                                                               |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `web/reasoning-chip.js`        | Thinking-level chip next to the model picker in the chat input                                             |
| `web/mobile-chat-layout.js`    | Keeps mobile chat and long drafts visible above the keyboard                                               |
| `web/mobile-ui-polish.js`      | Mobile text/icon proportions and adaptive full-screen model picker                                         |
| `web/model-edit-shortcut.js`   | Direct native model editing from a pen button beside each model menu                                       |
| `web/mobile-settings.js`       | Fullscreen phone Settings with grouped category navigation and native forms                                |
| `web/chat-header.js`           | Theme-aware translucent header, larger controls, and direct native Settings access                         |
| `web/chat-actions-menu.js`     | Modern native chat menu styling, touch targets, and keyboard-aware placement                               |
| `web/model-activity.js`        | Visible waiting/working status and elapsed time while the native response is pending                       |
| `web/chat-usage-info.js`       | Context usage and response/chat details, with tap access on mobile                                         |
| `usage_tracking.py`            | Persists server timing and provider token usage for each new response run                                  |
| `functions/reasoning_level.py` | Always-on filter (in the DB) that sends the level in each connection's format                              |
| `install_functions.py`         | Installs or updates everything in `functions/` into the DB                                                 |
| `owui_local_patches.py`        | Startup patches: Slack PSS keep-alive, response run tracking, and `web/` scripts in `/static/loader.js`    |
| `serve.py`                     | Buddy's source backend plus the patches; `start-buddy.ps1` uses it                                         |
| `tests/`                       | `.venv\Scripts\python.exe -m unittest discover -s local\tests -v` and `node --test local\tests\*.test.mjs` |

## Mobile chat layout

The mobile chat follows the browser's visible viewport, including iOS keyboard
height and viewport panning. Long drafts scroll inside the composer, and the
latest message stays visible if the conversation was already scrolled to the
bottom. Reading older messages preserves the scroll position. Conversation
wrappers use their content's actual height and leave scrolling to the messages
pane, avoiding offscreen wrapper bounds around long replies. Content growth
and late usage footers keep following only while the user is already at the end.

The full phone layout adds modest clearance below the reply. Clearance
shrinks with the available conversation height when the keyboard or a draft
reduces reading space. In a compact viewport, long drafts scroll inside a
smaller editor and response summaries stay on one line; full details remain
tappable. The compact new-chat layout removes excess vertical padding.

The script is loaded through the same `loader.js` hook as the thinking-level
chip. It applies to the main mobile chat and resets when navigating elsewhere.

## Mobile proportions and model picker

Phone controls use a readable 16px minimum root scale, 15px sidebar labels,
20px navigation glyphs, and 40–44px touch targets. Larger UI Scale settings
still apply. Sidebar width and swipe geometry stay synchronized.

The native model picker becomes a full-screen view below 640px, with a Models
header, Back and Done controls, search, and the original selection/Compare
behavior. Its virtual list retains 32px local rows and uses CSS zoom to render
44px touch rows without breaking scroll calculations. The view follows the
visible viewport when the search keyboard opens and keeps background controls
inert until it closes.

Wider screens keep the compact picker. When the browser exposes multiple
viewport segments (such as an open Duo), the picker stays within the display
containing its trigger, avoiding the hinge. Without that API, normal width-based
adaptation and the native floating position remain in use.

## Chat header

The main chat header uses a translucent surface matched to light/dark themes,
with a solid fallback when backdrop filtering is unavailable or reduced
transparency is requested. The sidebar and chat controls share 44px phone
buttons and 24px icons; desktop controls remain compact. Long titles truncate
within a single row. Settings sits to the right of the advanced controls and
opens the existing app Settings screen through its native route handler.

The header occupies its own layout space. Messages use 16px top padding rather
than another header-sized spacer, keeping scrolled response details below it.
The actual header height is tracked as the viewport and text scale change.

## Mobile Settings

Below 768px, Settings fills the visible viewport with a searchable vertical
category list. Native category buttons become grouped rows with icons and
chevrons; selecting one opens its existing settings panel. Back returns to
the list, and Close uses the original modal callback. Escape dismisses an
open submenu first, then returns to the category list. The native search,
permissions, forms, controls, and save handlers remain in use. Returning to
the list preserves the currently mounted panel and its unsaved fields.

The adaptation lives entirely in `web/mobile-settings.js`. Its CSS applies
only to the owned mobile Settings dialog, and the module removes its classes,
header, icons, and viewport styles when the window reaches 768px or Settings
closes. Desktop and wider tablet/Duo Settings retain their original layout.
Phone headers and save controls stay visible while the category content
scrolls, including when the keyboard changes the visible viewport height.
Below 360px, native select rows put the label above a full-width control,
avoiding split words and truncated choices with larger text.

## Model edit shortcut

Workspace model rows show a pen button immediately before the native More
menu when the row has write access. It uses the app's existing Pencil icon
shape and calls the native row Edit action, preserving the permission check
and editor navigation. Read-only models retain their native menu without an
Edit shortcut. Shift shortcuts and the enable/disable switch stay native.

The pen and More controls use 32px desktop buttons and 44px touch targets.
Phone row metadata wraps below the model name to make room for the controls.
Below 360px, writable rows place actions on a second line, retaining readable
model names with enlarged text and full-size touch targets.
The styling is scoped to the Workspace model list and supports light/dark
surfaces. No model requests, contents, or save handlers are replaced.

## Chat action menus

The chat header's three-dot control opens a rounded panel with a conversation
heading, larger icons and action rows, and a distinct Delete action. The same
presentation applies to sidebar chat actions. Light and dark themes use solid
surfaces for readable contrast.

The panel and Download/Move submenus retain their native callbacks. Menus fit
inside the visible viewport when a phone keyboard opens; long lists scroll.
On devices that expose multiple viewport segments, they stay on the display
containing the trigger. Keyboard navigation follows visible actions, and the
native Escape/outside-click behavior remains in control.

## Model activity

An empty pending reply displays a spinner and “Waiting for response” as soon
as the native reply placeholder appears. Elapsed time starts with that visible
attempt. New response content changes the label to “Working”; an active native
thinking signal takes precedence. The indicator follows the original pending
cursor, so a task acknowledgement does not clear it. Completion, stop, errors,
and navigation remove it. Continuing an existing answer starts a fresh timer.

The activity label is a polite screen-reader status; timer digits are excluded
from live announcements. Reduced-motion preferences disable the rotation. The
script does not change requests, response content, or cancellation behavior.

## Context and response usage

A compact context control above the composer opens details by clicking or
tapping. Response details show the model, timestamp, elapsed time when recorded,
and token usage with separate input/output counts. Chat details are also
available from the sidebar. Phone details use a sheet that follows the visible
viewport; larger screens use a compact panel.

Tokens consumed by a response run include its model/tool iterations. They are
separate from the latest request's context usage. Capacity is shown only when
explicitly configured or advertised by the model. A compaction threshold is
labelled separately and is never treated as model capacity. Missing provider
counts and historical timing remain unavailable; estimates are labelled.

The backend keeps timing and run usage under `message.meta.local_run` in the
existing chat/message JSON. No database schema changes are required. Existing
provider usage remains intact. Future runs get the additional metadata after
starting through the local launcher. The wrapper checks the installed version
and upstream function shapes, and skips itself safely if an upgrade changes
those contracts. UI reads stay authenticated and do not send prompts or alter
chat contents.

## Thinking level

The chat input shows a gauge chip next to the model name. Click it to choose
**Default**, **Off**, **Low**, **Medium**, **High**, **Extra high** or **Max**.
The choice applies to every chat in that browser until you change it.

- It sets the same `reasoning_effort` param as Chat Controls > Advanced Params,
  just one click away. **Default** sends nothing extra, so the chat's Chat
  Controls value, your Settings default, or the model's own default applies.
- QwenMOE (llama.cpp) turns the level into a thinking budget: low 1024, medium
  2048, high 4096, xhigh 8192, max 32768 tokens. Off disables thinking. Default
  uses the server's `--reasoning-budget 32768`.
- Gateway models (gpt-6-\*, claude-opus-5-5, luna) use the Responses API, which
  expects `reasoning: {effort}`. `functions/reasoning_level.py` converts the
  value for those connections, and a chosen level overrides luna's built-in
  `max`. Chat Controls' Reasoning Effort goes through the same conversion.
- If a model's API doesn't support a level, the API returns an error. Pick another.
- How it's wired: Open WebUI loads `/static/loader.js` on every page as a hook
  for custom scripts, and recopies it from its bundled frontend on each start.
  The launcher then appends `web/reasoning-chip.js`. If an Open WebUI update
  changes the input bar so the model picker can't be found, the chip simply
  doesn't appear; chats are unaffected.

## Slack PSS sign-in

Some Slack MCP gateways issue access tokens that expire after several hours
with **no refresh token** and advertise only `authorization_code`. Without a
refresh token, Open WebUI deletes the session at expiry, requiring another
sign-in.

`owui_local_patches.py` asks the gateway whether the token still works before
dropping it. If the gateway accepts it, the session is kept and checked again
every 30 minutes. If the gateway rejects it, you're asked to sign in as before.
Results are logged to `open-webui-data\local-patches.log`. After a day, that log
shows whether the gateway honors tokens past their advertised expiry.

If the log shows `token rejected (HTTP 401)` a few hours after sign-in, the
gateway enforces its expiry, and only the gateway team can fix it. Ask them to
enable the `refresh_token` grant for the DCR endpoint. Open WebUI already
refreshes tokens automatically when it gets one.

## Updating Open WebUI

Stop Open WebUI, then run this from the `open-webui` folder:

```powershell
git pull --ff-only
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.python"; $env:UV_CACHE_DIR = "$PWD\.uv-cache"
.uv-tools\Scripts\uv.exe pip install --python .venv\Scripts\python.exe open-webui==0.11.4
```

Start it again with **Start Open WebUI.bat**. The launcher reapplies the patches.
Keep the runtime at 0.11.4 until the customizations are validated against a
newer release. If an upgrade changes the code a patch wraps, the patch skips
itself and logs a `Skipping ...` line instead of breaking startup.

After editing a file in `functions/`, run
`.venv\Scripts\python.exe local\install_functions.py` (the server doesn't need a restart).
