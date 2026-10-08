# Provider setup

This document is intentionally separate from the bridge code. It explains
where each provider is authenticated; `config.json` only maps those existing
accounts to the bridge.

## 1. Microsoft Outlook and Teams

Install and authenticate PnP CLI for Microsoft 365 in the Windows user that
will run Open WebUI:

```powershell
npm install --global @pnp/cli-microsoft365
m365 login
m365 status --output json
```

The bridge uses this login for:

- `outlook event list` (the Outlook calendar)
- `teams team list`
- `teams channel list`
- `teams message list`
- `teams chat list` and `teams chat message list`

Set the Outlook account's `username` in `config.json` to its Microsoft 365
UPN, usually the address used to sign in. PnP's Outlook event command requires
either `--userName` or `--userId` even for the currently logged-in mailbox.
The bridge passes `username` as `--userName`.

Teams access is limited by the permissions granted to the PnP CLI login. The
bridge exposes only list/read operations; it does not call the PnP send,
remove, move, or delete commands.

## 2. Mail accounts through Himalaya

Configure one Himalaya account for each mailbox. The account names are the
values used by `email_account` in the bridge config:

```powershell
himalaya
himalaya account list
```

Himalaya supports IMAP and dedicated Gmail, Microsoft Graph, and other
backends. Use its own documented OAuth/token-broker flow for Gmail or
Microsoft, or an app password where the provider requires one. Keep the
Himalaya TOML configuration outside this project when possible and point the
bridge at it with `himalaya_config`.

For Yahoo Mail, create a Yahoo app password and configure Yahoo's IMAP
account in Himalaya. Do not use your normal Yahoo password in scripts.

## 3. Calendars through Calendula

Configure Google and Yahoo calendar accounts in Calendula. Their names are
the values used by `calendar_account` in the bridge config:

```powershell
calendula configure
calendula calendar list --account gmail
calendula calendar list --account yahoo
```

For Google, choose Calendula's `gcal` backend and use OAuth through Ortie (or
another token command) as described in the upstream documentation. For Yahoo,
choose the CalDAV backend and use the Yahoo CalDAV service URL:

```text
https://caldav.calendar.yahoo.com
```

Yahoo Calendar requires an app-generated password for third-party CalDAV
clients. The bridge does not store that password; use a password-manager
command in Calendula's configuration.

## 4. Map the accounts

Copy the example and change the account names/paths:

```powershell
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

The three example entries intentionally use the same names (`outlook`,
`gmail`, `yahoo`) for both upstream CLIs. If your upstream names differ, only
change `email_account` and/or `calendar_account`.

Use a real Microsoft UPN in the Outlook `username` field:

```json
{
  "name": "outlook",
  "provider": "outlook",
  "email_account": "work-outlook",
  "calendar_backend": "m365",
  "calendar_account": "outlook",
  "username": "you@contoso.com"
}
```

Check the result without contacting providers:

```powershell
python .\p4_comms.py --config .\config.json --pretty health
```

When the dependencies and account mappings are ready, run connection probes:

```powershell
python .\p4_comms.py --config .\config.json --pretty health --probe
```

## Credential boundaries

- PnP CLI stores the Microsoft login in its own cache.
- Himalaya and Calendula read their own TOML files and token/password
  commands.
- Ortie or another password manager can supply OAuth tokens without placing
  them in this project.
- `config.json` should contain account names and paths only. It is ignored by
  this project's `.gitignore`.
