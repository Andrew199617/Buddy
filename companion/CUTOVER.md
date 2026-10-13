# Moving production to the Buddy app

This moves the release instance (`docker/buddy/compose.yaml`) from the
address-registered Runner on 127.0.0.1:8765 to this PC connected through the
Buddy app. Each step changes live data or a running service, so do one at a
time and check it before the next.

## 1. Back up

In Admin Settings → Backups, choose **Run backup now** and wait for a new
successful run with its changelist. The deploy below adds a database table, so
this is the copy to restore if anything goes wrong.

## 2. Deploy

Update the checkout the release stack builds from to the merged branch, then
rebuild only Buddy's containers:

```powershell
docker compose -f docker/buddy/compose.yaml up -d --build buddy web
```

Add `-f docker/buddy/runner-tls.yaml` after `compose.yaml` if the stack uses
the Runner TLS overlay. On start, the migration adds the
`subscription_credential` table. Stored tokens and keys are encrypted with a
key derived from `WEBUI_SECRET_KEY` unless `BUDDY_SUBSCRIPTION_SECRET_KEY` is
set, so keep `WEBUI_SECRET_KEY` unchanged.

## 3. Check the old setup still works

- Admin Settings → AI plans lists the PC under **Your Runner addresses (older
  setup)**, and Claude and ChatGPT still run on it.
- The first administrator is now the instance owner and keeps the existing
  provider settings. Other accounts start with none.

## 4. Install the app on this PC

Build it from `desktop/` (see [its guide](../desktop/README.md)) and run
`release\Buddy Setup <version>.exe`. Windows SmartScreen warns because the
installer is not signed yet. Open the app, enter `http://localhost:8082` as
the Buddy address, and sign in as the owner.

## 5. Connect this PC

In **Settings → Machines**, choose **Connect this computer** and confirm.
Then match what the old Runner allowed: share the same folders it had as
`-Root`, and turn on **Allow editing files** and **Allow running commands**
only if it ran with `-WriteFiles` and `-AllowHostExecution`. Turn on **Start
Buddy when you sign in** so the PC reconnects after a restart.

## 6. Move Claude and ChatGPT

In **Settings → Machines → AI plans**, open each provider's gear button, set
**Runs on** to this PC, and choose **Your computer's sign-in**. The CLIs and
their sign-ins are the same ones the old Runner used, so the status should
show **Signed in**. Send one short message to each to confirm; it uses the
plan.

## 7. Retire the old Runner

Once both providers run on the app's connection:

1. Stop the Runner on 8765, and remove whatever starts
   `local\start-buddy-runner.ps1` at sign-in.
2. In Admin Settings → AI plans, remove the PC from **Your Runner addresses (older
   setup)**. Removing it earlier would turn off any provider still using it.

Each chat's Project tab opens the PC through Buddy, including from a phone,
so the Runner's browser address and pairing are no longer needed. Choose
where Claude and ChatGPT work per chat folder, in its **Project folder**
setting; see [Project folders](../BUDDY.md#project-folders).

## Rolling back

Rebuild the stack from the previous commit, then start the old Runner again
with `local\start-buddy-runner.ps1`. The old machine settings were never
changed, and the earlier code ignores the new table. If the Runner refuses to
start because its key folder is shared, fix it with
[these steps](README.md#keep-the-runner-key-private). Restore the backup from
step 1 only if data itself went wrong.
