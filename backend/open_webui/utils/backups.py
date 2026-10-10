"""Release backups for the admin Settings > Backups tab.

The backup service of the Docker stack (docker/buddy/backup/buddy_backup.py) records its runs and a heartbeat in a
small SQLite status file on the `backup-status` volume. Buddy mounts that volume and finds the file through
BUDDY_BACKUP_STATUS_DB. This module only reads the file and inserts queued manual runs; the service creates the
tables and executes every run. Keep the table and column names in sync with the service.

The status file is kept out of webui.db on purpose: the service never writes into the live app database, and the
data volume stays read-only for it.
"""

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

STATUS_DB_ENV = 'BUDDY_BACKUP_STATUS_DB'

# The service refreshes its heartbeat every loop (~30 s); a few missed loops means it is down.
HEARTBEAT_STALE_AFTER = timedelta(minutes=5)
# A nightly job: one missed night, plus slack for a slow run, makes the backups stale.
SUCCESS_STALE_AFTER = timedelta(hours=26)
RUNS_LIMIT = 30
RETENTION_LABEL = 'Last 7 revisions per file (binary+S7)'

RUN_SUMMARY_COLUMNS = (
    'id, status, trigger, requested_by, created_at, started_at, finished_at, changelist, '
    'db_bytes, data_bytes, env_bytes, db_revision, error'
)


class BackupServiceUnavailable(Exception):
    """The status file is not configured, or the backup service has not created it yet."""


class BackupAlreadyActive(Exception):
    def __init__(self, run):
        super().__init__('A backup is already queued or running.')
        self.run = run


def status_db_path():
    return os.environ.get(STATUS_DB_ENV, '').strip()


def format_time(value):
    """Same fixed-width UTC text as the service, so stored values sort and compare as text."""
    utc_value = value.astimezone(UTC)
    milliseconds = utc_value.microsecond // 1000
    return utc_value.strftime('%Y-%m-%dT%H:%M:%S') + f'.{milliseconds:03d}Z'


def parse_time(text):
    if not text:
        return None
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def open_status_db(path):
    """Open the existing status file (never create it) once the service has created its tables."""
    if not path:
        raise BackupServiceUnavailable('Backups are not configured for this instance.')
    if not os.path.isfile(path):
        raise BackupServiceUnavailable('The backup service has not started yet.')
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
    connection.isolation_level = None
    connection.row_factory = sqlite3.Row
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if 'backup_runs' not in tables or 'backup_scheduler' not in tables:
        connection.close()
        raise BackupServiceUnavailable('The backup service has not started yet.')
    return connection


def serialize_run(row):
    if row is None:
        return None
    started_at = parse_time(row['started_at'])
    finished_at = parse_time(row['finished_at'])
    duration_seconds = None
    if started_at and finished_at:
        duration_seconds = round((finished_at - started_at).total_seconds(), 1)
    return {
        'id': row['id'],
        'status': row['status'],
        'trigger': row['trigger'],
        'requested_by': row['requested_by'],
        'created_at': row['created_at'],
        'started_at': row['started_at'],
        'finished_at': row['finished_at'],
        'duration_seconds': duration_seconds,
        'changelist': row['changelist'],
        'db_bytes': row['db_bytes'],
        'data_bytes': row['data_bytes'],
        'env_bytes': row['env_bytes'],
        'db_revision': row['db_revision'],
        'error': row['error'],
    }


def unavailable_overview(configured, reason):
    return {
        'configured': configured,
        'available': False,
        'reason': reason,
        'schedule': None,
        'scheduler': None,
        'last_success': None,
        'latest_run': None,
        'active_run': None,
        'failing': False,
        'stale': False,
        'runs': [],
    }


def first_run(connection, where, parameters, order_by):
    query = f'SELECT {RUN_SUMMARY_COLUMNS} FROM backup_runs WHERE {where} ORDER BY {order_by} LIMIT 1'
    return connection.execute(query, parameters).fetchone()


def backup_overview(path, now=None):
    """Schedule, heartbeat, last success, alert flags and the latest runs (without their logs)."""
    now = now or datetime.now(UTC)
    try:
        connection = open_status_db(path)
    except BackupServiceUnavailable as exc:
        return unavailable_overview(configured=bool(path), reason=str(exc))

    with closing(connection):
        scheduler = connection.execute('SELECT * FROM backup_scheduler WHERE id = 1').fetchone()
        runs = connection.execute(
            f'SELECT {RUN_SUMMARY_COLUMNS} FROM backup_runs ORDER BY created_at DESC LIMIT ?', (RUNS_LIMIT,)
        ).fetchall()
        last_success = first_run(connection, "status = 'succeeded'", (), 'finished_at DESC')
        latest_finished = first_run(connection, "status IN ('succeeded', 'failed')", (), 'created_at DESC')
        active_run = first_run(connection, "status IN ('queued', 'running')", (), 'created_at DESC')

    heartbeat = None
    if scheduler is not None:
        heartbeat = parse_time(scheduler['last_heartbeat_at'])
    online = heartbeat is not None and now - heartbeat <= HEARTBEAT_STALE_AFTER

    last_success_at = None
    if last_success is not None:
        last_success_at = parse_time(last_success['finished_at'])
    stale = last_success_at is None or now - last_success_at > SUCCESS_STALE_AFTER

    failing = latest_finished is not None and latest_finished['status'] == 'failed'

    schedule = None
    scheduler_state = None
    if scheduler is not None:
        schedule = {
            'time': scheduler['schedule_time'],
            'timezone': scheduler['timezone'],
            'depot_path': scheduler['depot_path'],
            'p4_port': scheduler['p4_port'],
            'retention': RETENTION_LABEL,
        }
        scheduler_state = {
            'last_heartbeat_at': scheduler['last_heartbeat_at'],
            'next_run_at': scheduler['next_run_at'],
            'online': online,
        }

    return {
        'configured': True,
        'available': True,
        'reason': None,
        'schedule': schedule,
        'scheduler': scheduler_state,
        'last_success': serialize_run(last_success),
        'latest_run': serialize_run(latest_finished),
        'active_run': serialize_run(active_run),
        'failing': failing,
        'stale': stale,
        'runs': [serialize_run(run) for run in runs],
    }


def queue_backup(path, requested_by, now=None):
    """Queue a manual run for the service to pick up; raises BackupAlreadyActive while one is queued or running."""
    now = now or datetime.now(UTC)
    connection = open_status_db(path)
    with closing(connection):
        # BEGIN IMMEDIATE takes the write lock first, so two clicks (or a click and the service starting a
        # scheduled run) cannot both see "nothing active".
        connection.execute('BEGIN IMMEDIATE')
        try:
            active_run = first_run(connection, "status IN ('queued', 'running')", (), 'created_at DESC')
            if active_run is not None:
                raise BackupAlreadyActive(serialize_run(active_run))
            run_id = str(uuid.uuid4())
            connection.execute(
                'INSERT INTO backup_runs (id, status, trigger, requested_by, created_at) VALUES (?, ?, ?, ?, ?)',
                (run_id, 'queued', 'manual', requested_by, format_time(now)),
            )
            queued_run = first_run(connection, 'id = ?', (run_id,), 'created_at')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
        connection.execute('COMMIT')
    return serialize_run(queued_run)


def backup_run_log(path, run_id):
    """The log of one run, or None when there is no such run."""
    connection = open_status_db(path)
    with closing(connection):
        row = connection.execute('SELECT log FROM backup_runs WHERE id = ?', (run_id,)).fetchone()
    if row is None:
        return None
    return row['log'] or ''
