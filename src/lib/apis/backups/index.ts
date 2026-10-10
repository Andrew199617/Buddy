import { WEBUI_API_BASE_URL } from '$lib/constants';

// Release backups to Perforce (backend/open_webui/routers/backups.py). Times are UTC ISO-8601 strings.
export type BackupRunStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export type BackupRun = {
	id: string;
	status: BackupRunStatus;
	trigger: 'scheduled' | 'manual';
	requested_by: string | null;
	created_at: string;
	started_at: string | null;
	finished_at: string | null;
	duration_seconds: number | null;
	changelist: number | null;
	db_bytes: number | null;
	data_bytes: number | null;
	env_bytes: number | null;
	db_revision: string | null;
	error: string | null;
};

export type BackupOverview = {
	// False when this instance has no backup status file configured (BUDDY_BACKUP_STATUS_DB).
	configured: boolean;
	// False until the backup service has created its status file.
	available: boolean;
	reason: string | null;
	schedule: {
		time: string;
		timezone: string;
		depot_path: string;
		p4_port: string;
		retention: string;
	} | null;
	scheduler: {
		last_heartbeat_at: string | null;
		next_run_at: string | null;
		// The service checked in within the last 5 minutes.
		online: boolean;
	} | null;
	last_success: BackupRun | null;
	// The newest finished run, successful or not.
	latest_run: BackupRun | null;
	// A queued or running run.
	active_run: BackupRun | null;
	// The newest finished run failed.
	failing: boolean;
	// Nothing succeeded in the last 26 hours.
	stale: boolean;
	runs: BackupRun[];
};

const backupRequest = async (token: string, path: string, method: 'GET' | 'POST' = 'GET') => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/backups${path}`, {
		method,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});

	const data = await response.json().catch(() => null);
	if (!response.ok) {
		throw data?.detail ?? `Request failed (${response.status})`;
	}
	return data;
};

export const getBackupOverview = async (token: string): Promise<BackupOverview> => {
	return backupRequest(token, '/');
};

// Queues a manual run for the backup service; rejects while one is queued or running.
export const runBackupNow = async (token: string): Promise<BackupRun> => {
	return backupRequest(token, '/run', 'POST');
};

export const getBackupRunLog = async (
	token: string,
	runId: string
): Promise<{ id: string; log: string }> => {
	return backupRequest(token, `/runs/${encodeURIComponent(runId)}/log`);
};
