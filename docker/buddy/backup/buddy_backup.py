#!/usr/bin/env python3
"""Nightly backup of the RELEASE Buddy instance to a Perforce depot (the `backup` service of docker/buddy/compose.yaml).

Each run submits ONE changelist to BACKUP_DEPOT_PATH (default //Backups/Buddy) holding three files with filetype
binary+S7, so the server keeps only the last 7 revisions of each:
  webui.db      Buddy's SQLite database, copied with the online backup API and integrity-checked
  data.tar.gz   the rest of the data volume (uploads, vector_db, static, generated images and audio); SQLite files
                inside it are copied the same way, and re-downloadable model caches are left out
  buddy.env     docker/buddy/.env, the secrets a restore needs (WEBUI_SECRET_KEY)

The data volume and buddy.env are mounted read-only. Runs and a heartbeat are recorded in a small SQLite status
file on the `backup-status` volume; Buddy's admin Settings > Backups tab reads it there and queues manual runs
(backend/open_webui/utils/backups.py). Keep the table and column names stable: both sides depend on them.

The image is built FROM Buddy's own Python base image, so the SQLite library that copies webui.db is the one the
release instance writes it with. Standard library only.

Usage: buddy_backup.py serve | run-now | check
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = '/backup/data'
ENV_FILE = '/backup/buddy.env'
STATUS_DB = '/status/status.db'
HOST_TICKETS = '/p4host/p4tickets.txt'
HOST_TRUST = '/p4host/p4trust.txt'
WORK_DIR = '/work'

LOOP_SECONDS = 30
# A failed scheduled run is retried after this long, until one succeeds or the next slot comes.
RETRY_FAILED_AFTER = timedelta(hours=1)
LOG_LIMIT = 20000
FILETYPE = 'binary+S7'

DATABASE_NAME = 'webui.db'
ARCHIVE_NAME = 'data.tar.gz'
ENV_NAME = 'buddy.env'
BACKUP_FILES = (DATABASE_NAME, ARCHIVE_NAME, ENV_NAME)

# Model downloads Buddy fetches again on first use (about 1.2 GB together). Paths are relative to the data volume.
EXCLUDED_PATHS = ('cache/embedding', 'cache/whisper', 'cache/tiktoken')
SQLITE_SIDE_FILE_SUFFIXES = ('-wal', '-shm', '-journal')
SQLITE_HEADER = b'SQLite format 3\x00'
REQUIRED_ENV_KEYS = ('WEBUI_SECRET_KEY',)
# Times webui.db and data.tar.gz are taken again when Buddy deletes files in between, before the run fails.
SNAPSHOT_ATTEMPTS = 3

STATUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS backup_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,          -- queued | running | succeeded | failed
    trigger TEXT NOT NULL,         -- scheduled | manual
    requested_by TEXT,             -- name of the admin who queued a manual run
    created_at TEXT NOT NULL,      -- UTC, fixed-width text (format_time) so values compare as text
    started_at TEXT,
    finished_at TEXT,
    changelist INTEGER,
    db_bytes INTEGER,
    data_bytes INTEGER,
    env_bytes INTEGER,
    db_revision TEXT,              -- alembic revision of the copied webui.db
    error TEXT,
    log TEXT
);
CREATE INDEX IF NOT EXISTS backup_runs_created_at ON backup_runs (created_at);
CREATE TABLE IF NOT EXISTS backup_scheduler (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat_at TEXT,
    next_run_at TEXT,
    schedule_time TEXT,
    timezone TEXT,
    depot_path TEXT,
    p4_port TEXT
);
"""


class BackupError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ settings


def env_value(name, default=''):
    """Environment value with one pair of surrounding quotes removed (docker run --env-file keeps them)."""
    value = os.environ.get(name, default)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        value = value[1:-1]
    return value


@dataclass
class Settings:
    depot: str = '//Backups/Buddy'
    p4_port: str = ''
    p4_user: str = ''
    p4_client: str = 'buddy-backup'
    schedule_time: str = '03:00'
    timezone: str = 'UTC'


