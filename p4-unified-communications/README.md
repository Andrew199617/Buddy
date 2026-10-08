# P4 Unified Communications Bridge

This is a standalone, provider-neutral bridge for Open WebUI and other
scripts. It is deliberately separate from the Open WebUI source tree: the
bridge owns provider orchestration, while `open_webui_tool.py` is only a thin
subprocess adapter that can be copied into any Open WebUI instance.

It provides a single read-only interface for:

- Email from Microsoft/Outlook, Gmail, and Yahoo through [Himalaya](https://github.com/pimalaya/himalaya).
- Microsoft/Outlook calendars through [PnP CLI for Microsoft 365](https://pnp.github.io/cli-microsoft365/).
- Google and Yahoo calendars through [Calendula](https://github.com/pimalaya/calendula) (Google Calendar API or CalDAV).
- Microsoft Teams teams, channels, channel messages, chats, and chat messages through PnP CLI.

The adapter targets the current Pimalaya CLI generation (Himalaya 2.x and
Calendula 0.2.x), whose machine-readable switch is `--json`. Older Himalaya
1.x binaries use a different output flag and should not be mixed with this
bridge without adjusting the adapter.

The bridge never accepts an arbitrary command string. It invokes only fixed
read/list/check commands, uses `shell=False`, and returns JSON with provider
and account labels on every unified result. Sending, deleting, moving, and
editing email or calendar items are intentionally not exposed.

## Directory layout

```text
p4-unified-communications/
  p4_comms.py          # standalone CLI and provider adapters
  open_webui_tool.py   # optional thin Open WebUI adapter
  config.example.json  # copy to config.json; contains no secrets
  tests/                # offline unit tests
```

No provider credentials belong in this directory. Himalaya, Calendula, Ortie,
and PnP CLI keep their own authentication/configuration stores.

## Install the upstream CLIs

Install the current upstream tools using their documented method for your
platform. On Windows, pre-built binaries or Scoop are usually easiest for
Himalaya and Calendula. PnP CLI is already installable with npm:

```powershell
npm install --global @pnp/cli-microsoft365
```

The included `install.ps1` can perform the checks and install PnP CLI (and
Himalaya/Calendula when Rust/cargo is available), while preserving an existing
`config.json`:

```powershell
.\install.ps1
```

It does not create provider credentials or run an interactive login for you.

Then authenticate Microsoft 365 once in the same Windows account that runs
the bridge:

```powershell
m365 login
m365 status --output json
```

Himalaya and Calendula each have their own account wizard/config file. The
bridge does not duplicate those credential flows. Configure one account in
each upstream CLI and use the same account names in `config.json`:

```powershell
himalaya account list
calendula calendar list
```

For Gmail, Himalaya can use Gmail OAuth through Ortie or an app password. For
Yahoo mail, use Yahoo IMAP with a Yahoo app password. Yahoo Calendar is a
CalDAV account at `https://caldav.calendar.yahoo.com`; Calendula's CalDAV
backend can use that account. Do not put an app password in `config.json`.

## Configure the bridge

Copy the example configuration:

```powershell
Copy-Item .\config.example.json .\config.json
```

Edit only the account mapping and paths. The names in `email_account` and
`calendar_account` must match the account names in Himalaya and Calendula.
For the Outlook calendar entry, replace the example `username` with the
Microsoft 365 user principal name (normally the Outlook sign-in address).
PnP CLI's Outlook calendar command requires that user selector even when the
account is the one you logged in as.
If all three executables are on `PATH`, leave `executables` blank. Otherwise,
set an absolute path, for example:

```json
"executables": {
  "himalaya": "C:\\Tools\\himalaya.exe",
  "calendula": "C:\\Tools\\calendula.exe",
  "m365": "C:\\Users\\me\\AppData\\Roaming\\npm\\m365.cmd"
}
```

The bridge can also point to separate upstream configuration files:

```json
"himalaya_config": "C:\\Users\\me\\AppData\\Roaming\\himalaya\\config.toml",
"calendula_config": "C:\\Users\\me\\AppData\\Roaming\\calendula\\config.toml"
```

## CLI examples

Run these from this directory. Every command prints JSON; add `--pretty` for
human-readable indentation.

```powershell
python .\p4_comms.py --config .\config.json accounts
python .\p4_comms.py --config .\config.json health
python .\p4_comms.py --config .\config.json email recent --provider all --limit 20
python .\p4_comms.py --config .\config.json email search --provider all "from:alice and after 2026-09-01"
python .\p4_comms.py --config .\config.json email read --provider yahoo --account yahoo 123
python .\p4_comms.py --config .\config.json calendar upcoming --provider all --start 2026-09-11 --end 2026-09-18
python .\p4_comms.py --config .\config.json teams list
python .\p4_comms.py --config .\config.json teams channels --team-name "Project Team"
python .\p4_comms.py --config .\config.json teams messages --team-id TEAM_ID --channel-id CHANNEL_ID --since 2026-09-01
python .\p4_comms.py --config .\config.json teams chats --type group
python .\p4_comms.py --config .\config.json teams chat-messages --chat-id CHAT_ID
```

Message IDs are account-local, so reading a message requires both the
provider and bridge account name. A combined email/calendar operation returns
partial results when one provider is unavailable and identifies the failure in
the `errors` array.

## Open WebUI

Copy `open_webui_tool.py` into Open WebUI's Tools area. Set these user valves:

- `BRIDGE_SCRIPT`: absolute path to this directory's `p4_comms.py` (optional if
  the two files are copied together).
- `CONFIG_PATH`: absolute path to the bridge `config.json`.
- `PYTHON_PATH`: optional Python executable used to run the bridge.
- `TIMEOUT_SECONDS`: per-call limit, normally 60.

The Open WebUI adapter exposes separate methods for mail, calendars, Teams,
and health. It does not import or modify Open WebUI internals, so the whole
`p4-unified-communications` directory can be copied into P4, versioned, or
used by another application.

## Provider boundaries

Microsoft Teams is a Microsoft 365 service, so Teams methods use the signed-in
Microsoft tenant and do not apply to Gmail or Yahoo accounts. Calendars are
provider-specific: Microsoft uses Graph, Google uses Calendar API v3 through
Calendula, and Yahoo uses CalDAV. The bridge presents these as one result set,
but it does not pretend that provider-specific permissions or fields are
identical.

## Verification

The tests do not contact any provider or require credentials:

```powershell
python -m unittest discover -s tests -v
```
