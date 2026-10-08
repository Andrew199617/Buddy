# Outlook Research for Open WebUI

This tool invokes the open-source [CLI for Microsoft 365](https://github.com/pnp/cli-microsoft365) (`m365`) from Open WebUI. PnP's CLI owns the OAuth device-code login and refresh-token flow; the Open WebUI tool only runs a small, fixed set of read-only commands and never accepts arbitrary shell input.

## 1. Install the CLI

The machine that runs Open WebUI needs Node.js 20 or newer and the CLI:

```powershell
npm install -g @pnp/cli-microsoft365
m365 --version
```

If `m365` is not on the PATH inherited by Open WebUI, find it with `Get-Command m365` and put its full path (usually `...\AppData\Roaming\npm\m365.cmd`) in this tool's `M365_CLI_PATH` User Valve.

## 2. Log in once, in the Open WebUI runtime account

Run these commands in the same Windows account/environment that runs Open WebUI:

```powershell
m365 setup
m365 login --authType deviceCode
m365 status
```

`m365 setup` can guide creation/configuration of a Microsoft Entra application. If you use an existing app, make it a public client for device-code login and grant the delegated permissions needed by this tool: `User.Read`, `Mail.Read`, and `Calendars.Read`. The CLI's device-code flow opens a browser (or prints a code for another device), and it refreshes access tokens after login. [CLI login documentation](https://pnp.github.io/cli-microsoft365/cmd/login/)

If your tenant requires a specific app or tenant, use:

```powershell
m365 login --authType deviceCode --appId YOUR_APP_ID --tenant YOUR_TENANT_ID
```

The CLI persists its connection in the current user's profile. On Windows, its token/MSAL files are under the user's home directory and are not encrypted, so protect that Windows account and do not use this shared CLI connection for an untrusted multi-user Open WebUI instance. [Connection persistence details](https://pnp.github.io/cli-microsoft365/concepts/persisting-connection/)

## 3. Import the Open WebUI tool

1. Open **Workspace → Tools → Import JSON**.
2. Import `local-outlook-research.json` from this directory.
3. Enable **Outlook Research** in a chat.
4. Ask `Check my Outlook connection.`

The editable source is `local-outlook-research.py`. The import bundle calls only `m365 status`, read-only `m365 request --method get` calls against `/me/...`, and the read-only `m365 outlook event list` command. It cannot send, edit, move, cancel, or delete mail/events, and it does not download attachments.

## Example prompts

- `Search my Outlook for "Q4 milestone" and summarize the relevant messages with their Outlook links.`
- `List the latest messages in my inbox, then read the two most relevant ones.`
- `List my calendar from 2026-09-14T00:00:00-07:00 through 2026-09-21T00:00:00-07:00 using Pacific Standard Time.`

Mail and calendar text is returned as untrusted source material. Search/list operations are permission-limited and may be paginated; pass the returned `next_link` back to the tool when more results are needed.

## Troubleshooting

- `m365_cli_not_found`: install the package, restart Open WebUI, or set `M365_CLI_PATH` to the absolute path of `m365.cmd`.
- `m365_not_logged_in`: run `m365 login --authType deviceCode` in the same runtime account.
- HTTP 401/403 or a CLI permission error: reconnect after granting `User.Read`, `Mail.Read`, and/or `Calendars.Read`; tenant admin consent may be required.
- A shared or service account sees the wrong mailbox: run `m365 connection list`, then `m365 connection use --name NAME` in that same runtime account before testing.

## Why this CLI

The Microsoft Graph CLI is retired and no longer actively maintained. PnP's CLI for Microsoft 365 is open source, MIT-licensed, cross-platform, includes Outlook commands, and uses device-code login with persisted refresh tokens. [Project repository and license](https://github.com/pnp/cli-microsoft365), [Outlook message commands](https://pnp.github.io/cli-microsoft365/cmd/outlook/message/message-list/), [Outlook event commands](https://pnp.github.io/cli-microsoft365/beta/cmd/outlook/event/event-list/)