def load_settings():
    return Settings(
        depot=env_value('BACKUP_DEPOT_PATH', '//Backups/Buddy').rstrip('/'),
        p4_port=env_value('BACKUP_P4PORT'),
        p4_user=env_value('BACKUP_P4USER'),
        p4_client=env_value('BACKUP_P4CLIENT', 'buddy-backup'),
        schedule_time=env_value('BACKUP_TIME', '03:00'),
        timezone=env_value('TZ', 'UTC'),
    )


# -------------------------------------------------------------------------------------------------- times


def utcnow():
    return datetime.now(UTC)


def format_time(value):
    """UTC timestamp with a fixed width (millisecond precision), so stored values sort and compare as text."""
    utc_value = value.astimezone(UTC)
    milliseconds = utc_value.microsecond // 1000
    return utc_value.strftime('%Y-%m-%dT%H:%M:%S') + f'.{milliseconds:03d}Z'


def parse_time(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def parse_schedule_time(text):
    hour_text, minute_text = text.split(':')
    hour = int(hour_text)
    minute = int(minute_text)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f'BACKUP_TIME must be HH:MM, got {text!r}')
    return hour, minute


def slot_on(day: date, hour, minute, zone):
    """The UTC instant of the scheduled wall-clock time on a local calendar day.

    A time skipped when clocks spring forward runs the same distance after the jump (02:30 becomes 03:30), and a
    time repeated when they fall back runs at its first occurrence (fold=0).
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(UTC)


def schedule_slots(now_utc, schedule_time, timezone_name):
    """(last scheduled slot <= now, next slot > now) as aware UTC datetimes.

    Slots are built from local calendar dates, so the wall-clock time stays the same across DST changes. They
    are compared as UTC instants: comparing two times of the same zone in Python compares their wall clocks,
    which puts a slot in a skipped hour after a later instant.
    """
    zone = ZoneInfo(timezone_name)
    hour, minute = parse_schedule_time(schedule_time)
    today = now_utc.astimezone(zone).date()
    today_slot = slot_on(today, hour, minute, zone)
    if now_utc >= today_slot:
        return today_slot, slot_on(today + timedelta(days=1), hour, minute, zone)
    return slot_on(today - timedelta(days=1), hour, minute, zone), today_slot


# ------------------------------------------------------------------------------------------- status store


@dataclass
class RunResult:
    ok: bool
    log: list
    changelist: int | None = None
    sizes: dict = field(default_factory=dict)
    db_revision: str | None = None
    error: str | None = None


def scheduled_run_due_in(connection, last_slot, now):
    """The due rule of StatusStore.scheduled_run_due, inside the caller's connection or transaction."""
    slot_text = format_time(last_slot)
    handled = connection.execute(
        "SELECT count(*) FROM backup_runs WHERE status IN ('succeeded', 'running', 'queued') AND created_at >= ?",
        (slot_text,),
    ).fetchone()[0]
    if handled:
        return False
    last_failure = connection.execute(
        "SELECT max(finished_at) FROM backup_runs WHERE status = 'failed' AND trigger = 'scheduled' "
        'AND created_at >= ?',
        (slot_text,),
    ).fetchone()[0]
    if last_failure is None:
        return True
    return now - parse_time(last_failure) >= RETRY_FAILED_AFTER


def insert_running_run(connection, trigger, now):
    run_id = str(uuid.uuid4())
    connection.execute(
        'INSERT INTO backup_runs (id, status, trigger, created_at, started_at) VALUES (?, ?, ?, ?, ?)',
        (run_id, 'running', trigger, format_time(now), format_time(now)),
    )
    return run_id


