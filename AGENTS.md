# Product design context

We are designing the Buddy website and the mobile version of the app in this repository.
Keep the desktop and mobile experiences coherent, and treat touch input, safe areas, and on-screen keyboard behavior as part of the mobile design.

# Code readability

- Prefer straightforward code that can be read once over compact code that saves lines.
- For small, fixed sets of commands or actions, write explicit registration calls. Do not encode control flow in nested arrays of command/callback tuples and immediately loop over them.
- Extract repeated error handling and disposal into a named helper. Give substantial callbacks and configuration objects meaningful names before passing them to another function.
- Use ordinary conditionals instead of nested ternaries. Keep distinct operations on separate lines and use explicit object properties when mapping a small, fixed set of settings.
- Do not address a readability complaint by only wrapping the same dense expression across more lines. Simplify the structure.

# Commits

- Make small commits that each do one thing and read on their own: one module, one fix, one UI piece, or one config change. Do not put a whole feature or a whole round of review fixes in one commit.
- Commit as the work progresses. When the working tree holds several changes, split them into separate commits with `git add <paths>` or `git add -p`.
- Order commits so each builds on the one before, and put tests in the same commit as the code they cover.
- Give each commit a subject that says what changed and a short body that says why.

# Codex commit attribution

This PC is also used by the human repository owner. New commits created by
Codex in this repository must use this identity for both author and committer:

- Name: `AndrewVelezMuse`
- Email: `336336425+AndrewVelezMuse@users.noreply.github.com`

Apply the identity to each Git command rather than changing saved Git settings:

```powershell
git -c user.name=AndrewVelezMuse -c user.email=336336425+AndrewVelezMuse@users.noreply.github.com commit -m "Commit message"
```

- Use the same command-scoped overrides for other Git operations that create commits.
- Check for conflicting `GIT_AUTHOR_*` and `GIT_COMMITTER_*` environment variables before committing, and verify the resulting author and committer before pushing.
- Preserve existing authors when amending, cherry-picking, or rebasing unless the user explicitly requests an authorship change. Use the Muse identity for Codex's committer identity.
- Keep the human's saved repository and global Git identity settings intact. Do not rewrite prior commit attribution to apply this preference.

# Backups (release only)

The release instance is the Docker stack `docker/buddy/compose.yaml` (project `buddy`, volume `buddy_buddy-data`). Its `backup` service (`docker/buddy/backup/`) backs it up to the owner's Perforce depot.

- **Schedule.** Nightly at `BUDDY_BACKUP_TIME` in `BUDDY_BACKUP_TZ` (default 03:00 America/Los_Angeles, DST-aware; a time skipped when clocks spring forward runs right after the jump, and a repeated time runs at its first occurrence). If the PC was off at that time, the service catches up once when it starts. A failed scheduled run is retried hourly.
- **Contents.** Each run submits one changelist to `BUDDY_BACKUP_DEPOT_PATH` (default `//Backups/Buddy`). Filetype is `binary+S7`, so the server keeps the last 7 revisions of each file:
  - `webui.db`: an online-backup copy of the SQLite database, integrity-checked.
  - `data.tar.gz`: the rest of the data volume (uploads, `vector_db`, `static`, generated images and audio). SQLite files inside it are copied the same way. The model caches `cache/embedding`, `cache/whisper` and `cache/tiktoken` are left out; Buddy downloads them again.
  - `buddy.env`: `docker/buddy/.env`, including `WEBUI_SECRET_KEY`. The depot is restricted to the owner.
- **Setup.**
  - Put these in the release `.env` only: `COMPOSE_PROFILES=backup`, `BUDDY_BACKUP_P4PORT` and `BUDDY_BACKUP_P4USER` (see `example.env`).
  - Start it with `docker compose -f docker/buddy/compose.yaml up -d --build backup`. That starts only the backup service; `buddy` and `web` are not recreated.
  - The service reuses the PC's Perforce login: `%USERPROFILE%\p4tickets.txt` and `p4trust.txt` are mounted read-only. If the ticket expires, the owner runs `p4 login` on the PC. Workspace: `buddy-backup`.
  - Check mounts and login without printing secrets: `docker compose -f docker/buddy/compose.yaml run --rm backup check`.
- **Safety.**
  - The data volume and `.env` are mounted read-only.
  - Never print `.env` values or `docker compose config` output.
  - Never point a test instance at `//Backups/Buddy`. Rehearse with `BACKUP_DEPOT_PATH=//Backups/Buddy-test` and `BACKUP_P4CLIENT=buddy-backup-test`, in a one-off `docker run` with its own status volume.
- **Status.**
  - Admin Settings → Backups shows:
    - the last success: time, changelist and sizes;
    - a red alert with the error when the latest run failed, or when nothing succeeded in 26 hours;
    - an amber warning when the service has not checked in for 5 minutes;
    - run history with logs;
    - **Run backup now**, which queues a run; the service starts it within 30 seconds.
  - Runs and the heartbeat live in `status.db` on volume `buddy_backup-status`. Buddy mounts it at `/app/backend/backup-status` (`BUDDY_BACKUP_STATUS_DB`).
  - The status is kept out of `webui.db` so the service never writes to the live database.
  - Service log: `docker compose -f docker/buddy/compose.yaml logs backup`.
- **Restore** (the owner's call; it replaces live data):
  1. Pick a changelist with `p4 changes -l -m 10 //Backups/Buddy/...`. Its description shows the sizes and the database's alembic revision, so run code at least that new.
  2. Fetch the three files into an empty folder outside the repo, for example `p4 print -o webui.db //Backups/Buddy/webui.db@<change>`. The fetched files are read-only.
  3. Stop Buddy: `docker compose -f docker/buddy/compose.yaml stop buddy web`.
  4. Keep the current data: `docker run --rm -v buddy_buddy-data:/from:ro -v buddy_before_restore:/to alpine:3 cp -a /from/. /to/`
  5. Restore: `docker run --rm -v buddy_buddy-data:/data -v "<folder>:/restore:ro" alpine:3 sh -c "find /data -mindepth 1 -delete && tar -xzf /restore/data.tar.gz -C /data && cp /restore/webui.db /data/webui.db && chmod 666 /data/webui.db"`
     - Never copy `webui.db` over an existing one without removing its `-wal`/`-shm` files: SQLite would apply the old WAL to the restored database.
  6. Copy `buddy.env` to `docker/buddy/.env`. Without the same `WEBUI_SECRET_KEY`, sessions and stored tool tokens stop working.
  7. Start Buddy: `docker compose -f docker/buddy/compose.yaml up -d buddy web`.
  8. If knowledge search looks wrong, reindex in Admin Settings → Documents. Vector index files are copied live, and Buddy rebuilds them from the files.
  - Never run a restored copy next to the release instance; it would repeat scheduled automations. For a parallel copy, use `local/snapshot_buddy_data.py`, which pauses them.
- **Maintenance.**
  - Keep the backup image's base (`python:3.11-slim-bookworm`) the same as the `base` stage of the repository `Dockerfile`, so both use the same SQLite.
  - When Perforce republishes the `p4` build, compare the new checksum with the `p4` line of `SHA256SUMS` in the same download directory before updating the Dockerfile.
  - Tests: `local/tests/test_buddy_backup_service.py` (schedule, status store, snapshots and the p4 flow) and `local/tests/test_backup_status.py` (the admin API). Frontend tests: `src/lib/apis/backups/index.test.ts` and `src/lib/utils/backups.test.ts`.
