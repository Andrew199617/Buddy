"""Create an independent Buddy data snapshot without stopping the source app."""

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = source.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection, pages=256, sleep=0.05)
            result = destination_connection.execute('PRAGMA quick_check').fetchone()[0]
            if result != 'ok':
                raise RuntimeError(f'SQLite snapshot failed its integrity check: {destination}')


def copy_vector_store(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    for source_file in source.rglob('*'):
        if source_file.is_symlink():
            raise ValueError('Vector snapshots require ordinary files, without symbolic links')
        if not source_file.is_file():
            continue
        if source_file.name.endswith(('-wal', '-shm')):
            continue
        destination_file = destination / source_file.relative_to(source)
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        if source_file.suffix in {'.sqlite3', '.db'}:
            backup_sqlite(source_file, destination_file)
        else:
            shutil.copy2(source_file, destination_file)


def prepare_database_snapshot(database: Path, source: Path, destination: Path) -> dict:
    with closing(sqlite3.connect(database)) as connection:
        users = connection.execute('SELECT COUNT(*) FROM user').fetchone()[0]
        chats = connection.execute('SELECT COUNT(*) FROM chat').fetchone()[0]
        messages = connection.execute('SELECT COUNT(*) FROM chat_message').fetchone()[0]
        active_automations = connection.execute(
            'SELECT COUNT(*) FROM automation WHERE is_active = 1'
        ).fetchone()[0]
        # Preserve copied tasks and history, while preventing the parallel app
        # from automatically running the source app's scheduled work again.
        connection.execute('UPDATE automation SET is_active = 0, next_run_at = NULL WHERE is_active = 1')

        source_uploads = source / 'uploads'
        destination_uploads = destination / 'uploads'
        adjusted_files = 0
        for file_id, file_path in connection.execute('SELECT id, path FROM file').fetchall():
            if not file_path:
                continue
            try:
                relative_path = Path(file_path).resolve().relative_to(source_uploads.resolve())
            except ValueError:
                continue
            connection.execute(
                'UPDATE file SET path = ? WHERE id = ?',
                (str(destination_uploads / relative_path), file_id),
            )
            adjusted_files += 1
        connection.commit()
        return {
            'users': users,
            'chats': chats,
            'messages': messages,
            'copied_automations_paused': active_automations,
            'upload_paths_adjusted': adjusted_files,
        }


def create_snapshot(source: Path, destination: Path) -> dict:
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError('Buddy snapshot must be separate from the source data directory')
    if destination.exists():
        raise FileExistsError('Snapshot destination already exists; choose a new directory to avoid replacing data')
    if not (source / 'webui.db').is_file():
        raise FileNotFoundError('Source webui.db was not found')

    destination.mkdir(parents=True)
    backup_sqlite(source / 'webui.db', destination / 'webui.db')
    if (source / 'uploads').is_dir():
        shutil.copytree(source / 'uploads', destination / 'uploads')
    copy_vector_store(source / 'vector_db', destination / 'vector_db')
    counts = prepare_database_snapshot(destination / 'webui.db', source, destination)
    report = {
        'source_data_directory': str(source),
        'snapshot_data_directory': str(destination),
        'cache_copied': False,
        **counts,
    }
    (destination / 'snapshot-info.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT_DIR / 'open-webui-data')
    parser.add_argument('--destination', type=Path, default=ROOT_DIR / 'buddy-data-8081')
    arguments = parser.parse_args()
    report = create_snapshot(arguments.source, arguments.destination)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