class StatusStore:
    """The status file both the backup service and Buddy's backend open (on the shared `backup-status` volume)."""

    def __init__(self, path):
        self.path = path

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE takes the write lock up front, so Buddy queuing a run and the service claiming it never
        interleave."""
        with closing(self.connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
            except BaseException:
                connection.execute('ROLLBACK')
                raise
            connection.execute('COMMIT')

    def ensure_schema(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(STATUS_SCHEMA)

    def heartbeat(self, settings, next_slot, now):
        with self.transaction() as connection:
            connection.execute(
                'INSERT INTO backup_scheduler '
                '(id, last_heartbeat_at, next_run_at, schedule_time, timezone, depot_path, p4_port) '
                'VALUES (1, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT (id) DO UPDATE SET last_heartbeat_at = excluded.last_heartbeat_at, '
                'next_run_at = excluded.next_run_at, schedule_time = excluded.schedule_time, '
                'timezone = excluded.timezone, depot_path = excluded.depot_path, p4_port = excluded.p4_port',
                (
                    format_time(now),
                    format_time(next_slot),
                    settings.schedule_time,
                    settings.timezone,
                    settings.depot,
                    settings.p4_port,
                ),
            )

    def fail_interrupted_runs(self, now):
        with self.transaction() as connection:
            connection.execute(
                "UPDATE backup_runs SET status = 'failed', finished_at = ?, "
                "error = 'Interrupted: the backup service restarted while this run was in progress.' "
                "WHERE status = 'running'",
                (format_time(now),),
            )

    def claim_queued_run(self, now):
        """Mark the oldest queued (manual) run as running and return its id, or None."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM backup_runs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE backup_runs SET status = 'running', started_at = ? WHERE id = ?",
                (format_time(now), row['id']),
            )
            return row['id']

    def scheduled_run_due(self, last_slot, now):
        """Due when nothing succeeded or is in flight since the last slot, so a missed 03:00 catches up once.

        A failed attempt since the slot is retried after RETRY_FAILED_AFTER, so a lasting failure (for example an
        expired Perforce ticket) is retried hourly instead of every loop.
        """
        with closing(self.connect()) as connection:
            return scheduled_run_due_in(connection, last_slot, now)

    def start_scheduled_run_if_due(self, last_slot, now):
        """Start the scheduled run in the same transaction that checks it is due; returns its id or None.

        A run Buddy queued in the meantime goes first: it is claimed on the next loop, and when it was queued after
        the slot it also counts as that night's backup. Nothing starts while any run is running, whenever it began
        (a `run-now` started before the slot shares /work/ws and the Perforce client).
        """
        with self.transaction() as connection:
            waiting_or_running = connection.execute(
                "SELECT count(*) FROM backup_runs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            if waiting_or_running:
                return None
            if not scheduled_run_due_in(connection, last_slot, now):
                return None
            return insert_running_run(connection, 'scheduled', now)

    def start_run(self, trigger, now):
        """Insert a running run and return its id; refuses while another run is running."""
        with self.transaction() as connection:
            running = connection.execute("SELECT count(*) FROM backup_runs WHERE status = 'running'").fetchone()[0]
            if running:
                raise BackupError('Another backup is already running.')
            return insert_running_run(connection, trigger, now)

    def finish_run(self, run_id, result: RunResult, now):
        if result.ok:
            status = 'succeeded'
        else:
            status = 'failed'
        log_text = '\n'.join(result.log)[-LOG_LIMIT:]
        with self.transaction() as connection:
            connection.execute(
                'UPDATE backup_runs SET status = ?, finished_at = ?, changelist = ?, db_bytes = ?, data_bytes = ?, '
                'env_bytes = ?, db_revision = ?, error = ?, log = ? WHERE id = ?',
                (
                    status,
                    format_time(now),
                    result.changelist,
                    result.sizes.get(DATABASE_NAME),
                    result.sizes.get(ARCHIVE_NAME),
                    result.sizes.get(ENV_NAME),
                    result.db_revision,
                    result.error,
                    log_text,
                    run_id,
                ),
            )


# ------------------------------------------------------------------------------------------------- sqlite


def is_sqlite_database(path):
    with open(path, 'rb') as handle:
        return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def snapshot_sqlite(source, destination):
    """Copy a live SQLite database with the online backup API in one step (one read transaction), then check it.

    The source is opened read-only (mode=ro), which works on the read-only data mount, also in WAL mode.
    """
    source_uri = Path(source).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(source_uri, uri=True, timeout=60)) as source_connection:
        with closing(sqlite3.connect(destination)) as copy_connection:
            source_connection.backup(copy_connection)
            result = copy_connection.execute('PRAGMA integrity_check').fetchone()[0]
    if result != 'ok':
        raise BackupError(f'The copy of {source} failed its integrity check: {result}')
    for suffix in ('-wal', '-shm'):
        leftover = destination + suffix
        if os.path.exists(leftover):
            raise BackupError(f'The copy of {source} left {os.path.basename(leftover)} behind')


