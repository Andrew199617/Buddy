"""Point stored upload paths at this data folder after moving Buddy's data.

Buddy stores each upload's full path. After a Windows data folder is copied
into the Docker volume, those rows still name the Windows folder, so the
container cannot open any earlier upload. Run this inside the container after
the copy, with the Windows data folder that was copied. --dry-run reports what
would change without writing anything.
"""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path, PurePath, PureWindowsPath

EXAMPLE_COUNT = 3


def windows_uploads_dir(old_data_dir: str) -> PureWindowsPath:
    data_dir = PureWindowsPath(old_data_dir)
    if not data_dir.is_absolute():
        raise ValueError(f'Pass the old data folder as a full Windows path, such as C:\\Buddy\\open-webui-data: {old_data_dir}')
    return data_dir / 'uploads'


def moved_upload_path(stored_path: str, old_uploads: PureWindowsPath, new_uploads: PurePath) -> str | None:
    """Return the stored path rebased onto the new uploads folder, or None when
    it is not inside the old one. Windows paths compare without case."""
    try:
        relative_path = PureWindowsPath(stored_path).relative_to(old_uploads)
    except ValueError:
        return None
    return str(new_uploads.joinpath(*relative_path.parts))


def open_database(database: Path, writable: bool) -> sqlite3.Connection:
    # Both modes refuse to create a database, and a dry run cannot write.
    if writable:
        mode = 'rw'
    else:
        mode = 'ro'
    return sqlite3.connect(f'{database.resolve().as_uri()}?mode={mode}', uri=True)


def update_upload_paths(database: Path, old_data_dir: str, new_data_dir: PurePath, dry_run: bool) -> dict:
    old_uploads = windows_uploads_dir(old_data_dir)
    new_uploads = new_data_dir / 'uploads'
    if not database.is_file():
        raise FileNotFoundError(f'Buddy database was not found: {database}')

    changes = []
    other_paths = 0
    with closing(open_database(database, writable=not dry_run)) as connection:
        for file_id, stored_path in connection.execute('SELECT id, path FROM file').fetchall():
            new_path = None
            if stored_path:
                new_path = moved_upload_path(stored_path, old_uploads, new_uploads)
            if new_path is None:
                other_paths += 1
                continue
            changes.append({'id': file_id, 'old': stored_path, 'new': new_path})

        if not dry_run:
            connection.executemany(
                'UPDATE file SET path = ? WHERE id = ?',
                [(change['new'], change['id']) for change in changes],
            )
            connection.commit()

    missing_files = [change['id'] for change in changes if not Path(change['new']).is_file()]
    return {
        'database': str(database),
        'old_uploads_dir': str(old_uploads),
        'new_uploads_dir': str(new_uploads),
        'dry_run': dry_run,
        'matching_paths': len(changes),
        'other_paths': other_paths,
        'missing_files': len(missing_files),
        'missing_file_ids': missing_files[:EXAMPLE_COUNT],
        'examples': changes[:EXAMPLE_COUNT],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--old-data-dir',
        required=True,
        help='The data folder as the Windows instance used it, such as C:\\Buddy\\open-webui-data.',
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=os.environ.get('DATA_DIR'),
        help='The data folder that now holds webui.db and uploads (default: DATA_DIR).',
    )
    parser.add_argument('--dry-run', action='store_true', help='Report the changes without writing them.')
    arguments = parser.parse_args()
    if arguments.data_dir is None:
        parser.error('Pass --data-dir or set DATA_DIR.')

    # Match the backend, which stores uploads under the resolved DATA_DIR.
    data_dir = arguments.data_dir.resolve()
    try:
        report = update_upload_paths(data_dir / 'webui.db', arguments.old_data_dir, data_dir, arguments.dry_run)
    except (ValueError, FileNotFoundError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
