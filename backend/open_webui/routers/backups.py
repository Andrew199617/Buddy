"""Admin endpoints for the release backups (Settings > Backups); see open_webui.utils.backups.

Routes:
  GET  /                   schedule, service heartbeat, last success, alert flags and the latest runs
  POST /run                queue a manual backup (202), or 409 while one is queued or running
  GET  /runs/{run_id}/log  the log of one run

The backup service in the Docker stack executes queued runs. The handlers are plain functions, so FastAPI runs
their short SQLite calls in its thread pool.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from open_webui.utils import backups
from open_webui.utils.auth import get_admin_user

router = APIRouter()


@router.get('/')
def get_backup_overview(user=Depends(get_admin_user)):
    return backups.backup_overview(backups.status_db_path())


@router.post('/run', status_code=status.HTTP_202_ACCEPTED)
def run_backup_now(user=Depends(get_admin_user)):
    requested_by = user.name or user.email
    try:
        return backups.queue_backup(backups.status_db_path(), requested_by)
    except backups.BackupAlreadyActive as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except backups.BackupServiceUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.get('/runs/{run_id}/log')
def get_backup_run_log(run_id: str, user=Depends(get_admin_user)):
    try:
        log = backups.backup_run_log(backups.status_db_path(), run_id)
    except backups.BackupServiceUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such backup run.')
    return {'id': run_id, 'log': log}