def file_record_ids(database_path):
    """Ids in Buddy's `file` table (uploads and knowledge files), or None when the database has no such table.

    Read-only, so it also works on the live database through the read-only mount.
    """
    database_uri = Path(database_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(database_uri, uri=True, timeout=60)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'file' not in tables:
            return None
        return {row[0] for row in connection.execute('SELECT id FROM file')}


def describe_database(path):
    """(alembic revision or None, 'users N, chats N') of a copied webui.db, for the log and the description."""
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        revision = None
        if 'alembic_version' in tables:
            row = connection.execute('SELECT version_num FROM alembic_version').fetchone()
            if row:
                revision = row[0]
        counts = []
        for table in ('user', 'chat'):
            if table in tables:
                count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                counts.append(f'{table} rows {count}')
    return revision, ', '.join(counts)


# ------------------------------------------------------------------------------------------------ archive


def is_left_out(member_name, directory, file_name):
    """True for files the archive skips: the top-level webui.db (backed up on its own), model caches, and the
    -wal/-shm/-journal files next to an SQLite database (its snapshot already includes them)."""
    if member_name in EXCLUDED_PATHS:
        return True
    top_level_database = member_name == DATABASE_NAME or member_name.startswith(DATABASE_NAME + '-')
    if top_level_database:
        return True
    for suffix in SQLITE_SIDE_FILE_SUFFIXES:
        if file_name.endswith(suffix):
            database_name = file_name[: -len(suffix)]
            if os.path.exists(os.path.join(directory, database_name)):
                return True
    return False


def add_sqlite_snapshot(archive, source_path, member_name, scratch_dir):
    snapshot_path = os.path.join(scratch_dir, f'{uuid.uuid4().hex}.sqlite')
    try:
        snapshot_sqlite(source_path, snapshot_path)
        member = archive.gettarinfo(snapshot_path, arcname=member_name)
        source_stat = os.stat(source_path)
        member.mode = source_stat.st_mode & 0o7777
        member.mtime = int(source_stat.st_mtime)
        with open(snapshot_path, 'rb') as handle:
            archive.addfile(member, handle)
    finally:
        if os.path.exists(snapshot_path):
            os.remove(snapshot_path)


def add_directory_contents(archive, source_dir, prefix, scratch_dir, log, vanished):
    for name in sorted(os.listdir(source_dir)):
        source_path = os.path.join(source_dir, name)
        member_name = f'{prefix}/{name}' if prefix else name
        if is_left_out(member_name, source_dir, name):
            continue
        try:
            if os.path.isdir(source_path) and not os.path.islink(source_path):
                archive.add(source_path, arcname=member_name, recursive=False)
                add_directory_contents(archive, source_path, member_name, scratch_dir, log, vanished)
            elif os.path.isfile(source_path) and not os.path.islink(source_path) and is_sqlite_database(source_path):
                add_sqlite_snapshot(archive, source_path, member_name, scratch_dir)
                log.append(f'{member_name}: SQLite copied with the backup API')
            else:
                archive.add(source_path, arcname=member_name, recursive=False)
        except FileNotFoundError:
            # Deleted by Buddy between listing and reading; the caller takes the backup again.
            vanished.append(member_name)


def build_data_archive(data_dir, archive_path, scratch_dir, log):
    """Write the archive; returns the members that disappeared while it was being written."""
    vanished = []
    with tarfile.open(archive_path, 'w:gz', compresslevel=6) as archive:
        add_directory_contents(archive, data_dir, '', scratch_dir, log, vanished)
    return vanished


# ----------------------------------------------------------------------------------------------- env file


def env_file_keys(path):
    """{key: has a non-empty value} for a dotenv file. Values are never returned, logged or printed."""
    keys = {}
    with open(path, encoding='utf-8-sig') as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if key.startswith('export '):
                key = key[len('export ') :].strip()
            value = value.strip().strip('"\'')
            keys[key] = bool(value)
    return keys


def copy_env_file(source, destination, log):
    if not os.path.isfile(source):
        raise BackupError(f'{source} is not mounted: docker/buddy/.env is required for a restore')
    shutil.copyfile(source, destination)
    keys = env_file_keys(destination)
    for required in REQUIRED_ENV_KEYS:
        if not keys.get(required):
            raise BackupError(f'{ENV_NAME} has no value for {required}; a restore needs it')
    log.append(f'{ENV_NAME}: {len(keys)} keys ({", ".join(sorted(keys))})')


# ----------------------------------------------------------------------------------------------- commands


def run(command, log, env=None, input_text=None, timeout=3600, ok_codes=(0,)):
    log.append('$ ' + ' '.join(command))
    process = subprocess.run(
        command, capture_output=True, text=True, env=env, input=input_text, timeout=timeout, check=False
    )
    output = (process.stdout + process.stderr).strip()
    if output:
        log.append(output[-4000:])
    if process.returncode not in ok_codes:
        detail = (process.stderr or process.stdout).strip()[-2000:]
        command_name = ' '.join(command[:2])
        raise BackupError(f'{command_name} exited {process.returncode}: {detail}')
    return process.stdout


# ----------------------------------------------------------------------------------------------- perforce


def private_copy_lf(source, destination):
    """Copy a host ticket/trust file (CRLF on Windows) to a private LF copy p4 can read."""
    if not os.path.isfile(source):
        raise BackupError(f'{source} is not mounted: the Perforce ticket and trust files of the host are required')
    with open(source, 'rb') as handle:
        data = handle.read().replace(b'\r\n', b'\n')
    with open(destination, 'wb') as handle:
        handle.write(data)
    os.chmod(destination, 0o600)


class Perforce:
    def __init__(self, settings, log):
        self.settings = settings
        self.log = log
        self.env = None

    def prepare(self):
        if not self.settings.p4_port or not self.settings.p4_user:
            raise BackupError('BACKUP_P4PORT and BACKUP_P4USER must be set (BUDDY_BACKUP_P4PORT/_P4USER in .env)')
        home = os.path.expanduser('~')
        tickets = os.path.join(home, '.p4tickets')
        trust = os.path.join(home, '.p4trust')
        private_copy_lf(HOST_TICKETS, tickets)
        private_copy_lf(HOST_TRUST, trust)
        env = dict(os.environ)
        env.update(
            P4TICKETS=tickets,
            P4TRUST=trust,
            P4PORT=self.settings.p4_port,
            P4USER=self.settings.p4_user,
            P4CLIENT=self.settings.p4_client,
            P4CHARSET='auto',
            P4CONFIG='',
            P4ENVIRO='/dev/null',
        )
        self.env = env

    def run(self, args, **kwargs):
        return run(['p4', *args], self.log, env=self.env, **kwargs)

    def client_spec(self, root):
        return (
            f'Client: {self.settings.p4_client}\n'
            f'Owner: {self.settings.p4_user}\n'
            'Host:\n'
            f'Root: {root}\n'
            'Options: allwrite clobber nocompress unlocked nomodtime normdir\n'
            'SubmitOptions: submitunchanged\n'
            'LineEnd: local\n'
            'Description:\n\tBuddy release backups (docker/buddy/backup/buddy_backup.py).\n'
            'View:\n'
            f'\t{self.settings.depot}/... //{self.settings.p4_client}/...\n'
        )

    def file_exists_at_head(self, depot_file):
        info = self.run(['-ztag', 'fstat', '-T', 'headAction', depot_file], ok_codes=(0, 1))
        if 'headAction' not in info:
            return False
        deleted = 'headAction delete' in info or 'headAction move/delete' in info
        return not deleted

    def submit(self, root, file_names, description):
        """Open every file for add or edit (binary+S7) and submit them as one changelist; returns its number."""
        client_files = f'//{self.settings.p4_client}/...'
        self.run(['client', '-i'], input_text=self.client_spec(root))
        # Drop files a killed run left open, without touching the new local files.
        self.run(['revert', '-k', client_files], ok_codes=(0, 1))
        # Mark the head revisions as synced without downloading old backups.
        self.run(['flush', f'{self.settings.depot}/...'], ok_codes=(0, 1))
        for name in file_names:
            local_path = os.path.join(root, name)
            if self.file_exists_at_head(f'{self.settings.depot}/{name}'):
                self.run(['edit', '-t', FILETYPE, local_path])
            else:
                self.run(['add', '-t', FILETYPE, local_path])
        try:
            output = self.run(['-ztag', 'submit', '-d', description])
        except BackupError:
            self.run(['revert', '-k', client_files], ok_codes=(0, 1))
            raise
        for line in output.splitlines():
            if line.startswith('... submittedChange '):
                return int(line.split()[-1])
        raise BackupError('p4 submit did not report a changelist')


# ------------------------------------------------------------------------------------------------- backup


def describe_change(settings, trigger, run_id, sizes, db_revision):
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime('%Y-%m-%d %H:%M %Z')
    return (
        f'Buddy backup {stamp} ({trigger})\n'
        f'webui.db {sizes[DATABASE_NAME]} B (alembic {db_revision or "?"}), '
        f'data.tar.gz {sizes[ARCHIVE_NAME]} B, buddy.env {sizes[ENV_NAME]} B\n'
        f'run {run_id}'
    )


def snapshot_database_and_files(root, scratch_dir, log):
    """Copy webui.db, then archive the rest, so that every file record in the copy has its file in the archive.

    Buddy can delete an upload (record and file) after the database is copied and before the archive reaches it.
    When file records disappear or files vanish in that window, both are taken again. Files added meanwhile only
    leave an unreferenced file in the archive, which is harmless. Returns the alembic revision of the copy.
    """
    source_database = os.path.join(DATA_DIR, DATABASE_NAME)
    database_copy = os.path.join(root, DATABASE_NAME)
    archive_path = os.path.join(root, ARCHIVE_NAME)
    for attempt in range(1, SNAPSHOT_ATTEMPTS + 1):
        if os.path.exists(database_copy):
            os.remove(database_copy)
        started = time.monotonic()
        snapshot_sqlite(source_database, database_copy)
        db_revision, counts = describe_database(database_copy)
        log.append(f'{DATABASE_NAME}: copied and integrity-checked in {time.monotonic() - started:.1f} s; {counts}')
        records_in_copy = file_record_ids(database_copy)

        started = time.monotonic()
        vanished = build_data_archive(DATA_DIR, archive_path, scratch_dir, log)
        log.append(
            f'{ARCHIVE_NAME}: written in {time.monotonic() - started:.1f} s (left out: {", ".join(EXCLUDED_PATHS)})'
        )

        removed_records = set()
        if records_in_copy is not None:
            records_now = file_record_ids(source_database)
            if records_now is None:
                records_now = set()
            removed_records = records_in_copy - records_now
        if not removed_records and not vanished:
            return db_revision
        details = f'{len(removed_records)} file record(s) and {len(vanished)} file(s)'
        if vanished:
            details += f' ({", ".join(vanished[:5])})'
        log.append(f'attempt {attempt}: Buddy deleted {details} during the backup; taking it again')
    raise BackupError(
        f'Buddy kept deleting files while the backup ran ({SNAPSHOT_ATTEMPTS} attempts); the run is retried later'
    )


def create_backup_files(root, log):
    """Write webui.db, data.tar.gz and buddy.env into root; returns (sizes, alembic revision)."""
    source_database = os.path.join(DATA_DIR, DATABASE_NAME)
    if not os.path.isfile(source_database):
        raise BackupError(f'{source_database} not found: is the Buddy data volume mounted at {DATA_DIR}?')

    scratch_dir = os.path.join(WORK_DIR, 'scratch')
    os.makedirs(scratch_dir, exist_ok=True)
    db_revision = snapshot_database_and_files(root, scratch_dir, log)

    copy_env_file(ENV_FILE, os.path.join(root, ENV_NAME), log)

    sizes = {}
    for name in BACKUP_FILES:
        sizes[name] = os.path.getsize(os.path.join(root, name))
    return sizes, db_revision


def do_backup(store, settings, run_id, trigger):
    log = [f'backup {run_id} ({trigger}) -> {settings.depot}']
    result = RunResult(ok=False, log=log)
    root = os.path.join(WORK_DIR, 'ws')
    try:
        # Inside the try, so a failure here (such as a full disk) is recorded instead of leaving the run running.
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        sizes, db_revision = create_backup_files(root, log)
        result.sizes = sizes
        result.db_revision = db_revision
        perforce = Perforce(settings, log)
        perforce.prepare()
        description = describe_change(settings, trigger, run_id, sizes, db_revision)
        result.changelist = perforce.submit(root, BACKUP_FILES, description)
        log.append(f'submitted change {result.changelist}')
        result.ok = True
    except BackupError as exc:
        result.error = str(exc)
        print(f'backup {run_id} failed: {result.error}', file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded for the admin page
        result.error = f'{type(exc).__name__}: {exc}'
        log.append(traceback.format_exc()[-3000:])
        print(f'backup {run_id} failed: {result.error}', file=sys.stderr, flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        store.finish_run(run_id, result, utcnow())
    return result.ok


# ---------------------------------------------------------------------------------------------- entrypoints


@contextmanager
def heartbeat_while_running(beat, interval=LOOP_SECONDS):
    """Refresh the heartbeat from a background thread while a backup blocks the loop.

    Compressing or uploading a large data volume can take longer than the 5 minutes after which the admin page
    reports the service as not responding.
    """
    stop = threading.Event()

    def keep_beating():
        while not stop.wait(interval):
            try:
                beat()
            except Exception as exc:  # noqa: BLE001 - a missed beat must not stop the backup
                print(f'buddy-backup heartbeat error: {exc}', file=sys.stderr, flush=True)

    thread = threading.Thread(target=keep_beating, name='buddy-backup-heartbeat', daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def serve(store, settings):
    print(
        f'buddy-backup: {settings.schedule_time} {settings.timezone} -> {settings.depot} '
        f'via {settings.p4_port or "(BACKUP_P4PORT unset)"}',
        flush=True,
    )
    store.ensure_schema()
    store.fail_interrupted_runs(utcnow())
    while True:
        try:
            now = utcnow()
            last_slot, next_slot = schedule_slots(now, settings.schedule_time, settings.timezone)

            def beat():
                store.heartbeat(settings, next_slot, utcnow())

            beat()
            run_id = store.claim_queued_run(now)
            trigger = 'manual'
            if run_id is None:
                run_id = store.start_scheduled_run_if_due(last_slot, now)
                trigger = 'scheduled'
            if run_id:
                with heartbeat_while_running(beat):
                    do_backup(store, settings, run_id, trigger)
        except Exception as exc:  # noqa: BLE001 - keep the service alive; the admin page shows the stale heartbeat
            print(f'buddy-backup loop error: {exc}', file=sys.stderr, flush=True)
        time.sleep(LOOP_SECONDS)


def check(store, settings):
    """Verify the mounts, the env file and the Perforce login without printing any secret."""
    log = []
    if os.path.isfile(os.path.join(DATA_DIR, DATABASE_NAME)):
        print(f'data: {DATABASE_NAME} found')
    else:
        print(f'data: {DATABASE_NAME} MISSING under {DATA_DIR}')
    keys = env_file_keys(ENV_FILE)
    missing = [key for key in REQUIRED_ENV_KEYS if not keys.get(key)]
    print(f'{ENV_NAME}: {len(keys)} keys; missing required: {", ".join(missing) or "none"}')
    store.ensure_schema()
    print(f'status: {store.path} writable')
    perforce = Perforce(settings, log)
    perforce.prepare()
    perforce.run(['login', '-s'])
    perforce.run(['-ztag', 'info'])
    output_lines = '\n'.join(log).splitlines()
    for line in output_lines:
        if 'ticket expires' in line or line.startswith('... serverAddress '):
            print(line)
    print(f'perforce: logged in as {settings.p4_user}; depot path {settings.depot}')


def main(argv):
    command = argv[1] if len(argv) > 1 else 'serve'
    settings = load_settings()
    store = StatusStore(STATUS_DB)
    if command == 'serve':
        serve(store, settings)
    elif command == 'run-now':
        store.ensure_schema()
        run_id = store.start_run('manual', utcnow())
        succeeded = do_backup(store, settings, run_id, 'manual')
        sys.exit(0 if succeeded else 1)
    elif command == 'check':
        check(store, settings)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == '__main__':
    main(sys.argv)
